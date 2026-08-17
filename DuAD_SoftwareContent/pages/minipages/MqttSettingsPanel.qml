import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software
import "../components"

/*
    MQTT 通讯设置面板 — 点击 CloudServerCard 齿轮图标后展开。
*/
Item {
    id: root

    property bool expanded: false

    property string serverAddress: "broker.emqx.io"
    property string port:          "1883"
    property string username:      ""
    property string password:      ""
    property string qos:           "0"
    property int    keepAlive:     60
    property bool   sslEnabled:    false

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
            spacing: 12
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 24 }

            Text { text: qsTr("MQTT 通讯设置"); font.pixelSize: 14; font.bold: true; color: Colors.textPrimary }
            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder }

            InputRow { label: qsTr("服务器地址"); text: root.serverAddress; placeholderText: "broker.emqx.io"
                onTextEdited: root.serverAddress = text }

            InputRow { label: qsTr("端口号"); text: root.port; placeholderText: "1883"
                onTextEdited: root.port = text }

            InputRow { label: qsTr("用户名"); text: root.username; placeholderText: "(选填)"
                onTextEdited: root.username = text }

            InputRow { label: qsTr("密码"); text: root.password; placeholderText: "(选填)"; password: true
                onTextEdited: root.password = text }

            SwitchRow { label: qsTr("TLS/SSL"); on: root.sslEnabled
                onToggled: root.sslEnabled = on }

            ComboRow { label: qsTr("QoS 等级"); model: ["0", "1", "2"]
                currentIndex: parseInt(root.qos)
                onActivated: root.qos = model[index] }

            SliderRow { label: qsTr("心跳间隔"); sliderValue: root.keepAlive; from: 10; to: 300
                suffix: " s"; decimals: 0
                snapTicks: [10, 30, 60, 120, 180, 240, 300]
                wheelStep: 5
                onSliderValueChanged: root.keepAlive = sliderValue }
        }
    }
}
