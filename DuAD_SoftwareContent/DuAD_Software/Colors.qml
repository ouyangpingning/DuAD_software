pragma Singleton
import QtQuick

QtObject {
    id: root

    // ============================================================
    // 所有 UI 颜色的唯一来源
    // 主题(setTheme: 亮/暗)控制中性色，配色(setPreset)控制强调色
    // 两者独立，切换互不干扰，最终由 _apply() 统一计算
    // 用法: import DuAD_Software; ... color: Colors.xxx
    // ============================================================

    // ── 标题栏 ──────────────────────────────────────────────
    property color titleBarBg:             "#2c3e50"
    property color titleBarText:           "#ecf0f1"
    property color titleBarBtnHover:       "#34495e"
    property color titleBarBtnText:        "#bdc3c7"
    property color titleBarCloseBtnHover:  "#e74c3c"
    property color titleBarCloseBtnText:   "#ffffff"

    // ── 侧边导航栏 ──────────────────────────────────────────
    property color sidebarBg:              "#eaf4f7"

    // ── 内容区域 ────────────────────────────────────────────
    property color contentBg:              "#eaf4f7"
    property color pageBg:                 "#ffffff"

    // ── 交互状态 ────────────────────────────────────────────
    property color interactiveHover:       "#D3E6ED"
    property color interactivePressed:     "#aee9e7"
    property color interactiveChecked:     "#aee9e7"

    // ── 文字 ────────────────────────────────────────────────
    property color textPrimary:            "#212121"
    property color textSecondary:          "#5a5a5a"
    property color textPlaceholder:        "#7f8c8d"

    // ── 状态色 ──────────────────────────────────────────────
    property color statusConnected:        "#27ae60"
    property color statusDisconnected:     "#e74c3c"
    // 「松 / 未使能」的软绿底（2026-09-29 用户要求：使能状态必须用颜色说话）：
    //   绿 = 电机不出力（可以手推平台调机械，安全）
    //   淡红（dangerSoft）= 已使能（闭环抱死、带电 —— 危险的那一侧）
    // 只补一个"底"，描边与文字直接用 statusConnected，与 dangerSoft 的用法对称；
    // ⚠ 不要拿它当"成功"用（那是语义污染，这个色是给"未使能"这一种状态用的）。
    property color successSoft:            "#e8f7ee"

    // ── 卡片 ────────────────────────────────────────────────
    property color cardBorder:             "#e0e0e0"
    property color cardDangerBg:           "#fef0f0"  // 已连接卡片背景（微红）
    property color cardDangerHover:        "#fdd9d9"  // 断开悬停背景（浅红）
    // 已连接卡片的**描边**：用淡红而不是 statusDisconnected 的正红 ——
    // 1px 的正红描边围一圈看着像报错（"这台是不是出故障了？"），
    // 而"已连接"其实是好状态，红的语义只是"再点一下就断开"。
    property color cardDangerBorder:       "#f0c8c8"

    // ── 窗口 / 图标 ─────────────────────────────────────────
    property color windowBg:               "#ffffff"
    property color iconColor:              "#212121"   // SVG 图标染色

    // ── 卡片层级（2026-09-28 平台控制页美化引入）────────────
    // 为什么要有这一组：原来卡片和页面都靠 contentBg/pageBg 两个色区分，
    // 卡片是淡蓝、页面是白，同色系、无描边 → 所有卡片都"平贴"在页面上，
    // 连接卡/控制卡/折叠节/错误条主次不分。
    // 现在改成三级层次：**页面（白）→ 卡片（白底 + 描边 + 硬阴影）→ 内嵌块（accentSoft）**。
    property color cardBg:                 "#ffffff"   // 卡片底（浮在页面上）
    property color cardBorderStrong:       "#dbe4e9"   // 卡片描边（比 cardBorder 清楚一档）
    property color cardShadow:             "#1a0f1c24" // 卡片投影：**纯矩形硬阴影**，不是 shader
    property color accentSoft:             "#D3E6ED"   // 图标 chip / 折叠头 / 选中块的软强调底
    // 按钮的"硬阴影"（下移 2px 的圆角矩形，按下去时消失 = 真的陷进去了）。
    // 比 cardShadow 深一档：卡片面积大，太深会脏；按钮小，太浅就看不出是按钮。
    property color buttonShadow:           "#2b0f1c24"
    property color textOnAccent:           "#ffffff"   // 强调底（急停红等）上的文字与图标

    // ── 面板层级（2026-10-08：齿轮 / 折叠"展开出来"的设置界面）──────
    // 为什么要有：这些面板原来跟卡片一样是**白底**，展开后贴在同样是白的页面上，
    // 只剩一圈 1px 描边 + 2px 投影 —— 用户原话"和背景一个颜色，没有立体感"。
    // 于是层次从三级补成四级：
    //   页面（白）→ 卡片（白底 + 描边 + 轻投影，**常驻**的）
    //   → **面板（深一档 + 顶边内阴影 + 底边内高光，点出来的）** → 内嵌块（accentSoft）
    // ⚠ panelBg 跟着**预设**走（从预设的 sidebar 再压深一档），四套配色各自同色系；
    //   暗色主题取"页面(#1e1e1e)与卡片(#2f2f2f)之间"的一档：比卡片深（用户要的"深一些"）、
    //   又比页面浅（否则整块陷进背景里，更看不出是一块面）。
    // ⚠ 面板里的**分隔线/输入框描边**要用 panelBorder，不要再用 cardBorder：
    //   cardBorder(#e0e0e0) 画在深一档的面板底上几乎看不见（这是"面板一深就散架"的坑）。
    property color panelBg:                "#dfe8eb"
    property color panelBorder:            "#c8d1d3"
    property color panelShadow:            "#330f1c24" // 面板投影：比 cardShadow 重一档
    // 输入框描边 / 开关"关"的轨道 / 滑块槽 —— 这组中性件**要同时坐在白卡片和面板上**。
    // 原来它们用 cardBorder(#e0e0e0) / cardBorderStrong(#dbe4e9)：在白卡片上刚好，
    // 一挪到深一档的面板上就"消失"（滑块只剩一个悬空的圆钮、开关只剩个钮）。所以单独
    // 提一档灰度出来 —— 白底上读得清，面板上也读得清。
    property color fieldBorder:            "#bcc9cf"

    // ── 实色强调（2026-10-08 UI 评审引入）──────────────────
    // 为什么要有：原来唯一的强调色 accent(#aee9e7) 只是"淡薄荷"，白底上对比度 ~1.4:1，
    // 选中的步长、当前导航项、主操作按钮在一片白卡片里分不出主次。
    // 现在分两档：**淡**（accentSoft / interactive*，大面积底色）+ **实**（下面这组，小面积点睛）。
    // 对比度下限由 tests/test_design_tokens.py 按"每套配色 × 亮/暗"逐一钉住。
    property color accent:                 "#0E7C86"   // 主按钮底、选中步长底、导航指示条
    property color accentHover:            "#0B6C75"
    property color accentPressed:          "#095A61"
    // 实色强调上的文字。亮色主题是白字；暗色主题强调色变亮、文字改深 ——
    // 所以**不能**复用 textOnAccent（那个是急停红上的白，任何主题都是白）
    property color accentContent:          "#ffffff"   // ⚠ 别命名 onXxx：QML 会当成信号处理器
    property color accentText:             "#0A6870"   // 强调色**文字**（比 accent 深一档，淡底上也读得清）

    // ⚠ 关于 cardShadow：**不要改用 QtQuick.Effects 的 RectangularShadow**。
    //   它在最小探针脚本里一个像素都不画（`color:"#ff000000" blur:20 offset:(0,10)` 也空白），
    //   但**探针证不了"平台不支持"**（docs/19 §32 记了我据此误判、又自我更正的经过）。
    //   选它的真实理由是：普通矩形**更简单、且已经在页面渲染里验证过**，
    //   不必为一个 2px 的底边引入一个特效模块。

    // ============================================================
    // 当前状态（theme 与 preset 独立）
    // ============================================================
    property string _currentTheme:  "light"
    property string _currentPreset: "default"

    // ============================================================
    // 配色数据 — 只定义"强调色"和亮色主题下的中性色
    // ============================================================
    property var _presets: ({
        "default": {
            strong: "#0E7C86", ink: "#0A6870", strongDark: "#4FC3CC",
            accent: "#aee9e7", hover: "#D3E6ED",
            sidebar: "#eaf4f7", content: "#eaf4f7", page: "#ffffff",
            titleBar: "#2c3e50", titleBarHover: "#34495e",
            text1: "#212121", text2: "#5a5a5a", text3: "#7f8c8d",
            border: "#e0e0e0"
        },
        "ocean": {
            strong: "#1F6FA8", ink: "#175A8A", strongDark: "#6AB0E0",
            accent: "#7ab8d4", hover: "#c8ddf0",
            sidebar: "#e8f0f8", content: "#e8f0f8", page: "#ffffff",
            titleBar: "#1a3a5c", titleBarHover: "#2a5078",
            text1: "#1a2a3a", text2: "#4a6078", text3: "#8fa0b0",
            border: "#d0dae6"
        },
        "forest": {
            strong: "#2E7D4F", ink: "#226640", strongDark: "#6CC58E",
            accent: "#7cc48a", hover: "#c8e6d0",
            sidebar: "#eaf5ec", content: "#eaf5ec", page: "#ffffff",
            titleBar: "#1e3a2f", titleBarHover: "#2a5040",
            text1: "#1a2e22", text2: "#4a6854", text3: "#8fb098",
            border: "#d0e0d4"
        },
        "sunset": {
            strong: "#A85A22", ink: "#8A4A1A", strongDark: "#E0A070",
            accent: "#d4a87a", hover: "#f0dcc8",
            sidebar: "#faf0e6", content: "#faf0e6", page: "#ffffff",
            titleBar: "#5c3a1e", titleBarHover: "#785030",
            text1: "#3a2a1a", text2: "#78604a", text3: "#b09880",
            border: "#e6d8c8"
        }
    })

    // ============================================================
    // 颜色过渡动画 — 主题/配色切换时所有颜色渐变而非瞬变（护眼）
    // 每个颜色属性对应一个 ColorAnimation，_tween() 按属性名启动。
    // 控件通过绑定 Colors.xxx 自动跟随过渡，无需在各处加 Behavior。
    // ============================================================
    property int animDuration: 250   // 过渡时长 ms，0 = 无动画（瞬变）

    // 动画对象在 onCompleted 动态创建（QtObject 无默认属性，不能声明子对象）
    property var _tweens: []
    property var _animProps: [
        "titleBarBg", "titleBarText", "titleBarBtnHover", "titleBarBtnText",
        "titleBarCloseBtnHover", "titleBarCloseBtnText",
        "sidebarBg", "contentBg", "pageBg",
        "interactiveHover", "interactivePressed", "interactiveChecked",
        "textPrimary", "textSecondary", "textPlaceholder",
        "statusConnected", "statusDisconnected", "successSoft",
        "cardBorder", "cardDangerBg", "cardDangerHover", "cardDangerBorder",
        "windowBg", "iconColor",
        "cardBg", "cardBorderStrong", "cardShadow", "accentSoft", "textOnAccent",
        "buttonShadow",
        "panelBg", "panelBorder", "panelShadow", "fieldBorder",
        "accent", "accentHover", "accentPressed", "accentContent", "accentText"
    ]

    Component.onCompleted: {
        for (var i = 0; i < _animProps.length; i++) {
            var anim = Qt.createQmlObject(
                "import QtQuick; ColorAnimation {}",
                root, "ColorsAnim" + i)
            if (anim) {
                anim.target = root
                anim.property = _animProps[i]
                _tweens.push(anim)
            }
        }
    }

    // 过渡赋值：存在对应动画则启动，否则直接赋值（animDuration=0 时也直接赋值）
    function _tween(prop, to) {
        if (root.animDuration <= 0) {
            root[prop] = to
            return
        }
        for (var i = 0; i < _tweens.length; i++) {
            if (_tweens[i].property === prop) {
                _tweens[i].duration = root.animDuration
                _tweens[i].to = to
                _tweens[i].start()
                return
            }
        }
        root[prop] = to
    }

    // ============================================================
    // 统一应用 — 由 theme + preset 重新计算所有颜色
    // ============================================================
    function _apply() {
        var p = _presets[_currentPreset] || _presets["default"]
        var dark = (_currentTheme === "dark")

        // ── 中性色：暗色固定灰阶，亮色用预设色板 ──
        _tween("sidebarBg",   dark ? "#242424" : p.sidebar)
        _tween("contentBg",   dark ? "#2a2a2a" : p.content)
        _tween("pageBg",      dark ? "#1e1e1e" : p.page)
        _tween("textPrimary", dark ? "#e8e8e8" : p.text1)
        _tween("textSecondary", dark ? "#b0b0b0" : p.text2)
        _tween("textPlaceholder", dark ? "#808080" : p.text3)
        _tween("cardBorder",  dark ? "#3d3d3d" : p.border)
        _tween("windowBg",    dark ? "#1e1e1e" : p.page)
        _tween("iconColor",   dark ? "#d0d0d0" : p.text1)

        // ── 标题栏 ─────────────────────────────────
        _tween("titleBarBg",  dark ? "#1a1a1a" : p.titleBar)
        _tween("titleBarBtnHover", dark ? "#2d2d2d" : p.titleBarHover)
        _tween("titleBarText", dark ? "#e0e0e0" : "#ecf0f1")
        _tween("titleBarBtnText", dark ? "#9a9a9a" : "#bdc3c7")
        _tween("titleBarCloseBtnHover", "#e74c3c")
        _tween("titleBarCloseBtnText", "#ffffff")

        // ── 强调色：亮色直接用预设色，暗色用预设色的暗化版 ──
        if (dark) {
            _tween("interactiveHover",   Qt.darker(p.accent, 2.2))   // 深色调 hover
            _tween("interactivePressed", Qt.darker(p.accent, 3.2))   // 更深选中态
            _tween("interactiveChecked", Qt.darker(p.accent, 3.2))
        } else {
            _tween("interactiveHover",   p.hover)
            _tween("interactivePressed", p.accent)
            _tween("interactiveChecked", p.accent)
        }

        // ── 状态色：暗色用亮版本提高可读性 ──
        _tween("statusConnected", dark ? "#4cd964" : "#27ae60")
        _tween("statusDisconnected", dark ? "#ff6b6b" : "#e74c3c")
        // 未使能的软绿底：暗色下压成深绿底，statusConnected 的亮绿字才读得清
        _tween("successSoft", dark ? "#1b3527" : "#e8f7ee")

        // ── 卡片断开红：暗色用暗红避免刺眼 ──
        _tween("cardDangerBg",    dark ? "#3a2020" : "#fef0f0")
        _tween("cardDangerHover", dark ? "#4a2525" : "#fdd9d9")
        _tween("cardDangerBorder", dark ? "#6a3a3a" : "#f0c8c8")

        // ── 卡片层级：描边由预设 border 派生（换配色时描边跟着色系走）──
        _tween("cardBg",           dark ? "#2f2f2f" : "#ffffff")
        _tween("cardBorderStrong", dark ? "#4a4a4a" : Qt.darker(p.border, 1.14))
        // 投影用中性深色（不跟配色走）：它是"阴影"不是"品牌色"
        _tween("cardShadow",       dark ? "#45000000" : "#1a0f1c24")
        _tween("buttonShadow",     dark ? "#66000000" : "#2b0f1c24")
        // 暗色用 ×3.2（与 interactiveChecked 同档）而不是 ×2.2：
        // ×2.2 的底上放亮强调字只有 ~2.9:1，导航选中项读不清
        _tween("accentSoft",       dark ? Qt.darker(p.accent, 3.2) : p.hover)
        // ── 面板层级：预设 sidebar 压深一档（跟着配色走）；暗色取页面与卡片之间 ──
        _tween("panelBg",          dark ? "#2a2a2a" : Qt.darker(p.sidebar, 1.055))
        _tween("panelBorder",      dark ? "#454545" : Qt.darker(p.sidebar, 1.17))
        _tween("panelShadow",      dark ? "#8c000000" : "#330f1c24")
        _tween("fieldBorder",      dark ? "#4a4a4a" : "#bcc9cf")
        // 强调底上的文字：急停红、深色强调块上永远要白字，暗色主题也不例外
        _tween("textOnAccent",     "#ffffff")

        // ── 实色强调：亮色 = 深强调 + 白字；暗色 = 亮强调 + 深字 ──
        var strong = dark ? p.strongDark : p.strong
        _tween("accent",        strong)
        _tween("accentHover",   dark ? Qt.lighter(strong, 1.08) : Qt.darker(strong, 1.12))
        _tween("accentPressed", dark ? Qt.darker(strong, 1.12) : Qt.darker(strong, 1.3))
        _tween("accentContent",      dark ? "#10181a" : "#ffffff")
        _tween("accentText",    dark ? p.strongDark : p.ink)
    }

    // ============================================================
    // 主题切换 — 亮色 / 暗色 / 跟随系统
    // ============================================================
    function setTheme(name) {
        if (name === "system") name = "light"  // TODO: 对接系统 API
        if (name === _currentTheme) return
        _currentTheme = name
        _apply()
    }

    // ============================================================
    // 配色切换
    // ============================================================
    function setPreset(name) {
        if (name === "system") name = "default"  // TODO: 对接系统 API
        if (name === _currentPreset) return
        _currentPreset = name
        _apply()
    }
}
