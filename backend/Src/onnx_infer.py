#!/usr/bin/env python3
"""
ONNX 推理模块（无 torch 依赖）— 联调用轻量推理。

加载算法仓库（https://github.com/ouyangpingning/DuAD）的 export_onnx.py 导出的模型（优先 PCA 内联模式）：
    输入  image [B, 3, target_size, target_size] float32（ImageNet 归一化）
    输出  heatmaps [B, 1, target_size, target_size]（上采样+高斯平滑后的 amap）
          + image_scores [B] patch 级最大值

预处理与训练 transform 一致（dataset/mvtec.py::get_transform）：
    Resize(target_size) + CenterCrop(target_size) + Normalize(ImageNet)
后处理（双线性上采样到 target_size + 高斯平滑 sigma=4）已内化在 ONNX 图内，
与 Predictor._upsample_masks 一致；部署端不再重复做，直接使用输出的 heatmaps。

用法：
    det = ONNXAnomalyDetector("model.onnx", target_size=518)
    heatmap_rgb, score = det.predict(image_rgb_np)   # image: [H,W,3] uint8
"""
import json
import os
import platform
import sys

import numpy as np
from PIL import Image


def _ensure_gpu_dll_path():
    """Windows + onnxruntime-gpu：把 GPU 推理所需的 DLL 目录注入 PATH。

    - CUDA 运行库：onnxruntime-gpu 的 provider（onnxruntime_providers_cuda.dll
      等）加载时按 PATH 解析 cublas64_13.dll / cudnn64_9.dll 依赖；若找不到就
      静默回退 CPUExecutionProvider（日志只有 EP Error）。Windows wheel 不自带
      这些运行库，需 pip 装 nvidia-cublas-cu13 + nvidia-cudnn-cu13 +
      nvidia-cuda-runtime（DLL 落在 site-packages/nvidia/）。
    - TensorRT 库（nvinfer_10.dll 等）：无 pip 包（tensorrt wheel 不支持
      cp314、tensorrt_cu13_libs 仅 Linux），需手动从 NVIDIA 下载
      TensorRT 10.16.x Windows zip 解压。
    - 打包（PyInstaller frozen）时 GPU 库不随 exe 打包：从 **exe 同目录**
      的 nvidia/（cuBLAS/cuDNN）与 tensorrt/（TensorRT bin）加载，作为可选
      外部 GPU 库；开发环境走 site-packages/nvidia + TENSORRT_LIB_DIR /
      backend/libs_win_tensorrt/bin。
    必须在 import onnxruntime / 创建 session 前调用（进程级、幂等）。
    """
    if os.name != "nt":
        return
    dirs = []

    def add(d):
        d = os.path.normpath(d)
        if os.path.isdir(d) and d not in dirs:
            dirs.append(d)

    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        nv_roots = [os.path.join(exe_dir, "nvidia")]
    else:
        nv_roots = [os.path.join(sys.prefix, "Lib", "site-packages", "nvidia")]
    for root in nv_roots:
        if os.path.isdir(root):
            for sub in ("cu13", "cudnn"):
                for rel in ("bin/x86_64", "bin"):
                    add(os.path.join(root, sub, *rel.split("/")))

    add(os.environ.get("TENSORRT_LIB_DIR", ""))
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        for trt_root in (os.path.join(exe_dir, "tensorrt"),
                         os.path.join(exe_dir, "trt")):
            if os.path.isdir(trt_root):
                add(os.path.join(trt_root, "bin"))
                add(trt_root)
    else:
        _proj_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))))
        add(os.path.join(_proj_root, "backend", "libs_win_tensorrt", "bin"))

    if not dirs:
        return
    os.environ["PATH"] = ";".join(dirs) + ";" + os.environ.get("PATH", "")


_ensure_gpu_dll_path()

import onnxruntime as ort

_IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

# jet colormap 锚点（与 matplotlib 'jet' 一致），用于把分数映射为 RGB 热力图。
# 语义：低分=深蓝 → 中分=青绿 → 高分=红色（异常区域红色高亮）
_JET_ANCHORS = np.array([
    [0.0, 0.0, 0.5], [0.0, 0.0, 1.0], [0.0, 0.5, 1.0], [0.0, 1.0, 1.0],
    [0.5, 1.0, 0.5], [1.0, 1.0, 0.0], [1.0, 0.5, 0.0], [1.0, 0.0, 0.0],
    [0.5, 0.0, 0.0],
], dtype=np.float32)

