import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software

/* 开关行 — label + pill 风格开关 */
RowLayout {
    property string label: ""
    property alias on: toggleBtn.on
    property alias enabled: toggleBtn.enabled

    signal toggled()

    spacing: 8
    Text {
        text: label
        font.pixelSize: 12
        color: enabled ? Colors.textPrimary : Colors.textPlaceholder
        Layout.fillWidth: true       // 占满剩余空间（开关固定 44px 在右）
        elide: Text.ElideRight       // 长文本（英文翻译较长）省略号截断，不撑破行宽
    }

    Button {
        id: toggleBtn

        property bool on: false

        flat: true
        checkable: true
        checked: on
        implicitWidth: 44
        implicitHeight: 24

        onClicked: {
            on = !on
            parent.toggled()
        }

        background: Rectangle {
            anchors.fill: parent
            radius: height / 2
            color: toggleBtn.on ? Colors.interactivePressed : "#d0d0d0"

            Behavior on color { ColorAnimation { duration: 200 } }

            Rectangle {
                width: parent.height - 6
                height: width
                radius: width / 2
                color: "#ffffff"
                anchors.verticalCenter: parent.verticalCenter

                x: toggleBtn.on ? parent.width - width - 3 : 3

                Behavior on x {
                    NumberAnimation { duration: 200; easing.type: Easing.InOutCubic }
                }
            }
        }
    }
}
