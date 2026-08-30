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

## 模型

- 模型文件不入库（`*.onnx` 等已 gitignore）；在「异常检测页 → 选择模型」手动加载 `.onnx`。
- 新模型自带阈值/热力图尺度 metadata（`duad.image_threshold` 等）；旧模型配对 `*.onnx.threshold.json` / `*.onnx.scale.json`。
- 训练/导出/标定代码在算法仓库：https://github.com/ouyangpingning/DuAD

## 打包发布

- Windows：`DuAD_SoftwareContent\pyqml_win\Scripts\python.exe -u scripts\package_win.py 1.0.0 [--with-gpu]`（详见 `docs/15-Windows打包与GPU加速.md`）
- Linux：`bash scripts/package.sh 1.0.0`（Installter 包 / CPU-Portable 解压即用包；**CPU-Portable 内置环境与构建机 Python 版本绑定，目标机版本不同时 `run.sh` 首次运行会自动用本机 python3 重建环境（需联网约 3~5 分钟）**，详见 `docs/14-打包与GitHub-Release发布.md`）
- Jetson：`bash scripts/package_jetson.sh 1.0.0`（默认 CPU，`enable_gpu.sh` 可选 GPU，详见 `docs/14` 第 8 节）
- 推送 `v*` 标签触发 GitHub Actions 自动构建 Release（含 Linux x64 ×2 + Jetson ×1 + SHA256SUMS）

## 文档

- 开发文档索引：`docs/00-目录.md`（架构/组件/页面/推理链路/i18n/打包等 16 篇）
- 硬件：相机大恒 MER2-501-79U3C-L（USB3 Vision）；光源 CH340 串口控制器

## 许可

许可见 [LICENSE](LICENSE)。本仓库仅包含上位机软件与运行模块；数据集、模型权重不包含在仓库内。