"""生成「二轴平台台面」的纹理底图（无缝、中性灰、512²、几 KB）。

为什么自己生成而不是下载：
  1. **零授权风险** —— 下下来的图要么要署名、要么禁止"随软件再分发"。
     自己用噪声生成，版权上什么都不欠（CC0 那种站也推荐，但没必要）。
  2. **对比度可以精确调到"轨迹线看得清"** —— 下载的图往往偏亮偏花，
     台面是坐标参照物，上面还要画 2px 轨迹线与 1px 推导线。
  3. **文件极小、可复现**：噪声本身高度可压，512² JPEG 约 20~60KB。

⚠ 为什么是**中性灰（均值 0.5）**而不是接近白色：
  界面里台面 = `Image` + 一层 `Colors.panelBg` 半透明覆盖。`panelBg` 在
  **暗色主题下是 `#2a2a2a`**（亮色是 `#dfe8eb`）—— 所以
    亮色：0.72×223 + 0.28×128 ≈ 196（浅灰台面）
    暗色：0.72×42  + 0.28×128 ≈ 66 （深灰台面）
  一张中性灰图在**两套主题下都成立**，且纹理振幅对称；用接近白色的图，
  暗色主题下台面会被冲成一片灰白（试过就知道）。

用法：
    python3 scripts/make_texture.py            # 生成三张候选
    python3 scripts/make_texture.py --amp 0.22 # 想更明显就调大振幅
"""
from __future__ import annotations

import argparse
import pathlib

import numpy as np
from PIL import Image, ImageFilter

SIZE = 512
OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "DuAD_SoftwareContent" / "images"


def _to_img(arr01: np.ndarray) -> Image.Image:
    return Image.fromarray(np.uint8(np.clip(arr01, 0.0, 1.0) * 255.0), "L")


def _seamless_blur(arr01: np.ndarray, sigma: float) -> np.ndarray:
    """把数组当**周期**纹理做高斯模糊：铺 3×3 → 模糊 → 取正中那块。

    这样左右/上下边界天然连续（噪声直接模糊的话，接缝处的相关性是断的，
    平铺时会出现十字亮线 —— 我们虽然 1:1 铺不平铺，但无缝总是更稳）。
    """
    im = _to_img(arr01)
    tiled = Image.new("L", (SIZE * 3, SIZE * 3))
    for i in range(3):
        for j in range(3):
            tiled.paste(im, (i * SIZE, j * SIZE))
    tiled = tiled.filter(ImageFilter.GaussianBlur(sigma))
    out = tiled.crop((SIZE, SIZE, SIZE * 2, SIZE * 2))
    return np.asarray(out, dtype=np.float64) / 255.0


def _finish(arr: np.ndarray, amp: float, fade: int) -> np.ndarray:
    """去均值 + 归一到指定标准差 → 平移到 0.5（中性灰）→ 四边向均值淡出。

    ⚠ 淡出不是"为了好看"，是**必需的**：台面是圆角矩形，而 `Image` 只能画成矩形。
      界面里把这张图**向内缩 radius 像素**放（见 TrajectoryGridCard 的 `StagePlate`），
      方角就落在圆角以内；再让图自己的四边淡出，才不会看到"纹理的方形边界"那条硬边
      （否则圆角板里会冒出一个方块状的纹理）。
    """
    a = arr - float(arr.mean())
    s = float(a.std()) + 1e-9
    out = np.clip(0.5 + amp * (a / s), 0.0, 1.0)
    if fade > 0:
        up = np.arange(fade, dtype=np.float64) / float(fade)
        ramp = np.concatenate([up, np.ones(SIZE - 2 * fade), up[::-1]])
        out = 0.5 + (out - 0.5) * np.outer(ramp, ramp)
    return out


def matte_sandblasted(rng: np.random.Generator, amp: float, fade: int) -> np.ndarray:
    """哑光喷砂：极细的均匀颗粒（最中性，最不抢线的注意力）。"""
    n = _seamless_blur(rng.random((SIZE, SIZE)), 0.7)
    return _finish(n, amp, fade)


def brushed_metal(rng: np.random.Generator, amp: float, fade: int) -> np.ndarray:
    """拉丝铝：细横纹（沿 x 不变、沿 y 变化）+ 一层很缓的疏密变化 + 少量斑点。"""
    fine = _seamless_blur(rng.random((SIZE, 1)).repeat(SIZE, axis=1), 0.55)
    mid = _seamless_blur(rng.random((SIZE, 1)).repeat(SIZE, axis=1), 2.5)
    broad = _seamless_blur(rng.random((SIZE, 1)).repeat(SIZE, axis=1), 60.0)
    speck = _seamless_blur(rng.random((SIZE, SIZE)), 0.5)      # 少量斑点，破掉"太规整"
    mix = 0.55 * fine + 0.45 * mid + 0.55 * broad + 0.12 * speck
    return _finish(mix, amp, fade)


def frosted_glass(rng: np.random.Generator, amp: float, fade: int) -> np.ndarray:
    """磨砂玻璃：比喷砂更大的柔和斑块，带一点细腻颗粒。"""
    mottle = _seamless_blur(rng.random((SIZE, SIZE)), 3.0)
    grain = _seamless_blur(rng.random((SIZE, SIZE)), 0.6)
    return _finish(0.8 * mottle + 0.25 * grain, amp, fade)


STYLES = {
    "台面_哑光喷砂": matte_sandblasted,
    "台面_拉丝铝": brushed_metal,
    "台面_磨砂玻璃": frosted_glass,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--amp", type=float, default=0.06,
                    help="纹理标准差。0.06=很淡（推荐）、0.10=明显、0.16=过头（像雪花）")
    ap.add_argument("--fade", type=int, default=24,
                    help="四边向均值淡出的像素数（配合界面里内缩 radius 放置）")
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args()
    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(20261010)     # 固定种子 → 可复现
    for name, fn in STYLES.items():
        arr = fn(rng, args.amp, args.fade)
        rgb = np.repeat(np.uint8(np.clip(arr, 0, 1) * 255)[:, :, None], 3, axis=2)
        path = out_dir / f"{name}.jpg"
        # ⚠ 用 JPEG 不用 PNG：噪声图 PNG 压不动（512² 会到 300KB+）。
        #   q85 + 4:2:0 下每张 50~80KB，而纹理本身要跟固体色按 ~3:7 混合，
        #   这点压缩噪点根本看不出来（q92 反而白胖一倍）。
        Image.fromarray(rgb, "RGB").save(path, quality=85, subsampling=2, optimize=True)
        print(f"{path.name:22s} {path.stat().st_size / 1024:6.1f} KB  "
              f"均值={arr.mean():.3f} 标准差={arr.std():.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
