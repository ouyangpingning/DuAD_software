# DuAD Software

融合 DINOv2 与双分支训练架构的工业异常检测上位机。

技术栈：`PySide6 + QML`，后端包含大恒相机采集、ONNX 实时推理、MQTT 报警、CH340 光源控制与定时图像采集。

## 演示视频

<!-- 在 GitHub 网页端编辑本文件，将 Video_2026-08-17_16-38-40.mp4 拖入此区域即可内嵌播放 -->
<!-- <video src="https://github.com/user-attachments/assets/REPLACE_WITH_UPLOADED_ID" controls></video> -->

https://github.com/user-attachments/assets/5f7297c6-ca6f-4562-90fe-056e4c92d23e


---

## 1. 功能总览

| 页面 | 功能 | 状态 |
|---|---|---|
| 相机设置 | 大恒相机搜索/连接/断开、分辨率/像素格式/曝光/增益/帧率 | ✅ 真实相机 |
| 光源设置 | CH340 串口光源控制器，4 路亮度调节 | ✅ 串口指令 |
| 通讯设置 | MQTT 云服务器连接、用户名密码/TLS 登录、测试消息 | ✅ paho-mqtt |
| 异常检测 | 实时采集 + ONNX 推理 + 热力图/定位图 + ROI + 全屏 | ✅ 完整联调 |
| 图像采集 | 定时保存相机帧到指定目录 | ✅ 完整联调 |
| 设置 | 主题/语言/配色/使用说明/关于 | ✅ |

核心推理链路：

```text
大恒相机帧
  → CameraBridge
  → AlgorithmBridge.predict_frame (ONNX)
  → 异常分数 / jet 热力图 / 像素定位掩模
```

---

## 2. 目录结构

```text
DuAD_software/
├── DuAD_SoftwareContent/            # 前端入口与全部 QML
│   ├── main.py                      # 程序入口（Python 桥层）
│   ├── App.qml / MainuiRoot.qml
│   ├── DuAD_Software/               # QML 单例模块（Colors/Constants）
│   ├── pages/                       # 6 个功能页面
│   ├── components/                  # 通用组件
│   ├── minipages/                   # 页面卡片/面板
│   ├── images/ fonts/
│   └── pyqml/                       # 虚拟环境（不提交，setup_env.sh 重建）
├── backend/
│   ├── Src/
│   │   ├── camera.py                # 大恒相机驱动（低层）
│   │   ├── camera_bridge.py         # QML 相机桥 + ROI + frameProvider
│   │   ├── algorithm_bridge.py      # ONNX 推理桥（测试 + 实时）
│   │   ├── realtime_detect_bridge.py# 实时检测管线
│   │   ├── collect_bridge.py        # 图像采集保存管线
│   │   ├── light_bridge.py          # 光源串口桥
│   │   ├── mqtt_bridge.py           # MQTT 云服务器桥
│   │   └── frame_provider.py        # QML ImageProvider
│   ├── gxipy/                       # 大恒 Galaxy SDK Python wrapper
│   ├── libs/                        # 大恒 Linux x86_64 动态库（已提取）
│   ├── libs_win/                     # 大恒 Windows x64 动态库（自包含，已集成）
│   ├── config/99-galaxy-dev.rules   # USB 权限规则
│   ├── model_scales/                # 热力图固定显示尺度
│   ├── alg/                         # DuAD 训练/导出/标定算法代码
│   └── env.py                       # 测试脚本 SDK 环境注入
├── tests/                           # 无相机/无硬件冒烟测试
├── scripts/
│   ├── gen_translations.py          # i18n 翻译生成
│   ├── diag_camera.py               # 相机现场诊断工具
│   ├── package_win.py               # Windows 主程序打包（--with-gpu 附带 GPU 支持包）
│   ├── collect_gpu_dlls_win.py      # 收集/更新 Windows GPU 支持包
│   └── DuAD_win.spec                # PyInstaller spec（排除 GPU 库）
├── translations/                    # 英/繁中翻译
├── docs/                            # 详细开发文档
├── setup_env.sh                     # 一键环境配置脚本
├── requirements.txt                 # 基础依赖
├── requirements-gpu.txt             # GPU 推理依赖（当前开发机）
├── requirements-cpu.txt             # CPU 推理依赖
└── README.md
```

---

## 3. 环境要求

### 当前已验证平台

