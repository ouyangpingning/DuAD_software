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

    这一版（一期）**只做运动**：到点拍照、循环 N 次是二期的事。
    执行引擎是 Python 侧的 `stage_traj.PathRunner`（点列序列器），本文件只负责
    画图、收点击、显示进度 —— 一行运动逻辑都不放在 QML 里（理由见下面"为什么
    不在 QML 里跑序列"）。

    ============================================================
    六个模式（顶部 chips 切换）
    ============================================================
      点选   单击格心 → 相机走过去（**粗定位**）
      网格   生成"巡检点阵"：每格中心一个点，蛇形走位
      圆     在图上拖：按下 = 圆心，拖 = 半径
      矩形   在图上拖两个对角
      多段线 依次点击顶点，勾「平滑」走样条
      录制   点顶部「● 录制」，之后所有手动动作（十字点动/绝对定位/
             网格点选/预设前往）都被记下来，结束时存成一条轨迹

    ⚠ **网格点选替代不了十字点动**，两者是互补的：
      屏上网格图约 330px、工作区 360mm → **1 像素 ≈ 1.1mm**，格距再密也点不出
      0.1mm 的精度。所以：网格负责"粗定位/巡检布点"，十字点动（0.1mm 档）和
      绝对定位输入框负责精调。这一点用户已确认。

    ⚠ **网格密度的现实上限约 40×40**：再密的话格线在屏幕上不足 10px，
      "看得清再点"就不成立了。要更细只能靠吸附（点最近格心）—— 桥那边的
      `snapToGrid()` 就是这么做的，所以它不随格线变密而失效。

    ============================================================
    为什么这一整张卡不用 Canvas
    ============================================================
    网格线和轨迹折线都用普通 `Rectangle`（网格线是 1px 长方形；轨迹是逐段旋转的
    细长方形），不用 `Canvas`。两个理由，都不是洁癖：

      1. **换主题会"纹丝不动"**：Canvas 是命令式重绘，`Colors` 换配色时没有任何
         信号能通知它重画 —— 而本项目的规矩是"全部颜色走 ColorAnimation"。
         `Rectangle` 是声明式的，颜色自动跟着主题变。
      2. **测得到**：`Rectangle` 在可视树里，页测试能数出"格线 38 条""轨迹 62 段"；
         Canvas 画了什么，测试一点都看不见（只能截图看，而那正是 AGENTS 里
         "别拿探针的不渲染当结论"警告过的那类假断言）。

    ============================================================
    坐标换算只有一处
    ============================================================
    `_px()/_py()`（台面 mm → 场内 px）与 `_fxOf()/_fyOf()`（场内 px → 台面比例）
    是本文件里**唯一**的坐标变换，画图与收点击都调它们。
    ⚠ `_py` 里有 **Y 翻转**：台面 +Y = 向里，而屏幕 Y 朝下 —— 所以图上 +Y 朝上
      （与 `PositionReadout.qml` 的缩略图画法一致）。
    ⚠ 点选算出来的 mm 值不由本文件决定：QML 只把"比例"交给桥的
      `StageBridge.snapToGrid(fx, fy)`，吸附/换算在 `stage_traj.snap_to_cell`
      里只做一次（同一事实两份 = 本项目第 4 条坑）。
*/

