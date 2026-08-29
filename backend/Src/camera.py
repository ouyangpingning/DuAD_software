import os
import numpy
from PySide6.QtCore import Signal, QObject
from _ctypes import addressof
from ctypes import c_ubyte, c_void_p, c_int


def _load_bayer_demosaic_c():
    """加载 libbayer_demosaic.so（arm64 上编译的 Bayer 去马赛克加速，~3ms/5MP）。

    失败（库不存在/架构不符）返回 None，调用方回退 numpy 实现。
    """
    try:
        import ctypes as _ct
        import platform as _pf
        from pathlib import Path as _P
    except ImportError:
        return None
    base = _P(__file__).resolve().parent.parent
    libdir = base / ("libs_arm64" if _pf.machine() in ("aarch64", "arm64") else "libs")
    so = libdir / "libbayer_demosaic.so"
    if not so.exists():
        return None
    try:
        lib = _ct.CDLL(str(so))
        lib.bayer_demosaic_rgb24.argtypes = [_ct.c_void_p, _ct.c_void_p,
                                             _ct.c_int, _ct.c_int, _ct.c_int]
        lib.bayer_demosaic_rgb24.restype = _ct.c_int
        return lib.bayer_demosaic_rgb24
    except OSError:
        return None


_BAYER_DEMOSAIC_C = _load_bayer_demosaic_c()

_SDK_MISSING_MSG = "Galaxy Camera SDK (libgxiapi.so) not found. Camera functions unavailable."

try:
    from gxipy import gx_init_lib, GxStatusList, gx_close_lib, gx_update_device_list, gx_get_all_device_base_info, \
        GxOpenParam, GxAccessMode, GxOpenMode, gx_open_device, gx_close_device, gx_is_implemented, gx_is_readable, \
        gx_get_string, gx_get_int, gx_get_enum, gx_get_float, gx_get_bool, gx_is_writable, gx_set_string, gx_set_int, \
        gx_set_enum, gx_set_float, gx_set_bool, gx_send_command, GxFeatureID, GxPixelFormatEntry, \
        DxBayerConvertType, DxPixelColorFilter, GxPixelColorFilterEntry, CAP_CALL, \
        gx_register_capture_callback, gx_unregister_capture_callback
    try:
        # arm64（Jetson）SDK 不带 DxImageProc 库（libgxiapi.so 不导出
        # DxRaw8toRGB24）：gxipy 里不存在该函数，Bayer 帧走 numpy 去马赛克
        from gxipy import dx_raw8_to_rgb24
    except ImportError:
        dx_raw8_to_rgb24 = None
        print("[camera] SDK 无 DxImageProc（dx_raw8_to_rgb24 缺失），Bayer 帧将用 numpy 去马赛克")
