# AGENTS.md

你是 10 年经验的 UI 设计工程师 + 上位机开发工程师 + 算法工程师。

项目：PySide6 + QML 工业异常检测上位机（论文《融合Dinov2与双分支训练架构的工业异常检测》配套软件）。
架构入口见 `docs/00-目录.md`、`docs/01-项目总览与运行.md`；**踩坑与设计规矩见 `docs/19-踩坑实录与设计规矩.md`**（编号沿用该文，本文只留结论 + 指针）。

## 运行

```bash
source DuAD_SoftwareContent/pyqml/bin/activate
python DuAD_SoftwareContent/main.py
```

Windows：`pyqml_win\Scripts\python.exe -u main.py`；Jetson：`bash run_jetson.sh`（部署/问题见 `docs/Jetson部署.md`、`docs/16`）。

- 依赖在 `requirements.txt` / `requirements-cpu.txt` / `requirements-gpu.txt`；有 `README.md` 与 GitHub Actions release；**没有** `pyproject.toml` / pytest CI。
- ⚠ 不要绕过 main.py 启动自检：非 venv 会 execv 切到 `pyqml/bin/python`（Windows 为 `pyqml_win/Scripts/python.exe`），两个 venv 可共存。判断 GPU：看日志 `模型预热完成（['CUDAExecutionProvider', ...]）`；系统 python3 能跑但走 CPU。
- Windows onnxruntime-gpu **不自带 CUDA 库**：装 `nvidia-cublas-cu13` / `nvidia-cudnn-cu13` / `nvidia-cuda-runtime`，缺失静默回退 CPU；CUDA 13 要求驱动 ≥585。TRT 无 Windows pip 包：装 TensorRT 10.16.x，用 `TENSORRT_LIB_DIR` 指定（开发机也可自建 `backend/libs_win_tensorrt/bin`）。详见 `docs/15`。
- `pyside6-lupdate` / `lrelease` 的 wrapper 是绝对路径，项目搬移/换机后必报 bad interpreter；用 `DuAD_SoftwareContent/pyqml/lib/python3.14/site-packages/PySide6/` 里的原生二进制。
- 本代码目录是独立 git 仓库（根在 `DuAD_Software/`）；上级 `研究生论文/` 是论文文档仓库。

## 布局

- `DuAD_SoftwareContent/` — `main.py` + 全部 QML：`pages/`（7 页）、`pages/components/`、`pages/minipages/`。QML 模块是 **`DuAD_SoftwareContent/DuAD_Software/`**（`qmldir` 声明 `singleton Colors/Constants`），模块目录名必须与 module 名一致。
- `backend/Src/` — 相机/光源/平台/云/算法各桥。`backend/gxipy/` 是大恒 SDK wrapper；`backend/libs*` 是 SDK 动态库。
- ⚠ 相机 SDK 缺失不崩溃：`camera.py` 的 SDK import 用 try/except（含 `KeyError`）降级 stub，**不要回退**。
- ⚠ `.cti` 传输层必须齐全（`libs/`、`libs_win/GenTL/`、`libs_arm64/`）：缺它 `gx_init_lib` 返回 -1、枚举 0 台。Linux 大分辨率采集 -1010 先跑 `sudo bash scripts/set_usbfs.sh`。aarch64 用 `libs_arm64/`。
- ⚠ `pyqml/bin/activate` 已注入 `LD_LIBRARY_PATH=backend/libs`（运行时设置无效）；activate 里 `VIRTUAL_ENV` 是旧路径，不要依赖。
- 训练/导出/标定代码不在本仓库：https://github.com/ouyangpingning/DuAD 。

## 各桥要点

