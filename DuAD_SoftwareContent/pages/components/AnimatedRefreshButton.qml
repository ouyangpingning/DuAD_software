import QtQuick
import QtQuick.Controls
import DuAD_Software

Button {
    id: control

    // ============================================================
    // 公有 API
    // ============================================================
    /* 图标路径，支持相对路径（如 "images/refresh.svg"） */
    property url iconSource: ""

    /* 连续旋转：设为 true 时图标持续旋转（如加载中），false 时停止 */
    property bool running: false

    /* 按钮整体尺寸（宽高相等） */
    property int size: 40

    /* 图标尺寸（正方形边长） */
    readonly property real _iconSize: size * 0.5

    /* 背景圆直径 */
    readonly property real _circleDia: size * 0.75

    // ============================================================
    // 外观
    // ============================================================
    flat: true
    implicitWidth: size
    implicitHeight: size

    // 圆形悬停/按下背景 — 风格与 NavButton 的 iconOval 一致
    background: Rectangle {
        anchors.centerIn: parent
        width: control._circleDia
        height: control._circleDia
        radius: width / 2
        color: {
            if (control.hovered) return Colors.interactiveHover
            return "transparent"
        }
    }

    // ============================================================
    // 图标 + 旋转动画
    // ============================================================
    // Image 包在 Item 内，避免 Button 接管 Image 尺寸导致 SVG 按原始大小渲染
    contentItem: Item {
        IconImage {
            id: iconImage
            anchors.centerIn: parent
            width: control._iconSize
            height: control._iconSize
            source: control.iconSource
            visible: control.iconSource.toString() !== ""

            rotation: 0

            // -------- 单击：一次 360° 旋转 --------
            Behavior on rotation {
                enabled: !control.running
                NumberAnimation {
                    duration: 500
                    easing.type: Easing.OutCubic
                }
            }

            // -------- 连续旋转（running=true 时）--------
            RotationAnimation on rotation {
                from: 0
                to: 360
                duration: 800
                running: control.running
                loops: Animation.Infinite
                easing.type: Easing.Linear
            }
        }
    }

    // ============================================================
    // 交互
    // ============================================================
    onClicked: {
        // 非连续旋转时，点击触发一次 360° 旋转
        // 连续旋转时不做额外动画（已经在转了）
        if (!control.running) {
            iconImage.rotation += 360
        }
    }
}
