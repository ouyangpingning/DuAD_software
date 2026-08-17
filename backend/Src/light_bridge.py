"""LightBridge — 光源控制器串口桥（CH340 USB 串口 LED 光源）。

协议（与老项目 pyqt5/Src/dialogs/light_control.py 一致）：
    $L{通道号}={亮度值}#     通道号 0~3，亮度 0~255
    写入后 flush，并读取串口短响应（timeout=10ms）。

串口参数：
    port / baudrate / bytesize / parity / stopbits
    支持自动扫描 /dev/ttyUSB* 与 /dev/ttyCH341USB*。
"""
import glob
import threading

from PySide6.QtCore import QObject, Slot, Signal, Property, QTimer

try:
    import serial
    import serial.tools.list_ports
    _HAS_PYSERIAL = True
except Exception as _e:  # 无 pyserial 时界面仍可加载，连接/发送给出错误
    serial = None
    _HAS_PYSERIAL = False
    print(f"[LightBridge] pyserial 不可用: {_e}")

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

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ser = None
        self._write_lock = threading.Lock()
        self._last_command = ""
        self._last_response = ""
        self._ports = []

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

    def _getLastCommand(self) -> str:
        return self._last_command

    lastCommand = Property(str, _getLastCommand, notify=lastCommandChanged)

    def _getLastResponse(self) -> str:
        return self._last_response

    lastResponse = Property(str, _getLastResponse, notify=lastResponseChanged)

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
                timeout=0.01,
            )
            self._ser = ser
            self._last_command = ""
            self._last_response = ""
            self.connectedChanged.emit()
            self.serialConnected.emit()
            print(f"[LightBridge] 光源串口已连接: {port} @ {baud}")
            return True
        except Exception as e:
            print(f"[LightBridge] 串口打开失败: {e}")
            self._ser = None
            self.serialError.emit(f"打开串口失败: {e}")
            return False

    @Slot()
    def disconnectSerial(self):
        if self._ser is None:
            return
        try:
            self._ser.close()
        except Exception as e:
            print(f"[LightBridge] 串口关闭失败: {e}")
        self._ser = None
        self.connectedChanged.emit()
        self.serialDisconnected.emit()
        print("[LightBridge] 光源串口已断开")

    # ── 亮度指令（协议来自老项目）────────────────────────
    @Slot(int, int, result=bool)
    def setLightValue(self, channel: int, value: int) -> bool:
        if self._ser is None or not getattr(self._ser, "is_open", False):
            self.serialError.emit("光源控制器未连接，无法发送指令")
            return False
        channel = max(0, min(3, int(channel)))
        value = max(0, min(255, int(value)))
        cmd = f"$L{channel}={value}#"
        try:
            with self._write_lock:
                self._ser.write(cmd.encode("utf-8"))
                self._ser.flush()
                self._last_command = cmd
                self.commandSent.emit(cmd)
                self.lastCommandChanged.emit()
                # 短响应（控制器不回复时 timeout 10ms，不会卡 UI）
                if hasattr(self._ser, "read_all"):
                    data = self._ser.read_all()
                    if data:
                        text = data.decode("utf-8", errors="ignore").strip()
                        if text:
                            self._last_response = text
                            self.responseReceived.emit(text)
                            self.lastResponseChanged.emit()
                            print(f"[LightBridge] 收到响应: {text}")
            print(f"[LightBridge] 发送光源指令: {cmd}")
            return True
        except Exception as e:
            print(f"[LightBridge] 发送光源指令失败: {e}")
            self.serialError.emit(f"发送失败: {e}")
            return False
