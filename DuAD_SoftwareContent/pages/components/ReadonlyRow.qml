import QtQuick
import QtQuick.Layouts
import DuAD_Software

/* 只读行 */
RowLayout {
    property string label: ""
    property string value: ""

    spacing: 8

    Text {
        text: label
        font.pixelSize: 13
        color: Colors.textPrimary
    }

    Text {
        text: value
        font.pixelSize: 13
        color: Colors.textPlaceholder
    }
}
