import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software

Button {
    id: control
    // Button 自带 text、checked、checkable 等属性

    // ---------- 开放给外部调用的属性 ----------
    property url iconsource: ""
    property bool collapsed: false    // 侧边栏收起时只显示图标

    // 默认按钮样式：扁平化，无边框
    flat: true
    checkable: true
    Layout.fillWidth: true
    implicitHeight: 50

    // ---------- 自定义背景 ----------
    background: Rectangle {
        id: bg
        anchors.fill: parent
        radius: 5
        color: "transparent"
    }

    // ---------- 自定义内容：椭圆形图标 + 文字 ----------
    contentItem: RowLayout {
        spacing: 15

        // 左侧弹簧 — 收起时隐藏
        Item { Layout.fillWidth: true; visible: !control.collapsed }

        // ============ 椭圆形图标区域 ============
        Rectangle {
            id: iconOval
            Layout.preferredWidth: 50
            Layout.preferredHeight: 30
            radius: width / 2

            color: {
                if (control.pressed || control.checked) return Colors.interactivePressed
                if (control.hovered) return Colors.interactiveHover
                return "transparent"
            }

            IconImage {
                anchors.centerIn: parent
                width: parent.width * 0.8
                height: parent.height * 0.8
                source: control.iconsource
                visible: control.iconsource.toString() !== ""
            }
        }

        // ============ 按钮文字 — 收起时隐藏 ============
        Text {
            text: control.text
            color: Colors.textPrimary
            font.pixelSize: 14
            Layout.fillWidth: true
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight          // 英文长文本自动截断加省略号
            visible: !control.collapsed
        }

        // 右侧弹簧 — 收起时隐藏
        Item { Layout.fillWidth: true; visible: !control.collapsed }
    }
}
