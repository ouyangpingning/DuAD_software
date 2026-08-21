# AGENTS.md

PySide6 + QML 工业异常检测上位机（论文《融合Dinov2与双分支训练架构的工业异常检测》配套软件）。完整架构树见 `CLAUDE.md`；本文件只记录容易踩坑的事实。

## 运行

Linux（开发机）:

```bash
source DuAD_SoftwareContent/pyqml/bin/activate
python DuAD_SoftwareContent/main.py
```

Windows:

```powershell
pyqml_win\Scripts\python.exe -u main.py
```

- ⚠️ **系统 python3（/usr/bin/python3）也装了 PySide6 + onnxruntime（CPU 版）**：直接 `python3 main.py` 能跑通但推理走 CPU（无 GPU）。main.py 启动时会检测解释器，非 venv 自动 execv 切到对应 venv 重启——**不要绕过它**（Linux→`pyqml/bin/python`，Windows→`pyqml_win/Scripts/python.exe`）。判断是否走 GPU：看启动日志 `模型预热完成（['CUDAExecutionProvider', ...]）`。
- **跨平台 venv 约定**：main.py 顶部按 `os.name` 选择 venv——Windows 用 `pyqml_win/`（`Scripts/python.exe`），Linux 用 `pyqml/`（`bin/python`）。两个 venv 可共存于仓库，各自平台各自建。Windows 上 `pyqml_win/` 用 `uv` 或 `python -m venv` 创建，Python 3.14，依赖与 requirements*.txt 相同。
- 依赖仅 PySide6（6.11.1，Python 3.14）+ onnxruntime-gpu（1.28.0）。**⚠️ Windows 版 onnxruntime-gpu 并不自带 CUDA 运行库**：`onnxruntime/capi/` 只有 onnxruntime 自己的 4 个 DLL，provider（cublas64_13.dll / cudnn64_9.dll）需另装 `nvidia-cublas-cu13` + `nvidia-cudnn-cu13` + `nvidia-cuda-runtime` pip 包（DLL 在 `site-packages/nvidia/*/bin*`）；onnxruntime 加载 provider 时按 PATH 解析这些 DLL，缺失则**静默回退 CPU**（日志只有 EP Error）——main.py（Windows 分支）和 `onnx_infer.py` 顶部已把 nvidia bin 目录注入 PATH。**CUDA 13 要求 NVIDIA 驱动 ≥ 585**（555 等旧驱动 cudaSetDevice 报 801 回退 CPU）。**TRT 库 Windows 无 pip 包可装**（tensorrt 绑定 wheel 不支持 cp314、tensorrt_cu13_libs 仅 Linux），需手动从 NVIDIA 官网下载 TensorRT 10.16.x Windows zip 解压并把 lib 目录加入 PATH 注入——本机已装 `G:\software\TensorRT\TensorRT-10.16.1.11.Windows.amd64.cuda-13.2\TensorRT-10.16.1.11\bin`，经用户环境变量 `TENSORRT_LIB_DIR` 指定（main.py/onnx_infer.py 的注入逻辑读它，也可拷入 `backend/libs_win_tensorrt/bin` 免设变量）。**没有** requirements.txt / pyproject.toml / README / 测试 / CI。
- 项目曾位于 `算法对应上位机/Main/MainContent/`，后改名 → `pyqml/bin/pyside6-lupdate`、`pyside6-lrelease` 的 shebang 仍指向旧路径，**直接运行必报 "bad interpreter"**。翻译工具链要用 site-packages 里的原生二进制（见下）。
- git 仓库根在上级 `研究生论文/`，跟踪的是论文文档；**本代码目录未被 git 跟踪**（在此目录内 git status/diff 无意义）。

## 布局

