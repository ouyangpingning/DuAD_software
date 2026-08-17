import QtQuick
import QtQuick.Controls
import DuAD_Software

/*
    图像显示组件 — 等比缩放居中显示一帧画面。
    两种模式：
      simulated=true  内部 Canvas 生成动态模拟画面（联调前的演示画面）
      simulated=false 显示外部注入的图像（联调时由后端 frameReady 提供，
                      QML 端经 Image/ImageProvider 更新 source）
    aspectRatio 属性：>0 时内容区强制该宽高比并居中（相机分辨率如 1024:1224），
    窗口缩放时按比例缩放；=0 时铺满。
    无图/未就绪时显示占位文字。
*/
Item {
    id: root

    property bool simulated: false          // 模拟模式（Canvas 生成画面）
    property real simulatedHue: 0.55        // 模拟画面主色调（0~1）
    property string placeholderText: qsTr("等待图像")
    property bool imageActive: false        // 是否有有效画面（模拟帧已生成/后端帧到达）
    property int frameSeed: 0               // 模拟帧序号（外部 Timer 递增触发重绘）
    property real aspectRatio: 0            // 内容区宽高比（1024/1224），0 = 铺满

    // 外部注入画面（联调用）：真实图像 source 赋给 displayImage
    property alias imageSource: displayImage.source

    // 模拟帧序号变化 → 重绘模拟画面（处理器须挂在属主 root 上）
    onFrameSeedChanged: simCanvas.requestPaint()

    // ============================================================
    // 视觉容器（深色底，模拟屏幕）
    // ============================================================
    Rectangle {
        anchors.fill: parent
        radius: 8
        color: "#101010"

        // ── 内容区：按 aspectRatio 约束并居中（保持相机分辨率比例）──
        Item {
            id: content
            anchors.centerIn: parent

            // 单链计算：宽取"高度允许的宽"与"父宽"较小值，高 = 宽/比例，比例恒定
            width: root.aspectRatio > 0
                ? Math.min(parent.width, parent.height * root.aspectRatio)
                : parent.width
            height: root.aspectRatio > 0 ? width / root.aspectRatio : parent.height

            // ── 模拟画面（Canvas 渐变 + 噪点条带）──
            Canvas {
                id: simCanvas
                visible: root.simulated && root.imageActive
                anchors.fill: content
                renderStrategy: Canvas.Threaded

                onPaint: {
                    var ctx = getContext("2d")
                    var w = width, h = height
                    if (w <= 0 || h <= 0) return

                    // 背景渐变（随帧轻微漂移，模拟采集画面变化）
                    var drift = (root.frameSeed % 60) / 60.0
                    var g = ctx.createLinearGradient(0, 0, w, h)
                    var base = Qt.hsla(root.simulatedHue, 0.25, 0.18 + drift * 0.05, 1)
                    var top = Qt.hsla(root.simulatedHue, 0.35, 0.30 + drift * 0.06, 1)
                    g.addColorStop(0, base)
                    g.addColorStop(1, top)
                    ctx.fillStyle = g
                    ctx.fillRect(0, 0, w, h)

                    // 噪声条带（伪产品表面纹理）
                    ctx.globalAlpha = 0.15
                    for (var i = 0; i < 26; i++) {
                        var y = (i * 37 + root.frameSeed * 3) % h
                        ctx.fillStyle = (i % 3 === 0) ? "#ffffff" : "#000000"
                        ctx.fillRect(0, y, w, 1 + (i % 3))
                    }
                    // 模拟缺陷点（随帧移动，可被热力图"检出"）
                    ctx.globalAlpha = 0.9
                    ctx.fillStyle = "#d0d0d0"
                    var dx = w * (0.25 + 0.5 * ((root.frameSeed % 100) / 100.0))
                    var dy = h * (0.25 + 0.5 * ((root.frameSeed * 7) % 100) / 100.0)
                    ctx.beginPath()
                    ctx.arc(dx, dy, 5, 0, Math.PI * 2)
                    ctx.fill()
                }
            }

            // ── 外部注入画面（联调模式）──
            Image {
                id: displayImage
                visible: !root.simulated && root.imageActive
                anchors.fill: content
                fillMode: Image.PreserveAspectFit
                smooth: true
                source: ""
            }

            // ── 占位（无画面时）──
            Text {
                anchors.centerIn: content
                visible: !root.imageActive
                text: root.placeholderText
                font.pixelSize: 13
                color: "#808080"
            }
        }
    }
}
