#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# DuAD 上位机环境配置脚本
#
# 用法：
#   bash setup_env.sh                     # 自动检测 NVIDIA 驱动，有则 GPU、无则 CPU
#   DUAD_INSTALL_GPU=1 bash setup_env.sh  # 强制安装 GPU 推理依赖
#   DUAD_INSTALL_GPU=0 bash setup_env.sh  # 强制安装 CPU 推理依赖
#
# 仅验证平台：Linux x86_64 + Python 3.10+（开发机为 Python 3.14）
# Windows / Linux ARM 需使用对应平台的大恒相机 SDK 和 Python 包，
# 本脚本未覆盖。
# ============================================================

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT_DIR/DuAD_SoftwareContent/pyqml"
INSTALL_GPU="${DUAD_INSTALL_GPU:-auto}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

# NVIDIA 驱动检测：
#   /proc/driver/nvidia/version 存在 -> 已装驱动（最轻量、无 nvidia-smi 依赖）
#   nvidia-smi -L 成功             -> 用户空间工具可见 GPU（兼容某些容器/权限场景）
has_nvidia_driver() {
    if [ -r /proc/driver/nvidia/version ]; then
        return 0
    fi
    if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
        return 0
    fi
    return 1
}

echo "[setup] 项目目录: $ROOT_DIR"
echo "[setup] 虚拟环境: $VENV_DIR"
echo "[setup] Python: $($PYTHON_BIN --version)"

if [ "$INSTALL_GPU" = "auto" ]; then
    if has_nvidia_driver; then
        INSTALL_GPU=1
        echo "[setup] 检测到 NVIDIA 驱动，将安装 GPU 推理依赖（onnxruntime-gpu + TensorRT）"
    else
        INSTALL_GPU=0
        echo "[setup] 未检测到 NVIDIA 驱动，将安装 CPU 推理依赖（onnxruntime）"
    fi
elif [ "$INSTALL_GPU" != "1" ] && [ "$INSTALL_GPU" != "0" ]; then
    echo "[ERROR] DUAD_INSTALL_GPU 只能是 auto/1/0，当前为: $INSTALL_GPU"
    exit 1
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "[ERROR] 找不到 $PYTHON_BIN，请先安装 Python 3.10+"
    exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
    echo "[setup] 创建虚拟环境..."
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "[setup] 升级 pip..."
python -m pip install --upgrade pip

if [ "$INSTALL_GPU" = "1" ]; then
    echo "[setup] 安装 GPU 推理依赖（onnxruntime-gpu + TensorRT）..."
    python -m pip install -r "$ROOT_DIR/requirements-gpu.txt"
else
    echo "[setup] 安装 CPU 推理依赖..."
    # 若 venv 曾装过 GPU 依赖，先卸载避免与 CPU onnxruntime 同目录冲突
    python -m pip uninstall -y onnxruntime-gpu tensorrt_cu13_libs >/dev/null 2>&1 || true
    python -m pip install -r "$ROOT_DIR/requirements-cpu.txt"
fi

echo
echo "[setup] 依赖安装完成。"
echo
echo "下一步："
echo "  1) 安装大恒相机 USB 权限规则（需要 sudo）："
echo "     sudo cp $ROOT_DIR/backend/config/99-galaxy-dev.rules /etc/udev/rules.d/"
echo "     sudo udevadm control --reload-rules && sudo udevadm trigger"
echo "  2) 启动软件："
echo "     python $ROOT_DIR/DuAD_SoftwareContent/main.py"
echo
echo "提示："
echo "  默认会自动检测 NVIDIA 驱动；如需手动指定："
echo "  DUAD_INSTALL_GPU=1 bash $ROOT_DIR/setup_env.sh   # 强制 GPU"
echo "  DUAD_INSTALL_GPU=0 bash $ROOT_DIR/setup_env.sh   # 强制 CPU"
