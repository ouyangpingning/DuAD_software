import os
import shutil
import sys
import sysconfig
import platform
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
    # Jetson（aarch64）用 arm64 版大恒 SDK（backend/libs_arm64），不存在时回退 libs
    if platform.machine() in ("aarch64", "arm64"):
        _LIBS_ARM = _LIBS_DIR.parent / "libs_arm64"
        if _LIBS_ARM.is_dir():
            _LIBS_DIR = _LIBS_ARM
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

from PySide6.QtGui import QGuiApplication, QFontDatabase, QFont, QIcon, QPalette
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtCore import (Qt, QUrl, QObject, Slot, Property, Signal, QTranslator, QSettings,
                            QTimer, QCoreApplication)

# ── D-Bus（仅 Linux 用来读 xdg-desktop-portal 的亮/暗偏好）──────────
# 极简环境可能没有 QtDBus（Windows 的 PySide6 也是有这个模块的），
# 缺失时降级为空值，后面的 QStyleHints/调色板兜底照常工作。
try:
    from PySide6.QtDBus import (QDBusConnection, QDBusInterface, QDBusMessage,
                                QDBusVariant)
except Exception:                                   # pragma: no cover
    QDBusConnection = QDBusInterface = QDBusMessage = QDBusVariant = None
# 语言代码 → .qm 文件名（"zh_CN" 用源文本，不加载翻译）
LANG_FILES = {
    0: "app_en",       # English
    1: None,           # 简体中文（源文本）
    2: "app_zh_TW",    # 繁体中文
}

# ── 相机采集会话的所有权取值（跨页互斥的**唯一事实源**）────────────────
# 相机只有一台，同一时刻只能有一个页面持有采集。页面写
# `AppBridge.collectingOwner = "xxx"`，这里校验合法性，
# 真正的 startGather/stopGather 由 `_on_collecting_owner_changed` 统一执行。
#
# ⚠ 新增一个 owner 要同时改**两处**：这个元组 + `_on_collecting_owner_changed`
#    的分支。踩过的坑：加平台页的实时预览时只加了仲裁分支，漏了这个元组，
#    于是 `collectingOwner = "stage"` 被**静默丢弃** —— 表现成"预览开关点了没反应"，
#    而且连一行日志都没有，只能靠读代码找。
#    现在未知 owner 会明确打印警告，不再静默；`tests/test_stage_page.py` 里
#    还有一条静态校验：页面里写到的每个 owner 都必须在这个元组里。
COLLECTING_OWNERS = ("collect", "detect", "stage")

# ── 主题偏好（与设置页下拉框的稳定 key 一致；中文只走 displayFunc）────────
# theme 是用户偏好，system 表示跟随操作系统亮/暗；preset 只负责强调色。
# 两者都存 QSettings（与语言一致），重启恢复。
THEME_MODES = ("light", "dark", "system")
THEME_PRESETS = ("default", "ocean", "forest", "sunset")

# ── 系统亮/暗偏好（xdg-desktop-portal）──────────────────────────────
# GNOME / KDE 都把「设置 → 外观」的亮暗偏好放在 org.freedesktop.appearance
# 的 color-scheme 里（0=无偏好，1=偏好暗色，2=偏好亮色）。这是跨桌面环境
# 的公共事实源，**必须优先于** QStyleHints.colorScheme()：
# 后者在没有成对平台主题插件时（本机实测 Qt 6.11 + Wayland + GNOME
# prefer-dark）启动瞬间返回 Light，只在系统**切换**时才纠正 —— 于是
# “开着软件时切主题能跟随，关着软件时切了再启动还是亮色”。
PORTAL_SERVICE = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
PORTAL_IFACE = "org.freedesktop.portal.Settings"
PORTAL_APPEARANCE = "org.freedesktop.appearance"


