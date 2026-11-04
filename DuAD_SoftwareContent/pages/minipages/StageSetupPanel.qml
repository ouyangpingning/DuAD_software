import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software
import "../components"

/*
    平台设置面板 — 网络、工作区（行程）、速度。默认折叠：这些调一次就不动。

    为什么工作区放在这里、而且要显眼：
      固件对 `g0`/`move` 是 fail-closed 的 —— **不设行程就一律拒绝移动**。
      所以连上之后必须把工作区推下去，否则用户点任何方向键都是"被拒绝"。
      Bridge 在认证通过后会自动下发一次，这里负责让用户能改。

    2026-11 用户反馈：设置面板太长，所以每个功能分区都改成可折叠的
    （CollapsibleHeader + 一层 ColumnLayout 的 visible）。默认只展开 Wi-Fi，
    其它分区点标题再展开；「应用设置」始终在底部可见。
*/
Item {
    id: root
    objectName: "setupPanel"

    property bool expanded: false
    // 自己那个 14px 标题画不画 —— 住在折叠节里时由页面置 false（折叠头已经报了名字）
    property bool showTitle: true

    // 分区展开状态（不写进 QSettings：只是界面习惯）
    property bool wifiOpen: true
    property bool netOpen: false
    property bool workOpen: false
    property bool speedOpen: false
    property bool pathOpen: false
    property bool parkOpen: false

    // 网络
    property string host: ""
    property int port: 3333
    property string token: ""

    // 工作区（mm）
    property real wsXMin: 0
    property real wsYMin: 0
    property real wsXMax: 360
    property real wsYMax: 420

    // 速度（手动点动/绝对定位）
    property int rpm: 1200
    property int acc: 1
    // 轨迹速度（与上面的手动速度刻意分开：轨迹默认 1200/30）
    property int pathRpm: 1200
    property int pathAcc: 30

    signal applyRequested()
    signal speedChanged(int rpm, int acc)
    signal pathSpeedChanged(int rpm, int acc)

    // 输入框里的**当前内容**（不是 bridge 里已保存的值）
    readonly property string fieldHost: hostRow.text.trim()
    readonly property string fieldToken: tokenRow.text.trim()
    readonly property int fieldPort: {
        var p = parseInt(portRow.text)
        return isNaN(p) ? 3333 : p
    }
    readonly property bool fieldsFilled: fieldHost.length > 0 && fieldToken.length > 0
    readonly property real fieldXmin: parseFloat(xminRow.text)
    readonly property real fieldXmax: parseFloat(xmaxRow.text)
    readonly property real fieldYmin: parseFloat(yminRow.text)
    readonly property real fieldYmax: parseFloat(ymaxRow.text)
    readonly property int fieldRpm: rpmRow.sliderValue
    readonly property int fieldAcc: accRow.sliderValue
    readonly property int fieldPathRpm: pathRpmRow.sliderValue
    readonly property int fieldPathAcc: pathAccRow.sliderValue

    implicitWidth: 460
    implicitHeight: expanded ? contentLayout.implicitHeight + 42 : 0
    clip: true

    Behavior on implicitHeight {
        NumberAnimation { duration: 250; easing.type: Easing.InOutCubic }
    }

    CardSurface {
        variant: "panel"
        anchors.fill: parent

        ColumnLayout {
            id: contentLayout
            spacing: 10
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 12 }

            Text {
                visible: root.showTitle
                text: qsTr("二轴平台设置")
                font.pixelSize: 14
                font.bold: true
                color: Colors.textPrimary
            }
            Rectangle {
                visible: root.showTitle
                Layout.fillWidth: true; implicitHeight: 1; color: Colors.panelBorder
            }

            BleConfigPanel {
                Layout.fillWidth: true
                bridge: StageBridge
                token: root.fieldToken.length > 0 ? root.fieldToken : StageBridge.token
                roleLabel: "XY"
                expanded: root.wifiOpen
                onExpandedChanged: root.wifiOpen = expanded
            }

            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.panelBorder }

            // ── 网络（直连 IP）─────────────────────────────
            CollapsibleHeader {
                objectName: "xyNetFold"
                Layout.fillWidth: true
                title: qsTr("网络")
                expanded: root.netOpen
                onToggled: root.netOpen = !root.netOpen
            }
            ColumnLayout {
                Layout.fillWidth: true
                visible: root.netOpen
                spacing: 8

                InputRow {
                    id: hostRow
                    objectName: "hostField"
                    label: qsTr("板子 IP")
                    text: root.host
                    placeholderText: "192.168.1.42"
                }
                InputRow {
                    id: portRow
                    label: qsTr("端口")
                    text: String(root.port)
                    placeholderText: "3333"
                }
                InputRow {
                    id: tokenRow
                    objectName: "tokenField"
                    label: qsTr("口令")
                    text: root.token
                    password: true
                    placeholderText: qsTr("8 位十六进制")
                }
                Text {
                    Layout.fillWidth: true
                    text: qsTr("三个值都在板子的 USB 控制台上敲 net 就能看到，板子会直接打印出来。")
                    font.pixelSize: 11
                    color: Colors.textPlaceholder
                    wrapMode: Text.Wrap
                }
            }

            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.panelBorder }

            // ── 工作区 ─────────────────────────────────────
            CollapsibleHeader {
                objectName: "xyWorkFold"
                Layout.fillWidth: true
                title: qsTr("工作区（台面行程，mm）")
                expanded: root.workOpen
                onToggled: root.workOpen = !root.workOpen
            }
            ColumnLayout {
                Layout.fillWidth: true
                visible: root.workOpen
                spacing: 8

                Text {
                    Layout.fillWidth: true
                    text: qsTr("必须按实际台面量准后填写：固件不设行程就拒绝一切绝对移动，"
                               + "填大了则会在撞到机械限位前不刹车。")
                    font.pixelSize: 11
                    color: Colors.textPlaceholder
                    wrapMode: Text.Wrap
                }
                InputRow {
                    id: xminRow; Layout.fillWidth: true
                    label: qsTr("X 小"); text: root.wsXMin.toFixed(1)
                }
                InputRow {
                    id: xmaxRow; Layout.fillWidth: true
                    label: qsTr("X 大"); text: root.wsXMax.toFixed(1)
                }
                InputRow {
                    id: yminRow; Layout.fillWidth: true
                    label: qsTr("Y 小"); text: root.wsYMin.toFixed(1)
                }
                InputRow {
                    id: ymaxRow; Layout.fillWidth: true
                    label: qsTr("Y 大"); text: root.wsYMax.toFixed(1)
                }
            }

            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.panelBorder }

            // ── 速度 ───────────────────────────────────────
            CollapsibleHeader {
                objectName: "xySpeedFold"
                Layout.fillWidth: true
                title: qsTr("速度")
                expanded: root.speedOpen
                onToggled: root.speedOpen = !root.speedOpen
            }
            ColumnLayout {
                Layout.fillWidth: true
                visible: root.speedOpen
                spacing: 8

                SliderRow {
                    id: rpmRow
                    objectName: "rpmSliderRow"
                    label: qsTr("转速")
                    from: 60; to: StageBridge.uiMaxRpm
                    sliderValue: root.rpm
                    suffix: "rpm"; decimals: 0
                    snapTicks: [60, 300, 600, 1200, 2000, StageBridge.uiMaxRpm]
                    wheelStep: 50
                    resetValue: 1200
                    onReleased: root.speedChanged(rpmRow.sliderValue, accRow.sliderValue)
                }
                SliderRow {
                    id: accRow
                    objectName: "accSliderRow"
                    label: qsTr("加减速")
                    from: 1; to: StageBridge.uiMaxAcc
                    sliderValue: root.acc
                    suffix: ""; decimals: 0
                    logScale: true
                    snapTicks: [1, 2, 3, 4, 5, 7, 9, 12, 16, 21, 28, 37, 50, 67, 90, 120, 160, 200]
                    wheelStep: accRow.sliderValue <= 20 ? 1
                             : (accRow.sliderValue <= 60 ? 2 : 5)
                    resetValue: 1
                    onReleased: root.speedChanged(rpmRow.sliderValue, accRow.sliderValue)
                }
            }

            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.panelBorder }

            // ── 轨迹速度 ───────────────────────────────────
            CollapsibleHeader {
                objectName: "xyPathFold"
                Layout.fillWidth: true
                title: qsTr("轨迹速度")
                expanded: root.pathOpen
                onToggled: root.pathOpen = !root.pathOpen
            }
            ColumnLayout {
                Layout.fillWidth: true
                visible: root.pathOpen
                spacing: 8

                SliderRow {
                    id: pathRpmRow
                    objectName: "pathRpmSliderRow"
                    label: qsTr("轨迹转速")
                    from: 60; to: StageBridge.uiMaxRpm
                    sliderValue: root.pathRpm
                    suffix: "rpm"; decimals: 0
                    snapTicks: [60, 300, 600, 1200, 2000, StageBridge.uiMaxRpm]
                    wheelStep: 50
                    resetValue: 1200
                    onReleased: root.pathSpeedChanged(pathRpmRow.sliderValue,
                                                      pathAccRow.sliderValue)
                }
                SliderRow {
                    id: pathAccRow
                    objectName: "pathAccSliderRow"
                    label: qsTr("轨迹加减速")
                    from: 1; to: StageBridge.uiMaxAcc
                    sliderValue: root.pathAcc
                    suffix: ""; decimals: 0
                    logScale: true
                    snapTicks: [1, 2, 3, 4, 5, 7, 9, 12, 16, 21, 28, 37, 50, 67, 90, 120, 160, 200]
                    wheelStep: pathAccRow.sliderValue <= 20 ? 1
                             : (pathAccRow.sliderValue <= 60 ? 2 : 5)
                    resetValue: 30
                    onReleased: root.pathSpeedChanged(pathRpmRow.sliderValue,
                                                      pathAccRow.sliderValue)
                }
            }

            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.panelBorder }

            // ── 关机行为 ───────────────────────────────────
            CollapsibleHeader {
                objectName: "xyParkFold"
                Layout.fillWidth: true
                title: qsTr("关机行为")
                expanded: root.parkOpen
                onToggled: root.parkOpen = !root.parkOpen
            }
            ColumnLayout {
                Layout.fillWidth: true
                visible: root.parkOpen
                spacing: 8

                SwitchRow {
                    objectName: "parkOnExit"
                    Layout.fillWidth: true
                    label: qsTr("退出时回到零点")
                    on: StageBridge.parkOnExit
                    onToggled: StageBridge.setParkOnExit(!StageBridge.parkOnExit)
                }
            }

            ThemedButton {
                Layout.fillWidth: true
                implicitHeight: 42
                tone: "primary"
                text: qsTr("应用设置")
                onClicked: root.applyRequested()
            }
        }
    }
}
