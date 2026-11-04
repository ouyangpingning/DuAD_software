"""BLE 配网（QtBluetooth）。

设计目标：只把 BLE 当作**配网通道**，运动控制仍然走原来的 TCP。
板子会一直慢速广播（名字前缀 ``DuAD-XY-`` / ``DuAD-Z-``），
上位机扫到后连 GATT，先把口令交给板子，再复用板子现有的控制台命令：

    <token>                 -> #OK auth
    wifi status             -> {"configured":...,"connected":...,"state":...}
    wifi scan               -> {"nets":[{"ssid":...,"rssi":...,"auth":...}]}
    wifi set {"ssid":...}   -> #OK（随后每 1s 轮询 wifi status）
    wifi rollback           -> #OK
    wifi forget             -> #OK

这样固件侧不需要再造一套协议：BLE 只是第三条“控制台通道”，
和 USB / TCP 共用同一张命令表、同一套安全闸。

⚠ 本模块只负责 BLE 与配网状态机；不碰运动命令。
   BLE 侧固件也会把命令白名单限制在 wifi/net/ver/help，避免绕过 TCP 的急停语义。
"""

from __future__ import annotations

import json
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import QObject, QTimer, QByteArray, QUuid, Signal, Property, Slot
from PySide6.QtBluetooth import (
    QBluetoothDeviceDiscoveryAgent,
    QBluetoothDeviceInfo,
    QBluetoothUuid,
    QLowEnergyCharacteristic,
    QLowEnergyController,
    QLowEnergyService,
)

# 自定义 128-bit UUID。固件 `ble_prov` 组件必须用同一组值。
BLE_SERVICE_UUID = "d0ad0001-9a1b-4c2d-8e3f-1a2b3c4d5e6f"
BLE_RX_UUID = "d0ad0002-9a1b-4c2d-8e3f-1a2b3c4d5e6f"   # 上位机 -> 板子（write）
BLE_TX_UUID = "d0ad0003-9a1b-4c2d-8e3f-1a2b3c4d5e6f"   # 板子 -> 上位机（notify）

# 板卡广播名固定死（UI 不再提供选择）：
#   XY 板实测为 DuAD-XY-55C6；
#   Z 板的 MAC 后缀请按实物修改，默认用 DuAD-Z- 前缀自动匹配（仍不接受用户选择）。
BOARD_NAMES = {"xy": "DuAD-XY-55C6", "z": "DuAD-Z-"}


def _as_uuid(s: str) -> QBluetoothUuid:
    return QBluetoothUuid(QUuid(s))


class BleLink(QObject):
    """传输层抽象。真实实现是 QtBleLink；测试注入 FakeBleLink。"""

    connected = Signal()
    disconnected = Signal()
    error = Signal(str)
    lineReceived = Signal(str)
    deviceFound = Signal(dict)      # {name, address, rssi}
    scanFinished = Signal()

    def start_scan(self, prefix: str) -> None:
        raise NotImplementedError

    def stop_scan(self) -> None:
        pass

    def connect_device(self, address: str) -> None:
        raise NotImplementedError

    def disconnect_device(self) -> None:
        pass

    def send_line(self, text: str) -> None:
        raise NotImplementedError


class FakeBleLink(BleLink):
    """测试用：不碰蓝牙硬件，按脚本回放板子的应答。

    ``script`` 是 {命令前缀: [应答行, ...]}；收到命令时逐行 emit。
    真实板子的一条命令会先输出若干 JSON/文本行，最后是 #OK / #ERR。
    """

    def __init__(self, script: Optional[Dict[str, List[str]]] = None, parent=None):
        super().__init__(parent)
        self.script = script or {}
        self.sent: List[str] = []
        self.connected_now = False
        self._prefix = ""

    def start_scan(self, prefix: str) -> None:
        self._prefix = prefix
        self.deviceFound.emit({"name": prefix + "1A2B", "address": "AA:BB:CC:DD:EE:FF",
                               "rssi": -45})
        self.scanFinished.emit()

    def connect_device(self, address: str) -> None:
        self.connected_now = True
        self.connected.emit()

    def disconnect_device(self) -> None:
        if self.connected_now:
            self.connected_now = False
            self.disconnected.emit()

    def send_line(self, text: str) -> None:
        self.sent.append(text)
        for key, replies in self.script.items():
            if text.startswith(key):
                for line in replies:
                    self.lineReceived.emit(line)
                return
        # 默认：未知命令当失败，避免测试里“没配脚本也能过”
        self.lineReceived.emit(f"#ERR unknown {text.split(' ', 1)[0]}")


