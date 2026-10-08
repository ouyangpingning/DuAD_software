import QtQuick
import DuAD_Software

/*
    卡片 / 面板底板 —— 「平台控制」页起的统一样式（2026-09-28 美化）。

    为什么做成组件：这一页有 6 种卡片（两张平台连接卡、两张手动控制卡、预览卡、
    设置卡、协议卡）。样式散在 6 个文件里手抄"白底 + 描边 + 阴影"，迟早漂移；
    做成组件后改一处全页一致。

    ⚠ 为什么不用 QtQuick.Effects 的 RectangularShadow：它在最小探针脚本里一个像素都不画
      （`color:"#ff000000" blur:20 offset:(0,10)` 也空白）。但**别把探针结果当平台结论** ——
      docs/19 §32 记了我据此误判、又自我更正的完整经过。
      选普通矩形的真实理由很朴素：**更简单、零依赖，而且已经在页面渲染里验证过**，
      不必为了一个 2px 的底边去引一个特效模块（还得担心 Jetson 那边）。

    两个变体（2026-10-08 新增 panel）：
      · variant = "card"（默认，**别动**）—— 常驻在页面上的卡片：白底 + 1px 描边 +
        2px 单层硬投影（在底边露出）。全站连接卡/控制卡/结果卡都是它。
      · variant = "panel" —— **点出来**的面板（齿轮打开的设置、折叠节展开的表单）：
        底色深一档（Colors.panelBg）+ 上亮下暗的极轻渐变 + 顶边 1px 高光 +
        底边 1px 压深 + 面下方露出的一圈软投影，读成"浮在页面上的浅灰软板"。
        层次于是成了四级：
          页面（白）→ 卡片（浮起，常驻）→ **面板（再浮高一档，点出来的）** → 内嵌块（accentSoft）

    ⚠ **面板的投影必须画在自己矩形里面**（这是这一版的关键）：
      面板全都写着 `clip: true`（展开时要裁住溢出的内容），画在矩形**外面**的投影会被
      一个像素不剩地裁掉。原来这些面板就是"白底 + 1px 描边 + 一个根本看不见的 2px 投影"，
      贴在同样白的页面上，用户的原话是"和背景一个颜色，没有立体感"。
      所以面板的做法是：**面的下边往上收 `_panelLift` px**，投影就落在这条缝里 ——
      既浮得起来，又裁不掉。

    用法（把卡片原来的 `Rectangle { anchors.fill: parent; radius: 12; color: … }` 换掉）：
        CardSurface {
            id: cardBg
            anchors.fill: parent
            color: root.connected ? Colors.cardDangerBg : Colors.cardBg   // 可选：覆盖底色
        }
        ColumnLayout { anchors.fill: parent; ... }   // 内容锚点**不用改**：本组件占满父项
*/
Item {
    id: root

    property string variant: "card"

    property color color: root.variant === "panel" ? Colors.panelBg : Colors.cardBg
    property color borderColor: root.variant === "panel" ? Colors.panelBorder
                                                         : Colors.cardBorderStrong
    property real cornerRadius: 12
    property bool shadowed: true
    // 阴影相对于卡片底边的露出量。2px 是实测"看得出浮起、又不显脏"的值。
    property int lift: 2

    readonly property bool _isPanel: root.variant === "panel"
    // 面板的投影在**面板矩形之内**露出多少 px：面的下边往上收这么多，影子就落在这条缝里。
    // ⚠ 不能画到矩形外面去 —— 面板全都 clip:true，外面的东西会被裁掉（见文件头）。
    readonly property int _panelLift: root.lift + 2

    // ══════════════════════════════════════════════════════════
    // 投影
    // ══════════════════════════════════════════════════════════
    // ── 卡片：单层硬阴影。y 起点比卡片低 lift+1，高度少 1，于是只在**底边**露出 lift 像素；
    //    左右各缩 1px，避免两侧边缘出现一条灰线（那看着像描边画歪了）。
    //    ⚠ 用 x/y/width/height 而不是 anchors：anchors.fill + topMargin 会同时
    //      改到上边，卡片自己的矩形也会跟着变（内容的锚点全部错位）。
    Rectangle {
        visible: root.shadowed && !root._isPanel
        x: 1
        y: root.lift + 1
        width: parent.width - 2
        height: parent.height - 1
        radius: root.cornerRadius
        color: Colors.cardShadow
    }

    // ── 面板：两段式软投影，落在面下方那 _panelLift px 里 ──
    // 紧贴的一层 + 往里缩 3px 的更淡一层，读成"底下那圈影子"；
    // 两层都算在 root 的高度里，所以 clip:true 也裁不掉。
    Rectangle {
        visible: root.shadowed && root._isPanel
        x: 1
        y: 2
        width: parent.width - 2
        height: parent.height - 2
        radius: root.cornerRadius
        color: Colors.panelShadow
        opacity: 0.75
    }
    Rectangle {
        visible: root.shadowed && root._isPanel
        x: 3
        y: 3
        width: parent.width - 6
        height: parent.height - 3
        radius: root.cornerRadius
        color: Colors.panelShadow
        opacity: 0.35
    }

    // ══════════════════════════════════════════════════════════
    // 面
    // ══════════════════════════════════════════════════════════
    // ── 卡片面：原样式一字未改（连上/断开时底色渐变的 Behavior 也保留）──
    Rectangle {
        visible: !root._isPanel
        anchors.fill: parent
        radius: root.cornerRadius
        color: root.color
        border { width: 1; color: root.borderColor }

        Behavior on color { ColorAnimation { duration: 200 } }
    }

    // ── 面板面：**凸起**的一块软板（上亮下暗 + 顶边高光 + 底边压深 + 底下露一圈影子）──
    // 参考的是"浅灰圆角块浮在白底上"那种质感：立体感靠三件事一起给 ——
    //   ① 上亮下暗的极轻渐变（光从上面来）；② 顶边 1px 高光 / 底边 1px 压深；
    //   ③ 面下方露出的那圈软投影（_panelLift）。
    Rectangle {
        id: panelFace
        visible: root._isPanel
        anchors {
            top: parent.top; left: parent.left; right: parent.right; bottom: parent.bottom
            bottomMargin: root._panelLift
        }
        radius: root.cornerRadius
        // 渐变只是**一点点**（≈2%）：面板够大，纯平会显得"糊"，但也别做成金属拉丝 ——
        // 参考样式里那块浅灰块其实是纯平的，立体感主要交给"顶边高光 + 底边压深"。
        // ⚠ 渐变端一律从 root.color **推**出来（Qt.lighter/darker），不写死色值：
        //   换主题/换配色时它会跟着 panelBg 一起过渡。
        gradient: Gradient {
            GradientStop { position: 0.0; color: Qt.lighter(root.color, 1.02) }
            GradientStop { position: 0.5; color: root.color }
            GradientStop { position: 1.0; color: Qt.darker(root.color, 1.02) }
        }
        border { width: 1; color: root.borderColor }

        // 顶边内高光（1px，左右让开圆角，免得压到弧线上）
        Rectangle {
            anchors {
                top: parent.top; left: parent.left; right: parent.right
                topMargin: 1
                leftMargin: Math.max(2, root.cornerRadius - 4)
                rightMargin: Math.max(2, root.cornerRadius - 4)
            }
            height: 1
            color: Qt.lighter(root.color, 1.09)
        }

        // 底边内压深（1px）：凸起的下缘
        Rectangle {
            anchors {
                bottom: parent.bottom; left: parent.left; right: parent.right
                bottomMargin: 1
                leftMargin: Math.max(2, root.cornerRadius - 4)
                rightMargin: Math.max(2, root.cornerRadius - 4)
            }
            height: 1
            // 参考样式里最抓眼睛的就是这条底边：比填充深一档（≈1.3:1），一条线就把
            // "这块浮着"说清楚了 —— 所以它比顶边高光重，别调轻。
            color: Qt.darker(root.color, 1.16)
        }
    }
}
