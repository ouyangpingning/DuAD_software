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
        // 36：卡收窄到 ~224（三列版面）后一行要装下"步长 + 4 个 chip"，
        // 52 是整卡 460 时代定的（ZStageJogPanel 的 chip 同理）。
        implicitWidth: 36

        onClicked: root.step = chip.value

        background: Rectangle {
            radius: 6
            color: chip.checked ? Colors.interactivePressed
                                : (chip.hovered ? Colors.interactiveHover : "transparent")
            border { width: 1; color: chip.checked ? Colors.textSecondary : Colors.cardBorder }
            Behavior on color { ColorAnimation { duration: 120 } }
        }
        contentItem: Text {
            text: chip.value < 1 ? chip.value.toFixed(1) : chip.value.toFixed(0)
            font.pixelSize: 12
            // 当前步长是**安全相关参数**（按一下走多远），选中态必须一眼可辨：
            // 加粗 + 描边加深，而不是只把底色从透明换成浅色。
            font.bold: chip.checked
            color: chip.enabled ? Colors.textPrimary : Colors.textPlaceholder
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }

    // ============================================================
    // 本体
    // ============================================================
    CardSurface {
        anchors.fill: parent

        ColumnLayout {
            id: mainLayout
            spacing: 10
            // 16（原 24）：卡宽 224 − 32 = 192，正好装下 JogPad(188)
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 16 }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                IconImage {
                    Layout.alignment: Qt.AlignVCenter
                    source: "../../images/二轴平台.svg"
                    width: 16
                    height: 16
                }
                Text {
                    text: qsTr("二轴手动控制")
                    font.pixelSize: 14
                    font.bold: true
                    color: Colors.textPrimary
                }
                Item { Layout.fillWidth: true }
            }

            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder }

            // ── 步长 ──────────────────────────────────
            RowLayout {
                Layout.fillWidth: true
                // ⚠ spacing 4（不是 6/8）：这一行的 implicitWidth 会成为嵌套布局的
                //   **最小宽度**，超过列宽(192)时整列跟着溢出（实测 6 时 iw=198）。
                spacing: 4
                Text {
                    text: qsTr("步长")
                    font.pixelSize: 12
                    color: Colors.textSecondary
                }
                StepChip { value: 0.1 }
                StepChip { value: 1.0 }
                StepChip { value: 10.0 }
                StepChip { value: 50.0 }
                Item { Layout.fillWidth: true }
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

            // ── 使能 / 失能（2026-09-13 用户要求补上）──
            //   为什么必须有：状态条上一直显示"未使能"，而界面**没有任何地方能改** ——
            //   状态看得见、操作没有，正是"点了没反应"的镜像。
            //   两个方向都有实际用途：
            //     · 失能 → 用手推台面调机械、对基准；
            //     · 使能 → 顶住位置（失能时台面能被外力推动，一推坐标系就废了）。
            // 行序照 2026-09-28 手绘稿：[使能][设为原点] 一行，「停止」单独一行 ——
            //   窄卡里塞不下三个并排按钮，急停独占一行也更醒目、更够得着。
            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                // ⚠ 样式一律走 ThemedButton（颜色只来自 Colors，不再手抄 background）
                ThemedButton {
                    objectName: "enableButton"
                    implicitHeight: 32
                    hPadding: 24
                    enabled: root.connected
                    // 文案是**动作**而不是状态：免得用户看着"未使能"再去点写着"未使能"的按钮。
                    // ⚠ 写在 Button.text 上（而不是只写在 contentItem 里）：
                    //   一是无障碍/自动化能读到，二是页面测试能断言它 ——
                    //   只写在 contentItem 里的话 `property("text")` 是空串，
                    //   测试就成了假断言（这条真踩过）。
                    // 短标签 + 悬停说明（2026-09-28 精简）：长文案是"说明"，
                    // 不是"操作"，缩到图标+两个字，细节交给 ToolTip 与文档。
                    text: root.motorEnabled ? qsTr("失能") : qsTr("使能")
                    iconSource: "../../images/电源.svg"
                    iconSize: 14
                    onClicked: root.enableToggled()

                    ToolTip.visible: hovered
                    ToolTip.delay: 600
                    ToolTip.text: qsTr("失能 = 松掉电机，可以用手推台面调机械；"
                                       + "但台面被推动后基准就废了，要重新「设为原点」。"
                                       + "注意「设为原点」和任何运动命令都会自动重新使能。")
                }

                ThemedButton {
                    objectName: "zeroButton"
                    implicitHeight: 32
                    enabled: root.connected
                    // ⚠ text 写在 Button 上（不只是 contentItem）：无障碍/自动化读得到，
                    //   否则 property("text") 是空串（AGENTS 里那条"假断言"的坑）。
                    text: qsTr("设为原点")
                    iconSource: "../../images/home.svg"
                    iconSize: 15
                    fontPixelSize: 13
                    onClicked: root.zeroRequested()

                    ToolTip.visible: hovered
                    ToolTip.delay: 600
                    ToolTip.text: qsTr("把当前位置当作 0 点（立基准）。"
                                       + "先把滑座推到靠块/硬限位贴实再点它，"
                                       + "每次上电都要重立一次。")
                }

                Item { Layout.fillWidth: true }   // 弹簧：控件靠左，不拉满
            }

            // ── 急停 ─────────────────────────────────
            // 只要连着就永远可点（不依赖 moving）——"只有动的时候才能停"的急停等于没有急停。
            ThemedButton {
                objectName: "stopButton"
                Layout.fillWidth: true
                implicitHeight: 40
                radius: 10
                // 急停：**禁用时也保留淡红底**（ThemedButton 里 danger 的处理）——
                // 整个消失的话，用户会以为"急停按钮怎么没了"。
                tone: "danger"
                enabled: root.connected
                text: qsTr("停止")      // 同上：写在 Button 上，给无障碍与测试读
                iconSource: "../../images/停止.svg"
                iconSize: 17
                fontPixelSize: 15
                fontBold: true
                onClicked: root.stopRequested()
            }

            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder }

            // ── 绝对定位 ──────────────────────────────
            RowLayout {
                Layout.fillWidth: true
                spacing: 6
                IconImage {
                    Layout.alignment: Qt.AlignVCenter
                    source: "../../images/靶心.svg"
                    width: 13; height: 13
                }
                Text {
                    text: qsTr("绝对定位")
                    font.pixelSize: 12
                    font.bold: true
                    color: Colors.textSecondary
                }
                Item { Layout.fillWidth: true }
            }

            // X / Y 竖排：卡收窄后放不下一对 132px 的输入行（手绘稿也是竖排）
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

            ThemedButton {
                objectName: "moveToButton"
                // 居中（用户 2026-09-28 截图批注："我觉得可以居中"）—— 原来靠左，
                // 在这张空白很多的卡片里显得没着落。
                Layout.alignment: Qt.AlignHCenter
                implicitHeight: 36
                hPadding: 40
                // soft：本卡"提交一个动作"的主按钮，淡强调底让它和周围的描边按钮分开。
                // 原来它**没写 background** → 用的是 Fusion 默认灰渐变，换主题时纹丝不动，
                // 正是用户截图点名"设置成可以随主题变化"的那个按钮。
                tone: "soft"
                enabled: root.canMove
                text: qsTr("移动到该位置")
                onClicked: {
                    var x = parseFloat(txRow.text)
                    var y = parseFloat(tyRow.text)
                    if (isNaN(x) || isNaN(y)) return
                    root.moveToRequested(x, y)
                }
            }

        }
    }
}
