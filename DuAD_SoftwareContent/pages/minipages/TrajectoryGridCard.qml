import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software
import "../components"

/*
    「轨迹与网格」卡 —— 二轴平台的**第二种操作方式**（2026-10-08 用户提出）。

    用户原话："把二轴平台设置成一个密集的网格……通过网格上的点击来控制相机的
    二轴平台运动。这样也就可以增加一些更复杂的运动了，比如画圆形、曲线、
    多段线等操作，还有记忆功能，用来将我的操作记录下来，进行重复。"
    一期**只做运动**：到点拍照、循环 N 次是二期的事。

    执行引擎是 Python 侧的 `stage_traj.PathRunner`（点列序列器），本文件只负责
    画图、收点击、显示进度 —— 一行运动逻辑都不放在 QML 里。

    ============================================================
    五个模式 + 录制（2026-10-10 第二版：按用户重画的 UI 改）
    ============================================================
      点选   **单击台面 → 相机立刻走过去**（只有这个模式会因点击而移动）
      网格   生成"巡检点阵"（每格中心一个点，可蛇形）→ 存为轨迹 → 执行
      圆     在台面上按住 = 圆心，拖 = 半径；也可直接输入半径/圆心
      矩形   在台面上按住拖出两个对角
      多段线 依次单击加顶点，可勾「平滑」走 Catmull-Rom 过点样条
      录制   不在 chips 里（右下角「录制」按钮）：它记的是**移动**——
             点选 / 十字点动 / 绝对定位 / 预设前往，都会被记成一条轨迹。
             ⚠ 圆/矩形/多段线**不移动**，所以那三个模式下录制什么也记不到，
               这不是 bug（用户 2026-10-10 问过这一条）。

    ⚠ **只有「点选」会因单击而移动**（2026-10-10 改）：
      以前网格模式也响应单击移动，于是"我在网格模式里点一下本来只是想看看"
      就会真的把台面开走。现在其余四个模式只负责"画 + 预览"，
      提交动作一律是「存为轨迹」→「执行」。

    ============================================================
    ⚠ 台面背景只有标尺、没有格线（2026-10-10 用户要求）
    ============================================================
    用户原话："二轴平台背景不要使用网格了，而是只留刻度标尺，这个标尺和初始化的
    工作区大小有关系"。所以：
      · 左边缘 = Y 标尺（+Y 向里 → 图上朝上，所以 0 在下、最大在上）；
      · 下边缘 = X 标尺（0 在左、最大在右）；
      · 刻度步长由 `_niceStep()` 按范围选 1/2/5×10ⁿ，**首末刻度必定是工作区边界**；
      · 主刻度带数字，其间每 1/5 步长一条无标签细刻度（只在屏幕间距够时画）。
    ⚠ 与之配套，**点选不再吸附格心**（用户 2026-10-10 选定）：背景没有格子了，
      吸附就失去视觉依据；而且不吸附精度更高（±1mm，比吸到 18mm 格心更准）。
      桥侧的 `snapToGrid()`/`snap_to_cell()` 与 `gridRows/gridCols` 已随之删除。

    ============================================================
    ⚠⚠ 折线为什么曾经是"一地乱线"（2026-10-10 修，务必别再改回去）
    ============================================================
    `SegLine` 原来把一段线**锚在包围盒左上角、绕左中点旋转**。旋转原点只有在
    "起点恰好是包围盒左上角"时才与真实起点重合；而 `_py` 是递减的，所以
    **只要线段在屏幕上朝右上走，起点就落在包围盒下角** —— 整段被平移一个段长
    （实测 100mm 的段在 360px 台面上偏 99px）。
    网格蛇形路径里约一半的段、圆的 48 段里近一半都是这个方向 → 看起来就是
    "方向对、位置全错"的散乱斜线；圆也因此完全不像圆。
    **正确做法**：锚在**起点** `(_px(x1), _py(y1))`，让旋转原点（局部
    `(0, lineWidth/2)`）与起点重合。`_ang` 本来就是"起点→终点"的屏幕夹角，
    不需要改公式。

    ============================================================
    立体感从哪来（复用 CardSurface 「panel」那套配方）
    ============================================================
    本卡的 `StagePlate` 局部组件照 `pages/components/CardSurface.qml` 的 panel 变体
    画：上亮下暗的极轻渐变 + 顶边 1px 内高光 + 底边 1px 内压深 + 面下方露出的软投影。
    颜色一律由 `Colors` 令牌 `Qt.lighter/darker` **推导**，不新增色值 ——
    换主题/换配色时它跟着一起过渡（`test_design_tokens.py` 也不会被牵动）。
    ⚠ 不直接用 `CardSurface{variant:"panel"}`：它 panel 的面在底部内收 `_panelLift`，
      内容锚点会被那 2~4px 影响，而这里的内容（折线/游标）必须与台面边框严格重合。

    ============================================================
    为什么整张卡不用 Canvas
    ============================================================
    标尺刻度与折线都用普通 `Rectangle`/`Text`（折线是逐段旋转的细长方形）。
      1. **换主题会"纹丝不动"**：Canvas 是命令式重绘，`Colors` 换配色时没有任何
         信号能通知它重画；`Rectangle` 是声明式的，颜色自动跟着主题变。
      2. **测得到**：普通项在可视树里，页测试能数出"刻度 5 条""折线 8 段"、
         能量某一段的旋转原点对不对；Canvas 画了什么测试一点都看不见。
      （`Shape`/`PathPolyline` 也是声明式的、本可胜任，但这个项目从未用过它，
        不为一条折线引入新模块。）

    ============================================================
    坐标换算只有一处
    ============================================================
    `_px()/_py()`（台面 mm → 场 px）与 `_fxOf()/_fyOf()`（场 px → 台面比例）是本文件
    **唯一**的坐标变换，标尺、折线、游标、鼠标都调它们。
    ⚠ `_py` 里有 **Y 翻转**：台面 +Y = 向里，而屏幕 Y 朝下 → 图上 +Y 朝上
      （与 `PositionReadout.qml` 的缩略图一致）。
    ⚠ 点选的 mm 值也由本文件算（`_mmOf`），信号 `mapPicked(xmm, ymm)` 直接把
      **台面 mm** 交给页面 → `StageBridge.moveTo()`。不再跨层做"比例→mm"换算。
*/

