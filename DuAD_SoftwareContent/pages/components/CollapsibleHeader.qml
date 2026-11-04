import QtQuick
import QtQuick.Layouts
import DuAD_Software
import "."

/*
    可折叠的分节标题（只用于「平台设置」里的功能分区）。

    为什么不用 SectionHeader 直接加个三角：SectionHeader 是一个纯 Text，
    在别的页面也大量使用；把它改成 Item/RowLayout 会影响那些页面的宽度与高度。
    这里单独做一个带「下单箭头」的标题，`onToggled` 由宿主切换 expanded，
    宿主自己负责把内容 visible 绑到 expanded。

    ⚠ MouseArea 必须放在 RowLayout **外面**：布局管理的子项再 anchors.fill 会报
      "Detected anchors on an item that is managed by a layout"（渲染时会报 QML 错误）。
*/
Item {
    id: root
    objectName: "collapsibleHeader"

    property string title: ""
    property bool expanded: true
    signal toggled()

    implicitHeight: 24
    implicitWidth: row.implicitWidth

    RowLayout {
        id: row
        anchors.fill: parent
        spacing: 6

        Item {
            Layout.alignment: Qt.AlignVCenter
            Layout.preferredWidth: 12
            Layout.preferredHeight: 12

            Image {
                anchors.centerIn: parent
                source: "../../images/折叠箭头.svg"
                sourceSize.width: 10
                sourceSize.height: 10
                width: 10
                height: 10
                fillMode: Image.PreserveAspectFit
                rotation: root.expanded ? 0 : -90
                Behavior on rotation { NumberAnimation { duration: 150; easing.type: Easing.OutCubic } }
            }
        }

        Text {
            Layout.fillWidth: true
            text: root.title
            font.pixelSize: 12
            font.bold: true
            color: Colors.textPrimary
            elide: Text.ElideRight
        }
    }

    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.toggled()
    }
}
