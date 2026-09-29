import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software
import "../components"

/*
    光源调控面板 — 连接光源控制器后显示，4 路亮度独立调节。

    协议（手册四.2，真机实测一致）:
        发送 $L{通道}={值}#   通道 0-3（= 面板通道 1-4），值 0-255
        成功回 +OK，失败回 E1~ER（错误码在 LightBridge 里翻译成中文）

    ⚠ 不要在 QML 里拼指令 —— 串口收发、应答校验、错误码全在 LightBridge 里，
      界面只表达"要设成多少"。
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

    /* 把控制器回读到的通道亮度刷到界面上。
       ⚠ 这里必须用**函数赋值**，不能写成 `sliderValue: LightBridge.channelValues[i]`：
         SliderRow 拖动时是对 sliderValue 做**命令式赋值**，绑定当场被打断，
         之后再改绑定源，滑块手柄不会动 —— 表现就是"界面显示的值和控制器对不上"。 */
    function syncFromDevice(vals) {
        var rows = [slider1, slider2, slider3, slider4]
        if (!vals)
            return
        for (var i = 0; i < rows.length && i < vals.length; ++i)
            rows[i].sliderValue = vals[i]
        trigRow.currentIndex = LightBridge.trigMode
    }

    Component.onCompleted: {
        LightBridge.deviceStateChanged.connect(function() {
            root.syncFromDevice(LightBridge.channelValues)
        })
        if (LightBridge.connected)
            root.syncFromDevice(LightBridge.channelValues)
    }

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
    CardSurface {
        anchors.fill: parent

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
                id: slider1
                objectName: "lightSlider0"
                label: qsTr("光源 1")
                sliderValue: root.light1; from: 0; to: 255
                suffix: ""; decimals: 0
                snapTicks: [0, 32, 64, 96, 128, 160, 192, 224, 255]
                wheelStep: 1
                onSliderValueChanged: root.light1 = sliderValue
                // ⚠ 必须写成 function(value)：直接引用 `value` 是"把信号参数注入
                //   处理函数作用域"的旧写法，Qt 6.11 会报 deprecation 警告，
                //   一旦哪天改成编译型 QML 就再也不注入 → 发下去的会是 undefined。
                onReleased: function(value) { LightBridge.setLightValue(0, value) }
            }
            SliderRow {
                id: slider2
                objectName: "lightSlider1"
                label: qsTr("光源 2")
                sliderValue: root.light2; from: 0; to: 255
                suffix: ""; decimals: 0
                snapTicks: [0, 32, 64, 96, 128, 160, 192, 224, 255]
                wheelStep: 1
                onSliderValueChanged: root.light2 = sliderValue
                onReleased: function(value) { LightBridge.setLightValue(1, value) }
            }
            SliderRow {
                id: slider3
                objectName: "lightSlider2"
                label: qsTr("光源 3")
                sliderValue: root.light3; from: 0; to: 255
                suffix: ""; decimals: 0
                snapTicks: [0, 32, 64, 96, 128, 160, 192, 224, 255]
                wheelStep: 1
                onSliderValueChanged: root.light3 = sliderValue
                onReleased: function(value) { LightBridge.setLightValue(2, value) }
            }
            SliderRow {
                id: slider4
                objectName: "lightSlider3"
                label: qsTr("光源 4")
                sliderValue: root.light4; from: 0; to: 255
                suffix: ""; decimals: 0
                snapTicks: [0, 32, 64, 96, 128, 160, 192, 224, 255]
                wheelStep: 1
                onSliderValueChanged: root.light4 = sliderValue
                onReleased: function(value) { LightBridge.setLightValue(3, value) }
            }

            Rectangle {
                Layout.fillWidth: true; implicitHeight: 1
                color: Colors.cardBorder
            }

            // ── 触发方式（$TR）────────────────────────────────
            // 出厂默认 TR=0（E0L，外部跟随低电平）：灯的亮灭由 TRIG IN 端口电平
            // 决定。触发方式和实际接线不一致时，改亮度也看不出效果 —— 所以必须
            // 能在界面上改，而不是只能靠面板旋钮进菜单。
            ComboRow {
                id: trigRow
                objectName: "trigModeCombo"
                label: qsTr("触发方式")
                model: [0, 1, 2, 3]
                displayFunc: function(key) {
                    if (key === 0) return qsTr("E0L 外部跟随低电平")
                    if (key === 1) return qsTr("E1H 外部跟随高电平")
                    if (key === 2) return qsTr("E2L 外部下降沿触发")
                    return qsTr("E3H 外部上升沿触发")
                }
                currentIndex: LightBridge.trigMode
                onActivated: LightBridge.setTrigMode(model[index])
            }

            Text {
                Layout.fillWidth: true
                text: qsTr("出厂默认 E0L：灯的亮灭还受 TRIG IN 电平控制，触发方式要与实际接线一致。")
                font.pixelSize: 11
                color: Colors.textPlaceholder
                wrapMode: Text.Wrap
            }

            ThemedButton {
                objectName: "lightSaveButton"
                Layout.fillWidth: true
                implicitHeight: 40
                tone: "soft"
                text: qsTr("保存到控制器（掉电保存）")
                onClicked: LightBridge.saveToDevice()
            }

            Rectangle {
                Layout.fillWidth: true; implicitHeight: 1
                color: Colors.cardBorder
            }

            ReadonlyRow {
                objectName: "lightDeviceRow"
                label: qsTr("控制器")
                value: LightBridge.deviceInfo
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