- `DuAD_SoftwareContent/` — 入口 `main.py` + 全部 QML：`pages/`（6 个页面）、`pages/components/`、`pages/minipages/`
- `DuAD_SoftwareContent/DuAD_Software/` — QML 模块（`qmldir` 声明 `singleton Colors` / `singleton Constants`），由 main.py 的 `engine.addImportPath(DuAD_SoftwareContent)` 注册，QML 中 `import DuAD_Software`。**模块目录名必须与 qmldir 的 module 名一致**（引擎按目录名找模块）。
- `backend/` — 后端（已从 pyqt5 复制并适配 PySide6）：
  - `Src/` — 相机驱动/采集管线（PyQt5→PySide6 已适配，`pyqtSignal`→`Signal`）
  - `Src/camera_bridge.py` — **CameraBridge（QML 相机桥）**：search→camerasFound / connectCamera→cameraOpened·cameraError / getFeature·setFeature（同步返回）/ disconnectCamera→cameraClosed / startGather·stopGather / applyRoi·resetRoi；每帧写 `frameProvider` 并递增 `frameIndex`（QML 经 `image://camera/original?t=<index>` 取图），同时发 `rawFrameReady(np.ndarray)` 给实时推理管线；main.py 注册 context property 并把连接状态同步到 AppBridge.cameraConnected
  - `Src/frame_provider.py` — **CameraFrameProvider**：`image://camera/original|heatmap|mask` 的最新帧缓存（线程安全，QML 重取图）
  - `Src/realtime_detect_bridge.py` — **RealtimeDetectBridge（实时检测管线）**：消费 `CameraBridge.rawFrameReady`，maxsize=1 队列只留最新帧；后台线程调 `AlgorithmBridge.predict_frame`（复用同一 ONNX session），结果写 provider 后自增 resultCounter/maskCounter 通知 QML；`scoreReady(score)` 更新分数。启停由 main.py 根据 `AppBridge.collectingOwner`/`cameraConnected` 集中仲裁
  - `Src/collect_bridge.py` — **CollectBridge（图像采集保存管线）**：`configure(path,prefix,fmt,interval)` 配置；消费 `CameraBridge.rawFrameReady`，maxsize=1 最新帧队列 + 后台 PIL 节流写盘；`saving/savedCount/lastSavedPath/saveError` 供 QML。main.py 在 owner=="collect" 时启停，与 detect 互斥
  - `Src/light_bridge.py` — **LightBridge（光源控制器）**：pyserial CH340 串口；每 2s 扫描 `/dev/ttyUSB*`、`/dev/ttyCH341USB*`；`connectSerial(port,baud,data,stop,parity)` / `setLightValue(channel,value)`；协议 `$L{通道}={亮度}#`（0~3，0~255），发送后读 10ms 短响应
  - `Src/mqtt_bridge.py` — **MqttBridge（云服务器通信）**：paho-mqtt 后台线程连接；支持用户名/密码登录（付费 Broker）与 TLS/SSL（8883）；`connectServer(addr,port,user,pwd,keepalive,useTls)` / `publish(topic,payload,qos)` / `subscribe(topic,qos)`；日志与收发经 `logMessage`/`messageReceived` 信号回 QML
  - `Src/algorithm_bridge.py` — **AlgorithmBridge（测试推理桥）**：loadModel / inferImage（**后台线程推理不阻塞 UI**，jet 热力图存临时 PNG，经 inferenceReady(score, path) 信号返回；**二值掩模叠加图**（`last_anomaly_mask` 异常像素红色高亮，定位缺陷）经 maskReady(path) 信号返回，**先发 maskReady 再发 inferenceReady**——否则 QML 侧 imageSource 出现空 file:// 协议警告）；**模型默认 null（不预置），用户在 DetectPage"选择模型"自选 .onnx**，loadModel 后自动后台预热；**阈值读取优先级 = ONNX metadata（训练时标定写入）> `*.threshold.json` > 默认值 1.7**，session 建好后自动覆盖；**像素阈值用户可调**（`setPixelThreshold`，NaN 恢复 metadata F1-max）；**热力图固定显示尺度**（calibrate_scale.py 用 good 样本统计：vmin=像素 P2、vmax=P99.9——**不能用 max**（尾部分布长，正常图的标签/噪声 patch 会把色阶拉宽、缺陷黄区变弱）；产出 `<模型>.scale.json`，查找顺序：模型旁 → `backend/model_scales/`，无则回退逐图百分位）；**切换模型的内存释放三件套（缺一 RSS 累积膨胀）**：`onnx_infer` 里 `enable_cpu_mem_arena=False`（禁 ORT CPU arena）+ loadModel 里 `del old` + `gc.collect()` + `malloc_trim(0)`（ctypes 调 glibc 归还堆，实测 547MB→65MB）；session 构造在 `_get_detector` 锁外进行（避免长锁），构造期间模型被切换则立即丢弃该过时 session；**`predict_frame(np.ndarray)` 供实时管线同步调用**（`_infer_lock` 串行化、`_build_lock` 防 warmup/实时并发建双 session）；**`unloadModel()` 卸载当前 ONNX**（session 置空 + `del old` + `gc.collect()` + `malloc_trim(0)`，阈值/尺度恢复默认，发 modelUnloaded）
  - `gxipy/` — 大恒 SDK wrapper（gxwrapper/dxwrapper 已改为**本地 libs 优先加载**）
  - `libs/` — SDK 动态库（libgxiapi + ffmpeg 全家桶 + **GxU3VTL.cti 等 GenICam 传输层文件**，从官方 Galaxy_camera.run 提取，**无需 root 安装**）。⚠️ **.cti 必须齐全**：缺传输层时 gx_init_lib 返回 -1、枚举永远 0 台（即使相机在 lsusb 可见、udev 权限正确）；udev 规则在 `backend/config/99-galaxy-dev.rules`（需 root 装到 /etc/udev/rules.d/，否则普通用户无 USB 权限同样枚举不到）
  - `alg/` — DuAD 算法代码（torch 训练 + `deploy/export_onnx.py` 导出；**`deploy/onnx_infer.py` 是联调用的无 torch 推理模块**：加载 pca_inline 模式 onnx，输入 numpy RGB 帧 → 返回 jet 热力图 RGB + 分数；预处理 Resize+CenterCrop(target_size)+ImageNet 归一化，后处理双线性上采样+**numpy 自实现高斯模糊**（Pillow 12 的 GaussianBlur 不支持 float32 图）；**分数为判别器负输出的 patch max——带符号、无界、未归一化**（正常图 ≈ -1~1.5、异常图 > 2），与标定阈值比较判异常（**阈值来源优先级：ONNX metadata（训练时标定写入 ckpt→export 写 metadata，见 `alg/threshold_utils.py`）> `*.threshold.json` > 默认 1.7**），**不要** clamp/min-max 到 0~1（会把任何图归一成恒 1 误报）；target_size 默认 518，须与导出时一致）
  - ⚠️ **加载前提**：`pyqml/bin/activate` 已注入 `LD_LIBRARY_PATH=backend/libs`（glibc dlopen 依赖解析只认进程启动时的 ld 路径，运行时设置无效）；activate 脚本里 VIRTUAL_ENV 是改名前的硬编码旧路径，**不要依赖它**
  - **Windows 相机 SDK（自包含）**：Windows SDK 已集成到项目 `backend/libs_win/`（与 Linux 的 `libs/` 平行，不互拆），保持官方目录结构：`APIDll/Win64/`（GxIAPI.dll、DxImageProc.dll 及 VC 运行库）、`GenICam/bin/Win64_x64/`（GenApi 等 GenICam 运行时）、`GenTL/Win64/`（.cti 传输层）。`gxwrapper.py`/`dxwrapper.py` Windows 分支已改为**本地优先**：检测到 `libs_win/` 就自动 `add_dll_directory` + 设置环境变量，**无需手动设置任何环境变量。关键：GXInitLib 找 GenTL 传输层走的是 `GENICAM_GENTL64_PATH`（大恒安装器设的变量名，非 `GX_` 前缀），缺它 gx_init_lib 返回 -1、报 “Failed to get GenTL path”。gxwrapper 已在本地优先分支自动设 `GENICAM_GENTL64_PATH` 指向 `libs_win/GenTL/Win64`。