Item {
    id: root
    objectName: "trajCard"

    // ============================================================
    // 公有 API（全部由 StagePage 从 StageBridge / ZStageBridge 灌进来）
    // ============================================================
    property bool connected: false
    property bool canRun: false             // StageBridge.pathCanRun
    property bool moving: false
    property string hint: ""                // StageBridge.pathHint（说清为什么不能跑）
    property real posX: 0
    property real posY: 0
    property real targetX: NaN
    property real targetY: NaN
    property real wsXMin: 0
    property real wsYMin: 0
    property real wsXMax: 360
    property real wsYMax: 360

    // ── Z 轴高度条（2026-10-10 新增；2026-10-10 当天又按用户要求改成**可点**）──
    // 原来只读，用户实测后说"Z 轴好像没法直接控制" → 现在点/拖条上任意高度
    // 就让 Z 走到那里（`zPicked` → 页面 `ZStageBridge.moveTo()`）。
    // ⚠ 闸门**直接用桥的 `canMove`**（已连接 + 已立基准 + 设过软限位 + 当前不动），
    //   不要在 QML 里自己拼一遍条件 —— 那就是"同一事实两份"（本项目第 4 条坑）。
    // ⚠ 点不动的原因用桥的 `datumHint` 提前说出来（本项目铁律：不许让用户对着灰控件猜）。
    property bool zConnected: false
    property real posZ: 0
    property real zLo: 0
    property real zHi: 250
    property bool zCanMove: false
    property string zHint: ""
    // Z 条拖拽中的归一化高度：< 0 = 没在拖
    property real zDragFrac: -1

    property var paths: []                  // [{name, kind, params, rpm, acc}]
    property var previewPoints: []          // 选中轨迹的落点 [[x,y],...]
    property int selectedPath: -1
    property string pathState: "idle"       // idle/running/paused/done/aborted
    property int pathDone: 0
    property int pathTotal: 0
    property string abortReason: ""
    property real estimateS: 0

    property bool recording: false
    property int recordCount: 0

    // 默认轨迹速度：**加减速默认比手动点动大得多**（手动是 1）。
    // 理由：位置模式每一段都是一条完整梯形曲线，5mm 的小段用 acc=1 的话
    // 全程都在爬坡/刹停，一段要好几秒 —— 轨迹会慢到没法用。
    property int pathRpm: 1200
    property int pathAcc: 30

    // ── 信号（页面负责转成 StageBridge 的调用）──
    // ⚠ 交出去的是**台面 mm**（不是比例）：换算只在本文件的 `_mmOf` 里做一次。
    signal mapPicked(real xmm, real ymm)
    // Z 条被点/拖到了某个高度（mm）
    signal zPicked(real zmm)
    signal saveRequested(string name, string kind, string paramsJson, int rpm, int acc)
    signal selectRequested(int index)
    signal deleteRequested(int index)
    signal runRequested(int index)
    signal pauseRequested()
    signal resumeRequested()
    signal stopRequested()
    signal recordStartRequested()
    signal recordStopRequested(string name)
    signal recordCancelRequested()

    implicitHeight: mainCol.implicitHeight + 32

    // ============================================================
    // 内部状态
    // ============================================================
    // 当前模式："select" | "grid" | "circle" | "rect" | "polyline"
    property string mode: "select"
    // 多段线的顶点（台面 mm）
    property var draft: []
    // 圆形/矩形拖拽的起止点（台面 mm）
    property var dragA: null
    property var dragB: null
    property bool dragging: false
    // 矩形定形后的两个角（与 dragA/dragB 分开：拖完要能继续用输入框/重画改）
    property var rectA: null
    property var rectB: null
    // 参数
    property real pStep: 5.0
    property int pRows: 6
    property int pCols: 6
    property bool pSerpentine: true
    property bool pSmooth: false
    property real pCx: 180
    property real pCy: 180
    property real pR: 50
    property string saveName: ""

    // ── 轨迹折线（选中轨迹 → 逐段）──
    // ⚠ 必须**命令式**地算好存进属性，不能在绑定里写 `_buildSegs(previewPoints)`
    //   —— QML 的绑定不追踪函数体里的属性读取（AGENTS 第 6 条），那样只求值一次。
    property var segs: []
    // ⚠ 画多段的**硬上限**。段长 0.2mm 的大圆能算出上万段，逐段建 Item 会把界面
    //   卡住并吃掉大量内存（2026-10-10 出过一次内存事故，见 `_ticksFor` 的注释）。
    //   执行本身**不受影响**：序列器走的是桥算出来的完整点列，这里只是"别全画出来"。
    readonly property int _drawCap: 1500
    property bool drawTruncated: false

    function _rebuildSegs() {
        var pts = root.previewPoints
        var out = []
        var cut = false
        if (pts && pts.length > 1) {
            for (var i = 0; i + 1 < pts.length; i++) {
                if (out.length >= root._drawCap) { cut = true; break }
                out.push([pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]])
            }
        }
        root.drawTruncated = cut
        root.segs = out
    }
    onPreviewPointsChanged: _rebuildSegs()

    // ── 正在画的形状（草稿）──
    // ⚠ 一律**从参数推导**（pCx/pCy/pR、rectA/rectB、draft），不从"上一次拖拽的
    //   起止点"推导。以前从拖拽点推导，于是"在输入框里改了半径，图上还是旧的"
    //   （用户 2026-10-10 报的）。现在拖拽只是**连续写参数**的一种方式。
    property var draftSegs: []
    function _touchDraft() {
        var out = []
        var m = root.mode
        if (m === "polyline") {
            for (var i = 0; i + 1 < root.draft.length; i++) {
                if (out.length >= root._drawCap) break
                out.push([root.draft[i][0], root.draft[i][1],
                          root.draft[i + 1][0], root.draft[i + 1][1]])
            }
        } else if (m === "circle" && root.pR > 0.01) {
            // 段数与 stage_traj.sample_circle 同规：max(8, ceil(周长/段长))。
            // 这样"图上看到的多边形感"就是执行时真实的多边形感（含最少 8 段的下限）。
            var n = Math.max(8, Math.ceil(2 * Math.PI * root.pR / Math.max(0.2, root.pStep)))
            if (n > root._drawCap) n = root._drawCap      // ⚠ 见 _drawCap 的注释
            for (var k = 0; k < n; k++) {
                var a1 = 2 * Math.PI * k / n, a2 = 2 * Math.PI * (k + 1) / n
                out.push([root.pCx + root.pR * Math.cos(a1), root.pCy + root.pR * Math.sin(a1),
                          root.pCx + root.pR * Math.cos(a2), root.pCy + root.pR * Math.sin(a2)])
            }
        } else if (m === "rect" && root.rectA && root.rectB) {
            var x0 = Math.min(root.rectA[0], root.rectB[0]), x1 = Math.max(root.rectA[0], root.rectB[0])
            var y0 = Math.min(root.rectA[1], root.rectB[1]), y1 = Math.max(root.rectA[1], root.rectB[1])
            if (x1 - x0 > 0.05 && y1 - y0 > 0.05) {
                out = [[x0, y0, x1, y0], [x1, y0, x1, y1], [x1, y1, x0, y1], [x0, y1, x0, y0]]
            }
        }
        root.draftSegs = out
    }
    onModeChanged: _touchDraft()

    // ============================================================
    // 坐标换算（本文件唯一的变换：标尺/折线/游标/鼠标 共用）
    // ============================================================
    readonly property real _wsW: Math.max(1e-6, wsXMax - wsXMin)
    readonly property real _wsH: Math.max(1e-6, wsYMax - wsYMin)
    // 台面（工作区那块"板"）在卡里占的最长边：宽了给比例、窄了让它更大
    readonly property real _plateTarget: Math.max(140, Math.min(360,
        width * (width >= 620 ? 0.50 : 0.72)))
    // 等比缩放：W/H 用同一个 _fit → 圆在工作区是圆时画出来也是圆
    readonly property real _fit: _plateTarget / Math.max(1e-6, Math.max(_wsW, _wsH))
    readonly property real _plateW: Math.max(40, _wsW * _fit)
    readonly property real _plateH: Math.max(40, _wsH * _fit)
    // 刻度槽（标尺画在台面**外侧**）：左留 Y 标签、下留 X 标签、下再留 3px 给投影
    readonly property real _gutL: 32
    readonly property real _gutT: 8
    readonly property real _gutR: 18
    readonly property real _gutB: 22
    readonly property real _shadowPad: 3
    readonly property real _boxW: _plateW + _gutL + _gutR
    readonly property real _boxH: _plateH + _gutT + _gutB + _shadowPad

    function _px(xmm) { return _plateW * (xmm - wsXMin) / _wsW }
    function _py(ymm) { return _plateH * (1 - (ymm - wsYMin) / _wsH) }   // ⚠ Y 翻转
    function _fxOf(px) { return px / Math.max(1e-6, _plateW) }
    function _fyOf(py) { return 1 - py / Math.max(1e-6, _plateH) }        // ⚠ Y 翻转
    function _mmOf(fx, fy) {
        return [wsXMin + Math.max(0, Math.min(1, fx)) * _wsW,
                wsYMin + Math.max(0, Math.min(1, fy)) * _wsH]
    }

    // ============================================================
    // 标尺刻度（1/2/5×10ⁿ 的"整步长"，首末必定落在工作区边界上）
    // ============================================================
    property var xTicks: []
    property var yTicks: []

    function _niceStep(range, targetCount) {
        var raw = Math.max(1e-6, range) / Math.max(1, targetCount)
        var mag = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10))
        var n = raw / mag
        var mult = (n <= 1) ? 1 : (n <= 2) ? 2 : (n <= 5) ? 5 : 10
        return mult * mag
    }
    function _stepFor(range) {
        return _niceStep(range, 5)
    }
    function _fmt(v) {
        return Math.abs(v - Math.round(v)) < 1e-6 ? String(Math.round(v)) : v.toFixed(1)
    }
    function _ticksFor(lo, hi) {
        var out = []
        var range = hi - lo
        if (!(range > 1e-9)) return out
        var step = _niceStep(range, 5)
        // ⚠ 步长退化（NaN / 0）直接不画：绝不能让 0 步长进下面的循环。
        if (!(step > 0)) return out
        // 主刻度：lo …（不足一步时停在 hi 前，最后**必定**补 hi）
        out.push({ v: lo, label: _fmt(lo), major: true })
        var k = 1
        // ⚠ `k < 512` 只是护栏（正常范围最多 6 条主刻度）：没有它，只要 step
        //   退化成 0，`lo + k*0 < hi` 就永远为真 → 无限循环 + 无限 push。
        while (lo + k * step < hi - step * 0.25 && k < 512) {
            out.push({ v: lo + k * step, label: _fmt(lo + k * step), major: true })
            k++
        }
        if (hi - lo > step * 0.25) {
            out.push({ v: hi, label: _fmt(hi), major: true })
        }
        // 细刻度：主刻度之间每 1/5 步（屏幕间距不足 5px 就整批不画，免得糊成一片）
        if ((_plateW / Math.max(1e-6, range)) * (step / 5) >= 5) {
            // ⚠⚠ 必须**先把主刻度条数快照下来**。写成
            //   `for (i = 0; i + 1 < out.length; i++)` 并在循环体里 `out.push`，
            //   `out.length` 每轮涨 4 而 `i` 只涨 1 → **条件永远为真** →
            //   边遍历边 push，直到把内存吃光。
            //   2026-10-10 我就是在标尺这里这么写的：15G 内存的机器被占满、
            //   渲染进程被 OOM 杀掉（exit 124）。**别再写回那种形式。**
            var majors = out.length
            var minor = []
            for (var i = 0; i + 1 < majors; i++) {
                var a = out[i].v, b = out[i + 1].v
                for (var j = 1; j < 5; j++) {
                    minor.push({ v: a + (b - a) * j / 5, label: "", major: false })
                }
            }
            out = out.concat(minor)
            out.sort(function (p, q) { return p.v - q.v })
        }
        return out
    }
    function _rebuildTicks() {
        root.xTicks = _ticksFor(root.wsXMin, root.wsXMax)
        root.yTicks = _ticksFor(root.wsYMin, root.wsYMax)
    }
    onWsXMinChanged: _rebuildTicks()
    onWsYMinChanged: _rebuildTicks()
    onWsXMaxChanged: _rebuildTicks()
    onWsYMaxChanged: _rebuildTicks()
    // 细刻度要不要画，取决于"每毫米几个像素" → 卡变宽/变窄就得重算。
    // ⚠ 不能写 `on_plateWChanged`：**下划线开头的属性不会生成这种 handler**
    //   （QML 报 `Cannot assign to non-existent property`，而且整个类型都用不了）。
    //   监听 `width` 即可 —— `_plateW` 就是由它推出来的。
    onWidthChanged: _rebuildTicks()
    Component.onCompleted: {
        _rebuildTicks()
        _rebuildSegs()
        _touchDraft()
    }

    // ============================================================
    // 立体台面（照 CardSurface 的 panel 配方；颜色全由令牌推导）
    // ============================================================
    component StagePlate: Item {
        id: plate
        property color baseColor: Colors.panelBg
        property color edgeColor: Colors.panelBorder
        property real radius: 10

        // 投影：与面同大、下移 3px → 只在底边露出一点（面声明在后面，盖住其余部分）
        Rectangle {
            x: 1
            y: 3
            width: parent.width - 2
            height: parent.height
            radius: plate.radius
            color: Colors.panelShadow
        }
        // 面：上亮下暗极轻渐变 + 顶边高光 + 底边压深
        Rectangle {
            anchors.fill: parent
            radius: plate.radius
            gradient: Gradient {
                GradientStop { position: 0.0; color: Qt.lighter(plate.baseColor, 1.03) }
                GradientStop { position: 1.0; color: Qt.darker(plate.baseColor, 1.04) }
            }
            border { width: 1; color: plate.edgeColor }

            Rectangle {
                anchors {
                    top: parent.top; left: parent.left; right: parent.right; topMargin: 1
                    leftMargin: Math.max(2, plate.radius - 4)
                    rightMargin: Math.max(2, plate.radius - 4)
                }
                height: 1
                color: Qt.lighter(plate.baseColor, 1.12)
            }
            Rectangle {
                anchors {
                    bottom: parent.bottom; left: parent.left; right: parent.right; bottomMargin: 1
                    leftMargin: Math.max(2, plate.radius - 4)
                    rightMargin: Math.max(2, plate.radius - 4)
                }
                height: 1
                color: Qt.darker(plate.baseColor, 1.18)
            }
        }
    }

    // 轨迹/草稿折线的每一段 → 一个旋转的细长方形
    component SegLine: Item {
        id: seg
        // ⚠ 必须声明 `required property var modelData`：Qt 6 里 Repeater 的 delegate
        //   只有声明了它才拿得到模型数据（否则 `ReferenceError: modelData is not defined`，
        //   而且只在有数据时才炸）。
        required property var modelData
        objectName: "trajSeg"
        readonly property var segData: modelData   // [x1,y1,x2,y2]（台面 mm）
        property color lineColor: Colors.accent
        property real lineWidth: 2

        // ⚠⚠ 锚在**起点**上，让旋转原点（局部 (0, lineWidth/2)）与起点重合。
        //    以前锚在包围盒左上角 + 绕左中点旋转：起点落在包围盒下角的那些段
        //    （屏幕上朝右上/向上）会被平移**一个段长**，看起来就是"一地乱线"。
        x: root._px(seg.segData[0])
        y: root._py(seg.segData[1]) - seg.lineWidth / 2
        width: Math.max(seg.lineWidth, Math.hypot(seg.segData[2] - seg.segData[0],
                                                  seg.segData[3] - seg.segData[1]))
        height: seg.lineWidth

        // 屏幕夹角（y 朝下时正角 = 顺时针）：_px/_py 等比例，故 mm 域的
        // -atan2(Δy,Δx) 就是屏幕夹角，不需要再换算
        readonly property real _ang: -Math.atan2(seg.segData[3] - seg.segData[1],
                                                 seg.segData[2] - seg.segData[0]) * 180 / Math.PI
        transform: Rotation {
            origin.x: 0
            origin.y: seg.lineWidth / 2
            angle: seg._ang
        }
        Rectangle {
            anchors.fill: parent
            radius: parent.lineWidth / 2
            color: seg.lineColor
            opacity: 0.95
        }
    }

    // ============================================================
    // 本体
    // ============================================================
    CardSurface {
        anchors.fill: parent

        ColumnLayout {
            id: mainCol
            spacing: 10
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 14 }

            // ── 表头 ──────────────────────────────────────
            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                IconImage {
                    Layout.alignment: Qt.AlignVCenter
                    source: "../../images/靶心.svg"
                    // ⚠ 进 Layout 的项必须写 Layout.preferred*，写 width 会被覆盖成 24
                    implicitWidth: 16
                    implicitHeight: 16
                    Layout.preferredWidth: 16
                    Layout.preferredHeight: 16
                }
                Text {
                    text: qsTr("轨迹与网格")
                    font.pixelSize: 14
                    font.bold: true
                    color: Colors.textPrimary
                }
                Item { Layout.fillWidth: true }
                Text {
                    visible: root.recording
                    text: qsTr("● 正在记录移动（%1 点）").arg(root.recordCount)
                    font.pixelSize: 11
                    color: Colors.statusDisconnected
                }
            }

            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder }

            // ══════════════════════════════════════════════════
            // 台面 + Z 高度条（宽了并排、窄了上下叠）
            // ⚠ 整体**居中**（用户 2026-10-10 要求"两个平面要居中放置"）：
            //   关键是这里 `Layout.fillWidth: false` —— 让这个 GridLayout 的宽
            //   等于"台面+间距+Z 条"的**内容宽**，再由 `Layout.alignment:
            //   Qt.AlignHCenter` 把这一整组在卡里居中。
            //   若写 `fillWidth: true`，格子会撑满卡片、台面被推到最左边。
            // ══════════════════════════════════════════════════
            GridLayout {
                Layout.fillWidth: false
                Layout.alignment: Qt.AlignHCenter
                columns: root.width >= 620 ? 2 : 1
                columnSpacing: 16
                rowSpacing: 8

                // ══════════ XY 台面（含左/下标尺）══════════
                Item {
                    id: mapBox
                    objectName: "trajMap"
                    Layout.preferredWidth: root._boxW
                    Layout.preferredHeight: root._boxH
                    Layout.alignment: Qt.AlignTop

                    StagePlate {
                        id: plate
                        objectName: "trajPlate"
                        x: root._gutL
                        y: root._gutT
                        width: root._plateW
                        height: root._plateH
                        radius: 10
                    }

                    // ── 场：所有"台面坐标"的东西都挂在这儿（尺寸与台面严格重合）──
                    Item {
                        id: fieldBox
                        objectName: "trajField"
                        x: root._gutL
                        y: root._gutT
                        width: root._plateW
                        height: root._plateH

                        // 原点标记（零点写死在 X 左 + Y 外 = 图上左下角，
                        // 与 stage_bridge.DATUM_CORNER 一致）
                        Rectangle {
                            x: -3
                            y: parent.height - 3
                            width: 8; height: 8; radius: 2
                            color: Colors.statusConnected
                            opacity: 0.9
                        }

                        // 选中轨迹的落点折线
                        Repeater {
                            objectName: "trajPreview"
                            model: root.segs
                            delegate: SegLine { lineColor: Colors.accent }
                        }
                        // 正在画的形状（草稿）
                        Repeater {
                            objectName: "trajDraft"
                            model: root.draftSegs
                            delegate: SegLine {
                                lineColor: Colors.statusDisconnected
                                lineWidth: 1
                            }
                        }
                        // 折线的落点小圆点（一眼看出"分了几段、点在哪儿"）
                        // ⚠ 只在段数少时才画：段数一多（圆的 200+ 段、密集点阵），
                        //   4px 的圆点会挨在一起把线变成**虚线环**（用户 2026-10-10
                        //   截图里那个圆就是点点连成的）。密集时线本身就是信息。
                        Repeater {
                            objectName: "trajDots"
                            model: root.draftSegs.length <= 60 ? root.draftSegs : []
                            delegate: Item {
                                required property var modelData
                                x: root._px(modelData[0]) - 2
                                y: root._py(modelData[1]) - 2
                                width: 4; height: 4
                                Rectangle {
                                    anchors.fill: parent
                                    radius: 2
                                    color: Colors.statusDisconnected
                                }
                            }
                        }
                        // 多段线还没连成线的顶点（大一点，好认）
                        Repeater {
                            objectName: "trajVertices"
                            model: (root.mode === "polyline") ? root.draft : []
                            delegate: Item {
                                required property var modelData
                                x: root._px(modelData[0]) - 3
                                y: root._py(modelData[1]) - 3
                                width: 6; height: 6
                                Rectangle {
                                    anchors.fill: parent
                                    radius: 3
                                    color: Colors.statusDisconnected
                                }
                            }
                        }
                        // 矩形定形后的两个角
                        Repeater {
                            model: (root.mode === "rect" && root.rectA && root.rectB)
                                   ? [root.rectA, root.rectB] : []
                            delegate: Item {
                                required property var modelData
                                x: root._px(modelData[0]) - 3
                                y: root._py(modelData[1]) - 3
                                width: 6; height: 6
                                Rectangle {
                                    anchors.fill: parent
                                    radius: 1
                                    color: Colors.accent
                                }
                            }
                        }

                        // 目标叉（和 PositionReadout 同一套画法）
                        Item {
                            visible: !isNaN(root.targetX) && !isNaN(root.targetY)
                            x: root._px(root.targetX) - 7
                            y: root._py(root.targetY) - 7
                            width: 14; height: 14
                            Rectangle { anchors.centerIn: parent; width: 14; height: 2
                                        rotation: 45; color: Colors.statusDisconnected }
                            Rectangle { anchors.centerIn: parent; width: 14; height: 2
                                        rotation: -45; color: Colors.statusDisconnected }
                        }

                        // 当前位置（实心点 + 光晕）
                        Item {
                            objectName: "trajCursor"
                            x: root._px(root.posX) - 11
                            y: root._py(root.posY) - 11
                            width: 22; height: 22
                            Rectangle {
                                anchors.centerIn: parent
                                width: 22; height: 22; radius: 11
                                color: Colors.interactivePressed
                                opacity: 0.35
                            }
                            Rectangle {
                                anchors.centerIn: parent
                                width: 10; height: 10; radius: 5
                                color: Colors.textPrimary
                            }
                            Behavior on x { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
                            Behavior on y { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
                        }

                        // ⚠ 一个 MouseArea 管三种手势，靠 `mode` 分派：
                        //   点选 → 单击就走（**只有这个模式会移动**）；
                        //   圆/矩形 → 按下拖、拖动过程中**实时写参数**；
                        //   多段线 → 每次单击加一个顶点。
                        MouseArea {
                            id: mapMa
                            objectName: "trajMapArea"
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: root.mode === "select" ? Qt.PointingHandCursor : Qt.CrossCursor
                            enabled: root.connected && !root.moving
                            // ⚠⚠ 必须：不然在台面上按住拖动时，外层页面 Flickable 会把
                            //    拖动**抢走** —— 表现是"拖一下页面就上下滚"，而且
                            //    `onPositionChanged` 不再来、圆的半径冻在半路
                            //    （用户 2026-10-10 报的"圆卡住拖不大"就是这个）。
                            //    代价：在台面上拖不再滚页面（要滚就在台面外拖）。
                            preventStealing: true

                            function _mm(m) {
                                return root._mmOf(root._fxOf(m.x), root._fyOf(m.y))
                            }

                            onPressed: function (m) {
                                if (root.mode === "circle" || root.mode === "rect") {
                                    var p = _mm(m)
                                    root.dragA = p
                                    root.dragB = p
                                    root.dragging = true
                                    root._applyDrag()
                                }
                            }
                            onPositionChanged: function (m) {
                                if (!root.dragging) return
                                root.dragB = _mm(m)
                                root._applyDrag()
                            }
                            onReleased: function (m) {
                                if (!root.dragging) return
                                root.dragB = _mm(m)
                                root._applyDrag()
                                root.dragging = false
                                root._syncInputs()
                            }
                            onCanceled: {
                                root.dragging = false
                                root._syncInputs()
                            }
                            onClicked: function (m) {
                                if (root.mode === "select") {
                                    var p = _mm(m)
                                    root.mapPicked(p[0], p[1])
                                } else if (root.mode === "polyline") {
                                    root.draft = root.draft.concat([_mm(m)])
                                    root._touchDraft()
                                    root._syncInputs()
                                }
                            }

                            // 点动/移动中不许改图，鼠标形状也说明一下
                            // ⚠ MouseArea 没有 `hovered`（那是 Control 的属性），
                            //   它只有 `containsMouse` —— 写 hovered 会 ReferenceError。
                            ToolTip.visible: mapMa.containsMouse && !mapMa.enabled
                            ToolTip.delay: 700
                            ToolTip.text: root.moving ? qsTr("台面正在动") : qsTr("先连接平台")
                        }
                    }

                    // ══════════ X 标尺（台面下边缘）══════════
                    Repeater {
                        objectName: "trajXRuler"
                        model: root.xTicks
                        delegate: Item {
                            required property var modelData
                            // 每个刻度项宽 30：内容水平居中在刻度线上，好读
                            x: plate.x + root._px(modelData.v) - 15
                            y: plate.y + plate.height
                            width: 30
                            height: 18
                            Rectangle {
                                anchors.horizontalCenter: parent.horizontalCenter
                                y: 0
                                width: 1
                                height: modelData.major ? 5 : 3
                                color: modelData.major ? Colors.cardBorderStrong : Colors.cardBorder
                            }
                            Text {
                                visible: modelData.major
                                anchors.horizontalCenter: parent.horizontalCenter
                                y: 6
                                text: modelData.label
                                font.pixelSize: 9
                                color: Colors.textPlaceholder
                            }
                        }
                    }

                    // ══════════ Y 标尺（台面左边缘）══════════
                    Repeater {
                        objectName: "trajYRuler"
                        model: root.yTicks
                        delegate: Item {
                            required property var modelData
                            x: plate.x - 30
                            y: plate.y + root._py(modelData.v) - 7
                            width: 30
                            height: 14
                            Rectangle {
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                width: modelData.major ? 5 : 3
                                height: 1
                                color: modelData.major ? Colors.cardBorderStrong : Colors.cardBorder
                            }
                            Text {
                                visible: modelData.major
                                anchors.right: parent.right
                                anchors.rightMargin: 7
                                anchors.verticalCenter: parent.verticalCenter
                                text: modelData.label
                                font.pixelSize: 9
                                color: Colors.textPlaceholder
                            }
                        }
                    }
                }

                // ══════════ Z 高度条（可点/可拖：走到那个高度）══════════
                Item {
                    id: zPane
                    objectName: "trajZPane"
                    Layout.preferredWidth: 84
                    Layout.preferredHeight: root._boxH
                    Layout.alignment: Qt.AlignTop | Qt.AlignHCenter

                    readonly property real _stripH: Math.max(40, root._boxH - 20)
                    readonly property real _trackTop: 8
                    readonly property real _trackH: Math.max(1, zPlate.height - 16)
                    // 归一化：0 = zLo（底）、1 = zHi（顶）
                    readonly property real _frac: Math.max(0, Math.min(1,
                        (root.posZ - root.zLo) / Math.max(1e-6, root.zHi - root.zLo)))
                    // 拖拽中显示的目标（没在拖就返回 -1）
                    readonly property real _dragMm: root.zDragFrac >= 0
                        ? (root.zLo + Math.max(0, Math.min(1, root.zDragFrac)) * (root.zHi - root.zLo))
                        : -1
                    function _yOfFrac(f) { return _trackTop + (1 - f) * _trackH }
                    function _mmAtY(y) {
                        var f = 1 - (y - _trackTop) / _trackH
                        f = Math.max(0, Math.min(1, f))
                        return root.zLo + f * (root.zHi - root.zLo)
                    }
                    function _fracAtY(y) {
                        return Math.max(0, Math.min(1, 1 - (y - _trackTop) / _trackH))
                    }

                    StagePlate {
                        id: zPlate
                        objectName: "trajZPlate"
                        anchors { left: parent.left; right: parent.right; top: parent.top }
                        height: zPane._stripH
                        radius: 8
                    }

                    // 轨道（一根竖线）+ 高度填充 + 平台标记
                    Rectangle {
                        x: zPlate.width / 2 - 1
                        y: zPane._trackTop
                        width: 2
                        height: zPane._trackH
                        color: Colors.panelBorder
                    }
                    Rectangle {
                        x: zPlate.width / 2 - 8
                        width: 16
                        radius: 3
                        color: Colors.interactivePressed
                        opacity: root.zConnected ? 0.9 : 0.4
                        // 从底往上长
                        y: zPlate.height - 8 - height
                        height: Math.max(0, zPane._frac * zPane._trackH)
                    }
                    // 平台（一条横板，位置 = 当前高度）
                    Rectangle {
                        objectName: "trajZMarker"
                        x: 10
                        width: zPane.width - 20
                        height: 5
                        radius: 2.5
                        color: root.zConnected ? Colors.accent : Colors.cardBorder
                        y: zPane._yOfFrac(zPane._frac) - height / 2
                        Behavior on y { NumberAnimation { duration: 150; easing.type: Easing.OutCubic } }
                    }
                    // 拖拽预览：一条虚线位置的目标线（松手才真的下发，绝不连发 move）
                    Rectangle {
                        objectName: "trajZPreview"
                        visible: root.zDragFrac >= 0
                        x: 6
                        width: zPane.width - 12
                        height: 2
                        radius: 1
                        color: Colors.statusDisconnected
                        y: zPane._yOfFrac(root.zDragFrac) - 1
                    }
                    // 上下两个边界标签（无标签的细刻度就省了：条子太窄）
                    Text {
                        x: 4
                        y: 2
                        text: root._fmt(root.zHi)
                        font.pixelSize: 9
                        color: Colors.textPlaceholder
                    }
                    Text {
                        x: 4
                        y: zPlate.height - 14
                        text: root._fmt(root.zLo)
                        font.pixelSize: 9
                        color: Colors.textPlaceholder
                    }

                    Text {
                        objectName: "trajZValue"
                        anchors { horizontalCenter: parent.horizontalCenter; bottom: parent.bottom }
                        text: zPane._dragMm >= 0
                              ? ("→ Z " + zPane._dragMm.toFixed(1))
                              : (root.zConnected ? ("Z " + root.posZ.toFixed(1)) : qsTr("Z --"))
                        font.pixelSize: 11
                        color: zPane._dragMm >= 0 ? Colors.statusDisconnected
                             : (root.zConnected ? Colors.textPrimary : Colors.textPlaceholder)
                    }

                    // ⚠ 点/拖 = **松手才下发一条** `moveTo`。
                    //   拖拽过程中只画预览线，绝不连续下发 —— 固件在途时再收 `move`
                    //   会**静默覆盖目标**（docs/17），连发就是拿命令流去打固件的队列。
                    MouseArea {
                        objectName: "trajZArea"
                        anchors.fill: parent
                        hoverEnabled: true
                        enabled: root.zCanMove
                        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                        // 同台面：不加这个，在 Z 条上按住拖会把整页滚起来
                        preventStealing: true

                        onPressed: function (m) { root.zDragFrac = zPane._fracAtY(m.y) }
                        onPositionChanged: function (m) {
                            if (pressed) root.zDragFrac = zPane._fracAtY(m.y)
                        }
                        onReleased: function (m) {
                            if (root.zDragFrac < 0) return
                            var mm = zPane._mmAtY(m.y)
                            root.zDragFrac = -1
                            root.zPicked(mm)
                        }
                        onCanceled: root.zDragFrac = -1

                        ToolTip.visible: containsMouse
                        ToolTip.delay: 600
                        ToolTip.text: root.zCanMove
                            ? qsTr("点或拖到某个高度 → Z 轴走到那里（松手才下发）")
                            : (root.zHint.length > 0 ? root.zHint
                               : qsTr("Z 轴现在动不了：需要先连接、立基准、设软限位"))
                    }
                }
            }

            // ══════════════════════════════════════════════════
            // 模式 chips（一行 5 个；窄了就折行）+ 当前模式说明
            // ══════════════════════════════════════════════════
            GridLayout {
                Layout.fillWidth: true
                columns: root.width >= 560 ? 5 : 3
                columnSpacing: 8
                rowSpacing: 6
                Repeater {
                    objectName: "trajModeChips"
                    model: [
                        { key: "select",   label: qsTr("点选"),   icon: "../../images/点选.svg" },
                        { key: "grid",     label: qsTr("网格"),   icon: "../../images/网格.svg" },
                        { key: "circle",   label: qsTr("圆"),     icon: "../../images/圆.svg" },
                        { key: "rect",     label: qsTr("矩形"),   icon: "../../images/矩形.svg" },
                        { key: "polyline", label: qsTr("多段线"), icon: "../../images/多段线.svg" }
                    ]
                    delegate: ThemedButton {
                        required property var modelData
                        objectName: "modeChip_" + modelData.key
                        Layout.fillWidth: true
                        implicitHeight: 34
                        hPadding: 10
                        iconSize: 16
                        fontPixelSize: 12
                        // ⚠ checkable: false —— 选中态由 `checked` 绑定驱动，
                        //   交给 Qt 自己翻转会打断绑定（ChoiceChip 文件头记过这条）
                        checkable: false
                        checked: root.mode === modelData.key
                        text: modelData.label
                        iconSource: modelData.icon
                        onClicked: {
                            root.mode = modelData.key
                            root._resetDraft()
                        }
                    }
                }
            }

            Text {
                Layout.fillWidth: true
                text: root._modeHint()
                font.pixelSize: 11
                color: Colors.textSecondary
                wrapMode: Text.Wrap
            }

            // 段数超上限时明确说出来 —— 否则用户会以为"执行时会少走一大截"。
            Text {
                objectName: "trajTruncHint"
                Layout.fillWidth: true
                visible: root.drawTruncated
                text: qsTr("线段太多，图上只画前 %1 段（执行时仍然走完整轨迹）").arg(root._drawCap)
                font.pixelSize: 10
                color: Colors.textPlaceholder
                wrapMode: Text.Wrap
            }

            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder }

            // ══════════════════════════════════════════════════
            // 当前模式的参数（整行宽，不再挤在窄列里）
            // ══════════════════════════════════════════════════
            InputRow {
                objectName: "stepInput"
                Layout.fillWidth: true
                visible: root.mode !== "select"
                label: qsTr("段长 mm")
                text: root.pStep.toFixed(1)
                onTextChanged: {
                    var v = parseFloat(text)
                    if (!isNaN(v) && v >= 0.2 && v <= 50) {
                        root.pStep = v
                        root._touchDraft()
                    }
                }
            }

            // 网格
            InputRow {
                objectName: "gridRowsInput"
                Layout.fillWidth: true
                visible: root.mode === "grid"
                label: qsTr("行")
                text: String(root.pRows)
                onTextChanged: {
                    var v = parseInt(text)
                    if (!isNaN(v) && v >= 1 && v <= 60) root.pRows = v
                }
            }
            InputRow {
                objectName: "gridColsInput"
                Layout.fillWidth: true
                visible: root.mode === "grid"
                label: qsTr("列")
                text: String(root.pCols)
                onTextChanged: {
                    var v = parseInt(text)
                    if (!isNaN(v) && v >= 1 && v <= 60) root.pCols = v
                }
            }
            SwitchRow {
                objectName: "serpentineSwitch"
                Layout.fillWidth: true
                visible: root.mode === "grid"
                label: qsTr("蛇形走位（省空行程）")
                on: root.pSerpentine
                onToggled: root.pSerpentine = on
            }
            Text {
                objectName: "gridCountHint"
                Layout.fillWidth: true
                visible: root.mode === "grid"
                text: qsTr("共 %1 个巡检点（每格中心一个）").arg(root.pRows * root.pCols)
                font.pixelSize: 11
                color: Colors.textPlaceholder
            }

            // 圆
            // ⚠ 这三个框**必须声明 `id`**（我第一版只写了 objectName，于是 `_syncInputs`
            //   里访问 `circleRInput` 直接抛 `ReferenceError`，调用方剩下的语句被吞掉
            //   —— 表现成"点重画没反应"）。⚠ objectName 不等于 id，两者都要有。
            // ⚠ 也**不要**给它们写 `text:` 绑定：下面 onTextChanged 会把夹取后的值
            //   回写到 text，而 text 又被 binding 盯着 → QML 报
            //   `Binding loop detected for property "text"`。改成**纯命令式驱动**
            //   （初值与每次改动都由 `_syncInputs()` 写入）。
            InputRow {
                id: circleRInput
                objectName: "circleRInput"
                Layout.fillWidth: true
                visible: root.mode === "circle"
                label: qsTr("半径 mm")
                onTextChanged: {
                    var v = parseFloat(text)
                    if (!isNaN(v) && v > 0) {
                        root.pR = v
                        // ⚠ 手输的半径同样要夹到"圆心到四边的最小距离"：不然图上
                        //   画到台面外，而执行时点会被桥夹回去 —— 预览就撒谎了
                        root._clampCircle()
                        root._touchDraft()
                    }
                }
            }
            InputRow {
                id: circleCxInput
                objectName: "circleCxInput"
                Layout.fillWidth: true
                visible: root.mode === "circle"
                label: qsTr("圆心 X")
                onTextChanged: {
                    var v = parseFloat(text)
                    if (!isNaN(v)) {
                        root.pCx = v
                        root._clampCircle()
                        root._touchDraft()
                    }
                }
            }
            InputRow {
                id: circleCyInput
                objectName: "circleCyInput"
                Layout.fillWidth: true
                visible: root.mode === "circle"
                label: qsTr("圆心 Y")
                onTextChanged: {
                    var v = parseFloat(text)
                    if (!isNaN(v)) {
                        root.pCy = v
                        root._clampCircle()
                        root._touchDraft()
                    }
                }
            }

            // 矩形
            Text {
                objectName: "rectHint"
                Layout.fillWidth: true
                visible: root.mode === "rect"
                text: (root.rectA && root.rectB)
                      ? qsTr("区域：(%1, %2) → (%3, %4) mm").arg(root.rectA[0].toFixed(1))
                          .arg(root.rectA[1].toFixed(1)).arg(root.rectB[0].toFixed(1))
                          .arg(root.rectB[1].toFixed(1))
                      : qsTr("在台面上按住拖出两个对角即可。")
                font.pixelSize: 11
                color: Colors.textPlaceholder
                wrapMode: Text.Wrap
            }

            // 多段线
            SwitchRow {
                objectName: "smoothSwitch"
                Layout.fillWidth: true
                visible: root.mode === "polyline"
                label: qsTr("平滑（过点样条）")
                on: root.pSmooth
                onToggled: root.pSmooth = on
            }

            // 名称 + 存为轨迹 / 重画
            // ⚠ 名称框**不要 fillWidth**（用户 2026-10-10："这个文本框要尽可能的小"）：
            //   以前它吃掉整行宽，输入框里空着 400px。现在固定一个够写
            //   "圆 1010-1530" 的窄宽，按钮紧跟着它，行尾用弹簧收边。
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                visible: root.mode !== "select"
                // ⚠ 名称框用**外面包一个普通 Item** 来定宽，不直接给 `InputRow` 写
                //   `Layout.preferredWidth`：`InputRow` 的根是 RowLayout（内部还带着
                //   `Layout.fillWidth` 的输入框），实测直接写它会被隐式宽度顶开
                //   （页面测试量到 524px，写的 190 没生效）。
                //   项目里对嵌套布局的既有做法就是"外面包普通 Item 承接宽度"（§26-5/§29）。
                Item {
                    objectName: "saveNameBox"
                    Layout.preferredWidth: 190
                    Layout.preferredHeight: 34
                    InputRow {
                        objectName: "saveNameInput"
                        anchors.fill: parent
                        label: qsTr("名称")
                        text: root.saveName
                        onTextChanged: root.saveName = text
                    }
                }
                ThemedButton {
                    objectName: "savePathButton"
                    implicitHeight: 34
                    hPadding: 14
                    tone: "primary"
                    enabled: root._canSave()
                    text: qsTr("存为轨迹")
                    fontPixelSize: 12
                    onClicked: root._saveCurrent()
                }
                ThemedButton {
                    objectName: "clearDraftButton"
                    implicitHeight: 34
                    hPadding: 12
                    tone: "neutral"
                    // ⚠ 网格模式没有"画出来的东西"可重画 → 直接不显示。
                    //   让一个按钮点了什么都不发生，比没有这个按钮更糟（用户会以为坏了）。
                    visible: root.mode !== "grid"
                    text: qsTr("重画")
                    fontPixelSize: 12
                    onClicked: root._resetDraft()
                }
                Item { Layout.fillWidth: true }      // 行尾弹簧
            }

            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder }

            // ── 轨迹速度（详细参数）+ 录制（按需，右下角）──
            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                InputRow {
                    objectName: "pathRpmInput"
                    Layout.preferredWidth: 210
                    label: qsTr("轨迹 rpm")
                    text: String(root.pathRpm)
                    onTextChanged: {
                        var v = parseInt(text)
                        if (!isNaN(v) && v >= 1) root.pathRpm = v
                    }
                }
                InputRow {
                    objectName: "pathAccInput"
                    Layout.preferredWidth: 210
                    label: qsTr("轨迹 acc")
                    text: String(root.pathAcc)
                    onTextChanged: {
                        var v = parseInt(text)
                        if (!isNaN(v) && v >= 1) root.pathAcc = v
                    }

                    // 「为什么 acc 默认这么大」属于**说明**，按规矩不进界面正文，放 ToolTip。
                    // ⚠ `InputRow` 的根是普通 Item（不是 Control），**没有 `hovered`**
                    //   —— 用 HoverHandler 取悬停态（它不消耗点击，MouseArea 会）。
                    HoverHandler { id: accHover }
                    ToolTip.visible: accHover.hovered
                    ToolTip.delay: 600
                    ToolTip.text: qsTr("加减速故意比手动点动大得多：位置模式每段都是一条完整的起步→匀速→刹停曲线，5mm 的小段用 acc=1 会全程在爬坡，一段要好几秒。")
                }

                Item { Layout.fillWidth: true }   // 弹簧：录制按钮靠右

                ThemedButton {
                    objectName: "recordButton"
                    implicitHeight: 34
                    hPadding: 14
                    tone: root.recording ? "dangerSoft" : "soft"
                    enabled: root.connected
                    text: root.recording
                          ? qsTr("结束录制（%1 点）").arg(root.recordCount)
                          : qsTr("录制")
                    iconSource: "../../images/电源.svg"
                    iconSize: 13
                    fontPixelSize: 12
                    onClicked: {
                        if (root.recording) root.recordStopRequested(root.saveName)
                        else root.recordStartRequested()
                    }
                    ToolTip.visible: hovered
                    ToolTip.delay: 600
                    ToolTip.text: qsTr("记录**移动**：点选 / 十字点动 / 绝对定位 / 预设前往 都会被记下来，之后可以原样重放。圆、矩形、多段线是用「存为轨迹」画出来的（它们不移动台面），所以录制对它们没有内容可记。")
                }
                ThemedButton {
                    objectName: "recordCancelButton"
                    visible: root.recording
                    implicitHeight: 34
                    hPadding: 12
                    tone: "neutral"
                    text: qsTr("放弃")
                    fontPixelSize: 12
                    onClicked: root.recordCancelRequested()
                }
            }

            // ── 轨迹列表 ──────────────────────────────────
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Text {
                    text: qsTr("已存轨迹")
                    font.pixelSize: 12
                    font.bold: true
                    color: Colors.textPrimary
                }
                Text {
                    text: root.paths.length + " " + qsTr("条")
                    font.pixelSize: 11
                    color: Colors.textPlaceholder
                }
                Item { Layout.fillWidth: true }
                Text {
                    visible: root.paths.length > 10
                    text: qsTr("只列出前 10 条 —— 先删掉不用的")
                    font.pixelSize: 10
                    color: Colors.statusDisconnected
                }
            }

            Text {
                Layout.fillWidth: true
                visible: root.paths.length === 0
                text: qsTr("还没有轨迹。选一个模式在上面画出来，或者点右下角「录制」把手动移动记成一条。")
                font.pixelSize: 11
                color: Colors.textPlaceholder
                wrapMode: Text.Wrap
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 4
                // 列表封顶 10 行：再多就靠删（不做嵌套滚动 —— 页面已经有整页滚动条，
                // 再嵌一层 Flickable 会和外层抢滚轮，本项目在 SliderRow 上踩过这个坑）
                Repeater {
                    objectName: "trajList"
                    model: root.paths.slice(0, 10)
                    delegate: Rectangle {
                        id: prow
                        required property int index
                        required property var modelData
                        Layout.fillWidth: true
                        implicitHeight: 34
                        radius: 6
                        color: (root.selectedPath === prow.index)
                               ? Colors.accentSoft
                               : (rowMa.containsMouse ? Colors.interactiveHover : "transparent")
                        border {
                            width: 1
                            color: (root.selectedPath === prow.index) ? Colors.accent : Colors.cardBorder
                        }

                        MouseArea {
                            id: rowMa
                            anchors.fill: parent
                            hoverEnabled: true
                            acceptedButtons: Qt.NoButton     // 只用于悬停底色，点击交给按钮
                        }
                        RowLayout {
                            anchors { fill: parent; leftMargin: 10; rightMargin: 6 }
                            spacing: 8
                            Text {
                                text: (prow.index + 1) + "."
                                font.pixelSize: 11
                                color: Colors.textPlaceholder
                            }
                            Text {
                                Layout.fillWidth: true
                                text: prow.modelData.name
                                elide: Text.ElideRight
                                font.pixelSize: 13
                                color: Colors.textPrimary
                            }
                            Text {
                                text: root._kindLabel(prow.modelData.kind)
                                font.pixelSize: 10
                                color: Colors.textPlaceholder
                            }
                            ThemedButton {
                                objectName: "pathRun_" + prow.index
                                implicitHeight: 26
                                hPadding: 10
                                tone: "soft"
                                enabled: root.canRun
                                text: qsTr("执行")
                                fontPixelSize: 11
                                onClicked: root.runRequested(prow.index)
                            }
                            ThemedButton {
                                objectName: "pathDel_" + prow.index
                                implicitHeight: 26
                                hPadding: 8
                                tone: "neutral"
                                text: qsTr("删除")
                                fontPixelSize: 11
                                onClicked: root.deleteRequested(prow.index)
                            }
                        }
                        MouseArea {
                            anchors.fill: parent
                            acceptedButtons: Qt.LeftButton
                            onClicked: root.selectRequested(prow.index)
                            // ⚠ 让上面的按钮先拿到点击：这一层放在**最后**并
                            //   设 propagateComposedEvents，否则"执行"永远点不到。
                            propagateComposedEvents: true
                            onPressed: function (m) { m.accepted = false }
                        }
                    }
                }
            }

            // ── 进度 + 执行控制 ─────────────────────────────
            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder }

            RowLayout {
                Layout.fillWidth: true
                spacing: 10

                // 进度条（自己画，不用 ProgressBar：要和主题一致且好断言）
                Item {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 18
                    Rectangle {
                        anchors.fill: parent
                        radius: 9
                        color: Colors.cardBorder
                    }
                    Rectangle {
                        objectName: "trajProgressBar"
                        height: parent.height
                        radius: 9
                        color: root.pathState === "aborted" ? Colors.statusDisconnected : Colors.accent
                        width: root.pathTotal > 0
                               ? parent.width * Math.min(1, root.pathDone / root.pathTotal)
                               : 0
                        Behavior on width { NumberAnimation { duration: 120 } }
                    }
                    Text {
                        anchors.centerIn: parent
                        text: root.pathTotal > 0
                              ? (root.pathDone + " / " + root.pathTotal + "  " + qsTr("段"))
                              : qsTr("未运行")
                        font.pixelSize: 10
                        color: Colors.textPrimary
                    }
                }

                Text {
                    objectName: "trajEstimate"
                    visible: root.estimateS > 0
                    text: qsTr("约 %1 秒").arg(Math.round(root.estimateS))
                    font.pixelSize: 11
                    color: Colors.textPlaceholder
                }

                ThemedButton {
                    objectName: "trajRunButton"
                    implicitHeight: 36
                    hPadding: 18
                    tone: "primary"
                    enabled: root.canRun && root.selectedPath >= 0
                    text: qsTr("执行")
                    fontPixelSize: 13
                    onClicked: root.runRequested(root.selectedPath)
                }
                ThemedButton {
                    objectName: "trajPauseButton"
                    visible: root.pathState === "running" || root.pathState === "paused"
                    implicitHeight: 36
                    hPadding: 14
                    tone: "neutral"
                    text: root.pathState === "paused" ? qsTr("继续") : qsTr("暂停")
                    fontPixelSize: 12
                    onClicked: {
                        if (root.pathState === "paused") root.resumeRequested()
                        else root.pauseRequested()
                    }
                }
                ThemedButton {
                    objectName: "trajStopButton"
                    visible: root.pathState === "running" || root.pathState === "paused"
                    implicitHeight: 36
                    hPadding: 14
                    tone: "dangerSoft"
                    text: qsTr("停止轨迹")
                    fontPixelSize: 12
                    onClicked: root.stopRequested()
                }
            }

            // 不能跑的原因 / 中止原因（必须说出来，不能让用户对着灰按钮猜）
            Text {
                objectName: "trajHint"
                Layout.fillWidth: true
                visible: text.length > 0
                text: {
                    if (root.pathState === "aborted" && root.abortReason.length > 0)
                        return "⚠ " + root.abortReason
                    return root.hint.length > 0 ? ("⚠ " + root.hint) : ""
                }
                font.pixelSize: 11
                color: Colors.statusDisconnected
                wrapMode: Text.Wrap
            }
        }
    }

    // ============================================================
    // 交互逻辑（全部是"算参数"和"发信号"，不含任何运动逻辑）
    // ============================================================
    // 圆心处**能画出的最大半径** = 到工作区四边的最小距离。
    // ⚠ 这个夹取很关键（用户 2026-10-10 报"圆都可以画到外面去了"）：
    //   桥在保存/执行时会**把每个点夹进工作区**，于是不夹的话"图上看到的"
    //   和"实际会走的"不是同一个东西（一个越界的圆执行时会被压成带直边的形状，
    //   甚至完全不像圆）。夹到内切半径后，执行时一个点都不会被夹 → 所见即所得。
    function _maxRadiusAt(cx, cy) {
        return Math.max(0, Math.min(cx - wsXMin, wsXMax - cx,
                                    cy - wsYMin, wsYMax - cy))
    }
    // 把圆心/半径夹进工作区，并**回写输入框**（不静默改用户的数：他能看见被改成了多少）
    // ⚠ 这里**只封顶、不设下限**：下限（0.5mm）只属于"正在拖动"那条路径
    //   （见 `_applyDrag`）。要是这里也补一个下限，「重画」刚把半径归零，
    //   紧接着回写圆心文本触发的夹取就会把它顶回 0.5 —— 图上凭空冒出个小圆。
    function _clampCircle() {
        root.pCx = Math.max(wsXMin, Math.min(wsXMax, root.pCx))
        root.pCy = Math.max(wsYMin, Math.min(wsYMax, root.pCy))
        var rMax = root._maxRadiusAt(root.pCx, root.pCy)
        if (root.pR > rMax) root.pR = rMax
        root._syncInputs()
    }

    // 拖拽 → **实时写参数**（于是拖动/打字/切模式三条路径看到的形状完全一致）
    function _applyDrag() {
        if (!root.dragA || !root.dragB) return
        if (root.mode === "circle") {
            root.pCx = root.dragA[0]
            root.pCy = root.dragA[1]
            root.pR = Math.max(0.5, Math.hypot(root.dragB[0] - root.dragA[0],
                                               root.dragB[1] - root.dragA[1]))
            root._clampCircle()
        } else if (root.mode === "rect") {
            root.rectA = root.dragA
            root.rectB = root.dragB
        }
        root._touchDraft()
    }

    // 把参数回写到输入框文本（这三个框是**纯命令式**的，没有 text 绑定）。
    // ⚠ 必须显式赋值：不写回去就会出现"图上和数字不一致"（拖出来的半径不进输入框）。
    // ⚠⚠ 这里踩过两个坑，别再犯：
    //   1. QML 的 `id` **不等于** `objectName`。第一版只写了 objectName，
    //      于是访问 `circleRInput` 抛 `ReferenceError`，而且异常会把调用方
    //      剩下的语句一起吞掉（`_resetDraft()` 因此在 `_touchDraft()` 前中断，
    //      表现成"点重画没反应"）——所以那三个框必须显式声明 `id`。
    //   2. 不要再给它们加 `text:` 绑定：本函数会在 onTextChanged 里回写 text，
    //      与绑定打架会报 `Binding loop detected for property "text"`（实测报过）。
    // ⚠ 赋值前先比一次：值相同时还写会白转一圈、还可能抖动。
    function _syncInputs() {
        if (root.mode !== "circle") return
        var s = root.pR.toFixed(1)
        if (circleRInput.text !== s) circleRInput.text = s
        s = root.pCx.toFixed(1)
        if (circleCxInput.text !== s) circleCxInput.text = s
        s = root.pCy.toFixed(1)
        if (circleCyInput.text !== s) circleCyInput.text = s
    }

    // 重画 = 清掉"画出来的东西"，回到"等着你画"的状态。
    // ⚠ 圆的预览是**从参数推导**的（不是从拖拽点），所以光清 dragA/dragB
    //   什么都不会变 —— 用户 2026-10-10 报的"点重画没反应"就是这个：
    //   必须把参数也复位（半径归零 → 预览自然消失；圆心回到工作区中心）。
    function _resetDraft() {
        root.draft = []
        root.dragA = null
        root.dragB = null
        root.dragging = false
        root.rectA = null
        root.rectB = null
        // 圆心回工作区中心、半径归零（归零后 _touchDraft 不再画圆，输入框也会归零）
        root.pCx = (root.wsXMin + root.wsXMax) / 2
        root.pCy = (root.wsYMin + root.wsYMax) / 2
        root.pR = 0
        root._syncInputs()
        _touchDraft()
    }

    function _modeHint() {
        switch (root.mode) {
        case "select":   return qsTr("在台面上单击 → 相机立刻走到那一点（精度约 ±1mm；0.1mm 级精调仍用左侧十字键）")
        case "grid":     return qsTr("生成巡检点阵：每格中心一个点。按「存为轨迹」再用「执行」跑一遍。")
        case "circle":   return qsTr("在台面上按住 = 定圆心，拖 = 定半径；也可以直接改下面的半径/圆心。")
        case "rect":     return qsTr("在台面上按住拖出两个对角；松手后可以重新拖。")
        case "polyline": return qsTr("依次单击加顶点（图上会出现圆点），勾「平滑」可走样条。")
        default:         return ""
        }
    }

    function _kindLabel(kind) {
        switch (kind) {
        case "grid":     return qsTr("点阵")
        case "circle":   return qsTr("圆")
        case "rect":     return qsTr("矩形")
        case "polyline": return qsTr("多段线")
        case "recorded": return qsTr("录制")
        default:         return kind
        }
    }

    function _canSave() {
        if (!root.connected) return false
        switch (root.mode) {
        case "grid":     return true
        case "circle":   return root.pR > 0.5
        case "rect":     return root.rectA !== null && root.rectB !== null
        case "polyline": return root.draft.length >= 2
        default:         return false
        }
    }

    // 按模式把参数拼成 JSON。
    // ⚠ 传 JSON 字符串而不是 JS 对象：QML 的 JS 对象过到 Python 是嵌套的
    //   QVariantMap/QVariantList，`json.dumps` 序列化不了，得先递归转换 ——
    //   那是一类"看着能跑、偶尔炸"的坑（StageBridge.savePath 的注释里写了）。
    function _saveCurrent() {
        var name = root.saveName.length > 0 ? root.saveName : root._autoName()
        var kind = root.mode
        var prm = null
        if (kind === "grid") {
            prm = { rows: root.pRows, cols: root.pCols, serpentine: root.pSerpentine,
                    x0: root.wsXMin, y0: root.wsYMin, x1: root.wsXMax, y1: root.wsYMax,
                    step: root.pStep }
        } else if (kind === "circle") {
            prm = { cx: root.pCx, cy: root.pCy, r: root.pR, step: root.pStep }
        } else if (kind === "rect") {
            prm = { x0: root.rectA[0], y0: root.rectA[1],
                    x1: root.rectB[0], y1: root.rectB[1], step: root.pStep }
        } else if (kind === "polyline") {
            prm = { points: root.draft, smooth: root.pSmooth, step: root.pStep }
        } else {
            return
        }
        root.saveRequested(name, kind, JSON.stringify(prm), root.pathRpm, root.pathAcc)
    }

    function _autoName() {
        var d = new Date()
        function p2(v) { return (v < 10 ? "0" : "") + v }
        return root._kindLabel(root.mode) + " "
               + p2(d.getMonth() + 1) + p2(d.getDate()) + "-"
               + p2(d.getHours()) + p2(d.getMinutes())
    }
}
