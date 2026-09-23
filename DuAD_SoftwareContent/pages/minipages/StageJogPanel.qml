import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software
import "../components"

/*
    手动控制卡 —— 步长选择 + 十字点动 + 急停/立基准 + 绝对定位。

    三条设计决定（都不是随手写的）：

    1. **急停只要连着就永远可点**，不管有没有在动。
       "只有动的时候才能停"的急停等于没有急停。

    2. **禁用必须写出原因**。固件有四道闸（基准/行程/防呆/运动互斥），
       拒绝时不会发任何运动指令。界面如果只是把按钮变灰，用户看到的就是
       "点了没反应" —— 所以这里用 gateHint 把缺什么直接说出来。

    3. **点动是"步进式"**：每按一次走一个明确步长，不是按住连续走。
       原因见 JogPad.qml 顶部注释（链路往返 20~60ms，平滑手感做不出来）。
*/
Item {
    id: root
    objectName: "jogPanel"      // 页面测试量卡片顺序用

    // ============================================================
    // 公有 API
    // ============================================================
    property bool connected: false
    property bool canMove: false
    property bool moving: false
    property string gateHint: ""        // 非空 = 说明为什么不能动
    property real posX: 0
    property real posY: 0

    signal jogRequested(real dx, real dy)
    property bool motorEnabled: false   // 来自 StageBridge.enabled（固件 json 的 en）
    signal stopRequested()
    signal zeroRequested()
    signal enableToggled()
    signal moveToRequested(real x, real y)

    property real step: 1.0             // mm

    implicitWidth: 460
    implicitHeight: mainLayout.implicitHeight + 32

    // ============================================================
    // 卡内小组件：步长选项
    // ============================================================
    component StepChip: Button {
        id: chip
        property real value: 1.0
        // 给冒烟测试用的稳定标识（按名字找控件，比按 className 猜可靠）
        objectName: "stepChip_" + value
        checkable: true
        checked: Math.abs(root.step - chip.value) < 1e-9
        implicitHeight: 28
        implicitWidth: 52

        onClicked: root.step = chip.value

        background: Rectangle {
            radius: 6
            color: chip.checked ? Colors.interactivePressed
                                : (chip.hovered ? Colors.interactiveHover : "transparent")
            border { width: 1; color: chip.checked ? Colors.interactivePressed : Colors.cardBorder }
            Behavior on color { ColorAnimation { duration: 120 } }
        }
        contentItem: Text {
            text: chip.value < 1 ? chip.value.toFixed(1) : chip.value.toFixed(0)
            font.pixelSize: 12
            color: chip.enabled ? Colors.textPrimary : Colors.textPlaceholder
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }

    // ============================================================
    // 本体
    // ============================================================
    Rectangle {
        anchors.fill: parent
        radius: 12
        color: Colors.contentBg

        ColumnLayout {
            id: mainLayout
            spacing: 10
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 24 }

            Text {
                text: qsTr("手动控制")
                font.pixelSize: 14
                font.bold: true
                color: Colors.textPrimary
            }

            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder }

            // ── 步长 ──────────────────────────────────
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Text {
                    text: qsTr("步长")
                    font.pixelSize: 12
                    color: Colors.textSecondary
                }
                StepChip { value: 0.1 }
                StepChip { value: 1.0 }
                StepChip { value: 10.0 }
                StepChip { value: 50.0 }
                Text {
                    text: "mm"
                    font.pixelSize: 12
                    color: Colors.textPlaceholder
                    Layout.fillWidth: true
                }
            }

            // ── 十字点动 ──────────────────────────────
            JogPad {
                objectName: "jogPad"
                Layout.alignment: Qt.AlignHCenter
                step: root.step
                interactive: root.canMove
                onJog: function(dx, dy) { root.jogRequested(dx, dy) }
            }

            // ── 禁用原因（有就显示，没有就收起）────────
            Text {
                visible: root.gateHint.length > 0
                Layout.fillWidth: true
                text: "⚠ " + root.gateHint
                font.pixelSize: 11
                color: Colors.statusDisconnected
                wrapMode: Text.Wrap
            }

            // ── 急停 / 立基准 ─────────────────────────
            RowLayout {
                Layout.fillWidth: true
                spacing: 10

                // 急停：只要连着就永远可点（不依赖 moving）
                Button {
                    objectName: "stopButton"
                    Layout.fillWidth: true
                    implicitHeight: 40
                    enabled: root.connected
                    onClicked: root.stopRequested()

                    background: Rectangle {
                        radius: 10
                        color: !parent.enabled
                               ? "transparent"
                               : (parent.pressed ? Qt.darker(Colors.statusDisconnected, 1.3)
                                                 : Colors.statusDisconnected)
                        Behavior on color { ColorAnimation { duration: 120 } }
                    }
                    contentItem: Text {
                        text: qsTr("■  停止")
                        font.pixelSize: 15
                        font.bold: true
                        color: parent.enabled ? "#ffffff" : Colors.textPlaceholder
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                }

                Button {
                    objectName: "zeroButton"
                    Layout.fillWidth: true
                    implicitHeight: 40
                    enabled: root.connected
                    onClicked: root.zeroRequested()

                    background: Rectangle {
                        radius: 10
                        color: parent.pressed ? Colors.interactivePressed
                                              : (parent.hovered ? Colors.interactiveHover : "transparent")
                        border { width: 1; color: Colors.cardBorder }
                        Behavior on color { ColorAnimation { duration: 120 } }
                    }
                    contentItem: Text {
                        text: qsTr("⌂  把当前位置设为原点")
                        font.pixelSize: 13
                        color: parent.enabled ? Colors.textPrimary : Colors.textPlaceholder
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                }
            }

            // ── 使能 / 失能（2026-09-13 用户要求补上）──
            //   为什么必须有：状态条上一直显示"未使能"，而界面**没有任何地方能改** ——
            //   状态看得见、操作没有，正是"点了没反应"的镜像。
            //   两个方向都有实际用途：
            //     · 失能 → 用手推台面调机械、对基准；
            //     · 使能 → 顶住位置（失能时台面能被外力推动，一推坐标系就废了）。
            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                Button {
                    objectName: "enableButton"
                    Layout.fillWidth: true
                    implicitHeight: 32
                    enabled: root.connected
                    // 文案是**动作**而不是状态：免得用户看着"未使能"再去点写着"未使能"的按钮。
                    // ⚠ 写在 Button.text 上（而不是只写在 contentItem 里）：
                    //   一是无障碍/自动化能读到，二是页面测试能断言它 ——
                    //   只写在 contentItem 里的话 `property("text")` 是空串，
                    //   测试就成了假断言（这条真踩过）。
                    text: root.motorEnabled ? qsTr("失能（松掉电机，可用手推）")
                                            : qsTr("使能（顶住台面）")
                    onClicked: root.enableToggled()

                    background: Rectangle {
                        radius: 8
                        color: parent.pressed ? Colors.interactivePressed
                                              : (parent.hovered ? Colors.interactiveHover : "transparent")
                        border { width: 1; color: Colors.cardBorder }
                        Behavior on color { ColorAnimation { duration: 120 } }
                    }
                    contentItem: Text {
                        text: parent.text
                        font.pixelSize: 12
                        color: parent.enabled ? Colors.textPrimary : Colors.textPlaceholder
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                }

                Text {
                    text: root.motorEnabled ? qsTr("已使能") : qsTr("未使能")
                    font.pixelSize: 11
                    color: root.motorEnabled ? Colors.statusConnected : Colors.textPlaceholder
                    Layout.alignment: Qt.AlignVCenter
                }
            }

            Text {
                Layout.fillWidth: true
                text: qsTr("失能后可以用手推台面调机械；但别在失能状态下指望坐标 —— "
                           + "台面被推动后基准就废了。注意：「设为原点」和任何运动命令"
                           + "都会自动把电机重新使能，所以调机械时失能要放在这些动作之后。")
                font.pixelSize: 10
                color: Colors.textPlaceholder
                wrapMode: Text.Wrap
            }

            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder }

            // ── 绝对定位 ──────────────────────────────
            Text {
                text: qsTr("绝对定位")
                font.pixelSize: 12
                font.bold: true
                color: Colors.textSecondary
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                InputRow {
                    id: txRow
                    objectName: "targetX"
                    Layout.fillWidth: true
                    label: "X (mm)"
                    text: root.posX.toFixed(2)
                }
                InputRow {
                    id: tyRow
                    objectName: "targetY"
                    Layout.fillWidth: true
                    label: "Y (mm)"
                    text: root.posY.toFixed(2)
                }
            }

            Button {
                objectName: "moveToButton"
                Layout.fillWidth: true
                implicitHeight: 36
                enabled: root.canMove
                text: qsTr("移动到该位置")
                onClicked: {
                    var x = parseFloat(txRow.text)
                    var y = parseFloat(tyRow.text)
                    if (isNaN(x) || isNaN(y)) return
                    root.moveToRequested(x, y)
                }
            }

            Text {
                Layout.fillWidth: true
                text: qsTr("超出工作区的目标会被自动夹到边界（固件那道闸只当兜底）。")
                font.pixelSize: 10
                color: Colors.textPlaceholder
                wrapMode: Text.Wrap
            }
        }
    }
}