class AppBridge(QObject):
    """QML 可调用的 Python 桥接层 — 语言/主题切换等跨层操作。"""

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
    themeModeChanged = Signal()
    presetChanged = Signal()
    systemDarkChanged = Signal()

    def __init__(self, app: QGuiApplication, engine: QQmlApplicationEngine,
                 settings: QSettings = None):
        super().__init__()
        self._app = app
        self._engine = engine
        self._translator = QTranslator()
        # settings 可注入 —— 测试必须传文件隔离的 QSettings，不能写用户真实配置
        # （docs/19 §1；render_pages 也用它来固定渲染主题）。
        self._settings = settings if settings is not None else QSettings("DuAD", "DuADSoftware")
        self._languageIndex = int(self._settings.value("language", 1))
        self._cameraConnected = False
        self._collecting = False
        self._collectingOwner = ""
        self._algorithmEnabled = False

        # ── 主题偏好 + 系统亮/暗检测 ────────────────────────
        self._themeMode = self._pick_setting(
            self._settings.value("theme", "system"), THEME_MODES, "system")
        self._preset = self._pick_setting(
            self._settings.value("preset", "default"), THEME_PRESETS, "default")
        self._portal = None          # 缓存的 Settings 接口（D-Bus 代理）
        self._portal_tried = False
        self._systemDark = self._detect_system_dark()
        self._colors = None
        self._bind_retries = 0

        # 在 QML 加载前就把主题推给 Colors 单例：首帧就是正确颜色，
        # 不会出现"先白后暗"的闪烁。模块尚未注册时会在事件循环里重试一次。
        self._bind_colors()
        self._watch_system_theme()

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
        """采集会话互斥仲裁：相机只有一台，采集入口（数据采集/实时检测/平台预览）
        同时只能有一个持有者（见 COLLECTING_OWNERS）。后按者抢占，先按者自动停止。
        collecting 作为只读派生状态跟随 owner。"""
        v = str(value) if value else ""
        if v and v not in COLLECTING_OWNERS:
            # ⚠ 这里**不能静默 return**。之前漏了 "stage" 时就是静默丢弃，
            #   现象是"按钮点了没反应"且无任何日志 —— 最难查的一类。
            print(f"[AppBridge] 忽略未知的 collectingOwner={v!r}；"
                  f"合法值只有 {COLLECTING_OWNERS}。"
                  f"新增 owner 要同时改 COLLECTING_OWNERS 与 _on_collecting_owner_changed")
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

    # ── 主题偏好 / 配色 / 系统亮暗 ──────────────────────────
    # QML 设置页只写 themeMode / preset；持久化、系统检测、以及把结果推给
    # Colors 单例都在 Python 侧完成（Colors 仍是唯一的颜色来源）。
    @staticmethod
    def _pick_setting(value, choices, fallback):
        text = str(value) if value is not None else ""
        return text if text in choices else fallback

    def _portal_iface(self):
        """xDG-desktop-portal 的 Settings 接口；Linux 上不可用时返回 None。

        只在第一次调用时连会话总线 + 建代理（失败也记住，避免每次重试）。
        timeout 压到 800ms：服务没装/没启动时不能让启动画面卡住。
        """
        if self._portal is not None:
            return self._portal
        if self._portal_tried:
            return None
        self._portal_tried = True
        if _IS_WIN or sys.platform == "darwin" or QDBusConnection is None:
            return None
        try:
            bus = QDBusConnection.sessionBus()
            if not bus.isConnected():
                return None
            iface = QDBusInterface(PORTAL_SERVICE, PORTAL_PATH, PORTAL_IFACE, bus)
            if not iface.isValid():
                return None
            iface.setTimeout(800)
            self._portal = iface
            return iface
        except Exception as e:                       # 没有 D-Bus 的裸机
            print(f"[AppBridge] portal 主题接口不可用（{e}），改用 Qt 检测")
            return None

    def _portal_color_scheme(self):
        """读 portal 的 color-scheme：True=暗 / False=亮 / None=不知道。

        PySide 把返回值包了两层 QDBusVariant（签名是 `v`），逐层拆开。
        """
        iface = self._portal_iface()
        if iface is None:
            return None
        try:
            reply = iface.call("Read", PORTAL_APPEARANCE, "color-scheme")
            if reply.type() != QDBusMessage.MessageType.ReplyMessage:
                return None
            args = reply.arguments()
            if not args:
                return None
            value = args[0]
            depth = 0
            while isinstance(value, QDBusVariant) and depth < 4:
                value = value.variant()
                depth += 1
            value = int(value)
            if value == 1:                           # prefer dark
                return True
            if value == 2:                           # prefer light
                return False
        except Exception as e:
            print(f"[AppBridge] 读取 portal color-scheme 失败：{e}")
        return None                                  # 0 = 无偏好，交给后面兜底

    def _detect_system_dark(self) -> bool:
        """操作系统当前是否为暗色。

        检测顺序（2026-11 修正，原因见 PORTAL_* 注释）：
          1. **Linux 先问 xdg-desktop-portal** —— GNOME/KDE 的亮暗偏好在
             这里，启动首帧就准；
          2. Qt 6.5+ 的 QStyleHints.colorScheme()（Windows/macOS 的主要
             来源；Linux 上没有平台主题插件时启动会误报 Light）；
          3. 调色板亮度兜底（暗色下 WindowText 比 Window 亮），仍判断
             不了就按亮色。
        """
        portal = self._portal_color_scheme()
        if portal is not None:
            return portal

        hints = getattr(self._app, "styleHints", lambda: None)()
        scheme = None
        if hints is not None and hasattr(hints, "colorScheme"):
            try:
                scheme = hints.colorScheme()
            except Exception:
                scheme = None

        color_scheme = getattr(Qt, "ColorScheme", None)
        if color_scheme is not None:
            if scheme == color_scheme.Dark:
                return True
            if scheme == color_scheme.Light:
                return False

        palette = self._app.palette()
        return (palette.color(QPalette.WindowText).lightness()
                > palette.color(QPalette.Window).lightness())

    def _watch_system_theme(self):
        """监听系统亮/暗切换。

        三个来源都回调 _on_system_theme_changed（内部按值去重）：
          · portal SettingChanged —— Linux 上最可靠（GNOME/KDE 都实现）；
          · colorSchemeChanged  —— Windows/macOS 的主信号；
          · paletteChanged      —— Unknown 平台的兜底。
        """
        hints = getattr(self._app, "styleHints", lambda: None)()
        if hints is not None and hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(self._on_system_theme_changed)
        if hasattr(self._app, "paletteChanged"):
            self._app.paletteChanged.connect(self._on_system_theme_changed)
        self._watch_portal_theme()

    def _watch_portal_theme(self):
        """订阅 portal 的 SettingChanged（system 模式下系统切亮/暗实时跟随）。"""
        if self._portal_iface() is None or QDBusConnection is None:
            return
        try:
            # ⚠ slot 字符串必须以 "1" 开头：Qt 的 C++ 版 connect() 沿用
            #   SLOT() 宏的约定，**无条件丢掉第一个字符**；不写 "1" 就会
            #   去找 "nPortalSettingChanged(...)" 而静默返回 False。
            ok = QDBusConnection.sessionBus().connect(
                PORTAL_SERVICE, PORTAL_PATH, PORTAL_IFACE, "SettingChanged",
                self, "1onPortalSettingChanged(QString,QString,QDBusVariant)")
            if not ok:
                print("[WARN] 订阅 portal SettingChanged 失败，"
                      "系统主题实时跟随只能依赖 Qt 的 colorSchemeChanged")
        except Exception as e:
            print(f"[WARN] 订阅 portal SettingChanged 异常：{e}")

    @Slot(str, str, QDBusVariant if QDBusVariant is not None else "QVariant")
    def onPortalSettingChanged(self, namespace, key, _value):
        """portal 里任何设置变化都会走到这里；只关心外观亮/暗。"""
        if namespace != PORTAL_APPEARANCE or key != "color-scheme":
            return
        self._on_system_theme_changed()

    def _on_system_theme_changed(self, *_):
        dark = self._detect_system_dark()
        if dark == self._systemDark:
            return
        self._systemDark = dark
        print(f"[INFO] 系统主题切换为{'暗色' if dark else '亮色'}")
        if self._colors is not None:
            # Colors 自己判断 mode：只有"跟随系统"才重算颜色
            self._colors.setProperty("systemDark", dark)
        self.systemDarkChanged.emit()

    def _bind_colors(self):
        """把主题推给 QML 的 Colors 单例。

        在 QML 加载**之前**调用（main.py 里 AppBridge 先于 engine.load 创建），
        这样第一帧就是正确主题。若模块还没注册，等事件循环再试一次。
        """
        if self._colors is not None:
            return
        try:
            colors = self._engine.singletonInstance("DuAD_Software", "Colors")
        except Exception as e:                       # 引擎/模块尚未就绪
            print(f"[AppBridge] 取 Colors 单例失败：{e}")
            colors = None
        if colors is None:
            # 只重试有限次：模块真有问题时不能每 0ms 空转一次
            self._bind_retries += 1
            if self._bind_retries <= 3:
                QTimer.singleShot(0, self._bind_colors)
            else:
                print("[WARN] Colors 单例加载失败，主题/配色将使用 QML 默认值")
            return

        self._colors = colors
        self._bind_retries = 0
        # 启动应用不要渐变（否则会看到"白 → 暗"的闪一下），之后恢复原时长
        duration = colors.property("animDuration")
        colors.setProperty("animDuration", 0)
        colors.setProperty("systemDark", self._systemDark)
        colors.setTheme(self._themeMode)
        colors.setPreset(self._preset)
        if duration is not None:
            colors.setProperty("animDuration", duration)
        print(f"[INFO] 主题: {self._themeMode}"
              f"（系统当前{'暗色' if self._systemDark else '亮色'}），"
              f"配色: {self._preset}")

    def _getThemeMode(self) -> str:
        return self._themeMode

    def _setThemeMode(self, mode: str):
        original = mode
        mode = self._pick_setting(mode, THEME_MODES, "")
        if not mode:
            print(f"[AppBridge] 忽略未知主题 {original!r}；合法值只有 {THEME_MODES}")
            return
        if mode == self._themeMode:
            return
        self._themeMode = mode
        self._settings.setValue("theme", mode)
        if self._colors is None:
            self._bind_colors()
        if self._colors is not None:
            self._colors.setTheme(mode)
        self.themeModeChanged.emit()

    themeMode = Property(str, _getThemeMode, _setThemeMode,
                         notify=themeModeChanged)

    def _getPreset(self) -> str:
        return self._preset

    def _setPreset(self, name: str):
        original = name
        name = self._pick_setting(name, THEME_PRESETS, "")
        if not name:
            print(f"[AppBridge] 忽略未知配色 {original!r}；合法值只有 {THEME_PRESETS}")
            return
        if name == self._preset:
            return
        self._preset = name
        self._settings.setValue("preset", name)
        if self._colors is None:
            self._bind_colors()
        if self._colors is not None:
            self._colors.setPreset(name)
        self.presetChanged.emit()

    preset = Property(str, _getPreset, _setPreset, notify=presetChanged)

    def _getSystemDark(self) -> bool:
        return self._systemDark

    systemDark = Property(bool, _getSystemDark, notify=systemDarkChanged)

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
    content_dir = _content_dir()

    # ── 应用图标 + Portal 集成（系统原生文件/目录对话框）────────────
    # 图标分两处，缺一不可：
    #   ① 运行中的窗口/任务栏：X11 由 setWindowIcon 生效；Wayland 没有
    #      窗口图标协议，只能靠 app_id + .desktop 的 Icon=。
    #   ② 启动器（应用菜单/桌面快捷方式）：duad-software.desktop 的 Icon=，
    #      指向下面安装进 hicolor 主题的 duad-software.png。
    # 历史问题：这里只写了 .desktop 且没有 Icon=，也从未 setWindowIcon ——
    # 于是从终端启动时任务栏是 Qt 默认图标，从菜单启动时是空白图标。
    _APP_ID = "duad-software"
    _APP_NAME = "DuAD"
    app.setApplicationName(_APP_NAME)
    app.setApplicationDisplayName("DuAD 异常检测上位机")
    # Portal 前提：setDesktopFileName + 系统里有对应 .desktop（App info）。
    # 否则 FileDialog 会回退成 Qt 自绘的“银白、大字体、每行带 size”老式表格。
    app.setDesktopFileName(_APP_ID)

    # 应用图标（256×256，由 favicon.ico 导出；打包后位于 _internal/images/）
    _icon_src = content_dir / "images" / "duad-software.png"
    if _icon_src.exists():
        app.setWindowIcon(QIcon(str(_icon_src)))
        print(f"[INFO] 窗口图标: {_icon_src}")
    else:
        print(f"[WARN] 窗口图标缺失: {_icon_src}")

    # 装进 hicolor 图标主题，.desktop 里只写主题名 Icon=duad-software。
    # （写绝对路径在部分桌面环境/图标缓存下不稳定，主题名最稳。）
    # 仅 Linux：Windows 没有 XDG 图标主题/desktop 条目，写过去只是垃圾文件。
    if not _IS_WIN:
        try:
            _icon_dir = (Path.home() / ".local" / "share" / "icons"
                         / "hicolor" / "256x256" / "apps")
            _icon_dir.mkdir(parents=True, exist_ok=True)
            if _icon_src.exists():
                shutil.copyfile(_icon_src, _icon_dir / f"{_APP_ID}.png")
        except Exception as e:
            print(f"[WARN] 安装应用图标失败（启动器可能显示空白图标）: {e}")

    # 桌面条目：Portal 用 + 启动器图标用。内容变了才重写 ——
    # 旧版本生成的文件没有 Icon=，必须能自愈，否则用户永远看到空白图标。
    if not _IS_WIN:
        try:
            _applications_dir = Path.home() / ".local" / "share" / "applications"
            _desktop_file = _applications_dir / f"{_APP_ID}.desktop"
            _desktop_text = (
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=DuAD 异常检测上位机\n"
                "Comment=融合Dinov2与双分支训练架构的工业异常检测\n"
                f'Exec="{sys.executable}" "{Path(__file__).resolve()}"\n'
                f"Icon={_APP_ID}\n"
                f"StartupWMClass={_APP_NAME}\n"
                "Terminal=false\n"
                "Categories=Utility;\n"
            )
            _applications_dir.mkdir(parents=True, exist_ok=True)
            _old = (_desktop_file.read_text(encoding="utf-8")
                    if _desktop_file.exists() else "")
            if _old != _desktop_text:
                _desktop_file.write_text(_desktop_text, encoding="utf-8")
                print(f"[INFO] 已更新桌面条目: {_desktop_file}")
        except Exception as e:
            print(f"[WARN] 安装 portal 桌面条目失败（原生对话框将回退自绘）: {e}")

    # 全局注册自定义字体（文泉驿微米黑），QML 中可直接 font.family 引用
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
    stage_bridge = None
    zstage_bridge = None
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

        # 二轴相机平台（ESP32 + 两台 PD42S1，走 WiFi/TCP）。
        # 独立于相机/算法链路：板子不在线时页面照样能打开，只是连不上。
        from Src.stage_bridge import StageBridge

        stage_bridge = StageBridge()
        engine.rootContext().setContextProperty("StageBridge", stage_bridge)
        _BRIDGES.append(stage_bridge)

        # Z 轴升降平台（另一块 ESP32 + 两台 PD42S1，**另一个 IP**）。
        # 与上面的二轴平台各自独立：两块板子、两套地址/口令，两张卡可以同时连着。
        from Src.zstage_bridge import ZStageBridge

        zstage_bridge = ZStageBridge()
        engine.rootContext().setContextProperty("ZStageBridge", zstage_bridge)
        # ⚠ 必须进 keepalive 列表：context property 不增加 Python 引用计数，
        #   被 GC 后 QML 侧 ZStageBridge 变 null → 界面上所有 Z 轴按钮"点了没反应"。
        _BRIDGES.append(zstage_bridge)

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
                    collect_bridge.saveError.emit(
                        QCoreApplication.translate("AppBridge", "请先设置保存目录和文件前缀"))
                    bridge.collectingOwner = ""
                    return
                if _start_gather_for("collect"):
                    collect_bridge.start()
                else:
                    collect_bridge.stop()
                    bridge.collectingOwner = ""
            elif owner == "stage":
                # 平台控制页的实时预览：只要画面，既不推理也不存盘。
                # 单独一个 owner 是为了和 Detect/Collect 正常互斥 —— 相机只能被
                # 一个会话抓着，否则 startGather/stopGather 会互相打断。
                detect_bridge.stop()
                collect_bridge.stop()
                if not _start_gather_for("stage"):
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
        # ⚠⚠ **相机必须第一个停**（2026-10-10 修正顺序）。
        #   原来它排在最后，而它前面是 `stage_bridge.parkAndDisconnect()` ——
        #   那个函数会 `processEvents()` **泵事件最长 8 秒**等台面走回零点。
        #   于是整个退出过程里相机还在采集：每帧 `QImage(...).copy()` 是
        #   2448×2048 ≈ 20MB，30fps ≈ 600MB/s 的分配/释放。用户 2026-10-10 报的
        #   "关闭的时候内存被占满"就发生在这个窗口期（真实原因另有其 bug，
        #   但退出时绝不该继续产帧）。
        if camera_bridge is not None:
            try:
                if camera_bridge.cameraConnected:
                    camera_bridge.stopGather()
                    camera_bridge.disconnectCamera()
            except Exception as e:
                print(f"[WARN] 退出清理相机失败: {e}")
        # 推理/保存线程依赖相机帧，跟着一起停
        if detect_bridge is not None:
            detect_bridge.stop()
        if collect_bridge is not None:
            collect_bridge.stop()
        if light_bridge is not None:
            light_bridge.disconnectSerial()
        if mqtt_bridge is not None:
            mqtt_bridge.disconnectServer()
        if stage_bridge is not None:
            # ⚠ 不是简单的 disconnect：`parkAndDisconnect()` 会**先把台面开回零点角**，
            #   再把"停在哪儿"记进 settings —— 下次开机靠它核对"我们不在的时候
            #   台面被动过没有"（见 StageBridge._check_park）。
            #   ⚠ 它**不是**"保住基准"的手段：驱动器只要不断电就一直数着位置，
            #     不回零点基准照样有效；这一步的价值是"留一个已知姿态"。
            #   内部有 8s 上限，失败只记日志，绝不拖住退出。
            try:
                stage_bridge.parkAndDisconnect()
            except Exception as e:
                print(f"[WARN] 退出回零点失败: {e}")
                stage_bridge.disconnectDevice()
        if zstage_bridge is not None:
            # ⚠ Z 轴**只断开，不做"退出回零点"**：这台是升降平台，
            #   没有"下次开机核对台面被动过没有"的需求（X/Y 那台才有），
            #   而且它 250mm 行程走一趟要好几秒 —— 白白拖慢退出。
            try:
                zstage_bridge.disconnectDevice()
            except Exception as e:
                print(f"[WARN] 断开 Z 轴平台失败: {e}")
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