except (ImportError, NameError, OSError, KeyError) as e:
    # Fallback: SDK not available (e.g. dev machine without camera hardware)
    GxStatusList = type('GxStatusList', (), {'SUCCESS': 0, 'ERROR': -1})()
    _SDK_NAMES = [
        'STRING_DEVICE_MODEL_NAME', 'STRING_DEVICE_SERIAL_NUMBER', 'FLOAT_DEVICE_TEMPERATURE',
        'INT_SENSOR_WIDTH', 'INT_SENSOR_HEIGHT', 'INT_WIDTH_MAX', 'INT_HEIGHT_MAX',
        'INT_WIDTH', 'INT_HEIGHT', 'INT_OFFSET_X', 'INT_OFFSET_Y',
        'ENUM_PIXEL_FORMAT', 'ENUM_PIXEL_COLOR_FILTER',
        'INT_DEVICE_LINK_SELECTOR', 'ENUM_DEVICE_LINK_THROUGHPUT_LIMIT_MODE',
        'INT_DEVICE_LINK_THROUGHPUT_LIMIT', 'INT_DEVICE_LINK_CURRENT_THROUGHPUT',
        'BOOL_GAMMA_ENABLE', 'ENUM_GAMMA_MODE', 'FLOAT_GAMMA_PARAM',
        'ENUM_ACQUISITION_MODE', 'ENUM_ACQUISITION_FRAME_RATE_MODE',
        'FLOAT_ACQUISITION_FRAME_RATE', 'FLOAT_CURRENT_ACQUISITION_FRAME_RATE',
        'FLOAT_EXPOSURE_TIME', 'FLOAT_GAIN',
        'COMMAND_ACQUISITION_START', 'COMMAND_ACQUISITION_STOP',
        'ENUM_ACQUISITION_STATUS_SELECTOR', 'BOOL_ACQUISITION_STATUS',
        'ENUM_BINNING_HORIZONTAL_MODE', 'ENUM_BINNING_VERTICAL_MODE',
        'INT_BINNING_HORIZONTAL', 'INT_BINNING_VERTICAL',
    ]
    GxFeatureID = type('GxFeatureID', (), {k: i for i, k in enumerate(_SDK_NAMES)})()

    def _sdk_stub(*args, **kwargs):
        raise RuntimeError(_SDK_MISSING_MSG)

    gx_init_lib = gx_close_lib = gx_update_device_list = gx_get_all_device_base_info = _sdk_stub
    gx_open_device = gx_close_device = gx_is_implemented = gx_is_readable = _sdk_stub
    gx_get_string = gx_get_int = gx_get_enum = gx_get_float = gx_get_bool = _sdk_stub
    gx_is_writable = gx_set_string = gx_set_int = gx_set_enum = gx_set_float = gx_set_bool = _sdk_stub
    gx_send_command = gx_register_capture_callback = gx_unregister_capture_callback = _sdk_stub
    dx_raw8_to_rgb24 = _sdk_stub
    GxOpenParam = GxAccessMode = GxOpenMode = CAP_CALL = object
    DxBayerConvertType = type('DxBayerConvertType', (), {'NEIGHBOUR': 0})()
    DxPixelColorFilter = type('DxPixelColorFilter', (), {
        'NONE': 0, 'RG': 1, 'GB': 2, 'GR': 3, 'BG': 4,
    })()
    GxPixelColorFilterEntry = type('GxPixelColorFilterEntry', (), {
        'NONE': 0, 'BAYER_RG': 1, 'BAYER_GB': 2, 'BAYER_GR': 3, 'BAYER_BG': 4,
    })()
    GxPixelFormatEntry = type('GxPixelFormatEntry', (), {
        'MONO8': 0x1080001, 'BAYER_RG8': 0x1080009, 'BAYER_GB8': 0x108000A,
        'BAYER_BG8': 0x108000B, 'BAYER_GR8': 0x1080008,
        'RGB8': 0x2180014, 'BGR8': 0x2180015,
    })()
    print(f"[camera] {_SDK_MISSING_MSG}")

