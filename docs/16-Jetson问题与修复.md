# 16 Jetson 板卡问题与修复

> 部署步骤与环境基线见 [Jetson部署.md](Jetson部署.md)；本文记录联调中遇到的**每一个问题 → 根因 → 修复/绕过**，重装系统或排障时按条目对照。
>
> **当前基线：JetPack 7.2（TRT 10.13 / CUDA 13），由 JetPack 6.2（TRT 10.3）升级而来。**
> 软件栈：`~/micromamba/envs/duad`（conda-forge PySide6 6.11.2，Python 3.10）+ `onnxruntime_gpu 1.24.0`（`pypi.jetson-ai-lab.io/jp6/cu126`）+ cu12 pip 运行库补丁。

---

## 1. TRT 推理结果完全错误（哨兵值 1e10）→ 升级 JetPack

- **现象**：JetPack 6.2 自带的 **TRT 10.3.0.30** 上，新模型（后处理内化）fp32 引擎分数恒为 `1e10` 哨兵（PCA 质量门控被算反）；fp16 直接溢出 `65504`；`--noTF32`、builder level 0 无效；`trtexec` 绕过 ORT 直建引擎结果同样错误 → **确认是 TRT 10.3 内核问题，与 ORT 层无关**。
- **过程对策**：aarch64 上 `onnx_infer.py` 默认优先 `CUDAExecutionProvider`（x86 仍 TRT 优先），实测 CUDA EP 366ms/帧、数值与训练侧 PyTorch 一致（±1e-3）；`DUAD_PREFER_TRT=1` 可强制 TRT 做对照验证。
- **最终修复**：升级 **JetPack 7.2（TRT 10.13）**，TRT 数值正确（缺陷/好图分数与 CUDA 差 ~0.001、无哨兵），且更快（~140–190ms/帧）。`run_jetson.sh` 已默认 `export DUAD_PREFER_TRT=1`。

## 2. JetPack 7.2（CUDA 13）装不上官方 onnxruntime

- **现象**：NVIDIA 官方 Jetson 索引 `pypi.jetson-ai-lab.io` 只有 `jp6/cu126|cu128|cu129`，**没有 jp7/cu13**（404 “stage could not be found”）；微软 1.29.0 release 的 aarch64 只有 CPU wheel。
- **修复（cu12 pip 库补丁）**：用为 CUDA 12.6 编译的 `onnxruntime-gpu 1.24.0`，再补装 6 个 cu12 运行库：

  ```bash
  ~/micromamba/bin/micromamba run -p ~/micromamba/envs/duad pip install \
    nvidia-cuda-runtime-cu12 nvidia-cublas-cu12 nvidia-cudnn-cu12 \
    nvidia-cufft-cu12 nvidia-curand-cu12 nvidia-nvrtc-cu12
  ```

  并把 `site-packages/nvidia/*/lib` 注入 `LD_LIBRARY_PATH`（`main.py` 已自动 glob + 重启进程）。不装则 CUDA/TRT EP 加载失败静默回退 CPU。属“拼装”方案，等 NVIDIA 出 JP7 正式包再换。

## 3. TRT 引擎每次启动重建（~43s）

- **修复**：`onnx_infer.py` 在 aarch64 的 TRT `provider_options` 加：
  `trt_engine_cache_enable=True` + `trt_engine_cache_path=~/.cache/duad_trt_engine`。
  首次构建 ~43s 落盘，之后启动 ~1.3s。x86 保持最小 provider_options（不回退 CPU）。

## 4. FlClash 界面中文全是方框

- **排查**：字体/系统 locale 均正常，是 FlClash 自身 UI 的 CJK 渲染问题。
- **结论**：放弃中文界面，改用英文界面，不影响代理（监听 127.0.0.1:7890，需在 GUI 里开启才生效）。

## 5. xrdp / Remmina 远程连接闪退

三个叠加根因，逐个修复：

1. `~/.xsessionrc` 里写了 bash 数组语法 `remove_apps=(...)`，被 POSIX `sh` 解析失败 → 破坏 `/etc/X11/Xsession` 启动。**修复**：改成 POSIX `for app in ...; do ...; done` 循环。
2. GNOME 在 xrdp 下报 “Session manager already running” 退会话。**修复**：安装 XFCE，`~/.xsession` 写 `xfce4-session`，重启 xrdp。
3. `xrdp` 读 key.pem 报 Permission denied。**修复**：`sudo adduser xrdp ssl-cert && sudo systemctl restart xrdp`。

