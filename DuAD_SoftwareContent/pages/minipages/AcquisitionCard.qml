import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software
import "../components"

/*
    图像采集状态卡片 — 控制相机连续采集启停。
    点击卡片开始/停止采集；右上角齿轮展开采集/保存设置面板（采集前可先设置）。
    采集中卡片变浅红（同 CloudServerCard 已连接态），齿轮在采集中禁用（设置已锁定）。
    TODO: 对接 Python 后端 CameraDevice.gather_start()/gather_stop()；
    相机连接状态需经 AppBridge 跨页共享（当前各页独立模拟）。
*/
Item {
    id: root

    property bool collecting: false
    property int fps: 30
    property bool enabled: true
    property bool cameraConnected: true

    signal clicked()
    signal gearClicked()

    implicitWidth: 420
    implicitHeight: 80

    property bool _hovered: false

    MouseArea {
        anchors.fill: parent
        enabled: root.enabled
        hoverEnabled: root.enabled
        cursorShape: root.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
        onEntered: _hovered = true
        onExited: _hovered = false
        onClicked: root.clicked()
    }

    Rectangle {
        id: cardBg
        anchors.fill: parent
        radius: 12
        color: {
            if (!root.enabled)                return Colors.pageBg
            if (root.collecting && _hovered)  return Colors.cardDangerHover
            if (root.collecting)              return Colors.cardDangerBg
            if (_hovered)                     return Colors.interactiveHover
            return Colors.contentBg
        }

        Behavior on color { ColorAnimation { duration: 200 } }

        RowLayout {
            anchors { fill: parent; margins: 16 }
            spacing: 16

            Rectangle {
                Layout.preferredWidth: 48; Layout.preferredHeight: 48
                Layout.alignment: Qt.AlignVCenter
                radius: 10; color: cardBg.color
                IconImage {
                    anchors.centerIn: parent
                    source: "../../images/pictures.svg"
                    width: 32; height: 32
                }
            }

            ColumnLayout {
                spacing: 4
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignVCenter

                Text {
                    text: root.collecting
                        ? qsTr("相机 @ %1 fps").arg(root.fps)
                        : qsTr("图像采集")
                    font.pixelSize: 15; font.bold: true
                    color: root.enabled ? Colors.textPrimary : Colors.textPlaceholder
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
                        color: !root.cameraConnected ? Colors.statusDisconnected
                            : (root.collecting ? Colors.statusConnected : Colors.statusDisconnected) }
                    Text {
                        text: !root.cameraConnected ? qsTr("相机未连接")
                            : (root.collecting ? qsTr("采集中") : qsTr("未采集"))
                        font.pixelSize: 12
                        color: root.collecting ? Colors.statusConnected : Colors.statusDisconnected
                    }
                }
            }
        }
    }

    // 齿轮 — 展开/收起采集与保存设置面板（采集中禁用，设置已锁定）
    AnimatedRefreshButton {
        z: 1
        anchors { top: parent.top; right: parent.right; topMargin: 6; rightMargin: 6 }
        iconSource: "../../images/settings.svg"
        size: 28
        enabled: !root.collecting
        onClicked: root.gearClicked()
    }

    Rectangle {
        visible: _hovered
        anchors { horizontalCenter: parent.horizontalCenter; bottom: parent.top; bottomMargin: -6 }
        width: tipText.implicitWidth + 16; height: 26; radius: 6
        color: root.collecting ? "#e74c3c" : Colors.textSecondary
        Text {
            id: tipText
            anchors.centerIn: parent
            text: !root.cameraConnected ? qsTr("请先在相机设置页连接相机")
            : (root.collecting ? qsTr("点击停止采集") : qsTr("点击开始采集"))
            font.pixelSize: 11; color: "#ffffff"
        }
    }
}
