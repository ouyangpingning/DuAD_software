# AGENTS.md

你的身份：

你是一个具有10年工作经验的UI设计工程师兼上位机开发工程师，同时还是一名精通AI的算法工程师

我的项目：                                                    

PySide6 + QML 工业异常检测上位机（论文《融合Dinov2与双分支训练架构的工业异常检测》配套软件）。
完整架构树见 `CLAUDE.md`（注意其「当前状态」一节已过时）；**踩坑实录见 `docs/19-踩坑实录与设计规矩.md`**（本文只留一句话结论 + 指针，编号与该文一致）。

## 运行

Linux（开发机）:

```bash
source DuAD_SoftwareContent/pyqml/bin/activate
python DuAD_SoftwareContent/main.py
```

Windows: `pyqml_win\Scripts\python.exe -u main.py`；Jetson: `bash run_jetson.sh`（部署见 `docs/Jetson部署.md`，问题见 `docs/16-Jetson问题与修复.md`）。

- ⚠ **不要绕过 main.py 启动时的解释器自检**：非 venv 会自动 execv 切到 venv（Linux→`pyqml/bin/python`，Windows→`pyqml_win/Scripts/python.exe`，两个 venv 可共存、各自平台各自建）。判断是否走 GPU：看启动日志 `模型预热完成（['CUDAExecutionProvider', ...]）`。系统 python3 也能跑通但推理走 CPU。
- 依赖仅 PySide6（6.11.1，Python 3.14）+ onnxruntime-gpu。**无** requirements.txt / pyproject / README / 测试 CI。
- **Windows 的 onnxruntime-gpu 不自带 CUDA 运行库**：需另装 `nvidia-cublas-cu13`/`nvidia-cudnn-cu13`/`nvidia-cuda-runtime` pip 包，缺失则**静默回退 CPU**（main.py 与 onnx_infer.py 已把 nvidia bin 目录注入 PATH）；**CUDA 13 要求驱动 ≥585**。**TRT 库 Windows 无 pip 包**：手动装 TensorRT 10.16.x，用用户环境变量 `TENSORRT_LIB_DIR` 指定（或拷入 `backend/libs_win_tensorrt/bin`）。详见 `docs/15`。
- `pyside6-lupdate`/`lrelease` 的 shebang 指向改名前旧路径，**直接运行必报 bad interpreter** —— 用 `DuAD_SoftwareContent/pyqml/lib/python3.14/site-packages/PySide6/` 里的原生二进制。
- git 仓库根在上级 `研究生论文/`，跟踪论文文档；**本代码目录是一个独立的 git 仓库**（`git rev-parse --show-toplevel` 就落在 `DuAD_Software/`，文件是被跟踪的；旧文档里"未被跟踪"那句已过时）。

## 布局

- `DuAD_SoftwareContent/` — 入口 `main.py` + 全部 QML：`pages/`（7 个页面）、`pages/components/`、`pages/minipages/`。`DuAD_Software/DuAD_Software/` 是 QML 模块（`qmldir` 声明 `singleton Colors/Constants`），**模块目录名必须与 module 名一致**。
- `backend/Src/` — 相机/光源/平台/云/算法各桥（要点见下）。`backend/gxipy/` 大恒 SDK wrapper（本地 libs 优先加载）；`backend/libs*` — SDK 动态库。
- ⚠ **相机 SDK 缺失不崩溃**：`camera.py` 对 SDK import 包 try/except（含 `KeyError`），降级 stub、程序仍可启动。**不要回退这两处异常捕获**。
- ⚠ **`.cti` 传输层文件必须齐全**（`libs/`、`libs_win/GenTL/`、`libs_arm64/`）：缺它 gx_init_lib 返回 -1、枚举 0 台。Linux 大分辨率采集失败（-1010）根因是内核 `usbfs_memory_mb` 默认 16MB：跑 `sudo bash scripts/set_usbfs.sh`。相机 SDK 按架构选库（aarch64→`libs_arm64/`）。
- ⚠ **加载前提**：`pyqml/bin/activate` 已注入 `LD_LIBRARY_PATH=backend/libs`（运行时设置无效）；activate 里 VIRTUAL_ENV 是改名前旧路径，不要依赖。
- **训练/导出/标定代码不在本仓库**（已删，用算法仓库 https://github.com/ouyangpingning/DuAD ）。