```text
Linux x86_64：Python 3.14.6、PySide6 6.11.1、onnxruntime-gpu 1.28.0 + TensorRT（开发机）
Windows x64：Python 3.14.5、PySide6 6.11.1、onnxruntime-gpu 1.28.0 + nvidia-cublas-cu13/nvidia-cudnn-cu13（需 NVIDIA 驱动 ≥ 585）+ 可选 TensorRT 10.16（手动安装，TENSORRT_LIB_DIR 指定）
相机：大恒 MER2-501-79U3C-L（USB3 Vision）
光源：CH340 USB 串口控制器
```

### Python 依赖版本

| 包 | 版本 | 用途 |
|---|---|---|
| PySide6 | 6.11.1 | QML GUI |
| numpy | 2.5.2 | 图像/推理数据 |
| Pillow | 12.3.0 | 图像读取与保存 |
| pyserial | 3.5 | 光源串口 |
| paho-mqtt | 2.1.0 | MQTT 云通信 |
| onnxruntime-gpu | 1.28.0 | GPU 推理（可选 CPU） |
| tensorrt_cu13_libs | 见 requirements-gpu.txt | TensorRT 加速（仅 GPU 包，不随发布包打包） |

---

## 4. 一键配置环境

### 自动检测 NVIDIA 驱动（推荐）

```bash
cd DuAD_software
bash setup_env.sh
```

检测到 NVIDIA 驱动时安装 GPU 推理依赖（onnxruntime-gpu + TensorRT）；
未检测到时自动安装 CPU 推理依赖。

### 强制指定 CPU / GPU

```bash
# 仅 CPU 推理
DUAD_INSTALL_GPU=0 bash setup_env.sh

# 强制 GPU 推理依赖
DUAD_INSTALL_GPU=1 bash setup_env.sh
```

### 相机 USB 权限

```bash
sudo cp backend/config/99-galaxy-dev.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```

重新插拔相机。

### Windows 环境配置

在 Windows 上重建 venv（同样适用于 Linux，只是目录名不同）：

```powershell
# 在仓库根目录执行，用 uv 或 python -m venv 创建 Windows venv
uv venv DuAD_SoftwareContent/pyqml_win --python 3.14
uv pip install --python DuAD_SoftwareContent/pyqml_win/Scripts/python.exe -r requirements.txt
# 有 NVIDIA 显卡（驱动 ≥ 585）则加 GPU 推理依赖：
# onnxruntime-gpu 主包 + CUDA 运行库（onnxruntime-gpu 的 Windows wheel 不自带 cuBLAS/cuDNN）
uv pip install --python DuAD_SoftwareContent/pyqml_win/Scripts/python.exe onnxruntime-gpu==1.28.0
uv pip install --python DuAD_SoftwareContent/pyqml_win/Scripts/python.exe nvidia-cublas-cu13 nvidia-cudnn-cu13 nvidia-cuda-runtime
# 可选 TensorRT 10.16（Windows 无 pip 库包，需手动从 NVIDIA 官网下载 zip 解压），
# 设置用户环境变量指向其 bin 目录（main.py/onnx_infer.py 会自动注入 PATH）：
#   setx TENSORRT_LIB_DIR "解压路径\TensorRT-10.16.x\bin"
```

如果不用 uv，也可以用普通 pip： `python -m venv DuAD_SoftwareContent/pyqml_win` 后 `Scripts\python.exe -m pip install -r requirements.txt`。

> Windows 相机 SDK 已随项目提供（`backend/libs_win/`），无需单独安装（见第 5 节）。


### 启动

Linux:

```bash
python DuAD_SoftwareContent/main.py
# 或：DuAD_SoftwareContent/pyqml/bin/python DuAD_SoftwareContent/main.py
```

Windows（在 `DuAD_SoftwareContent/` 目录内）:

```powershell
pyqml_win\Scripts\python.exe -u main.py
```

> main.py 顶部会按平台自动切换到对应 venv（Linux `pyqml/`、Windows `pyqml_win/`）；
> Windows 下需先安装大恒 Windows 版 Galaxy 相机 SDK 并设置 `GALAXY_GENICAM_ROOT`（见第 5 节）。

### 桌面图标（双击启动）

解压发布包后执行一次：

```bash
bash install-desktop.sh
```

之后即可在应用菜单搜索 **DuAD** 双击启动。

### 相机诊断

```bash
DuAD_SoftwareContent/pyqml/bin/python scripts/diag_camera.py
```

实测帧率：

```bash
DuAD_SoftwareContent/pyqml/bin/python scripts/diag_camera.py --fps-test
```

---

## 5. 平台兼容性说明

