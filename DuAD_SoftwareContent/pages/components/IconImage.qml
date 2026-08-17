import QtQuick
import Qt5Compat.GraphicalEffects
import DuAD_Software

/*
    SVG 图标 — 自动跟随主题染色。
    黑色 SVG 图标在暗色主题下会被染成浅色。
*/
Item {
    id: root

    property url source: ""
    property alias color: overlay.color
    property real imageOpacity: 1.0

    implicitWidth: 24
    implicitHeight: 24

    Image {
        id: img
        anchors.fill: parent
        source: root.source
        visible: false
        fillMode: Image.PreserveAspectFit
        mipmap: true
        smooth: true
    }

    ColorOverlay {
        id: overlay
        anchors.fill: img
        source: img
        color: Colors.iconColor
        opacity: root.imageOpacity
        visible: img.status === Image.Ready
    }
}
