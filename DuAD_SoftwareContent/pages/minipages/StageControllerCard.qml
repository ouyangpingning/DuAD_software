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

    signal clicked()
    signal gearClicked()

    implicitWidth: 460
    implicitHeight: 80

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
            anchors { fill: parent; margins: 16 }
            spacing: 16

            Rectangle {
                visible: !root.connecting
                Layout.preferredWidth: 48
                Layout.preferredHeight: 48
                Layout.alignment: Qt.AlignVCenter
                radius: 10
                color: cardBg.color

                IconImage {
                    anchors.centerIn: parent
                    source: "../../images/二轴平台.svg"
                    width: 32
                    height: 32
                }
            }

            ColumnLayout {
                visible: !root.connecting
                spacing: 4
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignVCenter

                Text {
                    text: root.connected
                        ? (root.host + ":" + root.port)
                        : qsTr("二轴相机平台")
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
                text: qsTr("正在连接二轴平台...")
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