## 6. GNOME Wayland 弃用

- Remmina/X11 + XFCE 会话稳定；确认不引入 GNOME Wayland（性能/远程兼容无收益）。

## 7. 相机枚举不到 / log4cplus 告警

- **枚举 0 台**：`/etc/udev/rules.d/99-galaxy-dev.rules` 必须安装且**重新插拔**相机；`backend/libs_arm64/` 的 `.cti` 传输层文件必须齐全（缺传输层 `gx_init_lib` 返回 -1）。
- `log4cplus: could not open file /etc/Galaxy/cfg/log4cplus.properties` —— SDK 日志配置缺失的**无害告警**，忽略。

## 8. SSH 客户端连板子报错（开发机侧）

- `/etc/ssh/ssh_config.d/20-systemd-ssh-proxy.conf` 属主异常（nobody）导致 ssh 命令报错。**绕过**：用 `sshpass -p '...' ssh -F /dev/null ...` 忽略系统 ssh_config。

## 9. arm64 SDK 没有 DxImageProc → Bayer 去马赛克自实现

- **现象**：libgxiapi.so 不导出 `DxRaw8toRGB24`，gxipy 的 `dx_raw8_to_rgb24` 不可用，Bayer 转换缺失。
- **修复**：`camera.py` 优先走 `backend/libs_arm64/libbayer_demosaic.so`（自编 C，仅支持偶数尺寸），失败回退 `_bayer_demosaic_numpy`（含奇数尺寸兜底），两者与 DxImageProc NEIGHBOUR 双线性语义一致（边缘复制）。改逻辑需重编译：
  `gcc -shared -fPIC -O2 -o backend/libs_arm64/libbayer_demosaic.so backend/libs_arm64/bayer_demosaic.c`

## 10. ROI “应用失败/黑屏”（OFFSET 越界 INVALID_ACCESS）

- **根因**：大恒约束 `offset + 当前宽度 ≤ 传感器最大宽度`。相机全幅（2448）时先写 OFFSET（如 680）→ `680+2448>2448` 被拒，报“ROI 应用失败，已恢复原几何参数”。
- **修复**：`camera_bridge._writeGeometry` 自适应写序——**缩小**（新 W/H ≤ 当前）：先 WIDTH/HEIGHT 再 OFFSET；**放大**（恢复全幅）：先 OFFSET 再 WIDTH/HEIGHT。写后读回校验，仍失败恢复旧几何。

## 11. 设置分辨率后画面“缩放”（窗口裁剪 vs BINNING）

- **根因**：曾用 OFFSET+WIDTH 做“居中裁剪”缩分辨率，那只读出 sensor 中间一块 → 视野缩小、画面放大 2 倍（“在 2448 基础上缩放”），两侧视野丢失。
- **修复**：`applyResolution` 改用 **BINNING**（同旧版 pyqt5 的 `GX_INT_BINNING_HORIZONTAL/VERTICAL`）：binning N 输出 = sensor/N，**视野保持全幅、画面只变模糊**（省带宽、推理更快）。binning 系数按 `GX_INT_SENSOR_*` 推算，设完 binning 后重读 `WidthMax` 钳定输出；分辨率预设仅保留 2448（bin 1）/ 1224（bin 2）。

## 12. QML 显示“分辨率应用: … (失败)”实际却成功

- **根因**：`applyResolution` 声明为 `@Slot(int, int)` **漏了 `result=bool`**，PySide6 不把返回值传回 QML，前端恒判失败。
- **修复**：`@Slot(int, int, result=bool)`（与 `startGather` 的 `@Slot(result=bool)` 一致）。

## 13. Qt 6.11 信号参数注入弃用告警

- 日志：`Parameter "index" is not declared. Injection of parameters into signal handlers is deprecated.`
- **修复**：`onActivated: root._applyResolution(index)` → `onActivated: function(index) { root._applyResolution(index) }`（组件内 ComboBox 已是此写法；ComboRow/SliderRow 等自定义信号同理）。

