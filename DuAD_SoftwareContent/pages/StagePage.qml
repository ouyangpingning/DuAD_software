import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software
import "components"
import "minipages"

/*
    「平台控制」页 —— 通过 WiFi 控制二轴平台的相机位置。

    布局（docs/17 §7.1）：
      宽屏（≥1100px）：左列 460 控制流 + 右侧实时预览与位置读数
      窄屏：退化成单列，控制列居中，预览收起
    为什么值得开双栏（本项目第二个宽布局页面，第一个是 DetectPage）：
      调相机位置时**不看着画面就是盲开**。单列的话"位置数字"和"画面"被拆在
      两个页面，来回切页做定位很痛苦。

    数据流：
      StageBridge（WiFi/TCP）→ 位置/电压/基准/运动状态
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
    readonly property bool _wide: width >= 1100

    // 预览持有者就是本页时才算"在预览"
    readonly property bool _previewOn: AppBridge.collectingOwner === "stage"
    readonly property bool _previewLive: _previewOn && CameraBridge.frameIndex > 0

    property bool _setupExpanded: false
    property bool _diagExpanded: false
    property var _log: []

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

    // 为什么现在不能动 —— 对应固件那四道闸，翻译成人话。
    // 运动中是正常状态，不算"禁用原因"，所以单独排除。
    readonly property string _gateHint: {
        if (!_connected) return qsTr("未连接平台：先点上面的平台卡片连接")
        if (_moving) return ""
        // ⚠ 绝对位置模式用单圈编码器 —— 驱动器一掉电就丢多圈位置，所以**每次上电都要重立基准**，
        // 这一步省不掉。能"固定"的是物理位置（靠块/硬限位），不是驱动器里的数。
        if (!StageBridge.datum) return qsTr("缺少基准：每次上电都要重立一次 —— 先把滑座推到靠块/硬限位贴实，再点「⌂ 把当前位置设为原点」")
        if (!StageBridge.travelSet) return qsTr("未设置工作区：展开「平台设置」填写台面行程（固件不设行程就拒绝一切绝对移动）")
        return ""
    }

    readonly property bool _canMove: _connected && StageBridge.datum
                                     && StageBridge.travelSet && !_moving

    // ============================================================
    // 日志（诊断面板用）
    // ============================================================
    Component.onCompleted: {
        StageBridge.logMessage.connect(function (msg) {
            var lines = root._log.slice()
            lines.push(msg)
            // 只留最近 200 行：日志是给人看的，不是档案
            if (lines.length > 200) lines = lines.slice(lines.length - 200)
            root._log = lines
        })
    }

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
        signal toggled()

        implicitHeight: 36

        Rectangle {
            anchors.fill: parent
            radius: 8
            color: foldMa.containsMouse ? Colors.interactiveHover : Colors.contentBg

            RowLayout {
                anchors { fill: parent; leftMargin: 12; rightMargin: 12 }
                spacing: 8

                Text {
                    text: fold.expanded ? "▾" : "▸"
                    font.pixelSize: 12
                    color: Colors.textSecondary
                }
                Text {
                    text: fold.title
                    font.pixelSize: 12
                    color: Colors.textPrimary
                    Layout.fillWidth: true
                }
                Text {
                    visible: !fold.expanded && fold.title === qsTr("平台设置")
                             && (!root._connected || !StageBridge.travelSet)
                    text: qsTr("待设置")
                    font.pixelSize: 10
                    color: Colors.statusDisconnected
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

    // ============================================================
    // 页面
    // ============================================================
    Rectangle {
        anchors.fill: parent
        color: Colors.pageBg

        RowLayout {
            anchors.fill: parent
            anchors.margins: 24
            spacing: 20

            // 窄屏时把控制列推向中间
            Item { Layout.fillWidth: true; visible: !root._wide }

            // ── 左列：控制流 ──────────────────────────
            Flickable {
                id: leftFlick
                Layout.preferredWidth: 460
                Layout.fillHeight: true
                contentWidth: width
                contentHeight: leftCol.implicitHeight + 8
                clip: true
                boundsBehavior: Flickable.StopAtBounds

                // 滚动条：这一列内容比窗口高（5 张卡 + 2 个折叠区），
                // 没有它用户不知道下面还有东西 —— 会以为界面就这么多。
                // AsNeeded：只有真的能滚时才出现，不占地方也不误导。
                ScrollBar.vertical: ScrollBar {
                    id: leftBar
                    policy: ScrollBar.AsNeeded
                    width: 8
                    padding: 2
                    background: Rectangle { color: "transparent" }
                    contentItem: Rectangle {
                        implicitWidth: 6
                        radius: 3
                        color: leftBar.pressed ? Colors.interactivePressed
                                               : Colors.textPlaceholder
                        opacity: leftBar.active ? 0.85 : 0.35
                        Behavior on opacity { NumberAnimation { duration: 150 } }
                    }
                }

                ColumnLayout {
                    id: leftCol
                    // 给滚动条留出 12px，避免压住卡片右边缘
                    width: leftFlick.width - 12
                    spacing: 10

                    Text {
                        text: qsTr("平台控制")
                        font.pixelSize: 16
                        font.bold: true
                        color: Colors.textPrimary
                    }

                    // ── 设备卡片 ──────────────────────
                    StageControllerCard {
                        Layout.fillWidth: true
                        connected: root._connected
                        connecting: StageBridge.connecting
                        host: StageBridge.host
                        port: StageBridge.port
                        subtitle: root._connected
                            ? (StageBridge.voltage.toFixed(1) + "V · "
                               + (StageBridge.enabled ? qsTr("已使能") : qsTr("未使能"))
                               + " · " + StageBridge.rssi + "dBm")
                            : ""

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
                                //   这正是"点了没反应"的由来。所以这里把设置面板
                                //   展开，让用户立刻看到该填什么。
                                if (!ok) root._setupExpanded = true
                            } else {
                                AppBridge.collectingOwner = ""
                                StageBridge.disconnectDevice()
                            }
                        }
                        onGearClicked: root._setupExpanded = !root._setupExpanded
                    }

                    // ── 还没配好地址/口令时的主动提示 ──────────
                    // 不能等用户点了才说：卡片看起来是可点的，点下去却什么都不发生，
                    // 用户只会以为程序坏了。所以在他点之前就把话说出来。
                    Rectangle {
                        Layout.fillWidth: true
                        visible: !root._connected && !StageBridge.connecting
                                 && !setupPanel.fieldsFilled
                        implicitHeight: 30
                        radius: 8
                        color: Colors.cardDangerBg

                        Text {
                            anchors { fill: parent; leftMargin: 10; rightMargin: 10 }
                            verticalAlignment: Text.AlignVCenter
                            text: qsTr("⚠ 还没配置板子地址 —— 展开下面的「平台设置」填入，"
                                       + "板子 USB 控制台敲 net 会打印这三个值")
                            font.pixelSize: 11
                            color: Colors.statusDisconnected
                            elide: Text.ElideRight
                        }
                    }

                    // ── 错误提示（常驻在卡片下方）────────
                    Rectangle {
                        Layout.fillWidth: true
                        visible: StageBridge.lastError.length > 0
                        implicitHeight: errText.implicitHeight + 16
                        radius: 8
                        color: Colors.cardDangerBg

                        Text {
                            id: errText
                            anchors { fill: parent; margins: 8 }
                            text: StageBridge.lastError
                            font.pixelSize: 12
                            color: Colors.statusDisconnected
                            wrapMode: Text.Wrap
                        }
                    }

                    // ── 状态条（常驻，不随折叠消失）──
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: 34
                        radius: 8
                        color: Colors.contentBg

                        RowLayout {
                            anchors { fill: parent; leftMargin: 12; rightMargin: 12 }
                            spacing: 10

                            Repeater {
                                model: [
                                    {
                                        ok: root._connected,
                                        text: root._connected
                                            ? StageBridge.voltage.toFixed(1) + "V"
                                            : qsTr("无电压")
                                    },
                                    {
                                        ok: root._connected && StageBridge.enabled,
                                        text: StageBridge.enabled ? qsTr("已使能") : qsTr("未使能")
                                    },
                                    {
                                        ok: root._connected && StageBridge.datum,
                                        text: StageBridge.datum ? qsTr("已立基准") : qsTr("无基准")
                                    },
                                    {
                                        ok: root._connected && StageBridge.travelSet,
                                        text: StageBridge.travelSet ? qsTr("行程已设") : qsTr("行程未设")
                                    },
                                    {
                                        ok: root._connected && StageBridge.rssi > -75,
                                        text: root._connected ? StageBridge.rssi + "dBm" : "—"
                                    }
                                ]

                                delegate: RowLayout {
                                    spacing: 4
                                    Rectangle {
                                        width: 7; height: 7; radius: 4
                                        color: modelData.ok ? Colors.statusConnected
                                                            : Colors.statusDisconnected
                                    }
                                    Text {
                                        text: modelData.text
                                        font.pixelSize: 11
                                        color: Colors.textSecondary
                                    }
                                }
                            }

                            Item { Layout.fillWidth: true }

                            Text {
                                visible: root._moving
                                text: qsTr("运动中…")
                                font.pixelSize: 11
                                color: Colors.interactivePressed
                            }
                        }
                    }

                    // ── 折叠：平台设置 ─────────────────
                    // ⚠ 2026-09-13 位置调整（用户要求）：**移到「手动控制」上面**。
                    //   原因很实际：平台卡片右上角那个齿轮就是 `_setupExpanded` 的开关，
                    //   而设置面板原来在最下面 —— 点齿轮后要往下滚半屏才看得到它展开了，
                    //   现象上就是"点了没反应"（本项目最忌讳的那类）。
                    //   挨着卡片放，齿轮一动它就在眼前。
                    FoldHeader {
                        Layout.fillWidth: true
                        title: qsTr("平台设置")
                        expanded: root._setupExpanded
                        onToggled: root._setupExpanded = !root._setupExpanded
                    }

                    StageSetupPanel {
                        id: setupPanel
                        Layout.fillWidth: true
                        expanded: root._setupExpanded

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
                            StageBridge.setWorkspace(parseFloat(xminRow.text),
                                                     parseFloat(yminRow.text),
                                                     parseFloat(xmaxRow.text),
                                                     parseFloat(ymaxRow.text))
                            StageBridge.setSpeed(rpmRow.sliderValue, accRow.sliderValue)
                            // 已连着同一目标时 connectDevice 内部会跳过重连（只更新参数）
                            StageBridge.connectDevice(hostRow.text.trim(),
                                                      parseInt(portRow.text),
                                                      tokenRow.text.trim())
                        }
                    }

                    // ── 手动控制 ──────────────────────
                    StageJogPanel {
                        Layout.fillWidth: true
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

                    // ── 预设位置 ──────────────────────
                    StagePresetPanel {
                        Layout.fillWidth: true
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

                    // ── 折叠：诊断 ────────────────────
                    FoldHeader {
                        Layout.fillWidth: true
                        title: qsTr("诊断")
                        expanded: root._diagExpanded
                        onToggled: root._diagExpanded = !root._diagExpanded
                    }

                    StageDiagPanel {
                        Layout.fillWidth: true
                        expanded: root._diagExpanded
                        connected: root._connected
                        lastError: StageBridge.lastError
                        // ⚠ 不要写成 StageBridge.diagText()：
                        //   绑定里调函数没有依赖追踪，值会被冻结在创建那一刻
                        //   （诊断面板一直显示"未连接"就是这么来的）。
                        diagText: StageBridge.diagText
                        logLines: root._log

                        // ⚠ 2026-09-13 大扫除：这里原来接了 7 条回零相关的绑定
                        //   （homeKind/homeRpm/homeMa/homeCfgSent/homeCorner/homing/homingText）
                        //   和 5 个信号处理。无限位回零整套 UI 已删除，只剩通用排障设施。
                        //   限位开关的归零界面以后加，届时只需要接一个按钮 → `homeAll()`。

                        onPollRequested: StageBridge.pollNow()
                    }

                    Item { Layout.preferredHeight: 8 }
                }
            }

            Item { Layout.fillWidth: true; visible: !root._wide }

            // ── 右列：预览 + 位置读数 ─────────────────
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumWidth: 360
                visible: root._wide
                spacing: 10

                // 预览卡
                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumHeight: 200
                    radius: 12
                    color: Colors.contentBg

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

                        Text {
                            Layout.fillWidth: true
                            visible: root._previewOn && !root._previewFailed
                            text: qsTr("预览占用了相机采集（与「异常检测」「图像采集」互斥），"
                                       + "离开本页会自动释放。")
                            font.pixelSize: 10
                            color: Colors.textPlaceholder
                            wrapMode: Text.Wrap
                        }
                    }
                }

                // 位置读数 + 工作区缩略图
                PositionReadout {
                    Layout.fillWidth: true
                    live: root._connected
                    posX: StageBridge.posX
                    posY: StageBridge.posY
                    targetX: root._targetX
                    targetY: root._targetY
                    wsXMin: StageBridge.wsXMin
                    wsYMin: StageBridge.wsYMin
                    wsXMax: StageBridge.wsXMax
                    wsYMax: StageBridge.wsYMax
                }
            }
        }
    }
}
