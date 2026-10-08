import DuAD_Software
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

/*
    侧边导航项。

    2026-10-08 评审改版：原来是"椭圆图标 + 文字"整体居中，选中态只有图标外一圈淡薄荷
    （#aee9e7，与悬停色几乎分不出）；且各项文字长短不同、居中后左边缘参差不齐。
    现在：**整行**淡强调底 + 左侧 3px 实色指示条 + 强调色粗体字；图标与文字左对齐。

    2026-10-08 二次改版（"阴影" → "流动柔光"）：上一版选中/悬停是一块**硬边实色矩形**
    （4px 圆角 + 160ms 线性换色），贴在浅色侧栏上边界很"生"，切页时像开关一样"啪"地亮起来。
    现在整块底色都改成**柔光**（渐变 + 透明度），没有硬边、没有投影：

      ① 静止态 —— 左实右虚的强调渐变：左边压着指示条是实的，向右**渐隐**到侧栏底色，
         所以药丸没有"右边那条硬边"；没有阴影也不觉得是贴纸。
      ② 悬停态 —— 同样右端渐隐的圆角底，透明度缓入缓出（170ms，OutCubic），不闪。
      ③ 选中瞬间 —— 一条**更亮的柔光带**从左向右扫过（`flow` 走一趟 + `band` 先亮后灭），
         像光流进这一行，扫完自己消失；同时左侧指示条从 8px 小圆条"长"成整条。

    ⚠ 为什么这里不复用 CardSurface/ThemedButton 的**矩形硬阴影**：那个是给"浮在卡片上的
      按钮"用的；导航项贴在侧栏浅底上，再叠一层投影只会脏，层次交给**柔光**就够了。
    ⚠ 光带用 `Qt.lighter(accentSoft, ×)` 而不是实色 accent：亮/暗两套主题下它都比底色更亮，
      才读得成"光"；用实色 accent 在亮色主题里是一条**更深的**色带，压在字上还会糊字。
      `band` = 0 时光带颜色**恰好等于** accentSoft，所以静止态不会在底色里留一个透明豁口。
    ⚠ 渐变的"透明端"必须写成**同 RGB + alpha=0**：直接写 `"transparent"` 是透明黑，
      Qt 会让渐变中段发灰。色值仍只来自 Colors，不硬编码。
    ⚠ 图标尺寸用 implicitWidth/Height 给死（IconImage 创建后被改尺寸会画不出来，§19-35），
      用锚点摆放而不是进 Layout（进 Layout 会被覆盖成 implicitWidth）。
*/
Button {
    id: control

    property url iconsource: ""
    property bool collapsed: false // 侧边栏收起时只显示图标
    readonly property bool _active: control.checked || control.pressed

    // ── 柔光色：全部从 Colors 令牌**推**出来，不新增硬编码色值 ──
    readonly property color _hoverFill: control._active ? Colors.interactivePressed
                                                       : Colors.interactiveHover
    readonly property color _hoverFade: Qt.rgba(_hoverFill.r, _hoverFill.g, _hoverFill.b, 0)
    readonly property color _softFade: Qt.rgba(Colors.accentSoft.r, Colors.accentSoft.g,
                                               Colors.accentSoft.b, 0)
    readonly property color _sheenBase: Qt.lighter(Colors.accentSoft, 1.28)

    flat: true
    checkable: true
    Layout.fillWidth: true
    implicitHeight: 44

    // 选中时让柔光带走一趟（取消选中不用扫，淡出就够了）
    onCheckedChanged: if (control.checked) flowSweep.restart()

    background: Item {
        id: bg

        // ── ① 悬停层：右端渐隐，无硬边 ──
        Rectangle {
            anchors.fill: parent
            radius: 10
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: control._hoverFill }
                GradientStop { position: 0.70; color: control._hoverFill }
                GradientStop { position: 1.0; color: control._hoverFade }
            }
            opacity: control.hovered && !control.checked ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: 170; easing.type: Easing.OutCubic } }
        }

        // ── ② 选中层：左实右虚 + 扫过的柔光带 ──
        Rectangle {
            id: glowLayer
            anchors.fill: parent
            radius: 10
            opacity: control.checked ? 1 : 0

            // flow = 光带中心（0~1）；band = 光带亮度（静止 0，扫过时亮起再灭）
            // ⚠ `flow` 还兼任第 4 个渐变色标的起点（= flow+0.20，见下），所以它**不能停在
            //   0.84**：那样右端只剩 13px 的渐隐，又变成一条硬边。扫完必须回到 0.26，
            //   静止态才是"实到 56% 再往右化开 90px"的柔光底。
            property real flow: 0.26
            property real band: 0.0

            // 光带颜色 = accentSoft 朝"更亮的 accentSoft"按 band 混，band=0 时与底色同色
            readonly property color sheen: Qt.rgba(
                Colors.accentSoft.r + (control._sheenBase.r - Colors.accentSoft.r) * band,
                Colors.accentSoft.g + (control._sheenBase.g - Colors.accentSoft.g) * band,
                Colors.accentSoft.b + (control._sheenBase.b - Colors.accentSoft.b) * band, 1.0)

            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: Colors.accentSoft }
                GradientStop {
                    position: Math.max(0.0, glowLayer.flow - 0.20)
                    color: Colors.accentSoft
                }
                GradientStop { position: glowLayer.flow; color: glowLayer.sheen }
                GradientStop {
                    position: Math.min(0.94, glowLayer.flow + 0.20)
                    color: Colors.accentSoft
                }
                GradientStop { position: 1.0; color: control._softFade }
            }

            SequentialAnimation {
                id: flowSweep

                ParallelAnimation {
                    NumberAnimation {
                        target: glowLayer
                        property: "flow"
                        from: 0.04
                        to: 0.84
                        duration: 880
                        easing.type: Easing.InOutSine
                    }
                    SequentialAnimation {
                        NumberAnimation {
                            target: glowLayer
                            property: "band"
                            from: 0.0
                            to: 0.72
                            duration: 300
                            easing.type: Easing.OutCubic
                        }
                        PauseAnimation { duration: 300 }
                        NumberAnimation {
                            target: glowLayer
                            property: "band"
                            to: 0.0
                            duration: 420
                            easing.type: Easing.InOutSine
                        }
                    }
                }

                // 光带已经灭了，这里只是把 flow 挪回静止位（看不见，但决定右端渐隐有多长）
                NumberAnimation {
                    target: glowLayer
                    property: "flow"
                    to: 0.26
                    duration: 200
                    easing.type: Easing.Linear
                }
            }

            Behavior on opacity { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
        }

        // ── ③ 选中指示条：小圆条"长"成整条。收起状态下也保留 ——
        //      只剩图标时它是唯一的"你在这页"线索 ──
        Rectangle {
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            width: 3
            radius: 1.5
            color: Colors.accent
            height: control.checked ? Math.max(8, bg.height - 16) : 8
            opacity: control.checked ? 1 : 0

            Behavior on height { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }
            Behavior on opacity { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
        }
    }

    contentItem: Item {
        IconImage {
            id: icon

            implicitWidth: 22
            implicitHeight: 22
            width: 22
            height: 22
            x: control.collapsed ? (parent.width - width) / 2 : 10
            anchors.verticalCenter: parent.verticalCenter
            source: control.iconsource
            visible: control.iconsource.toString() !== ""
            color: control.checked ? Colors.accentText : Colors.iconColor
        }

        Text {
            anchors.left: icon.right
            anchors.leftMargin: 12
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            text: control.text
            color: control.checked ? Colors.accentText : Colors.textPrimary
            font.pixelSize: 14
            font.bold: control.checked
            elide: Text.ElideRight // 英文长文本自动截断加省略号
            visible: !control.collapsed

            Behavior on color { ColorAnimation { duration: 200; easing.type: Easing.OutCubic } }
        }
    }
}