# 热力图颜色映射：默认固定显示尺度（vmin/vmax 显式传入时），否则逐图百分位
# 归一化（p2/p98）仅用于显示 —— 契约 3.3。二值化（异常区域定位）必须用原始
# hm_smooth 与 pixel_threshold 比较，发生在归一化之前：百分位空间里的固定
# 阈值跨图不可比（每张图 [0,1] 空间不同）。


def _jet_colormap(values: np.ndarray) -> np.ndarray:
    """values [H,W] float → RGB [H,W,3] uint8（jet 色带）。"""
    t = np.clip(values, 0.0, 1.0) * (len(_JET_ANCHORS) - 1)
    i0 = np.floor(t).astype(np.int64)
    i1 = np.minimum(i0 + 1, len(_JET_ANCHORS) - 1)
    frac = (t - i0)[..., None]
    rgb = _JET_ANCHORS[i0] * (1 - frac) + _JET_ANCHORS[i1] * frac
    return (rgb * 255).astype(np.uint8)


def _gaussian_blur(arr: np.ndarray, sigma: float = 4.0) -> np.ndarray:
    """numpy 分离式高斯模糊。

    Pillow 12 的 GaussianBlur 不支持 float32（mode F）图像，故自实现；
    kernel_size=25, sigma=4 与训练侧 torchvision GaussianBlur 完全一致（契约 1.3）。
    """
    kernel_size = 25
    half = kernel_size // 2
    x = np.arange(kernel_size, dtype=np.float32) - half
    g1d = np.exp(-0.5 * (x / sigma) ** 2)
    g1d /= g1d.sum()

    h, w = arr.shape
    # 水平方向（edge 填充）
    padded = np.pad(arr, half, mode="edge")
    tmp = np.zeros_like(arr)
    for i in range(kernel_size):
        tmp += g1d[i] * padded[i:i + h, half:half + w]
    # 垂直方向
    padded = np.pad(tmp, half, mode="edge")
    out = np.zeros_like(arr)
    for i in range(kernel_size):
        out += g1d[i] * padded[half:half + h, i:i + w]
    return out


