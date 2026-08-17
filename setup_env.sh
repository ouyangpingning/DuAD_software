#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# DuAD 上位机环境配置脚本
#
# 用法：
#   bash setup_env.sh                     # 默认安装 GPU 推理依赖
#   DUAD_INSTALL_GPU=0 bash setup_env.sh  # CPU 推理（无 NVIDIA GPU）
#
# 仅验证平台：Linux x86_64 + Python 3.10+（开发机为 Python 3.14）
# Windows / Linux ARM 需使用对应平台的大恒相机 SDK 和 Python 包，
# 本脚本未覆盖。
# ============================================================

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT_DIR/DuAD_SoftwareContent/pyqml"
INSTALL_GPU="${DUAD_INSTALL_GPU:-1}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "[setup] 项目目录: $ROOT_DIR"
echo "[setup] 虚拟环境: $VENV_DIR"
echo "[setup] Python: $($PYTHON_BIN --version)"

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
echo "如果未安装 NVIDIA GPU 驱动，请改用："
echo "  DUAD_INSTALL_GPU=0 bash $ROOT_DIR/setup_env.sh"
