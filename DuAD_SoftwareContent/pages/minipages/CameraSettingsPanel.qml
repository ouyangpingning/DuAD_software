import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software
import "../components"

/*
    相机参数设置面板 — 点击 CameraCard 后展开，参数经 CameraBridge 读写真实相机。

    参数组：
      图像    — 分辨率(只读，来自相机)、像素格式
      曝光    — 曝光时间、增益
      采集    — 采集模式、目标帧率、当前帧率(只读)
      Gamma   — Gamma 开关、模式、参数值

    特征映射（大恒 gxidef 枚举值）：
      GX_FLOAT_EXPOSURE_TIME / GX_FLOAT_GAIN / GX_FLOAT_ACQUISITION_FRAME_RATE
      GX_BOOL_GAMMA_ENABLE / GX_ENUM_GAMMA_MODE(0=SRGB,1=User) / GX_FLOAT_GAMMA_PARAM
      GX_ENUM_ACQUISITION_MODE(0=SingleFrame,2=Continuous) / GX_ENUM_PIXEL_FORMAT
      GX_INT_WIDTH / GX_INT_HEIGHT
    连接成功（cameraOpened）后由 CameraPage 调用 initFromCamera() 初始化。
*/
Item {
    id: root

    // ============================================================
    // 公有 API
    // ============================================================
    property bool expanded: false

    // ============================================================
    // 参数值（连接后由 initFromCamera 从相机读取）
    // ============================================================
    property string resolutionText: "—"   // 实际分辨率
    property int _resIndex: 0              // 分辨率预设下标
    property string pixelFormat: "BayerRG8"
    property real   exposureTime: 5000  // μs
    property real   gain: 0             // dB
    property string acqMode: "Continuous"
    property real   targetFps: 30       // fps
    property bool   gammaEnabled: false
    property string gammaMode: "SRGB"
    property real   gammaValue: 1

    // 重置目标值 — 老项目（pyqt5/ui/main.ui）参数初始值：
    // 曝光 20000 μs（界面显示/输入单位为 ms）、增益 0 dB、目标帧率 1.0 fps
    property real _expReset: 20000
    property real _gainReset: 0
    property real _fpsReset: 1.0

    // 分辨率预设（全幅与半幅，与相机最大分辨率同比例）
    property var _resPresets: [[2448, 2048], [1224, 1024]]

    // ============================================================
    // 辅助函数
    // ============================================================
    function _pixelIndex(fmt) {
        var arr = ["BayerRG8", "BayerGB8", "BayerGR8", "BayerBG8", "Mono8"]
        var idx = arr.indexOf(fmt)
        return idx >= 0 ? idx : 0
    }

    // 像素格式 enum int → 显示名（大恒 GxPixelFormatEntry）
    function _pixelNameFromId(id) {
        if (id === 0x1080009) return "BayerRG8"
        if (id === 0x108000A) return "BayerGB8"
        if (id === 0x1080008) return "BayerGR8"
        if (id === 0x108000B) return "BayerBG8"
        if (id === 0x1080001) return "Mono8"
        return "0x" + id.toString(16)
    }

    // 连接后从相机读取全部参数（CameraPage 在 cameraOpened 时调用）
    function initFromCamera() {
        var w = CameraBridge.getFeature("GX_INT_WIDTH")
        var h = CameraBridge.getFeature("GX_INT_HEIGHT")
        if (w >= 0 && h >= 0) {
            root.resolutionText = w + " × " + h
            // 匹配预设下标（找不到匹配则全幅 0）
            root._resIndex = 0
            for (var i = 0; i < _resPresets.length; i++) {
                if (_resPresets[i][0] === w && _resPresets[i][1] === h) {
                    root._resIndex = i
                    break
                }
            }
        }

        var pf = CameraBridge.getFeature("GX_ENUM_PIXEL_FORMAT")
        if (pf >= 0)
            root.pixelFormat = _pixelNameFromId(pf)

        var exp = CameraBridge.getFeature("GX_FLOAT_EXPOSURE_TIME")
        if (exp >= 0)
            root.exposureTime = exp

        var g = CameraBridge.getFeature("GX_FLOAT_GAIN")
        if (g >= 0)
            root.gain = g

        var fps = CameraBridge.getFeature("GX_FLOAT_ACQUISITION_FRAME_RATE")
        if (fps > 0)
            root.targetFps = fps

        var am = CameraBridge.getFeature("GX_ENUM_ACQUISITION_MODE")
        if (am === 2) root.acqMode = "Continuous"
        else if (am === 0) root.acqMode = "SingleFrame"

        var ge = CameraBridge.getFeature("GX_BOOL_GAMMA_ENABLE")
        if (ge >= 0) root.gammaEnabled = (ge > 0.5)

        var gm = CameraBridge.getFeature("GX_ENUM_GAMMA_MODE")
        if (gm === 1) root.gammaMode = "User"
        else if (gm === 0) root.gammaMode = "SRGB"

        var gv = CameraBridge.getFeature("GX_FLOAT_GAMMA_PARAM")
        if (gv >= 0) root.gammaValue = gv
    }

    // 应用分辨率预设：交给 CameraBridge.applyResolution 做居中裁剪 + 步进对齐，
    // 并把该分辨率记录为「设定分辨率」（ROI 恢复全幅时回到它，而非传感器最大）。
    function _applyResolution(index) {
        var w = _resPresets[index][0], h = _resPresets[index][1]
        var ok = CameraBridge.applyResolution(w, h)
        if (ok) root.resolutionText = w + " × " + h
        console.log("[CameraSettingsPanel] 分辨率应用:", w, "×", h, ok ? "(已记录为设定分辨率)" : "(失败)")
    }

    // ============================================================
    // 尺寸
    // ============================================================
    implicitWidth: 420
    implicitHeight: expanded ? contentLayout.implicitHeight + 32 : 0
    clip: true

    Behavior on implicitHeight {
        NumberAnimation { duration: 250; easing.type: Easing.InOutCubic }
    }

    // ============================================================
    // 卡片本体
    // ============================================================
    Rectangle {
        anchors.fill: parent
        radius: 12
        color: Colors.contentBg
        border { width: 0; color: Colors.cardBorder }

        ColumnLayout {
            id: contentLayout
            spacing: 12
            anchors {
                left: parent.left; right: parent.right
                top: parent.top; bottom: parent.bottom
                margins: 24
            }

            Text {
                text: qsTr("相机参数设置")
                font.pixelSize: 14; font.bold: true
                color: Colors.textPrimary
            }

            // ── 图像 ────────────────────────────
            Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 1
                    color: Colors.cardBorder
                }
            SectionHeader { text: qsTr("图像") }

            // 分辨率预设可调（写入相机：OFFSET 居中 + WIDTH/HEIGHT，8/2 步进对齐）
            ComboRow {
                label: qsTr("分辨率")
                model: ["2448 × 2048", "1224 × 1024"]
                currentIndex: root._resIndex
                onActivated: root._applyResolution(index)
            }
            ComboRow {
                label: qsTr("像素格式")
                model: ["BayerRG8", "BayerGB8", "BayerGR8", "BayerBG8", "Mono8"]
                currentIndex: _pixelIndex(root.pixelFormat)
                // 写入相机（枚举 int 值）；采集回调按帧内实际 pixel_format
                // 选择对应的 DxRaw8toRGB24 Bayer 排列，不再写死 BG。
                onActivated: {
                    root.pixelFormat = model[index]
                    var ids = [0x1080009, 0x108000A, 0x1080008, 0x108000B, 0x1080001]
                    CameraBridge.setFeature("GX_ENUM_PIXEL_FORMAT", ids[index])
                }
            }
               
            // ── Gamma ───────────────────────────
            Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 1
                    color: Colors.cardBorder
                }
            SectionHeader { text: "Gamma" }

            SwitchRow {
                label: qsTr("Gamma")
                on: root.gammaEnabled
                onToggled: {
                    root.gammaEnabled = on
                    CameraBridge.setFeature("GX_BOOL_GAMMA_ENABLE", on ? 1 : 0)
                }
            }
            ComboRow {
                label: qsTr("Gamma 模式")
                model: ["SRGB", "User"]
                currentIndex: root.gammaMode === "SRGB" ? 0 : 1
                enabled: root.gammaEnabled
                onActivated: {
                    root.gammaMode = model[index]
                    CameraBridge.setFeature("GX_ENUM_GAMMA_MODE", index)  // 0=SRGB 1=User
                }
            }
            // Gamma 值 — 只读：MER2 系列固件对 GX_FLOAT_GAMMA_PARAM 只读
            // （写返回 INVALID_ACCESS），enable/mode 可正常设置
            ReadonlyRow {
                label: qsTr("Gamma 值")
                value: root.gammaValue.toFixed(2)
            }

            // ── 曝光 / 增益 ─────────────────────
            Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 1
                    color: Colors.cardBorder
                }
            SectionHeader { text: qsTr("曝光 / 增益") }

            SliderRow {
                label: qsTr("曝光时间")
                // 界面单位为 ms，提交相机时换算回大恒的 μs。
                // 拖动自动吸附标准曝光刻度，适合触摸屏；滚轮/Ctrl 滚轮可细调。
                sliderValue: root.exposureTime / 1000.0
                from: 0.01; to: 1000
                suffix: " ms"; decimals: 3
                snapTicks: [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10,
                            20, 30, 40, 50, 75, 100, 150, 200, 300, 500, 750, 1000]
                wheelStep: 0.1
                resetValue: root._expReset / 1000.0
                onSliderValueChanged: root.exposureTime = sliderValue * 1000.0   // 拖动实时预览
                onReleased: CameraBridge.setFeature("GX_FLOAT_EXPOSURE_TIME", value * 1000.0)
            }
            SliderRow {
                label: qsTr("增益")
                sliderValue: root.gain; from: 0; to: 24
                suffix: " dB"; decimals: 1
                snapTicks: [0, 3, 6, 9, 12, 15, 18, 21, 24]
                wheelStep: 0.1
                resetValue: root._gainReset
                onSliderValueChanged: root.gain = sliderValue
                onReleased: CameraBridge.setFeature("GX_FLOAT_GAIN", value)
            }

            // ── 采集 ────────────────────────────
            Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 1
                    color: Colors.cardBorder
                }
            SectionHeader { text: qsTr("采集") }

            ComboRow {
                label: qsTr("采集模式")
                model: ["Continuous", "SingleFrame"]
                currentIndex: root.acqMode === "Continuous" ? 0 : 1
                onActivated: {
                    root.acqMode = model[index]
                    // 大恒枚举: 0=SingleFrame 2=Continuous
                    CameraBridge.setFeature("GX_ENUM_ACQUISITION_MODE", index === 0 ? 2 : 0)
                }
            }
            SliderRow {
                label: qsTr("目标帧率")
                // 上限 100：MER2 系列默认目标帧率可达 79fps，60 会越界滑出轨道
                sliderValue: root.targetFps; from: 1; to: 100
                suffix: " fps"; decimals: 1
                snapTicks: [1, 2, 5, 10, 15, 20, 25, 30, 50, 60, 79, 100]
                wheelStep: 0.5
                resetValue: root._fpsReset
                onSliderValueChanged: root.targetFps = sliderValue
                onReleased: CameraBridge.setFeature("GX_FLOAT_ACQUISITION_FRAME_RATE", value)
            }
        }
    }
}