- `camera_bridge.py` — 相机桥：search/connect/feature/startGather/applyRoi；每帧写 `frameProvider` 并发 `rawFrameReady`。bool 特征写字符串 `"true"/"false"`；`GX_FLOAT_GAMMA_PARAM` 在 MER2 只读。**Bayer 排列按 `GX_ENUM_PIXEL_COLOR_FILTER` 选，读不到才查 pixel_format；Windows DxImageProc 的 R/B 与 Linux 相反，`os.name=='nt'` 的 `_SWAP_RB_BAYER` 不要移除**。低帧率先查曝光，再关吞吐量限制，最后查 USB2 口/线。
- `light_bridge.py` — RS-232 光源：**波特率必须 19200**（其它波特率控制器一个字都不回，而 `serial.Serial()` 永远打开成功 → 静默“已连接没反应”）。三条不要动：① `connectSerial` 后发 `$RD=9999#` 校验；② `_read_reply` 收到半截不要缩短等待（长应答跨 161ms）；③ 发指令前 `_drain()`。QML 波特率读 `LightBridge.defaultBaud`；自动选口用 `pickDefaultPort`（优先 `/dev/ttyUSB*`，别用 `ports[0]`）。
- `stage_bridge.py` — X/Y 平台（WiFi/TCP，行协议与 USB 控制台一致，口令→`#OK/#ERR`）。**唯一换算边界**：`1mm = 11.25°`、`1600 counts/mm`。移动一律非阻塞 `move`（绝不用 `g0`）；急停 `_enqueue(front=True)` 永不禁用；回零 `home corner … nowait`；连接期只发只读命令（`hcfg` 只写 RAM）；预设/轨迹存 `QSettings("DuAD","DuADSoftware")`（测试注入隔离 settings，docs/19 §1）。轨迹存**参数**不存点列表，所有绝对移动收敛到 `_issue_move()`；`pathCanRun/pathHint` 用单独的 `pathEnableChanged`。`gridRows/gridCols/setGrid/snapToGrid`、`stage_traj.snap_to_cell` 已删除（`sample_grid` 保留）。
- `ble_provision.py` — **BLE 只做配网，不做运动**（docs/20）。板卡名固定：XY=`DuAD-XY-55C6`，Z 用 `DuAD-Z-` 前缀；界面没有板卡下拉。刷新→自动连→自动 `wifi status`+`wifi scan`→列表点选→密码弹窗。GATT：service `d0ad0001-...`、RX `d0ad0002`、TX `d0ad0003`；第一条交 token，之后复用 `wifi status/scan/set/rollback`。⚠ 配网前先断 TCP + 暂停 `json` 轮询（`bleScan()` 已做，运动/回零中拒绝）；成功后用新 IP 改 `host` 自动重连。固件 `ble_prov` 只放行 wifi/net/ver/help；`wifi_bak` 备份，25s 没 IP 自动回滚；`wifi scan` 按行输出（`nets_start`/`net`/`nets_end`）。
- `stage_traj.py` — 轨迹几何（纯函数）+ `PathRunner` 序列器，**刻意不碰 Qt**（注入闭包/时钟，可假时钟单测）。⚠ 必须“发一段→等板子报到位→再发下一段”：固件 `0xF3` 是完整梯形曲线，且在途再发 `move` 会被静默覆盖目标；时序三规则见 docs/17 §14.4。
- `zstage_bridge.py` — Z 轴，**另一块板子**、独立 IP/口令。**零换算**（固件就是 mm）。连接期只发只读（`json`+`zsign`）；量程读 `uiMaxRpm(1200)/uiMaxAcc(200)/uiMinAcc(1)`（加速档 0=直接启动，界面必须排除）。⚠ `@` 开头的驱动器帧行必须在 `_handle_line` 第一分支丢掉（`trace` 可能开着，混入会让命令判定错位）。回零参数：`setHomeParams(rpm,ma,tmo)`→`zset home`，范围和经验区（60/300，推荐 100）全从桥里读；板子实际值走 json `hma/hrpm/htmo`。`setHomeDir("up"/"down")`→`zhome <dir> nowait` 只存本机；`setAutohome()` 不带方向。`SLOW_CMD_TIMEOUT_MS["stop"]=8000`（固件打断最坏 ~2.5s；板子 TCP 任务串行，桥抢先写救不了急停，docs/18 §7.7）。
- `algorithm_bridge.py` — 测试推理：模型默认 null（DetectPage 自选 .onnx）；阈值优先级 = ONNX metadata > `*.threshold.json` > 1.7；`maskReady` 先于 `inferenceReady`；热力图尺度 `<模型>.scale.json`（模型旁→`backend/model_scales/`）；切换模型内存三件套 `enable_cpu_mem_arena=False` + `del old` + `gc.collect()` + `malloc_trim(0)`；`predict_frame` 供实时管线（`_infer_lock`/`_build_lock` 防双 session）；`unloadModel()` 卸载。
- `onnx_infer.py` — 无 torch：预处理（Resize+CenterCrop+ImageNet 归一化）在客户端；后处理（上采样+高斯平滑）已内化进新 ONNX 图（旧 patch 模型走 `_gaussian_blur`）。**分数 = 判别器负输出的 patch max，带符号、无界、未归一化**（正常≈-1~1.5，异常>2），**不要 clamp/min-max 到 0~1**。`target_size` 默认 518 须与导出一致。
- 回归测试见 `tests/`；不再有单独的 mqtt_bridge 测试，MQTT 走 CommPage 手工联调。

