#!/usr/bin/env python3
"""
GPU 推理验证 + 测速脚本（排查“误删 tensorrt 后推理变慢”用）。

用法（在激活的 pyqml 环境里）：
    python scripts/check_gpu_inference.py [model.onnx] [--frames 30]

行为：
    1. 打印 onnxruntime 版本与 get_available_providers() 列表
       （注意：该列表是编译期能力，不代表库真的可用——以第 3 步为准）
    2. 自动按 onnx_infer.py 的偏好顺序（TensorRT > CUDA > CPU）逐项建 session 测速
    3. 每项 warmup 后计时 N 帧（随机噪声帧，形状 [1,3,518,518]），输出平均耗时
    4. 汇总“上位机实际会走哪个 EP”——TensorRT 缺失时 ORT 静默回退 CUDA/CPU

判定：
    - TensorrtExecutionProvider 出现且耗时最低（~70ms 级）→ 修复完成
    - 出现 "libnvinfer.so.10: cannot open shared object" → tensorrt_cu13_libs 未装
    - 出现 "no CUDA-capable device" → 检查 nvidia-smi / 驱动 / udev 权限
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort

# 与 backend/alg/deploy/onnx_infer.py 保持一致
TARGET_SIZE = 518


def find_default_model():
    """未指定模型时，在仓库内及上级权重目录里找第一个 .onnx。"""
    here = Path(__file__).resolve().parent.parent  # 仓库根
    candidates = [
        here / "backend",
        here.parent,  # 研究生论文/ 下的模型权重保存* 目录
    ]
    for base in candidates:
        if not base.is_dir():
            continue
        hits = sorted(base.glob("**/*.onnx"))
        hits = [h for h in hits if "site-packages" not in str(h)]
        if hits:
            return hits[0]
    return None


def make_session(model, provider, options):
    """按 onnx_infer.py 的 SessionOptions / provider_options 建 session。"""
    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
    so.enable_mem_pattern = False
    so.enable_cpu_mem_arena = False
    if provider == "TensorrtExecutionProvider":
        popts = {"device_id": 0}  # TRT 的 provider_options 必须最简，否则静默回退 CPU
    elif provider == "CUDAExecutionProvider":
        popts = {"device_id": 0, "gpu_mem_limit": 3 * 1024 * 1024 * 1024}
    else:
        popts = {}
    return ort.InferenceSession(model, sess_options=so,
                                providers=[provider], provider_options=[popts])


def bench(session, provider, frames):
    name, inputs = session.get_inputs()[0], session.get_inputs()
    shape = [1] + [d if isinstance(d, int) else TARGET_SIZE for d in inputs[0].shape[1:]]
    frame = np.random.rand(*shape).astype(np.float32)
    feeds = {inputs[0].name: frame}
    # warmup（TRT 首次会构建引擎，~17s，只发生一次）
    t0 = time.time()
    for _ in range(3):
        session.run(None, feeds)
    warm = time.time() - t0
    times = []
    for _ in range(frames):
        t0 = time.time()
        session.run(None, feeds)
        times.append(time.time() - t0)
    times.sort()
    med = times[len(times) // 2]
    print(f"  {provider}: 平均 {sum(times)/len(times)*1000:7.1f} ms"
          f" | 中位 {med*1000:7.1f} ms | warmup3帧 {warm*1000:8.1f} ms")
    return med * 1000


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("model", nargs="?", default=None, help="onnx 模型路径（缺省自动找）")
    ap.add_argument("--frames", type=int, default=20, help="每 EP 计时帧数")
    args = ap.parse_args()

    model = args.model or find_default_model()
    if not model:
        sys.exit("未找到 .onnx 模型，请显式传入路径")
    model = str(model)
    print(f"onnxruntime {ort.__version__}")
    print(f"模型: {model}")
    print(f"编译期 providers: {ort.get_available_providers()}")

    results = {}
    for prov in ("CPUExecutionProvider", "CUDAExecutionProvider",
                 "TensorrtExecutionProvider"):
        try:
            s = make_session(model, prov, None)
            results[prov] = bench(s, prov, args.frames)
            print(f"  -> session 实际使用: {s.get_providers()}")
        except Exception as e:
            msg = str(e).splitlines()[-1][:220]
            print(f"  {prov}: FAILED -> {msg}")

    pref = [p for p in ("TensorrtExecutionProvider", "CUDAExecutionProvider",
                        "CPUExecutionProvider") if p in results]
    if not pref:
        print("\n结论: 所有 provider 都不可用，无法推理")
    else:
        best = min(results, key=results.get)
        print(f"\n上位机实际会走: {pref[0]}")
        print(f"最快 EP: {best} ({results[best]:.1f} ms)")


if __name__ == "__main__":
    main()
