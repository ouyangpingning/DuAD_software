import QtQuick
import QtQuick.Controls
import DuAD_Software

/*
    主题化按钮 —— 「平台控制」页所有按钮的唯一画法（2026-09-28）。

    为什么需要它：这一页原来有 **9 个 Button 根本没写 `background`**，
    于是它们用的是 Qt Quick Controls 的 **Fusion 默认样式**（灰底 + 深灰渐变），
    **完全不吃 `Colors`** —— 换主题/换配色时它们纹丝不动，在一片白卡片里
    像从别的软件里剪下来贴上去的（用户 2026-09-28 截图点名：「可以将这个页面中的
    按钮设置成可以随主题变化吗」）。另外还有 9 个各自手抄了一遍 `background` +
    `contentItem`，圆角/留白/悬停色三家不同。

    现在：**颜色只来自 `Colors`，尺寸只来自这里的几个属性**。

    用法：
        ThemedButton {
            objectName: "moveToButton"
            text: qsTr("移动到该位置")
            tone: "soft"                     // 见下表
            implicitHeight: 36
            onClicked: root.moveToRequested(x, y)
        }

    tone（语气）—— 缺省 neutral：
        neutral     描边 + 透明底          普通动作（使能/设为原点/前往/发送…）
        soft        淡强调底 accentSoft    "提交一个动作"（移动到该位置/应用设置/记录）
        danger      实心红 statusDisconnected  急停
        dangerSoft  淡红底 + 红描边 + 红字      危险但次要（中断回零）

    ⚠ 图标一律走 `IconText`（不用 Unicode 字形当图标）；文字仍然写在 **Button.text**
      上，无障碍/自动化与页面测试都靠它（写到 contentItem 里读出来是空串 —— 假断言）。
*/
Button {
    id: root

    // ── 外观 ────────────────────────────────────────────────
    property string iconSource: ""
    property int iconSize: 15
    property int fontPixelSize: 12
    property bool fontBold: false
    property real radius: 8
    // 内容两侧留白：按钮宽度 = 内容宽 + 它（动作按钮不 fillWidth，见 AGENTS §19-26）
    property int hPadding: 28
    property string tone: "neutral"

    readonly property bool _isDanger: tone === "danger"
    readonly property bool _isDangerSoft: tone === "dangerSoft"

    readonly property color _fill: {
        if (!root.enabled) {
            // 禁用：danger 系列留一个可辨认的淡底（否则"急停"整个消失，
            // 用户会以为急停按钮没了 —— 它必须一直看得见，只是按不动）
            if (_isDanger) return Colors.cardDangerHover
            return "transparent"
        }
        if (_isDanger)     return root.pressed ? Qt.darker(Colors.statusDisconnected, 1.3)
                                               : Colors.statusDisconnected
        if (_isDangerSoft) return root.pressed || root.hovered ? Colors.cardDangerHover
                                                               : Colors.cardDangerBg
        if (root.pressed)  return Colors.interactivePressed
        if (root.hovered)  return Colors.interactiveHover
        if (root.tone === "soft") return Colors.accentSoft
        return "transparent"
    }

    readonly property color _borderColor: {
        if (_isDanger)     return Colors.statusDisconnected
        if (_isDangerSoft) return Colors.statusDisconnected
        return Colors.cardBorder
    }

    readonly property color _fg: {
        if (_isDanger)     return root.enabled ? Colors.textOnAccent : Colors.textPlaceholder
        if (_isDangerSoft) return Colors.statusDisconnected
        return root.enabled ? Colors.textPrimary : Colors.textPlaceholder
    }

    implicitWidth: implicitContentWidth + hPadding
    implicitHeight: 32

    background: Rectangle {
        radius: root.radius
        color: root._fill
        border { width: 1; color: root._borderColor }
        Behavior on color { ColorAnimation { duration: 120 } }
    }

    contentItem: IconText {
        text: root.text
        iconSource: root.iconSource
        iconSize: root.iconSize
        fontPixelSize: root.fontPixelSize
        fontBold: root.fontBold
        color: root._fg
    }
}
