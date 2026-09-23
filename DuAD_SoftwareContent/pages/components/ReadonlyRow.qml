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
        // ⚠ 必须能收缩 + 省略号。
        //   没有这两行时：长文本（如整行诊断摘要）的**隐式宽度**会把所在
        //   ColumnLayout 整体撑宽（实测 412 → 628），于是同一列里 fillWidth
        //   的兄弟行也跟着变宽，它们**右侧的控件（下拉框/开关）被推到卡片外裁掉** ——
        //   表现成"控件没画出来"。这个坑很隐蔽：坏的是 A 行，坏掉的是 B 行。
        Layout.fillWidth: true
        elide: Text.ElideRight
    }
}
