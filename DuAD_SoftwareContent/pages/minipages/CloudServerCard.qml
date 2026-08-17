import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software
import "../components"

/*
    云服务器连接卡片 — MQTT 通信。
    点击卡片连接/断开，右上角齿轮图标打开 MQTT 设置。
*/
Item {
    id: root

    property bool connected: false
    property bool connecting: false
    property string serverAddress: ""

    signal clicked()
    signal gearClicked()

    implicitWidth: 420
    implicitHeight: 80

    property bool _hovered: false

    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onEntered: _hovered = true
        onExited: _hovered = false
        onClicked: { if (!root.connecting) root.clicked() }
    }

    Rectangle {
        id: cardBg
        anchors.fill: parent
        radius: 12
        color: {
            if (root.connecting)              return Colors.cardDangerBg
            if (root.connected && _hovered)   return Colors.cardDangerHover
            if (root.connected)               return Colors.cardDangerBg
            if (_hovered)                     return Colors.interactiveHover
            return Colors.contentBg
        }

        Behavior on color { ColorAnimation { duration: 200 } }

        RowLayout {
            anchors { fill: parent; margins: 16 }
            spacing: 16

            Rectangle {
                visible: !root.connecting
                Layout.preferredWidth: 48; Layout.preferredHeight: 48
                Layout.alignment: Qt.AlignVCenter
                radius: 10; color: cardBg.color
                IconImage {
                    anchors.centerIn: parent
                    source: "../../images/MQTT-copy.svg"
                    width: 32; height: 32
                }
            }

            ColumnLayout {
                visible: !root.connecting
                spacing: 4
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignVCenter

                Text {
                    text: root.connected ? root.serverAddress : qsTr("云服务器连接")
                    font.pixelSize: 15; font.bold: true
                    color: Colors.textPrimary
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }

                Rectangle {
                    Layout.fillWidth: true; implicitHeight: 1
                    color: Colors.cardBorder
                }

                RowLayout {
                    spacing: 8
                    Rectangle { width: 8; height: 8; radius: 4
                        color: root.connected ? Colors.statusConnected : Colors.statusDisconnected }
                    Text {
                        text: root.connected ? qsTr("已连接") : qsTr("未连接")
                        font.pixelSize: 12
                        color: root.connected ? Colors.statusConnected : Colors.statusDisconnected
                    }
                }
            }
        }

        // 连接中：覆盖层整体居中于卡片（不在 RowLayout 内，否则左侧图标占位让文案偏右）
        ColumnLayout {
            visible: root.connecting
            anchors.centerIn: parent
            spacing: 8

            Text {
                text: qsTr("正在连接云服务器...")
                font.pixelSize: 14
                color: Colors.textSecondary
                horizontalAlignment: Text.AlignHCenter
            }
            BusyIndicator {
                implicitWidth: 24; implicitHeight: 24
                Layout.alignment: Qt.AlignHCenter
                running: true
            }
        }
    }

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
        anchors { horizontalCenter: parent.horizontalCenter; bottom: parent.top; bottomMargin: -6 }
        width: tipText.implicitWidth + 16; height: 26; radius: 6
        color: root.connected ? "#e74c3c" : Colors.textSecondary
        Text {
            id: tipText
            anchors.centerIn: parent
            text: root.connected ? qsTr("点击断开连接") : qsTr("点击连接云服务器")
            font.pixelSize: 11; color: "#ffffff"
        }
    }
}
