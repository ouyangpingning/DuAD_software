import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software
import "../components"

/*
    Z 轴设置面板（默认折叠）—— 网络、速度、软限位、上电自动回零。

    ⚠ Z 轴是**另一块板子、另一个 IP**，所以这里自带一套地址/端口/口令，
      与 X/Y 那张卡的设置**互不影响**（两张卡可以同时连着两台板子）。

    三条约定（都是本项目踩出来的）：

    1. **量程从桥里读**（`ZStageBridge.uiMaxRpm` / `uiMaxAcc`），不许在 QML 里写数字。
       X/Y 那边就踩过：`UI_MAX_RPM` 在 bridge 里声明得好好的却没人引用，
       滑块硬编码 1200 —— 改常量界面纹丝不动，而且**静默**
       （AGENTS.md 第 19 条：同一个数字出现在两处迟早漂移）。

    2. **`fieldHost/fieldPort/fieldToken` 是"输入框里的当前内容"**，
       不是桥里已保存的值。页面点卡片连接时必须用这三个 ——
       否则"改了 IP 但没点应用就点连接"会连旧地址，表现成"我明明改了却没用"。

    3. **上电自动回零必须写明它的代价**：开着的话板子一上电（约 3 秒后）
       就会自己朝机械死点撞一次 —— 用户不在场时那就是"平台自己动了"。
*/
Item {
    id: root
    objectName: "zSetupPanel"

    property bool expanded: false
    // 同 StageSetupPanel：住在折叠节里时标题由折叠头负责，自己不重复画
    property bool showTitle: true

    // 网络
    property string host: ""
    property int port: 3333
    property string token: ""

    // 速度 / 加减速
    property int rpm: 300
    property int acc: 100

    // 软限位（mm，相对基准零点）
    property real limitLo: 0
    property real limitHi: 250

    // 固件**实际**在用的软限位（显示用，来自 json）
    property real firmwareLo: 0
    property real firmwareHi: 250
    property bool limitsSet: false

    signal applyRequested()
    // 拖动滑块松手就发（不必等「应用设置」）—— 用户拖动时期待的就是立即生效
    signal speedChanged(int rpm, int acc)

    // ============================================================
    // 输入框里的**当前内容**（给页面点卡片连接时用，见文件头第 2 条）
    // ============================================================
    readonly property string fieldHost: hostRow.text.trim()
    readonly property string fieldToken: tokenRow.text.trim()
    readonly property int fieldPort: {
        var p = parseInt(portRow.text)
        return isNaN(p) ? 3333 : p
    }
    readonly property bool fieldsFilled: fieldHost.length > 0 && fieldToken.length > 0

    // ⚠ 软限位与速度的"当前界面值"也必须由本面板**显式暴露**：
    //   页面（StagePage.qml）拿不到本文件里的 id（`loRow` / `rpmRow` 这些只在
    //   本文件可见），直接写 `parseFloat(loRow.text)` 会在点击时抛 ReferenceError ——
    //   而 QML 的信号处理器出错**不会**弹窗、也不会让页面加载失败，
    //   表现就是"点了应用设置没反应"（本项目最忌讳的那类）。
    //   X/Y 那套的 onApplyRequested 里就是这样写的，见报告。
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

    implicitWidth: 460
    implicitHeight: expanded ? contentLayout.implicitHeight + 32 : 0
    clip: true

    Behavior on implicitHeight {
        NumberAnimation { duration: 250; easing.type: Easing.InOutCubic }
    }

    // 卡片底板：白底 + 描边 + 硬阴影（与连接卡/手动控制卡同一套，见 CardSurface.qml）
    CardSurface {
        anchors.fill: parent

        ColumnLayout {
            id: contentLayout
            objectName: "zSetupBody"
            spacing: 10
            // 12（原 24）：面板住在 ~200px 的窄列里（三列版面），同 StageSetupPanel
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
                Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder
            }

            // ── 网络（另一块板子）──────────────────────────
            SectionHeader { text: qsTr("网络（Z 轴这块板子）") }

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
                text: qsTr("三个值都在 Z 轴板子的 USB 控制台上敲 net 就能看到，板子会直接打印出来。")
                font.pixelSize: 11
                color: Colors.textPlaceholder
                wrapMode: Text.Wrap
            }

            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder }

            // ── 速度 ────────────────────────────────────────
            SectionHeader { text: qsTr("速度") }

            SliderRow {
                id: rpmRow
                objectName: "zRpmSliderRow"
                label: qsTr("转速")
                // ⚠ 量程来自桥（见文件头第 1 条）；固件上限 1200rpm = 8mm 导程上 160mm/s
                from: 1; to: ZStageBridge.uiMaxRpm
                sliderValue: root.rpm
                suffix: "rpm"; decimals: 0
                snapTicks: [1, 60, 150, 300, 600, ZStageBridge.uiMaxRpm]
                wheelStep: 10
                onReleased: root.speedChanged(rpmRow.sliderValue, accRow.sliderValue)
            }

            SliderRow {
                id: accRow
                objectName: "zAccSliderRow"
                label: qsTr("加减速")
                // ⚠ 下限是 1 不是 0：手册原文「数值越大加减速度越大，**0 表示直接启动**」——
                //   0 = 没有斜坡的硬起步，对双丝杠刚性平台是最伤机械的一种走法。
                from: ZStageBridge.uiMinAcc; to: ZStageBridge.uiMaxAcc
                sliderValue: root.acc
                suffix: ""; decimals: 0
                logScale: true
                snapTicks: [1, 2, 3, 4, 5, 7, 9, 12, 16, 21, 28, 37, 50, 67, 90, 120, 160, 200]
                wheelStep: accRow.sliderValue <= 20 ? 1 : (accRow.sliderValue <= 60 ? 2 : 5)
                onReleased: root.speedChanged(rpmRow.sliderValue, accRow.sliderValue)
            }

            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder }

            // ── 软限位 ──────────────────────────────────────
            SectionHeader { text: qsTr("软限位（mm，相对基准零点）") }

            Text {
                Layout.fillWidth: true
                text: qsTr("必须按实际行程量准后填写：固件对绝对移动是 fail-closed 的 —— "
                           + "没设软限位就一律拒绝移动；填大了则会在撞到机械限位前不刹车。"
                           + "本机构械行程 250mm。")
                font.pixelSize: 11
                color: Colors.textPlaceholder
                wrapMode: Text.Wrap
            }

            // 竖排（原左右两列）：窄列里一行放不下两个 InputRow（每个最小 152）
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

            // 固件**实际**在用的窗口（和上面输入框里的可以不同：输入框是"待下发"的值）
            Text {
                Layout.fillWidth: true
                visible: root.limitsSet
                text: qsTr("板子当前生效：%1 ~ %2 mm")
                      .arg(root.firmwareLo.toFixed(2)).arg(root.firmwareHi.toFixed(2))
                font.pixelSize: 10
                color: Colors.textPlaceholder
                wrapMode: Text.Wrap
            }

            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder }

            // ⚠ 「上电自动回零」开关 2026-09-28 从界面删掉（用户：只留最常用的操作）：
            //   开着它板子一上电就自己朝死点撞一次，属于"设一次就不动"的板子行为 ——
            //   改在控制台做（`zauto on` / `hauto on`），或用页面协议框的自定义命令框。
            //   桥的 setAutohome()/autohome 属性保留（主机侧测试仍在跑），只是没有界面入口。

            // 样式走 ThemedButton（tone: soft）：原来没写 background，用的是 Fusion 默认灰
            ThemedButton {
                objectName: "zApplyButton"
                Layout.fillWidth: true
                implicitHeight: 42
                tone: "soft"
                text: qsTr("应用设置")
                onClicked: root.applyRequested()
            }

        }
    }
}