class ONNXAnomalyDetector:
    """大恒相机帧 → 异常热力图/分数的 ONNX 推理器（纯 CPU，联调用）。"""

    def __init__(self, onnx_path: str, target_size: int = 518):
        self.target_size = target_size
        # provider 优先级：默认 TensorRT（算子融合，1650 Ti 实测 69ms vs CUDA 283ms）
        # → CUDA → CPU。无 TRT 库/无 GPU 时 get_available_providers 自动过滤。
        # ⚠ Jetson（aarch64）例外：JetPack 6.2 自带的 TRT 10.3 对 DINOv2 类模型
        # 数值错误（实测 fp32 引擎门控误触发→分数恒 1e10、fp16 引擎溢出→65504，
        # trtexec 直接构建同样错误，非 ORT 问题），默认改用 CUDA EP（实测
        # 366ms/帧且数值正确）；设 DUAD_PREFER_TRT=1 可强制 TRT 优先（供调试/
        # 升级 TRT 后验证）。x86 保持 TRT 优先。
        if (os.name != "nt" and platform.machine() in ("aarch64", "arm64")
                and os.environ.get("DUAD_PREFER_TRT") != "1"):
            _provider_pref = ('CUDAExecutionProvider', 'TensorrtExecutionProvider',
                              'CPUExecutionProvider')
        else:
            _provider_pref = ('TensorrtExecutionProvider', 'CUDAExecutionProvider',
                              'CPUExecutionProvider')
        providers = [p for p in _provider_pref if p in ort.get_available_providers()]

        # 运行时 GPU 自动判断：检测到 NVIDIA 驱动、但 onnxruntime 没有
        # CUDA/TensorRT provider（说明装的是 CPU 包或 GPU 依赖缺失），给出
        # 一次性升级提示；用户无驱动时不打扰。
        _gpu_providers = [p for p in providers
                          if p in ('TensorrtExecutionProvider', 'CUDAExecutionProvider')]
        if (not _gpu_providers
                and not getattr(ONNXAnomalyDetector, '_gpu_warned', False)
                and os.path.exists('/proc/driver/nvidia/version')):
            ONNXAnomalyDetector._gpu_warned = True
            print("[WARN] 检测到 NVIDIA 驱动，但当前 onnxruntime 无 GPU provider"
                  "（未安装 onnxruntime-gpu/TensorRT 依赖），将使用 CPU 推理；"
                  "运行 setup_env.sh 安装 GPU 依赖可大幅加速")

        # 内存优化（实测上位机 RSS 构成：session 加载 ~540MB + 首推 ~360MB）：
        # - ORT_ENABLE_BASIC 而非 ALL：ALL 级图优化会复制/变换图产生大量
        #   临时内存（实测可省 100~200MB），对推理速度影响很小
        # - enable_mem_pattern=False：不预分配中间 tensor 内存模式
        # - gpu_mem_limit=3GB：限制 CUDA 显存 arena 膨胀（4GB 卡上曾观察到
        #   膨胀到 3.7GB 导致其他进程开 CUDA session 直接 OOM）
        # 注意：TensorRT 的 provider_options 必须最简（{'device_id': 0}）——
        # ORT 1.28 + TRT 10.16 组合下带 trt_engine_cache_*/trt_max_workspace_size
        # 选项会静默加载失败回退 CPU；引擎构建由 warmup 在后台完成（首次 ~17s）。
        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
        so.enable_mem_pattern = False
        # 禁用 CPU 内存 arena：session 析构后内存立即归还 OS。默认启用时 arena
        # 大块内存释放后不归还（glibc 碎片残留），上位机切换多个 124MB 模型时
        # RSS 会累积膨胀；单图推理场景的分配开销可忽略。
        so.enable_cpu_mem_arena = False
        provider_options = []
        _is_aarch64 = platform.machine() in ("aarch64", "arm64")
        for p in providers:
            if p == 'TensorrtExecutionProvider':
                # 默认仅 device_id（在 x86 ORT 1.28 + TRT 10.16 下多加
                # trt_engine_cache_*/trt_max_workspace_size 会静默回退 CPU）。
                # Jetson（onnxruntime 1.24 + TRT 10.13）可安全启用引擎缓存：
                # 首次构建 ~43s 并落盘，之后每次启动 ~1.3s 秒配，避免重复构建。
                _opts = {"device_id": 0}
                if _is_aarch64:
                    _cache = os.path.join(os.path.expanduser("~"),
                                          ".cache", "duad_trt_engine")
                    os.makedirs(_cache, exist_ok=True)
                    _opts = {"device_id": 0,
                             "trt_engine_cache_enable": "True",
                             "trt_engine_cache_path": _cache}
                provider_options.append(_opts)
            elif p == 'CUDAExecutionProvider':
                provider_options.append(
                    {"device_id": 0, "gpu_mem_limit": 3 * 1024 * 1024 * 1024})
            else:
                provider_options.append({})

        # ── 带降级重试的 session 构建 ─────────────────────────────
        # 某些模型（含 If 控制流、分支形状不一致，如 torch 动态控制流导出的
        # DINOv2 模型）在旧版 ORT 的 TensorRT EP 上分区失败，会直接抛异常
        # （Jetson 的 onnxruntime_gpu 1.24 实测如此；x86 ORT 1.28 会静默
        # 回退）。这里逐级降级：全 providers → 去掉 TRT → 仅 CPU，保证任何
        # 模型都不至于让上位机崩溃（与"相机 SDK 缺失不崩溃"同一原则）。
        self.session = None
        attempt_lists = [providers]
        if 'TensorrtExecutionProvider' in providers:
            attempt_lists.append(
                [p for p in providers if p != 'TensorrtExecutionProvider'])
        attempt_lists.append(['CPUExecutionProvider'])
        for attempt in attempt_lists:
            opts = [provider_options[providers.index(p)] if p in providers else {}
                    for p in attempt]
            try:
                self.session = ort.InferenceSession(
                    onnx_path, sess_options=so,
                    providers=attempt, provider_options=opts,
                )
                break
            except Exception as e:
                last_err = e
                print(f"[onnx_infer] 构建 session 失败（providers={attempt}）: "
                      f"{str(e)[:200]}")
        if self.session is None:
            raise RuntimeError(f"ONNX 模型无法加载（所有 provider 均失败）: "
                               f"{last_err}")
        # PCA 内联模式输入只有 image；旧 ckpt 导出需额外 mask
        self.pca_inline = len(self.session.get_inputs()) == 1
        self._mask = np.ones((1, (target_size // 14) ** 2), dtype=np.bool_)

        # 部署阈值（训练时标定并写入 metadata；旧模型无 → None，由调用方兜底）
        self.deploy = None
        self.image_threshold = None   # 图像级部署阈值（原始 patch-max 尺度）
        self.pixel_threshold = None   # 像素级 F1-max 分割阈值（原始 amap 尺度）
        self.pixel_f1_max = None
        self.pca_flip = None          # PCA 掩模翻转方向（"true"/"false"/None=旧模型）
        self.heatmap_vmin = None      # 热力图固定显示尺度（训练期标定，可选）
        self.heatmap_vmax = None
        self._load_deploy_metadata()

        # 最近一次推理的中间结果（供像素级定位/分割 UI 使用）
        self.last_hm_smooth = None     # 原始尺度热力图 [target_size, target_size]
        self.last_anomaly_mask = None  # 二值异常区域 [target_size, target_size] uint8

    def _load_deploy_metadata(self):
        """从 ONNX metadata_props 读取训练时标定的部署阈值。"""
        try:
            m = self.session.get_modelmeta().custom_metadata_map or {}
        except Exception:
            return

        def _f(key):
            v = m.get(key)
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        self.image_threshold = _f("duad.image_threshold")
        self.pixel_threshold = _f("duad.pixel_threshold")
        self.pixel_f1_max = _f("duad.pixel_f1_max")
        # 热力图固定显示尺度（训练期标定：good 样本 amap 像素 P2/P99.9；
        # 新模型可选 key，旧模型无 → None，客户端回退本地 scale.json/百分位）
        self.heatmap_vmin = _f("duad.heatmap_vmin")
        self.heatmap_vmax = _f("duad.heatmap_vmax")
        # PCA 掩模翻转方向（PCA_FLIP_FIX.md：新模型训练期固化方向并写入 metadata，
        # "true"=取负投影方向/"false"=直接方向；key 不存在 = 旧模型，图内逐图判断）
        self.pca_flip = m.get("duad.pca_flip")
        # pixel_f1_max == -1 表示标定集无缺陷样本、像素阈值无法定义（契约 5）：
        # 此时 pixel_threshold 是 sentinel -1，置 None 表示「退化为手动阈值」。
        if self.pixel_f1_max is not None and self.pixel_f1_max <= -0.5:
            self.pixel_threshold = None
        raw = m.get("duad.deploy")
        if raw:
            try:
                self.deploy = json.loads(raw)
            except (TypeError, ValueError):
                self.deploy = None

    def predict(self, image_rgb: np.ndarray, vmin: float = None, vmax: float = None,
                pixel_threshold: float = None, refine_quantile: float = None):
        """输入 numpy RGB 图 [H,W,3] uint8，返回 (热力图 RGB, 异常分数)。

        热力图尺寸 = target_size（与模型输入一致，联调时前端等比显示）。

        部署契约（DEPLOY_CONTRACT.md）：
        - 颜色映射用百分位归一化（p2/p98，仅显示）；vmin/vmax 同时显式传入时
          改用固定尺度（向后兼容）。
        - 像素级二值化用原始 hm_smooth 与阈值比较，发生在归一化之前，结果存入
          self.last_anomaly_mask。pixel_threshold 显式传入时覆盖 metadata 的
          F1-max 阈值（用户可调）；None 用 metadata 值；两者皆无时掩模为 None。
        - refine_quantile 非 None 时开启"精细定位"：最终阈值 = max(F1 阈值,
          图内该分位数)，掩模收窄到最异常区域（视觉接近服务器端可视化效果，
          但不依赖 GT；服务器端是用 GT 逐图搜索最优阈值的实验可视化）。
        """
        # ── 预处理：Resize + CenterCrop(target_size) + ImageNet 归一化 ──
        img = Image.fromarray(image_rgb).resize(
            (self.target_size, self.target_size), Image.BILINEAR)
        arr = np.asarray(img, dtype=np.float32) / 255.0
        arr = (arr - _IMAGENET_MEAN) / _IMAGENET_STD
        x = arr.transpose(2, 0, 1)[None, ...].astype(np.float32)

        # ── 推理 ──
        if self.pca_inline:
            heatmaps, scores = self.session.run(None, {'image': x})
        else:
            heatmaps, scores = self.session.run(
                None, {'image': x, 'mask': self._mask})

        # ── 后处理：模型已内化上采样 + 高斯平滑（见 export_onnx.py）──
        # 新模型输出 heatmaps [B, 1, target, target]，已是上采样+模糊后的 amap，
        # 直接作为 hm_smooth；旧模型输出 patch 级 [B, H_patch, W_patch]，向后
        # 兼容仍在此上采样 + 高斯平滑。
        hm = np.asarray(heatmaps[0], dtype=np.float32)
        if hm.ndim == 3 and hm.shape[0] == 1:
            hm_smooth = hm[0]                       # [target, target] 已内化后处理
        else:
            # 旧模型（patch 级）：双线性上采样到 target_size + numpy 高斯平滑
            hm_img = Image.fromarray(hm).resize(
                (self.target_size, self.target_size), Image.BILINEAR)
            hm_smooth = _gaussian_blur(np.asarray(hm_img, dtype=np.float32), sigma=4.0)

        # ── 1. 像素级二值化（原始尺度，必须先于归一化 —— 契约 3.2）──
        anomaly_mask = self._pixel_mask(hm_smooth, pixel_threshold, refine_quantile)

        # ── 2. 颜色显示：百分位归一化（p2/p98，仅颜色 —— 契约 3.3）──
        if vmin is not None and vmax is not None:
            norm = np.clip((hm_smooth - vmin) / (vmax - vmin), 0.0, 1.0)
        else:
            p2 = np.percentile(hm_smooth, 2)
            p98 = np.percentile(hm_smooth, 98)
            span = p98 - p2
            norm = np.clip((hm_smooth - p2) / span, 0.0, 1.0) if span > 1e-8 \
                else np.zeros_like(hm_smooth)

        # 图像级分数：直接用模型输出的 patch max（与训练/评估同口径，
        # export_onnx 中 image_scores = heatmaps.max）。判别器语义：正常 patch
        # 输出 ≥1 → 分数 ≤ -1；异常 patch 输出 <0 → 分数 >0。**不做 min-max
        # 归一化**——score 本身是 patch max，归一化后恒为 1，正常图也会误报。
        score = float(scores[0])

        # 保存中间结果（供像素级定位/分割 UI 使用）
        self.last_hm_smooth = hm_smooth
        self.last_anomaly_mask = anomaly_mask

        heatmap_rgb = _jet_colormap(norm)
        return heatmap_rgb, score

    def _pixel_mask(self, hm_smooth: np.ndarray, pixel_threshold: float = None,
                    refine_quantile: float = None):
        """像素级异常区域二值图（原始尺度，契约 3.2）。

        阈值优先级：显式传入 > metadata 的 F1-max 阈值；两者皆无（未标定/
        sentinel）时返回 None，表示退化为手动阈值。
        refine_quantile 非 None 时（精细定位）：最终阈值 = max(F1 阈值, 图内
        该分位数)——绝对阈值保证"真异常"下限，图内分位数把掩模收窄到最高分
        区域，视觉上接近服务器端 GT 逐图阈值的效果（但不依赖 GT）。
        """
        thr = self.pixel_threshold if pixel_threshold is None else pixel_threshold
        if thr is None:
            return None
        if refine_quantile is not None:
            rel = float(np.percentile(hm_smooth, refine_quantile * 100.0))
            thr = max(thr, rel)
        return (hm_smooth > thr).astype(np.uint8)


if __name__ == "__main__":
    import sys
    import time

    if len(sys.argv) < 2:
        print("用法: python onnx_infer.py <model.onnx> [image.png]")
        sys.exit(1)
    det = ONNXAnomalyDetector(sys.argv[1])
    print(f"PCA inline: {det.pca_inline}, inputs: "
          f"{[i.name for i in det.session.get_inputs()]}")

    if len(sys.argv) >= 3:
        img = np.asarray(Image.open(sys.argv[2]).convert("RGB"))
        t0 = time.time()
        heat, score = det.predict(img)
        print(f"推理耗时: {(time.time() - t0) * 1000:.1f}ms, 分数: {score:.4f}")
        Image.fromarray(heat).save("heatmap_out.png")
        print("热力图已保存: heatmap_out.png")
