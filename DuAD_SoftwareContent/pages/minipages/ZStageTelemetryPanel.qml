import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software
import "../components"

/*
    Z 轴遥测卡 —— 大号高度读数 + 偏斜 + 一排小字状态 + 故障提示。

    为什么单独做一张卡（而不是塞进设备卡片）：
      设备卡片是"点一下连接/断开"的**按钮**，点哪儿都会触发；把实时读数放进去，
      用户想选数字就会误触断开。所以读数和连接动作必须分开（与 X/Y 那张卡同样的分工）。

    ⚠ 两条本项目的硬约定：
      1. **数据源一律是带 notify 的 Property**（`root.posZ` 由页面绑定 `ZStageBridge.z`），
         卡里不出现任何 `ZStageBridge.xxx()` 调用 —— 绑定里调函数没有依赖追踪，
         值会冻在创建那一刻（AGENTS.md 第 6 条坑）。
      2. **偏斜不在这里判"超没超限"**：阈值在固件里（`zset skew`，出厂 0.5mm）。
         界面只把固件**已经报出来的** `fault` 着色，不拿一个自己的数字去猜 ——
         否则两边迟早不一致，而且是静默的（AGENTS.md 第 19 条）。

    ⚠ `Item` 的 `z` 是 **FINAL 属性**（堆叠顺序），不能借来当"Z 轴高度" ——
      写成 `property real z: 0` 会让**整个页面加载失败**：
      `Cannot override FINAL property`。所以这里的属性叫 `posZ`
      （与 StageJogPanel 的 `posX`/`posY` 同一套命名）。
      这个错是 `tests/test_stage_page.py` 第 1 步（页面加载）当场抓到的。
*/
Item {
    id: root
    objectName: "zTelemetryPanel"

    // ============================================================
    // 双丝杠示意（用户手绘稿里 Z 状态面板上画的那个）
    // ============================================================
    // 它回答一个数字答不了的问题：**这两根丝杠现在各在什么高度、平台因此歪了多少**。
    // ⚠ 倾角是**放大**画的：真实偏斜 0.02mm 对应的角度肉眼根本看不见，
    //   所以角上标了「示意」，并在下面照旧给出真实数字 —— 不标就是骗人。
    component ScrewSchematic: Item {
        id: sch
        property real skew: 0            // 两侧高差（mm）
        property real posZ: 0            // 当前高度（mm）
        property real stroke: 250        // 机械行程（mm），把高度归一化到画面里
        property bool live: true

        implicitWidth: 168
        implicitHeight: 108

        // 两根丝杠
        Repeater {
            model: 2
            delegate: Rectangle {
                x: index === 0 ? 20 : sch.width - 23
                width: 3
                height: sch.height - 12
                radius: 1.5
                color: sch.live ? Colors.cardBorder : Colors.pageBg
            }
        }
        // 螺纹装饰（隔 9px 一道短横）：让人一眼看出"这是丝杠"而不是两根柱子。
        // ⚠ 逐条锚定在 [14, width-14] 之内，不要用 Row + spacing 去凑总宽 ——
        //   那么写实测会超出抽屉内容区（几何守卫当场抓到，右边界 189 > 卡片 182）。
        Repeater {
            model: Math.floor((sch.height - 12) / 9)
            delegate: Item {
                x: 14
                y: index * 9 + 2
                width: Math.max(0, sch.width - 28)
                height: 1
                Repeater {
                    model: 2
                    delegate: Rectangle {
                        y: 0
                        x: index === 0 ? 0 : parent.width - width
                        width: 13; height: 1
                        color: sch.live ? Colors.cardBorder : Colors.pageBg
                    }
                }
            }
        }
        // 平台（高度跟着 posZ 走，倾角跟着 skew 走）
        Rectangle {
            id: plate
            anchors.horizontalCenter: parent.horizontalCenter
            width: sch.width - 16
            height: 6
            radius: 3
            color: sch.live ? Colors.interactivePressed : Colors.cardBorder
            readonly property real _t: Math.max(0, Math.min(1,
                sch.posZ / Math.max(1, sch.stroke)))
            y: (sch.height - 12) - _t * (sch.height - 40)
            // ⚠ 放大显示（见上面说明）：0.02mm 的真实倾角是 0.1° 量级，肉眼看不出来。
            //   用 sqrt 映射而不是线性放大：线性系数要大到 300 才看得见 0.02，
            //   而 0.5mm 的真实偏斜（已经是故障级）就会转出屏幕。
            //   sqrt 让"很小也能看出一点点、大的一眼就看得出歪"。
            rotation: {
                var k = Math.sqrt(Math.abs(sch.skew)) * 14
                return Math.max(-10, Math.min(10, sch.skew < 0 ? -k : k))
            }
            transformOrigin: Item.Center
            Behavior on y { NumberAnimation { duration: 150; easing.type: Easing.OutCubic } }
            Behavior on rotation { NumberAnimation { duration: 150; easing.type: Easing.OutCubic } }
        }
        // 底部零点基准线
        Rectangle {
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
            height: 1
            color: Colors.cardBorder
        }
        Text {
            anchors { right: parent.right; bottom: parent.bottom; bottomMargin: 4 }
            text: qsTr("示意")
            font.pixelSize: 9
            color: Colors.textPlaceholder
        }
    }

    // ============================================================
    // 公有 API（全部由页面绑定到 ZStageBridge）
    // ============================================================
    property bool connected: false
    property real posZ: 0
    property real skew: 0
    property string fault: ""
    property string faultText: ""
    // ⚠ 只给"丝杠示意"归一化高度用（不再显示成文字 —— 那行说明 2026-09-28 删掉了）
    property real firmwareLo: 0
    property real firmwareHi: 250

    // 未连接时读数没有意义，显示占位符而不是上一块的残留数字
    readonly property string _zText: root.connected ? root.posZ.toFixed(2) : "--.--"
    readonly property string _skewText: root.connected ? root.skew.toFixed(2) : "--.--"

    // 无卡片模式：只画内容（大号读数当"列头"）
    property bool flat: false
    // 竖排：住进 StagePage 的窄侧抽屉时用（宽度只有 ~236px，横排两边都放不下）
    property bool vertical: false

    implicitWidth: 460
    implicitHeight: body.implicitHeight + 32

    // flat（住在折叠节里）时整块透明：不画底、不画描边、不投影 —— 否则会变成
    // "卡片套卡片"。非 flat 时才是正常卡片。
    CardSurface {
        anchors.fill: parent
        shadowed: !root.flat
        color: root.flat ? "transparent" : Colors.cardBg
        borderColor: root.flat ? "transparent" : Colors.cardBorderStrong

        ColumnLayout {
            id: body
            objectName: "zTelemetryBody"
            spacing: 10
            anchors {
                left: parent.left; right: parent.right; top: parent.top
                margins: root.flat ? 0 : 24
            }

            // ── 丝杠示意（只在竖排/抽屉形态下显示）──────────
            ScrewSchematic {
                Layout.alignment: Qt.AlignHCenter
                // 抽屉可能比示意窄：允许它收缩（内部尺寸都按 sch.width 算）
                Layout.fillWidth: true
                Layout.maximumWidth: 168
                visible: root.vertical
                live: root.connected
                skew: root.skew
                posZ: root.posZ
                stroke: Math.max(1, root.firmwareHi - root.firmwareLo)
            }

            // ── 大号高度 + 偏斜 ──────────────────────────────
            GridLayout {
                // 竖排时上下叠（窄抽屉里横排放不下）
                columns: root.vertical ? 1 : 2
                Layout.fillWidth: true
                rowSpacing: 6
                columnSpacing: 12

                ColumnLayout {
                    spacing: 2
                    Text {
                        text: qsTr("Z 轴高度")
                        font.pixelSize: 11
                        color: Colors.textSecondary
                    }
                    RowLayout {
                        spacing: 4
                        Text {
                            objectName: "zReadout"
                            text: root._zText
                            font.pixelSize: 30
                            font.bold: true
                            font.family: "monospace"
                            color: root.connected ? Colors.textPrimary : Colors.textPlaceholder
                        }
                        Text {
                            text: "mm"
                            font.pixelSize: 13
                            color: Colors.textPlaceholder
                            Layout.alignment: Qt.AlignBottom
                            Layout.bottomMargin: 5
                        }
                    }
                }

                Item { Layout.fillWidth: true; visible: !root.vertical }

                ColumnLayout {
                    spacing: 2
                    Text {
                        Layout.alignment: root.vertical ? Qt.AlignLeft : Qt.AlignRight
                        text: qsTr("两侧偏斜")
                        font.pixelSize: 11
                        color: Colors.textSecondary
                    }
                    RowLayout {
                        spacing: 4
                        Layout.alignment: root.vertical ? Qt.AlignLeft : Qt.AlignRight
                        Text {
                            objectName: "zSkewReadout"
                            text: root._skewText
                            font.pixelSize: 20
                            font.bold: true
                            font.family: "monospace"
                            // 只有固件**自己报出** skew 故障时才标红（见文件头第 2 条）
                            color: !root.connected ? Colors.textPlaceholder
                                 : (root.fault === "skew" ? Colors.statusDisconnected
                                                          : Colors.textPrimary)
                        }
                        Text {
                            text: "mm"
                            font.pixelSize: 11
                            color: Colors.textPlaceholder
                            Layout.alignment: Qt.AlignBottom
                            Layout.bottomMargin: 3
                        }
                    }
                }
            }

            // ── 故障（固件报出来的才显示）────────────────────
            Rectangle {
                Layout.fillWidth: true
                visible: root.connected && root.faultText.length > 0
                implicitHeight: faultCol.implicitHeight + 16
                radius: 6
                color: Colors.cardDangerBg

                ColumnLayout {
                    id: faultCol
                    anchors { fill: parent; margins: 8 }
                    spacing: 4

                    Text {
                        objectName: "zFaultText"
                        Layout.fillWidth: true
                        text: "⚠ " + root.faultText
                        font.pixelSize: 12
                        color: Colors.statusDisconnected
                        wrapMode: Text.Wrap
                    }
                    Text {
                        Layout.fillWidth: true
                        text: qsTr("处置：先点「停止」（急停会同时清掉故障锁存），"
                                   + "再把平台推到靠块重新「设为原点」。")
                        font.pixelSize: 10
                        color: Colors.textSecondary
                        wrapMode: Text.Wrap
                    }
                }
            }
        }
    }
}
