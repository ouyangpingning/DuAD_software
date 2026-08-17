# 02 QML 模块与主题

## QML 模块（import DuAD_Software）

`DuAD_SoftwareContent/DuAD_Software/` 是一个 **QML 模块**（不是页面目录），包含：

```
DuAD_Software/
├── qmldir        # 模块清单：声明 module 名 + 单例类型
├── Colors.qml    # 颜色单例（所有 UI 颜色的唯一来源）
└── Constants.qml # 常量单例（窗口尺寸、字体等）
```

**qmldir 内容**：
```
module DuAD_Software
singleton Constants 1.0 Constants.qml
singleton Colors 1.0 Colors.qml
```

**关键规则**：模块目录名必须与 `module` 声明名一致（引擎按目录名找模块）。main.py 里
`engine.addImportPath(DuAD_SoftwareContent)` 让引擎能在此路径下找到 `DuAD_Software/` 目录。
之后所有 QML 文件里写 `import DuAD_Software` 就能用 `Colors.xxx`。

**知识点 — QML 单例（pragma Singleton）**：
```qml
pragma Singleton        // 声明为单例：全应用只有一个实例，任何地方直接访问
import QtQuick

QtObject {              // 非可视化对象容器
    property color pageBg: "#ffffff"
}
```
- 单例没有 id、不能被实例化，用 `Colors.pageBg` 直接访问
- QtObject 适合放"纯数据"逻辑（颜色、常量、配置）

## Colors 单例 — 主题与配色

### 功能
- 管理亮色/暗色主题 + 4 套配色（default/ocean/forest/sunset）
- 运行时切换（设置页下拉框触发 `Colors.setTheme("dark")` / `setPreset("ocean")`）
- **切换时有 250ms 渐变动画**（不是瞬间跳变）

### 实现方式

```qml
pragma Singleton
import QtQuick

QtObject {
    id: root
    // ① 对外暴露的颜色属性（控件绑定它们）
    property color pageBg: "#ffffff"
    property color textPrimary: "#212121"
    // ... 共 22 个颜色属性

    // ② 预设数据表
    property var _presets: ({
        "default": { accent: "#aee9e7", page: "#ffffff", ... },
        "ocean":   { accent: "#7ab8d4", page: "#ffffff", ... },
        ...
    })

    // ③ 动画对象表（每个颜色属性一个 ColorAnimation）
    property var _animProps: ["titleBarBg", "contentBg", ...]
    Component.onCompleted: {
        for (var i = 0; i < _animProps.length; i++) {
            var anim = Qt.createQmlObject("import QtQuick; ColorAnimation {}", root, ...)
            anim.target = root
            anim.property = _animProps[i]
            _tweens.push(anim)
        }
    }

    // ④ 过渡赋值：有动画就走动画，否则直接赋值
    function _tween(prop, to) {
        if (root.animDuration <= 0) { root[prop] = to; return }
        for (var i = 0; i < _tweens.length; i++) {
            if (_tweens[i].property === prop) {
                _tweens[i].to = to
                _tweens[i].start()
                return
            }
        }
        root[prop] = to
    }

    // ⑤ 切换入口
    function setTheme(name) {
        if (name === _currentTheme) return
        _currentTheme = name
        _apply()   // 重新计算全部颜色 → _tween 过渡
    }
}
```

### 为什么控件会自动渐变？

所有控件的颜色都是**绑定**（`color: Colors.pageBg`）。动画逐帧修改 Colors 的属性值，
绑定自动跟随每帧新值 → 全界面颜色同步渐变，**无需给每个控件加动画**。

### 知识点

- **QML 绑定（Binding）**：`color: Colors.pageBg` 不是"赋值一次"，而是**保持追踪**——Colors.pageBg 一变，控件颜色跟着变。这是 QML 的响应式核心。
- **属性动画（ColorAnimation）**：作用在某个对象的某属性上，从当前值插值到 `to` 值。动画对象需要 `target`（谁）+ `property`（哪个属性）。
- **为什么动画对象要动态创建**：`QtObject` 没有默认属性，不能在它内部直接声明子对象（QML 会报错），所以用 `Qt.createQmlObject` 在 `Component.onCompleted` 里动态创建。
- **`Qt.darker(color, factor)`**：暗色主题下把强调色变暗，得到配套的 hover/按下色。

### 踩坑记录

- 数组字面量里不能声明 QML 对象（`property var x: [ColorAnimation {...}]` 非法）→ 必须先建对象再收集到数组。
- 信号处理器（`onXxxChanged`）必须挂在**声明该属性的对象**上，不能挂在子对象里（如 Canvas 里写 `onFrameSeedChanged` 会报"non-existent property"）。
