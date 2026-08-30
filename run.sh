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
    # CPU-Portable 内置 venv 是构建机 Python 版本锁定的（如 CI 为 3.12）。
    # 目标机默认 python3 版本不同（如 3.14）时，venv 的
    # lib/python3.x/site-packages 不匹配 → PySide6/onnxruntime 导入失败。
    # 自愈：检测到不兼容就用本机 python3 原地重建 venv（首次需联网装依赖）。
    if ! "$VENV_PY" -c "import PySide6, onnxruntime" >/dev/null 2>&1; then
        echo "[run] 内置 Python 环境与当前系统不兼容，正在用本机 python3 重建"
        echo "[run]   （首次运行需联网下载依赖，约 3~5 分钟）..." >&2
        rm -rf "$ROOT_DIR/DuAD_SoftwareContent/pyqml"
        if ! python3 -m venv "$ROOT_DIR/DuAD_SoftwareContent/pyqml" 2>/dev/null; then
            echo "[run] 重建失败：本机缺少 python3-venv。请先运行 Installer 包或安装 venv 后重试" >&2
            exit 1
        fi
        "$ROOT_DIR/DuAD_SoftwareContent/pyqml/bin/python" -m pip install --upgrade pip
        "$ROOT_DIR/DuAD_SoftwareContent/pyqml/bin/python" -m pip install -r "$ROOT_DIR/requirements-cpu.txt"
        echo "[run] 环境重建完成"
    fi
    exec "$VENV_PY" -u "$MAIN_PY" "$@"
fi

echo "[run] 未找到包内虚拟环境，改用系统 python3" >&2
exec python3 "$MAIN_PY" "$@"
