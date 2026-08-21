import os
import sys
import sysconfig
from pathlib import Path

# ── PyInstaller 打包（windowed 无控制台）时 stdout/stderr 为 None，
# 任何 print 都会抛 AttributeError。frozen 下重定向到日志文件方便排查。
if getattr(sys, "frozen", False) and (sys.stdout is None or sys.stderr is None):
    try:
        _log = open(Path.home() / "DuAD_app.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = _log
    except Exception:
        sys.stdout = sys.stderr = open(os.devnull, "w")

# ── venv 解释器自检（跨平台）────────────────────────────────
# 系统 python 也装有 PySide6 + onnxruntime（CPU 版），直接
# 跑通但推理走 CPU（无 CUDA、无 nvidia 库）。
# 全部依赖（onnxruntime-gpu + CUDA 库）装在项目 venv 里：
#   Windows → pyqml_win/Scripts/python.exe
#   Linux   → pyqml/bin/python
# 检测到非 venv 解释器时自动切换到 venv python 重启。
# 必须放在任何 onnxruntime/PySide6 import 之前。
_IS_WIN = os.name == "nt"
_VENV_DIR = Path(__file__).resolve().parent / ("pyqml_win" if _IS_WIN else "pyqml")
if sys.prefix != str(_VENV_DIR):
    _venv_python = _VENV_DIR / (Path("Scripts", "python.exe") if _IS_WIN else Path("bin", "python"))
    if _venv_python.exists():
        print(f"[INFO] 当前解释器非项目 venv（{sys.executable}，"
              f"切换到 {_venv_python}（推理需 GPU 库）")
        os.execv(str(_venv_python), [str(_venv_python), "-u"] + sys.argv)


def _is_frozen() -> bool:
    """是否 PyInstaller 打包运行（frozen）。"""
    return getattr(sys, "frozen", False)


def _content_dir() -> Path:
    """QML 资源目录：打包后 = sys._MEIPASS（资源都打进 _internal/）。"""
    if _is_frozen():
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def _backend_root() -> Path:
    if _is_frozen():
        return Path(sys._MEIPASS) / "backend"
    return _content_dir().parent / "backend"


def _translations_root() -> Path:
    if _is_frozen():
        return Path(sys._MEIPASS) / "translations"
    return Path(__file__).resolve().parent.parent / "translations"

# ── 相机 SDK 本地库注入（仅 Linux）──────────────────────
# glibc 的 dlopen 依赖解析只认进程启动时的 ld 搜索路径，运行时设置
# LD_LIBRARY_PATH 无效（activate 脚本的注入只在 source 时生效，直接
# 用 venv python 运行会漏）。检测缺失时自动重启进程完成注入。
# onnxruntime-gpu 的 CUDA 运行时库（nvidia-*-cu13 pip 包，site-packages/
# nvidia/*/lib）同理注入；未安装（无 GPU 机器）时自动跳过。
# Windows 下 DLL 依赖由 onnxruntime/PySide6 通过加载路径自行处理，
# 无需（也不能）用 LD_LIBRARY_PATH，故整段仅在 Linux 执行。
if not _IS_WIN:
    _LIBS_DIR = Path(__file__).resolve().parent.parent / "backend" / "libs"
    _SITE_PACKAGES = Path(sysconfig.get_paths()["purelib"])
    _NVIDIA_LIBS = sorted((_SITE_PACKAGES / "nvidia").glob("*/lib"))
    _extra_ld = [_LIBS_DIR] if _LIBS_DIR.is_dir() else []
    _extra_ld += [d for d in _NVIDIA_LIBS if d.is_dir()]
    # TensorRT 库（tensorrt_cu13_libs 包，TensorrtExecutionProvider 依赖）
    _trt_libs = _SITE_PACKAGES / "tensorrt_libs"
    if _trt_libs.is_dir():
        _extra_ld.append(_trt_libs)
    if _extra_ld:
        _ld_path = os.environ.get("LD_LIBRARY_PATH", "")
        _missing = [str(p) for p in _extra_ld if str(p) not in _ld_path.split(":")]
        if _missing:
            os.environ["LD_LIBRARY_PATH"] = ":".join(_missing) + \
                ((":" + _ld_path) if _ld_path else "")
            print(f"[INFO] 注入 LD_LIBRARY_PATH（{_missing}）并重启进程")
            # -u 保证重启进程 stdout 无缓冲（否则管道下日志丢失）
            os.execv(sys.executable, [sys.executable, "-u"] + sys.argv)
else:
    # ── Windows：onnxruntime-gpu 的 CUDA 运行库（nvidia-cublas-cu13 /
    # nvidia-cudnn-cu13 pip 包）DLL 不在 onnxruntime 包内，provider
    # （onnxruntime_providers_cuda.dll）加载时按 PATH 解析 cublas64_13.dll /
    # cudnn64_9.dll 等，缺失则静默回退 CPU。把 CUDA/TensorRT DLL 目录注入
    # PATH（onnx_infer.py 里有同样的兜底，双保险）：
    #   CUDA 运行库：开发 = site-packages/nvidia；打包(frozen) = exe 同目录 nvidia/
    #   TensorRT   ：环境变量 TENSORRT_LIB_DIR；开发 = backend/libs_win_tensorrt/bin；
    #                打包(frozen) = exe 同目录 tensorrt/（GPU 库可选项，不随 exe 打包）
    _nvidia_dll_dirs = []

    def _add_dll_dir(_d):
        if _d and os.path.isdir(_d) and str(_d) not in _nvidia_dll_dirs:
            _nvidia_dll_dirs.append(str(_d))

    if _is_frozen():
        _nv_roots = [Path(sys.executable).resolve().parent / "nvidia"]
    else:
        _nv_roots = [Path(sysconfig.get_paths()["purelib"]) / "nvidia"]
    for _root in _nv_roots:
        if not _root.is_dir():
            continue
        for _sub in sorted(_root.glob("*")):
            for _rel in ("bin", "bin/x86_64"):
                _add_dll_dir(_sub / _rel)
    _add_dll_dir(os.environ.get("TENSORRT_LIB_DIR", ""))
    if _is_frozen():
        for _trt_root in (Path(sys.executable).resolve().parent / "tensorrt",
                          Path(sys.executable).resolve().parent / "trt"):
            if _trt_root.is_dir():
                _add_dll_dir(_trt_root / "bin")
                _add_dll_dir(_trt_root)
    else:
        _add_dll_dir(_backend_root() / "libs_win_tensorrt" / "bin")
    if _nvidia_dll_dirs:
        _path = os.environ.get("PATH", "")
        _missing = [d for d in _nvidia_dll_dirs if d not in _path.split(";")]
        if _missing:
            os.environ["PATH"] = ";".join(_missing) + \
                ((";" + _path) if _path else "")
            print(f"[INFO] 注入 CUDA/TensorRT DLL 目录到 PATH（{_missing}）")

from PySide6.QtGui import QGuiApplication, QFontDatabase, QFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtCore import Qt, QUrl, QObject, Slot, Property, Signal, QTranslator, QSettings, QTimer
# 语言代码 → .qm 文件名（"zh_CN" 用源文本，不加载翻译）
LANG_FILES = {
    0: "app_en",       # English
    1: None,           # 简体中文（源文本）
    2: "app_zh_TW",    # 繁体中文
}


class AppBridge(QObject):
    """QML 可调用的 Python 桥接层 — 语言切换等跨层操作。"""

    def getLanguageIndex(self) -> int:
        """当前语言索引，QML 读取以同步下拉框显示。"""
        return self._languageIndex

    languageIndex = Property(int, getLanguageIndex)

    @Property(str, constant=True)
    def homeDir(self) -> str:
        """用户主目录 — QML 中展开路径里的 ~（QUrl 不会展开）。"""
        return str(Path.home())

    @Slot(str, result=bool)
    def isDir(self, path: str) -> bool:
        """目录是否存在 — QML 无文件系统 API，目录选择对话框校验初始目录用。"""
        return os.path.isdir(path)

    # ── 使用说明页 ──────────────────────────────────────
    # QML 信号总线：设置页"使用说明"按钮 emit 此信号 → App.qml 打开说明对话框
    # （绕开 .ui.qml 不能加逻辑、信号链路过长的约束）
    helpRequested = Signal()

    @Slot(result=bool)
    def shouldShowHelp(self) -> bool:
        """是否首次启动（未看过说明页）。"""
        return not bool(self._settings.value("helpShown", False))

    @Slot()
    def markHelpShown(self):
        """标记说明页已展示，下次启动不再弹出。"""
        self._settings.setValue("helpShown", True)

    # ── 跨页状态中枢（前后端联调契约）──────────────────────
    # 页面通过 AppBridge 共享相机/采集/算法状态，替代各页独立模拟：
    #   CameraPage  连接成功 → setCameraConnected(True)，断开 → False
    #   CollectPage 采集切换 → setCollecting(True/False)
    #   DetectPage  算法开关 → setAlgorithmEnabled(True/False)（防卡死闸门，
    #               后端推理线程轮询该标志，关闭时不推理只直通显示）
    # 帧数据流契约（已联调）：
    #   原图   CameraBridge.frameIndex → image://camera/original?t=<index>
    #   热力图 DetectBridge.resultCounter → image://camera/heatmap?t=<counter>
    #   定位图 DetectBridge.maskCounter → image://camera/mask?t=<counter>
    cameraConnectedChanged = Signal()
    collectingChanged = Signal()
    collectingOwnerChanged = Signal()
    algorithmEnabledChanged = Signal()

    def __init__(self, app: QGuiApplication, engine: QQmlApplicationEngine):
        super().__init__()
        self._app = app
        self._engine = engine
        self._translator = QTranslator()
        self._settings = QSettings("DuAD", "DuADSoftware")
        self._languageIndex = int(self._settings.value("language", 1))
        self._cameraConnected = False
        self._collecting = False
        self._collectingOwner = ""
        self._algorithmEnabled = False

    def _getCameraConnected(self) -> bool:
        return self._cameraConnected

    def _setCameraConnected(self, value: bool):
        if self._cameraConnected != bool(value):
            self._cameraConnected = bool(value)
            self.cameraConnectedChanged.emit()

    cameraConnected = Property(bool, _getCameraConnected, _setCameraConnected,
                               notify=cameraConnectedChanged)

    def _getCollecting(self) -> bool:
        return self._collecting

    def _setCollecting(self, value: bool):
        if self._collecting != bool(value):
            self._collecting = bool(value)
            self.collectingChanged.emit()

    collecting = Property(bool, _getCollecting, _setCollecting, notify=collectingChanged)

    def _getCollectingOwner(self) -> str:
        return self._collectingOwner

    def _setCollectingOwner(self, value: str):
        """采集会话互斥仲裁：相机只有一台，采集入口（数据采集/实时检测）同时
        只能有一个持有者（"" / "collect" / "detect"）。后按者抢占，先按者自动停止。
        collecting 作为只读派生状态跟随 owner。"""
        v = str(value) if value else ""
        if v not in ("", "collect", "detect"):
            return
        if self._collectingOwner != v:
            self._collectingOwner = v
            self.collectingOwnerChanged.emit()
            self._setCollecting(bool(v))

    collectingOwner = Property(str, _getCollectingOwner, _setCollectingOwner,
                               notify=collectingOwnerChanged)

    def _getAlgorithmEnabled(self) -> bool:
        return self._algorithmEnabled

    def _setAlgorithmEnabled(self, value: bool):
        if self._algorithmEnabled != bool(value):
            self._algorithmEnabled = bool(value)
            self.algorithmEnabledChanged.emit()

    algorithmEnabled = Property(bool, _getAlgorithmEnabled, _setAlgorithmEnabled,
                                notify=algorithmEnabledChanged)

    @Slot(int)
    def setLanguage(self, lang_index: int):
        """切换语言：加载对应 .qm 并刷新 QML 文本。"""
        self._languageIndex = int(lang_index)
        self._app.removeTranslator(self._translator)

        filename = LANG_FILES.get(self._languageIndex)
        if filename:
            qm_path = self._translations_dir() / f"{filename}.qm"
            if self._translator.load(str(qm_path)):
                self._app.installTranslator(self._translator)
                print(f"[INFO] 语言已切换: {filename}")

        # Qt 6.2+: 通知 QML 所有 qsTr 重新求值
        if hasattr(self._engine, "retranslate"):
            self._engine.retranslate()

        self._settings.setValue("language", self._languageIndex)

    def _translations_dir(self) -> Path:
        return _translations_root()


if __name__ == "__main__":
    # 强制 Qt Quick Controls 使用 Fusion 样式：原生样式（Windows 等）不支持
    # 自定义 background/contentItem（报 "current style does not support
    # customization"），所有自绘控件会渲染空白。必须设默认环境变量（KDE
    # 注入的 Breeze 样式与 Qt 6.11 QML 化控件不兼容，同此处理）。
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Fusion")
    # Qt 5 兼容模式：URL 在赋值时（而非消费时）解析，确保相对路径
    # 相对于赋值所在的 QML 文件解析，而非组件定义文件
    # 这对跨目录引用图标至关重要（如 MainWindow.ui.qml → components/NavButton.qml）
    os.environ["QML_COMPAT_RESOLVE_URLS_ON_ASSIGNMENT"] = "1"

    # 高 DPI 缩放 — 必须作为静态方法在 QGuiApplication 创建之前调用
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QGuiApplication(sys.argv)

    # ── Portal 集成（系统原生文件/目录对话框）────────────────────
    # Qt 6 的 FileDialog（打开图片/选模型/保存路径）走 XDG Desktop Portal，
    # 前提是应用在 portal registry 注册成功：需 setDesktopFileName + 系统里有
    # 对应的 .desktop 文件（App info）。否则 portal 不可用，FileDialog 回退
    # Qt 自绘（"银白、大字体、每行带 size"的老式表格对话框）。
    # 这里自动把 desktop 文件安装到 ~/.local/share/applications/（若缺失），
    # Exec 用当前解释器 + main.py 绝对路径，项目搬移不失效。
    _APP_ID = "duad-software"
    app.setDesktopFileName(_APP_ID)
    try:
        _applications_dir = Path.home() / ".local" / "share" / "applications"
        _desktop_file = _applications_dir / f"{_APP_ID}.desktop"
        if not _desktop_file.exists():
            _applications_dir.mkdir(parents=True, exist_ok=True)
            _desktop_file.write_text(
                f"[Desktop Entry]\n"
                f"Type=Application\n"
                f"Name=DuAD 异常检测上位机\n"
                f"Comment=融合Dinov2与双分支训练架构的工业异常检测\n"
                f"Exec={sys.executable} {Path(__file__).resolve()}\n"
                f"Terminal=false\n"
                f"Categories=Utility;\n",
                encoding="utf-8")
            print(f"[INFO] 已安装 portal 桌面条目: {_desktop_file}")
    except Exception as e:
        print(f"[WARN] 安装 portal 桌面条目失败（原生对话框将回退自绘）: {e}")

    # 全局注册自定义字体（文泉驿微米黑），QML 中可直接 font.family 引用
    content_dir = _content_dir()
    font_file = content_dir / "fonts" / "wqy-microhei.ttc"
    font_id = QFontDatabase.addApplicationFont(str(font_file))
    if font_id >= 0:
        families = QFontDatabase.applicationFontFamilies(font_id)
        print(f"[INFO] 字体已注册: {families}")
        # 设为应用默认字体 — 所有未显式指定 font.family 的控件继承
        app.setFont(QFont("WenQuanYi Micro Hei"))
    else:
        print(f"[WARN] 字体加载失败: {font_file}")

    # 设置 QML 导入路径：本目录（DuAD_SoftwareContent/），
    # 引擎在此查找模块目录 DuAD_Software/（qmldir 声明 module DuAD_Software）
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(content_dir))

    # 桥接层 — QML 中可直接调用 AppBridge.setLanguage()
    bridge = AppBridge(app, engine)
    engine.rootContext().setContextProperty("AppBridge", bridge)

    # 桥对象 keepalive：PySide6 的 context property 不增加 Python 引用计数，
    # 若 Python 侧引用丢失会被 GC → QML 侧 AppBridge 变 null（"点了没反应"）。
    # 挂在模块级列表最保险（execv 重启进程同样适用）。
    _BRIDGES = [bridge]

    # 相机桥 — 后端相机驱动（backend/Src/camera_bridge.py）。
    # 注意：必须保持 Python 侧引用（变量 camera_bridge 存着），
    # 否则被 GC 后 QML 侧 CameraBridge 变 null、点击回调静默失败。
    backend_root = _backend_root()
    detect_bridge = None
    collect_bridge = None
    light_bridge = None
    mqtt_bridge = None
    camera_bridge = None
    algorithm_bridge = None
    if backend_root.exists():
        sys.path.insert(0, str(backend_root))
        from Src.camera_bridge import CameraBridge

        camera_bridge = CameraBridge()
        engine.rootContext().setContextProperty("CameraBridge", camera_bridge)
        # 相机帧 provider：QML 经 image://camera/<kind> 取最新帧
        engine.addImageProvider("camera", camera_bridge.frameProvider)
        _BRIDGES.append(camera_bridge)

        # 算法桥 — ONNX 测试推理（后台线程执行，不阻塞 UI）。
        # 模型默认 null（不预置、不自动查找）：由用户在 DetectPage"选择模型"
        # 自行指定 .onnx 文件；loadModel 后自动后台预热 + 从 metadata 加载阈值。
        from Src.algorithm_bridge import AlgorithmBridge

        algorithm_bridge = AlgorithmBridge(model_path="")
        engine.rootContext().setContextProperty("AlgorithmBridge", algorithm_bridge)
        _BRIDGES.append(algorithm_bridge)
        print("[INFO] 算法模型: 未选择（DetectPage → 选择模型 指定 .onnx 文件）")

        # 实时检测管线 — 相机帧 → ONNX 推理 → 热力图 provider → QML。
        # 由 AppBridge.collectingOwner 仲裁采集会话：owner == "detect" 才启动
        # 相机采集 + 后台推理；切到其他页/停止采集时自动停止。
        from Src.realtime_detect_bridge import RealtimeDetectBridge

        detect_bridge = RealtimeDetectBridge(
            camera_bridge, algorithm_bridge, camera_bridge.frameProvider
        )
        engine.rootContext().setContextProperty("DetectBridge", detect_bridge)
        _BRIDGES.append(detect_bridge)
        detect_bridge.set_algorithm_enabled(bridge.algorithmEnabled)
        bridge.algorithmEnabledChanged.connect(
            lambda: detect_bridge.set_algorithm_enabled(bridge.algorithmEnabled)
        )
        algorithm_bridge.modelPathChanged.connect(detect_bridge.on_model_changed)

        # 图像采集保存管线 — 消费同一路 rawFrameReady，按间隔后台写盘。
        from Src.collect_bridge import CollectBridge

        collect_bridge = CollectBridge(camera_bridge)
        engine.rootContext().setContextProperty("CollectBridge", collect_bridge)
        _BRIDGES.append(collect_bridge)

        # 光源控制器 — CH340 串口，协议 $L{通道}={值}#。
        from Src.light_bridge import LightBridge

        light_bridge = LightBridge()
        engine.rootContext().setContextProperty("LightBridge", light_bridge)
        _BRIDGES.append(light_bridge)

        # MQTT 云服务器通信（免费/付费 Broker，用户名密码 + 可选 TLS）。
        from Src.mqtt_bridge import MqttBridge

        mqtt_bridge = MqttBridge()
        engine.rootContext().setContextProperty("MqttBridge", mqtt_bridge)
        _BRIDGES.append(mqtt_bridge)

        # ── 采集会话仲裁（跨页互斥）────────────────────────
        # DetectPage 只负责写 AppBridge.collectingOwner；真正的
        # CameraBridge.startGather/stopGather 与实时推理启停在这里集中执行，
        # 避免页面切换/抢占时相机采集状态残留。
        def _start_gather_for(owner: str) -> bool:
            if not camera_bridge.cameraConnected:
                print(f"[INFO] 相机未连接，无法开始采集（owner={owner}）")
                return False
            # 切换采集持有者时先完全停一次，避免上一会话状态残留
            if camera_bridge.gathering:
                camera_bridge.stopGather()
            return camera_bridge.startGather()

        def _on_collecting_owner_changed():
            owner = bridge.collectingOwner
            if owner == "detect":
                collect_bridge.stop()
                if _start_gather_for("detect"):
                    detect_bridge.start()
                else:
                    detect_bridge.stop()
                    bridge.collectingOwner = ""
            elif owner == "collect":
                detect_bridge.stop()
                if not collect_bridge.configured:
                    print("[INFO] CollectPage 尚未配置保存目录，拒绝开始采集")
                    collect_bridge.saveError.emit("请先设置保存目录和文件前缀")
                    bridge.collectingOwner = ""
                    return
                if _start_gather_for("collect"):
                    collect_bridge.start()
                else:
                    collect_bridge.stop()
                    bridge.collectingOwner = ""
            else:
                detect_bridge.stop()
                collect_bridge.stop()
                if camera_bridge.cameraConnected:
                    camera_bridge.stopGather()

        bridge.collectingOwnerChanged.connect(_on_collecting_owner_changed)

        # 相机断开：同步 AppBridge + 停管线；若正在采集中则释放 owner。
        def _on_camera_connection_changed():
            connected = camera_bridge.cameraConnected
            bridge._setCameraConnected(connected)
            if not connected:
                detect_bridge.stop()
                collect_bridge.stop()
                if bridge.collectingOwner:
                    bridge.collectingOwner = ""

        camera_bridge.cameraConnectedChanged.connect(_on_camera_connection_changed)

        # ROI 应用会短暂 stopGather→写参数→自动 restart。若重启失败，
        # 不能让页面停在“正在采集但实际没流”的假活状态：1.5s 后补救
        # 重试一次 startGather，仍失败则释放 collectingOwner，让按钮可点。
        def _recover_detect_gathering():
            if (bridge.collectingOwner == "detect"
                    and camera_bridge.cameraConnected
                    and not camera_bridge.gathering):
                print("[INFO] 检测到 detect 会话未在采集，尝试恢复采集")
                if not camera_bridge.startGather():
                    bridge.collectingOwner = ""

        def _on_gathering_changed():
            if bridge.collectingOwner == "detect" and not camera_bridge.gathering:
                QTimer.singleShot(1500, _recover_detect_gathering)

        camera_bridge.gatheringChanged.connect(_on_gathering_changed)
    else:
        print("[WARN] backend/ 目录不存在，相机功能不可用")

    # 退出时停止采集与后台推理线程，避免 USB 相机仍处于采集状态
    def _shutdown():
        if detect_bridge is not None:
            detect_bridge.stop()
        if collect_bridge is not None:
            collect_bridge.stop()
        if light_bridge is not None:
            light_bridge.disconnectSerial()
        if mqtt_bridge is not None:
            mqtt_bridge.disconnectServer()
        if camera_bridge is not None:
            try:
                if camera_bridge.cameraConnected:
                    camera_bridge.stopGather()
                    camera_bridge.disconnectCamera()
            except Exception as e:
                print(f"[WARN] 退出清理相机失败: {e}")

    app.aboutToQuit.connect(_shutdown)

    # 启动时恢复上次语言
    saved_lang = bridge._settings.value("language", 1)
    bridge.setLanguage(int(saved_lang))

    # 捕获 QML 错误信息
    def on_warnings(warnings):
        for w in warnings:
            print(f"[QML ERROR] {w.toString()}")

    engine.warnings.connect(on_warnings)

    # 加载入口 QML 文件
    qml_file = Path(__file__).resolve().parent / "App.qml"
    print(f"[INFO] 内容目录: {content_dir}")
    print(f"[INFO] 加载: {qml_file}")
    engine.load(str(qml_file))

    if not engine.rootObjects():
        print("[ERROR] QML 加载失败，详见上方错误信息")
        sys.exit(-1)

    print("[INFO] 启动成功")
    sys.exit(app.exec())
