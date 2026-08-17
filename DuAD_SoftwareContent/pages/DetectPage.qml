import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import DuAD_Software
import "components"

/*
    异常检测页 — 实时采集 + 实时推理显示。

    布局：主体只有两个实时图像窗口（原图 / 异常热力图，按相机实际分辨率
    比例缩放居中）+ 底部状态信息；所有控制操作（采集、算法、ROI、阈值）
    收在右侧点击展开/隐藏的侧边栏中。

    状态来源（跨页共享）：
      AppBridge.cameraConnected — CameraPage 写入
      AppBridge.collectingOwner — 采集会话互斥仲裁（"detect" = 本页持有），
      与 CollectPage 数据采集互斥；main.py 监听到 owner 变化后调用
      CameraBridge.startGather/stopGather 并启停 DetectBridge 推理管线
      AppBridge.algorithmEnabled — 本页算法开关，DetectBridge 后台线程消费

    帧数据流：
      原图   CameraBridge.frameIndex → image://camera/original?t=<index>
      热力图 DetectBridge.resultCounter → image://camera/heatmap?t=<counter>
      定位图 DetectBridge.maskCounter → image://camera/mask?t=<counter>

    ROI 流程：原图标题栏[▭] → 图上拖拽 → 框右下角[确定]直接应用；归一化
    坐标交给 CameraBridge.applyRoi 换算相机像素并按宽 8/高 2 步进对齐。
    应用后标题栏[↺]恢复全幅。
*/
Item {
    id: root

    // ============================================================
    // 状态（AppBridge 驱动）
    // ============================================================
    readonly property bool _collecting: AppBridge.collectingOwner === "detect"
    readonly property bool _imageActive: AppBridge.cameraConnected && _collecting
    property bool _panelOpen: false        // 侧边隐藏栏展开
    property bool _roiMode: false          // ROI 绘制模式
    property real _score: 0                // 异常分数（实时推理或测试推理）
    property real _captureFps: 0           // 1s 定时读取的当前采集帧率
    property string _fullscreenKind: ""    // "" / "origin" / "heatmap"
    property bool _roiApplied: false       // 当前是否已应用 ROI（标题栏恢复按钮高亮）

    readonly property string _fullscreenTitle:
        root._fullscreenKind === "origin" ? qsTr("原图")
        : (root._fullscreenKind === "heatmap"
            ? (root._pixelMaskEnabled ? qsTr("异常定位") : qsTr("异常热力图"))
            : "")

    readonly property bool _fullscreenSimulated: !root._testActive && !root._imageActive
    readonly property bool _fullscreenImageActive: {
        if (root._fullscreenKind === "origin") {
            return root._testActive
                ? true : (root._imageActive && CameraBridge.frameIndex > 0)
        }
        if (root._fullscreenKind === "heatmap") {
            return root._testActive ? true
                : (root._imageActive && AppBridge.algorithmEnabled
                    && (root._pixelMaskEnabled
                        ? DetectBridge.hasMask : DetectBridge.hasResult))
        }
        return false
    }
    readonly property string _fullscreenSource: {
        if (root._fullscreenKind === "origin") {
            if (root._testActive) return "file://" + root._testImagePath
            return root._imageActive ? root._liveOriginSource : ""
        }
        if (root._fullscreenKind === "heatmap") {
            if (root._testActive) {
                if (root._pixelMaskEnabled)
                    return root._testMaskPath.length > 0
                        ? ("file://" + root._testMaskPath) : ""
                return root._testHeatmapPath.length > 0
                    ? ("file://" + root._testHeatmapPath) : ""
            }
            if (root._imageActive && AppBridge.algorithmEnabled)
                return root._pixelMaskEnabled
                    ? root._liveMaskSource : root._liveHeatmapSource
        }
        return ""
    }

    // 测试推理状态（单图 ONNX 推理）
    property bool _testActive: false       // 测试结果是否显示在图像窗口
    property bool _inferring: false        // 推理执行中
    property int _testSession: 0           // 测试推理请求代号：开始实时采集/换图/卸载模型时自增
    property int _testActiveSession: -1    // 当前有效测试请求的代号
    property string _testImagePath: ""     // 测试图片路径
    property string _testHeatmapPath: ""   // 推理热力图路径
    property string _testMaskPath: ""      // 二值掩模叠加图路径（异常像素红色高亮定位）
    property bool _pixelMaskEnabled: false // 像素阈值定位开关（默认关=显示热力图）
    property bool _pixelRefineEnabled: false // 精细定位（掩模阈值收窄到 max(F1,P99)）

    // 应用 ROI：普通窗口与全屏窗口共用同一处理逻辑
    function _applyRoi(rect) {
        console.log("[DEBUG] DetectPage: ROI 应用 — 归一化",
                    rect.x.toFixed(3), rect.y.toFixed(3),
                    rect.width.toFixed(3), rect.height.toFixed(3))
        root._roiMode = false
        root._roiApplied = true
        roiOverlay.clearRoi()
        if (fullscreenRoiOverlay)
            fullscreenRoiOverlay.clearRoi()
        CameraBridge.applyRoi(rect.x, rect.y, rect.width, rect.height)
    }

    // 实时采集优先：从测试推理切到实时模式时清空测试画面
    function _enterLiveMode() {
        root._testSession++
        root._testActiveSession = -1
        root._testActive = false
        root._inferring = false
        root._testHeatmapPath = ""
        root._testMaskPath = ""
        root._score = 0
    }

    // 相机分辨率比例：跟随 CameraPage/ROI 写回的实际 GX_INT_WIDTH/HEIGHT。
    // 未读到相机几何时回退 MER2 全幅 2448×2048（老页面模拟值 1024/1224 已废弃）。
    readonly property real _camRatio: {
        var w = CameraBridge.imageWidth
        var h = CameraBridge.imageHeight
        return (w > 0 && h > 0) ? (w / h) : (2448 / 2048)
    }

    // 实时帧 provider URL（计数器每次变化 → QML 重新取图）
    readonly property string _liveOriginSource:
        "image://camera/original?t=" + CameraBridge.frameIndex
    readonly property string _liveHeatmapSource:
        "image://camera/heatmap?t=" + DetectBridge.resultCounter
    readonly property string _liveMaskSource:
        "image://camera/mask?t=" + DetectBridge.maskCounter

    // ── 算法桥信号接线（测试推理结果）──
    Component.onCompleted: {
        AlgorithmBridge.inferenceReady.connect(function(score, heatmapPath) {
            if (root._collecting || root._testSession !== root._testActiveSession) {
                console.log("[DetectPage] 忽略过期的测试推理结果")
                return
            }
            root._inferring = false
            root._score = score
            root._testHeatmapPath = heatmapPath
            root._testActive = true
            console.log("[DetectPage] 测试推理完成 分数:", score.toFixed(4))
        })
        AlgorithmBridge.maskReady.connect(function(maskPath) {
            if (root._collecting || root._testSession !== root._testActiveSession)
                return
            root._testMaskPath = maskPath
            console.log("[DetectPage] 测试推理完成 掩模定位图:", maskPath)
        })
        AlgorithmBridge.inferenceError.connect(function(msg) {
            if (root._collecting || root._testSession !== root._testActiveSession) {
                console.log("[DetectPage] 忽略过期测试推理错误:", msg)
                return
            }
            root._inferring = false
            console.log("[DetectPage] 测试推理错误:", msg)
        })
        AlgorithmBridge.modelReady.connect(function(modelName) {
            console.log("[DetectPage] 模型已切换:", modelName)
        })
        AlgorithmBridge.modelUnloaded.connect(function() {
            root._testSession++
            root._testActiveSession = -1
            root._testActive = false
            root._inferring = false
            root._testHeatmapPath = ""
            root._testMaskPath = ""
            if (!root._collecting)
                root._score = 0
            console.log("[DetectPage] 模型已卸载")
        })

        // 实时推理分数与错误提示（DetectBridge 由 main.py 注册）
        DetectBridge.scoreReady.connect(function(score) {
            if (root._collecting)
                root._score = score
        })
        DetectBridge.inferenceFailed.connect(function(msg) {
            console.log("[DetectPage] 实时推理错误:", msg)
        })
    }

    // 图像窗口标题栏按钮：无文字，后续可替换为图标
    component HeaderIconButton: Button {
        id: headerBtn
        property string glyph: ""
        property string tip: ""

        implicitWidth: 28
        implicitHeight: 28
        // checked 只由外部 binding 控制；不要设 checkable=true，否则
        // Button 内部自动切换会破坏 binding。
        font.pixelSize: 13

        ToolTip.visible: headerBtn.hovered
        ToolTip.text: headerBtn.tip
        ToolTip.delay: 500

        contentItem: Text {
            text: headerBtn.glyph
            font: headerBtn.font
            color: !headerBtn.enabled ? Colors.textPlaceholder
                : (headerBtn.checked ? "#ffffff" : Colors.textPrimary)
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }

        background: Rectangle {
            radius: 5
            color: !headerBtn.enabled ? Colors.pageBg
                : (headerBtn.checked
                    ? Colors.statusDisconnected
                    : (headerBtn.hovered ? Colors.interactiveHover : Colors.contentBg))
        }
    }

    Rectangle {
        anchors.fill: parent
        color: Colors.pageBg

        ColumnLayout {
            // rightMargin 跟随侧栏宽度（动画同步）：展开时内容区右移避让，不被遮挡
            anchors { fill: parent; margins: 20; rightMargin: 20 + sidePanel.width }
            spacing: 10

            // ── 标题行 ──────────────────────────────
            Text {
                text: qsTr("异常检测")
                font.pixelSize: 16; font.bold: true
                color: Colors.textPrimary
            }

            // ── 双实时图像窗口（1024:1224 比例，随窗口缩放）──
            Item {
                Layout.fillWidth: true
                Layout.fillHeight: true

                // 窗口宽 = min(图像区高度允许宽, 水平一半)，高 = 宽/比例。
                // 注意用 Item 自身 width/height（不能用 parent —— 那是 ColumnLayout
                // 总高，含标题与状态栏，会导致窗口溢出覆盖上下区域）
                readonly property real _winW: Math.min(
                    height * root._camRatio, (width - 12) / 2)
                readonly property real _winH: _winW / root._camRatio

                // 原图窗口
                Rectangle {
                    width: parent._winW
                    height: parent._winH
                    anchors { left: parent.left; verticalCenter: parent.verticalCenter }
                    radius: 10
                    color: Colors.contentBg
                    border { width: 0; color: Colors.cardBorder }

                    ColumnLayout {
                        anchors.fill: parent
                        spacing: 6

                        // 标题与工具按钮同一行（左侧标题，右侧 ROI/全屏）
                        RowLayout {
                            Layout.fillWidth: true
                            Layout.leftMargin: 16
                            Layout.rightMargin: 12
                            Layout.topMargin: 8
                            spacing: 8

                            Text {
                                text: qsTr("原图")
                                font.pixelSize: 12; font.bold: true
                                color: Colors.textPrimary
                            }

                            Item { Layout.fillWidth: true }

                            HeaderIconButton {
                                objectName: "originRoiButton"
                                glyph: "▭"
                                tip: qsTr("ROI 绘制")
                                checked: root._roiMode
                                enabled: root._imageActive
                                onClicked: {
                                    if (root._roiMode) {
                                        root._roiMode = false
                                        roiOverlay.cancelDrawing()
                                        fullscreenRoiOverlay.cancelDrawing()
                                    } else {
                                        root._roiMode = true
                                    }
                                }
                            }

                            HeaderIconButton {
                                objectName: "originRoiResetButton"
                                glyph: "↺"
                                tip: qsTr("恢复全幅")
                                enabled: AppBridge.cameraConnected
                                checked: root._roiApplied
                                onClicked: {
                                    root._roiMode = false
                                    root._roiApplied = false
                                    roiOverlay.cancelDrawing()
                                    roiOverlay.clearRoi()
                                    fullscreenRoiOverlay.cancelDrawing()
                                    fullscreenRoiOverlay.clearRoi()
                                    CameraBridge.resetRoi()   // 相机侧恢复全幅并自动重开采集
                                }
                            }

                            HeaderIconButton {
                                objectName: "originFullscreenButton"
                                glyph: "⛶"
                                tip: qsTr("全屏")
                                checked: root._fullscreenKind === "origin"
                                onClicked: {
                                    root._fullscreenKind =
                                        (root._fullscreenKind === "origin" ? "" : "origin")
                                }
                            }
                        }

                        Item {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            Layout.margins: 8

                            ImageView {
                                id: originView
                                anchors.fill: parent
                                // 测试模式显示测试图片；实时采集显示相机 provider 帧
                                simulated: !root._testActive && !root._imageActive
                                simulatedHue: 0.55
                                imageActive: root._testActive
                                    ? true
                                    : (root._imageActive && CameraBridge.frameIndex > 0)
                                imageSource: root._testActive
                                    ? ("file://" + root._testImagePath)
                                    : (root._imageActive ? root._liveOriginSource : "")
                                aspectRatio: root._camRatio
                                placeholderText: root._testActive ? ""
                                    : (!AppBridge.cameraConnected
                                        ? qsTr("未连接")
                                        : (root._collecting ? qsTr("等待图像") : qsTr("未采集")))
                            }

                            // ROI 层（仅原图）
                            RoiOverlay {
                                id: roiOverlay
                                anchors.fill: parent
                                imageWidth: CameraBridge.imageWidth
                                imageHeight: CameraBridge.imageHeight
                                aspectRatio: root._camRatio
                                drawingEnabled: root._roiMode && root._imageActive
                                                && root._fullscreenKind === ""
                                onRoiApplied: function(rect) {
                                    root._applyRoi(rect)
                                }
                            }
                        }
                    }
                }

                // 热力图窗口
                Rectangle {
                    width: parent._winW
                    height: parent._winH
                    anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                    radius: 10
                    color: Colors.contentBg
                    border { width: 0; color: Colors.cardBorder }

                    ColumnLayout {
                        anchors.fill: parent
                        spacing: 6

                        // 标题与工具按钮同一行（右侧全屏按钮）
                        RowLayout {
                            Layout.fillWidth: true
                            Layout.leftMargin: 16
                            Layout.rightMargin: 12
                            Layout.topMargin: 8
                            spacing: 8

                            Text {
                                text: root._pixelMaskEnabled ? qsTr("异常定位") : qsTr("异常热力图")
                                font.pixelSize: 12; font.bold: true
                                color: Colors.textPrimary
                            }

                            Item { Layout.fillWidth: true }

                            HeaderIconButton {
                                objectName: "heatmapFullscreenButton"
                                glyph: "⛶"
                                tip: qsTr("全屏")
                                checked: root._fullscreenKind === "heatmap"
                                onClicked: {
                                    root._fullscreenKind =
                                        (root._fullscreenKind === "heatmap" ? "" : "heatmap")
                                }
                            }
                        }

                        ImageView {
                            id: heatView
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            Layout.margins: 8
                            // 测试模式：默认显示 plasma 热力图；F1 阈值定位开关开启后
                            // 切换为二值掩模叠加图。实时模式同样经 provider 显示。
                            simulated: !root._testActive && !root._imageActive
                            simulatedHue: 0.02   // 红橙色系
                            imageActive: root._testActive
                                ? true
                                : (root._imageActive && AppBridge.algorithmEnabled
                                    && (root._pixelMaskEnabled
                                        ? DetectBridge.hasMask : DetectBridge.hasResult))
                            imageSource: root._testActive
                                ? (root._pixelMaskEnabled
                                    ? (root._testMaskPath.length > 0
                                        ? ("file://" + root._testMaskPath) : "")
                                    : (root._testHeatmapPath.length > 0
                                        ? ("file://" + root._testHeatmapPath) : ""))
                                : (root._imageActive && AppBridge.algorithmEnabled
                                    ? (root._pixelMaskEnabled
                                        ? root._liveMaskSource : root._liveHeatmapSource)
                                    : "")
                            aspectRatio: root._camRatio
                            placeholderText: root._testActive
                                ? (root._inferring ? qsTr("推理中...")
                                    : (root._pixelMaskEnabled && root._testMaskPath.length === 0
                                        ? qsTr("无异常定位信息") : ""))
                                : (!AppBridge.algorithmEnabled
                                    ? qsTr("算法未开启")
                                    : (root._collecting
                                        ? (AlgorithmBridge.modelPath.length === 0
                                            ? qsTr("未选择模型")
                                            : qsTr("等待推理结果"))
                                        : qsTr("未采集")))
                        }
                    }
                }
            }

            // ── 状态栏 ──────────────────────────────
            RowLayout {
                Layout.fillWidth: true
                spacing: 16

                Text {
                    text: qsTr("采集帧率 %1 fps").arg(
                        root._imageActive && root._captureFps > 0
                            ? root._captureFps.toFixed(1) : "0")
                    font.pixelSize: 12
                    color: Colors.textSecondary
                }
                Text {
                    text: qsTr("推理频率 %1 fps").arg(
                        AppBridge.algorithmEnabled ? DetectBridge.realtimeFps.toFixed(1) : "0")
                    font.pixelSize: 12
                    color: Colors.textSecondary
                }
                Text {
                    text: qsTr("分数 %1").arg(root._score.toFixed(3))
                    font.pixelSize: 12; font.bold: true
                    color: root._score > _threshold.value
                        ? Colors.statusDisconnected : Colors.textPrimary
                }
                // 推理耗时：测试结果显示测试推理耗时，实时采集显示实时推理耗时
                Text {
                    visible: root._testActive || (root._imageActive && AppBridge.algorithmEnabled)
                    text: root._testActive
                        ? qsTr("推理耗时 %1 ms").arg(AlgorithmBridge.lastInferenceMs.toFixed(0))
                        : qsTr("实时推理 %1 ms").arg(DetectBridge.lastInferenceMs.toFixed(0))
                    font.pixelSize: 12
                    color: Colors.textSecondary
                }

                Item { Layout.fillWidth: true }

                Rectangle { width: 10; height: 10; radius: 5
                    color: root._score > _threshold.value
                        ? Colors.statusDisconnected : Colors.statusConnected }
                Text {
                    text: root._score > _threshold.value ? qsTr("异常") : qsTr("正常")
                    font.pixelSize: 12; font.bold: true
                    color: root._score > _threshold.value
                        ? Colors.statusDisconnected : Colors.statusConnected
                }
            }
        }
    }

    // ============================================================
    // 右侧隐藏控制栏 — 点击手柄展开/收起
    // ============================================================
    Rectangle {
        id: sidePanel
        z: 5
        width: root._panelOpen ? 220 : 0
        anchors { top: parent.top; bottom: parent.bottom; right: parent.right }
        color: Colors.contentBg
        border { width: 1; color: Colors.cardBorder }
        clip: true

        Behavior on width { NumberAnimation { duration: 200; easing.type: Easing.InOutCubic } }

        ColumnLayout {
            anchors { fill: parent; margins: 10 }
            spacing: 8
            visible: root._panelOpen

            // 顶部弹簧 — 内容垂直居中，避免上下分散
            Item { Layout.fillHeight: true; Layout.minimumHeight: 4 }

            Text {
                text: qsTr("控制")
                font.pixelSize: 14; font.bold: true
                color: Colors.textPrimary
                Layout.alignment: Qt.AlignHCenter
            }
            Rectangle {
                Layout.fillWidth: true; implicitHeight: 1
                color: Colors.cardBorder
            }

            // 实时采集自锁按钮（申请 collectingOwner="detect"）
            // 相机未连接时禁用（后端无相机不可采集）
            Button {
                Layout.fillWidth: true
                text: root._collecting ? qsTr("停止采集") : qsTr("开始采集")
                implicitHeight: 36
                font.pixelSize: 13; font.bold: true
                enabled: AppBridge.cameraConnected

                contentItem: Text {
                    text: parent.text
                    font: parent.font
                    color: !parent.enabled ? Colors.textPlaceholder
                        : (parent.checked ? "#ffffff" : Colors.textPrimary)
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }

                checked: root._collecting

                onClicked: {
                    var startLive = !root._collecting
                    AppBridge.collectingOwner = startLive ? "detect" : ""
                    if (startLive) {
                        // 实时采集优先：立即切到实时画面，并让在途的
                        // 测试推理结果返回后自动作废，不会抢回画面。
                        root._enterLiveMode()
                    }
                }

                background: Rectangle {
                    radius: 4
                    color: !parent.enabled ? Colors.pageBg
                        : (parent.checked
                            ? Colors.statusDisconnected
                            : (parent.hovered ? Colors.interactiveHover : Colors.interactivePressed))
                }
            }

            SwitchRow {
                label: qsTr("算法推理")
                Layout.fillWidth: true
                on: AppBridge.algorithmEnabled
                onToggled: AppBridge.algorithmEnabled = on
            }

            Rectangle {
                Layout.fillWidth: true; implicitHeight: 1
                color: Colors.cardBorder
            }

            // 异常阈值 — 文本输入（-10~10，非法输入忽略，越界自动钳制到边界）。
            // 分数语义与训练/评估一致（判别器负输出，未归一化）。默认值来自
            // 标定脚本（calibrate_threshold.py，模型同目录 *.threshold.json），
            // 切换模型自动刷新；用户手动编辑后绑定失效、以手动值为准
            InputRow {
                id: _threshold
                label: qsTr("异常阈值")
                Layout.fillWidth: true
                text: AlgorithmBridge.threshold.toFixed(2)
                placeholderText: AlgorithmBridge.threshold.toFixed(2)
                property real value: AlgorithmBridge.threshold
                onTextEdited: {                    var v = parseFloat(newText)
                    if (!isNaN(v))
                        value = Math.max(-10, Math.min(10, v))
                }
            }

            // 像素级阈值 — 文本输入（可调）。默认/占位 = 模型 metadata 的
            // F1-max 阈值；清空输入恢复模型值（NaN）。用于下方开关开启时的
            // 缺陷定位二值化（hm_smooth > 像素阈值），下次推理生效。
            InputRow {
                id: _pixelThr
                label: qsTr("像素阈值")
                Layout.fillWidth: true
                text: isNaN(AlgorithmBridge.pixelThreshold)
                    ? "" : AlgorithmBridge.pixelThreshold.toFixed(3)
                placeholderText: isNaN(AlgorithmBridge.pixelThreshold)
                    ? qsTr("未标定") : AlgorithmBridge.pixelThreshold.toFixed(3)
                property real value: AlgorithmBridge.pixelThreshold
                onTextEdited: {
                    var v = parseFloat(newText)
                    if (isNaN(v)) {
                        // 清空 → 恢复模型 F1-max 阈值（下次推理生效）
                        value = AlgorithmBridge.pixelThreshold
                        AlgorithmBridge.setPixelThreshold(NaN)
                    } else {
                        value = v
                        AlgorithmBridge.setPixelThreshold(v)
                    }
                }
            }

            // F1 阈值定位开关：开 = 异常定位图（异常像素高亮）；关 = jet 热力图
            SwitchRow {
                label: qsTr("F1 阈值定位")
                Layout.fillWidth: true
                on: root._pixelMaskEnabled
                onToggled: root._pixelMaskEnabled = on
            }

            // 精细定位开关：掩模阈值取 max(F1阈值, 图内P99)，定位收窄到
            // 最异常区域（视觉接近服务器端 GT 逐图阈值效果，不依赖 GT）。
            // 不再依赖 F1 开关先打开：任何状态下都可点击；开启时自动
            // 打开 F1 阈值定位显示。
            SwitchRow {
                label: qsTr("精细定位")
                Layout.fillWidth: true
                on: root._pixelRefineEnabled
                onToggled: {
                    root._pixelRefineEnabled = on
                    if (on)
                        root._pixelMaskEnabled = true
                    AlgorithmBridge.setRefineMask(on)
                }
            }

            Rectangle {
                Layout.fillWidth: true; implicitHeight: 1
                color: Colors.cardBorder
            }

            // ── 测试推理（单图 ONNX 推理）──────────────
            Text {
                text: qsTr("测试推理")
                font.pixelSize: 13; font.bold: true
                color: Colors.textPrimary
                Layout.alignment: Qt.AlignHCenter
            }

            // 实时采集与测试推理互斥：采集中暂停测试区，避免测试结果
            // 抢走实时画面。停止采集后恢复。
            Text {
                visible: root._collecting
                Layout.fillWidth: true
                text: qsTr("实时采集中，测试推理已暂停")
                font.pixelSize: 11
                color: Colors.textSecondary
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.Wrap
            }

            Button {
                Layout.fillWidth: true
                text: qsTr("选择模型")
                implicitHeight: 32
                font.pixelSize: 12
                enabled: !root._collecting

                contentItem: Text {
                    text: parent.text
                    font: parent.font
                    color: !parent.enabled ? Colors.textPlaceholder : Colors.textPrimary
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }

                onClicked: modelPicker.open()

                background: Rectangle {
                    radius: 4
                    color: !parent.enabled ? Colors.pageBg
                        : (parent.hovered ? Colors.interactiveHover : Colors.interactivePressed)
                }
            }

            // 当前模型名（简短显示；切换类别需重新选择 onnx 文件）
            Text {
                Layout.fillWidth: true
                text: {
                    var p = AlgorithmBridge.modelPath
                    return p.length > 0 ? p.split("/").pop() : qsTr("未选择模型")
                }
                font.pixelSize: 11
                color: Colors.textSecondary
                elide: Text.ElideMiddle
            }

            Button {
                Layout.fillWidth: true
                text: qsTr("卸载模型")
                implicitHeight: 32
                font.pixelSize: 12
                enabled: !root._collecting && AlgorithmBridge.modelPath.length > 0

                contentItem: Text {
                    text: parent.text
                    font: parent.font
                    color: !parent.enabled ? Colors.textPlaceholder : Colors.textPrimary
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }

                onClicked: {
                    console.log("[DetectPage] 卸载模型，释放 ONNX session 内存")
                    AlgorithmBridge.unloadModel()
                }

                background: Rectangle {
                    radius: 4
                    color: !parent.enabled ? Colors.pageBg
                        : (parent.hovered ? Colors.interactiveHover : Colors.interactivePressed)
                }
            }

            Button {
                Layout.fillWidth: true
                text: qsTr("打开图片")
                implicitHeight: 32
                font.pixelSize: 12
                enabled: !root._collecting

                contentItem: Text {
                    text: parent.text
                    font: parent.font
                    color: !parent.enabled ? Colors.textPlaceholder : Colors.textPrimary
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }

                onClicked: imagePicker.open()

                background: Rectangle {
                    radius: 4
                    color: !parent.enabled ? Colors.pageBg
                        : (parent.hovered ? Colors.interactiveHover : Colors.interactivePressed)
                }
            }

            // 已选图片路径（简短显示）
            Text {
                visible: root._testImagePath.length > 0
                Layout.fillWidth: true
                text: root._testImagePath.split("/").pop()
                font.pixelSize: 11
                color: Colors.textSecondary
                elide: Text.ElideMiddle
            }

            Button {
                Layout.fillWidth: true
                text: root._inferring ? qsTr("推理中...") : qsTr("执行推理")
                implicitHeight: 32
                font.pixelSize: 12
                enabled: !root._collecting
                          && root._testImagePath.length > 0
                          && AlgorithmBridge.modelPath.length > 0 && !root._inferring

                contentItem: Text {
                    text: parent.text
                    font: parent.font
                    color: !parent.enabled ? Colors.textPlaceholder : Colors.textPrimary
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }

                onClicked: {
                    root._testSession++
                    root._testActiveSession = root._testSession
                    root._inferring = true
                    root._testHeatmapPath = ""
                    root._testMaskPath = ""
                    AlgorithmBridge.inferImage(root._testImagePath)
                }

                background: Rectangle {
                    radius: 4
                    color: !parent.enabled ? Colors.pageBg
                        : (parent.hovered ? Colors.interactiveHover : Colors.interactivePressed)
                }
            }

            // 底部弹簧
            Item { Layout.fillHeight: true; Layout.minimumHeight: 4 }
        }
    }

    // ============================================================
    // 全屏显示层：点击原图/热力图标题栏 ⛶ 放大到整个 DetectPage，
    // 再次点击标题栏 ⛶ 恢复原布局。
    // ============================================================
    Rectangle {
        id: fullscreenOverlay
        visible: root._fullscreenKind !== ""
        anchors.fill: parent
        z: 30
        color: Colors.contentBg
        radius: 10

        ColumnLayout {
            anchors { fill: parent; margins: 12 }
            spacing: 8

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                Text {
                    text: root._fullscreenTitle
                    font.pixelSize: 14; font.bold: true
                    color: Colors.textPrimary
                }

                Item { Layout.fillWidth: true }

                HeaderIconButton {
                    objectName: "fullscreenRoiButton"
                    visible: root._fullscreenKind === "origin"
                    glyph: "▭"
                    tip: qsTr("ROI 绘制")
                    checked: root._roiMode
                    enabled: root._imageActive
                    onClicked: {
                        if (root._roiMode) {
                            root._roiMode = false
                            roiOverlay.cancelDrawing()
                            fullscreenRoiOverlay.cancelDrawing()
                        } else {
                            root._roiMode = true
                        }
                    }
                }

                HeaderIconButton {
                    glyph: "⛶"
                    tip: qsTr("退出全屏")
                    checked: true
                    onClicked: root._fullscreenKind = ""
                }
            }

            Item {
                Layout.fillWidth: true
                Layout.fillHeight: true

                ImageView {
                    anchors.fill: parent
                    simulated: root._fullscreenSimulated
                    simulatedHue: root._fullscreenKind === "origin" ? 0.55 : 0.02
                    imageActive: root._fullscreenImageActive
                    imageSource: root._fullscreenSource
                    aspectRatio: root._camRatio
                    placeholderText: root._fullscreenKind === "origin"
                        ? qsTr("等待图像") : qsTr("等待推理结果")
                }

                // 原图全屏时同样支持 ROI 框选：放大画面后再画更精准
                RoiOverlay {
                    id: fullscreenRoiOverlay
                    anchors.fill: parent
                    imageWidth: CameraBridge.imageWidth
                    imageHeight: CameraBridge.imageHeight
                    aspectRatio: root._camRatio
                    drawingEnabled: root._fullscreenKind === "origin"
                                    && root._roiMode && root._imageActive
                    onRoiApplied: function(rect) {
                        root._applyRoi(rect)
                    }
                }
            }
        }
    }

    // 测试图片选择 — 系统原生 FileDialog
    FileDialog {
        id: imagePicker
        currentFolder: _testImagePath.length > 0
            ? ("file://" + _testImagePath) : AppBridge.homeDir
        nameFilters: ["Images (*.png *.jpg *.jpeg *.bmp)"]
        onAccepted: {
            var p = imagePicker.selectedFile.toString()
            if (p.startsWith("file://"))
                p = decodeURIComponent(p.slice(7))
            // 换图即作废上一次在途测试推理，避免旧图结果返回后覆盖新选择
            root._testSession++
            root._testActiveSession = -1
            root._inferring = false
            root._testImagePath = p
            root._testActive = false   // 新图片：等待重新推理
            console.log("[DetectPage] 测试图片:", p)
        }
    }

    // 模型选择 — 系统原生 FileDialog（切换类别需换 onnx 文件）
    FileDialog {
        id: modelPicker
        // 初始目录 = 当前模型所在目录；未选模型时回退主目录（模型默认 null，用户自选）
        currentFolder: {
            var mp = AlgorithmBridge.modelPath
            if (mp.length > 0)
                return "file://" + mp.split("/").slice(0, -1).join("/")
            return AppBridge.homeDir
        }
        nameFilters: ["ONNX 模型 (*.onnx)"]
        onAccepted: {
            var p = modelPicker.selectedFile.toString()
            if (p.startsWith("file://"))
                p = decodeURIComponent(p.slice(7))
            if (p !== AlgorithmBridge.modelPath)
                AlgorithmBridge.loadModel(p)
        }
    }

    // 手柄按钮 — 始终可见，点击展开/收起侧栏（展开时移到面板左缘）
    Rectangle {
        id: handle
        objectName: "sidePanelHandle"
        z: 6
        width: 24
        height: 72
        anchors { right: sidePanel.left; verticalCenter: parent.verticalCenter }
        radius: 6
        color: Colors.interactivePressed

        Behavior on anchors.rightMargin { NumberAnimation { duration: 200 } }

        Text {
            anchors.centerIn: parent
            text: root._panelOpen ? "❯" : "❮"
            font.pixelSize: 13
            color: "#ffffff"
        }

        MouseArea {
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: root._panelOpen = !root._panelOpen
        }
    }

    // 当前采集帧率：1s 读一次相机特征（避免每帧同步读 SDK 阻塞 UI）
    Timer {
        id: fpsTimer
        interval: 1000
        repeat: true
        running: root._imageActive
        onTriggered: {
            var fps = CameraBridge.getFeature("GX_FLOAT_CURRENT_ACQUISITION_FRAME_RATE")
            root._captureFps = (fps > 0) ? fps : 0
        }
    }
}
