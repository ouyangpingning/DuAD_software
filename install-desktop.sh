#!/usr/bin/env bash
set -euo pipefail

# 创建 DuAD 桌面图标（解压后执行一次，之后可在应用菜单/桌面双击启动）
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ID="duad-software"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESKTOP_FILE="$APPS_DIR/$APP_ID.desktop"
RUN_SCRIPT="$ROOT_DIR/run.sh"

# 应用图标：装进 hicolor 主题（.desktop 里只写主题名，避免绝对路径在
# 某些桌面环境/图标缓存下不生效）。源图 from favicon.ico（256×256）。
ICON_NAME="duad-software"
ICON_SRC="$ROOT_DIR/DuAD_SoftwareContent/images/duad-software.png"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps"

mkdir -p "$APPS_DIR" "$ICON_DIR"
if [ -f "$ICON_SRC" ]; then
    cp -f "$ICON_SRC" "$ICON_DIR/$ICON_NAME.png"
else
    echo "[install] 警告：未找到应用图标 $ICON_SRC" >&2
fi

cat > "$DESKTOP_FILE" <<INNER
[Desktop Entry]
Type=Application
Name=DuAD 异常检测上位机
Comment=融合Dinov2与双分支训练架构的工业异常检测
Exec="$RUN_SCRIPT"
Icon=$ICON_NAME
StartupWMClass=DuAD
Terminal=false
Categories=Utility;
INNER

chmod +x "$DESKTOP_FILE" 2>/dev/null || true
chmod +x "$RUN_SCRIPT" 2>/dev/null || true

# 让桌面环境尽快刷新条目/图标缓存（命令不存在时忽略）
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" >/dev/null 2>&1 || true
fi

echo "[install] 桌面图标已创建: $DESKTOP_FILE"
echo "[install] 之后可在应用菜单搜索 DuAD 启动。"
