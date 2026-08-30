#!/usr/bin/env bash
set -euo pipefail

# DuAD 上位机启动脚本
# 优先使用包内虚拟环境（CPU-Portable 包会自带），否则回退到系统 python3。
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$ROOT_DIR/DuAD_SoftwareContent/pyqml/bin/python"
MAIN_PY="$ROOT_DIR/DuAD_SoftwareContent/main.py"

if [ ! -f "$MAIN_PY" ]; then
    echo "[run] 未找到主程序: $MAIN_PY" >&2
    exit 1
fi

# 大分辨率（2448×2048）采集需要足够的 USB 缓冲内存：
# usbfs_memory_mb 过小（内核默认 16MB）时大恒 U3VTL 会以 -1010 拒绝启动采集。
if [ -r /sys/module/usbcore/parameters/usbfs_memory_mb ]; then
    _usbfs="$(cat /sys/module/usbcore/parameters/usbfs_memory_mb 2>/dev/null || echo 0)"
    if [ "${_usbfs:-0}" -lt 64 ]; then
        echo "[run] ⚠️ 内核 USB 缓冲内存上限仅 ${_usbfs}MB（全幅 2448×2048 可能无法启动采集）。" >&2
        echo "[run]    请执行: sudo bash $ROOT_DIR/scripts/set_usbfs.sh" >&2
    fi
fi

if [ -x "$VENV_PY" ]; then
    exec "$VENV_PY" -u "$MAIN_PY" "$@"
fi

echo "[run] 未找到包内虚拟环境，改用系统 python3" >&2
exec python3 "$MAIN_PY" "$@"