Item {
    id: root
    objectName: "trajCard"

    // ============================================================
    // 公有 API（全部由 StagePage 从 StageBridge 灌进来）
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

    property int gridRows: 20
    property int gridCols: 20

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
    signal gridClicked(real fx, real fy)                 // 比例，不是 mm
    signal saveRequested(string name, string kind, string paramsJson, int rpm, int acc)
    signal selectRequested(int index)
    signal deleteRequested(int index)
    signal runRequested(int index)
    signal pauseRequested()
    signal resumeRequested()
    signal stopRequested()
    signal gridChanged(int rows, int cols)
    signal recordStartRequested()
    signal recordStopRequested(string name)
    signal recordCancelRequested()

    implicitHeight: mainCol.implicitHeight + 32

    // ============================================================
    // 内部状态
    // ============================================================
    // 当前模式："select" | "grid" | "circle" | "rect" | "polyline"
    property string mode: "select"
    // 正在绘制的顶点（台面 mm）。点选模式不用它。
    property var draft: []
    // 拖拽的两个端点（圆用 [圆心, 圆周点]；矩形用两个对角）
    property var dragA: null
    property var dragB: null
    // 参数（各模式共用一个"段长"，其余按模式各有自己的）
    property real pStep: 5.0
    property int pRows: 6
    property int pCols: 6
    property bool pSerpentine: true
    property bool pSmooth: false
    property real pCx: 180
    property real pCy: 180
    property real pR: 50
    property string saveName: ""

    // 轨迹折线的分段表。
    // ⚠ 必须**命令式**地算好存进属性，不能在绑定里写 `_buildSegs(previewPoints)`
    //   —— QML 的绑定不会追踪函数体里的属性读取（AGENTS 第 6 条：绑定里别调函数），
    //   那样写完看起来能跑，实际只求值一次，选别的轨迹时线不会变。
    property var segs: []
    function _rebuildSegs() {
        var pts = root.previewPoints
        var out = []
        if (pts && pts.length > 1) {
            for (var i = 0; i + 1 < pts.length; i++) {
                var a = pts[i], b = pts[i + 1]
                out.push([a[0], a[1], b[0], b[1]])
            }
        }
        root.segs = out
    }
    onPreviewPointsChanged: _rebuildSegs()

    // 点选/绘制模式下"图上显示的临时形状"
    onDraftChanged: _rebuildDraftSegs()
    onDragAChanged: _rebuildDraftSegs()
    onDragBChanged: _rebuildDraftSegs()
    property var draftSegs: []
    function _rebuildDraftSegs() {
        var out = []
        if (root.mode === "polyline" && root.draft.length > 1) {
            for (var i = 0; i + 1 < root.draft.length; i++) {
                out.push([root.draft[i][0], root.draft[i][1],
                          root.draft[i + 1][0], root.draft[i + 1][1]])
            }
        } else if (root.mode === "circle" && root.dragA && root.dragB) {
            var c = root.dragA
            var r = Math.hypot(root.dragB[0] - c[0], root.dragB[1] - c[1])
            var n = 48
            for (var k = 0; k < n; k++) {
                var a1 = 2 * Math.PI * k / n, a2 = 2 * Math.PI * (k + 1) / n
                out.push([c[0] + r * Math.cos(a1), c[1] + r * Math.sin(a1),
                          c[0] + r * Math.cos(a2), c[1] + r * Math.sin(a2)])
            }
        } else if (root.mode === "rect" && root.dragA && root.dragB) {
            var x0 = Math.min(root.dragA[0], root.dragB[0]), x1 = Math.max(root.dragA[0], root.dragB[0])
            var y0 = Math.min(root.dragA[1], root.dragB[1]), y1 = Math.max(root.dragA[1], root.dragB[1])
            out = [[x0, y0, x1, y0], [x1, y0, x1, y1], [x1, y1, x0, y1], [x0, y1, x0, y0]]
        }
        root.draftSegs = out
    }

    // ============================================================
    // 坐标换算（本文件唯一的变换，画图与点击共用）
    // ============================================================
    readonly property real _wsW: Math.max(1e-6, wsXMax - wsXMin)
    readonly property real _wsH: Math.max(1e-6, wsYMax - wsYMin)
    // 场（工作区边框）在网格框里等比缩放后的像素尺寸
    readonly property real _fit: Math.min(_boxSide / _wsW, _boxSide / _wsH)
    readonly property real _fieldW: Math.max(40, _wsW * _fit)
    readonly property real _fieldH: Math.max(40, _wsH * _fit)
    // 网格框边长。宽度按卡的宽度取比例，上下叠时（窄窗口）允许占更大比例。
    readonly property real _boxSide: Math.max(160, Math.min(360,
        width * (width >= 620 ? 0.52 : 0.72)))

    function _px(xmm) { return _fieldW * (xmm - wsXMin) / _wsW }
    function _py(ymm) { return _fieldH * (1 - (ymm - wsYMin) / _wsH) }   // ⚠ Y 翻转
    function _fxOf(px) { return px / Math.max(1e-6, _fieldW) }
    function _fyOf(py) { return 1 - py / Math.max(1e-6, _fieldH) }        // ⚠ Y 翻转
    function _mmOf(fx, fy) {
        return [wsXMin + Math.max(0, Math.min(1, fx)) * _wsW,
                wsYMin + Math.max(0, Math.min(1, fy)) * _wsH]
    }

    // 轨迹/草稿折线的每一段 → 一个旋转的细长方形（不用 Canvas，见文件头）
    component SegLine: Item {
        id: seg
        // ⚠ 必须在这里声明 `required property var modelData`：Qt 6 里 Repeater 的
        //   delegate **只有声明了它**才拿得到模型数据。否则在 `segData: modelData`
        //   这种绑定里引用 modelData 会抛 `ReferenceError: modelData is not defined`
        //   —— 而且只在有数据时才炸（空列表时一切正常），很容易漏掉。
        required property var modelData
        readonly property var segData: modelData   // [x1,y1,x2,y2]（台面 mm）
        property color lineColor: Colors.accent
        property real lineWidth: 2

        // ⚠ 用 `x/y/width/height + rotation` 而不是 anchors：这是**普通 Item 直接
        //   放进一个定位容器**（不是 Layout），所以 width/height 不会被覆盖。
        x: root._px(Math.min(seg.segData[0], seg.segData[2]))
        y: root._py(Math.max(seg.segData[1], seg.segData[3]))
        width: Math.max(lineWidth, Math.hypot(seg.segData[2] - seg.segData[0],
                                              seg.segData[3] - seg.segData[1]))
        height: lineWidth

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
            opacity: 0.9
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

                // 录制：状态就写在按钮上（颜色 + 文字），不另开一行
                ThemedButton {
                    objectName: "recordButton"
                    implicitHeight: 32
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
                    ToolTip.text: qsTr("把接下来的手动操作记成一条轨迹：十字点动 / 绝对定位 / 网格点选 / 预设前往都会被录下来，之后可以原样重放。")
                }
                ThemedButton {
                    objectName: "recordCancelButton"
                    visible: root.recording
                    implicitHeight: 32
                    hPadding: 12
                    tone: "neutral"
                    text: qsTr("放弃")
                    fontPixelSize: 12
                    onClicked: root.recordCancelRequested()
                }
            }

            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder }

            // ── 主体：宽了「左网格图 | 右模式与参数」并排；窄了上下叠 ──
            // ⚠ 必须做这个退化：最窄支持宽度（页宽 730 → 中列 ~380）下右列只剩
            //   ~138px，而一个 `InputRow`（label 72 + 输入框）最小就要 ~230px ——
            //   硬并排会把右列内容顶出卡片外（渲染图里抓到过 chips 被挤出边界）。
            //   项目里的既有做法见 StagePage 的 `_jogSideBySide`。
            GridLayout {
                Layout.fillWidth: true
                columns: root.width >= 620 ? 2 : 1
                columnSpacing: 14
                rowSpacing: 10

                // ══════════ 左：网格图 ══════════
                Item {
                    id: mapBox
                    objectName: "trajMap"
                    Layout.preferredWidth: root._boxSide
                    Layout.preferredHeight: root._boxSide
                    Layout.alignment: Qt.AlignTop | Qt.AlignHCenter

                    Item {
                        id: fieldBox
                        objectName: "trajField"
                        anchors.centerIn: parent
                        width: root._fieldW
                        height: root._fieldH

                        // 台面边框
                        Rectangle {
                            anchors.fill: parent
                            color: "transparent"
                            border { width: 1; color: Colors.cardBorderStrong }
                            radius: 4
                        }
                        // 原点标记（零点写死在 X 左 + Y 外 = 图上的左下角，
                        // 与 stage_bridge.DATUM_CORNER 一致）
                        Rectangle {
                            x: -4
                            y: parent.height - 4
                            width: 8; height: 8; radius: 2
                            color: Colors.statusConnected
                            opacity: 0.85
                        }

                        // 网格线：cols-1 条竖线 + rows-1 条横线。
                        // ⚠ 用 Rectangle 不用 Canvas（见文件头：Canvas 换主题不会重画，
                        //   而且测试数不出它画了什么）。
                        Repeater {
                            objectName: "trajGridLines"
                            model: Math.max(0, root.gridCols - 1)
                            delegate: Rectangle {
                                x: fieldBox.width * (index + 1) / root.gridCols
                                y: 0
                                width: 1
                                height: fieldBox.height
                                color: Colors.cardBorder
                                opacity: 0.75
                            }
                        }
                        Repeater {
                            model: Math.max(0, root.gridRows - 1)
                            delegate: Rectangle {
                                x: 0
                                y: fieldBox.height * (index + 1) / root.gridRows
                                width: fieldBox.width
                                height: 1
                                color: Colors.cardBorder
                                opacity: 0.75
                            }
                        }

                        // 选中轨迹的落点折线
                        Repeater {
                            objectName: "trajPreview"
                            model: root.segs
                            delegate: SegLine {
                                lineColor: Colors.accent
                            }
                        }
                        // 正在绘制的形状（草稿）
                        Repeater {
                            model: root.draftSegs
                            delegate: SegLine {
                                lineColor: Colors.statusDisconnected
                                lineWidth: 1
                            }
                        }
                        // 多段线还没连成线的顶点
                        Repeater {
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
                        //   点选/网格 → 单击就走；圆/矩形 → 按下拖、松手定形；
                        //   多段线 → 每次单击加一个顶点。
                        MouseArea {
                            id: mapMa
                            objectName: "trajMapArea"
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.CrossCursor
                            enabled: root.connected && !root.moving

                            onPressed: function (m) {
                                if (root.mode === "circle" || root.mode === "rect") {
                                    var f = root._fraction(m)
                                    root.dragA = root._mmOf(f[0], f[1])
                                    root.dragB = root.dragA
                                }
                            }
                            onPositionChanged: function (m) {
                                if (!pressed) return
                                if (root.mode === "circle" || root.mode === "rect") {
                                    var f = root._fraction(m)
                                    root.dragB = root._mmOf(f[0], f[1])
                                }
                            }
                            onReleased: function (m) {
                                if (root.mode === "circle" || root.mode === "rect") {
                                    root._commitDrag()
                                }
                            }
                            onClicked: function (m) {
                                var f = root._fraction(m)
                                if (root.mode === "select" || root.mode === "grid") {
                                    // 只把**比例**交给桥，mm 由 snapToGrid 算（唯一换算处）
                                    root.gridClicked(f[0], f[1])
                                } else if (root.mode === "polyline") {
                                    root.draft = root.draft.concat([root._mmOf(f[0], f[1])])
                                    root._rebuildDraftSegs()
                                }
                            }

                            // 点动/移动中不许改图，鼠标形状也说明一下
                            // ⚠ MouseArea 没有 `hovered`（那是 Control 的属性），
                            //   它只有 `containsMouse` —— 写 hovered 会抛
                            //   ReferenceError，而且只在悬停时炸，很容易漏掉。
                            ToolTip.visible: mapMa.containsMouse && !mapMa.enabled
                            ToolTip.delay: 700
                            ToolTip.text: root.moving ? qsTr("台面正在动") : qsTr("先连接平台")
                        }
                    }
                }

                // ══════════ 右：模式 + 参数 ══════════
                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignTop
                    spacing: 8

                    // 模式 chips：两行（5 个 chip 塞一行会在窄列里溢出）
                    GridLayout {
                        Layout.fillWidth: true
                        columns: 3
                        rowSpacing: 6
                        columnSpacing: 6
                        Repeater {
                            objectName: "trajModeChips"
                            model: [
                                { key: "select",   label: qsTr("点选") },
                                { key: "grid",     label: qsTr("网格") },
                                { key: "circle",   label: qsTr("圆") },
                                { key: "rect",     label: qsTr("矩形") },
                                { key: "polyline", label: qsTr("多段线") }
                            ]
                            delegate: ChoiceChip {
                                required property var modelData
                                objectName: "modeChip_" + modelData.key
                                Layout.fillWidth: true
                                implicitHeight: 28
                                implicitWidth: 56
                                checkable: false      // ⚠ 自己管 checked，否则 Qt 点击会翻转它、打断绑定
                                checked: root.mode === modelData.key
                                text: modelData.label
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

                    Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder }

                    // ── 各模式的参数（只显示当前模式那份）──
                    // ⚠ 参数一律**单列竖排**，不做成对并排：两个 `InputRow` 并排时
                    //   每个的 label 列固定 72px + 输入框，最小宽度加起来会超过
                    //   右列（~400px），把整个 RowLayout 顶宽 → 上面的模式 chips
                    //   第三列被挤出卡片右边（渲染图里看到过）。
                    //   竖排没有这个风险，而且卡的总高由左边的网格图决定，不亏。
                    InputRow {
                        objectName: "stepInput"
                        Layout.fillWidth: true
                        visible: root.mode !== "select"
                        label: qsTr("段长 mm")
                        text: root.pStep.toFixed(1)
                        onTextChanged: {
                            var v = parseFloat(text)
                            if (!isNaN(v) && v >= 0.2 && v <= 50) root.pStep = v
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

                    // 圆
                    InputRow {
                        objectName: "circleRInput"
                        Layout.fillWidth: true
                        visible: root.mode === "circle"
                        label: qsTr("半径 mm")
                        text: root.pR.toFixed(1)
                        onTextChanged: {
                            var v = parseFloat(text)
                            if (!isNaN(v) && v > 0) root.pR = v
                        }
                    }
                    InputRow {
                        objectName: "circleCxInput"
                        Layout.fillWidth: true
                        visible: root.mode === "circle"
                        label: qsTr("圆心 X")
                        text: root.pCx.toFixed(1)
                        onTextChanged: {
                            var v = parseFloat(text)
                            if (!isNaN(v)) root.pCx = v
                        }
                    }
                    InputRow {
                        objectName: "circleCyInput"
                        Layout.fillWidth: true
                        visible: root.mode === "circle"
                        label: qsTr("圆心 Y")
                        text: root.pCy.toFixed(1)
                        onTextChanged: {
                            var v = parseFloat(text)
                            if (!isNaN(v)) root.pCy = v
                        }
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

                    Text {
                        Layout.fillWidth: true
                        visible: root.mode === "rect"
                        text: qsTr("矩形：在图上按住拖出两个对角即可。")
                        font.pixelSize: 11
                        color: Colors.textPlaceholder
                        wrapMode: Text.Wrap
                    }

                    Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder }

                    // ── 轨迹速度（独立于手动点动）──
                    InputRow {
                        objectName: "pathRpmInput"
                        Layout.fillWidth: true
                        label: qsTr("轨迹 rpm")
                        text: String(root.pathRpm)
                        onTextChanged: {
                            var v = parseInt(text)
                            if (!isNaN(v) && v >= 1) root.pathRpm = v
                        }
                    }
                    InputRow {
                        objectName: "pathAccInput"
                        Layout.fillWidth: true
                        label: qsTr("轨迹 acc")
                        text: String(root.pathAcc)
                        onTextChanged: {
                            var v = parseInt(text)
                            if (!isNaN(v) && v >= 1) root.pathAcc = v
                        }

                        // 「为什么 acc 默认这么大」属于**说明**，按规矩不进界面正文
                        // （2026-09-28 精简：界面只留控件与当下状态），放 ToolTip。
                        // ⚠ `InputRow` 的根是普通 Item（不是 Control），**没有 `hovered`**
                        //   —— 写 `ToolTip.visible: hovered` 会 ReferenceError。
                        //   用 HoverHandler 取悬停态：它不消耗点击（MouseArea 会）。
                        HoverHandler { id: accHover }
                        ToolTip.visible: accHover.hovered
                        ToolTip.delay: 600
                        ToolTip.text: qsTr("加减速故意比手动点动大得多：位置模式每段都是一条完整的起步→匀速→刹停曲线，5mm 的小段用 acc=1 会全程在爬坡，一段要好几秒。")
                    }
                    // ── 存为轨迹 / 清空绘制 ──
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        visible: root.mode !== "select"
                        InputRow {
                            objectName: "saveNameInput"
                            Layout.fillWidth: true
                            label: qsTr("名称")
                            text: root.saveName
                            onTextChanged: root.saveName = text
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
                            text: qsTr("重画")
                            fontPixelSize: 12
                            onClicked: root._resetDraft()
                        }
                    }
                }
            }

            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder }

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
                text: qsTr("还没有轨迹。选一个模式在上面画出来，或者点右上角「录制」把手动操作记成一条。")
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
                            // ⚠ 让上面的按钮先拿到点击：按钮在 MouseArea 之后声明时
                            //   会被这个 MouseArea 盖住，所以这一层要放在**最后**并
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

            // 不能跑的原因 / 中止原因（都是**必须说出来**的，不能让用户对着灰按钮猜）
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
    function _fraction(m) {
        // ⚠ MouseArea 的坐标是相对自己的（= 场），直接用；Y 要翻转（台面 +Y 向里）
        return [root._fxOf(m.x), root._fyOf(m.y)]
    }

    function _resetDraft() {
        root.draft = []
        root.dragA = null
        root.dragB = null
        _rebuildDraftSegs()
    }

    function _commitDrag() {
        if (!root.dragA || !root.dragB) return
        if (root.mode === "circle") {
            var c = root.dragA
            var r = Math.hypot(root.dragB[0] - c[0], root.dragB[1] - c[1])
            root.pCx = c[0]
            root.pCy = c[1]
            root.pR = Math.max(0.5, r)
        } else if (root.mode === "rect") {
            root._rectA = root.dragA
            root._rectB = root.dragB
        }
    }
    property var _rectA: null
    property var _rectB: null

    function _modeHint() {
        switch (root.mode) {
        case "select":   return qsTr("单击网格 → 相机走到那个格心（粗定位；精调仍用左侧十字键的 0.1mm 档）")
        case "grid":     return qsTr("生成巡检点阵：每格中心一个点。按「存为轨迹」再用「执行」跑一遍。")
        case "circle":   return qsTr("在图上按住 = 定圆心，拖 = 定半径，松手定形。")
        case "rect":     return qsTr("在图上按住拖出两个对角。")
        case "polyline": return qsTr("依次单击加顶点（图上会出现红点），勾「平滑」可走样条。")
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
        case "rect":     return root._rectA !== null && root._rectB !== null
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
            prm = { x0: root._rectA[0], y0: root._rectA[1],
                    x1: root._rectB[0], y1: root._rectB[1], step: root.pStep }
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
