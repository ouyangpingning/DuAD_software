import QtQuick
import QtQuick.Layouts
import DuAD_Software

/*
    十字方向键 —— 二轴平台的步进式点动。

    为什么是"步进式"而不是"按住连续走"：
      单次往返 = WiFi + 板子串口 + 驱动器应答 ≈ 20~60ms，**速度上限卡在链路而不是电机**。
      靠上位机连发小增量做不出平滑手感，所以每按一次走一个**明确的步长**。
      好处是天然安全：丢包最多多走一格，不会失控。
      （要平滑必须先在固件里加"速度模式 + 无指令自动停"的看门狗，见 docs/17 §5.3。）

    坐标方向（台面是水平面，俯视）：
      +X = 右、+Y = 向里 → 所以在画面上 **Y+ 朝上**。
      页面里另有文字标注，不让人猜。
*/
Item {
    id: root

    // ============================================================
    // 公有 API
    // ============================================================
    property real step: 1.0            // 步长，mm（由外部选择器给）
    property bool interactive: true    // false = 变灰不响应
    signal jog(real dx, real dy)       // 单位 mm，已经是"带方向和步长"的向量

    // 单个方向键的边长。2026-09-28 从 74 收到 60：整个十字从 234px 矮到 188px，
    // X/Y 列的「停止 / 设为原点」才落进 1080p 首屏（点动是高频动作，急停要够得着）。
    // 60 仍是够大的点击目标（Z 列那对「向上/向下」按钮就是 48px 高，同一量级）。
    property int cell: 60
    readonly property int _pad: cell * 3 + 8      // 3 格 + 两道 4px 间距

    implicitWidth: _pad
    implicitHeight: _pad

    // ============================================================
    // 按住连发
    // ============================================================
    // 手感：按下立刻走一格（要即时反馈），停 400ms 后开始每 150ms 连发。
    // 150ms 不是随便定的：一次往返要 20~60ms，再加板子的非阻塞 move 排队，
    // 比这更快只会把命令堆在通道里，不会走得更快。
    property int _dxPending: 0
    property int _dyPending: 0

    Timer {
        id: holdDelay
        interval: 400
        repeat: false
        onTriggered: repeatTimer.start()
    }

    Timer {
        id: repeatTimer
        interval: 150
        repeat: true
        onTriggered: {
            if (!root.interactive) { stop(); return }
            root.jog(root._dxPending * root.step, root._dyPending * root.step)
        }
    }

    function _stopRepeat() {
        holdDelay.stop()
        repeatTimer.stop()
    }

    // ============================================================
    // 单个方向键
    // ============================================================
    component DirKey: Rectangle {
        id: key
        // ⚠ 2026-09-28：原来的 `glyph` 是 "▲"/"◀"/"▶"/"▼" **文本字形** ——
        //   依赖 wqy-microhei 收录这些码位，渲染出来偏小、和方框不居中对齐，
        //   而且不能单独染色。现在改成图标（同一套 24×24 单色 SVG）。
        property url iconSource: ""
        property int dx: 0
        property int dy: 0
        property string tip: ""

        radius: 10
        color: {
            if (!root.interactive) return "transparent"
            if (ma.pressed) return Colors.interactivePressed
            if (ma.containsMouse) return Colors.interactiveHover
            return "transparent"
        }
        border {
            width: 1
            color: root.interactive ? Colors.cardBorder : "transparent"
        }

        Behavior on color { ColorAnimation { duration: 120 } }

        IconImage {
            anchors.centerIn: parent
            source: key.iconSource
            width: Math.round(root.cell * 0.42)
            height: Math.round(root.cell * 0.42)
            imageOpacity: root.interactive ? 1.0 : 0.35
        }

        MouseArea {
            id: ma
            anchors.fill: parent
            hoverEnabled: true
            enabled: root.interactive
            cursorShape: root.interactive ? Qt.PointingHandCursor : Qt.ArrowCursor

            onPressed: {
                root._dxPending = key.dx
                root._dyPending = key.dy
                root.jog(key.dx * root.step, key.dy * root.step)   // 立刻走一格
                holdDelay.start()
            }
            onReleased: root._stopRepeat()
            onCanceled: root._stopRepeat()
            // 鼠标移出按键也要停：否则会一直连发到松开，很危险
            onExited: if (!ma.pressed) root._stopRepeat()
        }

        // 悬停提示（沿用设备卡片的做法）
        Rectangle {
            visible: ma.containsMouse && root.interactive
            anchors {
                horizontalCenter: parent.horizontalCenter
                bottom: parent.top
                bottomMargin: -4
            }
            width: tipText.implicitWidth + 14
            height: 22
            radius: 5
            color: Colors.textSecondary
            z: 2
            Text {
                id: tipText
                anchors.centerIn: parent
                text: key.tip
                font.pixelSize: 11
                color: Colors.textOnAccent
            }
        }
    }

    // ============================================================
    // 十字布局
    // ============================================================
    GridLayout {
        anchors.centerIn: parent
        columns: 3
        rowSpacing: 4
        columnSpacing: 4

        Item { Layout.preferredWidth: root.cell; Layout.preferredHeight: root.cell }

        DirKey {
            Layout.preferredWidth: root.cell; Layout.preferredHeight: root.cell
            iconSource: "../../images/向上.svg"; dx: 0; dy: 1
            tip: qsTr("+Y 向里") + " " + root.step.toFixed(2) + " mm"
        }

        Item { Layout.preferredWidth: root.cell; Layout.preferredHeight: root.cell }

        DirKey {
            Layout.preferredWidth: root.cell; Layout.preferredHeight: root.cell
            iconSource: "../../images/向左.svg"; dx: -1; dy: 0
            tip: qsTr("−X 向左") + " " + root.step.toFixed(2) + " mm"
        }

        // 中心格显示当前步长。
        // ⚠ 这里**不放"回原点"**：回原点会把 0 点改掉、让所有预设位置失效，
        //    放在方向键正中间太容易误点。它在下方的按钮区。
        Rectangle {
            Layout.preferredWidth: root.cell; Layout.preferredHeight: root.cell
            radius: 10
            color: "transparent"
            border { width: 1; color: Colors.cardBorder }
            Column {
                anchors.centerIn: parent
                spacing: 0
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: root.step.toFixed(root.step < 1 ? 2 : (root.step < 10 ? 1 : 0))
                    font.pixelSize: Math.round(root.cell * 0.27)
                    font.bold: true
                    color: root.interactive ? Colors.textPrimary : Colors.textPlaceholder
                }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: qsTr("毫米/格")
                    font.pixelSize: 10
                    color: Colors.textPlaceholder
                }
            }
        }

        DirKey {
            Layout.preferredWidth: root.cell; Layout.preferredHeight: root.cell
            iconSource: "../../images/向右.svg"; dx: 1; dy: 0
            tip: qsTr("+X 向右") + " " + root.step.toFixed(2) + " mm"
        }

        Item { Layout.preferredWidth: root.cell; Layout.preferredHeight: root.cell }

        DirKey {
            Layout.preferredWidth: root.cell; Layout.preferredHeight: root.cell
            iconSource: "../../images/向下.svg"; dx: 0; dy: -1
            tip: qsTr("−Y 向外") + " " + root.step.toFixed(2) + " mm"
        }

        Item { Layout.preferredWidth: root.cell; Layout.preferredHeight: root.cell }
    }
}
