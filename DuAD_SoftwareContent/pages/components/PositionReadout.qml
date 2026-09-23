import QtQuick
import QtQuick.Layouts
import DuAD_Software

/*
    位置读数 —— 工作区缩略图（雷达图）+ X/Y 大号读数。

    **左右两列**（2026-09-13 用户要求）：
      左列 = 工作区缩略图（当前点 / 目标叉），**一眼看出"离边界还有多远、目标在哪个角"**；
      右列 = 两个大号 X/Y 指标 + 图例 + 工作区范围 + 坐标方向说明。
    竖着排比"读数占一整行、图缩在下面"省一半高度，而且指标和图挨着，对得上号。

    坐标方向（台面是水平面，俯视）：+X = 右、+Y = 向里 → 图上 Y 轴**朝上**。
*/
Item {
    id: root
    // 供页面测试用（量"两列并排"这条布局硬约束，见 tests/test_stage_page.py 11c3）
    objectName: "posReadout"

    // ============================================================
    // 公有 API
    // ============================================================
    property real posX: 0
    property real posY: 0
    property real targetX: NaN      // NaN = 当前没有目标
    property real targetY: NaN
    property real wsXMin: 0
    property real wsYMin: 0
    property real wsXMax: 360
    property real wsYMax: 360
    property bool live: true        // false = 数据不可信（未连接），整体变灰

    // 两列各自的几何（给布局断言用；不参与绘制逻辑）
    readonly property real _mapX: map.x
    readonly property real _numsX: nums.x
    readonly property real _mapH: map.height
    readonly property real _numsH: nums.height
    /* 缩略图边长（2026-09-13 用户要求"放大到和右侧高度一致"）。
       216 是**量出来的**：右列（两个 26px 大字 + 三条图例 + 三行说明）实测 228px 高，
       取 216 让两边差 ≤12px（测试 11c3 卡 ±30px）。
       ⚠ **故意写常数、不绑 `nums.implicitHeight`**：那会构成 QML 绑定环 ——
         图变宽 → 右列变窄 → 文字多折一行 → 右列变高 → 图再变高…（Qt 会报
         "Binding loop detected" 并冻结其中一个值，表现是布局偶尔跳一下、很难查）。
       `width` 是**父层给的**（页面里 fillWidth），不依赖内部布局，所以拿它算上限是安全的。
       下限 120 / 上限 45% 是为了窄屏退化成单列时右列文字不被挤没。 */
    readonly property real _mapSide: Math.min(216, Math.max(120, width * 0.45))

    implicitWidth: 300
    implicitHeight: content.implicitHeight + 32

    readonly property color _numberColor: live ? Colors.textPrimary : Colors.textPlaceholder

    // ============================================================
    // 本体
    // ============================================================
    Rectangle {
        anchors.fill: parent
        radius: 12
        color: Colors.contentBg

        RowLayout {
            id: content
            spacing: 16
            anchors {
                left: parent.left; right: parent.right; top: parent.top
                margins: 16
            }

            // ── 左列：工作区缩略图（雷达图）────────────────────
            Item {
                id: map
                Layout.preferredWidth: root._mapSide
                Layout.preferredHeight: root._mapSide
                Layout.alignment: Qt.AlignTop

                // 台面边框
                Rectangle {
                    id: field
                    anchors.fill: parent
                    anchors.margins: 6
                    color: "transparent"
                    border { width: 1; color: Colors.cardBorder }
                    radius: 4
                }

                // 十字参考线（过中心，帮助判断象限）
                Rectangle {
                    x: field.x + field.width / 2 - 0.5
                    y: field.y; width: 1; height: field.height
                    color: Colors.cardBorder; opacity: 0.5
                }
                Rectangle {
                    x: field.x; y: field.y + field.height / 2 - 0.5
                    width: field.width; height: 1
                    color: Colors.cardBorder; opacity: 0.5
                }

                // 原点角标记（零点写死在 X 左 + Y 外 = 图上的左下角）
                //   为什么值得画：零点只有一个、而且是**固定的**，
                //   画出来用户就知道"这个角是家"，也顺手代替了原来那个角落下拉框。
                Item {
                    x: field.x - width / 2
                    y: field.y + field.height - height / 2
                    width: 12; height: 12
                    Rectangle {
                        anchors.centerIn: parent
                        width: 12; height: 12; radius: 2
                        color: Colors.statusConnected
                        opacity: 0.85
                    }
                }

                // 目标位置（叉）
                Item {
                    visible: root.live && !isNaN(root.targetX) && !isNaN(root.targetY)
                    property real fx: Math.max(0, Math.min(1,
                        (root.targetX - root.wsXMin) / Math.max(1e-6, root.wsXMax - root.wsXMin)))
                    property real fy: Math.max(0, Math.min(1,
                        (root.targetY - root.wsYMin) / Math.max(1e-6, root.wsYMax - root.wsYMin)))
                    // ⚠ 屏幕 Y 是反的：+Y 向里 = 图上朝上
                    x: field.x + fx * field.width - width / 2
                    y: field.y + (1 - fy) * field.height - height / 2
                    width: 14; height: 14
                    Rectangle {
                        anchors.centerIn: parent
                        width: 14; height: 2; rotation: 45
                        color: Colors.statusDisconnected
                    }
                    Rectangle {
                        anchors.centerIn: parent
                        width: 14; height: 2; rotation: -45
                        color: Colors.statusDisconnected
                    }
                }

                // 当前位置（实心点 + 光晕）
                Item {
                    visible: root.live
                    property real fx: Math.max(0, Math.min(1,
                        (root.posX - root.wsXMin) / Math.max(1e-6, root.wsXMax - root.wsXMin)))
                    property real fy: Math.max(0, Math.min(1,
                        (root.posY - root.wsYMin) / Math.max(1e-6, root.wsYMax - root.wsYMin)))
                    x: field.x + fx * field.width - width / 2
                    y: field.y + (1 - fy) * field.height - height / 2
                    width: 22; height: 22

                    Rectangle {
                        anchors.centerIn: parent
                        width: 22; height: 22; radius: 11
                        color: Colors.interactivePressed
                        opacity: 0.35
                    }
                    Rectangle {
                        anchors.centerIn: parent
                        width: 10; height: 10; radius: 5
                        color: Colors.textPrimary
                    }
                    Behavior on x { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
                    Behavior on y { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
                }
            }

            // ── 右列：指标 + 图例 + 文字 ───────────────────────
            ColumnLayout {
                id: nums
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                spacing: 8

                // X / Y 大号读数（竖排：X 上、Y 下）
                Repeater {
                    model: [
                        { axis: "X", value: root.posX, hint: qsTr("+右 −左") },
                        { axis: "Y", value: root.posY, hint: qsTr("+里 −外") }
                    ]
                    delegate: RowLayout {
                        Layout.fillWidth: true
                        spacing: 6

                        Text {
                            text: modelData.axis
                            font.pixelSize: 13
                            font.bold: true
                            color: Colors.textSecondary
                            Layout.alignment: Qt.AlignBottom
                            Layout.bottomMargin: 5
                        }
                        Text {
                            // 固定 2 位小数：位数变化时右边的单位不跳
                            text: modelData.value.toFixed(2)
                            Layout.preferredWidth: 96
                            horizontalAlignment: Text.AlignRight
                            font.pixelSize: 26
                            font.bold: true
                            // 等宽字体让数字滚动时不抖（取不到就退回默认字体）
                            font.family: "monospace"
                            color: root._numberColor
                        }
                        Text {
                            text: "mm"
                            font.pixelSize: 12
                            color: Colors.textPlaceholder
                            Layout.alignment: Qt.AlignBottom
                            Layout.bottomMargin: 4
                        }
                        Text {
                            text: modelData.hint
                            font.pixelSize: 10
                            color: Colors.textPlaceholder
                            Layout.alignment: Qt.AlignBottom
                            Layout.bottomMargin: 5
                        }
                        Item { Layout.fillWidth: true }
                    }
                }

                Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder }

                // 图例
                RowLayout {
                    spacing: 6
                    Rectangle {
                        width: 10; height: 10; radius: 5
                        color: Colors.textPrimary
                    }
                    Text {
                        text: qsTr("当前位置")
                        font.pixelSize: 11
                        color: Colors.textSecondary
                    }
                    Item { Layout.fillWidth: true }
                }
                RowLayout {
                    spacing: 6
                    Item {
                        width: 10; height: 10
                        Rectangle {
                            anchors.centerIn: parent
                            width: 10; height: 2; rotation: 45
                            color: Colors.statusDisconnected
                        }
                        Rectangle {
                            anchors.centerIn: parent
                            width: 10; height: 2; rotation: -45
                            color: Colors.statusDisconnected
                        }
                    }
                    Text {
                        text: qsTr("目标位置")
                        font.pixelSize: 11
                        color: Colors.textSecondary
                    }
                    Item { Layout.fillWidth: true }
                }
                RowLayout {
                    spacing: 6
                    Rectangle {
                        width: 10; height: 10; radius: 2
                        color: Colors.statusConnected
                        opacity: 0.85
                    }
                    Text {
                        // 零点角写死（见 stage_bridge.py 的 DATUM_CORNER）
                        text: qsTr("零点（X 左 · Y 外）")
                        font.pixelSize: 11
                        color: Colors.textSecondary
                    }
                    Item { Layout.fillWidth: true }
                }

                Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder }

                Text {
                    text: qsTr("工作区") + "  " + (root.wsXMax - root.wsXMin).toFixed(0)
                          + " × " + (root.wsYMax - root.wsYMin).toFixed(0) + " mm"
                    font.pixelSize: 11
                    color: Colors.textPlaceholder
                }
                Text {
                    text: (root.wsXMin.toFixed(0) + "," + root.wsYMin.toFixed(0)) + " → "
                          + (root.wsXMax.toFixed(0) + "," + root.wsYMax.toFixed(0))
                    font.pixelSize: 10
                    color: Colors.textPlaceholder
                }
                Text {
                    text: qsTr("↑ +Y 向里    → +X 向右")
                    font.pixelSize: 10
                    color: Colors.textPlaceholder
                }
                Item { Layout.fillHeight: true }
            }
        }
    }
}
