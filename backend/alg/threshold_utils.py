"""
阈值计算工具（纯 numpy，无 torch / sklearn 依赖）。

训练端（backend/alg/main.py，有 torch）与标定端
（backend/alg/deploy/calibrate_threshold.py，无 torch）共用同一套逻辑，
保证「训练时算出的部署阈值」与「标定脚本算出的阈值」完全同口径。

两个量：
1. 图像级部署阈值 —— 在**原始 patch-max 分数**（未归一化）尺度上网格搜索
   youden / best_f1 / target_fpr / p99，与 ONNX image_scores 同口径。
2. 像素级 F1-max 分割阈值 —— 在**原始 amap**（双线性上采样 + 高斯平滑后、
   未归一化）尺度上求使像素级 F1 最大的二值化阈值，与 ONNX heatmaps
   上采样平滑后的 hm_smooth 同口径。
"""
import numpy as np

_GRID_N = 2000          # 图像级阈值网格点数
_PIXEL_GRID_N = 1000    # 像素级阈值网格点数
_PIXEL_MAX_SAMPLES = 500_000  # 像素级降采样上限（保护内存/耗时）


def percentile_thresholds(normal) -> dict:
    """正常样本高分位（P99 / P99.5，保证误报率 ≈1% / 0.5%）。"""
    normal = np.asarray(normal, dtype=np.float64)
    return {
        "p99": float(np.percentile(normal, 99)),
        "p99_5": float(np.percentile(normal, 99.5)),
    }


def grid_thresholds(normal, abnormal, target_fpr: float = 0.01,
                    grid_n: int = _GRID_N) -> dict:
    """图像级：网格搜索最优阈值（Youden J / Best F1 / 目标 FPR）。

    normal / abnormal: 原始（未归一化）图像级分数一维数组。
    """
    normal = np.asarray(normal, dtype=np.float64)
    abnormal = np.asarray(abnormal, dtype=np.float64)
    all_vals = np.concatenate([normal, abnormal])
    lo, hi = float(all_vals.min()), float(all_vals.max())
    grid = np.linspace(lo, hi, grid_n)

    best_youden, best_youden_t = -1.0, 0.0
    best_f1, best_f1_t = -1.0, 0.0
    best_tpr_at_fpr, best_tpr_at_fpr_t = -1.0, 0.0
    for t in grid:
        tp = float((abnormal > t).mean())
        fp = float((normal > t).mean())
        fn = 1.0 - tp
        youden = tp - fp
        if youden > best_youden:
            best_youden, best_youden_t = youden, float(t)
        f1 = 2 * tp / (2 * tp + fp + fn) if (tp + fp + fn) > 0 else 0.0
        if f1 > best_f1:
            best_f1, best_f1_t = f1, float(t)
        if fp <= target_fpr and tp > best_tpr_at_fpr:
            best_tpr_at_fpr, best_tpr_at_fpr_t = tp, float(t)

    return {
        "youden": best_youden_t,
        "best_f1": best_f1_t,
        f"target_fpr_{target_fpr:g}": best_tpr_at_fpr_t,
        "_youden_j": best_youden,
        "_best_f1": best_f1,
        "_tpr_at_target_fpr": best_tpr_at_fpr,
    }


def evaluate(threshold: float, normal, abnormal) -> dict:
    """给定阈值的回测指标（验证集上）。"""
    normal = np.asarray(normal, dtype=np.float64)
    abnormal = np.asarray(abnormal, dtype=np.float64)
    out = {"normal_fpr": float((normal > threshold).mean())}
    if len(abnormal):
        out["abnormal_tpr"] = float((abnormal > threshold).mean())
    return out


def compute_image_deploy_threshold(normal_scores, abnormal_scores,
                                   target_fpr: float = 0.01,
                                   method: str = "youden",
                                   grid_n: int = _GRID_N) -> dict:
    """图像级部署阈值（打包版，训练端/标定端共用）。

    有缺陷样本：网格搜索 youden / best_f1 / target_fpr；
    无缺陷样本：仅正常样本分位数（p99 兜底）。

    Returns:
        {"recommended": float, "method": str, "thresholds": dict}
    """
    normal = np.asarray(normal_scores, dtype=np.float64)
    abnormal = np.asarray(abnormal_scores, dtype=np.float64)

    if len(abnormal) == 0:
        thresholds = percentile_thresholds(normal)
        recommended = thresholds["p99"]
        method = "p99"
    else:
        grid = grid_thresholds(normal, abnormal, target_fpr, grid_n)
        thresholds = {k: v for k, v in grid.items() if not k.startswith("_")}
        thresholds.update(percentile_thresholds(normal))
        recommended = thresholds[method] if method in thresholds else thresholds["youden"]

    return {
        "recommended": float(recommended),
        "method": method,
        "thresholds": thresholds,
    }


def pixel_f1_max_threshold(amaps, masks_gt, grid_n: int = _PIXEL_GRID_N,
                           max_samples: int = _PIXEL_MAX_SAMPLES) -> dict:
    """像素级 F1-max 分割阈值（原始 amap 尺度，未归一化）。

    amaps:    [N, H, W] 异常分数图（双线性上采样 + 高斯平滑后）
    masks_gt: [N, H, W] 二值 gt 掩码
    返回 {"threshold": float, "f1": float}；f1=-1 表示无法定义（无正/负样本）。

    为避免对上千万像素逐点网格搜索过慢，先均匀降采样到 max_samples，
    再在 [min, max] 上网格搜索使像素级 F1 最大的阈值。
    """
    amaps = np.asarray(amaps, dtype=np.float64)
    masks_gt = np.asarray(masks_gt, dtype=np.int64)
    # 统一按 batch 维展平（amaps/gt 可能带单 channel 维 [N,1,H,W] 或不带 [N,H,W]），
    # 保证像素一一对应（batch 数一致，来自同一 dataloader）。
    flat_scores = amaps.reshape(amaps.shape[0], -1).ravel()
    flat_gt = masks_gt.reshape(masks_gt.shape[0], -1).ravel()

    n = flat_scores.size
    if n > max_samples:
        idx = np.linspace(0, n - 1, max_samples).astype(np.int64)
        flat_scores = flat_scores[idx]
        flat_gt = flat_gt[idx]

    gt_pos = flat_gt.astype(bool)
    n_pos = int(gt_pos.sum())
    n_neg = int((~gt_pos).sum())
    if n_pos == 0 or n_neg == 0:
        return {"threshold": float(np.percentile(flat_scores, 99)), "f1": -1.0}

    lo, hi = float(flat_scores.min()), float(flat_scores.max())
    grid = np.linspace(lo, hi, grid_n)

    best_f1, best_t = -1.0, float(lo)
    for t in grid:
        pred = flat_scores > t
        tp = int((pred & gt_pos).sum())
        fp = int((pred & ~gt_pos).sum())
        fn = n_pos - tp
        f1 = 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) > 0 else 0.0
        if f1 > best_f1:
            best_f1, best_t = f1, float(t)

    return {"threshold": best_t, "f1": best_f1}
