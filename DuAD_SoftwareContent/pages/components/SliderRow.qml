import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software

/*
    滑块 + 数值输入行 — 面向触摸屏与鼠标双操作优化。

    交互：
      1. 拖动/点击轨道：定位到对应值；若设置了 snapTicks，则自动锁定到最近刻度
      2. **Ctrl/Shift + 鼠标滚轮**：按 wheelStep 微调（Ctrl=1/10 步，Shift=10 倍步），停止滚动
         约 350ms 后自动提交 released()，不再出现“细调了但不知道是否生效”
      3. 点击数值：可直接键盘输入精确值，回车/失焦后提交 released()
      4. 松开滑块时提交 released()（拖动过程只预览，不频繁写相机）

    snapTicks 示例（曝光 ms）：
      [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000]
*/
RowLayout {
    id: rowRoot

    signal released(real value)

    property string label: ""
    property real sliderValue: 0
    property real from: 0
    property real to: 100
    property string suffix: ""
    property int decimals: 1
    property alias enabled: control.enabled
    property real resetValue: NaN          // 重置目标值；NaN 时不显示重置按钮
    property var snapTicks: []             // 拖动时吸附的标准刻度（当前值单位）
    property real wheelStep: 0             // 滚轮微调步长；0 = 自动(range/1000)
    property bool logScale: false          // ★ true = 轨道按**对数**分布（低值区占更多行程）
    property bool _commitFlash: false      // 提交后短暂显示 ✓，确认值已发送后端

    /* ── 值 ↔ 轨道位置 的映射（2026-09-13 新增 logScale）──────────────
       为什么需要：`加减速` 这种参数**低值区变化剧烈、高值区几乎无差别**
       （1 和 2 的手感差得很远，190 和 200 分不出来），线性滑块把 1~200 平铺在一条
       200px 的轨道上，低端根本拖不准。对数分布把行程按比例分给低值区：
       `from=1, to=200` 时 **1~50 占掉约 74% 的轨道**（线性只有 25%）。

       ⚠ 这两个函数是**唯一的映射定义**：手柄位置、刻度线、拖动、滚轮都必须走它们。
         谁再自己写一遍 `(v-from)/(to-from)`，对数刻度就会"手柄和刻度对不上"。 */
    function ratioOf(v) {
        if (!logScale)
            return to > from ? (v - from) / (to - from) : 0
        var lo = Math.log(Math.max(1e-6, from))
        var hi = Math.log(Math.max(1e-6, to))
        return (Math.log(Math.max(1e-6, v)) - lo) / (hi - lo)
    }

    function valueOf(r) {
        if (!logScale)
            return from + r * (to - from)
        var lo = Math.log(Math.max(1e-6, from))
        var hi = Math.log(Math.max(1e-6, to))
        return Math.exp(lo + r * (hi - lo))
    }

    // 给测试用的观测量：`from~50` 这段占轨道的比例（对数刻度下应当远大于线性）
    readonly property real _lowShare: ratioOf(Math.min(50, to))

    function commitValue() {
        rowRoot.released(sliderValue)
        rowRoot._commitFlash = true
        commitFlashTimer.restart()
    }

    spacing: 8

    // ── 数值规整 / 刻度吸附 ──────────────────────────────
    function normalizeValue(v) {
        var c = Math.max(from, Math.min(to, v))
        var f = Math.pow(10, decimals)
        return Math.round(c * f) / f
    }

    function snapValue(raw) {
        if (!snapTicks || snapTicks.length === 0)
            return normalizeValue(raw)
        var best = snapTicks[0]
        var bestDist = Math.abs(raw - best)
        for (var i = 1; i < snapTicks.length; i++) {
            var d = Math.abs(raw - snapTicks[i])
            if (d < bestDist) {
                bestDist = d
                best = snapTicks[i]
            }
        }
        return normalizeValue(best)
    }

    // label 列固定宽 → 各行轨道起点对齐；超长文本省略号截断
    Text {
        text: label
        font.pixelSize: 12
        color: control.enabled ? Colors.textPrimary : Colors.textPlaceholder
        Layout.preferredWidth: 72
        Layout.minimumWidth: 72
        Layout.maximumWidth: 72
        elide: Text.ElideRight
        horizontalAlignment: Text.AlignLeft
    }

    // 替换默认 Slider，自定义 track + handle
    Item {
        id: control
        Layout.fillWidth: true
        implicitHeight: 30   // 触摸友好：有效热区约 40px

        // ⚠ 必须走 rowRoot.ratioOf()（对数刻度时线性算会错位）
        readonly property real ratio: rowRoot.ratioOf(sliderValue)

        // 轨道
        Rectangle {
            id: track
            width: parent.width
            height: 6
            radius: 3
            anchors.verticalCenter: parent.verticalCenter
            color: "#e0e0e0"
        }

        // 已填充轨道
        Rectangle {
            y: track.y
            width: Math.min(track.width, track.width * control.ratio)
            height: track.height
            radius: track.radius
            color: control.enabled ? Colors.interactivePressed : "#d0d0d0"
        }

        // 标准刻度线：snapTicks 里的每个值在轨道上画一条短竖线
        Repeater {
            model: rowRoot.snapTicks.length > 0 ? rowRoot.snapTicks : 0
            delegate: Rectangle {
                visible: rowRoot.snapTicks.length > 1
                width: 1
                height: track.height + 4
                radius: 0.5
                x: {
                    var px = rowRoot.ratioOf(modelData) * track.width - width / 2
                    return Math.max(0, Math.min(track.width - width, px))
                }
                anchors.verticalCenter: track.verticalCenter
                color: control.enabled ? "#8a8a8a" : "#c8c8c8"
                opacity: 0.55
            }
        }

        // 手柄（触摸屏建议 ≥ 20px）
        Rectangle {
            id: handle
            width: 20
            height: 20
            radius: 10
            anchors.verticalCenter: track.verticalCenter
            x: control.ratio * (track.width - width)

            color: "#ffffff"
            border { width: 2; color: Colors.interactivePressed }
        }

        property bool _pressed: false

        MouseArea {
            anchors.fill: parent
            anchors.margins: -10
            enabled: control.enabled
            preventStealing: true

            onPressed: { control._pressed = true; setValue(mouseX) }
            onPositionChanged: if (control._pressed) setValue(mouseX)

            onReleased: {
                if (control._pressed) {
                    control._pressed = false
                    commitTimer.stop()
                    rowRoot.commitValue()
                }
            }

            // 滚轮细调：更新值并延迟提交；细调结果最终一定触发 released
            // ⚠ 2026-09-28：**不按 Ctrl/Shift 时把滚轮还给页面**（accepted=false）。
            //   这一版的滑块住在会滚动的页面里，用户报的现象是
            //   "滚轮上下移动的时候会影响到滑条" —— 滚页面时鼠标扫过滑条就把值改了，
            //   而且 350ms 后还会**写进板子**（速度/软限位）。细调没丢：
            //   Ctrl（更细）/ Shift（更粗）+ 滚轮；或者直接点右边的数值框输入。
            onWheel: function(wheel) {
                if (!control.enabled) return
                if (!(wheel.modifiers & (Qt.ControlModifier | Qt.ShiftModifier))) {
                    wheel.accepted = false      // 交给父级 Flickable 滚页面
                    return
                }
                var step = wheelStep > 0
                    ? wheelStep
                    : Math.max((to - from) / 1000, Math.pow(10, -decimals))
                // ⚠ Ctrl 细调要**保底一个显示精度**：decimals=0 的滑块上
                //   `step*0.1` 再经 normalizeValue 取整会回到原值 —— 表现是"按住 Ctrl 滚轮没反应"。
                if (wheel.modifiers & Qt.ControlModifier)
                    step = Math.max(step * 0.1, Math.pow(10, -decimals))
                else if (wheel.modifiers & Qt.ShiftModifier)
                    step *= 10           // Shift = 更粗

                var dy = wheel.angleDelta.y !== 0
                    ? wheel.angleDelta.y : wheel.angleDelta.x
                sliderValue = normalizeValue(sliderValue + (dy > 0 ? step : -step))
                commitTimer.restart()    // 停止滚动 350ms 后写后端
            }

            function setValue(mx) {
                var r = Math.max(0, Math.min(1, mx / track.width))
                // 拖动/触摸：靠近标准刻度时自动锁定（valueOf 负责反解对数刻度）
                sliderValue = snapValue(rowRoot.valueOf(r))
            }
        }
    }

    // ── 数值输入区：点击输入精确值，回车/失焦提交 ─────────
    RowLayout {
        Layout.preferredWidth: 80
        Layout.minimumWidth: 80
        Layout.maximumWidth: 80
        spacing: 2

        TextField {
            id: valueField
            Layout.fillWidth: true
            text: sliderValue.toFixed(decimals)
            font.pixelSize: 12
            color: control.enabled ? Colors.textPrimary : Colors.textPlaceholder
            horizontalAlignment: Text.AlignRight
            verticalAlignment: Text.AlignVCenter
            inputMethodHints: Qt.ImhFormattedNumbersOnly
            selectByMouse: true
            activeFocusOnPress: true

            validator: DoubleValidator {
                bottom: rowRoot.from
                top: rowRoot.to
                decimals: rowRoot.decimals
                notation: DoubleValidator.StandardNotation
            }

            background: Rectangle {
                implicitHeight: 26
                radius: 4
                color: valueField.activeFocus ? Colors.pageBg : "transparent"
                border {
                    width: valueField.activeFocus ? 1 : 0
                    color: valueField.activeFocus ? Colors.interactivePressed : "transparent"
                }
            }

            onEditingFinished: commit()

            function commit() {
                var v = parseFloat(text)
                if (!isNaN(v)) {
                    sliderValue = normalizeValue(v)
                    rowRoot.commitValue()
                }
                // 非法/空输入恢复当前值
                text = Qt.binding(function() { return sliderValue.toFixed(decimals) })
            }
        }

        Text {
            text: suffix
            font.pixelSize: 11
            color: control.enabled ? Colors.textSecondary : Colors.textPlaceholder
        }

        Text {
            visible: rowRoot._commitFlash
            text: "✓"
            font.pixelSize: 11
            color: Colors.statusConnected
        }
    }

    // 滚轮/失焦提交防抖：最后一次操作后 350ms 发 released
    Timer {
        id: commitTimer
        interval: 350
        repeat: false
        onTriggered: {
            if (!control._pressed)
                rowRoot.commitValue()
        }
    }

    // ✓ 提交反馈：短暂显示后消失
    Timer {
        id: commitFlashTimer
        interval: 800
        repeat: false
        onTriggered: rowRoot._commitFlash = false
    }

    // 重置按钮 — 恢复 resetValue 并触发 released
    Button {
        visible: !isNaN(resetValue)
        implicitWidth: 22
        implicitHeight: 22
        text: "↺"

        contentItem: Text {
            text: parent.text
            font.pixelSize: 12
            color: Colors.textSecondary
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }

        onClicked: {
            sliderValue = resetValue
            commitTimer.stop()
            rowRoot.commitValue()
        }

        background: Rectangle {
            radius: 4
            color: parent.hovered ? Colors.interactiveHover : "transparent"
        }
    }
}