### 各桥要点（按文件）

- `camera_bridge.py` — QML 相机桥：search/connect/feature 读写/startGather/applyRoi；每帧写 `frameProvider` 并发 `rawFrameReady`。特征写入：bool 要字符串 `"true"/"false"`；`GX_FLOAT_GAMMA_PARAM` 在 MER2 固件只读（界面只读显示）。**Bayer 转换按 `GX_ENUM_PIXEL_COLOR_FILTER`（寄存器读出）选排列，读不到才按 pixel_format 查表；Windows 的 DxImageProc 与 Linux 语义相反（R/B 互换），`os.name=='nt'` 时做 `_SWAP_RB_BAYER`——不要移除**。低帧率先查曝光，再查吞吐量（`DeviceLinkThroughputLimit` 采集前关掉），最后查 USB2 口/线。
- `light_bridge.py` — RS-232 光源控制器：**波特率必须 19200**（8N1 半双工；实测其它波特率一个字都不回，而 `serial.Serial()` 永远打开成功 → 静默"已连接没反应"）。三条**不要动**：① `connectSerial` 后必须 `$RD=9999#` 校验链路；② `_read_reply` 收到半截不许缩短等待（长应答分片跨 161ms）；③ 发指令前 `_drain()`。QML 波特率读 `LightBridge.defaultBaud`；自动选口用 `pickDefaultPort`（优先 /dev/ttyUSB*，别用 ports[0]）。
- `stage_bridge.py` — X/Y 平台（WiFi/TCP，异步走事件循环，行协议与 USB 控制台一致，连上交口令→`#OK/#ERR`）。⚠ **本文件是唯一换算边界**：界面 mm ↔ 固件度，`1mm = 11.25°`、`1600 counts/mm`。移动一律**非阻塞 `move`**（绝不用 `g0`）；急停 `_enqueue(front=True)` 且永不禁用；回零 `home corner … nowait`；连接期只发只读命令（参数登记走 `hcfg` 只写 RAM）；预设位置存 `QSettings("DuAD","DuADSoftware")`（测试必须注入隔离 settings，见 docs/19 §1）。量程只从 `uiMaxRpm/uiMaxAcc` 读（§19）。
- `zstage_bridge.py` — Z 轴平台，**另一块板子**、独立 IP/口令/连接。**零换算**（固件就是 mm，别抄 11.25）。连接期只发只读（`json` + `zsign`）；量程读 `uiMaxRpm(1200)/uiMaxAcc(200)/uiMinAcc(1)`（**加速档 0=直接启动，界面必须排除**）。`@` 开头的驱动器帧行走独立分支（§20），`PROTO_CAP=400`。
- `algorithm_bridge.py` — 测试推理桥：模型默认 null（DetectPage 自选 .onnx）；**阈值优先级 = ONNX metadata > `*.threshold.json` > 1.7**；`maskReady` 先于 `inferenceReady` 发；热力图固定尺度（`<模型>.scale.json`：模型旁 → `backend/model_scales/`）；**切换模型内存三件套**：`enable_cpu_mem_arena=False` + `del old` + `gc.collect()` + `malloc_trim(0)`；`predict_frame` 供实时管线（`_infer_lock`/`_build_lock` 防并发双 session）；`unloadModel()` 卸载。
- `onnx_infer.py` — 无 torch 推理：预处理（Resize+CenterCrop+ImageNet 归一化）在客户端；**后处理（上采样+高斯平滑）已内化进新 ONNX 图**（旧 patch 级模型向后兼容走 `_gaussian_blur`）。**分数为判别器负输出的 patch max——带符号、无界、未归一化**（正常 ≈ -1~1.5、异常 > 2），**不要 clamp/min-max 到 0~1**。target_size 默认 518 须与导出一致。
- `proto_log.py` + `proto_hub.py` — 公用协议框：两路合并带 `[XY]`/`[Z]` 前缀；单路 PROTO_CAP=400、合并 HUB_CAP=800；`lineAdded` 逐行发、`changed` 节流 80ms（§27）；能力（`supportsTrace`）从桥上报。
- `mqtt_bridge.py` / `stage`/`zstage` 各自的回归测试见下。

