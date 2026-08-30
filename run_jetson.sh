#!/usr/bin/env bash
# DuAD 上位机 Jetson（aarch64 / JetPack 6.2 或 7.2）启动脚本
#
# 依赖（首次部署见 docs/Jetson部署.md）：
#   - micromamba 环境 ~/micromamba/envs/duad
#     （python 3.10 + PySide6(conda-forge) + onnxruntime-gpu 1.24(Jetson) + numpy/Pillow/pyserial/paho-mqtt
#      注意：JP7.2(CUDA13) 上 onnxruntime-gpu 需补装 cu12 库并注入 LD_LIBRARY_PATH，
#      见 AGENTS.md「Jetson 关键事实」/ docs/Jetson部署.md）
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

# ── 推理 provider ─────────────────────────────────────────────
# onnx_infer.py 在 aarch64 默认优先 CUDA（JetPack 6.2 的 TRT 10.3 对 DINOv2
# 数值错误）。JetPack 7.2+（TRT 10.13+ 数值正确）想用 TRT 加速就开这个；
# 若回到 JetPack 6.2 请注释掉。配合 onnx_infer.py 的 TRT 引擎缓存，首次构建
# ~43s 落盘，之后每次启动 ~1.3s。
export DUAD_PREFER_TRT=1

cd "$ROOT_DIR"
exec "$PY" -u DuAD_SoftwareContent/main.py "$@"
