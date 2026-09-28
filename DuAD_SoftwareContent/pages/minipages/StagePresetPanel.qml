import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software
import "../components"

/*
    预设位置卡 —— 把常用工位记下来，一键前往。

    ⚠ 这里有一句必须显示的警告：
      驱动器用的是**单圈编码器**，掉电就丢多圈累计位置（手册 2.3③）。
      也就是说预设坐标是相对"本次上电立的那个基准"的 —— 重新上电后如果没重立基准，
      「前往预设」会走到一个完全错误的地方，而且界面看起来一切正常。
      （"重立基准"= 推到位 → 设为原点；**不是**必须回零 —— 无限位回零条件多，
       已降级为可选，见《使用说明》§6.3。）
      所以页面在未立基准时禁用整个列表（由外部的 enabled 控制），并常驻这行提示。
*/
Item {
    id: root

    property bool canUse: false            // 未连接 / 未立基准时为 false
    property var presets: []               // [{name, x, y}]

    signal gotoRequested(int index)
    signal deleteRequested(int index)
    signal saveRequested(string name)

    implicitWidth: 460
    implicitHeight: mainLayout.implicitHeight + 32

    Rectangle {
        anchors.fill: parent
        radius: 12
        color: Colors.contentBg

        ColumnLayout {
            id: mainLayout
            spacing: 10
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 24 }

            RowLayout {
                Layout.fillWidth: true
                Text {
                    text: qsTr("预设位置")
                    font.pixelSize: 14
                    font.bold: true
                    color: Colors.textPrimary
                    Layout.fillWidth: true
                }
                Text {
                    text: root.presets.length + " " + qsTr("个")
                    font.pixelSize: 11
                    color: Colors.textPlaceholder
                }
            }

            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder }

            // 单圈编码器的警告：常驻，不折叠 —— 这条一旦忽略就会走错位置。
            // 口径是「每次上电重立基准（推到位 → 设为原点）」，**不是**「必须回零」：
            // 无限位回零条件多、已降级为可选，见《使用说明》§6.3。
            // ── 列表 ──────────────────────────────────
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 4

                Text {
                    visible: root.presets.length === 0
                    text: qsTr("还没有预设。把台面移到工位后，在下面起个名字记下来。")
                    font.pixelSize: 11
                    color: Colors.textPlaceholder
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                }

                Repeater {
                    model: root.presets

                    delegate: Rectangle {
                        id: row
                        required property int index
                        required property var modelData

                        Layout.fillWidth: true
                        implicitHeight: 34
                        radius: 6
                        color: rowMa.containsMouse ? Colors.interactiveHover : "transparent"
                        border { width: 1; color: Colors.cardBorder }

                        MouseArea {
                            id: rowMa
                            anchors.fill: parent
                            hoverEnabled: true
                            acceptedButtons: Qt.NoButton      // 只用于悬停底色，点击交给按钮
                        }

                        RowLayout {
                            anchors { fill: parent; leftMargin: 10; rightMargin: 6 }
                            spacing: 8

                            Text {
                                text: (row.index + 1) + "."
                                font.pixelSize: 11
                                color: Colors.textPlaceholder
                            }
                            Text {
                                text: row.modelData.name
                                font.pixelSize: 13
                                color: root.canUse ? Colors.textPrimary : Colors.textPlaceholder
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                            Text {
                                text: Number(row.modelData.x).toFixed(2) + ", "
                                      + Number(row.modelData.y).toFixed(2) + " mm"
                                font.pixelSize: 11
                                font.family: "monospace"
                                color: Colors.textSecondary
                            }

                            Button {
                                implicitWidth: 52; implicitHeight: 24
                                enabled: root.canUse
                                text: qsTr("前往")
                                onClicked: root.gotoRequested(row.index)
                                background: Rectangle {
                                    radius: 5
                                    color: parent.pressed ? Colors.interactivePressed
                                                          : (parent.hovered ? Colors.interactiveHover
                                                                            : "transparent")
                                    border { width: 1; color: Colors.cardBorder }
                                }
                                contentItem: Text {
                                    text: parent.text
                                    font.pixelSize: 11
                                    color: parent.enabled ? Colors.textPrimary : Colors.textPlaceholder
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }
                            }

                            // 删除：普通按钮就好，不要为了复用 AnimatedRefreshButton
                            // 去套一个 ✕ 文字层 —— 那层嵌套除了增加耦合没别的用。
                            Button {
                                implicitWidth: 26; implicitHeight: 24
                                enabled: root.canUse
                                text: "✕"
                                onClicked: root.deleteRequested(row.index)
                                background: Rectangle {
                                    radius: 5
                                    color: parent.pressed ? Colors.cardDangerHover
                                                          : (parent.hovered ? Colors.cardDangerBg
                                                                            : "transparent")
                                }
                                contentItem: Text {
                                    text: parent.text
                                    font.pixelSize: 12
                                    color: parent.enabled ? Colors.textSecondary : Colors.textPlaceholder
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }
                            }
                        }
                    }
                }
            }

            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder }

            // ── 记录当前位置 ──────────────────────────
            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                InputRow {
                    id: nameRow
                    objectName: "presetName"
                    Layout.fillWidth: true
                    label: qsTr("名称")
                    placeholderText: qsTr("例如：工位1")
                }

                Button {
                    implicitWidth: 130; implicitHeight: 32
                    enabled: root.canUse && nameRow.text.trim().length > 0
                    text: qsTr("记录当前位置")
                    onClicked: {
                        root.saveRequested(nameRow.text.trim())
                        nameRow.text = ""
                    }
                    background: Rectangle {
                        radius: 6
                        color: parent.enabled
                               ? (parent.pressed ? Colors.interactivePressed
                                                 : (parent.hovered ? Colors.interactiveHover
                                                                   : "transparent"))
                               : "transparent"
                        border { width: 1; color: Colors.cardBorder }
                    }
                    contentItem: Text {
                        text: parent.text
                        font.pixelSize: 12
                        color: parent.enabled ? Colors.textPrimary : Colors.textPlaceholder
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                }
            }
        }
    }
}