- `tests/` — 冒烟回归脚本（offscreen + QTest，**防 /tmp 清理丢失**）：`test_camera_bridge.py`（无相机路径）、`test_camera_link.py`（mock 相机全链路）、`test_detect_pipeline.py`（实时检测 provider/队列/分数链路）、`test_collect_pipeline.py`（采集保存节流写盘）、`test_light_bridge.py`（光源协议/串口指令）、`test_mqtt_bridge.py`（MQTT 登录/TLS/发布订阅）、`fake_bridges.py`（共享 fake）
- `scripts/gen_translations.py` + `translations/` — i18n 工作流（见下）
- `scripts/diag_camera.py` — 相机现场诊断：sysfs 速度/设备节点 → SDK init/枚举 → 像素格式 + 吞吐量限制特征（7.2fps 与黄蓝互换问题定位）
- `scripts/diag_bayer.py` — Bayer 排列逐项诊断：抓一帧用 RG/GB/GR/BG 各转一张对比图 + 打印 pixel_format/color_filter（黄蓝互换定位，Windows 需用 BG 的结论来源）
- `scripts/package_win.py` + `scripts/DuAD_win.spec` — **Windows PyInstaller 打包**（onedir → zip + SHA256SUMS，见下）

## Windows 打包（PyInstaller onedir）

