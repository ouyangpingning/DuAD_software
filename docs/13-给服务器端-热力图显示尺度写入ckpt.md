# 给服务器端 AI：热力图固定显示尺度写入 ckpt/metadata

> 客户端（上位机）已实现固定尺度热力图显示（方案 2），但当前 vmin/vmax 是客户端
> 用本地 MVTec 验证集统计的临时值（`backend/model_scales/*.scale.json`）。
> 请服务器端把显示尺度与阈值一起**训练期标定、随模型持久化**，客户端将自动优先使用。

---

## 1. 需要新增的两个量

| 名称 | 含义 | 统计口径 |
|---|---|---|
| `heatmap_vmin` | 热力图显示色阶下界 | good 样本 amap 像素的 **P2** 分位 |
| `heatmap_vmax` | 热力图显示色阶上界 | good 样本 amap 像素的 **P99.9** 分位 |

**关键口径（务必一致，否则客户端热力图色阶错乱）：**

1. 统计对象 = **good（正常）样本**的 amap 像素，**不掺缺陷样本**。色阶只覆盖"正常波动
   范围"，缺陷高分超出 vmax 被 clip 成亮黄（plasma 色带高端）。
2. amap = **上采样（双线性 37→518）+ 高斯平滑（kernel 25 / sigma 4）之后**、
   **含背景 min_fg 填充**的完整图——即 `model.predict()` 返回的 `masks` 元素
   （训练端 `_upsample_masks` 输出），与 ONNX 推理端 `hm_smooth` 同尺度。
3. **用 P2 / P99.9 分位，不要用 min/max**：正常样本尾部分布长（标签区域/噪声 patch
   会把 max 拉高），用 max 会把正常图的高分区域也压进色阶、缺陷黄区变弱。
   （客户端实测 bottle：P99.9=0.59 而 max=1.15；取 P99.9 后正常图亮黄占比 0.1%~0.3%、
   缺陷图 13%~44%，效果正确。）

## 2. ckpt 改动（训练期，`main.py` 最终评估处）

在现有 `deploy` dict（已有 image_threshold / pixel_threshold 等）里追加两个字段：

```python
# 与阈值标定同一处：用 predict 返回的 masks（good 样本）统计
good_amaps = [np.asarray(m) for m, l in zip(masks, labels_gt) if int(l) == 0]
all_pixels = np.concatenate([m.reshape(-1) for m in good_amaps])
deploy = {
    ...  # 现有字段
    "heatmap_vmin": float(np.percentile(all_pixels, 2)),
    "heatmap_vmax": float(np.percentile(all_pixels, 99.9)),
}
```

（可直接复用 `threshold_utils.py`，建议加一个 `heatmap_scale_from_good(masks, labels_gt)`
函数与客户端 `calibrate_scale.py` 同口径。）

## 3. 导出改动（`export_onnx.py`）

`_write_deploy_metadata` 里追加：

```python
put("duad.heatmap_vmin", deploy.get("heatmap_vmin"))
put("duad.heatmap_vmax", deploy.get("heatmap_vmax"))
```

## 4. 契约文档（DEPLOY_CONTRACT.md）

新增两行 metadata key 说明：

| key | 类型 | 含义 |
|---|---|---|
| `duad.heatmap_vmin` | str(float) | 热力图显示色阶下界（good 样本 amap 像素 P2） |
| `duad.heatmap_vmax` | str(float) | 热力图显示色阶上界（good 样本 amap 像素 P99.9） |

## 5. 客户端兼容性（无需服务器端额外处理）

客户端读取优先级已实现：**metadata `duad.heatmap_vmin/vmax` > 本地 `*.scale.json` > 逐图百分位归一化**。

- 新模型带这两个 key → 自动用训练期标定尺度；
- 旧模型无 key → 客户端本地 scale.json 兜底（当前 15 个类别已生成）；
- 两者都无 → 回退旧行为（逐图百分位），无回归风险。
