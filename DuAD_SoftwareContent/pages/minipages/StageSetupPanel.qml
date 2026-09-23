import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software
import "../components"

/*
    平台设置面板 — 网络、工作区（行程）、速度。默认折叠：这些调一次就不动。

    为什么工作区放在这里、而且要显眼：
      固件对 `g0`/`move` 是 fail-closed 的 —— **不设行程就一律拒绝移动**。
      所以连上之后必须把工作区推下去，否则用户点任何方向键都是"被拒绝"。
      Bridge 在认证通过后会自动下发一次，这里负责让用户能改。
*/
Item {
    id: root
    objectName: "setupPanel"    // 页面测试量卡片顺序用

    property bool expanded: false

    // 网络
    property string host: ""
    property int port: 3333
    property string token: ""

    // 工作区（mm）
    property real wsXMin: 0
    property real wsYMin: 0
    property real wsXMax: 360
    property real wsYMax: 360

    // 速度
    property int rpm: 1200
    property int acc: 1

    signal applyRequested()
    // 拖动滑块松手就发（不必等「应用设置」）—— 用户拖动时期待的就是立即生效
    signal speedChanged(int rpm, int acc)

    // ============================================================
    // 输入框里的**当前内容**（不是 bridge 里已保存的值）
    // ============================================================
    // ⚠ 页面点"平台卡片"连接时必须用这几个，而不是上面的 host/token。
    //   否则会出现：用户把 IP 改了、没点「应用设置」、直接点卡片 →
    //   连的还是旧地址 → 表现成"我明明改了却没用"。
    //   hostRow.text 的绑定会在用户输入时断开，所以读到的总是界面上的真实内容。
    readonly property string fieldHost: hostRow.text.trim()
    readonly property string fieldToken: tokenRow.text.trim()
    readonly property int fieldPort: {
        var p = parseInt(portRow.text)
        return isNaN(p) ? 3333 : p
    }
    readonly property bool fieldsFilled: fieldHost.length > 0 && fieldToken.length > 0

    implicitWidth: 460
    implicitHeight: expanded ? contentLayout.implicitHeight + 32 : 0
    clip: true

    Behavior on implicitHeight {
        NumberAnimation { duration: 250; easing.type: Easing.InOutCubic }
    }

    Rectangle {
        anchors.fill: parent
        radius: 12
        color: Colors.contentBg

        ColumnLayout {
            id: contentLayout
            spacing: 10
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 24 }

            Text {
                text: qsTr("平台设置")
                font.pixelSize: 14
                font.bold: true
                color: Colors.textPrimary
            }
            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder }

            SectionHeader { text: qsTr("网络") }

            InputRow {
                id: hostRow
                objectName: "hostField"
                label: qsTr("板子 IP")
                text: root.host
                placeholderText: "192.168.1.42"
            }
            InputRow {
                id: portRow
                label: qsTr("端口")
                text: String(root.port)
                placeholderText: "3333"
            }
            InputRow {
                id: tokenRow
                objectName: "tokenField"
                label: qsTr("口令")
                text: root.token
                password: true
                placeholderText: qsTr("8 位十六进制")
            }

            Text {
                Layout.fillWidth: true
                text: qsTr("三个值都在板子的 USB 控制台上敲 net 就能看到，板子会直接打印出来。")
                font.pixelSize: 11
                color: Colors.textPlaceholder
                wrapMode: Text.Wrap
            }

            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder }

            SectionHeader { text: qsTr("工作区（台面行程，mm）") }

            Text {
                Layout.fillWidth: true
                text: qsTr("必须按实际台面量准后填写：固件不设行程就拒绝一切绝对移动，"
                           + "填大了则会在撞到机械限位前不刹车。")
                font.pixelSize: 11
                color: Colors.textPlaceholder
                wrapMode: Text.Wrap
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                InputRow {
                    id: xminRow; Layout.fillWidth: true
                    label: qsTr("X 小"); text: root.wsXMin.toFixed(1)
                }
                InputRow {
                    id: xmaxRow; Layout.fillWidth: true
                    label: qsTr("X 大"); text: root.wsXMax.toFixed(1)
                }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                InputRow {
                    id: yminRow; Layout.fillWidth: true
                    label: qsTr("Y 小"); text: root.wsYMin.toFixed(1)
                }
                InputRow {
                    id: ymaxRow; Layout.fillWidth: true
                    label: qsTr("Y 大"); text: root.wsYMax.toFixed(1)
                }
            }

            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder }

            SectionHeader { text: qsTr("速度") }

            SliderRow {
                id: rpmRow
                objectName: "rpmSliderRow"   // 页面测试量它的量程（见 11c6）
                label: qsTr("转速")
                // ⚠ 量程**从 StageBridge 拿**，别在这儿写死数字：
                //   固件上限 6000、界面顶 3000（扭矩随转速掉得快，见 stage_bridge.py 的说明）。
                //   以前这里硬编码 1200、而 bridge 里另有一个没人用的 UI_MAX_RPM —— 就是
                //   "同一个事实两份"那个坑：改了常量界面不动。
                from: 60; to: StageBridge.uiMaxRpm
                sliderValue: root.rpm
                suffix: "rpm"; decimals: 0
                snapTicks: [60, 300, 600, 1200, 2000, StageBridge.uiMaxRpm]
                wheelStep: 50
                resetValue: 1200
                onReleased: root.speedChanged(rpmRow.sliderValue, accRow.sliderValue)
            }
            SliderRow {
                id: accRow
                objectName: "accSliderRow"   // 页面测试量刻度分布（见 11c7）
                label: qsTr("加减速")
                // ⚠ 上限是 200 不是 255：固件组帧时 `if (acc > 200) return -1;`
                //   直接拒帧，放在 255 会让 201~255 这一段"拖了没反应"。
                from: 1; to: StageBridge.uiMaxAcc
                sliderValue: root.acc
                suffix: ""; decimals: 0
                // ★ 对数刻度（2026-09-13 用户要求"1-50 之间再细分"）：
                //   加减速是 0~200 的档位，低端手感差异大（1 和 2 差很多）、
                //   高端几乎无感（190 和 200 分不出来）。线性轨道把 1~200 平铺在一条
                //   ~200px 的轨道上，1~50 只有 25% 行程 → 拖不准。
                //   改成对数后 1~50 占约 74% 行程（测试 11c7 断言 ≥60%）。
                logScale: true
                // 刻度按**等比**排（每个约前一个的 1.35 倍）—— 这样在对数轨道上间距均匀，
                // 刻度线不会挤成一坨。要精确到任意整数：点右边的数字直接输入。
                snapTicks: [1, 2, 3, 4, 5, 7, 9, 12, 16, 21, 28, 37, 50, 67, 90, 120, 160, 200]
                // 滚轮步长随值自适应：低端 1 格 1 步（能慢慢摸到 2、3、4），
                // 中段 2、高端 5 —— 否则要么低端太粗、要么高端要滚半天。
                wheelStep: accRow.sliderValue <= 20 ? 1
                         : (accRow.sliderValue <= 60 ? 2 : 5)
                resetValue: 1
                onReleased: root.speedChanged(rpmRow.sliderValue, accRow.sliderValue)
            }

            Text {
                Layout.fillWidth: true
                text: qsTr("转速 = 巡航速度；加减速 = 起步/停下的猛烈程度，"
                           + "数值越大越猛（1 最柔和、200 最猛，默认 1）。")
                      + " " + qsTr("拖动松手即生效，不用点「应用设置」。")
                      + "  " + qsTr("参考：走 100mm 约")
                      + " " + (100 * 11.25 / 360 / Math.max(1, rpmRow.sliderValue) * 60).toFixed(1)
                      + " " + qsTr("秒（只按转速算，不含加减速耗时）")
                font.pixelSize: 10
                color: Colors.textPlaceholder
                wrapMode: Text.Wrap
            }

            // 对数刻度这件事必须说一句：不然用户看到"拖一点点就跳到 4、再拖才到 5"
            // 会以为滑块坏了（其实是刻意的 —— 低端值差异大，值得多给行程）。
            Text {
                Layout.fillWidth: true
                text: qsTr("加减速是对数刻度：一半行程就覆盖了 1~15，低端能一点点调"
                           + "（1 和 2 的手感差得很远），高端 190/200 几乎无感所以挤在一起。"
                           + "滚轮在 20 以下 1 格 1 步；要精确到任意整数，直接点右边的数字输入。")
                font.pixelSize: 10
                color: Colors.textPlaceholder
                wrapMode: Text.Wrap
            }

            // 高速的真实代价（2026-09-13 用户问"能不能到 3000rpm"时补的）。
            // ⚠ 必须写：不然用户把滑块拉到 3000 发现"没快多少/反而丢步"，会以为参数坏了。
            Text {
                Layout.fillWidth: true
                text: qsTr("⚠ 转速越高，能带的负载越小：3000rpm = 电机 50 圈/秒，"
                           + "1.8° 电机就是 10kHz 电频率，24V 下相电流来不及建立，扭矩掉得很快。"
                           + "3000rpm 在皮带上传动 = 1600mm/s、台面约 800mm/s（360mm 行程 0.45 秒跑完）——"
                           + "这个速度基本只适合空载。相机平台常用 600~1200rpm。\n"
                           + "⚠ 更要紧的是：加速太猛 + 皮带偏松 = 跳齿，而编码器在电机轴上，"
                           + "皮带跳齿驱动器是看不见的（它只会认为「我转到位了」）→ 坐标悄悄错掉。"
                           + "提速请一次加一档、跑长距离看台面有没有少走。")
                font.pixelSize: 10
                color: Colors.textPlaceholder
                wrapMode: Text.Wrap
            }

            // 这条必须写出来：否则用户拿 1mm 点动去试速度，怎么试都"没反应"，
            // 然后合理地认为这个参数坏了 —— 实际上是行程太短，看不出差别。
            Text {
                Layout.fillWidth: true
                text: qsTr("⚠ 步进点动（0.1~10mm）看不出速度差别：行程只有几十毫秒，"
                           + "基本全被驱动器的加减速斜坡吃掉了。要验证转速，请用「绝对定位」"
                           + "走一段长距离（比如 200mm），或展开「诊断」看日志里下发的指令"
                           + "（移动命令末尾两个数就是 rpm 和 acc）。")
                font.pixelSize: 10
                color: Colors.textPlaceholder
                wrapMode: Text.Wrap
            }

            Button {
                Layout.fillWidth: true
                implicitHeight: 34
                text: qsTr("应用设置")
                onClicked: root.applyRequested()
            }

            // ── 退出时回到零点（2026-09-13）──────────────────────────
            //   用户提的用法：别人只用上位机 → 退出时把台面开回零点角，
            //   下次开机就能用"还在不在那个角上"核对"我们不在的时候被动过没有"。
            SectionHeader { text: qsTr("关机行为") }

            SwitchRow {
                objectName: "parkOnExit"
                label: qsTr("退出时回到零点")
                on: StageBridge.parkOnExit
                onToggled: StageBridge.setParkOnExit(!StageBridge.parkOnExit)
            }

            // ⚠ 必须写清楚"它不是保鲜手段" —— 否则以后会有人以为
            //   "不回零点基准就丢了"，从而不敢关这个开关、也不敢关程序。
            Text {
                Layout.fillWidth: true
                text: qsTr("把台面开回零点角再退出，好处是下次开机能核对台面有没有被动过"
                           + "（差得超过 0.5mm 就会在日志里提示）。"
                           + "⚠ 它不是为了「保住基准」：驱动器只要不断电就一直数着位置，"
                           + "上位机什么时候关、断线、甚至崩掉，都不影响坐标。"
                           + "会丢基准的只有两件事 —— ① 板子/24V 断电（单圈编码器丢多圈位置，"
                           + "这时会提示重新立基准）② 有人用手推动了台面。")
                font.pixelSize: 10
                color: Colors.textPlaceholder
                wrapMode: Text.Wrap
            }
        }
    }
}
