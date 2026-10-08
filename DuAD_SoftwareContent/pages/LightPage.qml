import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software
import "components"
import "minipages"

Item {
    id: root

    // ============================================================
    // 状态 — 全部来自 LightBridge（CH340 串口光源控制器）
    // ============================================================
    readonly property bool _connected: LightBridge.connected
    property bool _connecting: false
    property bool _serialExpanded: false
    property var _ports: []
    property string _lastError: ""

    Component.onCompleted: {
        LightBridge.portsChanged.connect(function(ports) {
            root._ports = ports
            // ⚠ 自动选口用 LightBridge.defaultPort（优先 ttyUSB*），不要用 ports[0]：
            //   Linux 上 comports() 会把主板 ttyS0~31 一起报出来，排序后 ports[0]
            //   就是 /dev/ttyS0 —— 自动选中它，点连接必然失败。
            if (ports.length > 0
                && ports.indexOf(serialPanel.portName) < 0) {
                serialPanel.portName = LightBridge.pickDefaultPort(ports)
            }
            console.log("[LightPage] 串口列表:", ports, "→ 选中", serialPanel.portName)
        })
        LightBridge.serialConnected.connect(function() {
            root._connecting = false
            root._serialExpanded = false
            root._lastError = ""
            console.log("[LightPage] 光源控制器连接成功")
        })
        LightBridge.serialDisconnected.connect(function() {
            root._connecting = false
            console.log("[LightPage] 光源控制器已断开")
        })
        LightBridge.serialError.connect(function(msg) {
            root._connecting = false
            root._lastError = msg
            console.log("[LightPage] 光源控制器错误:", msg)
        })
    }

    Rectangle {
        anchors.fill: parent
        color: Colors.pageBg

        Flickable {
            anchors.fill: parent
            contentWidth: width
            contentHeight: contentColumn.implicitHeight + 40
            clip: true
            boundsBehavior: Flickable.StopAtBounds

            ColumnLayout {
                id: contentColumn
                // 自适应列宽（2026-10-08 评审）：原来写死 420，1280 宽的窗口里两侧各空 ~400px；
                // 封顶 640 —— 再宽表单行就太散（标签与输入框隔得太远）
                width: Math.min(640, Math.max(360, parent.width - 80))
                spacing: 10
                x: Math.max(0, (parent.width - width) / 2)
                y: 24

                // 标题行 — 与 CameraPage 保持一致的布局
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    Text {
                        text: qsTr("光源设置")
                        font.pixelSize: 18
                        font.bold: true
                        color: Colors.textPrimary
                    }
                }

                // 副标题：一句话说明这页做什么（文案复用 HelpDialog 已翻译的那句，不新增词条）
                Text {
                    Layout.fillWidth: true
                    Layout.topMargin: -6
                    Layout.bottomMargin: 4
                    text: qsTr("连接光源控制器，调节 4 路光源亮度")
                    font.pixelSize: 12
                    color: Colors.textSecondary
                    wrapMode: Text.WordWrap
                }

                LightControllerCard {
                    id: lightCard
                    Layout.fillWidth: true
                    height: 80
                    connected: _connected
                    connecting: _connecting
                    portName: serialPanel.portName
                    baudRate: serialPanel.baudRate

                    onClicked: {
                        if (_connecting) return
                        if (!_connected) {
                            console.log("[LightPage] 正在连接光源控制器...",
                                        serialPanel.portName, serialPanel.baudRate)
                            _connecting = true
                            _lastError = ""
                            var ok = LightBridge.connectSerial(
                                serialPanel.portName,
                                parseInt(serialPanel.baudRate),
                                serialPanel.dataBits,
                                serialPanel.stopBits,
                                serialPanel.parity)
                            if (!ok)
                                _connecting = false
                        } else {
                            console.log("[LightPage] 断开光源控制器")
                            LightBridge.disconnectSerial()
                        }
                    }

                    onGearClicked: {
                        _serialExpanded = !_serialExpanded
                    }
                }

                // 串口错误提示
                Text {
                    visible: root._lastError.length > 0
                    Layout.fillWidth: true
                    text: root._lastError
                    font.pixelSize: 12
                    color: Colors.statusDisconnected
                    wrapMode: Text.Wrap
                }

                SerialSettingsPanel {
                    id: serialPanel
                    Layout.fillWidth: true
                    expanded: _serialExpanded
                    ports: root._ports
                }

                LightControlPanel {
                    id: lightPanel
                    Layout.fillWidth: true
                    expanded: _connected
                }
            }
        }
    }
}
