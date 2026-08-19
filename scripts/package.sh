#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# DuAD 上位机 Linux x64 打包脚本
#
# 产物（输出到 dist/）：
#   DuAD_<版本>_Linux_x64_Installer.tar.gz   应用 + 安装脚本（不含任何 Python 依赖）
#                                            用户运行 setup_env.sh 自动装 CPU/GPU 依赖
#   DuAD_<版本>_Linux_x64_CPU-Portable.tar.zst
#                                            应用 + 内置 CPU 推理 venv，解压即用
#                                            （不打包 NVIDIA/CUDA/TensorRT 系统库）
#   SHA256SUMS
#
# 用法：
#   bash scripts/package.sh [版本号]
#   DUAD_SKIP_VENV=1 bash scripts/package.sh   # 只打 Installer 包（离线/快速验证）
#
# 说明：
#   1. GPU 系统动态库（nvidia-*-cu13、tensorrt_libs 等约 6GB）不打包；
#      有 NVIDIA 驱动的用户运行 setup_env.sh 时自动从 PyPI 安装 GPU 依赖。
#   2. CPU-Portable 包用全新 venv 构建，确保不混入开发机 GPU 依赖。
# ============================================================

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
BUILD_DIR="$DIST_DIR/staging"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SKIP_VENV="${DUAD_SKIP_VENV:-0}"

if [ -n "${1:-}" ]; then
    VERSION="$1"
else
    VERSION="$(git -C "$ROOT_DIR" describe --tags --always --dirty 2>/dev/null || echo "0.1.0")"
fi
VERSION="${VERSION#v}"
VERSION="${VERSION//\//_}"
PKG_NAME="DuAD_${VERSION}_Linux_x64"

echo "[package] 项目目录: $ROOT_DIR"
echo "[package] 版本: $VERSION"
echo "[package] 打包目录: $BUILD_DIR"
echo "[package] Python: $($PYTHON_BIN --version)"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "[ERROR] 找不到 $PYTHON_BIN"
    exit 1
fi

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

# ── 1. 拷贝应用文件（排除 .git、开发机 venv、大模型/日志/视频）────────
echo "[package] 拷贝应用文件..."
mkdir -p "$BUILD_DIR/$PKG_NAME"
tar -C "$ROOT_DIR" \
    --exclude='.git' \
    --exclude='dist' \
    --exclude='DuAD_SoftwareContent/pyqml' \
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

# 安装包内不再需要打包脚本自身生成的 dist 目录
rm -rf "$BUILD_DIR/$PKG_NAME/dist"

# ── 2. 先打 Installer 包（不含 venv）──────────────────────────────
cd "$BUILD_DIR"
ARCHIVE_INSTALLER="$DIST_DIR/${PKG_NAME}_Installer.tar.gz"
echo "[package] 生成 Installer 包: $ARCHIVE_INSTALLER"
tar -czf "$ARCHIVE_INSTALLER" "$PKG_NAME"

# ── 3. 构建 CPU venv（可选）──────────────────────────────────────
if [ "$SKIP_VENV" = "0" ]; then
    VENV_DIR="$BUILD_DIR/$PKG_NAME/DuAD_SoftwareContent/pyqml"
    echo "[package] 创建 CPU 虚拟环境: $VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"

    echo "[package] 安装 CPU 推理依赖（PySide6 + onnxruntime CPU）..."
    "$VENV_DIR/bin/python" -m pip install --upgrade pip
    "$VENV_DIR/bin/python" -m pip install -r "$ROOT_DIR/requirements-cpu.txt"

    echo "[package] 清理 venv 缓存..."
    find "$VENV_DIR" -type d -name '__pycache__' -prune -exec rm -rf '{}' +
    "$VENV_DIR/bin/python" -m pip cache purge >/dev/null 2>&1 || true

    ARCHIVE_CPU="$DIST_DIR/${PKG_NAME}_CPU-Portable.tar.zst"
    echo "[package] 生成 CPU-Portable 包: $ARCHIVE_CPU"
    if command -v zstd >/dev/null 2>&1; then
        tar -cf - "$PKG_NAME" | zstd -T0 -3 -o "$ARCHIVE_CPU"
    else
        echo "[WARN] 未找到 zstd，改用 gzip（体积会更大）"
        ARCHIVE_CPU="$DIST_DIR/${PKG_NAME}_CPU-Portable.tar.gz"
        tar -czf "$ARCHIVE_CPU" "$PKG_NAME"
    fi
else
    echo "[package] DUAD_SKIP_VENV=1，跳过 CPU-Portable 包"
fi

# ── 4. 校验和 ───────────────────────────────────────────────────
cd "$DIST_DIR"
sha256sum "${PKG_NAME}"_*.tar.* > SHA256SUMS

echo
echo "[package] 完成，产物列表："
ls -lh "$DIST_DIR"
echo
echo "上传到 GitHub Release 时请附带 SHA256SUMS。"
