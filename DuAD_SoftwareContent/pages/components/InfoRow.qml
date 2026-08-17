import QtQuick
import QtQuick.Layouts
import DuAD_Software

/* 信息行（标签 + 值） */
RowLayout {
    property string label: ""
    property string value: ""

    spacing: 8
    Text {
        text: label
        font.pixelSize: 12
        color: Colors.textSecondary
    }
    Text {
        text: value
        font.pixelSize: 12
        color: Colors.textPrimary
        Layout.fillWidth: true
        elide: Text.ElideRight
    }
}
