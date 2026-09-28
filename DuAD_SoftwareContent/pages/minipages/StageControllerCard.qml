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
    property string subtitle: ""        // 连上后显示的第二行（IP/信号等）
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
    implicitHeight: compact ? 64 : 80

    property bool _hovered: false

    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onEntered: _hovered = true
        onExited: _hovered = false
        onClicked: if (!root.connecting) root.clicked()
    }

    Rectangle {
        id: cardBg
        anchors.fill: parent
        radius: 12
        color: {
            if (root.connecting)            return Colors.cardDangerBg
            if (root.connected && _hovered) return Colors.cardDangerHover
            if (root.connected)             return Colors.cardDangerBg
            if (_hovered)                   return Colors.interactiveHover
            return Colors.contentBg
        }

        Behavior on color { ColorAnimation { duration: 200 } }

        RowLayout {
            anchors { fill: parent; margins: root.compact ? 10 : 16 }
            spacing: root.compact ? 8 : 16

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

                RowLayout {
                    spacing: 8
                    Text {
                        text: qsTr("状态")
                        font.pixelSize: 12
                        color: Colors.textSecondary
                    }
                    Rectangle {
                        width: 8; height: 8; radius: 4
                        color: root.connected ? Colors.statusConnected : Colors.statusDisconnected
                    }
                    Text {
                        text: root.connected ? qsTr("已连接") : qsTr("未连接")
                        font.pixelSize: 12
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
                        text: "· " + qsTr("运动中…")
                        font.pixelSize: 12
                        color: Colors.interactivePressed
                    }

                    // 地址/电压/信号放在最后：空间不够时它先被裁（闸门信息更重要）
                    Text {
                        visible: root.connected && root.subtitle.length > 0
                        text: "· " + root.subtitle
                        font.pixelSize: 12
                        color: Colors.textSecondary
                        elide: Text.ElideRight
                        Layout.fillWidth: true
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
            color: "#ffffff"
        }
    }
}