```powershell
DuAD_SoftwareContent\pyqml_win\Scripts\python.exe -u scripts\package_win.py 1.0.0
```

- **GPU 库不打包**：spec 的 `excludes` 排除 `nvidia*`/`tensorrt*`；内置 onnxruntime-gpu 主包（provider DLL 保留），无 nvidia 库时静默回退 CPU，有 GPU 库（`TENSORRT_LIB_DIR`/PATH 注入）自动加速。
- **frozen 路径**：main.py 的 `_content_dir()/_backend_root()/_translations_root()` 在 frozen 下返回 `sys._MEIPASS`（PyInstaller 6 onedir 的 `_internal/`）；spec 把所有资源（QML、`backend/`、`translations/`）都打进 `_internal/`。改布局时三处必须同步。
- **windowed 无控制台**：`console=False`，frozen 时 main.py 把 stdout/stderr 重定向到 `%USERPROFILE%\DuAD_app.log`（**必须 `buffering=1` 行缓冲**，否则强杀进程时日志丢失）。测试 exe：`QT_QPA_PLATFORM=offscreen` 启动看 `DuAD_app.log` 里 `[INFO] 启动成功` 且无 `QML ERROR`。
- **必须强制 Fusion 样式**：打包后若不加 `os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Fusion")`，Windows 原生样式不支持自定义 background/contentItem → 大量 `QML ERROR ... current style does not support customization`（界面控件空白）。该设置在 main.py `__main__` 块、QGuiApplication 创建前。
- **venv 自检分支的坑**：`main.py` 顶部 venv 检测里 `"Scripts" / "python.exe"`（str/str 相除）在开发环境因 `sys.prefix==venv` 不执行、Python 3.14 下直接 TypeError——打包后条件成立必崩。已改为 `Path("Scripts", "python.exe")`，**不要改回**。
- spec 的 `ROOT = Path(SPECPATH).parent`（SPECPATH=spec 所在目录 scripts/）；`__file__` 在 spec 执行时不可用。图标用 `favicon.ico`（`DuAD.ico` 已废弃删除）。

## 关键约定（易踩坑）

