import QtQuick

/* 骨架屏占位条 — 带呼吸动画 */
Rectangle {
    id: bar
    radius: 4
    color: "#e8e8e8"

    opacity: 1.0
    NumberAnimation on opacity {
        from: 0.4
        to: 1.0
        duration: 2000
        loops: Animation.Infinite
        easing.type: Easing.InOutSine
        running: true
    }
}
