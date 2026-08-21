#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows GPU 支持包收集脚本。

把 onnxruntime-gpu 推理所需的 CUDA 运行库（cuBLAS/cuDNN/cudart）与
TensorRT 运行库 DLL 收集成**可选** GPU 支持包（不随主程序打包），供有
GPU 的目标机解压到 exe 同目录，实现自动加速。

用法（用 pyqml_win 的 python 跑，这样能直接定位到它自己的 nvidia pip 包）：

  pyqml_win\\Scripts\\python.exe -u scripts/collect_gpu_dlls_win.py [版本号]
      [--trt {min,full}] [--into-app]

产物（dist/）：
  DuAD_GPU_runtime\\                    暂存目录（nvidia/ + tensorrt/ + README）
  DuAD_GPU_runtime_<版本>_Windows_x64.zip   GPU 支持包

设计约定（与 main.py / onnx_infer.py 的 frozen 注入逻辑一一对应）：
  - frozen 下从 exe 同目录读 nvidia/ 与 tensorrt/ 并注入 PATH；
  - nvidia 包结构必须保持 pip 原样：nvidia/cu13/bin/x86_64/*.dll、
    nvidia/cudnn/bin/*.dll（main.py 会 glob nvidia/*/bin、onnx_infer.py
    会精确找 cu13/bin/x86_64 与 cudnn/bin）；
  - tensorrt 放 tensorrt/bin/*.dll（代码也认 tensorrt/ 根目录）。

--trt min（默认）：TensorRT 只收核心推理 DLL + nvinfer_builder_resource_ptx_10.dll
    （PTX JIT 覆盖所有 GPU 架构，首次建引擎稍慢）；跳过 nvinfer_builder_resource_sm*
    （每个上百 MB，只加速对应架构的建引擎）。约 1.8GB 总量。
--trt full：TensorRT bin 全部 DLL（含所有 sm* builder 资源）。约 3.4GB 总量，
    但各架构建引擎最快。

CUDA 运行库（cuBLAS/cuDNN/cudart）两种模式都完整收集（含 nvrtc/nvblas，避免
边界情况缺 DLL 静默回退 CPU）。显卡驱动（≥585）不在此列，目标机需单独安装。
"""
import argparse
import os
import shutil
import sys
import sysconfig
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
APP = DIST / "DuAD"
STAGE = DIST / "DuAD_GPU_runtime"

# TensorRT 来源优先级：环境变量 TENSORRT_LIB_DIR → 项目内 backend/libs_win_tensorrt/bin
_PROJ_TRT = ROOT / "backend" / "libs_win_tensorrt" / "bin"


def _env_from_registry(name: str) -> str:
    """兜底：从 Windows 注册表读用户级/系统级环境变量。

    某些启动方式（IDE、服务、已运行进程 spawn 的子进程）不会继承注册表里
    新设的 User 环境变量，os.environ 拿不到时这里再查一次注册表。
    """
    try:
        import winreg
    except ImportError:
        return ""
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, "Environment") as k:
                val, _ = winreg.QueryValueEx(k, name)
                if val:
                    return val
        except OSError:
            continue
    return ""


def _copy_dll_tree(src: Path, dst: Path, skip=None) -> int:
    """把 src 下所有 *.dll 按相对路径复制到 dst，返回复制文件数。"""
    n = 0
    for p in sorted(src.rglob("*.dll")):
        if skip and skip(p):
            continue
        rel = p.relative_to(src)
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, out)
        n += 1
    return n


def _find_trt_bin() -> Path:
    cands = []
    env = os.environ.get("TENSORRT_LIB_DIR", "") or _env_from_registry("TENSORRT_LIB_DIR")
    if env:
        cands.append(Path(env))
    cands.append(_PROJ_TRT)
    for c in cands:
        if c and c.is_dir():
            return c
    raise SystemExit(
        "找不到 TensorRT bin 目录：请设置环境变量 TENSORRT_LIB_DIR 指向 "
        "TensorRT 解压后的 bin 目录（如 ...\\TensorRT-10.16.1.11\\bin），"
        "或把 DLL 放进 backend/libs_win_tensorrt/bin/。")


def collect(ver: str, trt_mode: str = "min", into_app: bool = False) -> Path:
    print("== 收集 GPU DLL 到支持包 ==")

    # 1) CUDA 运行库：来自当前 pyqml_win venv 的 nvidia pip 包（cu13 + cudnn）
    purelib = Path(sysconfig.get_paths()["purelib"])
    nvidia_src = purelib / "nvidia"
    if not (nvidia_src / "cu13" / "bin" / "x86_64").is_dir() or \
            not (nvidia_src / "cudnn" / "bin").is_dir():
        raise SystemExit(
            f"venv 缺 nvidia 运行库 pip 包：{nvidia_src}\n"
            "请 pip install nvidia-cublas-cu13 nvidia-cudnn-cu13 nvidia-cuda-runtime-cu13")
    for sub in ("cu13", "cudnn"):
        dst = STAGE / "nvidia" / sub
        n = _copy_dll_tree(nvidia_src / sub, dst)
        print(f"  nvidia/{sub}: {n} 个 DLL → {dst}")

    # 2) TensorRT：来自 TENSORRT_LIB_DIR / 项目内 libs_win_tensorrt
    trt_bin = _find_trt_bin()
    dst = STAGE / "tensorrt" / "bin"
    if trt_mode == "min":
        n = _copy_dll_tree(
            trt_bin, dst,
            skip=lambda p: p.name.startswith("nvinfer_builder_resource_sm"))
    else:
        n = _copy_dll_tree(trt_bin, dst)
    print(f"  tensorrt/bin: {n} 个 DLL（--trt {trt_mode}）→ {dst}")

    # 3) 使用说明
    readme = STAGE / "README_GPU.txt"
    readme.write_text(
        "DuAD GPU 支持包（可选）\n"
        "======================\n"
        "把本压缩包里的 nvidia 和 tensorrt 两个文件夹解压到与 DuAD.exe 同级目录：\n\n"
        "  DuAD\\\n"
        "    DuAD.exe\n"
        "    _internal\\\n"
        "    nvidia\\      <- 本包\n"
        "    tensorrt\\    <- 本包\n"
        "    README_GPU.txt\n\n"
        "放好后双击 DuAD.exe，启动日志出现\n"
        "  模型预热完成（['TensorrtExecutionProvider', 'CUDAExecutionProvider', ...]）\n"
        "即表示已启用 GPU。未放置本包时程序仍可运行，但推理回退 CPU。\n\n"
        "注意：显卡驱动需 ≥ 585（CUDA 13 要求），须在目标机单独安装 NVIDIA 驱动。\n",
        encoding="utf-8")
    print(f"  README_GPU.txt 已写入")

    # 4) 可选：直接拷进 dist/DuAD（本地 GPU 验证用，需在主程序 zip 之后执行）
    if into_app:
        for sub in ("nvidia", "tensorrt"):
            shutil.copytree(STAGE / sub, APP / sub, dirs_exist_ok=True)
        print(f"  已复制 nvidia/ + tensorrt/ 到 {APP}（本地 GPU 验证）")

    # 5) 打包 zip
    zip_path = DIST / f"DuAD_GPU_runtime_{ver}_Windows_x64.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(STAGE.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(STAGE))
    size_mb = zip_path.stat().st_size / 1e6
    print(f"\n完成！GPU 支持包：{zip_path}（{size_mb:.1f} MB）")
    print("用法：解压到 DuAD.exe 同级目录（nvidia/ + tensorrt/）即可自动加速。")
    return zip_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("version", nargs="?", default="0.0.0-dev")
    ap.add_argument("--trt", choices=("min", "full"), default="min",
                    help="min=核心 DLL+PTX（约 1.8GB 总量，默认）；full=全部 sm* 资源（约 3.4GB）")
    ap.add_argument("--into-app", action="store_true",
                    help="同时复制到 dist/DuAD/（本地验证用；主程序 zip 之后执行才不污染主包）")
    args = ap.parse_args()

    if not (APP / "DuAD.exe").exists():
        raise SystemExit("未找到 dist/DuAD/DuAD.exe，请先运行 package_win.py 打包主程序。")

    shutil.rmtree(STAGE, ignore_errors=True)
    collect(args.version, args.trt, args.into_app)


if __name__ == "__main__":
    main()
