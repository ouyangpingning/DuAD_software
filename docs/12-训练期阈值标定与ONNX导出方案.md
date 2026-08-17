# 12 训练期阈值标定 + ONNX Metadata 导出方案

> 目标：把「部署时用验证集标定阈值」前移到**训练期**，阈值/F1-max 随 ckpt 持久化，
> 导出 ONNX 时写入模型 metadata，部署端（上位机）直接从模型文件读取，无需再拿验证集。
>
> 分工：**服务器端**改训练/导出代码（本文第 2 节）；**本地端**只改推理读取（已改好，第 3 节）。
> 两端靠第 1 节的 metadata key 契约对齐。

---

## 1. ONNX metadata 契约（两端对齐的唯一接口）

导出时把阈值写进 ONNX `metadata_props`，本地 `onnx_infer.py` 按这些 key 读取。
**key 名必须完全一致**：

| key | 类型 | 含义 |
|---|---|---|
| `duad.image_threshold` | str(float) | 图像级部署阈值（原始 patch-max 尺度） |
| `duad.image_threshold_method` | str | `"youden"` / `"best_f1"` / `"p99"` |
| `duad.pixel_threshold` | str(float) | 像素级 F1-max 分割阈值（原始 amap 尺度） |
| `duad.pixel_f1_max` | str(float) | 像素级最优 F1 值 |
| `duad.category` | str | 类别名（如 `bottle`） |
| `duad.calibrated_at` | str | 标定时间 ISO 字符串 |
| `duad.deploy` | str(JSON) | 完整 deploy dict（含 image_thresholds 阈值表，诊断/扩展用） |

本地读取优先级：**ONNX metadata > `*.threshold.json` > 默认值 1.7**。

---

## 2. 服务器端改动（5 个文件）

> 服务器 `backend/alg/` 的 `main.py` / `DuAD.py` / `export_onnx.py` 是训练/导出入口。
> 改动后训练产物 ckpt 自带 `deploy` 字段，导出产物 onnx 自带 metadata。

### 2.1 新增 `threshold_utils.py`（核心，纯 numpy，无 torch/sklearn 依赖）

放在 `backend/alg/threshold_utils.py`（与 `main.py` 同级），训练端与 `calibrate_threshold.py` 共用：

```python
"""
阈值计算工具（纯 numpy，无 torch / sklearn 依赖）。
训练端（main.py）与标定端（deploy/calibrate_threshold.py）共用同一套逻辑，
保证「训练时算出的部署阈值」与「标定脚本算出的阈值」完全同口径。

两个量：
1. 图像级部署阈值 —— 原始 patch-max 分数尺度（与 ONNX image_scores 同口径）。
2. 像素级 F1-max 分割阈值 —— 原始 amap 尺度（上采样+高斯平滑后、未归一化，
   与 ONNX heatmaps 上采样平滑后的 hm_smooth 同口径）。
"""
import numpy as np

_GRID_N = 2000          # 图像级阈值网格点数
_PIXEL_GRID_N = 1000    # 像素级阈值网格点数
_PIXEL_MAX_SAMPLES = 500_000  # 像素级降采样上限（保护内存/耗时）


def percentile_thresholds(normal) -> dict:
    """正常样本高分位（P99 / P99.5）。"""
    normal = np.asarray(normal, dtype=np.float64)
    return {
        "p99": float(np.percentile(normal, 99)),
        "p99_5": float(np.percentile(normal, 99.5)),
    }


def grid_thresholds(normal, abnormal, target_fpr: float = 0.01,
                    grid_n: int = _GRID_N) -> dict:
    """图像级：网格搜索 youden / best_f1 / target_fpr。"""
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
    """给定阈值的回测指标。"""
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
    """图像级部署阈值（打包版）。无缺陷样本回退 p99。"""
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

    return {"recommended": float(recommended), "method": method, "thresholds": thresholds}


def pixel_f1_max_threshold(amaps, masks_gt, grid_n: int = _PIXEL_GRID_N,
                           max_samples: int = _PIXEL_MAX_SAMPLES) -> dict:
    """像素级 F1-max 分割阈值（原始 amap 尺度）。

    amaps: [N,H,W] 或 [N,1,H,W] 异常分数图；masks_gt: 同 shape 二值 gt。
    返回 {"threshold": float, "f1": float}；f1=-1 表示无法定义（无正/负样本）。
    """
    amaps = np.asarray(amaps, dtype=np.float64)
    masks_gt = np.asarray(masks_gt, dtype=np.int64)
    # 按 batch 维展平，兼容带/不带单 channel 维
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
```