## 关键约定（高危"不要动"清单）

- **main.py venv 自检 execv**：不要绕过；打包版 frozen 分支的 `Path("Scripts","python.exe")` 写法不要改回 str/str 相除。
- **两处 Fusion 强制**：`QT_QUICK_CONTROLS_STYLE=Fusion`（main.py `__main__` 块，QGuiApplication 创建前）——KDE Breeze 与 Qt 6.11 QML 控件不兼容（ComboBox 下拉空白）；Windows 打包无它则控件空白。**不要移除**。
- **颜色唯一来源** `DuAD_Software/Colors.qml`：`setTheme/setPreset` 运行时切换，全部颜色走 ColorAnimation（`animDuration`）。禁止硬编码 `#rrggbb`。卡片层级另有一组令牌：`cardBg / cardBorderStrong / cardShadow / accentSoft / textOnAccent / cardDangerBorder`。
- **不要用 shader 特效做质感**：`RectangularShadow`、`ColorOverlay` 在**软件渲染后端下整个不画、也不报错**，而 `render_page.py` 与 Jetson 都可能走软件渲染（docs/19 §32）。卡片"浮起"一律用 `pages/components/CardSurface.qml`（白底 + 1px 描边 + 普通矩形硬阴影）；按钮里"图标+文字"用 `pages/components/IconText.qml`，**不许拿 `⏻ ⌂ ■ ▲` 这类 Unicode 字形当图标**（依赖字体收录、大小不齐、不能单独染色）。
- **图标在不在，只能靠断言不能靠截图**：offscreen 下 `IconImage` 从来是空的。`tests/test_stage_page.py` 的 12e 会逐个解析 `IconImage.source` 查文件存在 —— 改图标或搬文件后必须回跑它。
- **URL 解析**：main.py 设 `QML_COMPAT_RESOLVE_URLS_ON_ASSIGNMENT=1`，`pages/` 引 `images/` 必须写 `../images/`；**测试与渲染脚本同样要设**（否则图标静默消失，§22.1-3）。跨平台 URL 转换用 `_toFileUrl()/_fromFileUrl()`（DetectPage 已内置，新增 FileDialog 照抄）。
- **`.ui.qml` 仅供 Qt Design Studio**；StackLayout 子项顺序必须与 `navGroup.buttons` 一致（有静态校验钉着）。
- **ComboRow 的 model 用稳定 key（不翻译）**，显示文本走 `displayFunc`；**ComboBox 下拉高度用 `combo.count * 32 + 4` 同步计算**（异步 contentHeight 有 0 高死循环），改 delegate 高度同步改公式。
- **SliderRow 的滚轮只在 Ctrl/Shift 时生效**（否则冒泡给页面滚，§19-31）；**label 列固定 72px、数值列 80px**；**重量级操作挂 `released` 信号**（松开才发）；曝光 UI 单位 ms，写相机换算 μs；相机重置目标 = 曝光 20000μs / 增益 0dB / 帧率 1.0。
- **AppBridge 是跨页状态中枢**：`collectingOwner`（`""`/"collect"/"detect"/"stage"，后按者抢占，main.py `COLLECTING_OWNERS` 仲裁）。**新增持有者必须同时改三处**：元组、仲裁分支、页面申请/释放（漏一处 = 静默丢弃，完整说明见 docs/19 §5）；页面离开时必须释放（StackLayout 不销毁页面）。Python 侧对象必须保持引用防 GC。
- **QML 绑定里不要调函数**（无依赖追踪），用带 notify 的 Property（§6）。
- **嵌套布局的 `Layout.alignment`/`Layout.preferredWidth` 不可靠**：外面包普通 `Item` 承接，内部布局 `width: parent.width`；折叠状态放外层 Item；**嵌套 RowLayout 的 implicitWidth 是最小宽度**，窄列里先算好（§26-5、§29）。
- **多列宽度三条约束一起夹**（比例 / 上限 / 保中列下限），窄屏先保中间那列；并排卡片用 `GridLayout` + 动态 `columns`（§19-30）。**控件不许一律 fillWidth**（动作按钮用 implicitContentWidth+留白+行尾弹簧）（§19-26）。
- **相机 ROI**：写入前 stopGather、写完读回校验、延迟 200ms 自动 startGather（失败重试 2 次，main.py 1.5s 兜底）；分辨率切换走 **BINNING**（视野不变），**不要**用 OFFSET+WIDTH 窗口裁剪（会放大画面丢视野）。
- **实时采集优先于测试推理**（DetectPage 自增 `_testSession` 作废在途结果）。
- **相机连接中文案居中**是卡片级覆盖层（CameraCard/LightControllerCard/CloudServerCard 模式，照抄别放 RowLayout）。
- 全局字体 `fonts/wqy-microhei.ttc` 由 main.py 注册，QML 无需指定 family。