class QtBleLink(BleLink):
    """QtBluetooth 的真实实现。

    ⚠ Windows / Linux 都用同一个 Qt API；Linux 依赖 BlueZ + D-Bus。
    扫描失败（蓝牙关闭/无适配器）会通过 error 信号上报，UI 显示可读原因。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._agent: Optional[QBluetoothDeviceDiscoveryAgent] = None
        self._devices: Dict[str, QBluetoothDeviceInfo] = {}
        self._controller: Optional[QLowEnergyController] = None
        self._service: Optional[QLowEnergyService] = None
        self._rx: Optional[QLowEnergyCharacteristic] = None
        self._tx: Optional[QLowEnergyCharacteristic] = None
        self._rx_buf = bytearray()
        self._write_queue: List[bytes] = []
        self._write_busy = False
        self._write_timer = QTimer(self)
        self._write_timer.setSingleShot(True)
        self._write_timer.timeout.connect(self._on_write_timeout)

    # ── 扫描 ─────────────────────────────────────────────
    def start_scan(self, prefix: str) -> None:
        self.stop_scan()
        methods = QBluetoothDeviceDiscoveryAgent.supportedDiscoveryMethods()
        if not (methods & QBluetoothDeviceDiscoveryAgent.DiscoveryMethod.LowEnergyMethod):
            self.error.emit(self.tr("当前系统不支持 BLE 扫描"))
            self.scanFinished.emit()
            return

        self._devices.clear()
        agent = QBluetoothDeviceDiscoveryAgent(self)
        agent.setLowEnergyDiscoveryTimeout(8000)
        agent.deviceDiscovered.connect(lambda info: self._on_device(info, prefix))
        agent.finished.connect(self.scanFinished.emit)
        agent.errorOccurred.connect(self._on_agent_error)
        self._agent = agent
        agent.start(QBluetoothDeviceDiscoveryAgent.DiscoveryMethod.LowEnergyMethod)

    def stop_scan(self) -> None:
        if self._agent is not None:
            try:
                self._agent.stop()
            except Exception:
                pass
            self._agent = None

    def _on_agent_error(self, err) -> None:
        self.error.emit(self.tr("BLE 扫描失败：{}").format(
            self._agent.errorString() if self._agent else err))
        self.scanFinished.emit()

    def _on_device(self, info: QBluetoothDeviceInfo, prefix: str) -> None:
        name = info.name() or ""
        if not name.lower().startswith(prefix.lower()):
            return
        if not (info.coreConfigurations()
                & QBluetoothDeviceInfo.CoreConfiguration.LowEnergyCoreConfiguration):
            return
        address = info.address().toString()
        if address in self._devices:
            return
        self._devices[address] = info
        self.deviceFound.emit({"name": name, "address": address, "rssi": info.rssi()})

    # ── 连接 ─────────────────────────────────────────────
    def connect_device(self, address: str) -> None:
        info = self._devices.get(address)
        if info is None:
            self.error.emit(self.tr("没有找到设备 {}，请重新扫描").format(address))
            return
        self._cleanup_controller()
        try:
            controller = QLowEnergyController.createCentral(info, self)
        except Exception as e:
            self.error.emit(self.tr("创建 BLE 连接失败：{}").format(e))
            return
        self._controller = controller
        controller.connected.connect(self._on_connected)
        controller.disconnected.connect(self._on_disconnected)
        controller.serviceDiscovered.connect(self._on_service_discovered)
        controller.discoveryFinished.connect(self._on_discovery_finished)
        controller.errorOccurred.connect(self._on_controller_error)
        controller.connectToDevice()

    def disconnect_device(self) -> None:
        if self._controller is not None:
            try:
                self._controller.disconnectFromDevice()
            except Exception:
                pass

    def _cleanup_controller(self) -> None:
        self._service = None
        self._rx = None
        self._tx = None
        self._rx_buf.clear()
        self._write_queue.clear()
        self._write_busy = False
        if self._controller is not None:
            try:
                self._controller.disconnectFromDevice()
            except Exception:
                pass
            self._controller = None

    def _on_connected(self) -> None:
        if self._controller is None:
            return
        self._controller.discoverServices()

    def _on_service_discovered(self, uuid) -> None:
        # 只记日志；真正的服务对象在 discoveryFinished 后创建
        pass

    def _on_discovery_finished(self) -> None:
        if self._controller is None:
            return
        target = None
        for s in self._controller.services():
            if s == _as_uuid(BLE_SERVICE_UUID):
                target = s
                break
        if target is None:
            self.error.emit(self.tr("设备没有 DuAD 配网服务，请确认固件版本"))
            self.disconnect_device()
            return
        service = self._controller.createServiceObject(target, self)
        if service is None:
            self.error.emit(self.tr("无法打开 BLE 配网服务"))
            self.disconnect_device()
            return
        self._service = service
        service.stateChanged.connect(self._on_service_state)
        service.characteristicChanged.connect(self._on_characteristic_changed)
        service.characteristicWritten.connect(self._on_characteristic_written)
        service.errorOccurred.connect(self._on_service_error)
        if service.state() == QLowEnergyService.ServiceState.RemoteServiceDiscovered:
            self._setup_characteristics()
        else:
            service.discoverDetails()

    def _on_service_state(self, state) -> None:
        if state == QLowEnergyService.ServiceState.RemoteServiceDiscovered:
            self._setup_characteristics()

    def _setup_characteristics(self) -> None:
        if self._service is None:
            return
        rx = self._service.characteristic(_as_uuid(BLE_RX_UUID))
        tx = self._service.characteristic(_as_uuid(BLE_TX_UUID))
        if not rx.isValid() or not tx.isValid():
            self.error.emit(self.tr("BLE 特征值不完整，请确认固件版本"))
            self.disconnect_device()
            return
        self._rx = rx
        self._tx = tx
        # 订阅 TX 通知
        cccd = tx.clientCharacteristicConfiguration()
        if not cccd.isValid():
            self.error.emit(self.tr("BLE 通知未配置（缺少 CCCD）"))
            self.disconnect_device()
            return
        self._service.writeDescriptor(
            cccd, QLowEnergyCharacteristic.CCCDEnableNotification)
        self.connected.emit()

    def _on_characteristic_changed(self, ch, value: QByteArray) -> None:
        if ch.uuid() != _as_uuid(BLE_TX_UUID):
            return
        self._rx_buf.extend(bytes(value))
        while True:
            idx = self._rx_buf.find(b"\n")
            if idx < 0:
                break
            raw = bytes(self._rx_buf[:idx])
            del self._rx_buf[:idx + 1]
            try:
                line = raw.decode("utf-8")
            except UnicodeDecodeError:
                line = raw.decode("utf-8", errors="replace")
            self.lineReceived.emit(line.rstrip("\r"))

    def _on_characteristic_written(self, ch, value) -> None:
        if ch.uuid() != _as_uuid(BLE_RX_UUID):
            return
        self._write_timer.stop()
        self._write_busy = False
        self._pump_write()

    def _on_write_timeout(self) -> None:
        self._write_busy = False
        self.error.emit(self.tr("BLE 写入超时"))
        self._write_queue.clear()

    def _on_controller_error(self, err) -> None:
        msg = self._controller.errorString() if self._controller else str(err)
        self.error.emit(self.tr("BLE 连接失败：{}").format(msg))

    def _on_disconnected(self) -> None:
        self._write_queue.clear()
        self._write_busy = False
        self.disconnected.emit()

    def _on_service_error(self, err) -> None:
        self.error.emit(self.tr("BLE 服务错误：{}").format(err))

    # ── 发送（按 MTU 分片；下位机按 \n 重组）─────────────
    def send_line(self, text: str) -> None:
        if self._service is None or self._rx is None:
            self.error.emit(self.tr("BLE 未连接"))
            return
        data = (text + "\n").encode("utf-8")
        mtu = self._controller.mtu() if self._controller else 0
        chunk = max(20, (mtu or 23) - 3)
        for i in range(0, len(data), chunk):
            self._write_queue.append(data[i:i + chunk])
        self._pump_write()

    def _pump_write(self) -> None:
        if self._write_busy or not self._write_queue or self._service is None or self._rx is None:
            return
        chunk = self._write_queue.pop(0)
        props = self._rx.properties()
        with_response = bool(props & QLowEnergyCharacteristic.PropertyType.Write)
        mode = (QLowEnergyService.WriteMode.WriteWithResponse if with_response
                else QLowEnergyService.WriteMode.WriteWithoutResponse)
        self._write_busy = True
        self._write_timer.start(3000)
        self._service.writeCharacteristic(self._rx, chunk, mode)
        if not with_response:
            # WriteWithoutResponse 不一定有 characteristicWritten；给一点时间再发下一片
            QTimer.singleShot(20, self._on_write_timeout_fake)

    def _on_write_timeout_fake(self) -> None:
        if self._write_busy:
            self._write_timer.stop()
            self._write_busy = False
            self._pump_write()


class BleProvisioner(QObject):
    """配网状态机 + 协议解析；不直接碰 QtBluetooth（可注入 FakeBleLink 测试）。"""

    stateChanged = Signal()
    devicesChanged = Signal()
    netsChanged = Signal()
    statusChanged = Signal()
    provisioned = Signal(str, str)      # ip, ssid
    failed = Signal(str)
    forgotten = Signal(str)             # 被忘记的 SSID（空串=板上没有已保存网络）

    BUSY_STATES = ("scanning", "connecting", "authenticating",
                   "scanning_wifi", "provisioning")

    def __init__(self, role: str, link: Optional[BleLink] = None, parent=None):
        super().__init__(parent)
        self.role = role
        self.board_name = BOARD_NAMES.get(role, "DuAD-")
        self._link = link or QtBleLink(self)
        self._link.connected.connect(self._on_link_connected)
        self._link.disconnected.connect(self._on_link_disconnected)
        self._link.error.connect(self._on_link_error)
        self._link.lineReceived.connect(self._on_line)
        self._link.deviceFound.connect(self._on_device_found)
        self._link.scanFinished.connect(self._on_scan_finished)

        self._state = "idle"
        self._state_text = ""
        self._devices: List[dict] = []
        self._nets: List[dict] = []
        self._ssid = ""
        self._saved_ssid = ""
        self._ip = ""
        self._rssi = 0
        self._error = ""
        self._token = ""
        self._address = ""
        self._pending = None          # (label, on_ok, timer)
        self._last_json: dict = {}
        self._poll_timer = QTimer(self)
        self._poll_timer.setSingleShot(True)
        self._poll_timer.timeout.connect(self._poll_status)
        self._poll_started = 0
        self._poll_count = 0
        self._scan_retry = False

        self._cmd_timeout = QTimer(self)
        self._cmd_timeout.setSingleShot(True)
        self._cmd_timeout.timeout.connect(self._on_cmd_timeout)

        self._set_state("idle")

    # ── QML 属性 ─────────────────────────────────────────
    def _get_state(self) -> str:
        return self._state

    state = Property(str, _get_state, notify=stateChanged)

    def _get_state_text(self) -> str:
        return self._state_text

    stateText = Property(str, _get_state_text, notify=stateChanged)

    def _get_devices(self) -> list:
        return list(self._devices)

    devices = Property("QVariantList", _get_devices, notify=devicesChanged)

    def _get_nets(self) -> list:
        return list(self._nets)

    nets = Property("QVariantList", _get_nets, notify=netsChanged)

    def _get_ssid(self) -> str:
        return self._ssid

    ssid = Property(str, _get_ssid, notify=statusChanged)

    def _get_saved_ssid(self) -> str:
        return self._saved_ssid

    savedSsid = Property(str, _get_saved_ssid, notify=statusChanged)

    def _get_ip(self) -> str:
        return self._ip

    ip = Property(str, _get_ip, notify=statusChanged)

    def _get_rssi(self) -> int:
        return self._rssi

    rssi = Property(int, _get_rssi, notify=statusChanged)

    def _get_error(self) -> str:
        return self._error

    error = Property(str, _get_error, notify=statusChanged)

    def _get_busy(self) -> bool:
        return self._state in self.BUSY_STATES

    busy = Property(bool, _get_busy, notify=stateChanged)

    # ── 对外动作 ─────────────────────────────────────────
    @Slot(str)
    def set_token(self, token: str) -> None:
        """扫描前先把口令交给状态机，便于“只扫到一台时自动连接”。"""
        self._token = (token or "").strip()

    @Slot()
    def scan(self) -> None:
        self._error = ""
        self._devices.clear()
        self.devicesChanged.emit()
        # 重新扫描前先释放旧连接（否则部分平台会一直占着 GATT）
        self._link.disconnect_device()
        self._set_state("scanning")
        # 固定板卡名：不显示列表、不让用户选；扫到就自动连。
        self._link.start_scan(self.board_name)

    @Slot(str, str)
    def connect_device(self, address: str, token: str) -> None:
        self._token = (token or "").strip()
        if not self._token:
            self._fail(self.tr("请先填写板子的连接口令（USB 控制台敲 net 能看到）"))
            return
        if not address:
            self._fail(self.tr("请先选择要配网的板卡"))
            return
        self._address = address
        self._error = ""
        self._set_state("connecting")
        self._link.connect_device(address)

    @Slot()
    def scan_wifi(self) -> None:
        if self._state == "scanning_wifi":
            return
        if self._state != "reading":
            self._fail(self.tr("请先连接板卡并完成口令认证"))
            return
        # 刚认证完时 wifi status 可能还在途；不要立刻报“上一条没完成”，
        # 等它结束再自动重试（用户点了“扫描 Wi-Fi”就应该扫）。
        if self._pending is not None:
            if not self._scan_retry:
                self._scan_retry = True
                QTimer.singleShot(200, self._retry_scan_wifi)
            return
        self._nets.clear()
        self.netsChanged.emit()
        self._set_state("scanning_wifi")
        self._send("wifi scan", self._on_scan_wifi_ok, timeout_ms=12000)

    def _retry_scan_wifi(self) -> None:
        self._scan_retry = False
        self.scan_wifi()

    @Slot(str, str)
    def apply_wifi(self, ssid: str, password: str) -> None:
        if self._state not in ("reading", "scanning_wifi"):
            self._fail(self.tr("请先连接板卡并完成口令认证"))
            return
        ssid = (ssid or "").strip()
        if not ssid:
            self._fail(self.tr("请先选择或输入 Wi-Fi 名称"))
            return
        if len(ssid) > 32:
            self._fail(self.tr("Wi-Fi 名称最长 32 字节"))
            return
        if not password:
            self._fail(self.tr("密码不能为空（当前界面不支持开放网络）"))
            return
        if len(password or "") > 64:
            self._fail(self.tr("Wi-Fi 密码最长 63 个字符"))
            return
        self._ssid = ssid
        self._error = ""
        self._set_state("provisioning")
        self._poll_count = 0
        self._poll_started = 0
        payload = json.dumps({"ssid": ssid, "pass": password or ""},
                             ensure_ascii=False)
        self._send("wifi set " + payload, self._on_set_ok, timeout_ms=10000)

    @Slot()
    def forget_wifi(self) -> None:
        if self._state not in ("reading", "connected"):
            self._fail(self.tr("请先连接板卡"))
            return
        self._send("wifi forget", self._on_forget_ok, timeout_ms=5000)

    @Slot()
    def rollback_wifi(self) -> None:
        if self._state not in ("reading", "connected"):
            self._fail(self.tr("请先连接板卡"))
            return
        self._send("wifi rollback", self._on_rollback_ok, timeout_ms=5000)

    # ── 状态与错误 ───────────────────────────────────────
    def _set_state(self, state: str) -> None:
        self._state = state
        mapping = {
            "idle": self.tr("未开始"),
            "scanning": self.tr("正在搜索板卡…"),
            "connecting": self.tr("正在连接板卡…"),
            "authenticating": self.tr("正在验证口令…"),
            "reading": self.tr("已连接板卡"),
            "scanning_wifi": self.tr("正在扫描 Wi-Fi…"),
            "provisioning": self.tr("正在连接 Wi-Fi…"),
            "connected": self.tr("已连接 Wi-Fi"),
            "failed": self.tr("配网失败"),
        }
        self._state_text = mapping.get(state, state)
        self.stateChanged.emit()

    def _fail(self, msg: str) -> None:
        self._error = msg
        self._set_state("failed")
        self.failed.emit(msg)

    def _on_link_error(self, msg: str) -> None:
        # 连接中/配网中的错误才是致命的；空闲时（例如扫描权限）也提示
        self._fail(msg)

    def _on_link_disconnected(self) -> None:
        if self._state in ("authenticating", "reading",
                           "scanning_wifi", "provisioning"):
            self._fail(self.tr("蓝牙连接已断开，请重新搜索板卡"))

    def _on_device_found(self, dev: dict) -> None:
        for d in self._devices:
            if d.get("address") == dev.get("address"):
                return
        self._devices.append(dev)
        self.devicesChanged.emit()
        # 固定板卡名：第一台匹配的设备直接连，不再等扫描超时/不再让用户选。
        if self._state == "scanning":
            self._link.stop_scan()
            self.connect_device(dev["address"], self._token)

    def _on_scan_finished(self) -> None:
        if self._state != "scanning":
            return
        if self._devices:
            best = max(self._devices, key=lambda d: int(d.get("rssi", -999) or -999))
            self.connect_device(best["address"], self._token)
            return
        self._fail(self.tr("没有找到板卡 {}，请确认板子已上电并在广播范围内")
                   .format(self.board_name))

    # ── 命令/应答 ────────────────────────────────────────
    def _send(self, cmd: str, on_ok: Callable, timeout_ms: int = 5000) -> None:
        if self._pending is not None:
            self._fail(self.tr("上一条命令还没完成，请稍候"))
            return
        self._pending = (cmd.split(" ", 1)[0], on_ok)
        self._cmd_timeout.start(timeout_ms)
        self._link.send_line(cmd)

    def _finish(self, ok: bool, detail: str = "") -> None:
        if self._pending is None:
            return
        label, on_ok = self._pending
        self._pending = None
        self._cmd_timeout.stop()
        if ok:
            on_ok()
        else:
            self._fail(self.tr("{} 失败：{}").format(label, detail or self.tr("板子返回错误")))

    def _on_cmd_timeout(self) -> None:
        self._finish(False, self.tr("等待板子应答超时"))

    def _on_line(self, line: str) -> None:
        line = line.strip("\r\0")
        if not line:
            return
        if line == "#OK" or line.startswith("#ERR"):
            self._finish(line == "#OK", "" if line == "#OK" else line[5:].strip())
            return
        if line.startswith("#OK "):
            # 认证应答是 "#OK auth"，也按成功处理
            self._finish(True)
            return
        if line.startswith("{"):
            try:
                self._last_json = json.loads(line)
            except json.JSONDecodeError:
                self._last_json = {}
                return
            obj = self._last_json
            if "nets_start" in obj:
                self._nets = []
                self.netsChanged.emit()
            elif "net" in obj and isinstance(obj["net"], dict):
                self._nets.append(obj["net"])
                self.netsChanged.emit()
            elif "nets_end" in obj:
                # 列表结束；保持 nets 给 _on_scan_wifi_ok 收尾
                pass
            return
        # 其他文本行（例如 help / 诊断）先忽略，避免误判 #OK

    # ── 认证/命令回调 ────────────────────────────────────
    def _on_link_connected(self) -> None:
        self._set_state("authenticating")
        self._send(self._token, self._on_auth_ok, timeout_ms=5000)

    def _on_auth_ok(self) -> None:
        # 先读一次状态；读完之前保持 authenticating（busy），
        # 避免“扫描 Wi-Fi”在 status 还在途时被点到而报“上一条没完成”。
        self._send("wifi status", self._on_status_ok, timeout_ms=5000)

    def _on_status_ok(self) -> None:
        self._apply_status(self._last_json)
        self._set_state("reading")
        # 用户流程：BLE 连上后直接扫描 Wi-Fi，不再需要点「扫描 Wi-Fi」。
        QTimer.singleShot(0, self._auto_scan_wifi)

    def _auto_scan_wifi(self) -> None:
        # 用户可能在 status 回来的瞬间已经点了某个网络（进入 provisioning），
        # 这时不要再补一次扫描。
        if self._state == "reading" and self._pending is None:
            self.scan_wifi()

    def _apply_status(self, obj: dict) -> None:
        self._saved_ssid = str(obj.get("ssid", "") or "")
        self._ip = str(obj.get("ip", "") or "")
        self._rssi = int(obj.get("rssi", 0) or 0)
        self.statusChanged.emit()

    def _on_scan_wifi_ok(self) -> None:
        # 新固件逐条输出 {"net":{...}}，旧固件仍可能给 {"nets":[...]}。
        if not self._nets and isinstance(self._last_json.get("nets"), list):
            self._nets = list(self._last_json["nets"])
        self.netsChanged.emit()
        self._set_state("reading")

    def _on_set_ok(self) -> None:
        self._poll_count = 0
        self._poll_timer.start(800)

    def _poll_status(self) -> None:
        if self._state != "provisioning":
            return
        self._poll_count += 1
        if self._poll_count > 35:       # 约 28s
            self._rollback_and_fail(self.tr("超时未拿到 IP，已回滚到原网络"))
            return
        self._send("wifi status", self._on_poll_ok, timeout_ms=4000)

    def _on_poll_ok(self) -> None:
        obj = self._last_json
        self._apply_status(obj)
        state = str(obj.get("state", "") or "")
        connected = bool(obj.get("connected", False))
        if connected or state == "connected":
            self._set_state("connected")
            self._poll_timer.stop()
            self.provisioned.emit(self._ip, self._saved_ssid or self._ssid)
            self._link.disconnect_device()
            return
        if state == "failed":
            reason = str(obj.get("reason", "") or "")
            self._rollback_and_fail(self._reason_text(reason))
            return
        if self._poll_count >= 3:
            self._set_state("provisioning")   # 保持状态，继续轮询
        self._poll_timer.start(1000)

    def _reason_text(self, reason: str) -> str:
        table = {
            "auth": self.tr("密码错误或认证失败"),
            "no_ap": self.tr("找不到该 Wi-Fi（请确认是 2.4GHz）"),
            "timeout": self.tr("连接超时"),
            "dhcp": self.tr("已连上但没拿到 IP（检查路由器 DHCP）"),
            "internal": self.tr("板子内部错误"),
        }
        return table.get(reason, reason or self.tr("未知原因"))

    def _rollback_and_fail(self, why: str) -> None:
        self._poll_timer.stop()
        self._error = why
        # 板子侧也会自动回滚；这里再显式发一条，用户可以从失败状态直接重试
        try:
            self._link.send_line("wifi rollback")
        except Exception:
            pass
        self._set_state("failed")
        self.failed.emit(why)

    def _on_forget_ok(self) -> None:
        old = self._saved_ssid
        self._saved_ssid = ""
        self._ip = ""
        self._rssi = 0
        self.statusChanged.emit()
        self._set_state("reading")
        self.forgotten.emit(old)

    def _on_rollback_ok(self) -> None:
        self._set_state("reading")
