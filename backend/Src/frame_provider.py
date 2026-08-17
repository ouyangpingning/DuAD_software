"""QQuickImageProvider for live camera frames.

QML 侧的 Image 不能直接消费 Python 的 QImage，需要通过
``image://camera/<kind>?t=<counter>`` 请求；计数器每次变化时 QML 会重新
向 provider 取图。provider 只保存“最近一帧”，避免 QML 侧堆积图像请求。
"""
import threading

from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageProvider


class CameraFrameProvider(QQuickImageProvider):
    """线程安全的最新帧缓存（original / heatmap / mask）。"""

    def __init__(self):
        super().__init__(QQuickImageProvider.Image)
        self._frames = {}
        self._lock = threading.RLock()

    def set_frame(self, kind: str, image: QImage):
        """写入最新帧。传入 QImage 已经是独立拷贝，跨线程只做引用替换。"""
        if image is None or image.isNull():
            return
        with self._lock:
            self._frames[kind] = image

    def clear(self):
        with self._lock:
            self._frames.clear()

    def clear_frame(self, kind: str):
        with self._lock:
            self._frames.pop(kind, None)

    def has_frame(self, kind: str) -> bool:
        with self._lock:
            return kind in self._frames

    def requestImage(self, id, size, requestedSize):
        # URL 形如 image://camera/original?t=123；只取 provider id 部分
        kind = str(id).split("?")[0]
        with self._lock:
            image = self._frames.get(kind)
            if image is None or image.isNull():
                image = QImage(2, 2, QImage.Format_RGB888)
                image.fill(0x101010)
            # 隐式共享：返回同一 QImage 对象给 C++ 侧时会发生一次浅拷贝；
            # 之后 provider 被替换为新帧不会影响 QML 已取走的旧帧数据。
            return image
