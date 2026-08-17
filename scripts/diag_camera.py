#!/usr/bin/env python3
"""大恒相机诊断脚本（在真实桌面环境运行）。

检查链路：lsusb/sysfs 速度 → SDK init → 枚举 → 打开 → 关键特征。
重点输出像素格式（黄/蓝问题）与 DeviceLinkThroughputLimit（7.2fps 问题）。
"""
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from env import ensure_sdk_paths  # noqa: E402

ensure_sdk_paths()

from gxipy import (  # noqa: E402
    gx_init_lib, gx_update_device_list, gx_get_all_device_base_info,
    gx_close_lib, GxStatusList,
)
from Src.camera import CameraDevice  # noqa: E402


PIXEL_NAMES = {
    0x1080001: "Mono8",
    0x1080008: "BayerGR8",
    0x1080009: "BayerRG8",
    0x108000A: "BayerGB8",
    0x108000B: "BayerBG8",
    0x2180014: "RGB8",
    0x2180015: "BGR8",
}


def section(title):
    print(f"\n{'=' * 56}\n{title}\n{'=' * 56}")


def main():
    section("1. 系统 USB 状态")
    try:
        out = subprocess.check_output(["lsusb"], text=True)
        for line in out.splitlines():
            if "Daheng" in line or "2ba2" in line:
                print("lsusb:", line.strip())
                break
    except Exception as e:
        print("lsusb 不可用:", e)

    dev_bus = Path("/dev/bus/usb")
    if dev_bus.exists():
        nodes = list(dev_bus.glob("*/*"))
        print(f"/dev/bus/usb 存在: {dev_bus}，设备节点数 = {len(nodes)}")
        for dev in nodes:
            print(f"  {dev}  mode={oct(dev.stat().st_mode & 0o777)}")
        if not nodes:
            print("  ⚠ 没有设备节点：请安装 backend/config/99-galaxy-dev.rules，")
            print("    执行 udevadm trigger 并重新插拔相机")
    try:
        for base in Path("/sys/bus/usb/devices").glob("*"):
            if (base / "idVendor").exists():
                vid = (base / "idVendor").read_text().strip()
                if vid.lower() == "2ba2":
                    speed = (base / "speed").read_text().strip()
                    serial = (base / "serial").read_text().strip()
                    product = (base / "product").read_text().strip()
                    speed_name = {
                        "5000": "USB3 SuperSpeed(5Gbps)",
                        "480": "USB2 HighSpeed(480Mbps)",
                    }.get(speed, f"{speed}Mbps")
                    print(f"sysfs: {product}  速度: {speed_name}  SN: {serial}")

    except Exception as e:
        print("读取 sysfs 失败:", e)

    section("2. SDK init / 枚举")
    status = gx_init_lib()
    print(f"gx_init_lib = {status} ({'SUCCESS' if status == GxStatusList.SUCCESS else 'FAIL'})")
    if status != GxStatusList.SUCCESS:
        print("初始化失败：检查 backend/libs/*.cti 是否齐全、/dev/bus/usb 是否挂载")
        return 1

    status, dev_num = gx_update_device_list()
    print(f"gx_update_device_list = {status}, 设备数 = {dev_num}")
    if status != GxStatusList.SUCCESS:
        return 1

    devices = []
    if dev_num > 0:
        status, infos = gx_get_all_device_base_info(dev_num)
        if status == GxStatusList.SUCCESS:
            for info in infos:
                devices.append({
                    "model": info.model_name.decode("utf-8", "ignore").strip("\x00"),
                    "sn": info.serial_number.decode("utf-8", "ignore").strip("\x00"),
                })
            for d in devices:
                print("  设备:", d)
        else:
            print("gx_get_all_device_base_info 失败:", status)
            return 1
    else:
        print("枚举到 0 台相机")
        gx_close_lib()
        return 1

    section("3. 打开相机 + 关键特征")
    cam = CameraDevice(devices[0]["sn"])
    if cam.cam is None:
        print("打开相机失败")
        gx_close_lib()
        return 1

    def feat(name, ftype):
        try:
            return cam.get_remote_feature(name, ftype)
        except Exception as e:
            return f"<ERR {e}>"

    w = feat("GX_INT_WIDTH", "int")
    h = feat("GX_INT_HEIGHT", "int")
    print(f"当前分辨率: {w} × {h}")
    print(f"最大分辨率: {feat('GX_INT_WIDTH_MAX', 'int')} × {feat('GX_INT_HEIGHT_MAX', 'int')}")

    pf = feat("GX_ENUM_PIXEL_FORMAT", "enum")
    print(f"像素格式: {pf} (0x{pf:08x}) = {PIXEL_NAMES.get(pf, '未知/其他')}")

    print(f"采集模式: {feat('GX_ENUM_ACQUISITION_MODE', 'enum')} (0=Single,2=Continuous)")
    print(f"帧率控制模式: {feat('GX_ENUM_ACQUISITION_FRAME_RATE_MODE', 'enum')} (0=Off,1=On)")
    target_fps = feat("GX_FLOAT_ACQUISITION_FRAME_RATE", "float")
    print(f"目标帧率: {target_fps} fps")
    print(f"当前帧率: {feat('GX_FLOAT_CURRENT_ACQUISITION_FRAME_RATE', 'float')} fps")
    exposure = feat("GX_FLOAT_EXPOSURE_TIME", "float")
    print(f"曝光时间: {exposure} us")
    if isinstance(target_fps, (int, float)) and isinstance(exposure, (int, float)) \
            and target_fps > 0 and exposure > 1_000_000.0 / target_fps * 0.9:
        print(f">>> 曝光 {exposure:.0f}us 太长：该曝光下理论最大帧率仅 "
              f"{1_000_000.0 / exposure:.3f}fps。开始采集时代码会自动把曝光压到 "
              f"{int(1_000_000.0 / target_fps * 0.9)}us 左右以匹配 {target_fps:.1f}fps。")
    print(f"增益: {feat('GX_FLOAT_GAIN', 'float')} dB")

    print(f"LinkSelector: {feat('GX_INT_DEVICE_LINK_SELECTOR', 'int')}")
    mode = feat("GX_ENUM_DEVICE_LINK_THROUGHPUT_LIMIT_MODE", "enum")
    limit = feat("GX_INT_DEVICE_LINK_THROUGHPUT_LIMIT", "int")
    current = feat("GX_INT_DEVICE_LINK_CURRENT_THROUGHPUT", "int")
    print(f"吞吐量限制模式: {mode} (0=Off,1=On)")
    print(f"吞吐量限制值: {limit} B/s")
    print(f"当前吞吐量: {current} B/s")
    if isinstance(mode, int) and mode == 1:
        print(">>> 限制模式为 On！5MP 下会被限制在约 7.2fps。"
              "进入异常检测页点击开始采集时会自动写 Off。")

    if "--fps-test" in sys.argv:
        section("4. 采集帧率实测（会启动/停止相机）")
        print("调用 gather_start()：代码将自动执行以下操作")
        print("  - 关闭 DeviceLinkThroughputLimit")
        print("  - 切 Continuous + 开帧率控制")
        print("  - 若曝光超过帧周期，自动下调曝光")
        if cam.gather_start():
            print("采集已启动，等待 3s 稳定帧率...")
            for _ in range(6):
                time.sleep(0.5)
                fps = feat("GX_FLOAT_CURRENT_ACQUISITION_FRAME_RATE", "float")
                exp = feat("GX_FLOAT_EXPOSURE_TIME", "float")
                print(f"  当前帧率: {fps} fps, 曝光: {exp} us")
            cam.gather_stop()
        else:
            print("采集启动失败")

    cam.cam_close()
    gx_close_lib()
    section("诊断完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
