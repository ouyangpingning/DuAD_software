import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software
import "components"
import "minipages"

/*
    「平台控制」页 —— 两台板子（X/Y 二轴相机平台 + Z 轴升降平台）的 WiFi 控制台。

    版面（2026-09-28 用户第二张手绘稿，第四版 v4.1"自适应填满"）：

      ┌─────────┐ ┌───────────────────┐ ┌─────────┐
      │二轴相机平台│ │   实时预览          │ │Z轴升降平台│  ← 连接卡 + 一行状态
      ├─────────┤ │ （2448×2048 等比，  │ ├─────────┤
      │▸二轴…状态 │ │   允许有黑边）       │ │▸Z轴…状态  │  ← 折叠节（默认收起）
      │ [缩略图/  │ └───────────────────┘ │ [高度/偏斜]│  ← 读数（状态节的内容）
      │  X/Y读数] │ ┌─────────┬─────────┐ ├─────────┤
      │ +预设位置 │ │二轴手动控制│Z轴手动控制│ │[高度/偏斜]│
      ├─────────┤ └─────────┴─────────┘ ├─────────┤
      │▸二轴…设置 │ ┌───────────────────┐ │▸Z轴…设置  │  ← 折叠节（默认收起）
      │[网络/工作区│ │ ▸高级（协议显示）   │ │[网络/限位/ │
      │/速度/应用] │ └───────────────────┘ │ 速度/应用] │
      └─────────┘                        └─────────┘

      **三列自适应填满整页**（用户：手绘稿本来就是按全屏画的）：
      左右列 = 21% 页宽、夹在 [200,340]；中列吃满剩余。
      v4.0 曾按手绘稿等比定宽 884 居中 —— 用户否掉（全屏时两侧大片空白"非常窄"）。

    这一版相对上一版的三处结构性变化（都来自手绘稿）：

    1. **"左右滑出抽屉"取消**，改成左右列里各自的**垂直折叠节**
       （状态一节 + 设置一节）。手绘稿里它们就是上下堆在窄列里的。
    2. **平台设置从中央「高级」搬回各自的窄列**
       （网络/工作区/速度就在那台板子的连接卡下面）；
       中央的「高级」只剩**公用协议显示框**。
       ⚠ 2026-09-28 起时：**预设位置也搬去了左列「二轴相机平台状态」**
       （用户要求）—— 它就是 X/Y 两个坐标，跟二轴的读数是同一件事，
       顺带把左列下方那片空白填掉了；「高级」现在只剩协议显示。
    3. **两张手动控制卡收窄并排**（各 ~224px），
       步长 chips、急停、设原点按手绘稿的行序重排。

    数据流（与上一版一致）：
      StageBridge / ZStageBridge（各一条 WiFi/TCP）→ 遥测、闸门、协议行
      ProtoHub ← 两块桥的协议行（公用协议显示框）
      CameraBridge.frameIndex → image://camera/original?t=<index>（预览用）
      预览要拿帧就得走 startGather，而采集由 AppBridge.collectingOwner 仲裁 ——
      所以本页申请的是第三个 owner "stage"（另外两个是 detect / collect）。
      ⚠ StackLayout 切换页面时页面**不会被销毁**，所以离开时必须主动释放 owner。
*/