## i18n（新增 qsTr 必做，否则英文/繁体缺翻译）

源文本 = 简体中文；`LANG_FILES`：0=en / 1=zh_CN(源) / 2=zh_TW。**必须从仓库根目录运行**：

```bash
LUPDATE=DuAD_SoftwareContent/pyqml/lib/python3.14/site-packages/PySide6/lupdate
LRELEASE=DuAD_SoftwareContent/pyqml/lib/python3.14/site-packages/PySide6/lrelease
"$LUPDATE" -no-obsolete DuAD_SoftwareContent/App.qml DuAD_SoftwareContent/MainuiRoot.qml DuAD_SoftwareContent/MainWindow.ui.qml DuAD_SoftwareContent/pages -ts translations/app_en.ts
python scripts/gen_translations.py      # 新字符串要同时进 EN 和 TW 两个 dict
"$LRELEASE" translations/app_en.ts translations/app_zh_TW.ts
```

- ⚠ **跑 lupdate 必须连 stderr 一起看**：QML 语法错误只打一行 `error:` 到 stderr（stdout 照样报 Found N），**该文件全部字符串会从 .ts 里静默消失**。
- `gen_translations.py` 两条守卫任一不满足 `exit 1`：① EN/TW dict 逐条对齐（改文案两边一起改，否则繁体显示英文）；② 生成结果不许有空翻译。
- 语言持久化 `QSettings("DuAD","DuADSoftware")`，切换走 `AppBridge.setLanguage()`。

## 冒烟测试（offscreen，不需要真硬件）

```bash
source DuAD_SoftwareContent/pyqml/bin/activate
QT_QPA_PLATFORM=offscreen python3 -u tests/test_stage_page.py      # StagePage 端到端 + 导航三处同步 + 几何守卫
QT_QPA_PLATFORM=offscreen python3 -u tests/test_zstage_page.py     # Z 半边端到端 + 几何守卫
QT_QPA_PLATFORM=offscreen python3 -u tests/test_stage_bridge.py    # StageBridge 协议（含 14f4 连接序列反向断言）
QT_QPA_PLATFORM=offscreen python3 -u tests/test_zstage_bridge.py   # ZStageBridge 23 组
QT_QPA_PLATFORM=offscreen python3 -u tests/test_proto_hub.py       # 公用协议框
QT_QPA_PLATFORM=offscreen python3 -u tests/test_light_bridge.py    # 假光源控制器（只认 19200 + 长应答分片）
QT_QPA_PLATFORM=offscreen python3 tests/render_page.py /tmp/p.png 1680 1700 both --dump   # 渲染 + 量几何（改版面必看）
python3 tests/render_icons.py /tmp/icons.png      # 图标接触表（**唯一**能目检图标的手段，见 §32）
```

