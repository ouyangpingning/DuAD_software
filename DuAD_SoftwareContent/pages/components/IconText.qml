import QtQuick
import QtQuick.Layouts
import DuAD_Software

/*
    图标 + 文字 的按钮内容（居中排版）。

    为什么需要它：`⏻ 使能` / `⌂ 设为原点` / `■ 停止` / `▲ 向上` 这些原来是**用 Unicode
    字形当图标**。问题有三个，都是实测出来的：
      1. 依赖 wqy-microhei 恰好收录了这些码位 —— 收了也大小不一、基线不齐；
      2. Unicode 字形**不能单独染色**（要红色就得整段变红），换主题时没法跟；
      3. 语义靠字形猜（`⏻` 是什么？），不如一张电源图标直白。

    用法（在 Button 里）：
        contentItem: IconText {
            text: qsTr("使能")
            iconSource: "../../images/电源.svg"
            color: parent.enabled ? Colors.textPrimary : Colors.textPlaceholder
        }
    ⚠ `text` 仍然要写在 **Button.text** 上（而不是只写在这里）：无障碍/自动化要读得到、
      页面冒烟测试也是断言 `property("text")`（写在 contentItem 里读出来是空串，
      测试会变成假断言 —— 这条真踩过）。
*/
RowLayout {
    id: root

    property string text: ""
    property url iconSource: ""
    property color color: Colors.textPrimary
    property int iconSize: 15
    property int fontPixelSize: 13
    property bool fontBold: false

    IconImage {
        visible: root.iconSource != ""
        Layout.alignment: Qt.AlignVCenter
        source: root.iconSource
        width: root.iconSize
        height: root.iconSize
        color: root.color
    }
    Text {
        Layout.alignment: Qt.AlignVCenter
        text: root.text
        font.pixelSize: root.fontPixelSize
        font.bold: root.fontBold
        color: root.color
    }
}
