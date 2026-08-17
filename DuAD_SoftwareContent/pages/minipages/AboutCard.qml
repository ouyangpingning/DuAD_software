import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software
import "../components"

/*
    关于卡片。
*/
Item {
    id: root

    implicitWidth: 420
    implicitHeight: contentLayout.implicitHeight + 32

    Rectangle {
        anchors.fill: parent
        radius: 12
        color: Colors.contentBg
        border { width: 0; color: Colors.cardBorder }

        ColumnLayout {
            id: contentLayout
            spacing: 10
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 24 }

            Text { text: qsTr("其他"); font.pixelSize: 14; font.bold: true; color: Colors.textPrimary }
            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder }

            ColumnLayout {
                spacing: 6
                Layout.alignment: Qt.AlignHCenter

                Text {
                    text: "𝒟𝓊𝒜𝒟"
                    font.pixelSize: 28; font.bold: true
                    color: Colors.textPrimary
                    Layout.alignment: Qt.AlignHCenter
                }
                Text {
                    text: qsTr("融合 Dinov2 与双分支训练架构的工业异常检测")
                    font.pixelSize: 12; color: Colors.textSecondary
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignHCenter
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap          // 长文本自动换行
                }
                Text {
                    text: "Version 1.0.0"
                    font.pixelSize: 11; color: Colors.textPlaceholder
                    Layout.alignment: Qt.AlignHCenter
                }
                Rectangle { Layout.preferredHeight: 8; Layout.fillWidth: true; color: "transparent" }
                Text {
                    text: "© 2025 DuAD. All rights reserved."
                    font.pixelSize: 11; color: Colors.textPlaceholder
                    Layout.alignment: Qt.AlignHCenter
                }

                // 使用说明 — 随时重开启动时的说明页
                Button {
                    text: qsTr("使用说明")
                    Layout.alignment: Qt.AlignHCenter
                    Layout.topMargin: 6
                    implicitHeight: 30
                    font.pixelSize: 12

                    contentItem: Text {
                        text: parent.text
                        font: parent.font
                        color: Colors.textPrimary
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }

                    // 经 AppBridge 信号打开（.ui.qml 不能加逻辑，信号链路过长）。
                    // 注意：QML 调用 Python 信号直接函数调用，不能用 .emit()
                    onClicked: AppBridge.helpRequested()

                    background: Rectangle {
                        radius: 4
                        color: parent.hovered ? Colors.interactiveHover : Colors.interactivePressed
                    }
                }
            }
        }
    }
}
