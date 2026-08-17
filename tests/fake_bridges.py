"""冒烟测试共用 Fake 桥（Algorithm/Detect）。

真实 AlgorithmBridge 会在测试环境尝试创建 onnxruntime session，代价高且
无模型可用；DetectPage 只在栈里实例化也会引用 AlgorithmBridge/DetectBridge，
因此这里提供接口等价的轻量 fake，只验证 QML 绑定与信号接线不炸。
"""
import math

from PySide6.QtCore import QObject, Property, Slot, Signal


class FakeAlgorithmBridge(QObject):
    inferenceReady = Signal(float, str)
    maskReady = Signal(str)
    inferenceError = Signal(str)
    modelReady = Signal(str)
    modelUnloaded = Signal()
    modelPathChanged = Signal()
    thresholdChanged = Signal()
    pixelThresholdChanged = Signal()
    lastInferenceMsChanged = Signal()

    def __init__(self):
        super().__init__()
        self._model_path = ""
        self._threshold = 1.7
        self._pixel_threshold = float("nan")
        self._last_inference_ms = 0.0

    def _getModelPath(self):
        return self._model_path

    modelPath = Property(str, _getModelPath, notify=modelPathChanged)

    def _getThreshold(self):
        return self._threshold

    threshold = Property(float, _getThreshold, notify=thresholdChanged)

    def _getPixelThreshold(self):
        return self._pixel_threshold

    pixelThreshold = Property(float, _getPixelThreshold, notify=pixelThresholdChanged)

    def _getLastInferenceMs(self):
        return self._last_inference_ms

    lastInferenceMs = Property(float, _getLastInferenceMs, notify=lastInferenceMsChanged)

    @Slot(str)
    def loadModel(self, path):
        self._model_path = path
        self.modelPathChanged.emit()
        self.modelReady.emit(path.split("/")[-1])

    @Slot()
    def unloadModel(self):
        self._model_path = ""
        self._threshold = 1.7
        self._pixel_threshold = float("nan")
        self.modelPathChanged.emit()
        self.thresholdChanged.emit()
        self.pixelThresholdChanged.emit()
        self.modelUnloaded.emit()

    @Slot(str)
    def inferImage(self, path):
        pass

    @Slot(float)
    def setPixelThreshold(self, value):
        if math.isnan(value):
            self._pixel_threshold = float("nan")
        else:
            self._pixel_threshold = float(value)
        self.pixelThresholdChanged.emit()

    @Slot(bool)
    def setRefineMask(self, enabled):
        pass


class FakeDetectBridge(QObject):
    scoreReady = Signal(float)
    inferenceFailed = Signal(str)
    resultUpdated = Signal()
    maskUpdated = Signal()
    runningChanged = Signal()
    realtimeFpsChanged = Signal()
    lastInferenceMsChanged = Signal()

    def __init__(self):
        super().__init__()
        self._running = False
        self._result_counter = 0
        self._mask_counter = 0
        self._has_result = False
        self._has_mask = False
        self._realtime_fps = 0.0
        self._last_inference_ms = 0.0

    def _getRunning(self):
        return self._running

    running = Property(bool, _getRunning, notify=runningChanged)

    def _getResultCounter(self):
        return self._result_counter

    resultCounter = Property(int, _getResultCounter, notify=resultUpdated)

    def _getMaskCounter(self):
        return self._mask_counter

    maskCounter = Property(int, _getMaskCounter, notify=maskUpdated)

    def _getHasResult(self):
        return self._has_result

    hasResult = Property(bool, _getHasResult, notify=resultUpdated)

    def _getHasMask(self):
        return self._has_mask

    hasMask = Property(bool, _getHasMask, notify=maskUpdated)

    def _getRealtimeFps(self):
        return self._realtime_fps

    realtimeFps = Property(float, _getRealtimeFps, notify=realtimeFpsChanged)

    def _getLastInferenceMs(self):
        return self._last_inference_ms

    lastInferenceMs = Property(float, _getLastInferenceMs, notify=lastInferenceMsChanged)

    def set_algorithm_enabled(self, enabled):
        pass

    def start(self):
        self._running = True
        self.runningChanged.emit()

    def stop(self):
        self._running = False
        self.runningChanged.emit()

    def clear_results(self):
        self._has_result = False
        self._has_mask = False
        self._result_counter += 1
        self._mask_counter += 1
        self.resultUpdated.emit()
        self.maskUpdated.emit()

    def on_model_changed(self):
        self.clear_results()


