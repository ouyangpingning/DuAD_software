#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LightBridge / LightPage 回归测试 —— 进程内假控制器 + offscreen 页面。

运行（不需要真控制器、不需要相机）：
    source DuAD_SoftwareContent/pyqml/bin/activate
    QT_QPA_PLATFORM=offscreen python3 tests/test_light_bridge.py

假控制器严格照抄真机行为（2026-09-22 用 /dev/ttyUSB1 实测，4 通道机型）：
  · **只认 19200**：9600 / 38400 / 115200 下一个字都不回
        ← 这是本文件存在的理由。旧测试的假串口对波特率完全不敏感，
          于是真机上最要命的缺陷（默认 9600 → 完全没法控制）在测试里根本不存在。
  · `$L0=128#` → `+OK`；`$RD=0#` → `$L0=0,T0=100,F0=1#`
  · `$RD=9999#` → `$ID=0,L0=..,L3=..,TR=0,..,SN=..#`
  · 错误码：E2 数据类型（`$L0=abc#`）/ E6 数据超范围（`$L0=999#`）/ E7 通道号超范围（`$L9=100#`）
  · 未知命令名（`$ZZ=1#`）**无任何应答**
  · 缺 `#` 无应答；`#` 后多余 `\\r\\n` 被忽略
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "DuAD_SoftwareContent"
sys.path.insert(0, str(ROOT / "backend"))

from PySide6.QtCore import QMetaObject, QObject, QUrl                       # noqa: E402
from PySide6.QtGui import QGuiApplication                                   # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine                             # noqa: E402

import Src.light_bridge as lb                                              # noqa: E402
from Src.light_bridge import SERIAL_BAUD, LightBridge                      # noqa: E402

_PASS = 0
_FAIL = 0


def check(name, cond, extra=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}  {extra}")


# ============================================================
# 假控制器（内存版，真机语义）
# ============================================================
class FakeController:
    def __init__(self):
        self.received = []          # 收到的原始指令（bytes，不含 # 后的空白）
        self.params = {
            "ID": "0",
            "L0": 0, "T0": 100, "F0": 1,
            "L1": 0, "T1": 100, "F1": 1,
            "L2": 100, "T2": 100, "F2": 1,
            "L3": 100, "T3": 100, "F3": 1,
            "TR": 0, "LC": 0,
            "SN": "066CFF514949877187204711",
        }

    def _read_all_reply(self):
        p = self.params
        seq = ["ID=" + p["ID"]]
        for i in range(4):
            seq += [f"L{i}={p[f'L{i}']}", f"T{i}={p[f'T{i}']}", f"F{i}={p[f'F{i}']}"]
        seq += [f"TR={p['TR']}", f"LC={p['LC']}", "ALT11=1", "UT=50", "DHCP=0", "NE=2",
                "IP=192.168.1.2", "IU=255.255.255.0", "IS=192.168.1.1", "IL=1200",
                "DP=192.168.1.3", "DL=1200", "MC=88-71-87-20-47-11",
                "SN=" + p["SN"]]
        return "$" + ",".join(seq) + "#"

    def handle(self, data: bytes):
        """按真机规则产生应答；返回 bytes（空 = 不应答）。"""
        raw = data.strip(b"\r\n")
        self.received.append(raw)
        if not raw.startswith(b"$") or not raw.endswith(b"#"):
            return b""                                  # 缺 '#' → 无应答
        cmd = raw[1:-1].decode("ascii", "ignore")
        if not cmd:
            return b""

        # 读指令
        if cmd.startswith("RD="):
            val = cmd[3:]
            if not val.isdigit():
                return b"E2"
            n = int(val)
            if n == 9999:
                return self._read_all_reply().encode()
            if n > 3:
                return b"E7"
            p = self.params
            return f"$L{n}={p[f'L{n}']},T{n}={p[f'T{n}']},F{n}={p[f'F{n}']}#".encode()

        # 写指令：<键><通道>=<值>
        key, _, val = cmd.partition("=")
        if len(key) == 2 and key[0] in "LTF" and key[1].isdigit():
            ch = int(key[1])
            if ch > 3:
                return b"E7"
            if not val.isdigit():
                return b"E2"
            num = int(val)
            limit = 999 if key[0] == "T" else 255
            if key[0] == "F" and num > 1:
                return b"E6"
            if num > limit:
                return b"E6"
            self.params[key] = num
            return b"+OK"

        if key == "TR" and val.isdigit():
            if int(val) > 3:
                return b"E6"
            self.params["TR"] = int(val)
            return b"+OK"
        if key == "LC" and val.isdigit():
            self.params["LC"] = int(val)
            return b"+OK"
        if cmd == "SA=1":
            return b"+OK"
        if cmd == "RS=1":
            return b"+OK"
        return b""                                      # 未知命令名 → 真机也不回