平台相关测试自带**进程内假板子**（严格按固件行为建模；替身的诚实度决定测试能发现什么，§11）。改版面/组件后先跑两个 page 测试再 render_page 目检。

通用坑：**必须 `python -u`**；交互优先 `btn.clicked.emit()`（`MouseArea.clicked` 带 MouseEvent 参数 emit 不了，要 `QTest.mouseClick`，且是窗口坐标、点前先滚进视口，§28）；找控件用 **objectName**（className 是 `Button_QMLTYPE_*` 不稳定）；**Repeater delegate 不在 QObject 树**，走可视树 `childItems()`；FakeBridge 必须存变量防 GC；offscreen 屏幕 800×800 会裁窗口宽（断言前先设窗口尺寸，§25）；进程末尾的 `TypeError ... of null` 多是退出噪音，看加载完成那一刻的 warnings；`image://camera/...` 无 provider 属预期噪音。

## 「平台控制」页（v4.3，2026-09-28 美化：图标接线 + 卡片质感）

**卡片一律用 `pages/components/CardSurface.qml`**（白底 `cardBg` + 1px `cardBorderStrong` + 2px 矩形硬阴影），内嵌块（折叠头/图标 chip）用 `accentSoft`，页面底保持白 —— 层次 = 页面 → 卡片 → 内嵌块。**已连接卡片的描边是 `cardDangerBorder`（淡红），不要用 `statusDisconnected` 正红**（1px 正红围一圈像故障告警，而"已连接"是好状态）。**按钮一律走 `pages/components/ThemedButton.qml`**（`tone: neutral|soft|danger|dangerSoft`）—— 本页曾有一批 Button 没写 `background`，吃的是 **Fusion 默认灰**、完全不吃 `Colors`（§19-33）；它的 contentItem 内部用 `IconText`，不许回退到 `⏻ ⌂ ■ ▲` 字形。**禁用态换颜色（`Colors.textPlaceholder`），不许压透明度**（实心图标会淡成浅影）。折叠头 = `FoldHeader`（accentSoft 条 + `下单箭头.svg` 旋转 0/−90 表示展开/收起 + 红色 pill badge）；**折叠体里的面板 `showTitle: false`**（标题由折叠头负责，别一个名字说两遍）。连接卡状态行分层：`● 已连接`（加粗、状态色）→ 闸门红项 → 地址 11px 灰 → `闪电/信号格` 图标 + 数值；**未连接时 `gates` 传 `[]`**（"未连接"由状态词说，别再出红字）。遥测拆成 `voltage`/`rssi` 独立属性（窄列 `width<300` 时整组让位）。**预设位置在左列「二轴相机平台状态」折叠节里**（`showTitle: true`，2026-09-28 用户要求从「高级」搬来）；「高级」只剩协议显示。Z 轴「向上/向下」不 fillWidth（188 居中）；「移动到该位置」居中 + `tone: soft`。`StagePresetPanel` 要能在 164px 内容宽下活：边距 12、坐标文本 `width>=230` 才显示、「名称+记录」用 `GridLayout` 动态列数（宽了并排、窄了竖排）。

## 「平台控制」页（v4.2，2026-09-28 第二张手绘稿：三列）

