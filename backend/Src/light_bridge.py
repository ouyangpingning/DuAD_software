"""LightBridge — 光源控制器串口桥（RS-232 / USB-RS232）。

协议依据：控制器随附《数字控制器使用说明书》"四、通讯参数"，并于 2026-09-22 用
真机（/dev/ttyUSB1，4 通道机型，SN=066CFF514949877187204711）逐条实测核对。

串口配置（手册 四.1；真机实测一致）
    RS-232 · 半双工 · **19200bps** · 起始位 1 / 数据位 8 / 校验位 0 / 停止位 1
    ⚠ **波特率必须 19200**。实测 9600 / 38400 / 115200 下控制器一个字都不回，
      而 `serial.Serial()` 打开串口**永远成功** —— 于是界面显示"已连接"、
      滑块怎么拖都没反应。这正是"上位机软件没法控制光源"的根因。
      → 所以 `connectSerial()` 打开串口后**必须**发 `$RD=9999#` 校验链路，
        收不到应答就当连接失败并给出可操作提示（fail loudly，不要静默）。

ASCII 指令（手册 四.2；括号内为真机实测应答）
    $L{n}={v}#    通道 n 亮度，n=0~7（L0 即面板"通道 1"），v=0~255        (+OK)
    $T{n}={ms}#   通道 n 发光时间，1~999ms                                (+OK)
    $F{n}={0|1}#  通道 n 输出使能，0=OFF / 1=ON                           (+OK)
    $TR={0..3}#   触发方式：0=E0L 跟随低 / 1=E1H 跟随高 /
                            2=E2L 下降沿 / 3=E3H 上升沿                   (+OK)
    $RD={n}#      读通道 n 参数（n=0~7）          ($L0=0,T0=100,F0=1#)
    $RD=9999#     读控制器全部参数                ($ID=0,L0=..,TR=0,..,SN=..#)
    $LC={0|1}#    界面锁 / 解锁        $SA=1# 保存参数（掉电保存）  $RS=1# 恢复出厂
    $NE=2;IP=192.168.1.2;#   设置网络参数（ASCII，分号分隔）

    应答：`+OK` 成功；`E1`~`ER` 失败（含义见 _ERR_CODES，实测一致）；
          读指令回 `$...=...#`。
    书写规则（手册四.2 注 1~3）：以 `$` 开始、`#` 结束，中间不能有空格，
    字母必须大写。实测缺 `#` 无应答，`#` 后多余的 `\\r\\n` 会被忽略。

    控制器回复指令表（手册 四.2 表 13）
        返回读取信息 / +OK / E1 命令格式 / E2 数据格式 / E3 命令名称 /
        E4 通道名称 / E5 命令名称长度 / E6 数据长度 / E7 通道长度 / ER 其它

⚠ 多条**完整指令**拼接时控制器"仅执行最后一条"（手册四.2 注 2），
  所以这里永远一条一条发，不把多条 `$...#` 拼成一个包。
⚠ 出厂默认 `TR=0`（E0L 外部跟随低电平触发）：灯的亮灭由 TRIG IN 端口的电平
  决定。若 TRIG 未接线，先把触发方式设成与实际接线相符的模式再判断"灯不亮"
  是不是软件的问题。
"""
import glob
import threading
import time

from PySide6.QtCore import QObject, Slot, Signal, Property, QTimer

try:
    import serial
    import serial.tools.list_ports
    _HAS_PYSERIAL = True
except Exception as _e:  # 无 pyserial 时界面仍可加载，连接/发送给出错误
    serial = None
    _HAS_PYSERIAL = False
    print(f"[LightBridge] pyserial 不可用: {_e}")

#: 手册 四.1：RS-232 · 半双工 · 19200bps · 8N1（**不是 9600**）
SERIAL_BAUD = 19200
#: 手册 L0~L7，最多 8 通道（真机 4 通道机型上报 L0~L3）
MAX_CHANNELS = 8
#: 单条指令等待应答的上限；控制器应答很快，这里只为不把 UI 卡死
REPLY_TIMEOUT = 0.25
#: `$RD=9999#` 的应答很长，必须给足时间
#: —— 实测真机 241 字节分 7 片、跨 161ms 才发完（19200bps，约 20ms/片）
QUERY_TIMEOUT = 0.8

#: 自动选串口时优先的前缀 —— USB 转串口才是光源控制器该连的口
_USB_PORT_PREFIXES = ("/dev/ttyCH341USB", "/dev/ttyUSB", "/dev/ttyACM")

