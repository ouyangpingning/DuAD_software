# Jetson（ARM64）部署

在 **NVIDIA Jetson Orin NX**（JetPack 6.2 / L4T r36.5.2 / Ubuntu 22.04 / CUDA 12.6 /
TensorRT 10.3 / cuDNN 9.3）上运行 DuAD 上位机，已验证相机采集与 GPU 推理全链路。

## 与 x86 开发机的差异

| 组件 | x86_64 开发机 | Jetson (aarch64) |
|---|---|---|
| Python | venv `pyqml/`（pip） | micromamba 环境 `~/micromamba/envs/duad`（conda-forge，Python 3.10） |
| PySide6 | pip 6.11.1 | conda-forge 6.11.2（**pip 的 aarch64 wheel 要求 glibc ≥ 2.39，Jetson 只有 2.35，装不了**；conda 自带新版 glib，无需升级系统） |
| onnxruntime | pip onnxruntime-gpu 1.28（CUDA 13） | `onnxruntime_gpu 1.24.0`（**NVIDIA 官方 Jetson 索引** `pypi.jetson-ai-lab.io/jp6/cu126`，CUDA 12.6 + TensorRT 10.x，仅 cp310 wheel） |
| numpy | 2.5.2 | 2.2.6（3.10 无 2.5 的 wheel；ORT 1.24 兼容） |
| 相机 SDK | `backend/libs/`（x86_64） | `backend/libs_arm64/`（Galaxy_Linux-arm64_Gige-U3 2.4.2507.8231 提取） |
| Bayer 转换 | DxImageProc（libdxmediaproc.so） | **arm64 SDK 不带 DxImageProc** → `libbayer_demosaic.so`（自编译 C，~50ms/5MP）+ numpy 兜底 |
| 内存释放 | malloc_trim | 同（glibc 一致） |

架构相关的代码分支：`gxwrapper.py` / `dxwrapper.py` / `main.py` / `env.py` 按
`platform.machine()` 自动选 `libs_arm64`；`camera.py` 在 DxImageProc 缺失时自动
走 C/numpy 去马赛克。x86_64 行为完全不变。

## 首次部署步骤（已在本机完成，供重装参考）

```bash
# 0) 网络代理（可选，加快下载）：Clash Verge 开系统代理，端口 7897
export https_proxy=http://127.0.0.1:7897 http_proxy=http://127.0.0.1:7897

# 1) micromamba（用户态，无需 sudo）
curl -L https://micro.mamba.pm/api/micromamba/linux-aarch64/latest -o /tmp/mm.tar.bz2
mkdir -p ~/micromamba/bin && tar -xjf /tmp/mm.tar.bz2 bin/micromamba -C /tmp && \
  mv /tmp/bin/micromamba ~/micromamba/bin/

# 2) 创建环境（conda-forge 的 pyside6 提供 aarch64 的 Qt 6.11）
~/micromamba/bin/micromamba create -y -p ~/micromamba/envs/duad \
  -c conda-forge python=3.10 pyside6=6.11 pip

# 3) NVIDIA 官方 Jetson ORT-GPU（CUDA + TensorRT EP，cp310）
~/micromamba/bin/micromamba run -p ~/micromamba/envs/duad pip install \
  --index-url https://pypi.jetson-ai-lab.io/jp6/cu126/+simple/ onnxruntime_gpu==1.24.0

# 4) 其余依赖（PyPI，注意 numpy 版本兼容 Python 3.10 + ORT 1.24）
~/micromamba/bin/micromamba run -p ~/micromamba/envs/duad pip install \
  numpy==2.2.6 Pillow pyserial paho-mqtt

# 5) 相机 USB 权限（需要 sudo；规则文件仓库自带）
sudo cp backend/config/99-galaxy-dev.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
# 重新插拔相机

# 6) libbayer_demosaic.so 已随仓库提供（backend/libs_arm64/）。
#    若需重新编译（换 SDK/重装系统后）：
#    gcc -shared -fPIC -O2 -o backend/libs_arm64/libbayer_demosaic.so \
#        backend/libs_arm64/bayer_demosaic.c
```

## 启动

