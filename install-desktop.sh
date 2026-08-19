#!/usr/bin/env bash
set -euo pipefail

# 创建 DuAD 桌面图标（解压后执行一次，之后可在应用菜单/桌面双击启动）
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ID="duad-software"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESKTOP_FILE="$APPS_DIR/$APP_ID.desktop"
RUN_SCRIPT="$ROOT_DIR/run.sh"
ICON="$ROOT_DIR/DuAD_SoftwareContent/images/Detec.svg"

mkdir -p "$APPS_DIR"

cat > "$DESKTOP_FILE" <<INNER
[Desktop Entry]
Type=Application
Name=DuAD 异常检测上位机
Comment=融合Dinov2与双分支训练架构的工业异常检测
Exec="$RUN_SCRIPT"
Path="$ROOT_DIR"
Icon=$ICON
Terminal=false
Categories=Utility;
INNER

chmod +x "$DESKTOP_FILE" 2>/dev/null || true
chmod +x "$RUN_SCRIPT" 2>/dev/null || true

echo "[install] 桌面图标已创建: $DESKTOP_FILE"
echo "[install] 之后可在应用菜单搜索 DuAD 启动。"
