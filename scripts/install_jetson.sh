#!/usr/bin/env bash
# ============================================================
# DuAD 上位机 Jetson（aarch64）安装脚本 —— 默认 CPU 推理
#
# 用法：解压 DuAD_<版本>_Jetson_aarch64.tar.gz 后，在包内运行
#   bash install.sh
#
# 行为：
#   1. 安装 micromamba（用户态，无需 sudo）
#   2. 创建环境 ~/micromamba/envs/duad（已存在则跳过创建）
#      python 3.10 + PySide6 6.11（conda-forge，aarch64 wheel 的
#      pyqml/ 走 pip 因 glibc 2.39 要求无法安装，conda 自带新版 glib）
#   3. 装 aarch64 唯一可用的 ORT：NVIDIA Jetson 索引的
#      onnxruntime_gpu 1.24.0（jp6/cu126，py310 wheel）。
#      ⚠️ 关键：这里**不装** cu12 运行库补丁 —— CUDA/TRT EP 初始化
#      失败时 onnxruntime 自动回退 CPU —— 即“默认只支持 CPU 推理”。
#      需要 ONNX(CUDA)/TRT 加速时再运行 enable_gpu.sh（加载 cu12 库）。
#   4. 尝试安装相机 USB udev 规则（无 sudo 权限则打印手动命令）
# ============================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MM_BIN="$HOME/micromamba/bin/micromamba"
ENV_DIR="$HOME/micromamba/envs/duad"

echo "[install] 项目目录: $ROOT_DIR"
echo "[install] 依赖环境: $ENV_DIR"

# ── 1. micromamba（用户态）─────────────────────────────────
if [ ! -x "$MM_BIN" ]; then
    echo "[install] 安装 micromamba..."
    mkdir -p "$HOME/micromamba/bin"
    curl -L https://micro.mamba.pm/api/micromamba/linux-aarch64/latest -o /tmp/mm.tar.bz2
    tar -xjf /tmp/mm.tar.bz2 bin/micromamba -C /tmp
    mv /tmp/bin/micromamba "$MM_BIN"
fi
echo "[install] micromamba: $("$MM_BIN" --version 2>/dev/null | head -1)"

# ── 2. 创建 conda-forge 环境（PySide6 只能走 conda）────────
if [ ! -x "$ENV_DIR/bin/python" ]; then
    echo "[install] 创建环境（conda-forge PySide6 6.11 + Python 3.10，首次约几分钟）..."
    "$MM_BIN" create -y -p "$ENV_DIR" -c conda-forge python=3.10 pyside6=6.11 pip
    "$MM_BIN" run -p "$ENV_DIR" pip install numpy==2.2.6 Pillow pyserial paho-mqtt
else
    echo "[install] 环境已存在，跳过创建（不会破坏现有 GPU 配置）"
fi

# ── 3. ORT（CPU 默认）──────────────────────────────────────
# 若已装过 onnxruntime*（含 GPU 补丁），这里保持幂等：pip 会就地覆盖为同一个包。
echo "[install] 安装 onnxruntime_gpu 1.24.0（NVIDIA Jetson 索引）..."
echo "         未装 cu12 运行库时默认回退 CPU 推理"
"$MM_BIN" run -p "$ENV_DIR" pip install --index-url https://pypi.jetson-ai-lab.io/jp6/cu126/+simple/ onnxruntime_gpu==1.24.0

echo "[install] 当前可用的推理 provider："
"$MM_BIN" run -p "$ENV_DIR" python -c "import onnxruntime as ort; print('  ', ort.get_available_providers())"

# ── 4. 相机 USB 权限（需要 sudo）───────────────────────────
RULES="$ROOT_DIR/backend/config/99-galaxy-dev.rules"
if [ -f "$RULES" ]; then
    if sudo cp "$RULES" /etc/udev/rules.d/ 2>/dev/null; then
        sudo udevadm control --reload-rules && sudo udevadm trigger
        echo "[install] udev 规则已安装，请重新插拔相机"
    else
        echo "[install] ⚠️ 无法自动安装相机权限，请手动执行（需要 sudo 密码）："
        echo "   sudo cp backend/config/99-galaxy-dev.rules /etc/udev/rules.d/"
        echo "   sudo udevadm control --reload-rules && sudo udevadm trigger"
        echo "   然后重新插拔相机"
    fi
fi

# ── 5. 提升内核 USB 缓冲上限（全幅 2448×2048 采集必需）──────
# 内核默认 16MB，U3VTL 分配大负载缓冲环可能失败（-1010 开采失败，
# 大恒官方 FAQ 解法 = SetUSBStack.sh）。无 sudo 权限时仅打印提示，不中断。
if [ -f "$ROOT_DIR/scripts/set_usbfs.sh" ]; then
    bash "$ROOT_DIR/scripts/set_usbfs.sh" >/dev/null 2>&1 || true
fi

echo
echo "═══════════════════════════════════════════════════════"
echo " 安装完成（默认 CPU 推理）。"
echo " 启动软件：        bash run_jetson.sh"
echo " 如需 GPU 加速：   bash enable_gpu.sh   （加载 ONNX(CUDA)/TRT 依赖）"
echo " 桌面临时图标：    bash install-desktop.sh"
echo "═══════════════════════════════════════════════════════"