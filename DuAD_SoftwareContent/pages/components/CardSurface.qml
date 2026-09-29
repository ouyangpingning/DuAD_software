import QtQuick
import DuAD_Software

/*
    卡片底板 —— 「平台控制」页的统一样式（2026-09-28 美化）。

    为什么做成组件：这一页有 6 种卡片（两张平台连接卡、两张手动控制卡、预览卡、
    设置卡、协议卡）。样式散在 6 个文件里手抄"白底 + 描边 + 阴影"，迟早漂移；
    做成组件后改一处全页一致。

    ⚠ 为什么不用 QtQuick.Effects 的 RectangularShadow：它在最小探针脚本里一个像素都不画
      （`color:"#ff000000" blur:20 offset:(0,10)` 也空白）。但**别把探针结果当平台结论** ——
      docs/19 §32 记了我据此误判、又自我更正的完整经过。
      选普通矩形的真实理由很朴素：**更简单、零依赖，而且已经在页面渲染里验证过**，
      不必为了一个 2px 的底边去引一个特效模块（还得担心 Jetson 那边）。

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

    property color color: Colors.cardBg
    property color borderColor: Colors.cardBorderStrong
    property real cornerRadius: 12
    property bool shadowed: true
    // 阴影相对于卡片底边的露出量。2px 是实测"看得出浮起、又不显脏"的值。
    property int lift: 2

    // ── 阴影：y 起点比卡片低 lift+1，高度少 1，于是只在**底边**露出 lift 像素；
    //    左右各缩 1px，避免两侧边缘出现一条灰线（那看着像描边画歪了）。
    //    ⚠ 用 x/y/width/height 而不是 anchors：anchors.fill + topMargin 会同时
    //      改到上边，卡片自己的矩形也会跟着变（内容的锚点全部错位）。
    Rectangle {
        visible: root.shadowed
        x: 1
        y: root.lift + 1
        width: parent.width - 2
        height: parent.height - 1
        radius: root.cornerRadius
        color: Colors.cardShadow
    }

    Rectangle {
        id: surface
        anchors.fill: parent
        radius: root.cornerRadius
        color: root.color
        border { width: 1; color: root.borderColor }

        // 与原来各卡片上的 Behavior 一致（连上/断开时底色渐变，不瞬变）
        Behavior on color { ColorAnimation { duration: 200 } }
    }
}
