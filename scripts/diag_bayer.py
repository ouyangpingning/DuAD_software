#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bayer 颜色排列诊断 — Windows/Linux 黄蓝互换定位。

连接相机抓一帧原始 Bayer 数据，用 RG/GB/GR/BG 四种排列各转换一张图，
并打印相机上报的 pixel_format 与 GX_ENUM_PIXEL_COLOR_FILTER 值。
运行后查看同目录下生成的 diag_bayer_*.png，哪张颜色正常就是正确答案。

用法：<venv-python> -u scripts/diag_bayer.py
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import numpy as np
from ctypes import c_ubyte
from _ctypes import addressof
from PIL import Image

from gxipy import (
    GxStatusList, gx_init_lib, gx_update_device_list, gx_get_all_device_base_info,
    GxOpenParam, GxAccessMode, GxOpenMode, gx_open_device,
    gx_register_capture_callback, gx_unregister_capture_callback,
    gx_send_command, dx_raw8_to_rgb24, DxBayerConvertType, DxPixelColorFilter,
    CAP_CALL,
)

raw_frames = []


def diag_cb(ptr):
    fp = ptr.contents
    size = int(fp.image_size)
    raw = (c_ubyte * size).from_buffer_copy(
        (c_ubyte * size).from_address(fp.image_buf))
    raw_frames.append({
        "pixel_format": int(fp.pixel_format),
        "height": int(fp.height),
        "width": int(fp.width),
        "raw": raw,
    })


def main():
    if gx_init_lib() != GxStatusList.SUCCESS:
        print("gx_init_lib 失败"); return
    status, dev_num = gx_update_device_list()
    status, dev_info = gx_get_all_device_base_info(dev_num)
    if dev_num == 0:
        print("未枚举到相机"); return
    sn = dev_info[0].serial_number.decode("utf-8").strip("\x00")
    print(f"相机 SN: {sn}")

    op = GxOpenParam()
    op.access_mode = GxAccessMode.CONTROL
    op.openMode = GxOpenMode.SN
    op.content = sn.encode("utf-8")
    status, cam = gx_open_device(op)
    if status != GxStatusList.SUCCESS:
        print("打开相机失败", status); return

    def get_enum(fid):
        from gxipy import gx_is_implemented, gx_is_readable, gx_get_enum, GxFeatureID
        s, impl = gx_is_implemented(cam, fid)
        if s != GxStatusList.SUCCESS or not impl:
            return None
        s, rd = gx_is_readable(cam, fid)
        if s != GxStatusList.SUCCESS or not rd:
            return None
        s, v = gx_get_enum(cam, fid)
        return v if s == GxStatusList.SUCCESS else None

    from gxipy import GxFeatureID
    pf = get_enum(GxFeatureID.ENUM_PIXEL_FORMAT)
    cf = get_enum(GxFeatureID.ENUM_PIXEL_COLOR_FILTER)
    print(f"GX_ENUM_PIXEL_FORMAT = {pf:#010x}")
    print(f"GX_ENUM_PIXEL_COLOR_FILTER = {cf}  (1=RG 2=GB 3=GR 4=BG)")
    print("color_filter -> 应映射的 bayer =",
          {1: "RG", 2: "GB", 3: "GR", 4: "BG", None: "未读到"}.get(cf))

    cb = CAP_CALL(diag_cb)
    gx_register_capture_callback(cam, cb)
    gx_send_command(cam, GxFeatureID.COMMAND_ACQUISITION_START)

    print("采集中，等待一帧...")
    t0 = time.time()
    while not raw_frames and time.time() - t0 < 8:
        time.sleep(0.05)
    gx_send_command(cam, GxFeatureID.COMMAND_ACQUISITION_STOP)
    gx_unregister_capture_callback(cam)
    from gxipy import gx_close_device
    gx_close_device(cam)

    if not raw_frames:
        print("未抓到帧"); return
    fr = raw_frames[0]
    w, h, size = fr["width"], fr["height"], len(fr["raw"])
    print(f"帧: {w}x{h}  pixel_format={fr['pixel_format']:#010x}")
    if fr["pixel_format"] != pf:
        print(f"⚠️ 帧内 pixel_format({fr['pixel_format']:#010x}) 与读取值({pf:#010x})不同！")

    for name, bf in (("RG", DxPixelColorFilter.RG), ("GB", DxPixelColorFilter.GB),
                     ("GR", DxPixelColorFilter.GR), ("BG", DxPixelColorFilter.BG)):
        out = (c_ubyte * (size * 3))()
        st = dx_raw8_to_rgb24(addressof(fr["raw"]), addressof(out), w, h,
                              DxBayerConvertType.NEIGHBOUR, bf, False)
        img = np.frombuffer(out, dtype=np.uint8).reshape(h, w, 3).copy()
        p = ROOT / f"diag_bayer_{name}.png"
        Image.fromarray(img).save(p)
        r, g, b = img[..., 0].mean(), img[..., 1].mean(), img[..., 2].mean()
        print(f"  {name}: status={st} R={r:6.1f} G={g:6.1f} B={b:6.1f} "
              f"|R-B|={abs(r-b):5.1f} -> {p.name}")

    print("\n请打开上面 4 张图，哪张颜色正常（黄/蓝不互换）就是正确答案。")


if __name__ == "__main__":
    main()
