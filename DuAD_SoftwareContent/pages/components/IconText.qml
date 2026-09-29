import QtQuick
import DuAD_Software

/*
    图标 + 文字 的按钮内容（**恒定居中**，纯锚点，不依赖任何布局）。

    为什么需要它：`⏻ 使能` / `⌂ 设为原点` / `■ 停止` / `▲ 向上` 这些原来是**用 Unicode
    字形当图标**。问题有三个，都是实测出来的：
      1. 依赖 wqy-microhei 恰好收录了这些码位 —— 收了也大小不一、基线不齐；
      2. Unicode 字形**不能单独染色**（要红色就得整段变红），换主题时没法跟；
      3. 语义靠字形猜（`⏻` 是什么？），不如一张电源图标直白。

    ⚠ **为什么这里连 RowLayout 都不用**（2026-09-28 连试三种写法量出来的）：
      本组件当 `Button.contentItem` 时会被 Qt **拉满整行**（实测 188px 宽的按钮里
      contentItem 宽 180）。于是"怎么把内容摆到正中"变成一场和布局的搏斗：
        · 根用 `RowLayout`：子项**从左边堆** → 无图标时文字贴左（112px 的按钮里
          文字中心 40 / 按钮中心 56）、有图标时图标贴左、文字飘在中间；
        · 两侧加 `Item { Layout.fillWidth: true }` 弹簧：**不可靠** —— 188px 的按钮里
          图标仍在 x=0、文字在 x=90，多出来的空间被分到了别处；
        · 外层 `Item` + 内层 `RowLayout { anchors.centerIn: parent }`：
          `QQuickLayout` 会自己管几何，把 `anchors` 顶掉，仍然贴左。
      最后用**纯锚点算偏移**（下面两个 horizontalCenterOffset）：居中由算术保证，
      跟布局怎么分配空间完全无关。实测 188px 的按钮里图标中心 67、文字中心 121，
      整组中心 94 = 按钮中心 94 ✓

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
Item {
    id: root

    property string text: ""
    property url iconSource: ""
    property color color: Colors.textPrimary
    property int iconSize: 15
    property int fontPixelSize: 13
    property bool fontBold: false
    property int gap: 6

    readonly property bool _hasIcon: iconSource != ""
    readonly property real _iconW: _hasIcon ? iconSize : 0
    readonly property real _textW: label.implicitWidth
    // 整组（图标 + 间隔 + 文字）的宽度
    readonly property real _groupW: _iconW + (_hasIcon ? gap : 0) + _textW

    implicitWidth: _groupW
    implicitHeight: Math.max(_hasIcon ? iconSize : 0, label.implicitHeight)

    IconImage {
        id: icon
        visible: root._hasIcon
        width: root.iconSize
        height: root.iconSize
        source: root.iconSource
        color: root.color
        anchors.verticalCenter: parent.verticalCenter
        // 图标的中心要落在"整组左端起 _iconW/2 处" → 相对父中心偏移 -(gap + 文字宽)/2
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.horizontalCenterOffset: -(root._groupW - root._iconW) / 2
    }

    Text {
        id: label
        text: root.text
        font.pixelSize: root.fontPixelSize
        font.bold: root.fontBold
        color: root.color
        anchors.verticalCenter: parent.verticalCenter
        // 文字的中心要落在"图标之后" → 相对父中心偏移 +(图标宽 + gap)/2
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.horizontalCenterOffset: root._iconW / 2 + (root._hasIcon ? root.gap / 2 : 0)
    }
}