- **相机 SDK 缺失不崩溃（重要）**：`backend/Src/camera.py` 对大恒 SDK 的 import 包在 try/except 里，SDK 不可用时降级为 stub，程序仍能启动（相机功能自动不可用）。但原来只捕 `(ImportError, NameError, OSError)`，而 Windows 上 gxwrapper.py 在环境变量缺失时抛 `KeyError: GALAXY_GENICAM_ROOT`，未被捕获→**整个程序一启动就崩溃。已修：**gxwrapper.py的 `except OSError`改为 `except (OSError, KeyError)`，camera.py的 `except (ImportError, NameError, OSError)` 改为 `...+KeyError`。后续若更改 SDK 加载逻辑不要把这两处回退。
- **颜色唯一来源** `DuAD_Software/Colors.qml`，有状态单例：`Colors.setTheme("light"|"dark")` / `setPreset(...)` 运行时切换，**全部颜色走 ColorAnimation 渐变过渡**（`animDuration` 属性，0=瞬变；22 个动画对象在 `Component.onCompleted` 里用 `Qt.createQmlObject` 动态创建——QtObject 无默认属性，数组字面量也不能声明对象）。禁止硬编码 `#rrggbb`。
- **URL 解析**：main.py 设 `QML_COMPAT_RESOLVE_URLS_ON_ASSIGNMENT=1`，相对 URL 按"赋值所在 qml 文件"解析 → `pages/` 引 `images/` 必须写 `../images/`。
- **`.ui.qml`（MainWindow.ui.qml）仅供 Qt Design Studio**，不要手动加逻辑；其中 StackLayout 子项顺序必须与 `navGroup.buttons` 顺序一致（currentIndex 用 indexOf 计算）。侧边栏宽度 < 900 自动折叠为图标。
- **ComboRow 的 model 必须用稳定 key（不翻译）**，显示文本走 `displayFunc`；否则语言切换时 ComboBox 重建、currentIndex 被重置（见 GeneralSettingsCard.qml 注释）。
- **ComboBox 下拉框**：popup 已强制 `popupType: Popup.Item` + **高度用 `combo.count * 32 + 4` 同步计算**（不依赖异步 contentHeight——delegate 未布局时高度 0 会在 KWin 下形成"0 高→不创建 delegate→仍 0 高"死循环，下拉空白）。改动 delegate 高度时同步改这个公式。
- **KDE 桌面必须强制 Fusion 样式**：main.py 已 `os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Fusion")`——KDE 经系统配置注入的 Breeze 样式（`org/kde/breeze`）与 Qt 6.11 QML 化控件不兼容（ComboBox 下拉空白、类型转换警告）。**不要移除**，否则 KDE 下回归。
- **SliderRow / InputRow 的 label 列固定 72px、数值列固定 80px**（超出省略号截断）——这是对齐约定，改动时不要放开。
- **SliderRow 已带 `released(real value)` 信号**（松开才触发，拖动过程不发）——写相机等重量级操作必须挂在 onReleased 而非 onSliderValueChanged；另有 `resetValue` 属性（NaN 时不显示 ↺ 重置按钮，点击恢复 resetValue 并触发 released）。新增：`snapTicks` 拖动吸附刻度并在轨道上绘制刻度线（触摸友好）、`wheelStep` 滚轮细调（停止滚动 350ms 自动 released）、数值列可直接点击输入精确值，提交后显示短暂 ✓；所有数据调节滑块都配置了 snapTicks。相机参数重置目标 = 老项目初始值（曝光 20000μs / 增益 0dB / 帧率 1.0）。**曝光 UI 单位为 ms，写相机时换算 μs**。
- **CameraBridge 特征写入**：bool 特征底层要求字符串 `"true"/"false"`（不是数值）；`GX_FLOAT_GAMMA_PARAM` 在 MER2 固件只读（写返回 -8 INVALID_ACCESS，界面已改为只读显示）。
- **Bayer 转换不要再写死 BG**：`capture_image` 必须按 `frame_param.pixel_format` 查 `_PIXEL_FORMAT_TO_BAYER` 选择 RG/GB/GR/BG；写错排列的症状就是黄/蓝互换。**优先按 `GX_ENUM_PIXEL_COLOR_FILTER`（传感器真实滤镜，读自相机寄存器、跨平台一致）选排列**（`_COLOR_FILTER_TO_BAYER`，连接时读入 `self.color_filter`），读不到才回退 pixel_format 查表。**⚠️ Windows 的 DxImageProc.dll 与 Linux 的 bayer_type 语义相反（R/B 互换）**：同一相机 color_filter=RG 时 Linux 正常、Windows 黄蓝互换；diag_bayer 逐排列验证 Windows 需用 BG。修复：`capture_image` 在 `os.name=='nt'` 时把 bayer 排列做 `_SWAP_RB_BAYER`（RG↔BG、GB↔GR）——**不要移除**，否则 Windows 回归黄蓝互换（诊断脚本 `scripts/diag_bayer.py`）。Mono8 走灰度扩展，RGB8/BGR8 直接拷贝（BGR 交换 R/B）。
- **低帧率先查曝光，再查吞吐量**：实测 MER2 曾出现目标 29fps 但当前仅 6fps，原因是曝光 165508us（1e6/165508≈6.04fps）。`gather_start` 会自动把曝光压到 `1e6/target_fps*0.9`。另有部分大恒相机出厂开启 `DeviceLinkThroughputLimit`（36,000,000 B/s，2448×2048×7.2≈36MB/s），采集前会写 `GX_ENUM_DEVICE_LINK_THROUGHPUT_LIMIT_MODE=0`（Off）。都排除后仍低帧率再查 USB2 口/USB2 线（U3 相机必须 USB3，`lsusb -t` 看是否 5000M）。
- **连接中文案居中**：CameraCard / LightControllerCard / CloudServerCard 的 "正在连接…" 是卡片级覆盖层（`anchors.centerIn`，连接时图标隐藏）。若新增此类卡片，照抄该模式，不要放 RowLayout 内（图标占位会让文案偏右）。
- **目录/文件选择**：用系统原生 `FolderDialog`（保存路径，SaveSettingsPanel）/ `FileDialog`（测试图片/模型，DetectPage，`import QtQuick.Dialogs`）。原生对话框是平台窗口（尺寸由系统决定），**注意**：Qt 6.11 里它们的 QML 对象是 `QFileDialogOptions` 包装（className 匹配测试用），offscreen 下打开不置 visible（真机正常）。路径转换**必须跨平台**（Windows 的 `file:///C:/...` slice(7) 后是 `/C:/...`，带盘符前斜杠，直接给 Python `os.path.exists` 会报"文件不存在"）：**正向用 `root._toFileUrl()`（反斜杠→正斜杠、根前补 `/` 生成 `file:///C:/...` 或 `file:///home/...`），反向用 `root._fromFileUrl()`（`slice(7)` 后去掉 `/^\/[A-Za-z]:/` 盘符前斜杠）**——DetectPage 已内置这两个函数，新增 FileDialog/FolderDialog 时照抄；`~` 经 `AppBridge.homeDir` 展开。后端 tempfile 返回的 `C:\Users\...`（反斜杠）路径同样要过 `_toFileUrl` 才能显示。
- **AppBridge（main.py）**：`setLanguage()` / `homeDir`（~展开）/ `isDir(path)`（目录校验）/ **跨页状态中枢**：`cameraConnected`（CameraPage 写）、**`collectingOwner`（采集会话互斥仲裁，"" / "collect" / "detect"，后按者抢占，`collecting` 只读派生跟随）**、`algorithmEnabled`（DetectPage 算法开关，后端推理线程轮询防卡死）。QML 里 AppBridge 引用失败时界面静默失效——Python 侧对象必须保持引用防 GC。
- **DetectPage 图像区**：已联调真实相机。原图 = `image://camera/original?t=<CameraBridge.frameIndex>`；热力图/定位图 = `image://camera/heatmap|mask?t=<DetectBridge.resultCounter|maskCounter>`（`CameraFrameProvider` 只存最近一帧）。`RoiOverlay` 归一化坐标 → `CameraBridge.applyRoi`（宽 8/高 2 步进对齐，先 OFFSET 后 WIDTH/HEIGHT）；绘制时实时显示像素范围，松开后[确定]/[重绘]；原图标题栏有 ROI/↺恢复全幅/⛶ 图标按钮，热力图标题栏有 ⛶；原图全屏层同样带 ROI 按钮并可放大框选，⛶ 点击进入占满 DetectPage 的全屏层，再点退出。**ROI 写入时若正在采集会先 stopGather，写完读回校验后延迟 200ms 自动 startGather（失败重试 2 次）**（大恒 Continuous 流中直接改 ROI 不生效/INVALID_ACCESS；STOP 后立刻 START 也可能失败）；main.py 还有 1.5s 兜底恢复，仍失败会释放 collectingOwner 让按钮可点；**首次 ROI 前会记录当前几何，恢复全幅回到该设置值而非传感器最大分辨率**；ROI 归一化按 ImageView 实际图像内容区（剔除黑边）换算；归一化坐标相对当前显示画面，后端会叠加当前 OFFSET 换算成传感器绝对坐标。**页面自带自锁"开始采集"按钮（申请 collectingOwner="detect"）**，真正的 startGather/stopGather 由 main.py 的 owner 仲裁统一执行；数据采集（CollectPage，owner="collect"）与实时检测采集互斥。**实时采集优先于测试推理**：开始采集会自增 `_testSession` 作废旧测试请求，在途 inferenceReady/maskReady 返回后直接丢弃；采集中测试推理区按钮禁用并显示提示。“精细定位”开关不再要求先打开“F1 阈值定位”：始终可点击，开启时自动打开定位显示；侧栏含**测试推理区**（文件选图 → AlgorithmBridge.inferImage 后台推理 → 结果切换显示在原图/热力图窗口），并可 **unloadModel 卸载 ONNX** 释放 session 内存。
- 全局字体 `fonts/wqy-microhei.ttc` 由 main.py 注册为默认字体，QML 中无需指定 family。