### 2.2 `DuAD.py`：save / load 持久化 `deploy` 字段

`DINOv2AnomalyDetector.__init__` 里，`self._pca_component = None` 之后加一行：

```python
        self._deploy = None  # checkpoint 里的部署阈值（训练时标定，导出 ONNX 时写 metadata）
```

`save()` 加参数并把 deploy 写进 state（注意 `deploy` 是新增关键字参数，**不影响旧调用**）：

```python
    def save(self, path: str, epoch: int = 0, scores: dict = None, deploy: dict = None):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        state = {
            'proj_state': self.projection.state_dict(),
            'dsc_state': self.discriminator.state_dict(),
            'agg_state': self.feature_extractor.aggregator.state_dict(),
            'epoch': epoch,
            'scores': scores,
        }
        if deploy is not None:
            state['deploy'] = deploy
        # ... 后面 PCA SVD 部分、torch.save 保持不变
```

`load()` 读回并暴露（把原来返回 tuple 的第 3 个 `None` 改成 `self._deploy`）：

```python
        self._pca_mean = state.get('pca_mean')
        self._pca_component = state.get('pca_component')
        self._deploy = state.get('deploy')      # ← 新增
        self.trainer = None
        self.predictor = None
        self.logger.info(f"Checkpoint loaded from {path}")
        return state.get('epoch', 0), state.get('scores', None), self._deploy, -1
```

### 2.3 `main.py`：训练最终评估时算阈值写回 ckpt

顶部 import 加两行：

```python
from datetime import datetime
from threshold_utils import compute_image_deploy_threshold, pixel_f1_max_threshold, evaluate
```

在 `train_category` 里，`best_score_full = {...}` 之后、`# 训练完成总结` 之前，插入：

```python
    # === 部署阈值标定（训练时就地算，随 ckpt 持久化，部署端免再拿验证集标定）===
    # scores 是 predict 返回的原始 patch-max 分数（未归一化），与 ONNX image_scores
    # 同口径；masks 是上采样+高斯平滑后的原始 amap，与 ONNX hm_smooth 同口径。
    normal_scores = [float(s) for s, l in zip(scores, labels_gt) if int(l) == 0]
    abnormal_scores = [float(s) for s, l in zip(scores, labels_gt) if int(l) == 1]
    logger.info(f"Deploy threshold calibration: {len(normal_scores)} normal, "
                f"{len(abnormal_scores)} abnormal")

    image_thr = compute_image_deploy_threshold(normal_scores, abnormal_scores,
                                               method="youden")
    pixel_thr = pixel_f1_max_threshold(masks, masks_gt) if len(masks) else \
        {"threshold": -1.0, "f1": -1.0}

    deploy = {
        "category": atype,
        "image_threshold": image_thr["recommended"],
        "image_threshold_method": image_thr["method"],
        "image_thresholds": image_thr["thresholds"],
        "pixel_threshold": pixel_thr["threshold"],
        "pixel_f1_max": pixel_thr["f1"],
        "calibrated_at": datetime.now().isoformat(timespec="seconds"),
    }

    backtest = evaluate(deploy["image_threshold"], normal_scores, abnormal_scores)
    logger.info(f"  Image deploy threshold (youden): {deploy['image_threshold']:.4f}")
    logger.info(f"    backtest: {backtest}")
    logger.info(f"  Pixel F1-max threshold: {pixel_thr['threshold']:.4f} "
                f"(F1={pixel_thr['f1']:.4f})")

    # 把阈值写回 best checkpoint（覆盖训练循环里 save 的无阈值版本）
    model.save(best_ckpt_path, epoch=best_epoch, scores=best_score_full, deploy=deploy)
    logger.info(f"Deploy thresholds embedded into checkpoint: {best_ckpt_path}")
```

> 要点：这里用的是 `model.predict()` 返回的**原始 scores / masks**（不是 `evaluate()` 里
> 归一化后的值），所以阈值落在部署端 `onnx_infer.predict` 返回的分数同一尺度上。

### 2.4 `export_onnx.py`：导出后写 metadata

顶部 import 加 `import json`。

在 `export_onnx()` 函数前新增函数：

