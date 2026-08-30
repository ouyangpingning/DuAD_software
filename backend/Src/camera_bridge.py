"""
CameraBridge — QML 与后端相机驱动的桥接层。

封装 CameraManager/CameraDevice，暴露给 QML 的同步/异步接口：
    搜索（异步信号 camerasFound）→ 连接（信号 cameraOpened/cameraError）
    → 特征读写（getFeature/setFeature 同步调用）→ 断开（cameraClosed）
    采集帧 → frameReady(QImage)（QML 联调用）+ rawFrameReady(np.ndarray)
    （Python 实时推理管线用）

帧显示：CameraBridge 内部维护 CameraFrameProvider，main.py 将其注册为
``image://camera``。QML 侧通过 ``image://camera/original?t=<frameIndex>``
取最近一帧；frameIndex 变化触发 QML 重新取图。

生命周期约定（联调契约）：
    - CameraPage 搜索 + 连接 + 参数读写；断开时释放设备
    - 采集会话（startGather/stopGather）由 main.py 根据 AppBridge.collectingOwner
      仲裁后驱动（本桥只提供原语，不自行决策）
"""
import time

import numpy as np
from PySide6.QtCore import QObject, Slot, Signal, Property, QTimer
from PySide6.QtGui import QImage

from Src.camera import CameraManager, CameraDevice, adjust_to_step, \
    gx_unregister_capture_callback, gx_register_capture_callback
from Src.frame_provider import CameraFrameProvider


# 特征名 → ctypes 类型（按 gxipy 命名前缀推断，与 CameraDevice.set/get_remote_feature 一致）
def _feature_type(name: str) -> str:
    if name.startswith("GX_STRING_"):
        return "string"
    if name.startswith("GX_INT_"):
        return "int"
    if name.startswith("GX_FLOAT_"):
        return "float"
    if name.startswith("GX_BOOL_"):
        return "bool"
    if name.startswith("GX_COMMAND_"):
        return "command"
    return "enum"  # GX_ENUM_*


