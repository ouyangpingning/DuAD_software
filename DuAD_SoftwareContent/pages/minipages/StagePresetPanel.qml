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
    // 标题画不画。⚠ 2026-09-28 起本面板住在**左列**「二轴相机平台状态」折叠节里，
    //   折叠头没有"预设"两个字，所以页面传 showTitle: true —— 靠这个小标题
    //   把"读数"和"预设"两块分开。
    property bool showTitle: true

    signal gotoRequested(int index)
    signal deleteRequested(int index)
    signal saveRequested(string name)

    implicitWidth: 460
    implicitHeight: mainLayout.implicitHeight + 32

    // 卡片底板：白底 + 描边 + 硬阴影（与连接卡/手动控制卡同一套，见 CardSurface.qml）
    CardSurface {
        anchors.fill: parent

        ColumnLayout {
            id: mainLayout
            spacing: 10
            // ⚠ 边距 12（原 24）：本面板 2026-09-28 搬进**左列**（宽 160~420），
            //   默认窗口下内容区只有 ~164px，24 的边距直接吃掉 48px。
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 12 }

            RowLayout {
                Layout.fillWidth: true
                Text {
                    visible: root.showTitle
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
                            // 坐标：窄列里整块让位（"前往/删除"比坐标重要 ——
                            // 坐标在列表里只是帮认路，点不动才是真问题）。
                            // 164px 内容宽时固定项 = 序号 10 + 前往 40 + ✕ 22 + 间距 24，
                            // 剩给名字 ~68px，够放 3~4 个汉字。
                            Text {
                                visible: row.width >= 230
                                text: Number(row.modelData.x).toFixed(2) + ", "
                                      + Number(row.modelData.y).toFixed(2) + " mm"
                                font.pixelSize: 11
                                font.family: "monospace"
                                color: Colors.textSecondary
                            }

                            ThemedButton {
                                implicitWidth: 46; implicitHeight: 26
                                radius: 6; hPadding: 10; iconSize: 0
                                // 列表行里的小按钮**不要阴影**：一列小方块个个浮起来
                                // 会比内容还吵，而且行高只有 34。
                                raised: false
                                enabled: root.canUse
                                text: qsTr("前往")
                                onClicked: root.gotoRequested(row.index)
                            }

                            // 删除：普通按钮就好，不要为了复用 AnimatedRefreshButton
                            // 去套一个 ✕ 文字层 —— 那层嵌套除了增加耦合没别的用。
                            ThemedButton {
                                implicitWidth: 26; implicitHeight: 26
                                radius: 6; hPadding: 0
                                tone: "dangerSoft"
                                raised: false
                                enabled: root.canUse
                                text: "✕"
                                onClicked: root.deleteRequested(row.index)
                                ToolTip.visible: hovered
                                ToolTip.delay: 600
                                ToolTip.text: qsTr("删除这个预设")
                            }
                        }
                    }
                }
            }

            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder }

            // ── 记录当前位置 ──────────────────────────
            // ⚠ 用 GridLayout + 动态 columns（AGENTS §19-30 的老办法）：
            //   窄列（左列默认内容区 ~164）里 InputRow 的最小宽就有 72+80=152，
            //   再并排一个 130 的按钮 = 290 → 整列被顶破。窄了改成上下两行。
            GridLayout {
                Layout.fillWidth: true
                columns: width >= 300 ? 2 : 1
                columnSpacing: 8
                rowSpacing: 8

                InputRow {
                    id: nameRow
                    objectName: "presetName"
                    Layout.fillWidth: true
                    label: qsTr("名称")
                    placeholderText: qsTr("例如：工位1")
                }

                ThemedButton {
                    implicitHeight: 40
                    hPadding: 32
                    tone: "soft"
                    Layout.fillWidth: parent.columns > 1 ? false : true
                    Layout.alignment: parent.columns > 1 ? Qt.AlignRight : Qt.AlignHCenter
                    enabled: root.canUse && nameRow.text.trim().length > 0
                    text: qsTr("记录当前位置")
                    onClicked: {
                        root.saveRequested(nameRow.text.trim())
                        nameRow.text = ""
                    }
                }
            }
        }
    }
}
