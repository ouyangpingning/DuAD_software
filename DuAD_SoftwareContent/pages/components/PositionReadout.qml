import QtQuick
import QtQuick.Layouts
import DuAD_Software

/*
    位置读数 —— 工作区缩略图（雷达图）+ X/Y 大号读数。

    **上下两段**（2026-09-28 改，跟用户手绘稿的"二轴相机平台状态"小面板一致）：
      上 = 工作区缩略图（当前点 / 目标叉），**一眼看出"离边界还有多远、目标在哪个角"**；
      下 = 两个大号 X/Y 指标 + 图例。
    为什么从"左右两列"改成"上下两段"：这个组件现在住在**可折叠的窄侧抽屉**里
    （展开宽度 ~236px，见 StagePage 的 StatusDrawer），横着排在那种宽度下两边都放不下。
    ⚠ 改布局时**一起改了断言**（tests/test_stage_page.py 11c3）：原来量的是
      "缩略图在左、读数在右"，现在量"缩略图在上、读数在下"。

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
    // 无卡片模式（2026-09-28）：不画自己的圆角背景、也不留 16px 内边距。
    property bool flat: false

    // 两列各自的几何（给布局断言用；不参与绘制逻辑）
    readonly property real _mapX: map.x
    readonly property real _numsX: nums.x
    readonly property real _mapY: map.y
    readonly property real _numsY: nums.y
    readonly property real _mapH: map.height
    readonly property real _numsH: nums.height
    /* 缩略图边长：填满抽屉宽度（上下布局，宽度是父层给的，不依赖内部布局），
       并夹在 [120, 168] —— 下限保证看得清，上限避免它把读数挤到抽屉外面去。
       ⚠ **故意写常数上限、不绑 `nums.implicitHeight`**：那会构成 QML 绑定环
         （图变高 → 文字折行变化 → 再变高…），Qt 会报 "Binding loop detected"
         并冻结其中一个值，表现是布局偶尔跳一下、极难查。 */
    readonly property real _mapSide: Math.min(150, Math.max(120, width - 12))

    implicitWidth: 300
    implicitHeight: content.implicitHeight + (flat ? 0 : 32)

    readonly property color _numberColor: live ? Colors.textPrimary : Colors.textPlaceholder

    // ============================================================
    // 本体
    // ============================================================
    Rectangle {
        anchors.fill: parent
        radius: 12
        color: root.flat ? "transparent" : Colors.contentBg

        GridLayout {
            id: content
            // 上下两段（见文件头）；columns 一改就是"横排 / 竖排"两种形态
            columns: 1
            rowSpacing: 10
            columnSpacing: 16
            anchors {
                left: parent.left; right: parent.right; top: parent.top
                margins: root.flat ? 0 : 16
            }

            // ── 左列：工作区缩略图（雷达图）────────────────────
            Item {
                id: map
                Layout.preferredWidth: root._mapSide
                Layout.preferredHeight: root._mapSide
                Layout.alignment: Qt.AlignHCenter | Qt.AlignTop

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

            // ── 下段：指标 + 图例 ──────────────────────────────
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

                // 图例：一行三个（原来三行，占 54px —— 抽屉的高度跟预览行绑着，
                // 多出来的 36px 会把内容挤出可视区，实测过）
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 12

                    RowLayout {
                        spacing: 5
                        Rectangle { width: 10; height: 10; radius: 5; color: Colors.textPrimary }
                        Text {
                            text: qsTr("当前")
                            font.pixelSize: 11
                            color: Colors.textSecondary
                        }
                    }
                    RowLayout {
                        spacing: 5
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
                            text: qsTr("目标")
                            font.pixelSize: 11
                            color: Colors.textSecondary
                        }
                    }
                    RowLayout {
                        spacing: 5
                        Rectangle {
                            width: 10; height: 10; radius: 2
                            color: Colors.statusConnected
                            opacity: 0.85
                        }
                        Text {
                            // ⚠ 只写"零点"：抽屉展开后内容区只有 ~182px，
                            //   "零点（X 左 · Y 外）"会把这一行顶出边界（几何守卫抓得到）。
                            //   具体在哪个角由缩略图上那个绿方块表示（stage_bridge.py 的 DATUM_CORNER）。
                            text: qsTr("零点")
                            font.pixelSize: 11
                            color: Colors.textSecondary
                        }
                    }
                    Item { Layout.fillWidth: true }
                }

                // ⚠ 2026-09-28 精简：这里原来还有「工作区 360 × 360 mm / 0,0 → 360,360 /
                //   ↑+Y 向里 →+X 向右」三行 —— 全删了。理由：
                //     · 工作区的**数值**在「平台设置」里有输入框，缩略图本身也画着边界；
                //     · 方向说明在实时预览的表头上已经有一份（同一事实显示两处，见 AGENTS 第 19 条）；
                //     · 少三行后这一列矮 68px，X/Y 的「停止」才落进首屏（"点动时手要够得着"）。
                Item { Layout.fillHeight: true }
            }
        }
    }
}
