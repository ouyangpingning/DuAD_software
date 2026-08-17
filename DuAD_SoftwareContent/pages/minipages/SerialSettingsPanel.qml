import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software
import "../components"

/*
    串口通讯设置面板 — 点击 LightControllerCard 的齿轮图标后展开。
*/
Item {
    id: root

    // ============================================================
    // 公有 API
    // ============================================================
    property bool expanded: false

    // 串口参数（ports 由 LightBridge 每 2s 扫描结果注入）
    property var ports: []
    property string portName:      "/dev/ttyUSB0"
    property string baudRate:      "9600"
    property string dataBits:      "8"
    property string stopBits:      "1"
    property string parity:        "NONE"

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
            spacing: 12
            anchors {
                left: parent.left; right: parent.right
                top: parent.top
                margins: 24
            }

            Text {
                text: qsTr("串口通讯设置")
                font.pixelSize: 14; font.bold: true
                color: Colors.textPrimary
            }

            Rectangle {
                Layout.fillWidth: true; implicitHeight: 1
                color: Colors.cardBorder
            }

            ComboRow {
                label: qsTr("串口号")
                model: root.ports.length > 0
                    ? root.ports : ["/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyCH341USB0"]
                currentIndex: {
                    var arr = model
                    var idx = arr.indexOf(root.portName)
                    return idx >= 0 ? idx : 0
                }
                onActivated: root.portName = model[index]
            }

            ComboRow {
                label: qsTr("波特率")
                model: ["2400", "4800", "9600", "19200", "38400", "57600", "115200"]
                currentIndex: {
                    var arr = ["2400", "4800", "9600", "19200", "38400", "57600", "115200"]
                    return Math.max(0, arr.indexOf(root.baudRate))
                }
                onActivated: root.baudRate = model[index]
            }

            ComboRow {
                label: qsTr("数据位")
                model: ["5", "6", "7", "8"]
                currentIndex: parseInt(root.dataBits) - 5
                onActivated: root.dataBits = model[index]
            }

            ComboRow {
                label: qsTr("停止位")
                model: ["1", "1.5", "2"]
                currentIndex: root.stopBits === "1" ? 0 : (root.stopBits === "1.5" ? 1 : 2)
                onActivated: root.stopBits = model[index]
            }

            ComboRow {
                label: qsTr("校验位")
                model: ["NONE", "EVEN", "ODD", "MARK", "SPACE"]
                currentIndex: {
                    var arr = ["NONE", "EVEN", "ODD", "MARK", "SPACE"]
                    return Math.max(0, arr.indexOf(root.parity))
                }
                onActivated: root.parity = model[index]
            }
        }
    }
}