```bash
bash run_jetson.sh                     # SSH 下自动弹到桌面 :0
~/micromamba/envs/duad/bin/python -u DuAD_SoftwareContent/main.py   # 等价
```

启动日志要点：

- `[INFO] 注入 LD_LIBRARY_PATH（[…/backend/libs_arm64]）并重启进程` —— arm64 SDK 路径就绪；
- 模型预热后应打印 `模型预热完成（['TensorrtExecutionProvider', 'CUDAExecutionProvider', 'CPUExecutionProvider']）`；
- 首次用 TensorRT 推理会后台编译引擎（DINOv2 级模型约 1~3 分钟，8GB 内存机器注意
  与相机采集并发时的内存峰值；引擎构建失败时自动回退 CUDA EP）。

## 验证清单

```bash
# GPU 推理自检（应打印三个 provider）
~/micromamba/envs/duad/bin/python -c "import onnxruntime as ort; print(ort.get_available_providers())"

# 单图推理自检（bottle 模型 + 测试图，应打印 CUDA provider 与 score）
~/micromamba/envs/duad/bin/python -u backend/alg/deploy/onnx_infer.py \
    models/bottle_k4_s0_full.onnx models/000.png

# 无相机冒烟（6 个套件，全部 PASS）
QT_QPA_PLATFORM=offscreen ~/micromamba/envs/duad/bin/python -u tests/test_camera_bridge.py
QT_QPA_PLATFORM=offscreen ~/micromamba/envs/duad/bin/python -u tests/test_camera_link.py
QT_QPA_PLATFORM=offscreen ~/micromamba/envs/duad/bin/python -u tests/test_detect_pipeline.py
QT_QPA_PLATFORM=offscreen ~/micromamba/envs/duad/bin/python -u tests/test_collect_pipeline.py
QT_QPA_PLATFORM=offscreen ~/micromamba/envs/duad/bin/python -u tests/test_light_bridge.py
QT_QPA_PLATFORM=offscreen ~/micromamba/envs/duad/bin/python -u tests/test_mqtt_bridge.py

# 相机诊断
~/micromamba/envs/duad/bin/python scripts/diag_camera.py
```

## 已知限制

- **TensorRT 不可用（当前模型）**：`bottle_k4_s0_full.onnx` 含 5 个 `If` 控制流节点
  （DINOv2 注意力掩模的动态控制流导出产物），分支输出形状不一致（`[-1,1536]` vs
  `[-1,1,1536]`），Jetson 版 ORT 1.24 的 TRT EP 在分区时直接抛异常（x86 ORT 1.28
  会静默回退）。`onnx_infer.py` 已加**逐级降级**：TRT → CUDA → CPU，任何模型都
  不会崩。实测 CUDA EP **~700ms/帧**（518px，fp32；x86 TRT 为 69ms）。
  想要 TRT 加速需在训练侧重新导出**不含 If 控制流**的 ONNX（固定尺寸、
  消除动态 mask 分支），可参考 `alg/deploy/export_onnx.py`。
- **fp16 加速不可用（当前模型）**：onnxconverter-common 自动转 fp16 时因 If
  分支内 Cast 类型不一致而失败；手动转风险高，未采用。
- **像素格式**：Bayer8（去马赛克走自实现 C）与 Mono8 正常；RGB8/BGR8 直通；
  Bayer10/12 等高比特格式未处理（与 x86 一致，请选 8bit 格式）。
- **性能实测（Orin NX 8GB）**：相机 2448×2048 BayerRG8 **~67fps** 连续采集；
  实时推理稳态 ~700ms/帧（首帧含 CUDA 初始化 ~5s）；Bayer 去马赛克 C 实现
  ~50ms/5MP（DVFS 满载时更快）。
- 阈值/尺度 metadata 与标定文件机制与 x86 完全一致（metadata > *.threshold.json >
  默认 1.7；热力图尺度 metadata > *.scale.json > 逐图百分位）。
- log4cplus 报 `could not open file /etc/Galaxy/cfg/log4cplus.properties` 为
  SDK 日志配置缺失的无害告警（x86 无此文件同样工作），可忽略。