## 关键约定（高危“不要动”）

- **main.py venv 自检 execv** 不要绕过；frozen 分支 `Path("Scripts","python.exe")` 写法不要改回 str/str 相除。
- **Fusion 强制**：`os.environ.setdefault("QT_QUICK_CONTROLS_STYLE","Fusion")`（main.py，QGuiApplication 创建前；全仓库就这一处）。移除会导致 KDE/Windows 控件空白。
- **颜色唯一来源** `DuAD_Software/Colors.qml`：`setTheme/setPreset` 运行时切换，全部走 ColorAnimation；禁止硬编码 `#rrggbb`。卡片令牌：`cardBg/cardBorderStrong/cardShadow/accentSoft/textOnAccent/cardDangerBorder`。实色强调：`accent/accentHover/accentPressed/accentContent/accentText`——淡色铺大面积，实色只点睛；强调底上的字用 `accentContent`（暗色主题是深字），**不是** `textOnAccent`。属性名别用 `onXxx`。对比度由 `tests/test_design_tokens.py` 钉住，改配色必跑。
- **卡片/按钮**一律 `CardSurface.qml` / `ThemedButton.qml`（白底 + 1px 描边 + 普通矩形硬阴影，不用 shader）。按钮“图标+文字”用 `IconText.qml`；**不许拿 `⏻ ⌂ ■ ▲` 等 Unicode 字形当图标**。
- **别拿探针的“不渲染”当结论**：探针能证“能”，很难证“不能”；否定要在真实页面渲染（`render_page.py`/`render_pages.py`）里做（docs/19 §32）。
- **图标路径靠断言兜底**（写错会静默不画）：`tests/test_ui_theme.py` 逐个解析 `IconImage.source`，`test_stage_page.py` 12e 同理；改图标/搬文件必须回跑。
- **URL 解析**：main.py 设 `QML_COMPAT_RESOLVE_URLS_ON_ASSIGNMENT=1`；相对当前 QML 文件：`pages/` 用 `../images/`，`pages/components/`、`pages/minipages/` 用 `../../images/`；测试/渲染脚本也要设。跨平台 URL 用 `_toFileUrl()/_fromFileUrl()`。
- **`.ui.qml` 仅供 Qt Design Studio**；StackLayout 子项顺序必须与 `navGroup.buttons` 一致（有静态校验）。
- **ComboRow** model 用稳定 key，显示走 `displayFunc`；ComboBox 下拉高度用 `combo.count * 32 + 4` 同步计算（异步 contentHeight 会 0 高死循环），改 delegate 高度同步改公式。
- **SliderRow** 滚轮仅 Ctrl/Shift 生效（否则冒泡滚页，§19-31）；label 列 72px、数值列 80px；重量级操作挂 `released`；曝光 UI 用 ms，写相机换算 μs；相机重置目标=曝光 20000μs/增益 0dB/帧率 1.0。
- **AppBridge 跨页状态**：`collectingOwner`（`""`/`collect`/`detect`/`stage`，后按者抢占）。新增持有者必须同时改三处：`COLLECTING_OWNERS`、仲裁分支、页面申请/释放（漏一处=静默丢弃，docs/19 §5）；页面离开必须释放（StackLayout 不销毁页面）；Python 侧对象保持引用防 GC。
- **QML 绑定里不要调函数**（无依赖追踪），用带 notify 的 Property（§6）。
- **进了 Layout 的项尺寸写 `Layout.preferredWidth/Height`**：写 `width/height` 会被覆盖成 implicit（`IconImage` 是 24），不报错不警告（§19-35）。在 `Item/Rectangle` 里 `anchors.centerIn` 的不受影响。
- **`IconImage` 改尺寸要连 `implicitWidth/implicitHeight` 一起给**（创建时定死）；创建后被布局改尺寸可能画不出。10px 级箭头改用普通 `Image`（见 v4.6），颜色别改成浅色（全站“图标深、标签灰”）。
- **嵌套布局的 `Layout.alignment/preferredWidth` 不可靠**：外套普通 `Item`，内部布局 `width: parent.width`；折叠状态放外层 Item；嵌套 RowLayout 的 implicitWidth 是最小宽度（§26-5、§29）。
- **多列宽度三条约束一起夹**（比例/上限/保中列下限），窄屏先保中列；并排卡片用 `GridLayout` + 动态 `columns`（§19-30）。控件不许一律 fillWidth（动作按钮用 implicitContentWidth+留白+行尾弹簧，§19-26）。
- **相机 ROI**：写入前 stopGather、写完读回校验、延迟 200ms 自动 startGather（失败重试 2 次，main.py 1.5s 兜底）；分辨率切换走 **BINNING**（视野不变），不要 OFFSET+WIDTH 裁剪。
- **实时采集优先于测试推理**（DetectPage 自增 `_testSession` 作废在途结果）。
- **相机连接中文案居中**是卡片级覆盖层（CameraCard/LightControllerCard/CloudServerCard 模式，照抄别放 RowLayout）。
- 全局字体 `fonts/wqy-microhei.ttc` 由 main.py 注册，QML 无需指定 family。

