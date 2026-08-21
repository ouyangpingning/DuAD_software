#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows 打包脚本：PyInstaller onedir + zip + SHA256SUMS。

用法：
  pyqml_win\\Scripts\\python.exe -u scripts/package_win.py [版本号] [--with-gpu]

产物（dist/）：
  DuAD\\                     onedir 应用（DuAD.exe 双击启动）
  DuAD_<版本>_Windows_x64.zip  主程序压缩包（CPU-only，小）
  SHA256SUMS                校验和

GPU 库（nvidia-* / tensorrt / 驱动）不随主程序打包：onnxruntime-gpu 主包带
provider DLL，无 nvidia 库时自动回退 CPU；有 GPU 库的机器经
TENSORRT_LIB_DIR / PATH 注入自动加速。

加 --with-gpu 时，主程序 zip 之后额外调用 collect_gpu_dlls_win.py 生成
可选 GPU 支持包 DuAD_GPU_runtime_<版本>_Windows_x64.zip（nvidia/ + tensorrt/），
并复制到 dist/DuAD/（本地验证）。目标机解压支持包到 DuAD.exe 同级目录即加速。
"""
import argparse
import hashlib
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV_PY = ROOT / "DuAD_SoftwareContent" / "pyqml_win" / "Scripts" / "python.exe"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
SPEC = ROOT / "scripts" / "DuAD_win.spec"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("version", nargs="?", default="0.0.0-dev")
    ap.add_argument("--with-gpu", action="store_true",
                    help="打包主程序后额外生成 GPU 支持包（DuAD_GPU_runtime_*.zip）"
                         "并复制 nvidia/ + tensorrt/ 到 dist/DuAD/")
    args = ap.parse_args()
    ver = args.version

    print("== 1/4 清理旧的 build/dist ==")
    shutil.rmtree(BUILD, ignore_errors=True)
    shutil.rmtree(DIST / "DuAD", ignore_errors=True)

    print("== 2/4 PyInstaller 打包 onedir ==")
    subprocess.run(
        [str(VENV_PY), "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", str(DIST), "--workpath", str(BUILD), str(SPEC)],
        check=True, cwd=str(ROOT))

    app = DIST / "DuAD"
    if not (app / "DuAD.exe").exists():
        raise SystemExit("打包失败：未生成 DuAD.exe")
    if not (app / "_internal" / "PySide6").is_dir():
        raise SystemExit(
            "打包失败：_internal 缺 PySide6，说明 COLLECT 阶段被中断（文件没拷完）。"
            "请重新运行本脚本，且打包期间不要关闭终端/中断进程。")

    print("== 3/4 压缩 zip ==")
    zip_name = f"DuAD_{ver}_Windows_x64.zip"
    zip_path = DIST / zip_name
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(app.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(app.parent))
    # 校验 zip 完整性（防打包被中断时静默产出坏 zip）
    with zipfile.ZipFile(zip_path) as _z:
        if not any(n.endswith("DuAD.exe") for n in _z.namelist()):
            raise SystemExit("打包失败：zip 缺 DuAD.exe（压缩可能被中断）")

    if args.with_gpu:
        print("== 生成 GPU 支持包（--with-gpu） ==")
        shutil.rmtree(DIST / "DuAD_GPU_runtime", ignore_errors=True)
        for _p in DIST.glob("DuAD_GPU_runtime_*_Windows_x64.zip"):
            _p.unlink(missing_ok=True)
        subprocess.run(
            [str(VENV_PY), "-u", str(ROOT / "scripts" / "collect_gpu_dlls_win.py"),
             ver, "--into-app"],
            check=True, cwd=str(ROOT))

    print("== 4/4 SHA256SUMS ==")
    sums = DIST / "SHA256SUMS"
    with open(sums, "w", encoding="utf-8") as f:
        for p in sorted(DIST.iterdir()):
            if p.is_file() and p.suffix == ".zip":
                f.write(f"{sha256(p)}  {p.name}\n")

    print(f"\n完成！产物：")
    print(f"  {app}")
    print(f"  {zip_path}  ({(zip_path.stat().st_size / 1e6):.1f} MB)")
    print(f"  {sums}")


if __name__ == "__main__":
    main()
