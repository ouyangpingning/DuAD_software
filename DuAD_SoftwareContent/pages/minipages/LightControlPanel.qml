import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software
import "../components"

/*
    光源调控面板 — 连接光源控制器后显示，4 路亮度独立调节。
    发送格式: $L{通道}={值}#  (通道 0-3, 值 0-255)
*/
Item {
    id: root

    // ============================================================
    // 公有 API
    // ============================================================
    property bool expanded: false

    property real light1: 0
    property real light2: 0
    property real light3: 0
    property real light4: 0
    property string lastError: ""

    // ============================================================
    // 尺寸
    // ============================================================
    implicitWidth: 420
    implicitHeight: expanded ? contentLayout.implicitHeight + 32 : 0
    clip: true

    Behavior on implicitHeight {
        NumberAnimation { duration: 250; easing.type: Easing.InOutCubic }
    }

    // ============================================================
    // 卡片本体
    // ============================================================
    Rectangle {
        anchors.fill: parent
        radius: 12
        color: Colors.contentBg
        border { width: 0; color: Colors.cardBorder }

        ColumnLayout {
            id: contentLayout
            spacing: 10
            anchors {
                left: parent.left; right: parent.right
                top: parent.top
                margins: 24
            }

            Text {
                text: qsTr("光源亮度调节")
                font.pixelSize: 14; font.bold: true
                color: Colors.textPrimary
            }

            Rectangle {
                Layout.fillWidth: true; implicitHeight: 1
                color: Colors.cardBorder
            }

            SliderRow {
                label: qsTr("光源 1")
                sliderValue: root.light1; from: 0; to: 255
                suffix: ""; decimals: 0
                snapTicks: [0, 32, 64, 96, 128, 160, 192, 224, 255]
                wheelStep: 1
                onSliderValueChanged: root.light1 = sliderValue
                onReleased: LightBridge.setLightValue(0, value)
            }
            SliderRow {
                label: qsTr("光源 2")
                sliderValue: root.light2; from: 0; to: 255
                suffix: ""; decimals: 0
                snapTicks: [0, 32, 64, 96, 128, 160, 192, 224, 255]
                wheelStep: 1
                onSliderValueChanged: root.light2 = sliderValue
                onReleased: LightBridge.setLightValue(1, value)
            }
            SliderRow {
                label: qsTr("光源 3")
                sliderValue: root.light3; from: 0; to: 255
                suffix: ""; decimals: 0
                snapTicks: [0, 32, 64, 96, 128, 160, 192, 224, 255]
                wheelStep: 1
                onSliderValueChanged: root.light3 = sliderValue
                onReleased: LightBridge.setLightValue(2, value)
            }
            SliderRow {
                label: qsTr("光源 4")
                sliderValue: root.light4; from: 0; to: 255
                suffix: ""; decimals: 0
                snapTicks: [0, 32, 64, 96, 128, 160, 192, 224, 255]
                wheelStep: 1
                onSliderValueChanged: root.light4 = sliderValue
                onReleased: LightBridge.setLightValue(3, value)
            }

            Rectangle {
                Layout.fillWidth: true; implicitHeight: 1
                color: Colors.cardBorder
            }

            ReadonlyRow {
                label: qsTr("最近指令")
                value: LightBridge.lastCommand
            }
            ReadonlyRow {
                label: qsTr("控制器响应")
                value: LightBridge.lastResponse
            }
        }
    }
}
