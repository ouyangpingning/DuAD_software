import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software
import "../components"

/*
    二轴相机平台卡片 — 点击连接/断开，右上角齿轮展开网络设置。

    结构照抄 LightControllerCard：连接中显示居中覆盖层（不在 RowLayout 里，
    否则左侧图标占位会把文案挤偏）、连接后卡片变微红表示"点一下会断开"、
    悬停给提示。这几点都是既有约定，别改。

    ⚠ 2026-09-27 加了三个**可选**属性（title / connectingText / iconSource）：
      本页现在有两台**不同板子**的平台卡（X/Y 二轴 + Z 轴升降），
      它们只有文案和图标不同。三个属性的默认值与加它们之前**逐字一致**，
      所以 X/Y 那张卡的调用点一个字都不用改（零回归）。
      不要为了"少两个属性"把标题再写死回 qsTr —— 那样 Z 卡片就只能复制一份
      180 行的卡片出来，两份迟早漂移。
*/
Item {
    id: root

    // ============================================================
    // 公有 API
    // ============================================================
    property bool connected: false
    property bool connecting: false
    property string host: ""
    property int port: 3333
    property string subtitle: ""        // 连上后显示的第二行（地址：host:port）
    // 遥测拆成独立属性（2026-09-28）：原来 host/电压/使能/信号被拼成**一个字符串**，
    // 渲染出来全是一样重的灰字，电压和 dBm 跟地址抢注意力。
    // 拆开之后：地址是文字、电压/信号用小图标 + 数字，且窄列里可以整组让位。
    property real voltage: 0
    property int rssi: 0
    // ── 闸门状态（"沉默即正常"）：[{ok, text}] ──────────────────
    // **只显示不 ok 的那些**（红字），正常的项一个像素都不占。
    // 用户 2026-09-28 反馈："状态只在卡片上显示" —— 原来卡片下面还挂着一条
    // 单独的状态行（StatusLine），"未连接"在同一个平台里出现两次；
    // 现在"连没连上 + 能不能动"同在这一行里，列里不用再多一行。
    property var gates: []
    // 运动中（原来挂在独立状态行的 trailing 上；状态行并进卡片后要把它接回来，
    // 否则"平台正在动"这件事在界面上就完全看不见了）
    property bool moving: false

    // 把"不 ok"的闸门合成一行（空串 = 全正常，一个像素都不占）
    readonly property string _badGates: {
        var out = []
        for (var i = 0; i < gates.length; i++) {
            var g = gates[i]
            if (g && !g.ok) out.push("⚠ " + g.text)
        }
        return out.join("  ·  ")
    }

    // ── 可选的文案/图标覆写（默认值 = 加这三个属性之前的行为）──
    property string title: qsTr("二轴相机平台")
    property string connectingText: qsTr("正在连接二轴平台...")
    property url iconSource: "../../images/二轴平台.svg"

    // ── 紧凑模式（2026-09-28 三列版面）：左右窄列（~200px）用的。
    //   去掉 48px 图标块、边距 16→10 —— 那张卡在窄列里只需要"标题 + 状态行"。
    //   默认 false = 加这个属性之前的逐像素行为（别的页面不受影响）。
    property bool compact: false

    signal clicked()
    signal gearClicked()

    implicitWidth: compact ? 200 : 460
    // 68（原 64）：紧凑卡里多了一个 26px 图标 chip，留出它对行的呼吸位。
    // ⚠ 侧列变高**不会**影响首屏硬约束（tests/test_stage_page.py 12a2 量的是中列的
    //   点动/急停），RowLayout 的高度取三列的最大值，而中列一直更高。
    implicitHeight: compact ? 68 : 80

    property bool _hovered: false

    // 紧凑模式是否画图标 chip：窄列（<180）里 chip 会把"已连接 · IP · 电压"挤到
    // 几乎全被 elide —— 那时文字优先，图标让位。
    readonly property bool _showChip: root.compact && root.width >= 180

    // 电压/信号图标只在卡片够宽时出现：窄列里它们会把地址挤没，
    // 而"能不能动"的判断从来不靠电压和信号强度。
    readonly property bool _showTelemetry: root.connected && root.width >= 300

    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onEntered: _hovered = true
        onExited: _hovered = false
        onClicked: if (!root.connecting) root.clicked()
    }

    CardSurface {
        id: cardBg
        anchors.fill: parent
        color: {
            if (root.connecting)            return Colors.cardDangerBg
            if (root.connected && _hovered) return Colors.cardDangerHover
            if (root.connected)             return Colors.cardDangerBg
            if (_hovered)                   return Colors.interactiveHover
            return Colors.cardBg
        }
        // 已连接用**淡红**描边（不是正红）：卡片本来就变微红了，再围一圈正红
        // 看着像故障告警，而"已连接"是好状态（见 Colors.cardDangerBorder 注释）。
        borderColor: root.connected ? Colors.cardDangerBorder : Colors.cardBorderStrong

        RowLayout {
            anchors { fill: parent; margins: root.compact ? 10 : 16 }
            spacing: root.compact ? 8 : 16

            // ── 图标 chip（紧凑模式）──────────────────────────────
            // 2026-09-28 美化：紧凑模式原来写的是 `visible: !connecting && !compact`，
            // 于是三列版面里**两张平台卡一个图标都没有**（而侧边导航栏里那对图标
            // 明明很好认）。现在窄列也给一个小 chip：软强调底 + 18px 图标。
            Rectangle {
                visible: !root.connecting && root._showChip
                Layout.preferredWidth: 26
                Layout.preferredHeight: 26
                Layout.alignment: Qt.AlignVCenter
                radius: 7
                color: Colors.accentSoft

                IconImage {
                    anchors.centerIn: parent
                    source: root.iconSource
                    width: 17
                    height: 17
                }
            }

            Rectangle {
                visible: !root.connecting && !root.compact
                Layout.preferredWidth: 48
                Layout.preferredHeight: 48
                Layout.alignment: Qt.AlignVCenter
                radius: 10
                color: cardBg.color

                IconImage {
                    anchors.centerIn: parent
                    source: root.iconSource
                    width: 32
                    height: 32
                }
            }

            ColumnLayout {
                visible: !root.connecting
                spacing: 4
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignVCenter

                // ⚠ 标题**永远**是平台名（2026-09-27 改）：两台板子的卡片现在并排放在
                //   页顶，连上以后如果标题变成 `192.168.1.42:3333`，两张卡就只剩两串
                //   地址 —— 而两块板子**用的是同一个端口 3333**，光看地址分不出
                //   哪张是 X/Y、哪张是 Z。地址下沉到副标题第一段（由调用方拼）。
                Text {
                    text: root.title
                    font.pixelSize: 15
                    font.bold: true
                    color: Colors.textPrimary
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 1
                    color: Colors.cardBorder
                }

                // ── 状态行（2026-09-28 分层）──────────────────────
                // 改之前：`状态 ● 已连接 · 127.0.0.1:46193 · 24.2V · 已使能 · -58dBm`
                // 全是 12px 同色 —— "已连接"这个最该跳出来的判据和 dBm 一样重。
                // 改之后：**圆点 + 加粗状态词**（最高对比度）→ 红色闸门项 → 灰字遥测。
                // "状态"两个字去掉了：圆点和状态词已经把意思说全，那两个字只是占宽度
                // （窄列 160px 时尤其浪费）。
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6

                    Rectangle {
                        Layout.alignment: Qt.AlignVCenter
                        width: 8; height: 8; radius: 4
                        color: root.connected ? Colors.statusConnected : Colors.statusDisconnected
                    }
                    Text {
                        text: root.connected ? qsTr("已连接") : qsTr("未连接")
                        font.pixelSize: 12
                        font.bold: true
                        color: root.connected ? Colors.statusConnected : Colors.statusDisconnected
                    }
                    // 闸门：只画"不 ok"的红项（无基准 / 未设限位 / 未使能……）。
                    // ⚠ 合成**一个** Text 而不是 Repeater 出多个：窄列里（默认窗口
                    //   页面 784 → 侧列 188 → 内容区 ~148px）多个不可收缩的 Text
                    //   会把整行顶出卡片。一个 Text + elide + fillWidth 能收缩 ✓
                    Text {
                        visible: root._badGates.length > 0
                        text: root._badGates
                        font.pixelSize: 12
                        font.bold: true
                        color: Colors.statusDisconnected
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }

                    Text {
                        visible: root.moving
                        text: qsTr("运动中…")
                        font.pixelSize: 11
                        color: Colors.interactivePressed
                    }

                    // 地址放在最后：空间不够时它先被裁（闸门信息更重要）。
                    // 11px + textPlaceholder：遥测是"参考信息"，不该和状态词抢注意力。
                    Text {
                        visible: root.connected && root.subtitle.length > 0
                        text: root.subtitle
                        font.pixelSize: 11
                        color: Colors.textPlaceholder
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }

                    // 电压 / 信号：小图标 + 数字（用户新给的 闪电/信号格 图标）。
                    // 比 "24.2V · -58dBm" 这种纯文字更快扫读，也短得多。
                    IconImage {
                        visible: root._showTelemetry
                        Layout.alignment: Qt.AlignVCenter
                        source: "../../images/闪电.svg"
                        width: 11; height: 11
                    }
                    Text {
                        visible: root._showTelemetry
                        text: root.voltage.toFixed(1) + "V"
                        font.pixelSize: 11
                        color: Colors.textPlaceholder
                    }
                    IconImage {
                        visible: root._showTelemetry
                        Layout.alignment: Qt.AlignVCenter
                        source: "../../images/信号格.svg"
                        width: 11; height: 11
                    }
                    Text {
                        visible: root._showTelemetry
                        text: root.rssi + "dBm"
                        font.pixelSize: 11
                        color: Colors.textPlaceholder
                    }
                }
            }
        }

        // 连接中：覆盖层整体居中（不在 RowLayout 内）
        ColumnLayout {
            visible: root.connecting
            anchors.centerIn: parent
            spacing: 8

            Text {
                text: root.connectingText
                font.pixelSize: 14
                color: Colors.textSecondary
                horizontalAlignment: Text.AlignHCenter
            }
            BusyIndicator {
                implicitWidth: 24
                implicitHeight: 24
                Layout.alignment: Qt.AlignHCenter
                running: true
            }
        }
    }

    // 齿轮：连接后冻结（网络参数不允许热改）
    AnimatedRefreshButton {
        z: 1
        anchors { top: parent.top; right: parent.right; topMargin: 6; rightMargin: 6 }
        iconSource: "../../images/settings.svg"
        size: 28
        enabled: !root.connected
        onClicked: root.gearClicked()
    }

    Rectangle {
        visible: _hovered && !root.connecting
        anchors {
            horizontalCenter: parent.horizontalCenter
            bottom: parent.top
            bottomMargin: -6
        }
        width: tipText.implicitWidth + 16
        height: 26
        radius: 6
        color: root.connected ? Colors.statusDisconnected : Colors.textSecondary
        z: 2

        Text {
            id: tipText
            anchors.centerIn: parent
            text: root.connected ? qsTr("点击断开连接") : qsTr("点击连接平台")
            font.pixelSize: 11
            color: Colors.textOnAccent
        }
    }
}
