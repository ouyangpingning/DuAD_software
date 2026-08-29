"""CollectBridge 冒烟：相机帧 → 按间隔节流保存到指定目录。"""
import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QObject, Signal, QTimer, QEventLoop
from PySide6.QtGui import QGuiApplication

PROJECT = os.environ.get(
    "DUAD_PROJECT_ROOT",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT, "backend"))

import numpy as np
from PIL import Image
from Src.collect_bridge import CollectBridge


class FakeCamera(QObject):
    rawFrameReady = Signal(object)


def wait_until(fn, timeout=3000):
    deadline = time.time() + timeout / 1000.0
    while time.time() < deadline:
        if fn():
            return True
        loop = QEventLoop()
        QTimer.singleShot(40, loop.quit)
        loop.exec()
    return fn()


app = QGuiApplication(sys.argv)
results = []


def check(name, cond):
    results.append((name, cond))
    print(("PASS" if cond else "FAIL"), "-", name)


camera = FakeCamera()
bridge = CollectBridge(camera)
tmp = tempfile.mkdtemp(prefix="duad_collect_")
errors = []
bridge.saveError.connect(lambda msg: errors.append(msg))

check("未配置时 configured=False", bridge.configured is False)
ok = bridge.configure(tmp, "test", "jpg", 0.2)
check("配置保存目录成功", ok and bridge.configured is True)
check("开始前 savedCount=0", bridge.savedCount == 0)

bridge.start()
check("开始后 saving=True", bridge.saving is True)
# 模拟 30fps 相机：每 80ms 一帧，保存线程按 0.2s 间隔取最新帧写盘
for i in range(12):
    camera.rawFrameReady.emit(np.full((32, 48, 3), i, dtype=np.uint8))
    loop = QEventLoop()
    QTimer.singleShot(80, loop.quit)
    loop.exec()

saved = wait_until(lambda: bridge.savedCount >= 3, timeout=5000)
check("节流保存至少 3 张", saved and bridge.savedCount >= 3)

files = sorted(os.listdir(tmp))
check("文件已写入磁盘", len(files) >= 3)
if files:
    p = os.path.join(tmp, files[0])
    img = Image.open(p)
    check("保存文件可解码", img.size == (48, 32))
    check("文件名包含前缀", files[0].startswith("test_"))

bridge.stop()
check("停止后 saving=False", bridge.saving is False)
before = bridge.savedCount
for i in range(10):
    camera.rawFrameReady.emit(np.zeros((8, 8, 3), dtype=np.uint8))
time.sleep(0.2)
check("停止后不再保存", bridge.savedCount == before)

failed = [n for n, ok in results if not ok]
print()
print(f"{len(results) - len(failed)}/{len(results)} passed")
sys.exit(1 if failed else 0)
