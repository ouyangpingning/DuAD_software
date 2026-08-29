"""LightBridge 冒烟：光源指令格式与串口写入。"""
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtGui import QGuiApplication

PROJECT = os.environ.get(
    "DUAD_PROJECT_ROOT",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT, "backend"))

from Src.light_bridge import LightBridge


class FakeSerial:
    is_open = True

    def __init__(self, response=b""):
        self.response = response
        self.written = []

    def write(self, data):
        self.written.append(data)

    def flush(self):
        pass

    def read_all(self):
        return self.response


app = QGuiApplication(sys.argv)
results = []


def check(name, cond):
    results.append((name, cond))
    print(("PASS" if cond else "FAIL"), "-", name)


bridge = LightBridge()
ser = FakeSerial()
bridge._ser = ser

check("发送通道 2 = 128", bridge.setLightValue(2, 128) is True)
check("串口收到 $L2=128#", b"$L2=128#" in ser.written)
check("lastCommand 正确", bridge.lastCommand == "$L2=128#")

ser2 = FakeSerial(b"OK\r\n")
bridge._ser = ser2
bridge.setLightValue(0, 255)
check("发送通道 0 = 255", b"$L0=255#" in ser2.written)
check("收到控制器响应", bridge.lastResponse == "OK")

check("亮度越界自动钳制", bridge.setLightValue(3, 999) is True
      and b"$L3=255#" in ser2.written)

failed = [n for n, ok in results if not ok]
print()
print(f"{len(results) - len(failed)}/{len(results)} passed")
sys.exit(1 if failed else 0)