FEATURE_MAP = {
    "GX_STRING_DEVICE_MODEL_NAME": GxFeatureID.STRING_DEVICE_MODEL_NAME,
    "GX_STRING_DEVICE_SERIAL_NUMBER": GxFeatureID.STRING_DEVICE_SERIAL_NUMBER,
    "GX_FLOAT_DEVICE_TEMPERATURE": GxFeatureID.FLOAT_DEVICE_TEMPERATURE,
    "GX_INT_SENSOR_WIDTH": GxFeatureID.INT_SENSOR_WIDTH,
    "GX_INT_SENSOR_HEIGHT": GxFeatureID.INT_SENSOR_HEIGHT,
    "GX_INT_WIDTH_MAX": GxFeatureID.INT_WIDTH_MAX,
    "GX_INT_HEIGHT_MAX": GxFeatureID.INT_HEIGHT_MAX,
    "GX_INT_WIDTH": GxFeatureID.INT_WIDTH,
    "GX_INT_HEIGHT": GxFeatureID.INT_HEIGHT,
    "GX_INT_OFFSET_X": GxFeatureID.INT_OFFSET_X,
    "GX_INT_OFFSET_Y": GxFeatureID.INT_OFFSET_Y,
    "GX_ENUM_PIXEL_FORMAT": GxFeatureID.ENUM_PIXEL_FORMAT,
    "GX_ENUM_PIXEL_COLOR_FILTER": GxFeatureID.ENUM_PIXEL_COLOR_FILTER,
    "GX_INT_DEVICE_LINK_SELECTOR": GxFeatureID.INT_DEVICE_LINK_SELECTOR,
    "GX_ENUM_DEVICE_LINK_THROUGHPUT_LIMIT_MODE": GxFeatureID.ENUM_DEVICE_LINK_THROUGHPUT_LIMIT_MODE,
    "GX_INT_DEVICE_LINK_THROUGHPUT_LIMIT": GxFeatureID.INT_DEVICE_LINK_THROUGHPUT_LIMIT,
    "GX_INT_DEVICE_LINK_CURRENT_THROUGHPUT": GxFeatureID.INT_DEVICE_LINK_CURRENT_THROUGHPUT,
    "GX_BOOL_GAMMA_ENABLE": GxFeatureID.BOOL_GAMMA_ENABLE,
    "GX_ENUM_GAMMA_MODE": GxFeatureID.ENUM_GAMMA_MODE,
    "GX_FLOAT_GAMMA_PARAM": GxFeatureID.FLOAT_GAMMA_PARAM,
    "GX_ENUM_ACQUISITION_MODE": GxFeatureID.ENUM_ACQUISITION_MODE,
    "GX_ENUM_ACQUISITION_FRAME_RATE_MODE": GxFeatureID.ENUM_ACQUISITION_FRAME_RATE_MODE,
    "GX_FLOAT_ACQUISITION_FRAME_RATE": GxFeatureID.FLOAT_ACQUISITION_FRAME_RATE,
    "GX_FLOAT_CURRENT_ACQUISITION_FRAME_RATE": GxFeatureID.FLOAT_CURRENT_ACQUISITION_FRAME_RATE,
    "GX_FLOAT_EXPOSURE_TIME": GxFeatureID.FLOAT_EXPOSURE_TIME,
    "GX_FLOAT_GAIN": GxFeatureID.FLOAT_GAIN,
    "GX_COMMAND_ACQUISITION_START": GxFeatureID.COMMAND_ACQUISITION_START,
    "GX_COMMAND_ACQUISITION_STOP": GxFeatureID.COMMAND_ACQUISITION_STOP,
    "GX_ENUM_ACQUISITION_STATUS_SELECTOR": GxFeatureID.ENUM_ACQUISITION_STATUS_SELECTOR,
    "GX_BOOL_ACQUISITION_STATUS": GxFeatureID.BOOL_ACQUISITION_STATUS,
    "GX_ENUM_BINNING_HORIZONTAL_MODE": GxFeatureID.ENUM_BINNING_HORIZONTAL_MODE,
    "GX_ENUM_BINNING_VERTICAL_MODE": GxFeatureID.ENUM_BINNING_VERTICAL_MODE,
    "GX_INT_BINNING_HORIZONTAL": GxFeatureID.INT_BINNING_HORIZONTAL,
    "GX_INT_BINNING_VERTICAL": GxFeatureID.INT_BINNING_VERTICAL,
}

# 大恒采集模式枚举（0=SingleFrame, 2=Continuous）；帧率控制模式枚举（0=Off, 1=On）
_GX_ACQ_MODE_CONTINUOUS = 2
_GX_FRAME_RATE_MODE_ON = 1
# DeviceLinkThroughputLimitMode 枚举：0=Off, 1=On。部分相机出厂默认 On 且限速
# 36 MB/s，5MP 全幅正好被卡在 ~7.2fps，开始采集前必须关闭。
_GX_THROUGHPUT_LIMIT_MODE_OFF = 0

# 大恒 Bayer 8bit 像素格式 → DxRaw8toRGB24 所需的 Bayer 排列参数
_PIXEL_FORMAT_TO_BAYER = {
    GxPixelFormatEntry.BAYER_RG8: DxPixelColorFilter.RG,
    GxPixelFormatEntry.BAYER_GB8: DxPixelColorFilter.GB,
    GxPixelFormatEntry.BAYER_GR8: DxPixelColorFilter.GR,
    GxPixelFormatEntry.BAYER_BG8: DxPixelColorFilter.BG,
}

# 传感器颜色滤镜排列（GX_ENUM_PIXEL_COLOR_FILTER，读自相机寄存器，跨平台一致）
# → DxRaw8toRGB24 的 Bayer 参数。优先于 pixel_format 使用：某些环境/固件下
# 相机上报的 pixel_format（BAYER_RG8 等）与传感器真实滤镜不一致，直接按
# pixel_format 查表会把 R/B 通道转反（症状 = 黄蓝互换）。
_COLOR_FILTER_TO_BAYER = {
    GxPixelColorFilterEntry.BAYER_RG: DxPixelColorFilter.RG,
    GxPixelColorFilterEntry.BAYER_GB: DxPixelColorFilter.GB,
    GxPixelColorFilterEntry.BAYER_GR: DxPixelColorFilter.GR,
    GxPixelColorFilterEntry.BAYER_BG: DxPixelColorFilter.BG,
}