TRIG_NAMES = {
    0: "E0L 外部跟随低电平",
    1: "E1H 外部跟随高电平",
    2: "E2L 外部下降沿触发",
    3: "E3H 外部上升沿触发",
}

# 手册 四.2 表 13；★ = 真机实测确认过的
_ERR_CODES = {
    "E1": "命令格式有误",
    "E2": "数据类型有误",     # ★ $L0=abc# → E2
    "E3": "命令名称有误",
    "E4": "通道名称有误",
    "E5": "命令名称长度有误",
    "E6": "数据超出范围",     # ★ $L0=999# → E6
    "E7": "通道号超出范围",   # ★ $L9=100# → E7
    "ER": "其它错误",
}

_BYTESIZES = {
    "5": None, "6": None, "7": None, "8": None,
}
_PARITIES = {
    "NONE": None, "EVEN": None, "ODD": None, "MARK": None, "SPACE": None,
}
_STOPBITS = {"1": None, "1.5": None, "2": None}

if _HAS_PYSERIAL:
    _BYTESIZES.update({
        "5": serial.FIVEBITS, "6": serial.SIXBITS,
        "7": serial.SEVENBITS, "8": serial.EIGHTBITS,
    })
    _PARITIES.update({
        "NONE": serial.PARITY_NONE, "EVEN": serial.PARITY_EVEN,
        "ODD": serial.PARITY_ODD, "MARK": serial.PARITY_MARK,
        "SPACE": serial.PARITY_SPACE,
    })
    _STOPBITS.update({
        "1": serial.STOPBITS_ONE,
        "1.5": serial.STOPBITS_ONE_POINT_FIVE,
        "2": serial.STOPBITS_TWO,
    })