```python
def _write_deploy_metadata(onnx_path: str, deploy: dict):
    """把训练时标定的部署阈值写入 ONNX metadata_props（模型自包含）。"""
    if not deploy:
        return
    try:
        import onnx
        from onnx import StringStringEntryProto
    except ImportError:
        print("[WARN] onnx package not available, skip writing deploy metadata")
        return

    model = onnx.load(onnx_path)
    props = {mp.key: mp.value for mp in model.metadata_props}

    def put(k, v):
        if v is not None:
            props[k] = str(v)

    put("duad.category", deploy.get("category"))
    put("duad.image_threshold", deploy.get("image_threshold"))
    put("duad.image_threshold_method", deploy.get("image_threshold_method"))
    put("duad.pixel_threshold", deploy.get("pixel_threshold"))
    put("duad.pixel_f1_max", deploy.get("pixel_f1_max"))
    put("duad.calibrated_at", deploy.get("calibrated_at"))
    props["duad.deploy"] = json.dumps(deploy, ensure_ascii=False)

    del model.metadata_props[:]
    for k, v in props.items():
        model.metadata_props.append(StringStringEntryProto(key=k, value=v))
    onnx.save(model, onnx_path)
    print(f"[INFO] Deploy thresholds written to ONNX metadata: "
          f"image={deploy.get('image_threshold')}, "
          f"pixel={deploy.get('pixel_threshold')}")
```

在 `export_onnx()` 里 `torch.onnx.export(...)` 结束、`print("[OK] ...")` 之前加：

```python
    # 训练时标定的部署阈值随模型写入 metadata（旧 ckpt 无 deploy 字段则跳过）
    _write_deploy_metadata(onnx_path, getattr(detector, "_deploy", None))
```

> 说明：`export_onnx()` 内已有 `detector`（`_build_detector_and_onnx_model` 返回，
> 其 `detector.load(ckpt_path)` 已把 ckpt 的 `deploy` 读进 `detector._deploy`）。

### 2.5（可选）`calibrate_threshold.py` 复用 threshold_utils

不是必须，但建议做，保证「标定脚本」与「训练期标定」永远同口径。把脚本里本地的
`grid_thresholds / percentile_thresholds / evaluate` 三个函数删掉，改为：

```python
from alg.threshold_utils import (
    grid_thresholds, percentile_thresholds, evaluate, _GRID_N,
)
```

其余逻辑不变（脚本仍可用，作为旧模型/工厂现场只有好品的兜底标定工具）。

---

## 3. 本地端改动（已完成，2 个文件）

### 3.1 `backend/alg/deploy/onnx_infer.py`

`__init__` 建 session 后，读 metadata 暴露阈值：

```python
        self.deploy = None
        self.image_threshold = None   # 图像级部署阈值
        self.pixel_threshold = None   # 像素级 F1-max 分割阈值
        self.pixel_f1_max = None
        self._load_deploy_metadata()
```

`_load_deploy_metadata()` 用 `session.get_modelmeta().custom_metadata_map` 读
`duad.image_threshold` / `duad.pixel_threshold` / `duad.pixel_f1_max` / `duad.deploy`。

### 3.2 `backend/Src/algorithm_bridge.py`

阈值读取优先级改为 **metadata > json > 默认 1.7**：`_get_detector()` 建 session 后调用
`_sync_threshold_from_metadata()`，若 `detector.image_threshold` 非 None 则覆盖 json 值并
`thresholdChanged.emit()`。旧模型（无 metadata）保持 json/默认值不动。

---

## 4. 兼容性

- **旧 ckpt（无 deploy 字段）**：`export_onnx` 的 `getattr(detector, "_deploy", None)` 为 None，
  跳过写 metadata，onnx 里没有 `duad.*` key → 本地走 `*.threshold.json` 兜底，行为不变。
- **无缺陷样本（工厂现场）**：`compute_image_deploy_threshold` 回退 p99；像素阈值 f1=-1。
- **`calibrate_threshold.py` 脚本保留**：与训练端同口径，可用于旧模型补标定。

## 5. 服务器端自测

```bash
# 1. 训练（跑完后看日志里的 "Deploy thresholds embedded into checkpoint"）
python backend/alg/main.py --categories bottle

# 2. 导出（跑完后看 "[INFO] Deploy thresholds written to ONNX metadata"）
python backend/alg/deploy/export_onnx.py --category bottle

# 3. 验证 metadata 已写入
python -c "
import onnxruntime as ort
m = ort.InferenceSession('model_onnx/bottle_full.onnx',
                         providers=['CPUExecutionProvider']).get_modelmeta()
print(m.custom_metadata_map.get('duad.image_threshold'))
print(m.custom_metadata_map.get('duad.pixel_threshold'))
"
```

拿到带 metadata 的 onnx 后，放回本地，上位机加载即自动用训练期阈值，无需再跑
`calibrate_threshold.py`。
