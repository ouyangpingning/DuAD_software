#!/usr/bin/env bash
# ============================================================
# DuAD 上位机 Jetson（aarch64）打包脚本
#
# 产物（输出到 dist/）：
#   DuAD_<版本>_Jetson_aarch64.tar.gz
#       应用源码 + 安装脚本（不含 Python 环境，环境由安装脚本现场创建）
#       - install.sh     默认 CPU 推理环境安装
#       - enable_gpu.sh  可选：加载 ONNX(CUDA)/TRT 依赖（cu12 补丁）
#       - run_jetson.sh  启动脚本（默认 DUAD_PREFER_TRT=1）
#       - install-desktop.sh / README / docs
#   SHA256SUMS
#
# 用法：bash scripts/package_jetson.sh [版本号]
#
# 说明：
#   - aarch64 无法用 pip venv 装 PySide6（wheel 要求 glibc 2.39），
#     故不内置环境；install.sh 用 conda-forge micromamba 在目标机现场建环境。
#   - GPU 系统库（CUDA/TRT 约数 GB）不打包：enable_gpu.sh 从 pip 装
#     cu12 运行库“拼装”（JP7.2 CUDA13 无官方 ORT 包的方案）。
# ============================================================

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
BUILD_DIR="$DIST_DIR/staging"

if [ -n "${1:-}" ]; then
    VERSION="$1"
else
    VERSION="$(git -C "$ROOT_DIR" describe --tags --always --dirty 2>/dev/null || echo "0.1.0")"
fi
VERSION="${VERSION#v}"
VERSION="${VERSION//\//_}"
PKG_NAME="DuAD_${VERSION}_Jetson_aarch64"

echo "[package-jetson] 项目目录: $ROOT_DIR"
echo "[package-jetson] 版本: $VERSION"
echo "[package-jetson] 产物: $DIST_DIR/${PKG_NAME}.tar.gz"

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/$PKG_NAME"

# ── 1. 拷贝应用文件 ────────────────────────────────────────
echo "[package-jetson] 拷贝应用文件..."
tar -C "$ROOT_DIR" \
    --exclude='.git' \
    --exclude='dist' \
    --exclude='DuAD_SoftwareContent/pyqml' \
    --exclude='DuAD_SoftwareContent/pyqml_win' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='*.onnx' \
    --exclude='*.ckpt' \
    --exclude='*.pth' \
    --exclude='*.pt' \
    --exclude='*.engine' \
    --exclude='*.log' \
    --exclude='*.tmp' \
    --exclude='*.mp4' \
    -cf - . | tar -C "$BUILD_DIR/$PKG_NAME" -xf -

# 把安装脚本放到包根目录，命名友好（用户解压后直接 bash install.sh）
cp "$ROOT_DIR/scripts/install_jetson.sh"    "$BUILD_DIR/$PKG_NAME/install.sh"
cp "$ROOT_DIR/scripts/enable_gpu_jetson.sh" "$BUILD_DIR/$PKG_NAME/enable_gpu.sh"
chmod +x "$BUILD_DIR/$PKG_NAME/install.sh" \
        "$BUILD_DIR/$PKG_NAME/enable_gpu.sh" \
        "$BUILD_DIR/$PKG_NAME/run_jetson.sh" \
        "$BUILD_DIR/$PKG_NAME/run.sh" \
        "$BUILD_DIR/$PKG_NAME/install-desktop.sh"

# 包内 src 同名脚本保留（便于对照），不删。
# 清理包内 dist 与打包临时产物
rm -rf "$BUILD_DIR/$PKG_NAME/dist"

# ── 2. 打包 + 校验和 ───────────────────────────────────────
ARCHIVE="$DIST_DIR/${PKG_NAME}.tar.gz"
echo "[package-jetson] 生成 $ARCHIVE"
tar -C "$BUILD_DIR" -czf "$ARCHIVE" "$PKG_NAME"

cd "$DIST_DIR"
sha256sum "${PKG_NAME}.tar.gz" > SHA256SUMS

echo
echo "[package-jetson] 完成，产物列表："
ls -lh "$DIST_DIR"
echo
echo "上传到 GitHub Release 时请附带 SHA256SUMS。"
echo "用户侧使用：解压 → bash install.sh（CPU 默认）→ bash run_jetson.sh；"
echo "GPU 加速：bash enable_gpu.sh（加载 ONNX(CUDA)/TRT 依赖）。"