## 14. 分辨率/ROI 交互要点

- `applyResolution` 成功后记录 `_roiBaseline`（设定分辨率）；`resetRoi`（恢复全幅）**回到设定分辨率**（如 1224×1024），而不是直接跳传感器最大 2448×2048——避免恢复后推理变慢。
- 1224（binning 2）下画面尺寸 1224×1024 = 整幅视野合成；ROI 坐标按当前输出尺寸归一化，binning 模式下 offset 恒 0。

---

## 验证清单（升级 JP7.2 后）

```bash
# TRT/CUDA/CPU 三个 provider 都在
~/micromamba/envs/duad/bin/python -c "import onnxruntime as ort; print(ort.get_available_providers())"

# TRT 数值对照（分数应与 CUDA 一致、无 1e10 哨兵）
DUAD_PREFER_TRT=1 ~/micromamba/envs/duad/bin/python -u backend/Src/onnx_infer.py <模型.onnx> <测试图.png>

# 相机采集与分辨率链路
~/micromamba/envs/duad/bin/python scripts/diag_camera.py
```
## 15. 采集启动失败（TL -1010 "Unable to start acquisition"）

- **现象**：`开始采集` 无反应，日志 `[CameraBridge] 相机错误: 相机采集启动失败`；
  原始错误为 U3VTL 传输层 -1010（`gx_get_last_error`：`TL Error: Unable to start acquisition`）。
- **两类根因**（务必区分）：
  1. **分辨率/ROI 变更后流缓冲未刷新**（板子会遇到的场景）：负载从半幅(1224×1024)恢复全幅(2448×2048)
     时 START 被拒。**修复**：`CameraBridge.startGather` 失败时自动「重注册采集回调」重建
     流缓冲并重试一次（板端验证有效）。
  2. **内核 usbfs_memory_mb=16MB 过小**（开发机 x86 全幅必现；大恒官方 FAQ
     「USB3 相机开采失败」的根因与解法）：U3VTL 分配缓冲环时被 USB 缓冲内存上限
     卡住 → -1010。**修复**：`sudo bash scripts/set_usbfs.sh`
     （等价官方 `SetUSBStack.sh`：`echo 1000 > /sys/module/usbcore/parameters/usbfs_memory_mb`，
     重启失效，可写 GRUB 参数 `usbcore.usbfs_memory_mb=1000` 持久化）。
     arm64 单相机在 16MB 下恰好够用（板子一直正常），多相机场景同样需要提升。
     应用侧：-1010+大负载时直接提示执行 `set_usbfs.sh`（不再无意义重试）。
- **诊断**：`gather_start` 失败现在会打印原始错误串（`[camera] ACQUISITION_START 失败 — code=-1010 …`）。

## 16. TRT fp16 引擎（JP7.2 已实测可用，2 倍提速）

- **背景**：JetPack 6.2（TRT 10.3）时 fp16 引擎数值溢出（65504），文档记载「勿用」。
  升级 JP7.2（TRT 10.13）后 TRT 版本完全不同，fp16 是否可用需重新验证。
- **实测（zipper_k4_s0_full.onnx，6 张 缺陷/好图 ×5 次）**：

  | 指标 | TRT fp32 | TRT fp16 |
  |---|---|---|
  | 推理耗时 | ~180–195ms/帧 | **~91–93ms/帧（≈2.07× 提速）** |
  | 分数偏差 | — | 最大 |Δ| ≈ 0.012（远小于阈值判别，无哨兵/溢出） |

- **启用**：`export DUAD_TRT_FP16=1`（`run_jetson.sh` 已默认设置；onnx_infer.py 据此给
  TRT provider 加 `trt_fp16_enable=True`）。
- **引擎缓存分目录**：fp16 走 `~/.cache/duad_trt_engine_fp16`，fp32 走
  `~/.cache/duad_trt_engine`——ORT 缓存名不含 fp16 标记，混用目录会用错引擎，勿改。
- ⚠️ JetPack 6.2 勿开（TRT 10.3 fp16 溢出）；每个新模型建议先用
  `DUAD_TRT_FP16=0` 跑基准分，再开 fp16 对比偏差 <0.05 再采用。
