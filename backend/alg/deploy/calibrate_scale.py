#!/usr/bin/env python3
"""
热力图固定显示尺度标定（客户端工具，无 torch）。

用验证集的正常样本（good）统计 hm_smooth 的全局像素分布，
产出每类别的固定 vmin/vmax —— 热力图不再逐图百分位归一化，
而是所有图共用同一色阶：正常图像素落在色阶中低段（深紫），
异常缺陷像素超出 vmax 被 clip 到亮黄。

用法:
    python calibrate_scale.py bottle_k4_s0_full.onnx \
        --data /path/to/mvtec_anomaly_detection --category bottle
    python calibrate_scale.py --all \
        --data /path/to/mvtec_anomaly_detection \
        --model-dir /path/to/model_onnx

产出: <model>.scale.json → {"vmin", "vmax", "n_images", "method"}
上位机 AlgorithmBridge 加载模型时读取，推理传固定 vmin/vmax；
文件不存在时回退逐图百分位归一化（旧行为）。
"""
import argparse
import glob
import json
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from alg.deploy.onnx_infer import ONNXAnomalyDetector

_P_LOW = 2      # vmin = 正常像素分布的 P2
_P_HIGH = 99.9  # vmax = 正常像素分布的 P99.9（正常图仅 ~0.1% 像素偏黄、视觉干净；
                # 尾部分布长（max 常被标签/噪声 patch 拉高），用 max 会把正常图
                # 的高分区域也压进色阶、缺陷黄区变弱；P99.9 是折中）


def collect_good_pixels(detector, good_dir, max_images=None):
    """对 good 目录所有图推理，返回全部 hm_smooth 像素值拼接数组。"""
    files = sorted(glob.glob(os.path.join(good_dir, "*.png"))
                   + glob.glob(os.path.join(good_dir, "*.jpg"))
                   + glob.glob(os.path.join(good_dir, "*.bmp")))
    if max_images:
        files = files[:max_images]
    parts = []
    for f in files:
        img = np.asarray(Image.open(f).convert("RGB"))
        detector.predict(img)
        hm = detector.last_hm_smooth
        if hm is not None:
            parts.append(hm.ravel())
    if not parts:
        return None, 0
    return np.concatenate(parts), len(files)


def calibrate(model_path: str, data_root: str, category: str, out_dir: str = None) -> dict:
    good_dir = os.path.join(data_root, category, "test", "good")
    if not os.path.isdir(good_dir):
        sys.exit(f"找不到 good 目录: {good_dir}")
    detector = ONNXAnomalyDetector(model_path)
    pixels, n = collect_good_pixels(detector, good_dir)
    if pixels is None:
        sys.exit(f"{category} 无正常样本可统计")
    vmin = float(np.percentile(pixels, _P_LOW))
    vmax = float(np.percentile(pixels, _P_HIGH))
    if out_dir:
        # 写到指定目录（模型目录只读时用），文件名 = <模型名>.scale.json
        out_path = os.path.join(out_dir, os.path.basename(model_path) + ".scale.json")
        os.makedirs(out_dir, exist_ok=True)
    else:
        out_path = model_path + ".scale.json"
    payload = {
        "model": os.path.basename(model_path),
        "category": category,
        "vmin": vmin,
        "vmax": vmax,
        "percentiles": [_P_LOW, _P_HIGH],
        "n_images": n,
        "method": f"good_p{_P_LOW}_p{_P_HIGH}",
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[{category}] vmin={vmin:.3f} vmax={vmax:.3f} ({n} 张 good) → {out_path}")
    return payload


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("model", nargs="?", help="ONNX 模型路径（单类别模式）")
    ap.add_argument("--data", required=True, help="数据集根目录（含 <category>/test/good）")
    ap.add_argument("--category", help="类别名（单类别模式）")
    ap.add_argument("--model-dir", help="模型目录（--all 批量模式，找 <cat>_k4_s0_full.onnx）")
    ap.add_argument("--out-dir", help="scale.json 输出目录（默认模型旁；模型目录只读时指定）")
    ap.add_argument("--all", action="store_true", help="批量标定数据目录下所有类别")
    ap.add_argument("--max-images", type=int, default=None, help="每类最多统计多少张（调试）")
    args = ap.parse_args()

    if args.all:
        if not args.model_dir:
            sys.exit("--all 需要 --model-dir")
        cats = sorted(d for d in os.listdir(args.data)
                      if os.path.isdir(os.path.join(args.data, d, "test", "good")))
        for cat in cats:
            mp = os.path.join(args.model_dir, f"{cat}_k4_s0_full.onnx")
            if not os.path.exists(mp):
                print(f"[SKIP] {cat}: 模型不存在 {mp}")
                continue
            try:
                calibrate(mp, args.data, cat, args.out_dir)
            except Exception as e:
                print(f"[ERROR] {cat}: {e}")
        return

    if not args.model or not args.category:
        sys.exit("单类别模式需要 model + --category；批量用 --all --model-dir")
    calibrate(args.model, args.data, args.category, args.out_dir)


if __name__ == "__main__":
    main()
