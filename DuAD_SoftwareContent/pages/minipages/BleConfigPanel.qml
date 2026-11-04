import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software
import "../components"

/*
    BLE 配网面板（二轴 / Z 轴共用）。

    只做一件事：通过 BLE 把 Wi-Fi 名称/密码交给板子，板子自己连路由器并持久化。
    运动控制仍然走 TCP —— 这里不出现任何 move/stop 按钮。

    ⚠ 板子一直慢速广播，所以“搜索板卡”随时可用；搜到一台会自动连接，
      多台时在「板卡」下拉里选。口令取本面板外面的「口令」输入框（USB 控制台敲 net 可见），
      因为 BLE 侧也要先认证才允许改 Wi-Fi。

    折叠：标题是 CollapsibleHeader；`expanded=false` 时只留标题，
    由「平台设置」面板决定默认展开还是收起。
*/
Item {
    id: root
    objectName: "blePanel"

    property var bridge: null
    property string token: ""
    property string roleLabel: ""
    property bool expanded: true

    implicitWidth: 460
    implicitHeight: layout.implicitHeight

    function _connectDevice(index) {
        if (!root.bridge || index < 0 || index >= root.bridge.bleDevices.length)
            return
        var d = root.bridge.bleDevices[index]
        root.bridge.bleSetToken(root.token)
        root.bridge.bleConnect(d.address)
    }

    function _pickSsid(index) {
        if (!root.bridge || index < 0 || index >= root.bridge.bleNetSsids.length)
            return
        ssidField.text = root.bridge.bleNetSsids[index]
    }

    function _statusColor() {
        if (!root.bridge)
            return Colors.textPlaceholder
        if (root.bridge.bleState === "connected")
            return Colors.statusConnected
        if (root.bridge.bleState === "failed")
            return Colors.statusDisconnected
        if (root.bridge.bleBusy)
            return Colors.accent
        return Colors.textPlaceholder
    }

    ColumnLayout {
        id: layout
        width: parent.width
        spacing: 8

        // 扫描结果回来时，把第一项（默认选中项）直接填进「Wi-Fi 名称」。
        // ComboBox 自动选中第一项不会发 activated，所以不能只靠 onActivated。
        Connections {
            target: root.bridge
            function onBleNetsChanged() {
                if (!root.bridge || root.bridge.bleNetSsids.length === 0)
                    return
                netCombo.currentIndex = 0
                ssidField.text = root.bridge.bleNetSsids[0]
            }
        }

        CollapsibleHeader {
            Layout.fillWidth: true
            title: qsTr("Wi-Fi 连接（蓝牙配网）")
            expanded: root.expanded
            onToggled: root.expanded = !root.expanded
        }

        ColumnLayout {
            id: body
            Layout.fillWidth: true
            visible: root.expanded
            spacing: 8

            RowLayout {
                Layout.fillWidth: true
                spacing: 6

                Rectangle {
                    Layout.alignment: Qt.AlignVCenter
                    implicitWidth: 8
                    implicitHeight: 8
                    radius: 4
                    color: root._statusColor()
                }
                Text {
                    Layout.fillWidth: true
                    text: root.bridge ? root.bridge.bleStateText : ""
                    font.pixelSize: 12
                    color: Colors.textPrimary
                    wrapMode: Text.Wrap
                }
            }

            Text {
                Layout.fillWidth: true
                visible: root.bridge && root.bridge.bleState === "connected"
                text: qsTr("当前：%1    IP：%2    信号 %3 dBm")
                      .arg(root.bridge ? root.bridge.bleSavedSsid : "")
                      .arg(root.bridge ? root.bridge.bleIp : "")
                      .arg(root.bridge ? root.bridge.bleRssi : 0)
                font.pixelSize: 11
                color: Colors.textSecondary
                wrapMode: Text.Wrap
            }

            Text {
                Layout.fillWidth: true
                visible: root.bridge && root.bridge.bleError.length > 0
                text: root.bridge ? root.bridge.bleError : ""
                font.pixelSize: 11
                color: Colors.statusDisconnected
                wrapMode: Text.Wrap
            }

            Text {
                Layout.fillWidth: true
                visible: root.bridge && root.bridge.bleSavedSsid.length > 0
                         && root.bridge.bleState !== "connected"
                text: qsTr("板子已保存：%1").arg(root.bridge ? root.bridge.bleSavedSsid : "")
                font.pixelSize: 11
                color: Colors.textPlaceholder
                wrapMode: Text.Wrap
            }

            ThemedButton {
                objectName: "bleScanButton"
                Layout.fillWidth: true
                implicitHeight: 34
                tone: "soft"
                text: qsTr("搜索板卡")
                enabled: root.bridge && !root.bridge.bleBusy
                onClicked: {
                    root.bridge.bleSetToken(root.token)
                    root.bridge.bleScan()
                }
            }

            ComboRow {
                objectName: "bleDeviceCombo"
                Layout.fillWidth: true
                label: qsTr("板卡")
                comboWidth: Math.max(170, root.width - 70)
                model: root.bridge ? root.bridge.bleDeviceLabels : []
                enabled: root.bridge && !root.bridge.bleBusy
                         && root.bridge.bleDevices.length > 0
                onActivated: function (index) {
                    root._connectDevice(index)
                }
            }

            ThemedButton {
                objectName: "bleScanWifiButton"
                Layout.fillWidth: true
                implicitHeight: 34
                tone: "soft"
                text: qsTr("扫描 Wi-Fi")
                enabled: root.bridge && root.bridge.bleState === "reading"
                onClicked: root.bridge.bleScanWifi()
            }

            ComboRow {
                id: netCombo
                objectName: "bleNetCombo"
                Layout.fillWidth: true
                label: qsTr("网络")
                comboWidth: Math.max(170, root.width - 70)
                model: root.bridge ? root.bridge.bleNetLabels : []
                enabled: root.bridge && root.bridge.bleNets.length > 0
                onActivated: function (index) {
                    root._pickSsid(index)
                }
            }

            InputRow {
                id: ssidField
                objectName: "bleSsidField"
                Layout.fillWidth: true
                label: qsTr("Wi-Fi 名称")
                placeholderText: qsTr("只能选 2.4GHz 网络")
            }

            InputRow {
                id: passField
                objectName: "blePassField"
                Layout.fillWidth: true
                label: qsTr("Wi-Fi 密码")
                password: true
                placeholderText: qsTr("开放网络留空")
            }

            ThemedButton {
                objectName: "bleApplyButton"
                Layout.fillWidth: true
                implicitHeight: 38
                tone: "primary"
                text: qsTr("连接并保存")
                enabled: root.bridge && !root.bridge.bleBusy
                         && root.bridge.bleState === "reading"
                         && ssidField.text.trim().length > 0
                onClicked: {
                    root.bridge.bleSetToken(root.token)
                    root.bridge.bleApplyWifi(ssidField.text.trim(), passField.text)
                }
            }

            ThemedButton {
                objectName: "bleForgetButton"
                Layout.fillWidth: true
                implicitHeight: 32
                tone: "dangerSoft"
                text: qsTr("忘记网络")
                enabled: root.bridge && !root.bridge.bleBusy
                         && (root.bridge.bleState === "reading"
                             || root.bridge.bleState === "connected")
                onClicked: root.bridge.bleForgetWifi()
            }

            Text {
                Layout.fillWidth: true
                text: qsTr("板子只支持 2.4GHz。配置时平台会短暂离线，请先停止运动；"
                           + "失败会自动回滚到原网络。")
                font.pixelSize: 11
                color: Colors.textPlaceholder
                wrapMode: Text.Wrap
            }
        }
    }
}
