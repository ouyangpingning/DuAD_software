import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software
import "../components"

/*
    BLE 配网面板（二轴 / Z 轴共用）—— 固定板卡、自动扫描、列表点选弹窗。

    流程（与 docs/20 一致）：
      1. 右上角刷新 → 搜固定名字的板卡（XY = DuAD-XY-55C6；Z = DuAD-Z- 前缀）
      2. 搜到就自动连；连上后自动 `wifi status` + `wifi scan`
      3. 列表列出板子扫到的全部 Wi-Fi（2.4GHz）
      4. 点某一项 → 弹窗输入密码 → 连接；失败留在弹窗显示原因
      5. 弹窗里的「忘记网络」只对板子当前保存的 SSID 生效

    ⚠ 不再有「板卡下拉」「Wi-Fi下拉」「Wi-Fi名称/密码输入框」——
      这些都在流程里自动完成或收进弹窗。
*/
Item {
    id: root
    objectName: "blePanel"

    property var bridge: null
    property string roleLabel: ""
    property bool expanded: true

    implicitWidth: 460
    implicitHeight: layout.implicitHeight

    readonly property bool busy: root.bridge
                                 && (root.bridge.bleState === "scanning"
                                     || root.bridge.bleState === "connecting"
                                     || root.bridge.bleState === "probing"
                                     || root.bridge.bleState === "scanning_wifi"
                                     || root.bridge.bleState === "provisioning")

    function _statusColor() {
        if (!root.bridge)
            return Colors.textPlaceholder
        if (root.bridge.bleState === "connected")
            return Colors.statusConnected
        if (root.bridge.bleState === "failed")
            return Colors.statusDisconnected
        if (root.busy)
            return Colors.accent
        return Colors.textPlaceholder
    }

    function _openWifi(net) {
        if (!root.bridge || !net)
            return
        wifiDialog.ssid = String(net.ssid || "")
        wifiDialog.rssi = Math.round(Number(net.rssi || 0))
        wifiDialog.message = ""
        wifiDialog.open()
    }

    ColumnLayout {
        id: layout
        width: parent.width
        spacing: 8

        RowLayout {
            Layout.fillWidth: true
            spacing: 6

            CollapsibleHeader {
                Layout.fillWidth: true
                title: qsTr("Wi-Fi 连接（蓝牙配网）")
                expanded: root.expanded
                onToggled: root.expanded = !root.expanded
            }

            AnimatedRefreshButton {
                objectName: "bleSearchButton"
                Layout.alignment: Qt.AlignVCenter
                size: 36
                iconSource: "../../images/refresh.svg"
                running: root.busy
                enabled: root.bridge && !root.busy
                onClicked: {
                    root.bridge.bleScan()
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            visible: root.expanded
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
            visible: root.expanded && root.bridge
                     && root.bridge.bleSavedSsid.length > 0
            text: qsTr("板子已保存：%1").arg(root.bridge ? root.bridge.bleSavedSsid : "")
            font.pixelSize: 11
            color: Colors.textPlaceholder
            wrapMode: Text.Wrap
        }

        Text {
            Layout.fillWidth: true
            visible: root.expanded && root.bridge && root.bridge.bleError.length > 0
            text: root.bridge ? root.bridge.bleError : ""
            font.pixelSize: 11
            color: Colors.statusDisconnected
            wrapMode: Text.Wrap
        }

        // Wi-Fi 列表：带圆角边框的列表框（不是裸列表），行悬停/分隔线/已保存 pill
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(
                240,
                Math.max(36, (root.bridge ? root.bridge.bleNets.length : 0) * 38 + 10))
            visible: root.expanded && root.bridge && root.bridge.bleNets.length > 0
            radius: 8
            color: Colors.pageBg
            border { width: 1; color: Colors.fieldBorder }

            ListView {
                id: wifiList
                anchors.fill: parent
                anchors.margins: 5
                clip: true
                model: root.bridge ? root.bridge.bleNets : []

                delegate: Rectangle {
                    width: wifiList.width
                    height: 38
                    radius: 5
                    color: rowMa.containsMouse ? Colors.interactiveHover : "transparent"

                    Rectangle {
                        anchors { left: parent.left; right: parent.right; bottom: parent.bottom
                                  leftMargin: 8; rightMargin: 8 }
                        height: 1
                        color: Colors.panelBorder
                        visible: index < wifiList.count - 1
                    }

                    Text {
                        anchors { left: parent.left; leftMargin: 10; verticalCenter: parent.verticalCenter }
                        text: modelData.ssid || ""
                        font.pixelSize: 13
                        color: Colors.textPrimary
                        elide: Text.ElideRight
                        width: parent.width - 110
                    }

                    Text {
                        anchors { right: parent.right; rightMargin: 10; verticalCenter: parent.verticalCenter }
                        text: (modelData.rssi || 0) + " dBm"
                        font.pixelSize: 10
                        color: Colors.textPlaceholder
                    }

                    // 当前板子保存的网络：绿色 pill
                    Rectangle {
                        anchors { right: parent.right; rightMargin: 62; verticalCenter: parent.verticalCenter }
                        visible: root.bridge && modelData.ssid === root.bridge.bleSavedSsid
                        implicitWidth: savedText.implicitWidth + 10
                        implicitHeight: 16
                        radius: 8
                        color: Colors.successSoft
                        border { width: 1; color: Colors.statusConnected }

                        Text {
                            id: savedText
                            anchors.centerIn: parent
                            text: qsTr("已保存")
                            font.pixelSize: 9
                            color: Colors.statusConnected
                        }
                    }

                    MouseArea {
                        id: rowMa
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root._openWifi(modelData)
                    }
                }
            }
        }

        Text {
            Layout.fillWidth: true
            visible: root.expanded && root.bridge
                     && root.bridge.bleState === "reading"
                     && root.bridge.bleNets.length === 0
            text: qsTr("没有扫描到 Wi-Fi。请确认板子在 2.4GHz 路由器附近，点右上角刷新重试。")
            font.pixelSize: 11
            color: Colors.textPlaceholder
            wrapMode: Text.Wrap
        }

        Text {
            Layout.fillWidth: true
            visible: root.expanded
            text: qsTr("板子只支持 2.4GHz。配置时平台会短暂离线，请先停止运动；失败会自动回滚到原网络。"
                       + "无需口令：连上后会自己把板子的连接口令读回来。")
            font.pixelSize: 11
            color: Colors.textPlaceholder
            wrapMode: Text.Wrap
        }
    }

    // ── Wi-Fi 密码 / 连接 / 忘记 ───────────────────────────
    Popup {
        id: wifiDialog
        objectName: "bleWifiDialog"
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        width: Math.min(360, root.width - 24)
        x: (root.width - width) / 2
        y: 40
        padding: 14

        property string ssid: ""
        property int rssi: 0
        property string message: ""
        property bool forgetPending: false

        background: Rectangle {
            color: Colors.cardBg
            radius: 10
            border { width: 1; color: Colors.cardBorderStrong }
        }

        contentItem: ColumnLayout {
            spacing: 10

            Text {
                Layout.fillWidth: true
                text: wifiDialog.ssid
                font.pixelSize: 14
                font.bold: true
                color: Colors.textPrimary
                elide: Text.ElideRight
            }

            Text {
                Layout.fillWidth: true
                text: qsTr("信号 %1 dBm").arg(wifiDialog.rssi)
                font.pixelSize: 11
                color: Colors.textPlaceholder
            }

            TextField {
                id: passwordField
                objectName: "bleWifiPasswordField"
                Layout.fillWidth: true
                placeholderText: qsTr("Wi-Fi 密码（必填）")
                echoMode: TextField.Password
                font.pixelSize: 13
                color: Colors.textPrimary
                onAccepted: {
                    if (connectButton.enabled)
                        connectButton.clicked()
                }
                background: Rectangle {
                    implicitHeight: 34
                    radius: 4
                    color: Colors.pageBg
                    border {
                        width: 1
                        color: passwordField.activeFocus ? Colors.accent : Colors.fieldBorder
                    }
                }
            }

            Text {
                Layout.fillWidth: true
                visible: wifiDialog.message.length > 0
                text: wifiDialog.message
                font.pixelSize: 11
                color: (root.bridge && root.bridge.bleState === "failed")
                       ? Colors.statusDisconnected : Colors.textSecondary
                wrapMode: Text.Wrap
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                ThemedButton {
                    id: connectButton
                    objectName: "bleWifiConnectButton"
                    Layout.fillWidth: true
                    implicitHeight: 36
                    tone: "primary"
                    text: qsTr("连接")
                    enabled: passwordField.text.length > 0
                             && root.bridge && !root.busy
                    onClicked: {
                        wifiDialog.message = qsTr("正在连接…")
                        root.bridge.bleApplyWifi(wifiDialog.ssid, passwordField.text)
                    }
                }

                ThemedButton {
                    objectName: "bleWifiCancelButton"
                    implicitHeight: 36
                    tone: "neutral"
                    text: qsTr("取消")
                    onClicked: wifiDialog.close()
                }
            }

            ThemedButton {
                objectName: "bleWifiForgetButton"
                Layout.fillWidth: true
                implicitHeight: 32
                tone: "dangerSoft"
                text: qsTr("忘记网络")
                enabled: root.bridge && !root.busy
                         && root.bridge.bleSavedSsid === wifiDialog.ssid
                onClicked: {
                    wifiDialog.forgetPending = true
                    root.bridge.bleForgetWifi()
                }
            }

            Text {
                Layout.fillWidth: true
                visible: root.bridge && root.bridge.bleSavedSsid !== wifiDialog.ssid
                text: qsTr("该网络不是板子当前保存的网络，无需忘记。")
                font.pixelSize: 10
                color: Colors.textPlaceholder
                wrapMode: Text.Wrap
            }
        }

        onOpened: {
            passwordField.text = ""
            passwordField.forceActiveFocus()
            wifiDialog.message = ""
        }

        Connections {
            target: root.bridge
            ignoreUnknownSignals: true
            function onBleStateChanged() {
                if (!wifiDialog.visible || !root.bridge)
                    return
                if (wifiDialog.forgetPending
                        && root.bridge.bleSavedSsid.length === 0) {
                    wifiDialog.forgetPending = false
                    wifiDialog.message = qsTr("已忘记网络：%1").arg(wifiDialog.ssid)
                    closeTimer.start()
                    return
                }
                if (root.bridge.bleState === "connected") {
                    wifiDialog.message = qsTr("已连接：%1").arg(root.bridge.bleIp)
                    closeTimer.start()
                } else if (root.bridge.bleState === "failed") {
                    wifiDialog.message = root.bridge.bleError
                } else if (root.bridge.bleState === "provisioning") {
                    wifiDialog.message = qsTr("正在连接 Wi-Fi…")
                }
            }
        }

        Timer {
            id: closeTimer
            interval: 1200
            onTriggered: wifiDialog.close()
        }
    }
}
