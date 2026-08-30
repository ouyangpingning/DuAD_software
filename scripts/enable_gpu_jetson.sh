#!/usr/bin/env bash
# ============================================================
# DuAD 上位机 Jetson —— 可选 GPU 加速启用脚本
# 加载 onnxruntime 的 ONNX(CUDA)/TensorRT 依赖（cu12 运行库补丁）
#
# 用法：bash enable_gpu.sh     （默认 CPU 安装完成后运行）
#
# 原理：
#   aarch64 上唯一的 ORT 是 NVIDIA Jetson 索引的 onnxruntime_gpu 1.24.0
#   （为 CUDA 12.6 编译）。JetPack 7.2 是 CUDA 13，官方无 jp7/cu13 包，
#   故用 cu12 pip 运行库“拼装”：装好后 CUDA/TRT EP 才能初始化。
#   反之不装这些库 → EP 初始化失败 → onnxruntime 自动回退 CPU。
#
#   依赖注入：main.py 启动时会自动 glob site-packages/nvidia/*/lib
#   并注入 LD_LIBRARY_PATH 后重启进程，无需手动设环境变量。
#
# 前提：JetPack ≥ 7.2（TRT 10.13 数值正确）。
#   JetPack 6.2 的 TRT 10.3 对 DINOv2 数值错误（分数恒 1e10），
#   若要留在 6.2：请把 run_jetson.sh 里的 export DUAD_PREFER_TRT=1 注释掉。
# ============================================================
set -euo pipefail

MM_BIN="$HOME/micromamba/bin/micromamba"
ENV_DIR="$HOME/micromamba/envs/duad"

if [ ! -x "$ENV_DIR/bin/python" ]; then
    echo "[gpu] 未找到环境 $ENV_DIR，请先运行 install.sh" >&2
    exit 1
fi

# 可选的 PyPI 镜像（国内网络加速）：PIP_INDEX 环境变量，如
#   PIP_INDEX=-i https://pypi.tuna.tsinghua.edu.cn/simple bash enable_gpu.sh
PIP_INDEX="${PIP_INDEX:-}"

echo "[gpu] 安装 cu12 运行库（CUDA/TRT EP 依赖）..."
# shellcheck disable=SC2086
"$MM_BIN" run -p "$ENV_DIR" pip install $PIP_INDEX \
    nvidia-cuda-runtime-cu12 nvidia-cublas-cu12 nvidia-cudnn-cu12 \
    nvidia-cufft-cu12 nvidia-curand-cu12 nvidia-nvrtc-cu12

echo "[gpu] 验证可用的 inference provider："
"$MM_BIN" run -p "$ENV_DIR" python -c "import onnxruntime as ort; print('  ', ort.get_available_providers())"

echo
echo "═══════════════════════════════════════════════════════"
echo " GPU 依赖已加载。预期 provider 输出："
echo "   ['TensorrtExecutionProvider', 'CUDAExecutionProvider', 'CPUExecutionProvider']"
echo "   （run_jetson.sh 已默认 DUAD_PREFER_TRT=1 优先 TRT）"
echo " 如果仍只有 ['CPUExecutionProvider']："
echo "   1) 确认已联网且 pip 装上了 nvidia-*-cu12 库"
echo "   2) 确认 JetPack ≥ 7.2（TRT 10.13+，数值正确）"
echo "═══════════════════════════════════════════════════════"