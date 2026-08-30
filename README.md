# DuAD Software

工业异常检测上位机（论文《融合 DINOv2 与双分支训练架构的工业异常检测》配套软件）。
技术栈：**PySide6 + QML**；功能：大恒相机采集、ONNX 实时推理（热力图/定位/异常分数）、ROI、MQTT 报警、CH340 光源控制、定时图像采集。

演示视频：https://github.com/user-attachments/assets/5f7297c6-ca6f-4562-90fe-056e4c92d23e

## 功能一览

| 页面 | 功能 |
|---|---|
| 相机设置 | 大恒相机搜索/连接/断开、分辨率（2448/1224，BINNING）/像素格式/曝光/增益/帧率 |
| 光源设置 | CH340 串口光源控制器，4 路亮度调节 |
| 通讯设置 | MQTT 云服务器连接、用户名密码/TLS 登录、测试消息 |
| 异常检测 | 实时采集 + ONNX 推理 + 热力图/定位图 + ROI + 全屏；侧栏测试推理区 |
| 图像采集 | 定时保存相机帧到指定目录 |
| 设置 | 主题/语言（简中/繁中/英文）/配色/使用说明/关于 |

推理链路：`相机帧 → CameraBridge → AlgorithmBridge.predict_frame(ONNX) → 异常分数 / jet 热力图 / 像素定位掩模`

---

## 三平台部署

### 1. Linux x86_64（开发机）

```bash
cd DuAD_software
bash setup_env.sh                          # 自动检测 NVIDIA 驱动 → 装 GPU/CPU 依赖

# 相机 USB 权限（之后重新插拔相机）
sudo cp backend/config/99-galaxy-dev.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger

# 提升内核 USB 缓冲上限（2448×2048 全幅采集必需；不提升则 U3VTL 报 -1010 启动失败）
sudo bash scripts/set_usbfs.sh

source DuAD_SoftwareContent/pyqml/bin/activate
python DuAD_SoftwareContent/main.py
```

相机诊断：`DuAD_SoftwareContent/pyqml/bin/python scripts/diag_camera.py [--fps-test]`

### 2. Windows x64

```powershell
# 创建 venv（Python 3.14）
uv venv DuAD_SoftwareContent/pyqml_win --python 3.14
uv pip install --python DuAD_SoftwareContent/pyqml_win/Scripts/python.exe -r requirements.txt

# GPU 推理（需 NVIDIA 驱动 ≥ 585）：onnxruntime-gpu 的 Windows wheel 不自带 CUDA 运行库
uv pip install --python DuAD_SoftwareContent/pyqml_win/Scripts/python.exe onnxruntime-gpu==1.28.0 nvidia-cublas-cu13 nvidia-cudnn-cu13 nvidia-cuda-runtime
# 可选 TensorRT 10.16：官网 zip 解压后 setx TENSORRT_LIB_DIR "<解压路径>\bin"

cd DuAD_SoftwareContent
pyqml_win\Scripts\python.exe -u main.py
```

相机 SDK 已自包含于 `backend/libs_win/`（DLL + GenTL 传输层），**无需安装大恒 SDK 或设置环境变量**；`gxwrapper.py` 自动本地优先加载。

### 3. Jetson（ARM64 / Orin NX）

```bash
bash run_jetson.sh        # 环境 ~/micromamba/envs/duad（conda-forge PySide6 6.11.2 + NVIDIA ORT-GPU 1.24）
```

- **当前基线 JetPack 7.2**（TRT 10.13，数值正确）：`run_jetson.sh` 已默认 `DUAD_PREFER_TRT=1` 走 TensorRT（~140–190ms/帧，带引擎缓存）。
- ❗ JetPack 6.2 的 TRT 10.3 有 DINOv2 数值 bug（分数恒 1e10），勿用 TRT。
- JP7.2（CUDA 13）无官方 ORT 包，需 cu12 pip 运行库补丁（见文档）。
- 相机权限：`sudo cp backend/config/99-galaxy-dev.rules /etc/udev/rules.d/` + reload + 重新插拔。
- 像素格式请选 8bit（`BayerRG8`/`Mono8`）。
- **打包分发给别的板卡**：`bash scripts/package_jetson.sh 1.0.0` → `dist/DuAD_1.0.0_Jetson_aarch64.tar.gz`（默认 CPU 推理：`bash install.sh` → `bash run_jetson.sh`；GPU 加速可选 `bash enable_gpu.sh` 加载 ONNX(CUDA)/TRT 依赖，详见 `docs/14` 第 8 节）。
- 部署步骤见 `docs/Jetson部署.md`；联调问题与修复见 `docs/16-Jetson问题与修复.md`。

---

## Release 包下载后使用（GPU / TensorRT 启用）

> GitHub Release 每次发布 3 个安装包 + `SHA256SUMS`（先 `sha256sum -c SHA256SUMS` 可校验完整性）。
> 推理后端说明：程序按 `onnxruntime.get_available_providers()` 自动选择——有 TensorRT 就 TRT 优先、
> 只有 CUDA 就 CUDA、都没有才 CPU；**无需手动切换**。GPU/TRT 是否真正生效，唯一凭证是
> 加载模型并开始推理后，启动日志的 `模型预热完成（['TensorrtExecutionProvider', 'CUDAExecutionProvider', ...]）`。

### Linux x64 — Installer 包（推荐正式安装）

