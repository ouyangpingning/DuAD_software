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

if [ -x "$VENV_PY" ]; then
    exec "$VENV_PY" -u "$MAIN_PY" "$@"
fi

echo "[run] 未找到包内虚拟环境，改用系统 python3" >&2
exec python3 "$MAIN_PY" "$@"
