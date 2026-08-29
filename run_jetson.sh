#!/usr/bin/env bash
# DuAD 上位机 Jetson（aarch64 / JetPack 6.2）启动脚本
#
# 依赖（首次部署见 docs/Jetson部署.md）：
#   - micromamba 环境 ~/micromamba/envs/duad
#     （python 3.10 + PySide6 6.11(conda-forge) + onnxruntime-gpu 1.24(Jetson) + numpy/Pillow/pyserial/paho-mqtt）
#   - backend/libs_arm64（arm64 大恒 SDK + libbayer_demosaic.so，随仓库提供）
#   - 相机 USB 权限：sudo cp backend/config/99-galaxy-dev.rules /etc/udev/rules.d/ && sudo udevadm control --reload-rules
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_DIR="$HOME/micromamba/envs/duad"
PY="$ENV_DIR/bin/python"

if [ ! -x "$PY" ]; then
    echo "[run] 未找到环境 $ENV_DIR，请先按 docs/Jetson部署.md 完成部署" >&2
    exit 1
fi

# 无显示时回退到本地桌面 :0（SSH 会话中直接弹出到桌面）
if [ -z "${DISPLAY:-}" ] && [ -S /tmp/.X11-unix/X0 ]; then
    export DISPLAY=:0
fi

cd "$ROOT_DIR"
exec "$PY" -u DuAD_SoftwareContent/main.py "$@"