## i18n（新增 qsTr 必做）

源文本=简体中文；`LANG_FILES`：0=en / 1=zh_CN(源) / 2=zh_TW。**从仓库根目录运行**：

```bash
LUPDATE=DuAD_SoftwareContent/pyqml/lib/python3.14/site-packages/PySide6/lupdate
LRELEASE=DuAD_SoftwareContent/pyqml/lib/python3.14/site-packages/PySide6/lrelease
"$LUPDATE" -no-obsolete DuAD_SoftwareContent/App.qml DuAD_SoftwareContent/MainuiRoot.qml DuAD_SoftwareContent/MainWindow.ui.qml DuAD_SoftwareContent/pages backend/Src/*.py DuAD_SoftwareContent/main.py -ts translations/app_en.ts
python scripts/gen_translations.py
"$LRELEASE" translations/app_en.ts translations/app_zh_TW.ts
```

- ⚠ 跑 lupdate 必须连 stderr 一起看：QML 语法错误只打一行 `error:`，该文件全部字符串会从 .ts 静默消失。
- ⚠ Python 侧必须逐个列 `.py`：给目录时 lupdate 不扫 `.py`，会静默漏掉桥接层文案。改完看 “Found N source text(s)” 是否增长。
- 桥接层界面文案写 `self.tr("…")`（在方法里，不要模块级 dict）；`_log()`/`logMessage`/`logLines` 等画在诊断面板的也是用户可见文案，要翻。纯 `print()` 终端日志、板子原始帧（`→ json`/`← {...}`/`@TX …`）不翻。
- 批量改写脚本两个坑：f-string 内外层中文互覆盖（多轮只改最内层）；`ast` 的 col_offset 是 UTF-8 字节偏移（在 bytes 上拼接）。
- `gen_translations.py` 两条守卫：EN/TW dict 逐条对齐；生成结果不许空翻译。语言持久化 `QSettings("DuAD","DuADSoftware")`，切换走 `AppBridge.setLanguage()`。
- 自检：临时 `AppBridge.setLanguage(0)`，遍历可视树 Text/TextField 的 `text`/`placeholderText` 找中文（2026-10-08 用它查出唯一漏项来自 `ZStageBridge.datumHint`）。

## 冒烟测试（offscreen，无需硬件）

