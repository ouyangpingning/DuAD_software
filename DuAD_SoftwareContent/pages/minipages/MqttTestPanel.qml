import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software
import "../components"

/*
    MQTT 连接测试面板 — 连接后显示。
    可发布测试消息并查看回显。
*/
Item {
    id: root

    property bool expanded: false
    property string testTopic: "duad/test"
    property string testMessage: "Hello DuAD"
    property string logText: ""
    property int qos: 0

    Component.onCompleted: {
        MqttBridge.logMessage.connect(function(line) {
            root.logText = line + "\n" + root.logText
        })
        MqttBridge.messageReceived.connect(function(topic, payload) {
            root.logText = "<<< [" + topic + "] " + payload + "\n" + root.logText
        })
    }

    implicitWidth: 420
    implicitHeight: expanded ? contentLayout.implicitHeight + 32 : 0
    clip: true

    Behavior on implicitHeight { NumberAnimation { duration: 250; easing.type: Easing.InOutCubic } }

    Rectangle {
        anchors.fill: parent
        radius: 12
        color: Colors.contentBg

        ColumnLayout {
            id: contentLayout
            spacing: 10
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 24 }

            Text { text: qsTr("连接测试"); font.pixelSize: 14; font.bold: true; color: Colors.textPrimary }
            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder }

            // ── 发布测试 ──────────────────────────
            SectionHeader { text: qsTr("发布测试消息") }

            InputRow { label: qsTr("Topic"); text: root.testTopic
                onTextEdited: root.testTopic = text }
            InputRow { label: qsTr("消息"); text: root.testMessage
                onTextEdited: root.testMessage = text }

            Button {
                text: qsTr("发送测试消息")
                Layout.alignment: Qt.AlignRight
                implicitHeight: 32
                font.pixelSize: 12

                contentItem: Text {
                    text: parent.text
                    font: parent.font
                    color: Colors.textPrimary
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }

                onClicked: {
                    MqttBridge.publish(root.testTopic, root.testMessage, root.qos)
                }

                background: Rectangle {
                    radius: 4
                    color: parent.hovered ? Colors.interactiveHover : Colors.interactivePressed
                }
            }

            // ── 日志 ──────────────────────────────
            SectionHeader { text: qsTr("消息日志") }

            ScrollView {
                Layout.fillWidth: true
                implicitHeight: 100
                clip: true

                TextArea {
                    id: logArea
                    text: root.logText
                    readOnly: true
                    font.pixelSize: 11
                    color: Colors.textPrimary
                    wrapMode: TextArea.Wrap

                    background: Rectangle {
                        radius: 4; color: Colors.pageBg
                        border { width: 1; color: Colors.cardBorder }
                    }
                }
            }
        }
    }
}