class CameraBridge(QObject):
    """QML 可调用相机桥。"""

    # ── 信号 ──────────────────────────────────────────────
    camerasFound = Signal(list)        # [{"model": str, "sn": str}, ...]
    cameraOpened = Signal()            # 连接成功（此后可读写特征）
    cameraClosed = Signal()            # 已断开
    cameraError = Signal(str)          # 错误提示（中文，前端直接显示）
    frameReady = Signal(QImage)        # 采集帧（numpy→QImage，QML 联调用）
    rawFrameReady = Signal(object)     # 采集帧 numpy RGB [H,W,3]（实时推理管线用）

    cameraConnectedChanged = Signal()
    frameIndexChanged = Signal()       # 新帧到 → QML 重取 image://camera/original
    imageWidthChanged = Signal()
    imageHeightChanged = Signal()
    gatheringChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._manager = CameraManager(1)
        self._device: CameraDevice = None
        self._deviceInfo = {}          # {"model": str, "sn": str}
        self._frameCache = None        # 最近一帧 numpy（供同步获取/调试）
        self._frameIndex = 0
        self._lastDisplayAt = 0.0    # 原图 QML 显示节流（30fps 足够人眼）
        self._restartAfterGeometry = False  # ROI 写入后待自动重开采集
        self._roiBaseline = None            # 首次 ROI 前的分辨率/OFFSET，恢复全幅用
        self._imageWidth = 0
        self._imageHeight = 0
        self._gathering = False
        # main.py 通过 camera_bridge.frameProvider 注册到 QQmlApplicationEngine
        self.frameProvider = CameraFrameProvider()

    # ── 状态属性 ──────────────────────────────────────────
    def _getConnected(self) -> bool:
        return self._device is not None

    cameraConnected = Property(bool, _getConnected, notify=cameraConnectedChanged)

    def _getFrameIndex(self) -> int:
        return self._frameIndex

    # QML Image source 使用 image://camera/original?t=frameIndex；每次自增
    # 都触发 QML 重新向 provider 取图，实现无 ImageProvider 额外 API 的推帧
    frameIndex = Property(int, _getFrameIndex, notify=frameIndexChanged)

    def _getImageWidth(self) -> int:
        return self._imageWidth

    imageWidth = Property(int, _getImageWidth, notify=imageWidthChanged)

    def _getImageHeight(self) -> int:
        return self._imageHeight

    imageHeight = Property(int, _getImageHeight, notify=imageHeightChanged)

    def _getGathering(self) -> bool:
        return self._gathering

    gathering = Property(bool, _getGathering, notify=gatheringChanged)

    # ── 搜索 ──────────────────────────────────────────────
    @Slot()
    def search(self):
        """枚举设备（同步执行，结果经 camerasFound 信号返回）。"""
        try:
            self._manager.init_lib()
            devices = self._manager.get_devices_info()
            print(f"[CameraBridge] 搜索到 {len(devices)} 台相机: {devices}")
            self.camerasFound.emit(devices)
        except Exception as e:
            print(f"[CameraBridge] 搜索异常: {e}")
            self.cameraError.emit(f"搜索相机失败: {e}")

    # ── 连接 / 断开 ───────────────────────────────────────
    @Slot(str)
    def connectCamera(self, sn: str):
        """按序列号打开相机。"""
        if self._device is not None:
            self.cameraError.emit("相机已连接，请先断开")
            return
        try:
            device = CameraDevice(sn)
            if device.cam is None:
                self.cameraError.emit(f"打开相机失败（SN: {sn}）")
                return
            self._device = device
            self._device.image_captured.connect(self._onFrame)
            self._deviceInfo = {"model": device.cam_name, "sn": sn}
            self._refreshGeometry()
            print(f"[CameraBridge] 相机已打开: {sn}")
            self.cameraConnectedChanged.emit()
            self.cameraOpened.emit()
        except Exception as e:
            print(f"[CameraBridge] 连接异常: {e}")
            self.cameraError.emit(f"打开相机失败: {e}")

    @Slot()
    def disconnectCamera(self):
        if self._device is None:
            return
        device = self._device
        self._device = None
        self._deviceInfo = {}
        self._gathering = False
        self._gatheringChangedEmit()
        try:
            device.cam_close()
        except Exception as e:
            print(f"[CameraBridge] 关闭异常: {e}")
        # 断开后清空最近帧，避免下个采集会话短暂显示旧图
        self._frameCache = None
        self._lastDisplayAt = 0.0
        self.frameProvider.clear()
        self._frameIndex = 0
        self.frameIndexChanged.emit()
        self._setGeometry(0, 0)
        self.cameraConnectedChanged.emit()
        self.cameraClosed.emit()
        print("[CameraBridge] 相机已断开")

    # ── 特征读写（同步，QML 直接取返回值）──────────────────
    @Slot(str, result=float)
    def getFeature(self, name: str) -> float:
        """读特征值（数值型：int/float/enum 统一返回 float；读失败返回 -1）。"""
        if self._device is None:
            return -1.0
        try:
            v = self._device.get_remote_feature(name, _feature_type(name))
            return float(v) if v is not None else -1.0
        except Exception as e:
            print(f"[CameraBridge] getFeature {name} 异常: {e}")
            return -1.0

    @Slot(str, result=str)
    def getFeatureString(self, name: str) -> str:
        """读字符串特征（如序列号）；读失败返回空串。"""
        if self._device is None:
            return ""
        try:
            v = self._device.get_remote_feature(name, "string")
            return v if isinstance(v, str) else str(v or "")
        except Exception as e:
            print(f"[CameraBridge] getFeatureString {name} 异常: {e}")
            return ""

    @Slot(str, float, result=bool)
    def setFeature(self, name: str, value: float) -> bool:
        """写数值型特征，返回是否写入成功。bool 底层要求 "true"/"false"。"""
        if self._device is None:
            return False
        try:
            t = _feature_type(name)
            ok = False
            if t == "bool":
                ok = self._device.set_remote_feature(
                    name, t, "true" if float(value) > 0.5 else "false")
            elif t == "float":
                ok = self._device.set_remote_feature(name, t, float(value))
            elif t == "int":
                ok = self._device.set_remote_feature(name, t, int(value))
            else:  # enum
                ok = self._device.set_remote_feature(name, t, int(value))
            # CameraPage 修改分辨率/ROI 后，DetectPage 的图像窗口比例要跟随
            if ok and name in ("GX_INT_WIDTH", "GX_INT_HEIGHT", "GX_INT_OFFSET_X", "GX_INT_OFFSET_Y"):
                self._refreshGeometry()
            return bool(ok)
        except Exception as e:
            print(f"[CameraBridge] setFeature {name}={value} 异常: {e}")
            return False

    @Slot(str, str)
    def setFeatureString(self, name: str, value: str):
        """写字符串特征。"""
        if self._device is None:
            return
        try:
            self._device.set_remote_feature(name, "string", value)
        except Exception as e:
            print(f"[CameraBridge] setFeatureString {name} 异常: {e}")

    # ── ROI（归一化坐标 → 大恒像素参数）─────────────────────
    def _writeGeometry(self, x: int, y: int, w: int, h: int, label: str) -> bool:
        """写 OFFSET/WIDTH/HEIGHT，失败时尝试恢复旧值。"""
        prev_x = self._featureInt("GX_INT_OFFSET_X")
        prev_y = self._featureInt("GX_INT_OFFSET_Y")
        prev_w = self._featureInt("GX_INT_WIDTH")
        prev_h = self._featureInt("GX_INT_HEIGHT")

        # 大恒相机约束：offset + 当前宽 <= 传感器最大宽。若相机当前是全幅
        # （或新尺寸更小），先写 OFFSET 会因 offset+当前宽 > 最大宽而
        # INVALID_ACCESS（ROI 带非零 offset 时实测 680+2448>2448 被拒）。
        # 故按是否「缩小」自适应写序：
        #   - 缩小/不变（新 W/H <= 当前）：先 WIDTH/HEIGHT 再 OFFSET
        #     （缩小后 offset+新宽 <= 最大宽）
        #   - 放大（恢复全幅）：先 OFFSET 再 WIDTH/HEIGHT（先把 offset 归位）
        shrink = (w <= prev_w) and (h <= prev_h)
        if shrink:
            ok = self.setFeature("GX_INT_WIDTH", w)
            ok = self.setFeature("GX_INT_HEIGHT", h) and ok
            ok = self.setFeature("GX_INT_OFFSET_X", x) and ok
            ok = self.setFeature("GX_INT_OFFSET_Y", y) and ok
        else:
            ok = self.setFeature("GX_INT_OFFSET_X", x)
            ok = self.setFeature("GX_INT_OFFSET_Y", y) and ok
            ok = self.setFeature("GX_INT_WIDTH", w) and ok
            ok = self.setFeature("GX_INT_HEIGHT", h) and ok
        if ok:
            self._refreshGeometry()
            actual_w = self._featureInt("GX_INT_WIDTH")
            actual_h = self._featureInt("GX_INT_HEIGHT")
            print(f"[CameraBridge] {label}: 写入 x={x}, y={y}, w={w}, h={h}; "
                  f"读回 {actual_w}×{actual_h}")
            if actual_w != w or actual_h != h:
                ok = False
        if not ok:
            # 尽量恢复旧几何，避免相机停留在半写入状态导致无法开流
            self.setFeature("GX_INT_OFFSET_X", prev_x)
            self.setFeature("GX_INT_OFFSET_Y", prev_y)
            self.setFeature("GX_INT_WIDTH", prev_w)
            self.setFeature("GX_INT_HEIGHT", prev_h)
            self._refreshGeometry()
            self.cameraError.emit(f"{label}失败，已恢复原几何参数")
        return ok

    def _waitGatherStopped(self, timeout: float = 0.8) -> bool:
        """等待相机 ACQUISITION_STATUS 变为停止。特性不可读时视作已停止。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                status = self._device.gather_status()
            except Exception:
                return True
            if status is None or not status:
                return True
            time.sleep(0.05)
        return False

    @Slot(float, float, float, float)
    def applyRoi(self, nx: float, ny: float, nw: float, nh: float):
        """应用 ROI：归一化 rect（0~1，相对当前原图显示区）→ GX 参数。

        对齐规则照抄老项目 _apply_roi_to_camera：宽为 8 的倍数、高为 2 的
        倍数；写参数顺序先 OFFSET 再 WIDTH/HEIGHT（大恒越界校验顺序）。
        """
        if self._device is None:
            self.cameraError.emit("相机未连接")
            return
        max_w = self._featureInt("GX_INT_WIDTH_MAX")
        max_h = self._featureInt("GX_INT_HEIGHT_MAX")
        if max_w <= 0 or max_h <= 0:
            self.cameraError.emit("无法读取相机最大分辨率，ROI 不可用")
            return

        nx = min(1.0, max(0.0, float(nx)))
        ny = min(1.0, max(0.0, float(ny)))
        nw = min(1.0 - nx, max(0.0, float(nw)))
        nh = min(1.0 - ny, max(0.0, float(nh)))

        # 归一化坐标相对“当前显示画面”计算。若相机已处于某个 ROI，
        # 需要在当前 OFFSET 基础上换算成传感器绝对坐标；直接乘最大分辨率
        # 会在二次框选/已缩放画面上产生错误区域。
        cur_w = self._featureInt("GX_INT_WIDTH")
        cur_h = self._featureInt("GX_INT_HEIGHT")
        if cur_w <= 0 or cur_h <= 0:
            cur_w, cur_h = max_w, max_h
        base_x = self._featureInt("GX_INT_OFFSET_X")
        base_y = self._featureInt("GX_INT_OFFSET_Y")
        if base_x < 0:
            base_x = 0
        if base_y < 0:
            base_y = 0

        x = adjust_to_step(base_x + int(nx * cur_w), 8, 0, max_w)
        y = adjust_to_step(base_y + int(ny * cur_h), 2, 0, max_h)
        w = adjust_to_step(int(nw * cur_w), 8, 8, max_w - x)
        h = adjust_to_step(int(nh * cur_h), 2, 2, max_h - y)
        if x + w > max_w:
            w = adjust_to_step(max_w - x, 8, 8, max_w)
        if y + h > max_h:
            h = adjust_to_step(max_h - y, 2, 2, max_h)
        if w < 8 or h < 2:
            self.cameraError.emit(f"ROI 尺寸过小（{w}×{h}），请重新框选")
            return
        print(f"[CameraBridge] applyRoi 注入: in(n={nx:.3f},{ny:.3f},{nw:.3f},{nh:.3f}) "
              f"cur={cur_w}x{cur_h}+({base_x},{base_y}) -> pixel({x},{y},{w}x{h})")

        # 首次 ROI 前记录当前几何，恢复全幅时回到这个“设置值”，
        # 而不是永远回到传感器最大分辨率。
        if self._roiBaseline is None and cur_w > 0 and cur_h > 0:
            self._roiBaseline = {
                "x": base_x, "y": base_y, "w": cur_w, "h": cur_h,
            }
            print(f"[CameraBridge] 记录 ROI 前几何: {cur_w}×{cur_h} "
                  f"(offset {base_x},{base_y})，恢复按钮将回到该分辨率")

        # 大恒相机在 Continuous 采集中直接写 WIDTH/HEIGHT/OFFSET 可能不生效
        # （甚至 INVALID_ACCESS）。所以正在采集时先停流，写完 ROI 再自动重开。
        was_gathering = self._gathering
        if was_gathering:
            stop_ok = self.stopGather()
            if stop_ok and self._device is not None:
                stop_ok = self._waitGatherStopped()
            self._restartAfterGeometry = True
            if not stop_ok:
                self.cameraError.emit("停止采集失败，ROI 未应用")
                self._scheduleGatherRestart()
                return

        try:
            self._writeGeometry(x, y, w, h, "ROI 应用")
        finally:
            if was_gathering and self._device is not None:
                self._scheduleGatherRestart()

    @Slot(int, int, result=bool)
    def applyResolution(self, w: int, h: int) -> bool:
        """应用相机设置的分辨率（预设 2448×2048 全幅 / 1224×1024 半幅）。

        与老项目（pyqt5 的 RESOLUTION_MAP + GX_INT_BINNING_*）一致，分辨率
        切换走 **BINNING**：相邻 N×N 像素合成输出 1 像素，输出尺寸 = 传感器/N，
        但视野(FOV)保持全幅不变——画面只是变模糊（省带宽、推理更快）。

        不能写 OFFSET+WIDTH 窗口裁剪：那只会读出传感器中间一块，画面被放大
        （“分辨率一改屏幕就缩放”，两侧视野丢失）——老项目实测 binning 才是
        用户期望的“变糊但不缩放”。

        成功后记录为「设定分辨率」（含 binning 状态）：ROI 的"恢复全幅"
        回到它，而不是传感器最大分辨率（避免采集恢复后跳到 2448 推理变慢）。
        """
        if self._device is None:
            return False
        # sensor 尺寸与 binning 无关，binning 系数据此推算（预设 2448→1、1224→2）
        sensor_w = self._featureInt("GX_INT_SENSOR_WIDTH")
        sensor_h = self._featureInt("GX_INT_SENSOR_HEIGHT")
        if sensor_w <= 0:
            sensor_w = 2448
        if sensor_h <= 0:
            sensor_h = 2048
        w = max(8, min(int(w), sensor_w))
        h = max(2, min(int(h), sensor_h))
        bx = max(1, round(sensor_w / float(w)))
        by = max(1, round(sensor_h / float(h)))
        prev_bx = self.getFeature("GX_INT_BINNING_HORIZONTAL")
        prev_by = self.getFeature("GX_INT_BINNING_VERTICAL")

        was_gathering = self._gathering
        if was_gathering and self._device is not None:
            self.stopGather()
            self._waitGatherStopped()
            self._restartAfterGeometry = True
        out_w = out_h = 0
        try:
            ok = self.setFeature("GX_INT_BINNING_HORIZONTAL", bx)
            ok = self.setFeature("GX_INT_BINNING_VERTICAL", by) and ok
            if ok:
                # 设完 binning 后 WidthMax 动态更新（bin=2 → 1224），据此钳定输出
                bin_max_w = self._featureInt("GX_INT_WIDTH_MAX")
                bin_max_h = self._featureInt("GX_INT_HEIGHT_MAX")
                out_w = max(8, min(w, bin_max_w if bin_max_w > 0 else w))
                out_h = max(2, min(h, bin_max_h if bin_max_h > 0 else h))
                # offset 恒为 0（binning 覆盖全幅），WIDTH/HEIGHT 写到 binning 后最大
                ok = self._writeGeometry(0, 0, out_w, out_h, "分辨率应用")
        finally:
            if was_gathering and self._device is not None:
                self._scheduleGatherRestart()
        if ok:
            # 记录为设定分辨率：ROI 恢复全幅回到它
            self._roiBaseline = {"x": 0, "y": 0, "w": out_w, "h": out_h}
        else:
            # 尽量恢复原 binning，避免相机停在半写入状态
            if prev_bx > 0:
                self.setFeature("GX_INT_BINNING_HORIZONTAL", int(prev_bx))
            if prev_by > 0:
                self.setFeature("GX_INT_BINNING_VERTICAL", int(prev_by))
        return ok

    @Slot()
    def resetRoi(self):
        """ROI 恢复全幅（先清零 OFFSET，再写最大 WIDTH/HEIGHT）。"""
        if self._device is None:
            return
        max_w = self._featureInt("GX_INT_WIDTH_MAX")
        max_h = self._featureInt("GX_INT_HEIGHT_MAX")
        if max_w <= 0 or max_h <= 0:
            max_w = self._featureInt("GX_INT_SENSOR_WIDTH")
            max_h = self._featureInt("GX_INT_SENSOR_HEIGHT")
        if max_w <= 0 or max_h <= 0:
            self.cameraError.emit("无法读取传感器尺寸，ROI 重置失败")
            return
        max_w = adjust_to_step(max_w, 8, 8, max_w)
        max_h = adjust_to_step(max_h, 2, 2, max_h)
        # 优先恢复到“首次 ROI 前”的分辨率（用户在相机设置页选择的值）；
        # 没有历史记录时才回退到传感器最大分辨率。
        if self._roiBaseline is not None:
            b = self._roiBaseline
            target_x, target_y = int(b["x"]), int(b["y"])
            target_w, target_h = int(b["w"]), int(b["h"])
        else:
            target_x, target_y = 0, 0
            target_w, target_h = max_w, max_h

        was_gathering = self._gathering
        if was_gathering:
            stop_ok = self.stopGather()
            if stop_ok and self._device is not None:
                stop_ok = self._waitGatherStopped()
            self._restartAfterGeometry = True
            if not stop_ok:
                self.cameraError.emit("停止采集失败，未能恢复全幅")
                self._scheduleGatherRestart()
                return

        try:
            ok = self._writeGeometry(
                target_x, target_y, target_w, target_h, "ROI 恢复全幅")
            if ok:
                self._roiBaseline = None
        finally:
            if was_gathering and self._device is not None:
                self._scheduleGatherRestart()

    def _scheduleGatherRestart(self, attempt: int = 0):
        """ROI 写入后延迟重开采集。

        大恒相机刚执行 ACQUISITION_STOP 后立刻 START 可能返回失败，
        所以延迟 200ms，失败则再重试两次。
        """
        if not self._restartAfterGeometry or self._device is None:
            return
        if self._gathering:
            self._restartAfterGeometry = False
            return
        ok = self.startGather()
        if ok:
            self._restartAfterGeometry = False
        elif attempt < 2:
            QTimer.singleShot(
                400, lambda: self._scheduleGatherRestart(attempt + 1))
        else:
            self._restartAfterGeometry = False
            self.cameraError.emit("ROI 写入后自动重启采集失败，请点击开始采集重试")

    # ── 采集（原语，由 main.py 的 AppBridge 采集仲裁方驱动）──────
    @Slot(result=bool)
    def startGather(self) -> bool:
        """开始连续采集。成功返回 True；相机未连接/采集启动失败返回 False。

        失败自愈：大恒 U3VTL 在分辨率/ROI 变更（尤其负载从半幅恢复全幅）后，
        可能因流缓冲未刷新报 -1010 "TL Error: Unable to start acquisition"。
        首次失败时自动「重注册采集回调」重建流缓冲并重试一次（板端已验证可行）。
        """
        if self._device is None:
            print("[CameraBridge] startGather 失败: 相机未连接")
            return False
        if self._gathering:
            self._restartAfterGeometry = False
            return True
        try:
            ok = self._device.gather_start()
            if not ok:
                # ── 自愈：重注册回调重建流缓冲后重试一次 ──
                print("[CameraBridge] 采集启动失败，重注册采集回调重建流缓冲后重试...")
                dev = self._device
                try:
                    gx_unregister_capture_callback(dev.cam)
                    time.sleep(0.2)
                    gx_register_capture_callback(dev.cam, dev.callback)
                    time.sleep(0.3)
                except Exception as e:
                    print(f"[CameraBridge] 重注册回调异常: {e}")
                ok = self._device.gather_start()
                if not ok:
                    self.cameraError.emit(
                        "相机采集启动失败（传输层拒绝，已自动重试1次；"
                        "若持续出现请查看日志中的 ACQUISITION_START 错误码）")
                    return False
                print("[CameraBridge] 重注册后采集启动成功")
            self._gathering = True
            self._restartAfterGeometry = False
            self.gatheringChanged.emit()
            print("[CameraBridge] 采集已启动")
            return True
        except Exception as e:
            print(f"[CameraBridge] startGather 异常: {e}")
            self.cameraError.emit(f"启动采集失败: {e}")
            return False

    @Slot(result=bool)
    def stopGather(self) -> bool:
        self._restartAfterGeometry = False  # 用户/仲裁方显式停止，取消自动重启
        if self._device is None or not self._gathering:
            return True
        try:
            ok = self._device.gather_stop()
            if self._gathering:
                self._gathering = False
                self.gatheringChanged.emit()
            # 停止后清掉最后一帧：重启采集时先显示“等待图像”，
            # 不会把上一个会话的旧画面误认为新会话画面。
            self._frameCache = None
            self._lastDisplayAt = 0.0
            self.frameProvider.clear_frame("original")
            if self._frameIndex != 0:
                self._frameIndex = 0
                self.frameIndexChanged.emit()
            print("[CameraBridge] 采集已停止")
            return ok
        except Exception as e:
            print(f"[CameraBridge] stopGather 异常: {e}")
            return False

    # ── 内部：帧回调 → QImage / provider / numpy ──────────────
    def _onFrame(self, numpy_img: np.ndarray):
        self._frameCache = numpy_img
        now = time.monotonic()

        # 原始帧始终发给实时推理管线；QML 原图显示按 30fps 节流。
        # 高帧率相机（MER2 可达 79fps）逐帧刷新 QML 会产生大量
        # 2448×2048 QImage 拷贝，拖慢 UI，且人眼无法分辨超过 30fps。
        if self._lastDisplayAt and now - self._lastDisplayAt < 1.0 / 30.0:
            self.rawFrameReady.emit(numpy_img)
            return
        self._lastDisplayAt = now

        h, w, _ = numpy_img.shape
        qimg = QImage(numpy_img.data, w, h, w * 3, QImage.Format_RGB888).copy()
        # 先写入 provider 再发 index 信号：QML 收到通知来取图时一定是最新帧
        self.frameProvider.set_frame("original", qimg)
        self._frameIndex += 1
        self.frameIndexChanged.emit()
        self.frameReady.emit(qimg)
        self.rawFrameReady.emit(numpy_img)

    # ── 内部辅助 ──────────────────────────────────────────
    def _featureInt(self, name: str) -> int:
        v = self.getFeature(name)
        return int(v) if v is not None and v >= 0 else -1

    def _refreshGeometry(self):
        if self._device is None:
            self._setGeometry(0, 0)
            return
        w = self._featureInt("GX_INT_WIDTH")
        h = self._featureInt("GX_INT_HEIGHT")
        self._setGeometry(w if w > 0 else 0, h if h > 0 else 0)

    def _setGeometry(self, w: int, h: int):
        if self._imageWidth != w:
            self._imageWidth = w
            self.imageWidthChanged.emit()
        if self._imageHeight != h:
            self._imageHeight = h
            self.imageHeightChanged.emit()

    def _gatheringChangedEmit(self):
        self.gatheringChanged.emit()