class FakeSerial:
    """pyserial.Serial 的替身：只认 19200，逐片返回应答。"""

    controller = FakeController()

    #: 真机是 32 字节/片、片间隔 ~20ms；这里把间隔放大到 60ms，
    #: 好让"读到半截就缩短等待时间"的写法当场现形（真机上就是这么把
    #: 241 字节应答的最后一片切掉、残留字节粘到下一条 `+OK` 上的）。
    CHUNK = 32
    CHUNK_GAP = 0.06

    def __init__(self, port=None, baudrate=9600, bytesize=None, parity=None,
                 stopbits=None, timeout=None, write_timeout=None, **kw):
        self.port = port
        self.baudrate = baudrate
        self.is_open = True
        self.writes = []            # 本串口收到的字节
        self._queue = []            # [(due_time, bytes), ...]

    # ── 真机事实：波特率不是 19200 就完全没有应答 ──
    @property
    def _answering(self):
        return int(self.baudrate) == SERIAL_BAUD

    def write(self, data: bytes):
        self.writes.append(bytes(data))
        if self._answering:
            payload = self.controller.handle(bytes(data))
            now = time.monotonic()
            for i in range(0, len(payload), self.CHUNK):
                self._queue.append((now + (i // self.CHUNK) * self.CHUNK_GAP,
                                    payload[i:i + self.CHUNK]))
        return len(data)

    def flush(self):
        pass

    def reset_input_buffer(self):
        self._queue.clear()

    def read(self, size=1):
        while True:
            now = time.monotonic()
            ready = b""
            while self._queue and self._queue[0][0] <= now and len(ready) < size:
                _, part = self._queue.pop(0)
                ready += part
            if ready:
                return ready[:size]
            if not self._queue:
                time.sleep(0.02)        # 模拟 timeout=0.02 的空读
                return b""
            time.sleep(min(0.005, self._queue[0][0] - now))

    def close(self):
        self.is_open = False


class _SerialShim:
    """只把 `Serial` 换成替身，其余 pyserial 常量/子模块原样透传。"""
    Serial = FakeSerial

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)


def pump(app, sec):
    end = time.time() + sec
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


# ============================================================
HARNESS = b"""
import QtQuick
import QtQuick.Controls
import "pages"

Window {
    width: 900
    height: 1400
    visible: true
    LightPage {
        objectName: "lightPage"
        anchors.fill: parent
    }
}
"""