class FakeCameraBridge(QObject):
    """轻量相机桥 fake：满足 CameraPage/DetectPage 的 QML 属性引用即可。"""
    camerasFound = Signal(list)
    cameraOpened = Signal()
    cameraClosed = Signal()
    cameraError = Signal(str)
    cameraConnectedChanged = Signal()
    frameIndexChanged = Signal()
    imageWidthChanged = Signal()
    imageHeightChanged = Signal()

    def __init__(self):
        super().__init__()
        self._connected = False
        self._frame_index = 0
        self._features = {
            "GX_INT_WIDTH": 2448.0, "GX_INT_HEIGHT": 2048.0,
            "GX_FLOAT_CURRENT_ACQUISITION_FRAME_RATE": 0.0,
        }

    @Slot()
    def search(self):
        self.camerasFound.emit([])

    @Slot(str)
    def connectCamera(self, sn):
        pass

    @Slot()
    def disconnectCamera(self):
        pass

    @Slot(str, result=float)
    def getFeature(self, name):
        return self._features.get(name, -1.0)

    @Slot(str, float)
    def setFeature(self, name, value):
        self._features[name] = value

    @Slot(float, float, float, float)
    def applyRoi(self, nx, ny, nw, nh):
        pass

    @Slot()
    def resetRoi(self):
        pass

    def _getConnected(self):
        return self._connected

    cameraConnected = Property(bool, _getConnected, notify=cameraConnectedChanged)

    def _getFrameIndex(self):
        return self._frame_index

    frameIndex = Property(int, _getFrameIndex, notify=frameIndexChanged)

    def _getImageWidth(self):
        return int(self._features.get("GX_INT_WIDTH", 0))

    imageWidth = Property(int, _getImageWidth, notify=imageWidthChanged)

    def _getImageHeight(self):
        return int(self._features.get("GX_INT_HEIGHT", 0))

    imageHeight = Property(int, _getImageHeight, notify=imageHeightChanged)


class FakeCollectBridge(QObject):
    savingChanged = Signal()
    configChanged = Signal()
    savedCountChanged = Signal()
    lastSavedPathChanged = Signal()
    saveError = Signal(str)

    def __init__(self):
        super().__init__()
        self._saving = False
        self._saved_count = 0
        self._configured = False
        self._last_saved_path = ""

    def _getSaving(self):
        return self._saving

    saving = Property(bool, _getSaving, notify=savingChanged)

    def _getConfigured(self):
        return self._configured

    configured = Property(bool, _getConfigured, notify=configChanged)

    def _getSavedCount(self):
        return self._saved_count

    savedCount = Property(int, _getSavedCount, notify=savedCountChanged)

    def _getLastSavedPath(self):
        return self._last_saved_path

    lastSavedPath = Property(str, _getLastSavedPath, notify=lastSavedPathChanged)

    @Slot(str, str, str, float, result=bool)
    def configure(self, save_path, prefix, fmt, interval):
        if not save_path or not prefix:
            self.saveError.emit("请先设置保存目录和文件前缀")
            return False
        self._configured = True
        self.configChanged.emit()
        return True

    def start(self):
        self._saving = True
        self.savingChanged.emit()

    def stop(self):
        if self._saving:
            self._saving = False
            self.savingChanged.emit()


class FakeLightBridge(QObject):
    portsChanged = Signal(list)
    serialConnected = Signal()
    serialDisconnected = Signal()
    serialError = Signal(str)
    commandSent = Signal(str)
    responseReceived = Signal(str)
    lastCommandChanged = Signal()
    lastResponseChanged = Signal()
    connectedChanged = Signal()

    def __init__(self):
        super().__init__()
        self._connected = False
        self._last_command = ""
        self._last_response = ""

    def _getConnected(self):
        return self._connected

    connected = Property(bool, _getConnected, notify=connectedChanged)

    def _getLastCommand(self):
        return self._last_command

    lastCommand = Property(str, _getLastCommand, notify=lastCommandChanged)

    def _getLastResponse(self):
        return self._last_response

    lastResponse = Property(str, _getLastResponse, notify=lastResponseChanged)

    @Slot()
    def refreshPorts(self):
        self.portsChanged.emit(["/dev/ttyUSB0"])

    @Slot(result=list)
    def listPorts(self):
        return ["/dev/ttyUSB0"]

    @Slot(str, int, str, str, str, result=bool)
    def connectSerial(self, port, baud, data_bits, stop_bits, parity):
        if not port:
            self.serialError.emit("请选择有效的串口")
            return False
        self._connected = True
        self.connectedChanged.emit()
        self.serialConnected.emit()
        return True

    @Slot()
    def disconnectSerial(self):
        if self._connected:
            self._connected = False
            self.connectedChanged.emit()
            self.serialDisconnected.emit()

    @Slot(int, int, result=bool)
    def setLightValue(self, channel, value):
        self._last_command = "$L%d=%d#" % (channel, value)
        self.lastCommandChanged.emit()
        self.commandSent.emit(self._last_command)
        return True


class FakeMqttBridge(QObject):
    mqttConnected = Signal()
    mqttDisconnected = Signal()
    mqttError = Signal(str)
    messageReceived = Signal(str, str)
    publishFinished = Signal(int)
    logMessage = Signal(str)
    connectedChanged = Signal()

    def __init__(self):
        super().__init__()
        self._connected = False

    def _getConnected(self):
        return self._connected

    connected = Property(bool, _getConnected, notify=connectedChanged)

    @Slot(str, int, str, str, int, bool)
    def connectServer(self, address, port, username, password, keep_alive, use_tls):
        if not address:
            self.mqttError.emit("服务器地址不能为空")
            return
        self._connected = True
        self.connectedChanged.emit()
        self.mqttConnected.emit()

    @Slot()
    def disconnectServer(self):
        if self._connected:
            self._connected = False
            self.connectedChanged.emit()
            self.mqttDisconnected.emit()

    @Slot(str, str, int, result=bool)
    def publish(self, topic, payload, qos):
        self.logMessage.emit("发布 → [" + topic + "] " + payload)
        return True

    @Slot(str, int, result=bool)
    def subscribe(self, topic, qos):
        return True