Item {
    id: root

    // ============================================================
    // 状态
    // ============================================================
    readonly property bool _connected: StageBridge.connected
    readonly property bool _moving: StageBridge.moving

    // ── Z 轴升降平台（另一块板子、另一个 IP）──────────────
    readonly property bool _zConnected: ZStageBridge.connected

    readonly property int _gap: 12

    // ── 三列宽度（v4.2：按手绘稿 160:350:160 ≈ 24%:52%:24% 配比，2026-09-28）──
    // v4.1 用的是 21% + 上限 340 —— 全屏（页宽 1680）时侧列只有 340（20%），
    // 而中列 976：① 连接卡的副标题被裁掉一半（"… · -58d"）；
    // ② 预览卡被拉成 976×530 的大黑框（画面只占中间 559px，两侧各 ~200px 黑边）。
    // 现在侧列 24%（全屏 403）、中列 850，两边的比例和手绘稿一致。
    // 下限 220 = 表单最小宽（InputRow 152 + 边距）；上限 420 防止超宽屏把输入框拉长。
    readonly property int _sideMin: 160      // 窄窗口时的硬下限（再窄连折叠头都放不下）
    readonly property int _sideMax: 420      // 超宽屏上限：别把输入框拉长
    readonly property int _midMin: 380       // 中列下限：够放"叠起来"的一张控制卡
    // 侧列宽 = min(24% 页宽, 上限, "让中列至少 _midMin" 的那个值)，且不低于 _sideMin。
    // 三条约束一起夹：宽屏看比例（手绘稿 24%:52%:24%），窄屏先保中列（控制卡优先）。
    readonly property real _effSide: {
        var byRatio = pageFlick.width * 0.24
        var byMid = (pageFlick.width - 2 * root._gap - root._midMin) / 2
        return Math.max(root._sideMin,
                        Math.min(root._sideMax, byRatio, Math.max(root._sideMin, byMid)))
    }
    // 中列实际宽度 = 页宽 − 两列 − 两道间距
    readonly property real _midColW: Math.max(340,
        pageFlick.width - 2 * _effSide - 2 * _gap)
    // 中列窄到"两张手动控制卡并排放不下"时改成上下叠（窄窗口/最小窗口的安全网）。
    // 700 = 两张卡各 ~344（够放步长 4 个 chip + 十字键 + 急停那行）+ 间距。
    readonly property bool _jogSideBySide: root._midColW >= 700

    // 预览画面高度上限：0.45×页高（下限 240 / 上限 560）。
    // 全屏时预览可以很大（用户点名），但上限必须有 —— 否则画面把下面的手动控制卡
    // （急停那行）顶出第一屏（tests/test_stage_page.py 12a2 量的是
    // "点动/急停/设原点 mapToItem 后的底边"，调这个系数后必须回跑它）。
    readonly property real _previewCapH: Math.max(240, Math.min(560, height * 0.45))

    // 预览内容区的宽高比 —— 相机是 2448×2048（近方形），手绘稿写明"允许有黑边"：
    // 按比例缩放居中，不拉扁。
    readonly property real _camRatio: (CameraBridge.imageWidth > 0 && CameraBridge.imageHeight > 0)
                                      ? (CameraBridge.imageWidth / CameraBridge.imageHeight)
                                      : (2448 / 2048)

    // 预览持有者就是本页时才算"在预览"
    readonly property bool _previewOn: AppBridge.collectingOwner === "stage"
    readonly property bool _previewLive: _previewOn && CameraBridge.frameIndex > 0

    // 五个折叠节：左右列各两个（状态/设置）+ 中央「高级」。
    // **默认全部收起**（手绘稿；首屏只留操作，细节展开才占地方）。
    property bool _xyStatusOpen: false
    property bool _zStatusOpen: false
    property bool _xySetupOpen: false
    property bool _zSetupOpen: false
    property bool _advExpanded: false
    // 协议框的当前来源（自定义命令发给谁、帧镜像开关跟着谁走）
    property string _protoSource: "z"

    // 缩略图上的目标叉：只在"确实发过一条移动"之后显示
    property real _targetX: NaN
    property real _targetY: NaN

    // 预览请求的落空监视。
    // 为什么需要：`collectingOwner = "stage"` 只是"申请"，真正的 startGather 由
    // main.py 的仲裁执行；**采集起不来时它会立刻把 owner 退回 ""**（典型原因：
    // 大分辨率下 Linux 的 usbfs 缓冲太小，ACQUISITION_START 返回 -1010）。
    // 那样开关会自己弹回来 —— 用户看到的又是"点了没反应"。所以这里等一小会儿，
    // 发现没生效就把原因写出来。
    property bool _previewWanted: false
    property bool _previewFailed: false

    // 为什么现在不能动 —— 对应固件那几道闸，翻译成人话。
    // 运动中是正常状态，不算"禁用原因"，所以单独排除。
    readonly property string _gateHint: {
        if (!_connected) return qsTr("未连接平台：先点上面的平台卡片连接")
        if (_moving) return ""
        // ⚠ 绝对位置模式用单圈编码器 —— 驱动器一掉电就丢多圈位置，所以**每次上电都要重立基准**，
        // 这一步省不掉。能"固定"的是物理位置（靠块/硬限位），不是驱动器里的数。
        if (!StageBridge.datum) return qsTr("缺少基准：每次上电都要重立一次 —— 先把滑座推到靠块/硬限位贴实，再点「设为原点」")
        if (!StageBridge.travelSet) return qsTr("未设置工作区：展开左侧「二轴相机平台设置」填写台面行程（固件不设行程就拒绝一切绝对移动）")
        return ""
    }

    readonly property bool _canMove: _connected && StageBridge.datum
                                     && StageBridge.travelSet && !_moving

    // ============================================================
    // ⚠ StackLayout 里页面只是被隐藏、不会被销毁：离开本页必须释放相机，
    //   否则采集会被本页一直占着，DetectPage/CollectPage 拿不到。
    onVisibleChanged: {
        if (!visible) {
            _previewWanted = false
            _previewFailed = false
            if (_previewOn) AppBridge.collectingOwner = ""
        }
    }

    // 预览开关的回同步。
    // ⚠ SwitchRow 内部是 `on = !on`，会**打断外部绑定**，所以不能只靠 `on:` 绑定：
    //   外部把 owner 清掉（离开本页、或被 Detect 抢占）时，必须显式把开关也拨回去，
    //   否则回来会看到"开关是开的、画面却没有"这种自相矛盾的状态。
    Connections {
        target: AppBridge
        function onCollectingOwnerChanged() {
            var mine = (AppBridge.collectingOwner === "stage")
            previewSwitch.on = mine
            if (mine) {
                root._previewWanted = false
                root._previewFailed = false
            } else {
                // owner 已经不是我（被别的页抢占，或仲裁退回）—— 不再等
                root._previewWanted = false
            }
        }
    }

    Timer {
        id: previewWatch
        interval: 900
        onTriggered: {
            // 只在"申请过、且现在仍然不是我在持有"时才算失败
            root._previewFailed = root._previewWanted && !root._previewOn
            root._previewWanted = false
        }
    }

    // ============================================================
    // 折叠加器（本页内部用）
    // ============================================================
    component FoldHeader: Item {
        id: fold
        property string title: ""
        property bool expanded: false
        // 收起时右侧的小标记（如「待设置」）。空串 = 不显示。
        property string badge: ""
        signal toggled()

        implicitHeight: 36

        // 折叠头 = **软强调底的内嵌块**（无描边、无阴影）。
        // 这样层次是：页面（白）→ 卡片（白底描边浮起）→ 折叠头/内嵌块（淡色）。
        // 改之前折叠头和卡片同为 contentBg、同 radius，堆在一起看不出谁是谁。
        Rectangle {
            anchors.fill: parent
            radius: 8
            color: foldMa.containsMouse ? Colors.interactiveHover : Colors.accentSoft
            Behavior on color { ColorAnimation { duration: 120 } }

            RowLayout {
                anchors { fill: parent; leftMargin: 12; rightMargin: 12 }
                spacing: 8

                // 展开箭头：原来是文本 "▸"/"▾" —— 依赖 wqy-microhei 里有这两个字形，
                // 渲染出来偏小、基线不齐，而且换主题时不能单独染色。
                // 现在用「下单箭头.svg」（实心下三角）：收起时转 −90°（指向右，即 ▸），
                // 展开时转回 0°（指向下，即 ▾）—— 和原来的语义一一对应。
                Item {
                    Layout.alignment: Qt.AlignVCenter
                    Layout.preferredWidth: 14
                    Layout.preferredHeight: 14

                    IconImage {
                        anchors.centerIn: parent
                        source: "../images/下单箭头.svg"
                        width: 11
                        height: 11
                        rotation: fold.expanded ? 0 : -90
                        Behavior on rotation { NumberAnimation { duration: 150; easing.type: Easing.OutCubic } }
                    }
                }
                Text {
                    text: fold.title
                    font.pixelSize: 13
                    font.bold: true
                    color: Colors.textPrimary
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }
                // badge：原来是**只有红字没有底**，在淡色条上非常弱。
                // 现在做成红底白字的 pill（一眼能看出"这块还没配好"）。
                Rectangle {
                    visible: !fold.expanded && fold.badge.length > 0
                    Layout.alignment: Qt.AlignVCenter
                    implicitWidth: badgeText.implicitWidth + 12
                    implicitHeight: 18
                    radius: 9
                    color: Colors.statusDisconnected

                    Text {
                        id: badgeText
                        anchors.centerIn: parent
                        text: fold.badge
                        font.pixelSize: 10
                        color: Colors.textOnAccent
                    }
                }
            }
        }

        MouseArea {
            id: foldMa
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: fold.toggled()
        }
    }

    // 折叠节的内容：**外层普通 Item 承接 visible**。
    // ⚠ 不能让内容自己 visible:false 了事：ColumnLayout 的 implicitHeight
    //   即使不可见也会被算进父列（实测页面凭空长高一大截）——
    //   折叠状态必须放在外层 Item 上（AGENTS 第 26 条版面硬规矩 5）。
    component FoldBody: Item {
        id: foldBody
        property alias inner: holder.data
        visible: false
        implicitHeight: holder.visible ? holder.implicitHeight : 0

        ColumnLayout {
            id: holder
            visible: foldBody.visible
            anchors { left: parent.left; right: parent.right; top: parent.top }
            spacing: 10
        }
    }

    // ============================================================
    // 折叠节的展开 + 滚过去
    // ============================================================
    // 齿轮/提示条要展开的折叠节在**各自的窄列里**，而页面是一条整页滚动链 ——
    // 只把 expanded 置真、不滚动，用户看到的就是"点了没反应"（AGENTS 第 24 条）。
    function _reveal(marker) {
        if (!marker) return
        var y = marker.mapToItem(pageFlick.contentItem, 0, 0).y
        var maxY = Math.max(0, pageFlick.contentHeight - pageFlick.height)
        var want = Math.max(0, Math.min(y - 12, maxY))
        if (Math.abs(want - pageFlick.contentY) < 2) return
        pageFlick.animScroll = true
        pageFlick.contentY = want
        pageScrollReset.restart()
    }

    // ⚠ 必须**等一帧再滚**：折叠刚置真时内容还没参与布局，
    //   `contentHeight` 还是旧值（= 视口高），于是 maxY=0、`_reveal` 滚不动 ——
    //   表现就是"点了齿轮，面板展开了但没滚过去"。60ms 足够让 ColumnLayout 算完。
    Timer {
        id: revealRetry
        interval: 60
        repeat: false
        // 要滚到的锚点（折叠头）。⚠ 传 Item 而不是算好的 y：
        //   等这一帧的时间里布局还会变，滚的时候现算才准。
        property Item target: null
        onTriggered: root._reveal(target)
    }

    Timer { id: pageScrollReset; interval: 280; onTriggered: pageFlick.animScroll = false }

    // 展开（+滚过去）某个折叠节。只展开不收起：提示条/齿轮的语义是"我要去填"。
    function _openFold(prop, marker) {
        root[prop] = true
        revealRetry.target = marker
        revealRetry.restart()
    }

    function _openAdvanced() {
        root._advExpanded = !root._advExpanded
        if (root._advExpanded) {
            revealRetry.target = advHeader
            revealRetry.restart()
        }
    }

    // ============================================================
    // 页面
    // ============================================================
    Rectangle {
        anchors.fill: parent
        color: Colors.pageBg

        Flickable {
            id: pageFlick
            objectName: "pageScroll"      // 页面测试量"该不该滚"用
            property bool animScroll: false
            anchors.fill: parent
            // ⚠ 右边距小一点（6）让滚动条更靠页面外缘；内容再自己留 14px（见 pageCol.width）
            anchors { leftMargin: 20; rightMargin: 6; topMargin: 20; bottomMargin: 20 }
            contentWidth: width
            contentHeight: Math.max(height, pageCol.implicitHeight)
            boundsBehavior: Flickable.StopAtBounds
            interactive: contentHeight > height + 1
            clip: true
            Behavior on contentY {
                enabled: pageFlick.animScroll
                NumberAnimation { duration: 220; easing.type: Easing.OutCubic }
            }

            ScrollBar.vertical: ScrollBar {
                id: pageBar
                policy: pageFlick.interactive ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff
                width: 8
                padding: 2
                background: Rectangle { color: "transparent" }
                contentItem: Rectangle {
                    implicitWidth: 6
                    radius: 3
                    color: pageBar.pressed ? Colors.interactivePressed : Colors.textPlaceholder
                    opacity: pageBar.active ? 0.85 : 0.35
                    Behavior on opacity { NumberAnimation { duration: 150 } }
                }
            }

            // ── 整块内容：占满页宽（v4.1：填满整页，不再定宽居中）──
            ColumnLayout {
                id: pageCol
                // ⚠ 高度必须显式绑到 contentHeight：ColumnLayout 自己不会去撑满 Flickable
                // ⚠ 减 14 是给**页面滚动条**留位置：不留的话滚动条压在右列卡片上
                //   （用户："这里的滑块往外些"）。滚动条在 Flickable 的右边缘，
                //   配合右外边距 6，它就落在离窗口外缘 6px 处、不再压内容。
                width: pageFlick.width - 14
                height: pageFlick.contentHeight
                spacing: root._gap

                // ══════════════════════════════════════════════
                // 三列（左：二轴 / 中：预览+手动控制+高级 / 右：Z 轴）
                // ══════════════════════════════════════════════
                // ⚠ 外面这层 Item 是必须的：**嵌套布局**上的 `Layout.preferredWidth`
                //   不可靠（AGENTS 第 26 条版面硬规矩 5）—— 用普通 Item 承接宽度，
                //   里面的 ColumnLayout 用 width 跟随、implicitHeight 往外报。
                RowLayout {
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignTop
                    spacing: root._gap

                    // ────────────────────────────────────────────
                    // 左列：二轴相机平台
                    // ────────────────────────────────────────────
                    Item {
                        Layout.preferredWidth: root._effSide
                        Layout.alignment: Qt.AlignTop
                        implicitHeight: xyCol.implicitHeight

                        ColumnLayout {
                            id: xyCol
                            width: parent.width
                            spacing: root._gap

                            StageControllerCard {
                                id: xyCard
                                objectName: "xyControllerCard"
                                compact: true
                                Layout.fillWidth: true
                                // 闸门状态直接画在卡片的状态行里（用户："状态只在卡片上显示"）
                                // ⚠ 未连接时**不传** "未连接" 这一项：状态行左边已经有
                                //   圆点 + 加粗的「未连接」了，再来一条红字 "⚠ 未连接"
                                //   就是同一个信息说两遍（用户 2026-09-28 抱怨过的那类重复）。
                                gates: !root._connected
                                       ? []
                                       : [
                                           { ok: StageBridge.enabled,
                                             text: StageBridge.enabled ? qsTr("已使能") : qsTr("未使能") },
                                           { ok: StageBridge.datum,
                                             text: StageBridge.datum ? qsTr("已立基准") : qsTr("无基准") },
                                           { ok: StageBridge.travelSet,
                                             text: StageBridge.travelSet ? qsTr("行程已设") : qsTr("行程未设") }
                                         ]
                                moving: root._moving
                                connected: root._connected
                                connecting: StageBridge.connecting
                                host: StageBridge.host
                                port: StageBridge.port
                                // ⚠ 副标题 = **只有地址**（2026-09-28 美化）。
                                //   原来是一整串 `host:port · 24.2V · 已使能 · -58dBm`：
                                //     · 窄列里被裁成 "…已使…"，什么都不剩；
                                //     · "已使能"和闸门里的"未使能"是同一件事说两遍；
                                //     · 电压/信号和地址一样重，扫读时抢注意力。
                                //   现在电压/信号走独立属性（卡片里用 闪电/信号格 图标画），
                                //   使能状态交给闸门（只在异常时出红字）。
                                subtitle: root._connected
                                    ? (StageBridge.host + ":" + StageBridge.port)
                                    : ""
                                voltage: StageBridge.voltage
                                rssi: StageBridge.rssi

                                onClicked: {
                                    if (StageBridge.connecting) return
                                    if (!root._connected) {
                                        // 用 fieldHost/fieldToken（输入框当前内容），
                                        // **不是** setupPanel.host（bridge 里已保存的旧值）
                                        var ok = StageBridge.connectDevice(setupPanel.fieldHost,
                                                                           setupPanel.fieldPort,
                                                                           setupPanel.fieldToken)
                                        // ⚠ 参数没填全时 connectDevice 会**直接拒绝**，
                                        //   卡片不会有任何变化（不进入"正在连接"）——
                                        //   所以这里把本列的「设置」折叠节展开（并滚过去），
                                        //   让用户看到该填什么。
                                        if (!ok) root._openFold("_xySetupOpen", xySetupFold)
                                    } else {
                                        AppBridge.collectingOwner = ""
                                        StageBridge.disconnectDevice()
                                    }
                                }
                                // 齿轮：把人带到**本列**的「平台设置」折叠节
                                onGearClicked: root._openFold("_xySetupOpen", xySetupFold)
                            }

                            // ── 还没配好地址/口令时的主动提示 ──────────
                            // 不能等用户点了才说：卡片看起来是可点的，点下去却什么都不发生，
                            // 用户只会以为程序坏了。所以在他点之前就把话说出来。
                            CardSurface {
                                Layout.fillWidth: true
                                visible: !root._connected && !StageBridge.connecting
                                         && !setupPanel.fieldsFilled
                                // ⚠ 原来写死 implicitHeight: 30 —— 窄列里文案换行成两行时
                                //   会直接顶出卡片（文字被裁一半）。改成跟着文本走。
                                implicitHeight: Math.max(34, xyPromptText.implicitHeight + 16)
                                color: Colors.cardDangerBg
                                borderColor: Colors.statusDisconnected

                                RowLayout {
                                    anchors { fill: parent; leftMargin: 10; rightMargin: 10 }
                                    spacing: 6

                                    IconImage {
                                        Layout.alignment: Qt.AlignTop
                                        Layout.topMargin: 2
                                        source: "../images/triangle-notice.svg"
                                        width: 14
                                        height: 14
                                        color: Colors.statusDisconnected
                                    }
                                    Text {
                                        id: xyPromptText
                                        Layout.fillWidth: true
                                        text: qsTr("还没配置板子地址 —— 点这里展开下方「平台设置」填入，"
                                                   + "板子 USB 控制台敲 net 会打印这三个值")
                                        font.pixelSize: 11
                                        color: Colors.statusDisconnected
                                        wrapMode: Text.WrapAnywhere
                                    }
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: root._openFold("_xySetupOpen", xySetupFold)
                                }
                            }

                            // ── 错误提示（常驻在卡片下方）────────
                            CardSurface {
                                Layout.fillWidth: true
                                visible: StageBridge.lastError.length > 0
                                implicitHeight: errText.implicitHeight + 16
                                color: Colors.cardDangerBg
                                borderColor: Colors.statusDisconnected

                                Text {
                                    id: errText
                                    anchors { fill: parent; margins: 8 }
                                    text: StageBridge.lastError
                                    font.pixelSize: 12
                                    color: Colors.statusDisconnected
                                    wrapMode: Text.Wrap
                                }
                            }

                            // ── 状态折叠节：工作区缩略图 + X/Y 读数 ──
                            FoldHeader {
                                id: xyStatusFold
                                objectName: "xyStatusFold"
                                Layout.fillWidth: true
                                title: qsTr("二轴相机平台状态")
                                expanded: root._xyStatusOpen
                                onToggled: root._xyStatusOpen = !root._xyStatusOpen
                            }

                            FoldBody {
                                id: xyStatusBody
                                objectName: "xyStatusBody"
                                Layout.fillWidth: true
                                visible: root._xyStatusOpen

                                // 2026-09-28（用户要求）：**预设位置从「高级」搬进这里** ——
                                // "预设位置"本来就是 X/Y 两个坐标，跟二轴平台的读数是同一件事，
                                // 放在二轴这一列比塞在中列「高级」里顺着用。它也顺便把左列
                                // 下方那片空白填掉了。
                                inner: [
                                    PositionReadout {
                                        Layout.fillWidth: true
                                        flat: true
                                        live: root._connected
                                        posX: StageBridge.posX
                                        posY: StageBridge.posY
                                        targetX: root._targetX
                                        targetY: root._targetY
                                        wsXMin: StageBridge.wsXMin
                                        wsYMin: StageBridge.wsYMin
                                        wsXMax: StageBridge.wsXMax
                                        wsYMax: StageBridge.wsYMax
                                    },

                                    StagePresetPanel {
                                        Layout.fillWidth: true
                                        // 折叠头只写了"二轴相机平台状态"，没有"预设"两个字，
                                        // 所以这里保留它自己的小标题，把"读数"和"预设"分开。
                                        showTitle: true
                                        canUse: root._canMove
                                        presets: StageBridge.presets

                                        onGotoRequested: function (index) {
                                            var list = StageBridge.presets
                                            if (index < 0 || index >= list.length) return
                                            if (StageBridge.gotoPreset(index)) {
                                                root._targetX = Number(list[index].x)
                                                root._targetY = Number(list[index].y)
                                            }
                                        }
                                        onDeleteRequested: function (index) {
                                            StageBridge.deletePreset(index)
                                        }
                                        onSaveRequested: function (name) {
                                            StageBridge.savePreset(name)
                                        }
                                    }
                                ]
                            }

                            // ── 设置折叠节：网络 / 工作区 / 速度 ────
                            FoldHeader {
                                id: xySetupFold
                                objectName: "xySetupFold"
                                Layout.fillWidth: true
                                title: qsTr("二轴相机平台设置")
                                badge: (root._connected && !StageBridge.travelSet)
                                       ? qsTr("待设置") : ""
                                expanded: root._xySetupOpen
                                onToggled: root._xySetupOpen = !root._xySetupOpen
                            }

                            FoldBody {
                                id: xySetupBody
                                objectName: "xySetupBody"
                                Layout.fillWidth: true
                                visible: root._xySetupOpen

                                inner: StageSetupPanel {
                                    id: setupPanel
                                    Layout.fillWidth: true
                                    // 折叠由上面的折叠头负责，这里恒展开
                                    expanded: true
                                    // 标题也由折叠头负责（"二轴相机平台设置"）——
                                    // 面板自己再画一遍"二轴平台设置"就是同一句话说两次
                                    showTitle: false

                                    host: StageBridge.host
                                    port: StageBridge.port
                                    token: StageBridge.token
                                    wsXMin: StageBridge.wsXMin
                                    wsYMin: StageBridge.wsYMin
                                    wsXMax: StageBridge.wsXMax
                                    wsYMax: StageBridge.wsYMax
                                    rpm: StageBridge.rpm
                                    acc: StageBridge.acc

                                    // 拖动滑块松手立即生效（不必等「应用设置」）
                                    onSpeedChanged: function (rpm, acc) {
                                        StageBridge.setSpeed(rpm, acc)
                                    }

                                    onApplyRequested: {
                                        // ⚠ 一律走 setupPanel 暴露的 `field*`（= 输入框**当前内容**）。
                                        //   以前这里直接写 `xminRow.text` / `rpmRow.sliderValue` —— 那些 id
                                        //   属于 StageSetupPanel.qml，在**本文件里取不到**：点击时抛
                                        //   `ReferenceError: xminRow is not defined`，后面三条下发
                                        //   （工作区/速度/连接）**一条都不执行**，表现正是"点了没反应"。
                                        StageBridge.setWorkspace(setupPanel.fieldXmin, setupPanel.fieldYmin,
                                                                 setupPanel.fieldXmax, setupPanel.fieldYmax)
                                        StageBridge.setSpeed(setupPanel.fieldRpm, setupPanel.fieldAcc)
                                        // 已连着同一目标时 connectDevice 内部会跳过重连（只更新参数）
                                        StageBridge.connectDevice(setupPanel.fieldHost,
                                                                  setupPanel.fieldPort,
                                                                  setupPanel.fieldToken)
                                    }
                                }
                            }
                        }
                    }

                    // ────────────────────────────────────────────
                    // 中列：实时预览 + 两张手动控制卡 + 「高级」
                    // ────────────────────────────────────────────
                    Item {
                        Layout.fillWidth: true
                        Layout.alignment: Qt.AlignTop
                        implicitHeight: midCol.implicitHeight

                        ColumnLayout {
                            id: midCol
                            width: parent.width
                            spacing: root._gap

                            // ── 实时预览 ──────────────────────────
                            // 卡片占满中列宽，画面在卡内按相机比例（2448×2048）
                            // 等比缩放居中、**允许黑边**（手绘稿原文）—— 不拉扁。
                            // 画面高度由 _previewCapH 封顶（防止吃掉首屏的操作区）。
                            CardSurface {
                                id: previewCard
                                objectName: "previewCard"
                                Layout.fillWidth: true
                                Layout.preferredHeight: root._previewH

                                ColumnLayout {
                                    anchors { fill: parent; margins: 12 }
                                    spacing: 8

                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: 8

                                        Text {
                                            text: qsTr("实时预览")
                                            font.pixelSize: 13
                                            font.bold: true
                                            color: Colors.textPrimary
                                        }
                                        Text {
                                            text: qsTr("↑+Y 向里   →+X 向右")
                                            font.pixelSize: 10
                                            color: Colors.textPlaceholder
                                        }
                                        Item { Layout.fillWidth: true }

                                        Text {
                                            visible: !AppBridge.cameraConnected
                                            text: qsTr("相机未连接")
                                            font.pixelSize: 10
                                            color: Colors.textPlaceholder
                                        }

                                        SwitchRow {
                                            id: previewSwitch
                                            objectName: "previewSwitch"
                                            label: ""
                                            on: root._previewOn
                                            enabled: AppBridge.cameraConnected
                                            onToggled: {
                                                // 申请/释放采集：相机只能被一个会话抓着
                                                root._previewFailed = false
                                                root._previewWanted = on
                                                AppBridge.collectingOwner = on ? "stage" : ""
                                                if (on) previewWatch.restart()
                                            }
                                        }
                                    }

                                    Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder }

                                    ImageView {
                                        Layout.fillWidth: true
                                        Layout.fillHeight: true
                                        // 手绘稿写明"按 2448×2048 比例缩放，允许有黑边"：
                                        // 不约束比例就会把画面拉扁（看着不对，却查不出哪儿错）
                                        aspectRatio: root._camRatio
                                        placeholderText: !AppBridge.cameraConnected
                                                         ? qsTr("相机未连接 —— 预览需要先在「相机设置」里连上相机")
                                                         : (root._previewOn
                                                            ? qsTr("正在等待画面…")
                                                            : qsTr("预览已关闭（打开右上角开关即可）"))
                                        imageActive: root._previewLive
                                        imageSource: "image://camera/original?t=" + CameraBridge.frameIndex
                                    }

                                    Text {
                                        Layout.fillWidth: true
                                        visible: root._previewFailed
                                        text: qsTr("⚠ 预览没能启动：相机采集没有起来。"
                                                   + "请到「相机设置」确认相机已连上且没被别的页面占着；"
                                                   + "大分辨率下也可能是系统 usbfs 缓冲太小"
                                                   + "（终端跑 bash scripts/set_usbfs.sh 后重启程序）。")
                                        font.pixelSize: 10
                                        color: Colors.statusDisconnected
                                        wrapMode: Text.Wrap
                                    }
                                }
                            }

                            // ── 两张手动控制卡（手绘稿）─────────────────
                            // ⚠ 用 GridLayout + 动态 `columns`：宽了并排、窄了自动上下叠
                            //   （窄窗口/最小窗口下中列只有 340~480，硬并排会把卡片挤到
                            //   比内容还窄）。封顶 520 是给超宽屏用的：不封顶会把
                            //   "十字键居中 + 急停整行"拉成横幅（内容空、难看）。
                            GridLayout {
                                Layout.fillWidth: true
                                columns: root._jogSideBySide ? 2 : 1
                                columnSpacing: root._gap
                                rowSpacing: root._gap

                                StageJogPanel {
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 344
                                    Layout.maximumWidth: 520
                                    // ⚠ 必须带 AlignTop：只写 AlignHCenter 时较矮的那张卡
                                    //   会在格子里**垂直居中**，两张卡的顶边就错开了
                                    //   （用户："这里没有顶部对齐"）
                                    Layout.alignment: Qt.AlignHCenter | Qt.AlignTop
                                    connected: root._connected
                                    canMove: root._canMove
                                    moving: root._moving
                                    gateHint: root._gateHint
                                    posX: StageBridge.posX
                                    posY: StageBridge.posY
                                    motorEnabled: StageBridge.enabled

                                    onEnableToggled: StageBridge.setMotorEnabled(!StageBridge.enabled)

                                    onJogRequested: function (dx, dy) {
                                        if (!StageBridge.jog(dx, dy)) return
                                        root._targetX = StageBridge.posX + dx
                                        root._targetY = StageBridge.posY + dy
                                    }
                                    onMoveToRequested: function (x, y) {
                                        // 先按工作区夹取再显示目标叉，和 bridge 的实际行为一致
                                        var cx = Math.max(StageBridge.wsXMin, Math.min(StageBridge.wsXMax, x))
                                        var cy = Math.max(StageBridge.wsYMin, Math.min(StageBridge.wsYMax, y))
                                        if (StageBridge.moveTo(x, y)) {
                                            root._targetX = cx
                                            root._targetY = cy
                                        }
                                    }
                                    onStopRequested: {
                                        StageBridge.stopNow()
                                        root._targetX = NaN
                                        root._targetY = NaN
                                    }
                                    onZeroRequested: {
                                        StageBridge.zeroAll()
                                        root._targetX = NaN
                                        root._targetY = NaN
                                    }
                                }

                                ZStageJogPanel {
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 344
                                    Layout.maximumWidth: 520
                                    Layout.alignment: Qt.AlignHCenter | Qt.AlignTop
                                    connected: root._zConnected
                                    moving: ZStageBridge.moving
                                    // 点动不受基准闸限制（固件允许无基准相对点动）；桥的 canJog 与之一致
                                    canJog: ZStageBridge.canJog
                                    motorEnabled: ZStageBridge.enabled
                                    onEnableToggled: ZStageBridge.setEnabled(!ZStageBridge.enabled)
                                    // ⚠ 不传桥的 canMove：它挂 telemetryChanged，而这里想从各自带 notify
                                    //   的属性本地算（见面板里的说明）——两者结论一致，但本地算不依赖约定
                                    datum: ZStageBridge.datum
                                    limitsSet: ZStageBridge.limitsSet
                                    // 桥已经算好了"为什么点不动"（未连接/无基准/无软限位），
                                    // 而且它是带 notify 的 Property —— 直接绑，不要在 QML 里调函数
                                    gateHint: ZStageBridge.datumHint
                                    posZ: ZStageBridge.z
                                    step: ZStageBridge.step
                                    stepChoices: ZStageBridge.stepChoices
                                    homing: ZStageBridge.homing

                                    onStepPicked: function (mm) { ZStageBridge.setStep(mm) }
                                    onJogUpRequested: ZStageBridge.jogUp(0)
                                    onJogDownRequested: ZStageBridge.jogDown(0)
                                    onMoveToRequested: function (mm) { ZStageBridge.moveTo(mm) }
                                    onZeroRequested: ZStageBridge.setZero()
                                    onHomeRequested: ZStageBridge.homeNow()
                                    // 急停同一入口：中断回零也走它（固件 `stop all` 能打断回零）
                                    onStopRequested: ZStageBridge.stopNow()
                                }
                            }

                            // ── 高级：公用协议显示框（预设位置已搬去左列）──────
                            FoldHeader {
                                id: advHeader
                                objectName: "advHeader"
                                Layout.fillWidth: true
                                Layout.topMargin: 4
                                // 2026-09-28：预设位置搬去左列「二轴相机平台状态」了，
                                // 这里只剩协议显示框
                                title: qsTr("高级（协议显示）")
                                badge: (root._connected && !StageBridge.travelSet)
                                       || (root._zConnected && !ZStageBridge.limitsSet)
                                       || !root._connected || !root._zConnected
                                          ? qsTr("待设置") : ""
                                expanded: root._advExpanded
                                onToggled: root._openAdvanced()
                            }

                            FoldBody {
                                id: advBody
                                objectName: "advBody"
                                Layout.fillWidth: true
                                visible: root._advExpanded

                                inner: [
                                    // ── 公用协议显示框（两块板子的流汇成一条）──
                                    ProtoPanel {
                                        Layout.fillWidth: true
                                        showTitle: false       // 标题由「高级」折叠头负责
                                        lines: ProtoHub.lines
                                        paused: ProtoHub.paused
                                        sources: [
                                            { key: "xy", label: qsTr("二轴相机平台"), connected: root._connected },
                                            { key: "z", label: qsTr("Z 轴升降平台"), connected: root._zConnected }
                                        ]
                                        sourceKey: root._protoSource
                                        sourceLabel: root._protoSource === "xy" ? qsTr("二轴相机平台")
                                                                               : qsTr("Z 轴升降平台")
                                        connected: root._protoSource === "xy" ? root._connected : root._zConnected
                                        // 帧镜像（`trace`）只有 Z 轴那块板子的固件有 —— 能力从桥上报，
                                        // 不在界面里写死（见 StageBridge.supportsTrace 的注释）
                                        traceSupported: root._protoSource === "xy" ? StageBridge.supportsTrace
                                                                                   : ZStageBridge.supportsTrace
                                        traceOn: root._protoSource === "z" && ZStageBridge.traceOn

                                        onSourcePicked: function (key) { root._protoSource = key }
                                        onTraceToggled: function (on) {
                                            if (root._protoSource === "z") ZStageBridge.setTrace(on)
                                        }
                                        onPauseToggled: function (p) { ProtoHub.setPaused(p) }
                                        onClearRequested: ProtoHub.clear()
                                        onCommandEntered: function (text) {
                                            ProtoHub.sendCommand(root._protoSource, text)
                                        }
                                    }
                                ]
                            }
                        }
                    }

                    // ────────────────────────────────────────────
                    // 右列：Z 轴升降平台
                    // ────────────────────────────────────────────
                    Item {
                        Layout.preferredWidth: root._effSide
                        Layout.alignment: Qt.AlignTop
                        implicitHeight: zCol.implicitHeight

                        ColumnLayout {
                            id: zCol
                            width: parent.width
                            spacing: root._gap

                            StageControllerCard {
                                objectName: "zControllerCard"
                                compact: true
                                Layout.fillWidth: true
                                // 闸门（+方向符号）同样只画红项
                                // ⚠ 未连接时不传 "未连接"（理由同 XY 卡：状态行左边已说过了）
                                gates: !root._zConnected
                                       ? []
                                       : [
                                           { ok: ZStageBridge.enabled,
                                             text: ZStageBridge.enabled ? qsTr("已使能") : qsTr("未使能") },
                                           { ok: ZStageBridge.datum,
                                             text: ZStageBridge.datum ? qsTr("已立基准") : qsTr("无基准") },
                                           { ok: ZStageBridge.limitsSet,
                                             text: ZStageBridge.limitsSet ? qsTr("软限位已设") : qsTr("软限位未设") }
                                         ]
                                moving: ZStageBridge.moving
                                title: qsTr("Z 轴升降平台")
                                connectingText: qsTr("正在连接 Z 轴平台...")
                                iconSource: "../images/Z轴平台.svg"

                                connected: root._zConnected
                                connecting: ZStageBridge.connecting
                                host: ZStageBridge.host
                                port: ZStageBridge.port
                                // 同上：副标题只留地址，电压/信号走独立属性
                                subtitle: root._zConnected
                                    ? (ZStageBridge.host + ":" + ZStageBridge.port)
                                    : ""
                                voltage: ZStageBridge.voltage
                                rssi: ZStageBridge.rssi

                                onClicked: {
                                    if (ZStageBridge.connecting) return
                                    if (!root._zConnected) {
                                        // 用输入框里的当前内容（不是桥里已保存的旧值）——
                                        // 否则"改了 IP 没点应用就点连接"会连旧地址
                                        var ok = ZStageBridge.connectDevice(zSetupPanel.fieldHost,
                                                                            zSetupPanel.fieldPort,
                                                                            zSetupPanel.fieldToken)
                                        if (!ok) root._openFold("_zSetupOpen", zSetupFold)
                                    } else {
                                        ZStageBridge.disconnectDevice()
                                    }
                                }
                                onGearClicked: root._openFold("_zSetupOpen", zSetupFold)
                            }

                            // ── 还没配好 Z 轴地址时的主动提示 ──────────
                            CardSurface {
                                Layout.fillWidth: true
                                visible: !root._zConnected && !ZStageBridge.connecting
                                         && !zSetupPanel.fieldsFilled
                                implicitHeight: Math.max(34, zPromptText.implicitHeight + 16)
                                color: Colors.cardDangerBg
                                borderColor: Colors.statusDisconnected

                                RowLayout {
                                    anchors { fill: parent; leftMargin: 10; rightMargin: 10 }
                                    spacing: 6

                                    IconImage {
                                        Layout.alignment: Qt.AlignTop
                                        Layout.topMargin: 2
                                        source: "../images/triangle-notice.svg"
                                        width: 14
                                        height: 14
                                        color: Colors.statusDisconnected
                                    }
                                    Text {
                                        id: zPromptText
                                        Layout.fillWidth: true
                                        text: qsTr("还没配置 Z 轴板子的地址 —— 点这里展开下方「Z 轴设置」填入，"
                                                   + "在 Z 轴板子的 USB 控制台上敲 net 就能看到这三个值")
                                        font.pixelSize: 11
                                        color: Colors.statusDisconnected
                                        wrapMode: Text.WrapAnywhere
                                    }
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: root._openFold("_zSetupOpen", zSetupFold)
                                }
                            }

                            // ── Z 轴故障（**必须常显**，不能藏进折叠节）──
                            CardSurface {
                                Layout.fillWidth: true
                                visible: root._zConnected && ZStageBridge.faultText.length > 0
                                implicitHeight: zFaultCol.implicitHeight + 16
                                color: Colors.cardDangerBg
                                borderColor: Colors.statusDisconnected

                                ColumnLayout {
                                    id: zFaultCol
                                    anchors { fill: parent; margins: 8 }
                                    spacing: 4

                                    Text {
                                        objectName: "zFaultText"
                                        Layout.fillWidth: true
                                        text: "⚠ " + ZStageBridge.faultText
                                        font.pixelSize: 12
                                        color: Colors.statusDisconnected
                                        wrapMode: Text.Wrap
                                    }
                                    Text {
                                        Layout.fillWidth: true
                                        text: qsTr("处置：先点「停止」（急停会同时清掉故障锁存），"
                                                   + "再把平台推到靠块重新「设为原点」。")
                                        font.pixelSize: 10
                                        color: Colors.textSecondary
                                        wrapMode: Text.Wrap
                                    }
                                }
                            }

                            // ── Z 轴错误提示 ──────────────────────────
                            CardSurface {
                                Layout.fillWidth: true
                                visible: ZStageBridge.lastError.length > 0
                                implicitHeight: zErrText.implicitHeight + 16
                                color: Colors.cardDangerBg
                                borderColor: Colors.statusDisconnected

                                Text {
                                    id: zErrText
                                    anchors { fill: parent; margins: 8 }
                                    text: ZStageBridge.lastError
                                    font.pixelSize: 12
                                    color: Colors.statusDisconnected
                                    wrapMode: Text.Wrap
                                }
                            }

                            // ── 状态折叠节：Z 遥测（高度/偏斜/示意）──
                            FoldHeader {
                                id: zStatusFold
                                objectName: "zStatusFold"
                                Layout.fillWidth: true
                                title: qsTr("Z 轴升降平台状态")
                                expanded: root._zStatusOpen
                                onToggled: root._zStatusOpen = !root._zStatusOpen
                            }

                            FoldBody {
                                id: zStatusBody
                                objectName: "zStatusBody"
                                Layout.fillWidth: true
                                visible: root._zStatusOpen

                                inner: ZStageTelemetryPanel {
                                    Layout.fillWidth: true
                                    flat: true
                                    vertical: true
                                    connected: root._zConnected
                                    posZ: ZStageBridge.z
                                    skew: ZStageBridge.skew
                                    fault: ZStageBridge.fault
                                    faultText: ZStageBridge.faultText
                                    firmwareLo: ZStageBridge.firmwareLo
                                    firmwareHi: ZStageBridge.firmwareHi
                                }
                            }

                            // ── 设置折叠节：网络 / 速度 / 软限位 ────
                            FoldHeader {
                                id: zSetupFold
                                objectName: "zSetupFold"
                                Layout.fillWidth: true
                                title: qsTr("Z 轴升降平台设置")
                                badge: (root._zConnected && !ZStageBridge.limitsSet)
                                       ? qsTr("待设置") : ""
                                expanded: root._zSetupOpen
                                onToggled: root._zSetupOpen = !root._zSetupOpen
                            }

                            FoldBody {
                                id: zSetupBody
                                objectName: "zSetupBody"
                                Layout.fillWidth: true
                                visible: root._zSetupOpen

                                inner: ZStageSetupPanel {
                                    id: zSetupPanel
                                    Layout.fillWidth: true
                                    expanded: true
                                    showTitle: false       // 同上：标题由折叠头负责

                                    host: ZStageBridge.host
                                    port: ZStageBridge.port
                                    token: ZStageBridge.token
                                    rpm: ZStageBridge.rpm
                                    acc: ZStageBridge.acc
                                    limitLo: ZStageBridge.limitLo
                                    limitHi: ZStageBridge.limitHi
                                    firmwareLo: ZStageBridge.firmwareLo
                                    firmwareHi: ZStageBridge.firmwareHi
                                    limitsSet: ZStageBridge.limitsSet

                                    onSpeedChanged: function (rpm, acc) {
                                        ZStageBridge.setSpeed(rpm, acc)
                                    }
                                    onApplyRequested: {
                                        // ⚠ 全部走面板暴露出来的 field* 属性，**不要**写
                                        //   `parseFloat(loRow.text)` —— loRow 是另一个文件里的 id，
                                        //   在这里取不到，点击时会抛 ReferenceError 而界面毫无反应。
                                        ZStageBridge.setSoftLimits(zSetupPanel.fieldLo, zSetupPanel.fieldHi)
                                        ZStageBridge.setSpeed(zSetupPanel.fieldRpm, zSetupPanel.fieldAcc)
                                        ZStageBridge.connectDevice(zSetupPanel.fieldHost,
                                                                   zSetupPanel.fieldPort,
                                                                   zSetupPanel.fieldToken)
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // ============================================================
    // 预览面板高度（放在根部算，页面里好用）：
    //   画面高 = min(高度上限, 卡宽换算) —— 恒为"等比后的真实高度"，
    //   面板高 = 画面高 + 表头 62（表头不是画面，混进去画面就会被挤出比例）。
    //   画面比卡宽窄时在卡内左右留黑边（手绘稿："允许有黑边"）。
    // ============================================================
    readonly property real _previewH: {
        var imgW = Math.min(root._midColW - 24, root._previewCapH * root._camRatio)
        return imgW / root._camRatio + 62
    }
}