```bash
source DuAD_SoftwareContent/pyqml/bin/activate
QT_QPA_PLATFORM=offscreen python3 -u tests/test_stage_page.py
QT_QPA_PLATFORM=offscreen python3 -u tests/test_zstage_page.py
QT_QPA_PLATFORM=offscreen python3 -u tests/test_stage_bridge.py
QT_QPA_PLATFORM=offscreen python3 -u tests/test_zstage_bridge.py
QT_QPA_PLATFORM=offscreen python3 -u tests/test_light_bridge.py
QT_QPA_PLATFORM=offscreen python3 -u tests/test_stage_traj.py
QT_QPA_PLATFORM=offscreen python3 -u tests/test_ble_provision.py
QT_QPA_PLATFORM=offscreen python3 -u tests/test_ui_theme.py
QT_QPA_PLATFORM=offscreen python3 -u tests/test_design_tokens.py
QT_QPA_PLATFORM=offscreen python3 -u tests/test_detect_page.py
QT_QPA_PLATFORM=offscreen python3 tests/render_page.py /tmp/p.png 1680 1700 both --dump
python3 tests/render_pages.py camera /tmp/camera.png 1440 980
python3 tests/render_icons.py /tmp/icons.png
```

平台测试自带进程内假板子（严格按固件建模；替身诚实度决定能发现什么，§11）。改版面/组件后先跑两个 page 测试再 render_page 目检。

通用坑：必须 `python -u`；交互优先 `btn.clicked.emit()`（`MouseArea.clicked` 带 MouseEvent，要 `QTest.mouseClick` 且是窗口坐标、先滚进视口，§28）；找控件用 objectName（className 不稳定）；Repeater delegate 不在 QObject 树，走 `childItems()`；FakeBridge/`engine.rootObjects()[0]` 必须存变量防 GC；offscreen 800×800 会裁窗口，加载后还会改 QML 里的 width/height，断言/截图前用 `win.setProperty("width",…)` 再设一次（§25）；退出时的 `TypeError ... of null` 多是噪音；`image://camera/...` 无 provider 属预期。

临时产物丢仓库根、文件名以 `_` 开头（`.gitignore` 的 `/_*` 兜住）：`_probe_*.py`、`_render_*.png`、`_keys*.txt`；长期截图放 `.uicap/`。跑完删掉自己的 `_*`：`git status --short` 干净才算收工。

## 「平台控制」页（当前：v4.6）

- **设置面板内部按功能分区折叠**（`pages/components/CollapsibleHeader.qml`）：二轴 = Wi-Fi/网络/工作区/速度/轨迹速度/关机行为；Z = Wi-Fi/网络/速度/软限位/自动回零参数/上电行为。默认只展开 Wi-Fi，「应用设置」始终在底部。
- 箭头用普通 `Image`（`images/折叠箭头.svg`，中灰），**不要换回 `IconImage`**（10px ColorOverlay 在此上下文不渲染）。
- 分区标题要有 objectName（`xyNetFold`/`zHomeFold`…）；测试用 `toggled.emit()` 验展开/收起。
- Z 的限位电流/超时警告在「自动回零参数」里；测试断言 visible 前先 `zSetupPanel.homeOpen = true`。

### 轨迹与网格（v4.5 要点）

- `TrajectoryGridCard` 五模式：点选/网格/圆/矩形/多段线，录制按钮在右上角；卡可折叠（`trajFold`/`trajBody`），默认展开。
- **背景只有标尺、没有格线**；`_niceStep()` 取 1/2/5×10ⁿ，首末刻度落工作区边界。点选不吸附格心，mm 由 `_mmOf()` 算；`sample_grid()` 保留（网格模式生成点阵）。
- **只有点选模式会因单击移动**；网格/圆/矩形/多段线只画+预览，提交走「存为轨迹」→「执行」。
- 预览一律从参数推导（`pCx/pCy/pR`、`rectA/rectB`、`draft`），不从上次拖拽点；拖动实时写参数、松手 `_syncInputs()`。三个圆参数框必须显式 `id`，不写 `text:` 绑定（否则 Binding loop，§37）。
- 圆半径夹到“圆心到四边最小距离”；Z 高度条点/拖只画预览，**松手才下发一条** `moveTo`，闸门用 `canMove`，原因用 `datumHint`；绝不能在拖动中连发。
- 卡与两张手动控制卡用 `flat: true` 住进 `FoldHeader`+`FoldBody`；`flat` 只置空底板 `color/borderColor/shadowed`，**绝不能写 `visible: !flat`**（`mainCol` 会整卡空白）。两张手动卡默认展开（12a2 首屏保证靠它）。
- 台面纹理 `images/台面_哑光喷砂.jpg`（`scripts/make_texture.py` 生成，中性灰 0.5、四边淡出、512²），要内缩 `radius` 放；`test_stage_page.py` 12g 钉住文件存在。
- 轨迹 rpm/acc 在「平台设置 → 轨迹速度」（`setPathSpeed`，存 `stage/path_rpm|path_acc`）；手动 acc 默认 1，轨迹默认 1200/30，刻意分开。录制中折叠头 badge 显示「录制中」。
- `MouseArea` 加 `preventStealing: true`（否则外层 Flickable 抢拖动，§38）；折线锚在段起点（`x:_px(x1), y:_py(y1)-lineWidth/2`，§39）；整张卡不用 `Canvas`，标尺/折线用 Rectangle/Text。
- 版面上下堆叠（台面+Z 条→chips→提示→参数→名称/存为轨迹→速度+录制→列表→执行）；台面组 `GridLayout{Layout.fillWidth:false; Layout.alignment:Qt.AlignHCenter}`；`ColumnLayout` 里成对 `InputRow` 竖排；`InputRow` 宽度用外层普通 Item 定宽。
- 网格点选替代不了十字点动（1px≈1.1~1.9mm），精调仍用 0.1mm 档；12a 首屏超出量基线 2.0 倍（实测 1.84），`render_page.py ... card` 目检；详见 docs/17 §14。

