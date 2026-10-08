import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software
import "components"
import "minipages"

Item {
    id: root

    // ============================================================
    // 状态 — 来自 MqttBridge（paho-mqtt 后台线程）
    // ============================================================
    readonly property bool _connected: MqttBridge.connected
    property bool _connecting: false
    property bool _settingsExpanded: false
    property string _lastError: ""

    Component.onCompleted: {
        MqttBridge.mqttConnected.connect(function() {
            root._connecting = false
            root._settingsExpanded = false
            root._lastError = ""
            // 连接后订阅测试 Topic，发布的消息可由 Broker 回显
            MqttBridge.subscribe(testPanel.testTopic, parseInt(mqttPanel.qos))
            console.log("[CommPage] 云服务器连接成功")
        })
        MqttBridge.mqttDisconnected.connect(function() {
            root._connecting = false
            console.log("[CommPage] 云服务器已断开")
        })
        MqttBridge.mqttError.connect(function(msg) {
            root._connecting = false
            root._lastError = msg
            console.log("[CommPage] 云服务器错误:", msg)
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

                // 标题行
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    Text {
                        text: qsTr("通讯设置")
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
                    text: qsTr("配置串口与 MQTT 参数，连接云服务器并发送测试消息")
                    font.pixelSize: 12
                    color: Colors.textSecondary
                    wrapMode: Text.WordWrap
                }

                CloudServerCard {
                    id: serverCard
                    Layout.fillWidth: true
                    height: 80
                    connected: _connected
                    connecting: _connecting
                    serverAddress: mqttPanel.serverAddress + ":" + mqttPanel.port

                    onClicked: {
                        if (_connecting) return
                        if (!_connected) {
                            console.log("[CommPage] 正在连接云服务器...",
                                        mqttPanel.serverAddress, mqttPanel.port,
                                        "TLS:", mqttPanel.sslEnabled)
                            _connecting = true
                            _lastError = ""
                            MqttBridge.connectServer(
                                mqttPanel.serverAddress,
                                parseInt(mqttPanel.port),
                                mqttPanel.username,
                                mqttPanel.password,
                                mqttPanel.keepAlive,
                                mqttPanel.sslEnabled)
                        } else {
                            console.log("[CommPage] 断开云服务器")
                            MqttBridge.disconnectServer()
                        }
                    }

                    onGearClicked: {
                        _settingsExpanded = !_settingsExpanded
                    }
                }

                // 连接错误提示
                Text {
                    visible: root._lastError.length > 0
                    Layout.fillWidth: true
                    text: root._lastError
                    font.pixelSize: 12
                    color: Colors.statusDisconnected
                    wrapMode: Text.Wrap
                }

                MqttSettingsPanel {
                    id: mqttPanel
                    Layout.fillWidth: true
                    expanded: _settingsExpanded
                }

                MqttTestPanel {
                    id: testPanel
                    Layout.fillWidth: true
                    expanded: _connected
                    qos: parseInt(mqttPanel.qos)
                }
            }
        }
    }
}
