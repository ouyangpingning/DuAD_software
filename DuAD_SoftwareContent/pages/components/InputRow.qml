import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software

/* 文本输入行 */
RowLayout {
    property string label: ""
    property alias text: field.text
    property alias placeholderText: field.placeholderText
    property alias enabled: field.enabled
    property bool password: false

    signal textEdited(string newText)

    spacing: 8
    // label 列固定宽 → 各行输入框起点对齐；超长文本省略号截断
    Text {
        text: label
        font.pixelSize: 12
        color: enabled ? Colors.textPrimary : Colors.textPlaceholder
        Layout.preferredWidth: 72
        Layout.minimumWidth: 72
        Layout.maximumWidth: 72
        elide: Text.ElideRight
        horizontalAlignment: Text.AlignLeft
    }

    TextField {
        id: field
        font.pixelSize: 13
        color: enabled ? Colors.textPrimary : Colors.textPlaceholder
        Layout.fillWidth: true
        echoMode: password ? TextField.Password : TextField.Normal
        onTextEdited: parent.textEdited(text)

        background: Rectangle {
            implicitHeight: 32
            radius: 4
            color: field.enabled ? Colors.pageBg : "transparent"
            border { width: 1; color: field.activeFocus ? Colors.interactivePressed : Colors.cardBorder }
        }
    }
}
