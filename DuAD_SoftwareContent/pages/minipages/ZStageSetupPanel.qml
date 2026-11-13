import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software
import "../components"

/*
    Z 轴设置面板（默认折叠）—— 网络、速度、软限位、上电自动回零。

    ⚠ Z 轴是**另一块板子、另一个 IP**，所以这里自带一套地址/端口/口令，
      与 X/Y 那张卡的设置**互不影响**（两张卡可以同时连着两台板子）。

    2026-11 用户反馈：设置面板太长，所以每个功能分区都改成可折叠的
    （CollapsibleHeader + 一层 ColumnLayout 的 visible）。默认只展开 Wi-Fi。

    三条约定（都是本项目踩出来的）：

    1. **量程从桥里读**（`ZStageBridge.uiMaxRpm` / `uiMaxAcc`），不许在 QML 里写数字。
    2. **`fieldHost/fieldPort/fieldToken` 是"输入框里的当前内容"**，
       页面点卡片连接时必须用这三个。
    3. **上电自动回零必须写明它的代价**：开着的话板子一上电（约 3 秒后）
       就会自己朝机械死点撞一次 —— 用户不在场时那就是"平台自己动了"。
*/
Item {
    id: root
    objectName: "zSetupPanel"

    property bool expanded: false
    property bool showTitle: true

    // 分区展开状态（只是界面习惯，不进 QSettings）
    property bool wifiOpen: true
    property bool netOpen: false
    property bool speedOpen: false
    property bool limitOpen: false
    property bool homeOpen: false
    property bool autohomeOpen: false

    // 网络
    property string host: ""
    property int port: 3333
    property string token: ""

    // 速度 / 加减速
    property int rpm: 600
    property int acc: 1

    // 软限位（mm，相对基准零点）
    property real limitLo: 0
    property real limitHi: 250

    // 固件**实际**在用的软限位（显示用，来自 json）
    property real firmwareLo: 0
    property real firmwareHi: 250
    property bool limitsSet: false

    // ── 无限位回零的参数（`zset home`）────────────────────
    property int homeRpm: 400
    property int homeMa: 600
    property int homeTmo: 12000
    property bool autohome: false

    signal applyRequested()
    signal speedChanged(int rpm, int acc)
    signal homeApplyRequested(int rpm, int ma, int tmo)
    signal autohomeToggled(bool on)

    readonly property string fieldHost: hostRow.text.trim()
    readonly property string fieldToken: tokenRow.text.trim()
    readonly property int fieldPort: {
        var p = parseInt(portRow.text)
        return isNaN(p) ? 3333 : p
    }
    readonly property bool fieldsFilled: fieldHost.length > 0 && fieldToken.length > 0

    readonly property real fieldLo: {
        var v = parseFloat(loRow.text)
        return isNaN(v) ? root.limitLo : v
    }
    readonly property real fieldHi: {
        var v = parseFloat(hiRow.text)
        return isNaN(v) ? root.limitHi : v
    }
    readonly property int fieldRpm: rpmRow.sliderValue
    readonly property int fieldAcc: accRow.sliderValue

    readonly property int fieldHomeRpm: {
        var v = parseInt(homeRpmRow.text)
        return isNaN(v) ? root.homeRpm : v
    }
    readonly property int fieldHomeMa: {
        var v = parseInt(homeMaRow.text)
        return isNaN(v) ? root.homeMa : v
    }
    readonly property int fieldHomeTmo: {
        var v = parseInt(homeTmoRow.text)
        return isNaN(v) ? root.homeTmo : v
    }
    readonly property bool homeMaTooLow: fieldHomeMa < ZStageBridge.uiHomeMaSweetLo
    readonly property bool homeMaTooHigh: fieldHomeMa > ZStageBridge.uiHomeMaSweetHi

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
            objectName: "zSetupBody"
            spacing: 10
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 12 }

            Text {
                visible: root.showTitle
                text: qsTr("Z 轴设置")
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
                bridge: ZStageBridge
                roleLabel: "Z"
                expanded: root.wifiOpen
                onExpandedChanged: root.wifiOpen = expanded
            }

            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.panelBorder }

            // ── 网络（另一块板子）──────────────────────────
            CollapsibleHeader {
                objectName: "zNetFold"
                Layout.fillWidth: true
                title: qsTr("网络（Z 轴这块板子）")
                expanded: root.netOpen
                onToggled: root.netOpen = !root.netOpen
            }
            ColumnLayout {
                Layout.fillWidth: true
                visible: root.netOpen
                spacing: 8

                Text {
                    Layout.fillWidth: true
                    text: qsTr("Z 轴是另一块板子、另一个 IP，与上面二轴平台的地址互不影响。")
                    font.pixelSize: 11
                    color: Colors.textPlaceholder
                    wrapMode: Text.Wrap
                }
                InputRow {
                    id: hostRow
                    objectName: "zHostField"
                    label: qsTr("板子 IP")
                    text: root.host
                    placeholderText: "192.168.1.43"
                }
                InputRow {
                    id: portRow
                    objectName: "zPortField"
                    label: qsTr("端口")
                    text: String(root.port)
                    placeholderText: "3333"
                }
                InputRow {
                    id: tokenRow
                    objectName: "zTokenField"
                    label: qsTr("口令")
                    text: root.token
                    password: true
                    placeholderText: qsTr("8 位十六进制")
                }
                Text {
                    Layout.fillWidth: true
                    text: qsTr("口令在蓝牙配网时会自动读取；也可以从 Z 轴板子的 USB 控制台敲 net 查看后手工填写。")
                    font.pixelSize: 11
                    color: Colors.textPlaceholder
                    wrapMode: Text.Wrap
                }
            }

            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.panelBorder }

            // ── 速度 ────────────────────────────────────────
            CollapsibleHeader {
                objectName: "zSpeedFold"
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
                    objectName: "zRpmSliderRow"
                    label: qsTr("转速")
                    from: 1; to: ZStageBridge.uiMaxRpm
                    sliderValue: root.rpm
                    suffix: "rpm"; decimals: 0
                    snapTicks: [1, 60, 150, 300, 600, ZStageBridge.uiMaxRpm]
                    wheelStep: 10
                    onReleased: root.speedChanged(rpmRow.sliderValue, accRow.sliderValue)
                }
                // 与二轴同理：rpm 折算成 mm/s（Z 的导程由固件 json 的 umrev 给，默认 8000µm/圈）。
                Text {
                    objectName: "zRpmMmpsHint"
                    Layout.fillWidth: true
                    Layout.topMargin: -4
                    horizontalAlignment: Text.AlignRight
                    text: qsTr("≈ %1 mm/s").arg(
                              parseFloat((rpmRow.sliderValue / 60
                                          * ZStageBridge.umPerRev / 1000.0).toFixed(1)))
                    font.pixelSize: 11
                    color: Colors.textPlaceholder
                }
                SliderRow {
                    id: accRow
                    objectName: "zAccSliderRow"
                    label: qsTr("加减速")
                    from: ZStageBridge.uiMinAcc; to: ZStageBridge.uiMaxAcc
                    sliderValue: root.acc
                    suffix: ""; decimals: 0
                    logScale: true
                    snapTicks: [1, 2, 3, 4, 5, 7, 9, 12, 16, 21, 28, 37, 50, 67, 90, 120, 160, 200]
                    wheelStep: accRow.sliderValue <= 20 ? 1 : (accRow.sliderValue <= 60 ? 2 : 5)
                    onReleased: root.speedChanged(rpmRow.sliderValue, accRow.sliderValue)
                }
            }

            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.panelBorder }

            // ── 软限位 ──────────────────────────────────────
            CollapsibleHeader {
                objectName: "zLimitFold"
                Layout.fillWidth: true
                title: qsTr("软限位（mm，相对基准零点）")
                expanded: root.limitOpen
                onToggled: root.limitOpen = !root.limitOpen
            }
            ColumnLayout {
                Layout.fillWidth: true
                visible: root.limitOpen
                spacing: 8

                Text {
                    Layout.fillWidth: true
                    text: qsTr("必须按实际行程量准后填写：固件对绝对移动是 fail-closed 的 —— "
                               + "没设软限位就一律拒绝移动；填大了则会在撞到机械限位前不刹车。"
                               + "本机构械行程 250mm。")
                    font.pixelSize: 11
                    color: Colors.textPlaceholder
                    wrapMode: Text.Wrap
                }
                InputRow {
                    id: loRow; objectName: "zLimitLoField"
                    Layout.fillWidth: true
                    label: qsTr("最低"); text: root.limitLo.toFixed(1)
                }
                InputRow {
                    id: hiRow; objectName: "zLimitHiField"
                    Layout.fillWidth: true
                    label: qsTr("最高"); text: root.limitHi.toFixed(1)
                }
                Text {
                    Layout.fillWidth: true
                    visible: root.limitsSet
                    text: qsTr("板子当前生效：%1 ~ %2 mm")
                          .arg(root.firmwareLo.toFixed(2)).arg(root.firmwareHi.toFixed(2))
                    font.pixelSize: 10
                    color: Colors.textPlaceholder
                    wrapMode: Text.Wrap
                }
            }

            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.panelBorder }

            // ── 自动回零参数（`zset home`）──────────────────
            CollapsibleHeader {
                objectName: "zHomeFold"
                Layout.fillWidth: true
                title: qsTr("自动回零参数（存板子）")
                expanded: root.homeOpen
                onToggled: root.homeOpen = !root.homeOpen
            }
            ColumnLayout {
                Layout.fillWidth: true
                visible: root.homeOpen
                spacing: 8

                Text {
                    Layout.fillWidth: true
                    text: qsTr("限位电流＝驱动器判「顶住死点」的阈值。本机经验区 %1~%2 mA。")
                          .arg(ZStageBridge.uiHomeMaSweetLo).arg(ZStageBridge.uiHomeMaSweetHi)
                    font.pixelSize: 11
                    color: Colors.textPlaceholder
                    wrapMode: Text.Wrap
                }
                InputRow {
                    id: homeMaRow
                    objectName: "zHomeMaField"
                    Layout.fillWidth: true
                    label: qsTr("限位电流")
                    text: String(root.homeMa)
                    placeholderText: "100"
                }
                Text {
                    objectName: "zHomeMaWarn"
                    Layout.fillWidth: true
                    visible: root.homeMaTooLow || root.homeMaTooHigh
                    text: root.homeMaTooLow
                          ? qsTr("⚠ < %1 mA：太接近空转电流，会「一动就报完成」（假成功）")
                            .arg(ZStageBridge.uiHomeMaSweetLo)
                          : qsTr("⚠ > %1 mA：本机顶住时只有一百多 mA，会「永远不触发」")
                            .arg(ZStageBridge.uiHomeMaSweetHi)
                    font.pixelSize: 10
                    color: Colors.statusDisconnected
                    wrapMode: Text.Wrap
                }
                InputRow {
                    id: homeRpmRow
                    objectName: "zHomeRpmField"
                    Layout.fillWidth: true
                    label: qsTr("回零转速")
                    text: String(root.homeRpm)
                    placeholderText: "400"
                }
                InputRow {
                    id: homeTmoRow
                    objectName: "zHomeTmoField"
                    Layout.fillWidth: true
                    label: qsTr("回零超时")
                    text: String(root.homeTmo)
                    placeholderText: "12000"
                }
                Text {
                    objectName: "zHomeTmoWarn"
                    Layout.fillWidth: true
                    visible: ZStageBridge.homeTmoNeedMs > 0
                             && root.fieldHomeTmo < ZStageBridge.homeTmoNeedMs
                    text: qsTr("⚠ 超时偏短：满行程约需 %1 s（%2 rpm / 导程 %3 mm），"
                               + "离死点远时会在中途被判超时。")
                          .arg((ZStageBridge.homeTmoNeedMs / 1000).toFixed(1))
                          .arg(root.homeRpm)
                          .arg((ZStageBridge.umPerRev / 1000).toFixed(1))
                    font.pixelSize: 10
                    color: Colors.statusDisconnected
                    wrapMode: Text.Wrap
                }
                Text {
                    Layout.fillWidth: true
                    text: qsTr("范围 %1~%2 rpm · %3~%4 mA · %5~%6 ms")
                          .arg(ZStageBridge.uiHomeRpmMin).arg(ZStageBridge.uiHomeRpmMax)
                          .arg(ZStageBridge.uiHomeMaMin).arg(ZStageBridge.uiHomeMaMax)
                          .arg(ZStageBridge.uiHomeTmoMin).arg(ZStageBridge.uiHomeTmoMax)
                    font.pixelSize: 10
                    color: Colors.textPlaceholder
                    wrapMode: Text.Wrap
                }
                Text {
                    objectName: "zHomeCurrentText"
                    Layout.fillWidth: true
                    visible: ZStageBridge.connected
                    text: qsTr("板子当前生效：%1 rpm / %2 mA / %3 ms")
                          .arg(root.homeRpm).arg(root.homeMa).arg(root.homeTmo)
                    font.pixelSize: 10
                    color: Colors.textPlaceholder
                    wrapMode: Text.Wrap
                }
                ThemedButton {
                    objectName: "zHomeApplyButton"
                    Layout.fillWidth: true
                    implicitHeight: 38
                    text: qsTr("写入回零参数")
                    onClicked: root.homeApplyRequested(root.fieldHomeRpm, root.fieldHomeMa,
                                                       root.fieldHomeTmo)
                }
            }

            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.panelBorder }

            // ── 上电自动回零 ────────────────────────────────
            CollapsibleHeader {
                objectName: "zAutohomeFold"
                Layout.fillWidth: true
                title: qsTr("上电行为")
                expanded: root.autohomeOpen
                onToggled: root.autohomeOpen = !root.autohomeOpen
            }
            ColumnLayout {
                Layout.fillWidth: true
                visible: root.autohomeOpen
                spacing: 8

                SwitchRow {
                    id: zAutohomeSwitch
                    objectName: "zAutohomeSwitch"
                    Layout.fillWidth: true
                    label: qsTr("上电自动回零")
                    on: root.autohome
                    onToggled: root.autohomeToggled(!root.autohome)
                }
                Connections {
                    target: ZStageBridge
                    function onTelemetryChanged() {
                        if (zAutohomeSwitch.on !== ZStageBridge.autohome)
                            zAutohomeSwitch.on = ZStageBridge.autohome
                    }
                }
                Text {
                    Layout.fillWidth: true
                    text: qsTr("⚠ 开着它，板子一上电（约 3 秒后）会自己朝死点撞一次。"
                               + "方向用板子里存的那个。")
                    font.pixelSize: 10
                    color: root.autohome ? Colors.statusDisconnected : Colors.textPlaceholder
                    wrapMode: Text.Wrap
                }
            }

            ThemedButton {
                objectName: "zApplyButton"
                Layout.fillWidth: true
                implicitHeight: 42
                tone: "primary"
                text: qsTr("应用设置")
                onClicked: root.applyRequested()
            }
        }
    }
}
