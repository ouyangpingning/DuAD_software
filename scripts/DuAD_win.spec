# -*- mode: python ; coding: utf-8 -*-
"""DuAD Windows 打包 spec（onedir）。

布局（PyInstaller 6 onedir：数据相对 _internal，main.py 的
_content_dir/_backend_root/_translations_root 在 frozen 下都用 sys._MEIPASS）：
  _internal/                 = QML 资源（App.qml/pages/images/fonts...）
  _internal/backend/         = Python 源码 + 相机 SDK libs_win + config
  _internal/translations/    = i18n .qm
GPU 库（nvidia-* / tensorrt / 驱动）一律不打包：onnxruntime-gpu 主包带
provider DLL，但没有 nvidia 库时自动回退 CPU；有 GPU 库的机器经
TENSORRT_LIB_DIR / PATH 注入自动加速。
"""
from pathlib import Path

ROOT = Path(SPECPATH).parent
CONTENT = ROOT / "DuAD_SoftwareContent"

_datas = []
for _rel in ["App.qml", "MainuiRoot.qml", "MainWindow.ui.qml"]:
    _p = CONTENT / _rel
    if _p.exists():
        _datas.append((str(_p), "."))
for _rel in ["DuAD_Software", "pages", "images", "fonts"]:
    _p = CONTENT / _rel
    if _p.is_dir():
        _datas.append((str(_p), _rel))
if (ROOT / "backend").is_dir():
    _datas.append((str(ROOT / "backend"), "backend"))
if (ROOT / "translations").is_dir():
    _datas.append((str(ROOT / "translations"), "translations"))

a = Analysis(
    [str(CONTENT / "main.py")],
    pathex=[str(CONTENT), str(ROOT / "backend")],
    binaries=[],
    datas=_datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # GPU 运行库 / 开发环境一律不打包
        "nvidia", "nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_runtime",
        "tensorrt", "tensorrt_cu13_libs", "tensorrt_libs",
        "torch", "tests", "pytest", "pyqml", "pyqml_win", "opencv",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DuAD",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # 无控制台窗口（日志走 DuAD_app.log）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(ROOT / "favicon.ico")],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DuAD",
)
