# 06 AppBridge 与跨页状态

## AppBridge 是什么

`DuAD_SoftwareContent/main.py` 里定义的一个 Python 类（继承 QObject），通过
`engine.rootContext().setContextProperty("AppBridge", bridge)` 注册为 **QML 上下文属性**，
让 QML 里任何地方都能直接调用它。

它承担三类职责：
1. **语言切换**（setLanguage / languageIndex）
2. **文件系统辅助**（homeDir / isDir，QML 无文件系统 API）
3. **跨页状态中枢**（cameraConnected / collectingOwner / algorithmEnabled）

## Python↔QML 三种交互方式

| 机制 | Python 侧写法 | QML 侧用法 | 用途 |
|---|---|---|---|
| **Property** | `name = Property(type, getter, setter, notify=signal)` | `AppBridge.name` 读、`AppBridge.name = v` 写 | 状态共享 |
| **Slot** | `@Slot(int, result=bool) def f(self, x): ...` | `AppBridge.f(1)` 调用并拿返回值 | 函数调用 |
| **Signal** | `sig = Signal()` | `AppBridge.sig.connect(fn)` / 直接调用 | 事件通知 |

```python
class AppBridge(QObject):
    # ① 信号（notify 用，属性变化时发）
    cameraConnectedChanged = Signal()

    def __init__(self, ...):
        self._cameraConnected = False

    # ② 属性：getter + setter + notify
    def _getCameraConnected(self): return self._cameraConnected
    def _setCameraConnected(self, v):
        if self._cameraConnected != bool(v):
            self._cameraConnected = bool(v)
            self.cameraConnectedChanged.emit()   # 通知 QML 绑定刷新

    cameraConnected = Property(bool, _getCameraConnected,
                               _setCameraConnected, notify=cameraConnectedChanged)
```

**知识点**：
- **notify 信号是绑定刷新机制**：QML 里 `color: AppBridge.cameraConnected ? ... : ...` 这类绑定，只有 notify 信号发出时才重新求值。没有 notify 的属性 QML 读一次后不再更新。
- **setter 装饰器写法**（测试里常用）：`@Property(bool, notify=...)` + `@xxx.setter`。
- **QML 调用 Python 信号直接函数调用**：`AppBridge.helpRequested()`（不能 `.emit()`）。
- **GC 陷阱**：Python 侧必须**保持对象引用**（`bridge = AppBridge(...)` 存变量），否则被垃圾回收后 QML 侧 AppBridge 变 null——点击回调静默失败（表现像"点了没反应"）。

## 跨页状态中枢

### 为什么需要

6 个页面最初各自独立模拟状态（CameraPage 自己模拟连接、CollectPage 自己模拟采集），
页面之间互不知道。真实相机只有一个采集会话，必须全局统一。

### 采集会话互斥仲裁（collectingOwner）

**问题**：数据采集（CollectPage）和实时检测（DetectPage）都控制相机采集，但相机只有一台。

**方案**：`AppBridge.collectingOwner` 全局仲裁（`""` / `"collect"` / `"detect"`）：

```python
def _setCollectingOwner(self, value):
    v = str(value) if value else ""
    if v not in ("", "collect", "detect"):
        return                       # 非法值忽略
    if self._collectingOwner != v:
        self._collectingOwner = v
        self.collectingOwnerChanged.emit()
        self._setCollecting(bool(v)) # collecting 是 owner 的派生状态
```

两个页面的采集按钮都只是"申请/释放"：
```qml
Button {
    checked: AppBridge.collectingOwner === "detect"   // 本页持有才自锁
    onClicked: AppBridge.collectingOwner = checked ? "" : "detect"
}
```
**后按者抢占**：Collect 采集中点 Detect 的开始采集 → owner 变 "detect" → Collect 的按钮
因为绑定同一状态**自动弹起**、计时器自动停。无需任何页面间通信代码。

### 状态流转图

```
CameraPage 搜索连接 ──→ cameraConnected = true
                             │
CollectPage 点采集 ──→ collectingOwner = "collect" ──→ Collect 定时保存开始
DetectPage 点采集 ──→ collectingOwner = "detect"  ──→ Detect 图像区激活
                            │（任一页再点 → "" 释放）
DetectPage 算法开关 ──→ algorithmEnabled（后端推理线程轮询，防卡死）
```

## 使用模式

页面读取状态（绑定，自动响应）：
```qml
readonly property bool _collecting: AppBridge.collectingOwner === "collect"
Timer { running: _collecting }   // 状态一变，Timer 自动启停
```

页面写入状态（操作触发）：
```qml
onClicked: AppBridge.collectingOwner = _collecting ? "" : "collect"
```
