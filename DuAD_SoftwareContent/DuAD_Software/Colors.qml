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

    // ── 卡片 ────────────────────────────────────────────────
    property color cardBorder:             "#e0e0e0"
    property color cardDangerBg:           "#fef0f0"  // 已连接卡片背景（微红）
    property color cardDangerHover:        "#fdd9d9"  // 断开悬停背景（浅红）

    // ── 窗口 / 图标 ─────────────────────────────────────────
    property color windowBg:               "#ffffff"
    property color iconColor:              "#212121"   // SVG 图标染色

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
            accent: "#aee9e7", hover: "#D3E6ED",
            sidebar: "#eaf4f7", content: "#eaf4f7", page: "#ffffff",
            titleBar: "#2c3e50", titleBarHover: "#34495e",
            text1: "#212121", text2: "#5a5a5a", text3: "#7f8c8d",
            border: "#e0e0e0"
        },
        "ocean": {
            accent: "#7ab8d4", hover: "#c8ddf0",
            sidebar: "#e8f0f8", content: "#e8f0f8", page: "#ffffff",
            titleBar: "#1a3a5c", titleBarHover: "#2a5078",
            text1: "#1a2a3a", text2: "#4a6078", text3: "#8fa0b0",
            border: "#d0dae6"
        },
        "forest": {
            accent: "#7cc48a", hover: "#c8e6d0",
            sidebar: "#eaf5ec", content: "#eaf5ec", page: "#ffffff",
            titleBar: "#1e3a2f", titleBarHover: "#2a5040",
            text1: "#1a2e22", text2: "#4a6854", text3: "#8fb098",
            border: "#d0e0d4"
        },
        "sunset": {
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
        "statusConnected", "statusDisconnected",
        "cardBorder", "cardDangerBg", "cardDangerHover",
        "windowBg", "iconColor"
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

        // ── 卡片断开红：暗色用暗红避免刺眼 ──
        _tween("cardDangerBg",    dark ? "#3a2020" : "#fef0f0")
        _tween("cardDangerHover", dark ? "#4a2525" : "#fdd9d9")
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