```bash
tar -xzf DuAD_<版本>_Linux_x64_Installer.tar.gz && cd DuAD_<版本>_Linux_x64

bash setup_env.sh        # 自动检测 NVIDIA 驱动：有 → 装 GPU 依赖（onnxruntime-gpu + TensorRT + CUDA 13 库）；
                         #                   无 → 装 CPU 依赖。可 DUAD_INSTALL_GPU=0/1 强制。
# 相机 USB 权限（之后重新插拔相机）
sudo cp backend/config/99-galaxy-dev.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
# 提升内核 USB 缓冲上限（2448×2048 全幅采集必需）
sudo bash scripts/set_usbfs.sh

bash run.sh              # 启动；也可 python DuAD_SoftwareContent/main.py
```

- **GPU 机器**：`setup_env.sh` 装好后直接 TRT 加速（日志 `模型预热完成（['TensorrtExecutionProvider', ...]）` 即生效）。
- **显卡驱动需 ≥ 585**（CUDA 13 要求）；驱动不足会回退 CPU（日志只有 EP Error，属正常降级）。

### Linux x64 — CPU-Portable 包（解压即用 + 可选 GPU 升级）

```bash
tar --zstd -xf DuAD_<版本>_Linux_x64_CPU-Portable.tar.zst && cd DuAD_<版本>_Linux_x64
./run.sh                 # 内置 CPU venv，直接跑 CPU 推理
                         # ⚠️ 内置环境与构建机 Python 版本绑定：目标机版本不同时首次运行会自动
                         # 用本机 python3 重建（需联网约 3~5 分钟），之后正常

# 可选：在同一包内启用 GPU / TRT（检测到 NVIDIA 驱动则装 GPU 依赖到内置 venv）
bash setup_env.sh
sudo bash scripts/set_usbfs.sh
./run.sh
```

### Jetson（aarch64）— 默认 CPU，`enable_gpu.sh` 加载 GPU/TRT 依赖

```bash
tar -xzf DuAD_<版本>_Jetson_aarch64.tar.gz && cd DuAD_<版本>_Jetson_aarch64

bash install.sh          # ① 建环境（micromamba + conda-forge PySide6 + ORT）——默认 CPU 推理
sudo bash scripts/set_usbfs.sh    # 全幅 2448 采集建议执行
bash run_jetson.sh       # ② 启动（CPU）

# ③ 可选：加载 ONNX(CUDA)/TensorRT 依赖（JP7.2 的 cu12 运行库补丁，
#    main.py 自动注入 LD_LIBRARY_PATH，无需手动设环境变量）
bash enable_gpu.sh        # 完成后打印 provider：['TensorrtExecutionProvider', 'CUDAExecutionProvider', ...]
bash run_jetson.sh        # 重启生效，TRT ~140–190ms/帧（带引擎缓存，首次构建 ~43s）
```

- **JetPack ≥ 7.2（TRT 10.13）数值正确**，`run_jetson.sh` 已默认 `DUAD_PREFER_TRT=1` 优先 TRT。
- ❗ **JetPack 6.2 的 TRT 10.3 对 DINOv2 数值错误**（分数恒 1e10）：请注释 `run_jetson.sh` 中 `export DUAD_PREFER_TRT=1`（仍可用 CUDA）。
- 像素格式选 8bit（`BayerRG8`/`Mono8`）。

### Windows（后续发布）— onedir 主程序 + GPU 支持包

```powershell
# 解压 DuAD_<版本>_Windows_x64.zip → 双击 DuAD.exe（默认 CPU 推理）
# GPU 加速（可选）：解压 DuAD_GPU_runtime_<版本>_Windows_x64.zip，
#   把其中 nvidia\ 与 tensorrt\ 放到 DuAD.exe 同级目录（不放置则自动回退 CPU）
#   需显卡驱动 ≥ 585；TRT 生效看 %USERPROFILE%\DuAD_app.log 的 模型预热完成（[...]）
```

### 模型与检测

- 模型文件不随包分发：在「异常检测页 → 选择模型」加载自己的 `.onnx`（新模型自带阈值/尺度 metadata）。
- 全幅采集：Linux 需先 `sudo bash scripts/set_usbfs.sh`；分辨率预设 2448/1224 走 BINNING（视野不变、画面变糊省带宽）。

---

## 模型

- 模型文件不入库（`*.onnx` 等已 gitignore）；在「异常检测页 → 选择模型」手动加载 `.onnx`。
- 新模型自带阈值/热力图尺度 metadata（`duad.image_threshold` 等）；旧模型配对 `*.onnx.threshold.json` / `*.onnx.scale.json`。
- 训练/导出/标定代码在算法仓库：https://github.com/ouyangpingning/DuAD

## 打包发布

- Windows：`DuAD_SoftwareContent\pyqml_win\Scripts\python.exe -u scripts\package_win.py 1.0.0 [--with-gpu]`（详见 `docs/15-Windows打包与GPU加速.md`）
- Linux：`bash scripts/package.sh 1.0.0`（Installer 包 / CPU-Portable 解压即用包；**CPU-Portable 内置环境与构建机 Python 版本绑定，目标机版本不同时 `run.sh` 首次运行会自动用本机 python3 重建环境（需联网约 3~5 分钟）**，详见 `docs/14-打包与GitHub-Release发布.md`）
- Jetson：`bash scripts/package_jetson.sh 1.0.0`（默认 CPU，`enable_gpu.sh` 可选 GPU，详见 `docs/14` 第 8 节）
- 推送 `v*` 标签触发 GitHub Actions 自动构建 Release（含 Linux x64 ×2 + Jetson ×1 + SHA256SUMS）

## 文档

- 开发文档索引：`docs/00-目录.md`（架构/组件/页面/推理链路/i18n/打包等 16 篇）
- 硬件：相机大恒 MER2-501-79U3C-L（USB3 Vision）；光源 CH340 串口控制器

## 许可

许可见 [LICENSE](LICENSE)。本仓库仅包含上位机软件与运行模块；数据集、模型权重不包含在仓库内。