### 版面（v4.2 三列，现行）

- 状态只在连接卡 `StageControllerCard.gates`（只画红项，§19-26/30）；页面滚动条 14px。
- 三列：`_effSide = max(160, min(420, 页宽×0.24, (页宽-2×间距-380)/2))`，中列吃满剩余；宽屏约 24:52:24，窄屏先保中列。两张手动卡 `AlignHCenter|AlignTop`，中列控制卡并排、封顶 480。整页一条滚动条；外折叠用页内 `FoldHeader`+`FoldBody`，齿轮/提示用 `_openFold()`（展开+滚过去）。

## 页面状态

- **已联调**：Camera（MER2-501-79U3C-L 2448×2048）、Light（RS-232 19200、`$L{n}={v}#`）、Comm（MQTT/TLS）、Collect（定时保存，与 Detect 互斥）、Settings、Detect（实时采集+ROI+ONNX 热力图/分数/定位）。
- **Stage**：X/Y 与 **Z 轴均已真机联调**（Z 板状态已由用户确认，2026-11）；BLE 配网 XY 已真机跑通。协议细节见 docs/17/18。
- 推理链路 `docs/10~13`；打包 `docs/14/15`、`docs/Jetson部署.md`。

## Windows 打包（PyInstaller onedir）

```powershell
DuAD_SoftwareContent\pyqml_win\Scripts\python.exe -u scripts\package_win.py 1.0.0
```

要点（详见 docs/15）：GPU 库不打包（excludes nvidia*/tensorrt*，无库回退 CPU）；frozen 路径 `_content_dir()/_backend_root()/_translations_root()` 返回 `sys._MEIPASS`（改布局三处同步）；`console=False` 时 stdout/stderr 重定向 `%USERPROFILE%\DuAD_app.log`（buffering=1）；必须强制 Fusion；spec 用 `Path(SPECPATH).parent`；图标 `favicon.ico`。验收：`QT_QPA_PLATFORM=offscreen` 启动看日志有 `[INFO] 启动成功` 且无 `QML ERROR`。

## Jetson（aarch64）

环境 `~/micromamba/envs/duad`（conda-forge PySide6 + NVIDIA 索引 onnxruntime；Jetson 目标机上不要建 `pyqml/`）。JP7.2 是 CUDA 13：补装 cu12 的 nvidia-* pip 包并把 `site-packages/nvidia/*/lib` 注入 `LD_LIBRARY_PATH`（main.py 已自动）。TRT 数值正确性依 JetPack（JP6.2 勿用；JP7.2+ 可用，`DUAD_PREFER_TRT=1`/`DUAD_TRT_FP16=1` 已在 run_jetson.sh 默认开，引擎缓存分目录）。arm64 无 DxImageProc：Bayer 走自编 `libbayer_demosaic.so`（改 .c 必须重编译）。udev 规则必须装且重新插拔。详见 `docs/Jetson部署.md`、`docs/16`。

最后：重要节点按内容提交 git（可先询问）；汇报固定三段 = 做了什么 / 改了哪些文件 / 结果怎么样。