**状态只在连接卡里**（`StageControllerCard.gates`，只画红项，§19-26/30）；**页面滚动条占 14px 且贴在窗口外缘**；
**两张控制卡 `AlignHCenter | AlignTop`**（只写 HCenter 时矮的那张会垂直居中、顶边错开）。
**三列宽度三条约束一起夹**（§19-30）：`_effSide = max(160, min(420, 页宽×0.24, (页宽−2×间距−380)/2))`，
中列吃满剩余 —— 宽屏看比例（手绘稿 24%:52%:24%：1680→403/850）、窄屏先保中列（730→163/380）。
⚠ v4.1 的"21% + 上限 340"全屏时给出 340/976：连接卡副标题被裁、预览被拉成大黑框（详见 §19-30）。左右列内容：连接卡(紧凑) → 状态行 → 红色提示/错误条 → 「…状态」折叠节(默认收) → 「…设置」折叠节(默认收，内含网络/工作区/速度表单)。中列：实时预览（卡片占满中列宽，画面 2448×2048 等比、**允许黑边**，高度上限 0.45×页高/560——防吃掉首屏操作区）→ 二轴/Z轴手动控制卡并排（各半、封顶 480、居中）→ 「高级」折叠节（预设位置 + 公用协议框）。**整页一条滚动条**。折叠节组件 = 页内 `FoldHeader` + `FoldBody`（visible 放外层 Item）；齿轮/提示条 = `_openFold()`（展开+滚过去，断言 contentY）。真机联调注意（Z 板）：json 字段名、trace 帧镜像两行一组、点动 rpm/acc 与固件实际值。

## 页面状态

- **已联调**：Camera（搜索/连接/参数读写，MER2-501-79U3C-L 2448×2048）、Light（RS-232，2026-09-22 真机：19200、`$L{n}={v}#`）、Comm（MQTT，含 TLS/用户名密码）、Collect（定时保存，与 Detect 互斥）、Settings、Detect（实时采集+ROI+ONNX 实时推理热力图/分数/定位）。
- **Stage**：✅ 页面+桥完成，测试全过，真机已联调（2026-09-13）。⚠ 0x91 方向字节未定（Byte1/Byte2，见 docs/17 §11.2）。Z 板**真机未联调**（协议层假板子已验）。
- 后端细节与设计取舍见 `docs/17`（二轴）、`docs/18`（Z 轴）、`docs/19`（踩坑）；推理链路见 `docs/10~13`；打包见 `docs/14/15`、`docs/Jetson部署.md`。

## Windows 打包（PyInstaller onedir）

```powershell
DuAD_SoftwareContent\pyqml_win\Scripts\python.exe -u scripts\package_win.py 1.0.0
```

要点（详见 `docs/15`）：GPU 库不打包（excludes nvidia*/tensorrt*，无库回退 CPU）；frozen 路径 `_content_dir()/_backend_root()/_translations_root()` 返回 `sys._MEIPASS`（改布局三处同步）；`console=False` 时 stdout/stderr 重定向 `%USERPROFILE%\DuAD_app.log`（**buffering=1 行缓冲**，否则强杀丢日志）；**必须强制 Fusion**；spec 用 `Path(SPECPATH).parent`；图标 `favicon.ico`。测试 exe：`QT_QPA_PLATFORM=offscreen` 启动看日志有 `[INFO] 启动成功` 且无 `QML ERROR`。

## Jetson（aarch64）

环境 `~/micromamba/envs/duad`（conda-forge PySide6 + NVIDIA 索引 onnxruntime；`pyqml/` 目录不要建）。JP7.2 是 CUDA 13：需补装 cu12 的 nvidia-* pip 包并把 `site-packages/nvidia/*/lib` 注入 LD_LIBRARY_PATH（main.py 已自动）。TRT 数值正确性依 JetPack 版本（JP6.2 勿用 TRT；JP7.2+ 可用，`DUAD_PREFER_TRT=1`/`DUAD_TRT_FP16=1` 已在 run_jetson.sh 默认开，引擎缓存分目录）。arm64 无 DxImageProc：Bayer 走自编 `libbayer_demosaic.so`（改 .c 必须重编译）。udev 规则必须装且重新插拔。详见 `docs/Jetson部署.md`、`docs/16`。



最后：

1. 在每次完成一个重要的任务节点的时候（你可以询问我是否提交），进行一次git的commit操作，并根据任务节点的内容在提交的时候进行描述。
2. 我希望最后你告诉我的结果包括两个部分，第一个就是告诉我你干了什么，可以通过通俗的语言来解释。第二个是你修改了哪些文件。第三个是结果怎么样。