# Windows 平台 R/B 交换（Windows 与 Linux 的 DxImageProc 对 bayer_type 语义相反）：
# 实测同一相机（MER2，color_filter=RG）Linux 上 RG 正常、Windows 上 RG 黄蓝互换，
# diag_bayer 逐排列验证 Windows 需用 BG。因此 Windows 下把 RG↔BG、GB↔GR 交换。
_SWAP_RB_BAYER = {
    DxPixelColorFilter.RG: DxPixelColorFilter.BG,
    DxPixelColorFilter.BG: DxPixelColorFilter.RG,
    DxPixelColorFilter.GB: DxPixelColorFilter.GR,
    DxPixelColorFilter.GR: DxPixelColorFilter.GB,
}


def adjust_to_step(value, step, min_val=None, max_val=None):
    """就近向下对齐到 step 的倍数，并夹在 [min_val, max_val] 内。"""
    adjusted = (value // step) * step
    if min_val is not None and adjusted < min_val:
        adjusted = ((value + step - 1) // step) * step
    if max_val is not None and adjusted > max_val:
        adjusted = (max_val // step) * step
    return adjusted


def _bayer_demosaic_numpy(raw, bayer_filter):
    """numpy 双线性 Bayer 去马赛克（arm64 SDK 无 DxImageProc 时的兜底）。

    bayer_filter: DxPixelColorFilter（RG=1/GB=2/GR=3/BG=4，Linux 语义）。
    算法：对每个颜色通道，在已知采样相位上先边缘复制 padding，再对
    水平/垂直/对角邻域取均值填补其余相位（与 DxImageProc NEIGHBOUR
    插值一致）。返回 [h, w, 3] uint8 RGB。
    """
    h, w = raw.shape
    p00, p01, p10, p11 = raw[0::2, 0::2], raw[0::2, 1::2], raw[1::2, 0::2], raw[1::2, 1::2]
    # 各 Bayer 排列下 RGB 通道对应的相位（(采样网格, oy, ox)）
    if bayer_filter == DxPixelColorFilter.RG:            # RGGB
        r_s, r_oy, r_ox = p00, 0, 0
        g1_s, g1_oy, g1_ox = p01, 0, 1
        g2_s, g2_oy, g2_ox = p10, 1, 0
        b_s, b_oy, b_ox = p11, 1, 1
    elif bayer_filter == DxPixelColorFilter.GB:          # GBRG
        r_s, r_oy, r_ox = p10, 1, 0
        g1_s, g1_oy, g1_ox = p00, 0, 0
        g2_s, g2_oy, g2_ox = p11, 1, 1
        b_s, b_oy, b_ox = p01, 0, 1
    elif bayer_filter == DxPixelColorFilter.GR:          # GRBG
        r_s, r_oy, r_ox = p01, 0, 1
        g1_s, g1_oy, g1_ox = p00, 0, 0
        g2_s, g2_oy, g2_ox = p11, 1, 1
        b_s, b_oy, b_ox = p10, 1, 0
    else:                                                # BGGR
        r_s, r_oy, r_ox = p11, 1, 1
        g1_s, g1_oy, g1_ox = p01, 0, 1
        g2_s, g2_oy, g2_ox = p10, 1, 0
        b_s, b_oy, b_ox = p00, 0, 0

    def _fill(samples, oy, ox):
        """已知采样在 (oy,ox) 相位（步长 2），补全全分辨率网格。

        以相位网格为基准（p 是边缘复制 pad 后的相位网格）。输出行/列与
        相位网格索引一一对应：
        - 已知相位：out[2i+oy, 2j+ox] = samples[i,j]
        - 水平邻（同行、x 异相位）：相位列 (j,j+1)（ox=0）或 (j-1,j)（ox=1）
        - 垂直邻（y 异相位、同列）：相位行 (i,i+1)（oy=0）或 (i-1,i)（oy=1）
        - 对角邻：上述行对与列对的 4 组合
        维度为奇数时右侧/下侧多 pad 1 列/行（复制边缘），使最后一对邻居
        可取值（相机分辨率恒为偶数，此处仅兜底）。
        """
        sh, sw = samples.shape
        out = numpy.zeros((h, w), dtype=numpy.uint16)
        p = numpy.pad(samples.astype(numpy.uint16),
                      ((1, 1 + h % 2), (1, 1 + w % 2)), mode="edge")
        out[oy::2, ox::2] = samples
        # 水平邻的 p 列对；垂直邻的 p 行对；中心行/列（同行同列插值的基准）
        if ox == 0:
            cL, cR = slice(1, 1 + sw), slice(2, 2 + sw)
        else:
            cL, cR = slice(0, sw + w % 2), slice(1, 1 + sw + w % 2)
        if oy == 0:
            rU, rD = slice(1, 1 + sh), slice(2, 2 + sh)
        else:
            rU, rD = slice(0, sh + h % 2), slice(1, 1 + sh + h % 2)
        rowC, colC = slice(1, 1 + sh), slice(1, 1 + sw)
        # 水平插值（同行相邻列）
        t = out[oy::2, 1 - ox::2]
        t[...] = ((p[rowC, cL] + p[rowC, cR]) >> 1)[:t.shape[0], :t.shape[1]]
        # 垂直插值（同列相邻行）
        t = out[1 - oy::2, ox::2]
        t[...] = ((p[rU, colC] + p[rD, colC]) >> 1)[:t.shape[0], :t.shape[1]]
        # 对角（4 邻域均值）
        t = out[1 - oy::2, 1 - ox::2]
        t[...] = ((p[rU, cL] + p[rU, cR] + p[rD, cL] + p[rD, cR]) >> 2)[
            :t.shape[0], :t.shape[1]]
        return out

    r = _fill(r_s, r_oy, r_ox)
    g = (_fill(g1_s, g1_oy, g1_ox) + _fill(g2_s, g2_oy, g2_ox)) >> 1
    b = _fill(b_s, b_oy, b_ox)
    return numpy.stack([r, g, b], axis=2).astype(numpy.uint8)


class CameraManager:
    """Manages camera device discovery and library lifecycle."""

    def __init__(self, device_num):
        self.device_num = device_num

    def init_lib(self):
        status = gx_init_lib()
        if status == GxStatusList.SUCCESS:
            print("资源申请完成，打开设备")

    def close_lib(self):
        status = gx_close_lib()
        if status == GxStatusList.SUCCESS:
            print("资源释放完成，关闭设备")

    def get_device_manager(self):
        try:
            status, dev_num = gx_update_device_list()
            status, dev_info_list = gx_get_all_device_base_info(self.device_num)
            if status == GxStatusList.SUCCESS:
                if dev_num == 0:
                    print("Number of enumerated devices is 0")
                    return []
                else:
                    return [dev.serial_number.decode('utf-8').strip('\x00') for dev in dev_info_list]
            else:
                print("获取相机列表失败")
        except Exception as exception:
            print("错误:{}".format(exception))

    def get_devices_info(self):
        """枚举设备并返回 [{model, sn}, ...]（前端显示用）。"""
        try:
            status, dev_num = gx_update_device_list()
            status, dev_info_list = gx_get_all_device_base_info(dev_num)
            if status == GxStatusList.SUCCESS and dev_num > 0:
                return [{
                    "model": dev.model_name.decode('utf-8').strip('\x00'),
                    "sn": dev.serial_number.decode('utf-8').strip('\x00'),
                } for dev in dev_info_list]
            return []
        except Exception as exception:
            print("错误:{}".format(exception))
            return []


class CameraDevice(QObject):
    """Single camera device with feature control and image acquisition."""

    image_captured = Signal(numpy.ndarray)

    def __init__(self, device_name: str):
        QObject.__init__(self)
        self.cam_name = device_name
        self.cam = None
        self.roi_region = None
        self.is_gathering = False
        self.is_drawing_roi = False
        self.is_qinputdialog_set = False
        self.pixel_format = None
        self.color_filter = None
        self._unsupported_pixel_format_warned = False

        try:
            open_param = GxOpenParam()
            open_param.access_mode = GxAccessMode.CONTROL
            open_param.openMode = GxOpenMode.SN
            open_param.content = self.cam_name.encode('utf-8')
            status, self.cam = gx_open_device(open_param)
            if status == GxStatusList.SUCCESS:
                print(f"相机{self.cam_name}打开成功")
            self.callback = CAP_CALL(self.capture_image)
            status = gx_register_capture_callback(self.cam, self.callback)
            if status == GxStatusList.SUCCESS:
                print("回调函数创建成功\n")
            self.pixel_format = self.get_remote_feature("GX_ENUM_PIXEL_FORMAT", "enum")
            print(f"[camera] 当前像素格式: {self.pixel_format:#010x}"
                  if self.pixel_format is not None else "[camera] 无法读取像素格式")
            self.color_filter = self.get_remote_feature("GX_ENUM_PIXEL_COLOR_FILTER", "enum")
            if self.color_filter is not None:
                print(f"[camera] 传感器颜色滤镜: {self.color_filter} "
                      f"(1=RG 2=GB 3=GR 4=BG) — Bayer 转换以此为权威")
        except Exception as exception:
            print("错误:{}".format(exception))

    def get_remote_feature(self, feature_name, feature_type: str):
        """读特征；成功返回特征值，失败返回 None（上层统一转 -1/空串）。"""
        if self.cam is None:
            return None
        if isinstance(feature_name, str):
            if feature_name not in FEATURE_MAP:
                raise ValueError(f"未知的功能名称: {feature_name}")
            feature_id = FEATURE_MAP[feature_name]
        elif isinstance(feature_name, int):
            feature_id = feature_name
        else:
            raise TypeError("feature_name 必须是 str 或 int 类型")

        try:
            status, is_implemented = gx_is_implemented(self.cam, feature_id)
            if status != GxStatusList.SUCCESS or not is_implemented:
                return None
            status, is_readable = gx_is_readable(self.cam, feature_id)
            if status != GxStatusList.SUCCESS or not is_readable:
                return None

            if feature_type == "string":
                status, feature_value = gx_get_string(self.cam, feature_id)
            elif feature_type == "int":
                status, feature_value = gx_get_int(self.cam, feature_id)
            elif feature_type == "enum":
                status, feature_value = gx_get_enum(self.cam, feature_id)
            elif feature_type == "float":
                status, feature_value = gx_get_float(self.cam, feature_id)
            elif feature_type == "bool":
                status, feature_value = gx_get_bool(self.cam, feature_id)
            else:
                return None
            return feature_value if status == GxStatusList.SUCCESS else None
        except Exception as exception:
            print("错误:{}".format(exception))
            return None

    def set_remote_feature(self, feature_name, feature_type: str, set_value) -> bool:
        """写特征；成功返回 True，失败返回 False（调用方可感知采集失败）。"""
        if self.cam is None:
            return False
        if isinstance(feature_name, str):
            if feature_name not in FEATURE_MAP:
                raise ValueError(f"未知的功能名称: {feature_name}")
            feature_id = FEATURE_MAP[feature_name]
        elif isinstance(feature_name, int):
            feature_id = feature_name
        else:
            raise TypeError("feature_name 必须是 str 或 int 类型")

        try:
            status, is_implemented = gx_is_implemented(self.cam, feature_id)
            if status != GxStatusList.SUCCESS or not is_implemented:
                return False
            status, is_writable = gx_is_writable(self.cam, feature_id)
            if status != GxStatusList.SUCCESS or not is_writable:
                return False

            if feature_type == "string":
                status = gx_set_string(self.cam, feature_id, set_value)
            elif feature_type == "int":
                status = gx_set_int(self.cam, feature_id, set_value)
            elif feature_type == "enum":
                status = gx_set_enum(self.cam, feature_id, set_value)
            elif feature_type == "float":
                status = gx_set_float(self.cam, feature_id, set_value)
            elif feature_type == "bool":
                status = gx_set_bool(self.cam, feature_id, set_value in (True, "true", 1, "1"))
            elif feature_type == "command":
                status = gx_send_command(self.cam, feature_id)
            else:
                return False
            if status == GxStatusList.SUCCESS:
                print(f"{feature_name}设置成功")
                if feature_name == "GX_ENUM_PIXEL_FORMAT":
                    self.pixel_format = int(set_value)
                return True
            return False
        except Exception as exception:
            print("错误:{}".format(exception))
            return False

    def gather_start(self) -> bool:
        """开始连续采集。

        采集前依次：
        1. 关闭 DeviceLinkThroughputLimit（部分相机出厂限速 36MB/s，
           5MP 全幅会被卡在约 7.2fps）；
        2. 采集模式切 Continuous；
        3. 打开帧率控制，并重写一遍目标帧率（确保用户从 CameraPage
           写入的 GX_FLOAT_ACQUISITION_FRAME_RATE 生效）。
        """
        self._disable_throughput_limit()
        self.set_remote_feature("GX_ENUM_ACQUISITION_MODE", "enum", _GX_ACQ_MODE_CONTINUOUS)
        self.set_remote_feature("GX_ENUM_ACQUISITION_FRAME_RATE_MODE", "enum", _GX_FRAME_RATE_MODE_ON)

        target_fps = self.get_remote_feature("GX_FLOAT_ACQUISITION_FRAME_RATE", "float")
        if target_fps is not None and target_fps > 0:
            self.set_remote_feature(
                "GX_FLOAT_ACQUISITION_FRAME_RATE", "float", float(target_fps))
            print(f"[camera] 目标采集帧率: {target_fps:.2f} fps")
            # 曝光时间不能超过帧周期，否则目标帧率永远达不到。
            # 例：曝光 165508us → 最大帧率 ≈ 1000000/165508 ≈ 6.04fps，
            # 即使目标帧率写 29fps，相机实际也只有 6fps。
            self._fit_exposure_to_frame_rate(float(target_fps))
        else:
            print("[camera] 读取目标帧率失败，使用相机当前默认值")

        ok = self.set_remote_feature("GX_COMMAND_ACQUISITION_START", "command", None)
        self.is_gathering = ok
        return ok

    def _disable_throughput_limit(self):
        """关闭相机 USB 链路吞吐量限制。

        5MP 相机出厂默认限制为 36,000,000 B/s 时，最大帧率正好
        36e6 / (2448*2048) ≈ 7.2fps。Off=0 / On=1（大恒标准枚举）。
        """
        # 多 Link 设备先选 Link0；单 Link 相机若不实现该特征会返回 False，忽略即可
        self.set_remote_feature("GX_INT_DEVICE_LINK_SELECTOR", "int", 0)
        mode = self.get_remote_feature(
            "GX_ENUM_DEVICE_LINK_THROUGHPUT_LIMIT_MODE", "enum")
        limit = self.get_remote_feature(
            "GX_INT_DEVICE_LINK_THROUGHPUT_LIMIT", "int")
        if mode is None:
            return
        print(f"[camera] 吞吐量限制模式: {mode}, 限制值: {limit} B/s")
        if int(mode) != _GX_THROUGHPUT_LIMIT_MODE_OFF:
            ok = self.set_remote_feature(
                "GX_ENUM_DEVICE_LINK_THROUGHPUT_LIMIT_MODE", "enum",
                _GX_THROUGHPUT_LIMIT_MODE_OFF)
            if ok:
                print("[camera] 已关闭 DeviceLinkThroughputLimit，解除帧率限制")

    def _fit_exposure_to_frame_rate(self, target_fps: float):
        """曝光时间自动适配目标帧率。

        帧周期 = 曝光 + 读出时间。这里给读出留 10% 余量：
            max_exposure = 1_000_000 / target_fps * 0.9
        当前曝光超过该值时自动下调；否则即使 AcquisitionFrameRate 写对了，
        实际帧率仍被曝光时间卡住（165ms 曝光只能跑 ~6fps）。
        """
        if target_fps <= 0:
            return
        max_exposure = max(1000.0, 1_000_000.0 / target_fps * 0.9)
        exposure = self.get_remote_feature("GX_FLOAT_EXPOSURE_TIME", "float")
        if exposure is None:
            return
        if exposure > max_exposure:
            self.set_remote_feature(
                "GX_FLOAT_EXPOSURE_TIME", "float", float(int(max_exposure)))
            print(f"[camera] 曝光 {exposure:.0f}us 超过 {target_fps:.1f}fps 帧周期，"
                  f"已自动下调为 {int(max_exposure)}us")

    def gather_stop(self) -> bool:
        ok = self.set_remote_feature("GX_COMMAND_ACQUISITION_STOP", "command", None)
        self.is_gathering = False
        return ok

    def gather_status(self):
        return self.get_remote_feature("GX_BOOL_ACQUISITION_STATUS", "bool")

    def cam_close(self):
        try:
            if self.is_gathering:
                self.gather_stop()
            if self.cam is not None:
                status = gx_unregister_capture_callback(self.cam)
                if status == GxStatusList.SUCCESS:
                    print("回调注销成功")
                status = gx_close_device(self.cam)
                if status == GxStatusList.SUCCESS:
                    print(f"相机{self.cam_name}关闭成功")
        except Exception as exception:
            print("关闭相机异常:{}".format(exception))
        finally:
            self.cam = None
            self.is_gathering = False

    def capture_image(self, frame_param_ptr):
        frame_param = frame_param_ptr.contents
        pixel_format = int(frame_param.pixel_format)
        h = int(frame_param.height)
        w = int(frame_param.width)
        image_size = int(frame_param.image_size)

        # ── 8bit Bayer：优先按传感器颜色滤镜（GX_ENUM_PIXEL_COLOR_FILTER）选排列，
        # 读不到时回退帧内 pixel_format 查表 ──
        bayer_filter = None
        if self.color_filter is not None:
            bayer_filter = _COLOR_FILTER_TO_BAYER.get(self.color_filter)
            if bayer_filter is None:
                print(f"[camera] 未知颜色滤镜值 {self.color_filter}，回退 pixel_format 查表")
        if bayer_filter is None:
            bayer_filter = _PIXEL_FORMAT_TO_BAYER.get(pixel_format)
        if bayer_filter is not None:
            # Windows：大恒 DxImageProc.dll 的 bayer_type 语义与 Linux 相反，
            # 同一排列参数下 R/B 互换（黄蓝互换）。按诊断结果交换 RG↔BG、GB↔GR。
            if os.name == "nt":
                bayer_filter = _SWAP_RB_BAYER.get(bayer_filter, bayer_filter)
            if dx_raw8_to_rgb24 is not None:
                output_buffer = (c_ubyte * image_size * 3)()
                status = dx_raw8_to_rgb24(
                    frame_param.image_buf,
                    addressof(output_buffer),
                    w,
                    h,
                    DxBayerConvertType.NEIGHBOUR,
                    bayer_filter,
                    False
                )
                if status == GxStatusList.SUCCESS:
                    numpy_img = numpy.frombuffer(
                        output_buffer, dtype=numpy.uint8).reshape(h, w, 3)
                    self.image_captured.emit(numpy_img)
                return
            # arm64（Jetson）SDK 无 DxImageProc：优先 C 加速去马赛克
            # （libs_arm64/libbayer_demosaic.so，仅支持偶数尺寸），
            # 失败/奇数尺寸回退 numpy 双线性实现。
            # 注意此时 bayer_filter 是 Linux 语义（无 Windows R/B 交换）。
            if _BAYER_DEMOSAIC_C is not None and w % 2 == 0 and h % 2 == 0:
                out_buf = (c_ubyte * (image_size * 3))()
                rc = _BAYER_DEMOSAIC_C(frame_param.image_buf, out_buf, w, h,
                                       int(bayer_filter))
                if rc == 0:
                    self.image_captured.emit(
                        numpy.frombuffer(out_buf, dtype=numpy.uint8).reshape(h, w, 3))
                    return
                print(f"[camera] C 去马赛克失败(rc={rc})，回退 numpy")
            raw = numpy.frombuffer(
                (c_ubyte * image_size).from_address(frame_param.image_buf),
                dtype=numpy.uint8).reshape(h, w).copy()
            self.image_captured.emit(_bayer_demosaic_numpy(raw, bayer_filter))
            return

        # ── Mono8：灰度扩展为 RGB ──
        if pixel_format == GxPixelFormatEntry.MONO8:
            raw = numpy.frombuffer(
                (c_ubyte * image_size).from_address(frame_param.image_buf),
                dtype=numpy.uint8).reshape(h, w).copy()
            self.image_captured.emit(numpy.stack([raw, raw, raw], axis=2))
            return

        # ── 已是 RGB/BGR 的相机：直接拷贝（BGR 交换 R/B 通道）──
        if pixel_format in (GxPixelFormatEntry.RGB8, GxPixelFormatEntry.BGR8):
            raw = numpy.frombuffer(
                (c_ubyte * image_size).from_address(frame_param.image_buf),
                dtype=numpy.uint8).reshape(h, w, 3).copy()
            if pixel_format == GxPixelFormatEntry.BGR8:
                raw[..., [0, 2]] = raw[..., [2, 0]]
            self.image_captured.emit(raw)
            return

        if not self._unsupported_pixel_format_warned:
            self._unsupported_pixel_format_warned = True
            print(f"[camera] 不支持的像素格式 {pixel_format:#010x}，请在相机设置页改为 "
                  "BayerRG8/BayerGB8/Mono8")
