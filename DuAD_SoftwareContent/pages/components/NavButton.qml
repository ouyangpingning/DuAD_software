import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software

/*
    侧边导航项。

    2026-10-08 评审改版：原来是"椭圆图标 + 文字"整体居中，选中态只有图标外一圈淡薄荷
    （#aee9e7，与悬停色几乎分不出）；且各项文字长短不同、居中后左边缘参差不齐。
    现在：**整行**淡强调底 + 左侧 3px 实色指示条 + 强调色粗体字；图标与文字左对齐。

    ⚠ 图标尺寸用 implicitWidth/Height 给死（IconImage 创建后被改尺寸会画不出来，§19-35），
      用锚点摆放而不是进 Layout（进 Layout 会被覆盖成 implicitWidth）。
*/
Button {
    id: control

    property url iconsource: ""
    property bool collapsed: false    // 侧边栏收起时只显示图标

    flat: true
    checkable: true
    Layout.fillWidth: true
    implicitHeight: 44

    readonly property bool _active: control.checked || control.pressed

    background: Rectangle {
        radius: 8
        color: control._active ? Colors.accentSoft
                               : (control.hovered ? Colors.interactiveHover : "transparent")
        Behavior on color { ColorAnimation { duration: 120 } }

        // 选中指示条：只在选中时出现。收起状态下也保留 —— 只剩图标时它是唯一的"你在这页"线索
        Rectangle {
            visible: control.checked
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            width: 3
            height: parent.height - 16
            radius: 1.5
            color: Colors.accent
        }
    }

    contentItem: Item {
        IconImage {
            id: icon
            implicitWidth: 22
            implicitHeight: 22
            width: 22
            height: 22
            x: control.collapsed ? (parent.width - width) / 2 : 10
            anchors.verticalCenter: parent.verticalCenter
            source: control.iconsource
            visible: control.iconsource.toString() !== ""
            color: control.checked ? Colors.accentText : Colors.iconColor
        }

        Text {
            anchors.left: icon.right
            anchors.leftMargin: 12
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            text: control.text
            color: control.checked ? Colors.accentText : Colors.textPrimary
            font.pixelSize: 14
            font.bold: control.checked
            elide: Text.ElideRight          // 英文长文本自动截断加省略号
            visible: !control.collapsed
        }
    }
}