def main():
    app = QGuiApplication(sys.argv[:1])
    real_serial = lb.serial
    lb.serial = _SerialShim(real_serial)          # ★ 之后 new 出来的串口都是替身
    FakeSerial.controller = FakeController()

    print("=== 1. 串口配置 / 波特率 ===")
    check("手册规定波特率 = 19200（界面默认值也来自它）", SERIAL_BAUD == 19200,
          f"实际 {SERIAL_BAUD}")

    bridge = LightBridge()
    FakeSerial.controller = FakeController()

    # ── 1a. 波特率不对必须**连不上**，不能留下"已连接但没反应"的假状态 ──
    ok9600 = bridge.connectSerial("/dev/fake", 9600, "8", "1", "NONE")
    check("9600 下连接被拒绝（真机在该波特率完全无应答）", ok9600 is False)
    check("9600 失败后没有留下已连接状态", bridge.connected is False)

    errs = []
    bridge.serialError.connect(lambda m: errs.append(m))
    bridge.connectSerial("/dev/fake", 9600, "8", "1", "NONE")
    pump(app, 0.05)
    check("错误提示里点名 19200（否则用户不知道改哪儿）",
          any("19200" in e for e in errs), str(errs))

    # ── 1b. 19200 下连接成功，并回读控制器参数 ──
    errs.clear()
    ok = bridge.connectSerial("/dev/fake", 19200, "8", "1", "NONE")
    check("19200 下连接成功", ok is True)
    check("连接后 connected = True", bridge.connected is True)
    check("连接后回读到真实通道值 [0,0,100,100]",
          list(bridge.channelValues) == [0, 0, 100, 100], str(bridge.channelValues))
    # ★ 真机实测：$RD=9999# 的应答 241 字节分 7 片、跨 161ms 才发完。
    #   读到一半就放弃的话，残留字节会粘到下一条应答上（真机上出现过
    #   `...SN=066CFF...#+OK`，于是 +OK 认不出来、明明设成功却报失败）。
    check("长应答被完整读完（末尾未截断）",
          bridge.lastResponse.endswith("SN=066CFF514949877187204711#"),
          repr(bridge.lastResponse[-40:]))
    check("长应答没有被截断成半截（参数行完整）",
          "ID=0" in bridge.lastResponse and "MC=" in bridge.lastResponse,
          repr(bridge.lastResponse[:40]))
    check("回读到通道数 = 4", bridge.channelCount == 4, str(bridge.channelCount))
    check("回读到触发方式 TR=0", bridge.trigMode == 0, str(bridge.trigMode))
    check("设备摘要含通道数与 SN",
          "4 通道" in bridge.deviceInfo and "SN=066CFF" in bridge.deviceInfo,
          bridge.deviceInfo)

    print("=== 2. 亮度指令（$L{通道}={值}#）===")
    ser = bridge._ser
    ser.writes.clear()
    ok = bridge.setLightValue(0, 128)
    check("setLightValue(0,128) 返回成功", ok is True)
    check("实际发出 $L0=128#", ser.writes and ser.writes[-1] == b"$L0=128#",
          repr(ser.writes))
    check("lastCommand = $L0=128#", bridge.lastCommand == "$L0=128#", bridge.lastCommand)
    check("应答 +OK 被记录", bridge.lastResponse == "+OK", bridge.lastResponse)
    check("回读值同步为 128", bridge.channelValues[0] == 128, str(bridge.channelValues))

    ser.writes.clear()
    bridge.setLightValue(2, 255)
    check("通道 2（面板通道3）发出 $L2=255#", ser.writes[-1] == b"$L2=255#",
          repr(ser.writes))

    print("=== 3. 指令格式（手册四.2 注 1/3：$ 开头 # 结尾、大写、无空格）===")
    ser.writes.clear()
    bridge.setLightValue(1, 7)
    raw = ser.writes[-1]
    check("以 $ 开头", raw.startswith(b"$"))
    check("以 # 结束", raw.endswith(b"#"))
    check("没有空格", b" " not in raw)
    check("字母大写", raw == raw.upper())

    print("=== 4. 越界与错误码 ===")
    ser.writes.clear()
    bridge.setLightValue(0, 999)
    check("亮度 999 被钳到 255 再下发（控制器否则回 E6）",
          ser.writes[-1] == b"$L0=255#", repr(ser.writes))
    errs.clear()
    bridge.setLightValue(9, 10)          # 协议上限 L0~L7 → 假控制器回 E7
    pump(app, 0.05)
    check("通道 9 被钳到 7", ser.writes[-1] == b"$L7=10#", repr(ser.writes))
    check("控制器回 E7 时上报错误（不再静默）",
          any("E7" in e for e in errs), str(errs))

    print("=== 5. 触发方式 / 保存 ===")
    ser.writes.clear()
    check("setTrigMode(3) 成功", bridge.setTrigMode(3) is True)
    check("实际发出 $TR=3#", ser.writes[-1] == b"$TR=3#", repr(ser.writes))
    check("trigMode 同步为 3", bridge.trigMode == 3, str(bridge.trigMode))
    ser.writes.clear()
    check("保存指令 $SA=1#", bridge.saveToDevice() is True and ser.writes[-1] == b"$SA=1#",
          repr(ser.writes))

    print("=== 6. LightPage（offscreen）：滑块 → 串口 ===")
    eng = QQmlApplicationEngine()
    eng.addImportPath(str(CONTENT))
    eng.rootContext().setContextProperty("LightBridge", bridge)
    eng.loadData(HARNESS, QUrl.fromLocalFile(str(CONTENT / "_lighttest.qml")))
    pump(app, 0.6)
    if not eng.rootObjects():
        check("LightPage 能加载", False, "QML 根对象为空")
        return finish()
    win = eng.rootObjects()[0]
    page = win.findChild(QObject, "lightPage")
    check("LightPage 加载成功", page is not None)

    slider0 = page.findChild(QObject, "lightSlider0")
    check("找到光源1滑块", slider0 is not None)
    if slider0 is not None:
        # ★ 滑块发指令走的是 SliderRow.released(real value) → onReleased 里的 `value`
        #   参数。这一步就是在验"松手时到底把几发下去了"。
        slider0.setProperty("sliderValue", 200)
        invoked = QMetaObject.invokeMethod(slider0, "commitValue")
        pump(app, 0.2)
        sent = [w for w in bridge._ser.writes if w.startswith(b"$L")]
        check("滑块提交后真的发出 $L0=200#（不是 0、不是 NaN）",
              invoked and any(w == b"$L0=200#" for w in sent), repr(bridge._ser.writes))

    combo = page.findChild(QObject, "trigModeCombo")
    check("触发方式下拉存在", combo is not None)
    save = page.findChild(QObject, "lightSaveButton")
    check("保存按钮存在", save is not None)
    check("面板显示的触发方式 = 控制器上报值",
          combo is not None and combo.property("currentIndex") == bridge.trigMode,
          "" if combo is None else str(combo.property("currentIndex")))

    print("=== 7. 串口默认参数来自后端唯一事实源 ===")
    panel = page.findChild(QObject, "serialPanel")
    if panel is None:
        # SerialSettingsPanel 没有 objectName，退而求其次在页面里找同类型对象
        check("找得到串口设置面板", False, "SerialSettingsPanel 缺 objectName")
    else:
        check("串口面板默认波特率 = 19200（不是 9600）",
              str(panel.property("baudRate")) == "19200",
              str(panel.property("baudRate")))
        # ⚠ 真机实测：Linux 的 comports() 会报出主板 ttyS0~31，排序后 ports[0]
        #   就是 ttyS0 —— 自动选口选中它，点连接必然失败（"点了没反应"）。
        bridge.portsChanged.emit(["/dev/ttyS0", "/dev/ttyS1", "/dev/ttyS2", "/dev/ttyUSB1"])
        pump(app, 0.2)
        check("自动选口优先 ttyUSB1（不会被 ttyS0 抢走）",
              str(panel.property("portName")) == "/dev/ttyUSB1",
              str(panel.property("portName")))
        check("LightBridge.defaultPort 也优先 USB",
              bridge.pickDefaultPort(["/dev/ttyS0", "/dev/ttyUSB1"]) == "/dev/ttyUSB1"
              and bridge.pickDefaultPort(["COM3"]) == "COM3"
              and bridge.pickDefaultPort([]) == "",
              bridge.pickDefaultPort(["/dev/ttyS0", "/dev/ttyUSB1"]))

    bridge.disconnectSerial()
    return finish()


def finish():
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