| 平台 | 状态 |
|---|---|
| Linux x86_64 | ✅ 已开发验证 |
| Windows x64 | ✅ 已适配并支持打包（Python 3.14 + PySide6/onnxruntime-gpu Windows wheel）；相机 SDK 已自包含于 `backend/libs_win/`，无需安装 SDK 或设环境变量；`main.py` 已跨平台（Windows 用 `pyqml_win/` venv）；GPU 推理需 NVIDIA 驱动 ≥ 585 + `nvidia-cublas-cu13`/`nvidia-cudnn-cu13`，TensorRT 可选 |
| Linux ARM | ⚠️ 未测试；需要大恒 Linux ARM 版 `libgxiapi.so`、`.cti` 传输层和匹配的 `gxipy`，PySide6/onnxruntime 是否提供 ARM wheel 也需确认 |

`backend/libs/` 和 `backend/gxipy/` 来自 **Galaxy Linux x86_64 SDK**（Linux 用）；Windows 走安装的大恒 Windows SDK（`GxIAPI.dll` + `GALAXY_GENICAM_ROOT`）。


### Windows 相机设置（项目已自包含）

Windows 大恒 SDK 已集成到项目 `backend/libs_win/`（`gxwrapper.py`/`dxwrapper.py` Windows 分支自动优先从这里加载 DLL 并设置环境变量），**无需手工安装 SDK 或设置任何环境变量**。

`backend/libs_win/` 结构（与官方 Galaxy SDK 一致）：
- `APIDll/Win64/` — `GxIAPI.dll`、`DxImageProc.dll` 及 VC 运行库
- `GenICam/bin/Win64_x64/` — GenApi 等 GenICam 运行时
- `GenTL/Win64/` — `.cti` 传输层（GxU3VTL 等）

> 联动注意：GXInitLib 找 GenTL 传输层走环境变量 **`GENICAM_GENTL64_PATH`**（大恒官方安装器设置的就是这个名字，不是 `GX_` 前缀）；缺它 `gx_init_lib` 返回 -1、报 “Failed to get GenTL path”。项目里 `gxwrapper.py` 本地优先分支已自动设为 `backend/libs_win/GenTL/Win64`。

> 降级说明：完全没放 `libs_win/` 时，程序仍正常启动，但相机功能自动不可用（降级 stub）。

---

## 6. 模型文件说明

模型文件不包含在仓库中（`.gitignore` 已排除 `*.onnx`、`*.ckpt` 等大文件）。

模型放置后，在“异常检测页 → 测试推理 → 选择模型”中手动选择 `.onnx`。

推荐使用训练期已写入 metadata 的新模型，包含：

```text
duad.image_threshold     图像级异常阈值
duad.pixel_threshold     像素级 F1 阈值
duad.pixel_f1_max        像素 F1 最大值
duad.heatmap_vmin/vmax   热力图固定显示尺度
duad.pca_flip            PCA 方向
```

旧模型可配合同目录下：

```text
<模型名>.onnx.threshold.json
<模型名>.onnx.scale.json
```

---

## 7. 快速使用流程

```text
1. 相机设置页：
   搜索 → 连接大恒相机 → 调整分辨率/曝光/增益/帧率

2. 异常检测页：
   选择 ONNX 模型 → 开始采集 → 开启算法推理
   可选：F1 阈值定位 / 精细定位 / ROI / 全屏

3. 图像采集页：
   齿轮设置保存目录/间隔/格式 → 开始采集 → 自动写盘

4. 光源设置页：
   选择串口 → 连接 → 调节 4 路亮度

5. 通讯设置页：
   填写 Broker 地址/端口/用户名/密码/TLS → 连接 → 发送测试消息
```

---

## 8. 测试

```bash
cd DuAD_software
export QT_QPA_PLATFORM=offscreen
PY=DuAD_SoftwareContent/pyqml/bin/python

$PY tests/test_camera_bridge.py
$PY tests/test_camera_link.py
$PY tests/test_detect_pipeline.py
$PY tests/test_collect_pipeline.py
$PY tests/test_light_bridge.py
$PY tests/test_mqtt_bridge.py
```

`tests/test_infer.py` 需要本机存在可用的 ONNX 模型。

---

## 9. 打包与 GitHub Release

### 打包策略

> 详细说明书见 [docs/14-打包与GitHub-Release发布.md](docs/14-打包与GitHub-Release发布.md)。

- GPU 系统动态库（`nvidia-*-cu13`、`tensorrt_libs`，约 6GB）**不打包**；
  有 NVIDIA 驱动的用户运行 `setup_env.sh` 时自动从 PyPI 安装 GPU 依赖，
  无驱动用户安装 CPU 依赖。
