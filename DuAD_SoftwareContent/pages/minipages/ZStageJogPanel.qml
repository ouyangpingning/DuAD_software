import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software
import "../components"

/*
    Z 轴手动控制卡 —— 步长 + 向上/向下 + 急停/设为原点 + 自动回零（可选）+ 绝对定位。

    与 X/Y 的 StageJogPanel 是**同一套约定**，但有三处是 Z 轴特有的，都不是随手写的：

    1. **只有一根轴，所以不用十字方向键**，改成两个大按钮（向上/向下）——
       竖直平台的操作面就是"升/降"两个动作，十字键里那两个横键在这里没有意义
       （放在界面上就是"点了没反应"的变体：看得见、按了什么都不发生）。

    2. **步长选项来自桥的 `stepChoices`**（`Repeater` + `model: root.stepChoices`），
       不在 QML 里再抄一份 0.1/1/10/50；选中**立即** `setStep`（用户明确要求），
       并且**不回写自己的属性** —— 单向数据流：点击 → 通知页面 → 桥改值 →
       属性绑定更新 → 高亮跟着变。这样"界面显示"和"桥里的值"不可能不一致。
       ⚠ 也正因如此这里用 `checkable: false`：`checked` 只由绑定驱动。
         若照抄 X/Y 那种 `checkable: true`，点击会把 `checked` 的绑定**打断**，
         之后点第二个步长时第一个仍高亮（两个同时选中）—— 静默的显示错误。

    3. **急停的 `enabled` 只跟 `connected` 走**（绝不跟 `canMove`/`moving`）：
       "只有动的时候才能停"的急停等于没有急停（AGENTS.md 第 10 条）。
       回零中「自动回零」禁用，但旁边出现「中断回零」——
       界面上必须永远有一个按得动的退出方式。
*/
Item {
    id: root
    objectName: "zJogPanel"

    // ============================================================
    // 公有 API
    // ============================================================
    property bool connected: false
    property bool moving: false
    property bool datum: false
    property bool limitsSet: false

    // 能不能做**绝对定位** —— 在这里从各自带 notify 的属性算，不用桥的 `canMove`
    //   （桥的 `canMove` notify 是 telemetryChanged；虽然桥现在乐观置位时也会发它，
    //    但本地算不依赖那条约定，任何一个输入变化都必然刷新）。
    //   判据与桥的 `_get_can_move()` 一致：连接 + 有基准 + 有软限位 + 没在动。
    readonly property bool canMove: connected && datum && limitsSet && !moving

    // 能不能**点动** —— ⚠ 刻意不要求基准/软限位，与桥的 `canJog` 一致。
    //   固件**故意**允许无基准相对点动（单次 ≤20mm，并打 ⚠ 说明软限位不生效），
    //   因为"把平台挪到参考位置"就是立基准的前置步骤；这里要是也灰掉，
    //   首次立基准就只剩"用手推平台"这一条路了。
    property bool canJog: connected && !moving
    // 两轴是否已使能（固件 json 的 en）—— 「失能」按钮的文字随它变
    property bool motorEnabled: false
    property string gateHint: ""        // 非空 = 说明为什么不能动（桥的 datumHint）
    property real posZ: 0
    property real step: 1.0             // mm，由页面绑定 ZStageBridge.step
    property var stepChoices: []
    property int homing: 0              // 0 没回零过 / 1 正在 / 2 完成 / 3 失败
    // 自动回零的方向（"down"/"up"）。**只存上位机**（QSettings，见桥的 setHomeDir）：
    // 板子里那个方向是给「上电自动回零」用的，两条路径分开 —— 用户明确要求过。
    property string homeDir: "down"

    signal enableToggled()
    signal stepPicked(real mm)
    signal jogUpRequested()
    signal jogDownRequested()
    signal moveToRequested(real mm)
    signal zeroRequested()
    signal homeRequested()
    signal homeDirPicked(string dir)
    signal stopRequested()

    implicitWidth: 460
    implicitHeight: body.implicitHeight + 32

    // ============================================================
    // 卡内小组件：步长选项
    // ============================================================
    component StepChip: Button {
        id: chip
        property real value: 1.0
        // 稳定标识：测试按名字找控件（`findChild(QObject, "zStepChip_10")`）
        objectName: "zStepChip_" + value
        // ⚠ 刻意 false：见文件头第 2 条 —— 让 checked 始终是绑定，点击不改它
        checkable: false
        checked: Math.abs(root.step - chip.value) < 1e-9
        implicitHeight: 28
        // 36：卡收窄到 ~224（三列版面），52 放不下四个（见 StageJogPanel 同步改动）
        implicitWidth: 36

        onClicked: root.stepPicked(chip.value)

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
            // 当前步长是安全相关参数：加粗 + 描边加深（与 X/Y 那张卡同一条）
            font.bold: chip.checked
            color: chip.enabled ? Colors.textPrimary : Colors.textPlaceholder
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }

    // ============================================================
    // 卡内小组件：回零方向
    // ============================================================
    // 与 StepChip 同一套约定（checkable:false + checked 只由绑定驱动，
    // 否则点击会打断 checked 的绑定、两个方向同时高亮）。
    component DirChip: Button {
        id: dchip
        property string dir: "down"
        objectName: "zHomeDirChip_" + dir
        checkable: false
        checked: root.homeDir === dchip.dir
        implicitHeight: 28
        implicitWidth: 40
        onClicked: root.homeDirPicked(dchip.dir)

        background: Rectangle {
            radius: 6
            color: dchip.checked ? Colors.interactivePressed
                                 : (dchip.hovered ? Colors.interactiveHover : "transparent")
            border { width: 1; color: dchip.checked ? Colors.textSecondary : Colors.cardBorder }
            Behavior on color { ColorAnimation { duration: 120 } }
        }
        contentItem: Text {
            // "向上/向下"两个词与上面的大按钮共用（少两个 i18n 词条）
            text: dchip.dir === "up" ? qsTr("向上") : qsTr("向下")
            font.pixelSize: 12
            font.bold: dchip.checked
            color: dchip.enabled ? Colors.textPrimary : Colors.textPlaceholder
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }

    CardSurface {
        anchors.fill: parent

        ColumnLayout {
            id: body
            objectName: "zJogBody"
            spacing: 10
            // 16（原 24）：卡收窄后的内容宽 = 224 − 32 = 192
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 16 }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                IconImage {
                    Layout.alignment: Qt.AlignVCenter
                    source: "../../images/Z轴平台.svg"
                    // ⚠ Layout 的子项必须用 Layout.preferred*（width/height 会被覆盖成 24）；
                    //   并且 implicit* 也在创建时定死（ColorOverlay 创建后被改尺寸可能拿不到纹理）
                    objectName: "jogTitleIcon"
                    implicitWidth: 16
                    implicitHeight: 16
                    Layout.preferredWidth: 16
                    Layout.preferredHeight: 16
                }
                Text {
                    text: qsTr("Z 轴手动控制")
                    font.pixelSize: 14
                    font.bold: true
                    color: Colors.textPrimary
                }
                Item { Layout.fillWidth: true }
            }

            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder }

            // ── 步长 ──────────────────────────────────
            // objectName 是给冒烟测试用的：Repeater 的 delegate 查不到（见下），
            // 测试要靠这一行拿到可视子项再按 objectName 找 chip。
            RowLayout {
                objectName: "zStepRow"
                Layout.fillWidth: true
                // ⚠ spacing 4：这一行的 implicitWidth 是嵌套布局的最小宽度，
                //   超过列宽(192)会把整列顶溢出（实测 6 时 iw=198，见 StageJogPanel 同步改动）
                spacing: 4
                Text {
                    text: qsTr("步长")
                    font.pixelSize: 12
                    color: Colors.textSecondary
                }
                Repeater {
                    model: root.stepChoices
                    delegate: StepChip { value: modelData }
                }
                Item { Layout.fillWidth: true }
            }

            // ── 向上 / 向下 ───────────────────────────
            // fillWidth（原 260 固定）：卡收窄到 ~224，260 会被裁掉
            ThemedButton {
                objectName: "zJogUpButton"
                // ⚠ 不 fillWidth（用户 2026-09-28 截图批注："这个按钮有点宽"）：
                //   卡片在中列很宽（~420），fillWidth 会把「向上/向下」拉成一条横幅，
                //   图标和文字之间空出两百多像素。改成与左列十字键同宽（188）并居中。
                Layout.preferredWidth: 188
                Layout.alignment: Qt.AlignHCenter
                implicitHeight: 52
                enabled: root.canJog
                text: qsTr("向上")
                iconSource: "../../images/向上.svg"
                iconSize: 18
                fontPixelSize: 15
                fontBold: true
                onClicked: root.jogUpRequested()
            }

            ThemedButton {
                objectName: "zJogDownButton"
                // ⚠ 不 fillWidth（用户 2026-09-28 截图批注："这个按钮有点宽"）：
                //   卡片在中列很宽（~420），fillWidth 会把「向上/向下」拉成一条横幅，
                //   图标和文字之间空出两百多像素。改成与左列十字键同宽（188）并居中。
                Layout.preferredWidth: 188
                Layout.alignment: Qt.AlignHCenter
                implicitHeight: 52
                enabled: root.canJog
                text: qsTr("向下")
                iconSource: "../../images/向下.svg"
                iconSize: 18
                fontPixelSize: 15
                fontBold: true
                onClicked: root.jogDownRequested()
            }

            // ── 为什么点不动（点击前就说）──────────────
            Text {
                objectName: "zGateHint"
                visible: root.gateHint.length > 0
                Layout.fillWidth: true
                text: "⚠ " + root.gateHint
                font.pixelSize: 11
                color: Colors.statusDisconnected
                wrapMode: Text.Wrap
            }

            // ── 急停 / 设为原点 ────────────────────────
            // 行序照 2026-09-28 手绘稿：[使能][设为原点] 一行，「停止」单独一行 ——
            //   窄卡里三个按钮排不下一行；急停独占一行也更醒目。
            //
            // 失能 / 使能 —— "用手把平台推到底 → 设为原点"这条主线的关键一步。
            // ⚠ 本机丝杠**自锁**（现场确认：断电后平台不动），所以失能后平台停在
            //   原地不会掉；这也是它和"急停"的分工：
            //     急停 `stop all` = 刹车 + **保持使能**（闭环还抱着平台，想停住用它）
            //     失能 `dis all`  = 电机完全不出力（**可以手推**平台去靠块）
            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                ThemedButton {
                    objectName: "zEnableButton"
                    implicitHeight: 38
                    hPadding: 22
                    enabled: root.connected
                    iconSource: "../../images/电源.svg"
                    iconSize: 14
                    // ⚠ 颜色**就是状态**（用户 2026-09-29 要求"使能要有颜色指示"）：
                    //     未使能 = success（淡绿）—— 电机不出力，可以手推平台去靠块
                    //     已使能 = dangerSoft（淡红）—— 闭环抱死 + 带电，危险的一侧
                    //   文字仍然是**动作**（"使能"/"失能"）而不是状态：免得用户看着
                    //   "未使能"三个字再去点一个写着"未使能"的按钮（这条一直没变）。
                    //   未连接时 ThemedButton 会把 success 回落到中性白 —— 状态未知
                    //   不能画成绿色（见 ThemedButton 的 tone 说明）。
                    tone: root.motorEnabled ? "dangerSoft" : "success"
                    onClicked: root.enableToggled()

                    // 短标签（原"失能（可手推平台）"太长，窄卡放不下）；
                    // 分工细节进 ToolTip —— 与 X/Y 那张卡同一条精简规矩。
                    ToolTip.visible: hovered
                    ToolTip.delay: 600
                    ToolTip.text: root.motorEnabled
                        ? qsTr("当前【已使能】：闭环抱住平台、带电（按钮是淡红）。"
                               + "点它 = 失能：电机完全不出力，可以用手推平台去靠块"
                               + "（丝杠自锁，平台不会掉）。急停与它的分工：急停是刹车且保持使能。")
                        : qsTr("当前【未使能】：电机不出力，可以手推平台（按钮是淡绿）。"
                               + "点它 = 使能：闭环抱住平台，顶住外力"
                               + "（失能时被推动坐标系就废了）。")

                    // ⚠ text 写在 Button 上（不只是 contentItem）：无障碍/测试要读得到
                    text: root.motorEnabled ? qsTr("失能") : qsTr("使能")
                }

                ThemedButton {
                    objectName: "zZeroButton"
                    implicitHeight: 38
                    hPadding: 22
                    enabled: root.connected && !root.moving
                    text: qsTr("设为原点")
                    iconSource: "../../images/home.svg"
                    iconSize: 15
                    fontPixelSize: 13
                    onClicked: root.zeroRequested()

                    ToolTip.visible: hovered
                    ToolTip.delay: 600
                    ToolTip.text: qsTr("把当前位置当作 Z=0（立基准）。"
                                       + "先把平台推到靠块/机械死点贴实再点它 —— "
                                       + "这是主线的立基准方式，每次上电都要重立一次。")
                }
                Item { Layout.fillWidth: true }   // 弹簧：控件靠左，不拉满
            }

            // 急停：只要连着就永远可点（不依赖 canMove / moving）
            ThemedButton {
                objectName: "zStopButton"
                Layout.fillWidth: true
                implicitHeight: 46
                radius: 10
                iconSize: 18
                tone: "danger"
                enabled: root.connected
                text: qsTr("停止")
                iconSource: "../../images/停止.svg"
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
                    objectName: "jogTargetIcon"
                    implicitWidth: 13                // 同上：创建时定死 + preferred
                    implicitHeight: 13
                    Layout.preferredWidth: 13
                    Layout.preferredHeight: 13
                }
                Text {
                    text: qsTr("绝对定位")
                    font.pixelSize: 12
                    font.bold: true
                    color: Colors.textSecondary
                }
                Item { Layout.fillWidth: true }
            }

            InputRow {
                id: targetRow
                objectName: "zTargetInput"
                Layout.fillWidth: true
                label: qsTr("目标 (mm)")
                text: root.posZ.toFixed(2)
                placeholderText: "100.0"
            }

            ThemedButton {
                objectName: "zMoveToButton"
                // 居中 + 主题化（与 X/Y 那张卡同一条：用户批注"我觉得可以居中"）
                Layout.alignment: Qt.AlignHCenter
                implicitHeight: 42
                hPadding: 44
                tone: "soft"
                enabled: root.canMove
                text: qsTr("移动到该位置")
                onClicked: {
                    var v = parseFloat(targetRow.text)
                    if (isNaN(v)) return
                    root.moveToRequested(v)
                }
            }

            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder }

            // ── 自动回零（可选路径）────────────────────
            // 回零方向：固件本来就支持 `zhome up|down`（桥里原来写死 down）。
            // 只影响这个按钮 —— 板子里的方向归「上电自动回零」，两条路径分开。
            RowLayout {
                objectName: "zHomeDirRow"
                Layout.fillWidth: true
                spacing: 4
                Text {
                    text: qsTr("回零方向")
                    font.pixelSize: 12
                    color: Colors.textSecondary
                }
                DirChip { dir: "down" }
                DirChip { dir: "up" }
                Item { Layout.fillWidth: true }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 10

                ThemedButton {
                    objectName: "zHomeButton"
                    hPadding: 36
                    implicitHeight: 38
                    // 回零中禁用（但下面会出现「中断回零」，见文件头第 3 条）
                    enabled: root.connected && !root.moving
                    text: qsTr("自动回零（可选）")
                    onClicked: root.homeRequested()

                    ToolTip.visible: hovered
                    ToolTip.delay: 600
                    ToolTip.text: qsTr("驱动器让两个电机同时朝下顶死点、按相电流判「顶住了」。"
                                       + "它要求两侧丝杠同时顶到各自的死点，否则会把平台拧歪 —— "
                                       + "主线做法是「推到靠块 → 设为原点」。")
                                      + "\n" + qsTr("方向由上面那对按钮选（向下 = 往底座死点，"
                                                   + "重力帮忙、撞不坏）。回零中随时能按「停止」打断。")
                }

                ThemedButton {
                    objectName: "zHomeAbortButton"
                    Layout.fillWidth: true
                    implicitHeight: 38
                    tone: "dangerSoft"
                    visible: root.homing === 1
                    Layout.preferredWidth: visible ? implicitWidth : 0
                    text: qsTr("中断回零")
                    onClicked: root.stopRequested()
                }
            }

            // ⚠ 必须写明它是**可选**的：驱动器靠"相电流越过阈值"判机械死点，
            //   两侧丝杠必须同时顶到各自的死点，否则会把平台拧歪。
            //   主线永远是「推到靠块 → 设为原点」。
        }
    }
}