class LightBridge(QObject):
    """QML 可调用的光源串口桥。"""

    portsChanged = Signal(list)       # ["/dev/ttyUSB0", ...]
    serialConnected = Signal()
    serialDisconnected = Signal()
    serialError = Signal(str)
    commandSent = Signal(str)
    responseReceived = Signal(str)
    lastCommandChanged = Signal()
    lastResponseChanged = Signal()
    connectedChanged = Signal()
    #: 控制器参数发生变化（连接校验 / 读回 / 写入成功）—— QML 靠它同步滑块
    deviceStateChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ser = None
        self._io_lock = threading.Lock()
        self._last_command = ""
        self._last_response = ""
        self._ports = []

        # 控制器实际参数（连上/写入后回读得到，界面以此为准而不是"以为是多少"）
        self._channels = [0] * MAX_CHANNELS
        self._times = [100] * MAX_CHANNELS
        self._enables = [1] * MAX_CHANNELS
        self._channel_count = 0
        self._trig_mode = 0
        self._locked = 0
        self._device_id = None
        self._serial_number = ""

        # 老项目每 2s 刷新串口列表；未连接时轮询开销很小
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(2000)
        self._refresh_timer.timeout.connect(self.refreshPorts)
        self._refresh_timer.start()
        self.refreshPorts()

    # ── 状态属性 ──────────────────────────────────────────
    def _getConnected(self) -> bool:
        return self._ser is not None and getattr(self._ser, "is_open", False)

    connected = Property(bool, _getConnected, notify=connectedChanged)

    def _getDefaultBaud(self) -> int:
        return SERIAL_BAUD

    #: 手册规定的唯一波特率 —— QML 读它，别在界面里再写一遍 19200（会漂移）
    defaultBaud = Property(int, _getDefaultBaud, constant=True)

    @staticmethod
    def _pickPort(ports) -> str:
        """优先 USB 转串口，其次第一个；没有就空串。"""
        for p in ports or ():
            if str(p).startswith(_USB_PORT_PREFIXES):
                return str(p)
        return str(ports[0]) if ports else ""

    def _getDefaultPort(self) -> str:
        """自动选口：优先 USB 转串口，别选主板自带的 /dev/ttyS0。

        ⚠ 实测（Linux）`serial.tools.list_ports.comports()` 会把 8250 的
          `/dev/ttyS0`~`ttyS31` 一起报出来，排序后 `ports[0]` 就是 `ttyS0` ——
          界面自动选中它，点连接当然连不上（"点了没反应"的又一个来源）。
        """
        return self._pickPort(self._ports)

    defaultPort = Property(str, _getDefaultPort, notify=portsChanged)

    @Slot(list, result=str)
    def pickDefaultPort(self, ports) -> str:
        """给 QML 在 `portsChanged` 回调里用 —— 按**回调收到的列表**算。

        比读 `defaultPort` 稳：不依赖"信号发出时 Python 侧状态已经更新"这个顺序。
        """
        return self._pickPort(ports)

    def _getLastCommand(self) -> str:
        return self._last_command

    lastCommand = Property(str, _getLastCommand, notify=lastCommandChanged)

    def _getLastResponse(self) -> str:
        return self._last_response

    lastResponse = Property(str, _getLastResponse, notify=lastResponseChanged)

    def _getChannelValues(self) -> list:
        n = self._channel_count or 4
        return [int(v) for v in self._channels[:n]]

    #: 控制器上报的各通道亮度（通道 0 = 面板"通道 1"）
    channelValues = Property(list, _getChannelValues, notify=deviceStateChanged)

    def _getChannelCount(self) -> int:
        return self._channel_count

    channelCount = Property(int, _getChannelCount, notify=deviceStateChanged)

    def _getTrigMode(self) -> int:
        return self._trig_mode

    #: 触发方式 0~3（手册：0=E0L 1=E1H 2=E2L 3=E3H）
    trigMode = Property(int, _getTrigMode, notify=deviceStateChanged)

    def _getLocked(self) -> bool:
        return bool(self._locked)

    locked = Property(bool, _getLocked, notify=deviceStateChanged)

    def _getDeviceInfo(self) -> str:
        """一行摘要，给界面的只读行显示（"我到底连上了什么东西"）。"""
        if not self.connected:
            return ""
        parts = []
        if self._device_id is not None:
            parts.append(f"ID={self._device_id}")
        if self._channel_count:
            parts.append(f"{self._channel_count} 通道")
        parts.append("触发 " + TRIG_NAMES.get(self._trig_mode, str(self._trig_mode)).split(" ")[0])
        parts.append("已锁定" if self._locked else "未锁定")
        if self._serial_number:
            parts.append(f"SN={self._serial_number}")
        return " · ".join(parts)

    deviceInfo = Property(str, _getDeviceInfo, notify=deviceStateChanged)

    # ── 串口扫描 ──────────────────────────────────────────
    @Slot()
    def refreshPorts(self):
        ports = []
        try:
            if _HAS_PYSERIAL:
                ports = [p.device for p in serial.tools.list_ports.comports()]
        except Exception as e:
            print(f"[LightBridge] 扫描串口失败: {e}")
        try:
            ports += glob.glob("/dev/ttyCH341USB*")
            ports += glob.glob("/dev/ttyUSB*")
        except Exception:
            pass
        ports = sorted(set(ports))
        if ports != self._ports:
            self._ports = ports
            self.portsChanged.emit(list(ports))
        return list(ports)

    @Slot(result=list)
    def listPorts(self):
        return list(self._ports)

    # ── 连接 / 断开 ───────────────────────────────────────
    @Slot(str, int, str, str, str, result=bool)
    def connectSerial(self, port: str, baud: int, data_bits: str,
                      stop_bits: str, parity: str) -> bool:
        if not _HAS_PYSERIAL:
            self.serialError.emit("pyserial 未安装，无法使用光源控制器")
            return False
        if self.connected:
            self.serialError.emit("光源控制器已连接")
            return False
        if not port:
            self.serialError.emit("请选择有效的串口")
            return False
        try:
            ser = serial.Serial(
                port=port,
                baudrate=int(baud),
                bytesize=_BYTESIZES.get(str(data_bits), serial.EIGHTBITS),
                parity=_PARITIES.get(str(parity).upper(), serial.PARITY_NONE),
                stopbits=_STOPBITS.get(str(stop_bits), serial.STOPBITS_ONE),
                timeout=0.02,          # 短超时：_read_reply 自己轮询到 deadline
                write_timeout=1.0,     # 写不动时不要永久卡住 UI 线程
            )
        except Exception as e:
            print(f"[LightBridge] 串口打开失败: {e}")
            self._ser = None
            self.serialError.emit(f"打开串口失败: {e}")
            return False

        self._ser = ser
        self._last_command = ""
        self._last_response = ""
        print(f"[LightBridge] 已打开 {port} @ {baud}，正在校验控制器应答…")

        # ★ 打开串口永远成功，波特率错了照样"连上" —— 必须用读指令验一次。
        #   校验不过就断开并报错，绝不能留下"已连接但没反应"的假状态。
        if not self.queryAll():
            try:
                ser.close()
            except Exception:
                pass
            self._ser = None
            self.connectedChanged.emit()
            self.serialError.emit(
                f"{port} @ {baud} 收不到控制器应答。请依次检查："
                "① 波特率必须是 19200（手册四.1；实测 9600/38400/115200 全部无应答）；"
                "② RS-232 线序：控制器只用 2/3/5 脚（RXD/TXD/GND），别接成 TTL 电平；"
                "③ 串口是否被其它程序占用。"
            )
            return False

        self.connectedChanged.emit()
        self.serialConnected.emit()
        print(f"[LightBridge] 光源控制器已连接并通过校验: {port} @ {baud} (8{parity}{stop_bits})")
        return True

    @Slot()
    def disconnectSerial(self):
        if self._ser is None:
            return
        try:
            self._ser.close()
        except Exception as e:
            print(f"[LightBridge] 串口关闭失败: {e}")
        self._ser = None
        self._channel_count = 0
        self._device_id = None
        self._serial_number = ""
        self.connectedChanged.emit()
        self.deviceStateChanged.emit()
        self.serialDisconnected.emit()
        print("[LightBridge] 光源串口已断开")

    # ── 底层收发 ──────────────────────────────────────────
    def _drain(self):
        """读干净残留字节，并等总线安静下来再发下一条。

        ⚠ 半双工总线：控制器还在发的时候抢着写，会把上一帧截断、下一条指令
          的应答也会和残留字节粘在一起（实测就是这样把 `+OK` 粘成了
          `...SN=066CFF...#+OK`，`+OK` 认不出来 → 明明设成功了却报失败）。
        """
        ser = self._ser
        if ser is None:
            return
        deadline = time.monotonic() + 0.3
        last = time.monotonic()
        while time.monotonic() < deadline:
            try:
                chunk = ser.read(256)
            except Exception as e:
                print(f"[LightBridge] 串口排空失败: {e}")
                return
            now = time.monotonic()
            if chunk:
                last = now
            elif now - last >= 0.02:      # 20ms 内没新字节 → 总线空闲
                return

    def _read_reply(self, timeout: float = REPLY_TIMEOUT) -> str:
        """读到**一条完整应答**为止。

        控制器有三种应答：`+OK`（成功）、`E?`（错误码）、`$...=...#`（读回值）。
        只有第三种带 `#`，所以不能只等 `#` —— 否则前两种要白等到超时。

        ⚠ 这里**只在 `timeout` 到期时**才放弃，收到半截数据**不缩短**等待时间：
          真机 241 字节的应答要 161ms 才发完，中途按"再来 50ms 没完就算了"
          去截，会把最后一片切掉，残留字节污染下一条指令的应答。
        """
        ser = self._ser
        if ser is None:
            return ""
        buf = bytearray()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                chunk = ser.read(64)
            except Exception as e:
                print(f"[LightBridge] 读串口失败: {e}")
                break
            if not chunk:
                continue
            buf += chunk
            s = bytes(buf).strip()
            if s.endswith(b"#") or s == b"+OK" or (len(s) == 2 and s[:1] == b"E"):
                break
        return bytes(buf).decode("ascii", "ignore").strip()

    def _set_response(self, text: str):
        self._last_response = text
        self.responseReceived.emit(text)
        self.lastResponseChanged.emit()

    def _transact(self, cmd: str, timeout: float = REPLY_TIMEOUT,
                  quiet: bool = False):
        """发一条指令并取应答，返回 `(ok, reply)`。

        quiet=True 时只发不收错误提示（用于连接校验，错误由调用方统一给出）。
        """
        if self._ser is None or not getattr(self._ser, "is_open", False):
            if not quiet:
                self.serialError.emit("光源控制器未连接，无法发送指令")
            return False, ""
        with self._io_lock:
            try:
                self._drain()          # 半双工：先确认总线空闲，再发
                self._ser.write(cmd.encode("ascii"))
                self._ser.flush()
            except Exception as e:
                print(f"[LightBridge] 发送光源指令失败: {e}")
                if not quiet:
                    self.serialError.emit(f"发送失败: {e}")
                return False, ""
            self._last_command = cmd
            self.commandSent.emit(cmd)
            self.lastCommandChanged.emit()
            reply = self._read_reply(timeout)

        # 原始指令与原始应答都打日志 —— "我到底发出去了什么/对面回了什么"
        print(f"[LightBridge] → {cmd}   ← {reply!r}")

        if reply.startswith("$"):
            self._set_response(reply)
            return True, reply
        if reply == "+OK" or reply.startswith("+"):
            self._set_response(reply)
            return True, reply
        if len(reply) == 2 and reply[:1] == "E":
            msg = _ERR_CODES.get(reply, "未知错误")
            self._set_response(f"{reply} {msg}")
            if not quiet:
                self.serialError.emit(f"控制器拒绝 {cmd}：{reply} {msg}")
            return False, reply
        self._set_response("(无应答)")
        if not quiet:
            self.serialError.emit(
                f"控制器对 {cmd} 没有应答（检查波特率是否为 19200、串口线是否松脱）"
            )
        return False, reply

    # ── 协议指令 ──────────────────────────────────────────
    @Slot(result=bool)
    def queryAll(self):
        """`$RD=9999#` 读控制器全部参数 —— 连接校验 + 界面初始化都用它。"""
        ok, reply = self._transact("$RD=9999#", timeout=QUERY_TIMEOUT, quiet=True)
        if not ok or not reply.startswith("$"):
            return False
        self._parse_params(reply)
        self.deviceStateChanged.emit()
        return True

    @Slot(int, result=bool)
    def queryChannel(self, channel: int):
        """`$RD={n}#` 读单个通道参数。"""
        channel = max(0, min(MAX_CHANNELS - 1, int(channel)))
        ok, reply = self._transact(f"$RD={channel}#")
        if ok and reply.startswith("$"):
            self._parse_params(reply)
            self.deviceStateChanged.emit()
        return ok

    def _parse_params(self, reply: str):
        body = reply.strip()
        if body.startswith("$"):
            body = body[1:]
        if body.endswith("#"):
            body = body[:-1]
        kv = {}
        for item in body.split(","):
            if "=" in item:
                k, v = item.split("=", 1)
                kv[k.strip().upper()] = v.strip()

        found = []
        for k, v in kv.items():
            if len(k) >= 2 and k[0] in ("L", "T", "F") and k[1:].isdigit():
                i = int(k[1:])
                if not (0 <= i < MAX_CHANNELS):
                    continue
                try:
                    n = int(v)
                except ValueError:
                    continue
                if k[0] == "L":
                    self._channels[i] = n
                    found.append(i)
                elif k[0] == "T":
                    self._times[i] = n
                else:
                    self._enables[i] = n
        if found:
            self._channel_count = max(found) + 1
        if "ID" in kv:
            self._device_id = kv["ID"]
        if "TR" in kv:
            try:
                self._trig_mode = int(kv["TR"])
            except ValueError:
                pass
        if "LC" in kv:
            try:
                self._locked = int(kv["LC"])
            except ValueError:
                pass
        if "SN" in kv:
            self._serial_number = kv["SN"]

    @Slot(int, int, result=bool)
    def setLightValue(self, channel: int, value: int) -> bool:
        """`$L{通道}={亮度}#` —— 通道 0~7（0 = 面板通道 1），亮度 0~255。"""
        channel = max(0, min(MAX_CHANNELS - 1, int(channel)))
        value = max(0, min(255, int(value)))
        ok, _ = self._transact(f"$L{channel}={value}#")
        if ok:
            self._channels[channel] = value
            if channel + 1 > self._channel_count:
                self._channel_count = channel + 1
            self.deviceStateChanged.emit()
        return ok

    @Slot(int, int, result=bool)
    def setLightTime(self, channel: int, ms: int) -> bool:
        """`$T{通道}={发光时间}#` —— 1~999ms（触发模式下的点亮时长）。"""
        channel = max(0, min(MAX_CHANNELS - 1, int(channel)))
        ms = max(1, min(999, int(ms)))
        ok, _ = self._transact(f"$T{channel}={ms}#")
        if ok:
            self._times[channel] = ms
            self.deviceStateChanged.emit()
        return ok

    @Slot(int, bool, result=bool)
    def setChannelEnable(self, channel: int, enabled: bool) -> bool:
        """`$F{通道}={0|1}#` —— 通道输出使能（0=OFF 1=ON）。"""
        channel = max(0, min(MAX_CHANNELS - 1, int(channel)))
        on = 1 if enabled else 0
        ok, _ = self._transact(f"$F{channel}={on}#")
        if ok:
            self._enables[channel] = on
            self.deviceStateChanged.emit()
        return ok

    @Slot(int, result=bool)
    def setTrigMode(self, mode: int) -> bool:
        """`$TR={0..3}#` —— 0=E0L 1=E1H 2=E2L 3=E3H（详见模块 docstring）。"""
        mode = max(0, min(3, int(mode)))
        ok, _ = self._transact(f"$TR={mode}#")
        if ok:
            self._trig_mode = mode
            self.deviceStateChanged.emit()
        return ok

    @Slot(result=bool)
    def saveToDevice(self) -> bool:
        """`$SA=1#` 把当前参数写进控制器（掉电保存）。

        ⚠ 不要把每次滑块改动都顺手保存 —— 那是往 flash 里反复写。
        """
        return self._transact("$SA=1#")[0]
