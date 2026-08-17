#!/usr/bin/env python3
"""
阈值标定脚本（无 torch / 无 matplotlib，与上位机同一条 onnx 推理管线）。

用验证集（MVTec 布局：<root>/<category>/test/good = 正常，
其余子目录 = 缺陷类）统计 image_score 分布并确定部署阈值：

方法 1（有缺陷样本）：网格搜索 → youden / best_f1 / target_fpr
方法 2（正常样本）：P99 / P99.5 分位

结果写入 <model>.threshold.json，上位机启动/切模型时自动读取为默认阈值。

用法:
    python calibrate_threshold.py bottle_k4_s0_full.onnx \
        --data /path/to/mvtec_anomaly_detection --category bottle
    # 工厂现场只有好品样本：
    python calibrate_threshold.py bottle_k4_s0_full.onnx \
        --data /path/to/mvtec_anomaly_detection --category bottle --normal-only

注意：分数口径 = onnx_infer.predict 返回的原始 image_score（patch max，
未归一化），与界面"异常阈值"一致。
"""
import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

# 让 backend/ 下的模块可导入（脚本位于 backend/alg/deploy/ 时自动生效）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from alg.deploy.onnx_infer import ONNXAnomalyDetector
from alg.threshold_utils import (
    grid_thresholds, percentile_thresholds, evaluate, _GRID_N,
)

_GRID_N = _GRID_N  # 阈值网格点数（与训练端 threshold_utils 同口径）


def collect_scores(detector: ONNXAnomalyDetector, image_dir: str,
                   max_samples: int = None) -> list:
    """对目录下所有图片推理，返回 image_score 列表。"""
    files = sorted(glob.glob(os.path.join(image_dir, "*.png"))
                   + glob.glob(os.path.join(image_dir, "*.jpg"))
                   + glob.glob(os.path.join(image_dir, "*.bmp")))
    if max_samples:
        files = files[:max_samples]
    scores = []
    for f in files:
        img = np.asarray(Image.open(f).convert("RGB"))
        _, score = detector.predict(img)
        scores.append(score)
    return scores


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("model", help="ONNX 模型路径")
    ap.add_argument("--data", required=True, help="数据集根目录（含 <category>/test/）")
    ap.add_argument("--category", required=True, help="类别目录名（如 bottle）")
    ap.add_argument("--target-fpr", type=float, default=0.01,
                    help="目标误报率（默认 0.01 = 正常样本最多误报 1%%）")
    ap.add_argument("--normal-only", action="store_true",
                    help="只用正常样本（工厂现场无缺陷样本），仅输出分位数")
    ap.add_argument("--max-samples", type=int, default=None,
                    help="每类最多取多少张图（调试用）")
    args = ap.parse_args()

    test_dir = os.path.join(args.data, args.category, "test")
    if not os.path.isdir(test_dir):
        sys.exit(f"找不到测试集目录: {test_dir}")

    detector = ONNXAnomalyDetector(args.model)
    print(f"providers: {detector.session.get_providers()}")
    print(f"类别: {args.category}  测试集: {test_dir}\n")

    # ── 收集分数 ──
    normal_scores = collect_scores(detector, os.path.join(test_dir, "good"),
                                   args.max_samples)
    print(f"正常样本: {len(normal_scores)} 张")

    abnormal_scores = []
    abnormal_cats = []
    for sub in sorted(os.listdir(test_dir)):
        if sub == "good":
            continue
        sub_dir = os.path.join(test_dir, sub)
        if not os.path.isdir(sub_dir):
            continue
        sc = collect_scores(detector, sub_dir, args.max_samples)
        if sc:
            abnormal_scores += sc
            abnormal_cats.append(sub)
    print(f"缺陷样本: {len(abnormal_scores)} 张（类别: {', '.join(abnormal_cats) or '无'}）")

    if not normal_scores:
        sys.exit("正常样本为空，无法标定")
    normal = np.asarray(normal_scores, dtype=np.float64)

    # ── 分布统计 ──
    def stats(name, arr):
        if len(arr) == 0:
            print(f"  {name}: 无样本")
            return None
        print(f"  {name}: n={len(arr)} min={arr.min():.3f} "
              f"max={arr.max():.3f} mean={arr.mean():.3f}")
        return {"n": int(len(arr)), "min": float(arr.min()),
                "max": float(arr.max()), "mean": float(arr.mean())}

    print("\n分数分布:")
    normal_stats = stats("正常", normal)
    abnormal = np.asarray(abnormal_scores, dtype=np.float64) if abnormal_scores else np.array([])
    abnormal_stats = stats("异常", abnormal)

    # ── 标定 ──
    thresholds = {}
    recommended = None
    if args.normal_only or len(abnormal) == 0:
        thresholds.update(percentile_thresholds(normal))
        recommended = thresholds["p99"]
        print("\n仅正常样本标定（--normal-only），推荐阈值 = P99")
    else:
        grid = grid_thresholds(normal, abnormal, args.target_fpr)
        thresholds.update({k: v for k, v in grid.items() if not k.startswith("_")})
        thresholds.update(percentile_thresholds(normal))
        recommended = grid["youden"]
        print(f"\n网格搜索({_GRID_N} 点):")
        print(f"  Youden J(TPR-FPR 最大): {grid['youden']:.3f} "
              f"(J={grid['_youden_j']:.3f})")
        print(f"  Best F1:                {grid['best_f1']:.3f} "
              f"(F1={grid['_best_f1']:.3f})")
        print(f"  目标FPR≤{args.target_fpr:g}:            {grid[f'target_fpr_{args.target_fpr:g}']:.3f} "
              f"(TPR={grid['_tpr_at_target_fpr']:.3f})")
        print(f"  P99 / P99.5 分位:       "
              f"{thresholds['p99']:.3f} / {thresholds['p99_5']:.3f}")
        print(f"\n推荐阈值（Youden）: {recommended:.3f}")

    # ── 回测 ──
    backtest = evaluate(recommended, normal, abnormal) if len(abnormal) else \
        {"normal_fpr": float((normal > recommended).mean())}
    print(f"推荐阈值回测: {backtest}")

    # ── 写 JSON ──
    out_path = args.model + ".threshold.json"
    payload = {
        "model": os.path.basename(args.model),
        "category": args.category,
        "calibrated_at": datetime.now().isoformat(timespec="seconds"),
        "recommended": float(recommended),
        "thresholds": {k: float(v) for k, v in thresholds.items()},
        "backtest": backtest,
        "stats": {"normal": normal_stats, "abnormal": abnormal_stats},
        "n_abnormal_categories": len(abnormal_cats),
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n已写入: {out_path}")
    print("上位机启动/切换此模型时会自动读取 recommended 作为默认阈值。")


if __name__ == "__main__":
    t0 = time.time()
    main()
    print(f"\n标定耗时: {time.time() - t0:.1f}s")
