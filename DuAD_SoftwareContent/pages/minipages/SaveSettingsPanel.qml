import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import DuAD_Software
import "../components"

/*
    采集/保存设置面板 — 由采集卡片的齿轮按钮展开（采集前先设置）。
    按时间间隔（秒）节流保存当前帧到指定目录，仿老项目 ImageSaveWorker：
    队列 + 后台线程 + time.time() 节流 + 文件名 {prefix}_{timestamp}_{counter:05d}.{fmt}
    saving 由页面传入（= 采集中）：保存运行期间参数锁定（整体禁用）。
    TODO: 对接 Python 后端，节流与写盘应在 Python 侧完成，QML 只做参数与计数。
*/
Item {
    id: root

    // ============================================================
    // 公有 API
    // ============================================================
    property bool expanded: false
    property bool saving: false           // 采集中（参数锁定，由页面传入）
    property real saveInterval: 1.0      // 秒
    property string savePath: "~/Pictures/DuAD/"
    property string prefix: "capture"
    property string formatKey: "jpg"
    property int savedCount: 0

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
            spacing: 10
            anchors {
                left: parent.left; right: parent.right
                top: parent.top
                margins: 24
            }

            Text {
                text: qsTr("定时保存设置")
                font.pixelSize: 14; font.bold: true
                color: Colors.textPrimary
            }

            Rectangle {
                Layout.fillWidth: true; implicitHeight: 1
                color: Colors.cardBorder
            }

            // ── 参数区 — 保存运行时锁定 ────────────────
            // 注意：不能只靠 ColumnLayout 级联禁用 —— SliderRow 内部 control 用
            // `property bool enabled` 遮蔽了原生 enabled，级联断链。必须逐行显式传。
            ColumnLayout {
                spacing: 10

                SliderRow {
                    label: qsTr("保存间隔")
                    sliderValue: root.saveInterval; from: 0.5; to: 60
                    suffix: " s"; decimals: 1
                    snapTicks: [0.5, 1, 2, 5, 10, 15, 20, 30, 45, 60]
                    wheelStep: 0.5
                    enabled: !root.saving
                    onSliderValueChanged: root.saveInterval = sliderValue
                }

                RowLayout {
                    spacing: 8
                    Layout.fillWidth: true

                    InputRow {
                        Layout.fillWidth: true
                        label: qsTr("保存路径")
                        text: root.savePath
                        placeholderText: "~/Pictures/DuAD/"
                        enabled: !root.saving
                        onTextEdited: root.savePath = newText
                    }

                    Button {
                        text: qsTr("浏览")
                        implicitHeight: 32
                        font.pixelSize: 12
                        enabled: !root.saving

                        contentItem: Text {
                            text: parent.text
                            font: parent.font
                            color: parent.enabled ? Colors.textPrimary : Colors.textPlaceholder
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }

                        onClicked: picker.open()

                        background: Rectangle {
                            radius: 4
                            color: parent.hovered && parent.enabled
                                ? Colors.interactiveHover : Colors.interactivePressed
                        }
                    }
                }

                InputRow {
                    label: qsTr("文件前缀")
                    text: root.prefix
                    placeholderText: "capture"
                    enabled: !root.saving
                    onTextEdited: root.prefix = newText
                }

                ComboRow {
                    label: qsTr("图片格式")
                    // model 用稳定 key（不翻译），显示文本走 displayFunc — 见 ComboRow 注释
                    model: ["jpg", "png", "bmp"]
                    currentIndex: Math.max(0, model.indexOf(root.formatKey))
                    displayFunc: function(key) { return key.toUpperCase() }
                    enabled: !root.saving
                    onActivated: root.formatKey = model[index]
                }
            }

            ReadonlyRow {
                label: qsTr("已保存")
                value: qsTr("%1 张").arg(root.savedCount)
            }
        }
    }

    // ============================================================
    // 目录选择 — 系统原生 FolderDialog
    // ============================================================
    function _pathToFolderUrl(path) {
        // 本地路径（含 ~）→ file:// url（FolderDialog.currentFolder 用）
        var p = path
        if (p.length > 0 && p.indexOf("~") === 0)
            p = AppBridge.homeDir + p.slice(1)
        // 反斜杠→正斜杠；保证根前一个斜杠（Windows 盘符 → file:///C:/...，Linux → file:///home/...）
        p = p.replace(/\\/g, "/")
        if (p.charAt(0) !== "/")
            p = "/" + p
        return "file://" + p
    }

    function _folderUrlToPath(url) {
        var s = url.toString()
        if (s.indexOf("file://") === 0)
            s = decodeURIComponent(s.slice(7))
        // Windows 盘符：file:///C:/... → /C:/... 去掉盘符前的斜杠
        if (/^\/[A-Za-z]:/.test(s))
            s = s.slice(1)
        return s
    }

    FolderDialog {
        id: picker
        currentFolder: _pathToFolderUrl(root.savePath)
        onAccepted: {
            var p = _folderUrlToPath(picker.selectedFolder)
            if (p) {
                root.savePath = p
                console.log("[DEBUG] SaveSettingsPanel: 选择保存目录:", p)
            }
        }
    }
}
