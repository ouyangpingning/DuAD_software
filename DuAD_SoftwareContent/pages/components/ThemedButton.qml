import QtQuick
import QtQuick.Controls
import DuAD_Software

/*
    主题化按钮 —— 全站按钮的唯一画法（2026-09-28）。

    ── 为什么需要它 ────────────────────────────────────────────
    1. 这一页原来有 **9 个 Button 根本没写 `background`**，于是它们用的是 Qt Quick
       Controls 的 **Fusion 默认样式**（灰底 + 深灰渐变），**完全不吃 `Colors`** ——
       换主题/换配色时它们纹丝不动，在一片白卡片里像从别的软件里剪下来贴上去的
       （用户 2026-09-28 截图点名：「可以将这个页面中的按钮设置成可以随主题变化吗」）。
       另外还有一批各自手抄了一遍 `background` + `contentItem`，圆角/留白/悬停色三家不同。
    2. 用户同一张截图还提了两条，都在这里落实：
       · 「按钮的字体一定要居中」—— 见 IconText 里那对 fillWidth 弹簧：
         `contentItem` 会被拉满整行，RowLayout 默认从左边堆，文字于是贴左
         （实测 112px 的按钮里文字中心 40、按钮中心 56）。
       · 「设计得大一些，一眼看出就是按钮」—— 高度 32→38、字号 12→13、
         留白 28→34，并且每个按钮都带**硬阴影**（下移 2px 的圆角矩形）：
         静止时浮起、悬停更亮、**按下时面下沉 2px 且阴影消失**（真的按进去了）。

    ── 用法 ────────────────────────────────────────────────────
        ThemedButton {
            objectName: "moveToButton"
            text: qsTr("移动到该位置")
            tone: "soft"                     // 见下表
            implicitHeight: 40               // 需要更大/更小时显式覆盖
            onClicked: root.moveToRequested(x, y)
        }

    tone（语气）—— 缺省 neutral：
        neutral     白底 + 描边 + 阴影        普通动作（使能/设为原点/前往/发送…）
        soft        淡强调底 accentSoft        "提交一个动作"（移动到该位置/应用设置/记录）
        danger      实心红 statusDisconnected  急停
        dangerSoft  淡红底 + 红描边 + 红字      危险但次要（中断回零）／**已使能**
        success     淡绿底 + 绿描边 + 绿字      **未使能**（电机松着，可以手推平台）

    ⚠ 使能按钮的两种状态用色（2026-09-29 用户要求"状态要看得出来"）：
        · 未使能 → `success`（绿）—— 电机不出力 = 可以手推平台去靠块，安全的一侧
        · 已使能 → `dangerSoft`（淡红）—— 闭环抱死 + 带电，危险的一侧
      刻意**不**用 `danger`（实心红）：同一张卡里已经有实心红的「停止」，
      两个实心红按钮会让人分不清哪个是急停。禁用态（未连接）一律回中性白：
      状态未知时显示绿色等于撒谎。

    ⚠ 图标一律走 `IconText`（不用 Unicode 字形当图标）；文字仍然写在 **Button.text**
      上，无障碍/自动化与页面测试都靠它（写到 contentItem 里读出来是空串 —— 假断言）。
    ⚠ 阴影用**普通 Rectangle**，不用 `RectangularShadow`：更简单、零依赖，
      而且已经在页面渲染里验证过（docs/19 §32 有这段判断的来龙去脉）。
*/
Button {
    id: root

    // ── 外观 ────────────────────────────────────────────────
    property string iconSource: ""
    property int iconSize: 16
    property int fontPixelSize: 13
    property bool fontBold: false
    property real radius: 9
    // 内容两侧留白：按钮宽度 = 内容宽 + 它（动作按钮不 fillWidth，见 AGENTS §19-26）
    property int hPadding: 34
    property string tone: "neutral"
    // 要不要那块"浮起"的硬阴影。列表行里的小按钮（前往/删除）关掉更安静。
    property bool raised: true
    // 按下去面下沉多少像素（= 阴影的露出量）
    property int pressDepth: 2

    readonly property bool _isDanger: tone === "danger"
    readonly property bool _isDangerSoft: tone === "dangerSoft"
    readonly property bool _isSoft: tone === "soft"
    readonly property bool _isSuccess: tone === "success"

    readonly property color _fill: {
        if (!root.enabled) {
            // 禁用：danger 保留一个可辨认的淡底（否则"急停"整个消失，
            // 用户会以为急停按钮没了 —— 它必须一直看得见，只是按不动）
            if (_isDanger) return Colors.cardDangerHover
            if (_isSoft || _isDangerSoft) return Colors.cardDangerBg
            // success 禁用走中性白：它表达的是"此刻的电机状态"，而没连上时
            // 状态是**未知**——画成绿色等于撒谎（见文件头的用途说明）。
            return Colors.cardBg
        }
        if (_isDanger)     return root.pressed ? Qt.darker(Colors.statusDisconnected, 1.25)
                                               : (root.hovered ? Qt.lighter(Colors.statusDisconnected, 1.08)
                                                               : Colors.statusDisconnected)
        if (_isDangerSoft) return root.pressed || root.hovered ? Colors.cardDangerHover
                                                               : Colors.cardDangerBg
        if (_isSuccess)    return root.pressed || root.hovered
                                  ? Qt.darker(Colors.successSoft, 1.08)
                                  : Colors.successSoft
        // 选中态（checkable，如 DetectPage 的「开始/停止采集」开关按钮）：
        // 与项目里别处的"选中"语言一致 —— 强调色实底
        if (root.checked)  return root.pressed ? Qt.darker(Colors.interactivePressed, 1.12)
                                               : Colors.interactivePressed
        if (root.pressed)  return Colors.interactivePressed
        if (root.hovered)  return Colors.interactiveHover
        if (_isSoft)       return Colors.accentSoft
        // neutral 静止态用**白底**（不是透明）：白底 + 描边 + 阴影才读得成"按钮"，
        // 透明底在白色卡片上只剩一圈细线，用户会说"看不出这是按钮"。
        return Colors.cardBg
    }

    readonly property color _borderColor: {
        if (_isDanger)     return Qt.darker(Colors.statusDisconnected, 1.15)
        if (_isDangerSoft) return Colors.statusDisconnected
        if (_isSuccess)    return root.enabled ? Colors.statusConnected : Colors.cardBorderStrong
        // 静止态给一档更清楚的描边，悬停/按下/选中时换成强调色 —— "可点"这件事要看得见
        if (root.enabled && (root.hovered || root.pressed || root.checked))
            return Colors.interactivePressed
        return Colors.cardBorderStrong
    }

    readonly property color _fg: {
        if (_isDanger)     return root.enabled ? Colors.textOnAccent : Colors.textPlaceholder
        if (_isDangerSoft) return Colors.statusDisconnected
        if (_isSuccess)    return root.enabled ? Colors.statusConnected : Colors.textPlaceholder
        return root.enabled ? Colors.textPrimary : Colors.textPlaceholder
    }

    implicitWidth: implicitContentWidth + hPadding
    implicitHeight: 38

    background: Item {
        // ── 硬阴影：比面低 pressDepth，左右各缩 1px（免得两侧露出灰边）──
        // 按下去时**不画** —— 面沉下去、影子收掉，就是"按进去了"的观感。
        Rectangle {
            visible: root.raised && root.enabled && !root.pressed
            x: 1
            y: root.pressDepth + 1
            width: parent.width - 2
            height: parent.height - root.pressDepth
            radius: root.radius
            color: Colors.buttonShadow
        }

        // ── 面 ──
        Rectangle {
            id: face
            x: 0
            y: root.pressed && root.raised ? root.pressDepth : 0
            width: parent.width
            height: parent.height - (root.raised ? root.pressDepth : 0)
            radius: root.radius
            color: root._fill
            border { width: 1; color: root._borderColor }

            Behavior on y { NumberAnimation { duration: 90; easing.type: Easing.OutCubic } }
            Behavior on color { ColorAnimation { duration: 120 } }
            Behavior on border.color { ColorAnimation { duration: 120 } }
        }
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