## i18n：新增 qsTr 文本必做（否则英文/繁体缺翻译）

源文本 = 简体中文；main.py `LANG_FILES`：0=en(.qm) / 1=zh_CN(源文本) / 2=zh_TW(.qm)。以下命令必须从仓库根目录运行（.ts 内路径是相对根目录的）：

```bash
LUPDATE=DuAD_SoftwareContent/pyqml/lib/python3.14/site-packages/PySide6/lupdate
LRELEASE=DuAD_SoftwareContent/pyqml/lib/python3.14/site-packages/PySide6/lrelease
# 1. 提取新字符串到 app_en.ts —— 注意：不能扫整个 DuAD_SoftwareContent（pyqml/ 里的
#    venv 示例 QML 会被扫进来），必须显式列出源路径；-no-obsolete 清掉删除的旧条目
"$LUPDATE" -no-obsolete DuAD_SoftwareContent/App.qml DuAD_SoftwareContent/MainuiRoot.qml DuAD_SoftwareContent/MainWindow.ui.qml DuAD_SoftwareContent/pages -ts translations/app_en.ts
# 2. 填充翻译 —— 新字符串必须同时加入 scripts/gen_translations.py 的 EN 和 TW 两个 dict
python scripts/gen_translations.py
# 3. 编译 .qm 并提交
"$LRELEASE" translations/app_en.ts translations/app_zh_TW.ts
```

