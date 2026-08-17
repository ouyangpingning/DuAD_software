# DuAD Software

融合 DINOv2 与双分支训练架构的工业异常检测上位机。

技术栈：`PySide6 + QML`，后端包含大恒相机采集、ONNX 实时推理、MQTT 报警、CH340 光源控制与定时图像采集。

## 演示视频

<!-- 在 GitHub 网页端编辑本文件，将 Video_2026-08-17_16-38-40.mp4 拖入此区域即可内嵌播放 -->
<!-- <video src="https://github.com/user-attachments/assets/REPLACE_WITH_UPLOADED_ID" controls></video> -->

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
│   ├── config/99-galaxy-dev.rules   # USB 权限规则
│   ├── model_scales/                # 热力图固定显示尺度
│   ├── alg/                         # DuAD 训练/导出/标定算法代码
│   └── env.py                       # 测试脚本 SDK 环境注入
├── tests/                           # 无相机/无硬件冒烟测试
├── scripts/
│   ├── gen_translations.py          # i18n 翻译生成
│   └── diag_camera.py               # 相机现场诊断工具
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
OS：Linux x86_64
Python：3.14.6（3.10+ 理论上可尝试）
GUI：PySide6 6.11.1
推理：onnxruntime-gpu 1.28.0 + TensorRT
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
| tensorrt / tensorrt_cu13 / tensorrt_cu13_libs | 见 requirements-gpu.txt | TensorRT 加速 |

---

## 4. 一键配置环境

### Linux x86_64 + NVIDIA GPU

```bash
cd DuAD_software
bash setup_env.sh
```

### Linux x86_64 + 仅 CPU 推理

```bash
DUAD_INSTALL_GPU=0 bash setup_env.sh
```

### 相机 USB 权限

```bash
sudo cp backend/config/99-galaxy-dev.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```

重新插拔相机。

### 启动

```bash
python DuAD_SoftwareContent/main.py
```

> 也可以使用虚拟环境中的解释器：
> `DuAD_SoftwareContent/pyqml/bin/python DuAD_SoftwareContent/main.py`

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
| Windows x64 | ⚠️ 未测试；需要替换为大恒 Windows SDK 的 DLL/wrapper，并移除 `LD_LIBRARY_PATH` 注入逻辑 |
| Linux ARM | ⚠️ 未测试；需要大恒 Linux ARM 版 `libgxiapi.so`、`.cti` 传输层和匹配的 `gxipy`，PySide6/onnxruntime 是否提供 ARM wheel 也需确认 |

当前 `backend/libs/` 和 `backend/gxipy/` 均来自 **Galaxy Linux x86_64 SDK**，因此本仓库默认只保证 Linux x86_64。

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

## 9. Git 仓库同步

远程仓库：

```text
https://github.com/ouyangpingning/DuAD_software.git
```

首次同步：

```bash
cd DuAD_software
git init -b main
git remote add origin https://github.com/ouyangpingning/DuAD_software.git
git add .
git commit -m "init: DuAD software"
git push -u origin main
```

后续更新：

```bash
git add .
git commit -m "update"
git push
```

> GitHub 推送时如果使用 HTTPS，请使用 Personal Access Token 作为密码。

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