- 发布两种 Linux x64 产物：
  - `DuAD_<版本>_Linux_x64_Installer.tar.gz`：应用 + 安装脚本，不含 Python 依赖。
  - `DuAD_<版本>_Linux_x64_CPU-Portable.tar.zst`：内置 CPU 推理 venv，解压即用。
- 两种包均含 `run.sh` 启动脚本和 `install-desktop.sh` 桌面图标安装脚本。
- 运行时若检测到 NVIDIA 驱动但当前环境只有 CPU 依赖，会在日志中提示升级。

### 本地打包

```bash
bash scripts/package.sh 1.0.0
```

产物输出到 `dist/`。跳过 venv 构建（快速验证）可运行：

```bash
DUAD_SKIP_VENV=1 bash scripts/package.sh 1.0.0
```

### 发布到 GitHub Release

推送 `v*` 标签后，GitHub Actions 会自动构建并上传 Release 资产：

```bash
git tag v1.0.0
git push origin v1.0.0
```

也可以手动上传 `dist/` 下的 tar 包与 `SHA256SUMS`。

### Windows 打包（PyInstaller onedir）

> 详细说明书见 [docs/15-Windows打包与GPU加速.md](docs/15-Windows打包与GPU加速.md)。

Windows 用 PyInstaller 打包成 onedir 文件夹（`DuAD.exe` 双击启动）。
GPU 库（CUDA 运行库 / TensorRT / 驱动）**不随主程序打包**：内置 onnxruntime-gpu
主包带 CUDA/TRT provider，无 GPU 库时自动回退 CPU；有 GPU 的目标机把「GPU 支持包」
解压到 exe 同目录即可自动加速。

```powershell
# 在仓库根目录执行
# 只打主程序（CPU-only，体积小）
DuAD_SoftwareContent\pyqml_win\Scripts\python.exe -u scripts\package_win.py 1.0.0

# 打主程序 + GPU 支持包
DuAD_SoftwareContent\pyqml_win\Scripts\python.exe -u scripts\package_win.py 1.0.0 --with-gpu
```

产物输出到 `dist/`：

```text
dist\DuAD\                                    # onedir 应用（双击 DuAD.exe）
dist\DuAD_1.0.0_Windows_x64.zip               # 主程序（CPU-only）
dist\DuAD_GPU_runtime_1.0.0_Windows_x64.zip   # GPU 支持包（--with-gpu 时生成）
dist\SHA256SUMS                               # 校验和
```

> GPU 支持包用法：解压得到 `nvidia\` 和 `tensorrt\`，放到 `DuAD.exe` 同级目录，
> 启动日志出现 `模型预热完成（['TensorrtExecutionProvider', ...]）` 即启用 GPU；
> 未放置支持包则回退 CPU，程序仍可运行。显卡驱动需 ≥585（CUDA 13 要求），
> 须在目标机单独安装。
>
> 打包环境：需要 `pyqml_win` venv 里装 PyInstaller
> （`pip install pyinstaller`）。exe 为 windowed 无控制台模式，运行日志写到
> `%USERPROFILE%\DuAD_app.log`。相机 SDK（`backend/libs_win`）、QML、翻译均
> 已内置。

### 发布到 GitHub Release

```bash
git add -A
git commit -m "release: 添加 Windows 打包"
git push origin main
git tag v1.0.0
git push origin v1.0.0
```

也可以在 GitHub 网页端 Release → Draft a new release，手动上传
`dist/` 下的 Windows zip、Linux tar 包与 `SHA256SUMS`。

---

## 10. 常见问题

### 相机枚举不到

1. 确认 udev 规则已安装并重新插拔；
2. 确认 `/dev/bus/usb/` 下有设备节点；
3. 确认 `backend/libs/GxU3VTL.cti` 等传输层文件齐全。

### 帧率低

- 检查曝光时间是否超过帧周期，采集启动会自动适配；
- 检查 `DeviceLinkThroughputLimit` 是否关闭；
- `lsusb -t` 确认相机在 `5000M` USB3 链路上。

### 画面黄/蓝互换

- 已按帧内 `pixel_format` 自动选择 Bayer RG/GB/GR/BG；
- 相机设置页可选择 `BayerRG8 / BayerGB8 / BayerGR8 / BayerBG8 / Mono8`。

### ROI 应用后黑屏

- ROI 写入时会自动停流→写参数→延迟重开采集；
- 如仍失败，查看终端 `[CameraBridge]` 日志。

---

## 11. 许可与说明

本仓库仅包含上位机软件代码与算法运行模块；训练数据集、模型权重不包含在仓库内。