语言选择持久化在 `QSettings("DuAD","DuADSoftware")`，切换走 `AppBridge.setLanguage()`（main.py）。

## 冒烟测试（offscreen）

无测试框架，回归靠 `/tmp/opencode/` 下的临时脚本（Python + QTest 模拟点击 + `QT_QPA_PLATFORM=offscreen`）。已验证的关键点：

- **必须 `python -u`**（管道下 stdout 缓冲会吞日志）；QML 的 console.log 在该环境不可见，断言靠 Python 读 property。
- **FakeBridge 必须保持引用**（`bridge = FakeBridge()` 存变量），否则被 GC → QML 侧 AppBridge 变 null、点击回调静默失败（界面上表现为"点了没反应"）。
- 控件 className 不是 `QQuickButton` 等 C++ 名，Qt 6.11 控件是 QML 实现（`Button_QMLTYPE_*`），匹配用 `"Button" in className`；Popup/Dialog 不在 Item 树里（用 `findChildren(QObject)`），delegate 在 ListView 的 contentItem 里。
- 切页用 `stack.setProperty("currentIndex", n)`，等布局完成再点（齿轮/卡片坐标经 `mapToScene` 换算）。

## 页面状态

- 已实现：Camera（**真实相机后端已联调**：搜索/连接/断开/参数读写全链路，MER2-501-79U3C-L 分辨率 2448×2048）、Light（**CH340 串口光源已联调**）、Comm（**MQTT 云服务器已联调**）、Collect（**真实相机定时保存已联调**：保存目录/前缀/格式/间隔 → CollectBridge 后台节流写盘，与 Detect 采集互斥）、Settings（通用/云服务器/关于）、Detect（**真实相机实时采集已联调**：原图 provider 推帧 + ROI 写相机 + 算法开启时后台 ONNX 实时推理热力图/分数/定位图，`_camRatio` 跟随实际 GX_INT_WIDTH/HEIGHT）
- 注意：`CLAUDE.md` 的「当前状态」一节已过时（写于只有 CameraPage 时）。