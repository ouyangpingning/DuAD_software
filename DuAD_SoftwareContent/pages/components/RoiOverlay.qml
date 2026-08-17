import QtQuick
import QtQuick.Controls
import DuAD_Software

/*
    ROI 框选层 — 覆盖在原图显示区上。

    交互：
      1. drawingEnabled=true：显示“拖动绘制”提示 + 红框外框，光标变十字
      2. 图上拖拽画红框，拖动中实时显示像素范围
      3. 松开后弹出[确定]/[重绘]：
          确定 → roiApplied(归一化矩形) → 页面调 CameraBridge.applyRoi
          重绘 → 清空当前框，保持绘制模式继续画
      4. 坐标为归一化（0~1）；pixel 范围由页面传入 imageWidth/imageHeight 计算
*/
Item {
    id: root

    // ============================================================
    // 公有 API
    // ============================================================
    property bool drawingEnabled: false     // 绘制模式开关（页面控制）
    property bool hasRoi: false             // 是否已有生效 ROI
    property bool drawing: false            // 拖拽中（只读状态）
    property int imageWidth: 0              // 相机当前像素宽（显示范围用）
    property int imageHeight: 0             // 相机当前像素高
    property real aspectRatio: 0            // 图像内容宽高比；>0 时剔除黑边后归一化

    // 归一化 ROI 矩形（x/y/w/h 均 0~1）
    property rect roiRect: Qt.rect(0, 0, 0, 0)

    signal roiApplied(rect normalized)
    signal roiCleared()

    // ============================================================
    // 内部状态
    // ============================================================
    property bool _dragging: false
    property bool _preview: false           // 已画出框、显示确定/重绘
    property point _pressPos: Qt.point(0, 0)
    property point _currentPos: Qt.point(0, 0)
    property rect _curRect: Qt.rect(0, 0, 0, 0)   // 归一化

    // 图像实际显示区域：ImageView 按 aspectRatio 等比居中，外围可能有黑边。
    // 鼠标坐标是相对整个 RoiOverlay 的，必须换算到图像内容区域，否则
    // 框选结果与最终相机 ROI 会存在比例/偏移误差。
    function _contentRect() {
        var w = root.width, h = root.height
        if (root.aspectRatio > 0) {
            var cw = Math.min(w, h * root.aspectRatio)
            var ch = cw / root.aspectRatio
            return Qt.rect((w - cw) / 2, (h - ch) / 2, cw, ch)
        }
        return Qt.rect(0, 0, w, h)
    }

    function _normalize(r) {
        var c = _contentRect()
        if (c.width <= 0 || c.height <= 0) return Qt.rect(0, 0, 0, 0)
        var lx = r.x - c.x
        var ly = r.y - c.y
        return Qt.rect(Math.max(0, lx / c.width), Math.max(0, ly / c.height),
                       Math.min(1, r.width / c.width),
                       Math.min(1, r.height / c.height))
    }

    function _align(v, step, minVal, maxVal) {
        var a = Math.floor((v + step - 1) / step) * step
        if (a < minVal) a = minVal
        if (a > maxVal) a = Math.floor(maxVal / step) * step
        return a
    }

    // 当前框的原始像素范围（未做 8/2 对齐）
    function _rawPixelRect() {
        var r = root._normalize(root._curRect)
        return Qt.rect(
            Math.round(r.x * root.imageWidth),
            Math.round(r.y * root.imageHeight),
            Math.round(r.width * root.imageWidth),
            Math.round(r.height * root.imageHeight))
    }

    // 应用时将写入相机的像素范围（宽 8 / 高 2 步进对齐）
    function _alignedPixelRect() {
        var r = _rawPixelRect()
        var maxW = root.imageWidth
        var maxH = root.imageHeight
        var x = _align(r.x, 8, 0, maxW)
        var y = _align(r.y, 2, 0, maxH)
        var w = _align(r.width, 8, 8, maxW - x)
        var h = _align(r.height, 2, 2, maxH - y)
        if (x + w > maxW) w = _align(maxW - x, 8, 8, maxW)
        if (y + h > maxH) h = _align(maxH - y, 2, 2, maxH)
        return Qt.rect(x, y, w, h)
    }

    function clearRoi() {
        root.hasRoi = false
        root.roiRect = Qt.rect(0, 0, 0, 0)
        root.roiCleared()
    }

    function cancelDrawing() {
        root._dragging = false
        root._preview = false
        root._curRect = Qt.rect(0, 0, 0, 0)
    }

    // ============================================================
    // 绘制模式外框 + 提示
    // ============================================================
    Rectangle {
        visible: root.drawingEnabled
        anchors.fill: parent
        color: "transparent"
        border { width: 2; color: "#e74c3c" }
    }

    Rectangle {
        visible: root.drawingEnabled && !root._dragging && !root._preview
        anchors.centerIn: parent
        width: hintText.width + 20
        height: 30
        radius: 6
        color: "#99000000"
        z: 4

        Text {
            id: hintText
            anchors.centerIn: parent
            text: qsTr("在画面上拖动鼠标，框选 ROI 区域")
            color: "#ffffff"
            font.pixelSize: 12
        }
    }

    // ============================================================
    // 拖拽绘制
    // ============================================================
    MouseArea {
        id: dragArea
        anchors.fill: parent
        enabled: root.drawingEnabled
        cursorShape: root.drawingEnabled ? Qt.CrossCursor : Qt.ArrowCursor
        preventStealing: true
        z: 1

        onPressed: (mouse) => {
            root._dragging = true
            root._preview = false
            root._pressPos = Qt.point(mouse.x, mouse.y)
            root._currentPos = Qt.point(mouse.x, mouse.y)
            root._curRect = Qt.rect(0, 0, 0, 0)
        }
        onPositionChanged: (mouse) => {
            if (!root._dragging) return
            root._currentPos = Qt.point(mouse.x, mouse.y)
            root._curRect = Qt.rect(
                Math.min(root._pressPos.x, root._currentPos.x),
                Math.min(root._pressPos.y, root._currentPos.y),
                Math.abs(root._pressPos.x - root._currentPos.x),
                Math.abs(root._pressPos.y - root._currentPos.y))
        }
        onReleased: (mouse) => {
            if (!root._dragging) return
            root._dragging = false
            // 有效框（最小 8px）才进入预览，否则视为放弃
            if (root._curRect.width >= 8 && root._curRect.height >= 8) {
                root._preview = true
            }
        }
    }

    // ============================================================
    // 红框绘制（拖拽中实时 + 预览态）
    // ============================================================
    Rectangle {
        visible: (root._dragging || root._preview) && root._curRect.width > 0
        x: root._curRect.x
        y: root._curRect.y
        width: root._curRect.width
        height: root._curRect.height
        color: "transparent"
        border { width: 2; color: "#e74c3c" }
        z: 2

        Rectangle {
            anchors.fill: parent
            color: "#e74c3c"
            opacity: 0.15
        }
    }

    // 拖动中显示原始像素范围；预览时显示对齐后实际应用值
    Rectangle {
        visible: (root._dragging || root._preview)
                  && root._curRect.width > 0 && root.imageWidth > 0
        z: 12
        width: pixelRangeText.width + 10
        height: pixelRangeText.height + 6
        x: Math.max(2, Math.min(root._curRect.x, root.width - width - 2))
        y: Math.max(0, root._curRect.y - height - 4)
        radius: 3
        color: "#cc000000"

        Text {
            id: pixelRangeText
            anchors.centerIn: parent
            text: {
                if (root.imageWidth <= 0 || root.imageHeight <= 0) return ""
                var raw = root._rawPixelRect()
                if (root._preview) {
                    var app = root._alignedPixelRect()
                    return qsTr("应用 ROI: X=%1 Y=%2  W=%3 H=%4")
                        .arg(app.x).arg(app.y).arg(app.width).arg(app.height)
                }
                return qsTr("框选像素: X=%1 Y=%2  W=%3 H=%4")
                    .arg(raw.x).arg(raw.y).arg(raw.width).arg(raw.height)
            }
            color: "#ffffff"
            font.pixelSize: 11
        }
    }

    // 已生效 ROI（非绘制过程）：常显细框
    Rectangle {
        visible: root.hasRoi && !root._dragging && !root._preview
        x: _contentRect().x + root.roiRect.x * _contentRect().width
        y: _contentRect().y + root.roiRect.y * _contentRect().height
        width: root.roiRect.width * _contentRect().width
        height: root.roiRect.height * _contentRect().height
        color: "transparent"
        border { width: 1; color: "#e74c3c" }
    }

    // ============================================================
    // 预览操作：确定 / 重绘
    // ============================================================
    Row {
        id: previewActions
        visible: root._preview
        z: 20
        spacing: 6
        x: Math.max(2, Math.min(root._curRect.x + root._curRect.width - width - 2,
                                root.width - width - 2))
        y: Math.min(root._curRect.y + root._curRect.height + 2,
                    root.height - height - 2)

        Button {
            text: qsTr("确定")
            implicitHeight: 28
            font.pixelSize: 12

            contentItem: Text {
                text: parent.text
                font: parent.font
                color: "#ffffff"
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }

            onClicked: {
                root.roiRect = root._normalize(root._curRect)
                root.hasRoi = true
                root._preview = false
                root._curRect = Qt.rect(0, 0, 0, 0)
                // 不要在这里直接给 drawingEnabled 赋值（会破坏页面的 binding）；
                // 页面收到 roiApplied 后把 root._roiMode 置 false 即可。
                root.roiApplied(root.roiRect)
            }

            background: Rectangle {
                radius: 4
                color: parent.hovered ? "#c0392b" : "#e74c3c"
            }
        }

        Button {
            text: qsTr("重绘")
            implicitHeight: 28
            font.pixelSize: 12

            contentItem: Text {
                text: parent.text
                font: parent.font
                color: "#ffffff"
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }

            onClicked: {
                // 保持绘制模式，清空当前预览重新画
                root.cancelDrawing()
            }

            background: Rectangle {
                radius: 4
                color: parent.hovered ? "#7f8c8d" : "#95a5a6"
            }
        }
    }
}
