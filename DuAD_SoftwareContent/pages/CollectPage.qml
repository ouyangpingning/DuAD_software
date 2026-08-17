import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software
import "components"
import "minipages"

Item {
    id: root

    // ============================================================
    // 状态
    // 采集会话由 AppBridge.collectingOwner 统一仲裁：
    //   "collect" = 本页持有；"detect" = 异常检测页持有
    // 两者互斥，后按者抢占，先按者自动停止。
    // 真实保存管线在 Python CollectBridge：配置 → start → 节流写盘 → stop。
    // ============================================================
    property bool _settingsExpanded: false
    property real _captureFps: 0           // 1s 读一次相机实际帧率
    property string _lastError: ""

    readonly property bool _collecting: AppBridge.collectingOwner === "collect"

    Component.onCompleted: {
        CollectBridge.saveError.connect(function(msg) {
            root._lastError = msg
            console.log("[CollectPage] 保存错误:", msg)
        })
    }

    Rectangle {
        anchors.fill: parent
        color: Colors.pageBg

        Flickable {
            anchors.fill: parent
            contentWidth: width
            contentHeight: contentColumn.implicitHeight + 40
            clip: true
            boundsBehavior: Flickable.StopAtBounds

            ColumnLayout {
                id: contentColumn
                width: 420
                spacing: 10
                x: Math.max(0, (parent.width - width) / 2)
                y: 24

                // 标题行
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    Text {
                        text: qsTr("图像采集")
                        font.pixelSize: 16
                        font.bold: true
                        color: Colors.textPrimary
                    }
                }

                // 采集状态卡片
                AcquisitionCard {
                    id: acqCard
                    Layout.fillWidth: true
                    height: 80
                    collecting: _collecting
                    fps: _captureFps
                    enabled: AppBridge.cameraConnected
                    cameraConnected: AppBridge.cameraConnected

                    onClicked: {
                        var start = !root._collecting
                        if (start) {
                            // 先配置 Python 保存管线，成功后才申请采集会话
                            var ok = CollectBridge.configure(
                                savePanel.savePath,
                                savePanel.prefix,
                                savePanel.formatKey,
                                savePanel.saveInterval)
                            if (!ok)
                                return
                            root._lastError = ""
                        }
                        AppBridge.collectingOwner = start ? "collect" : ""
                        _settingsExpanded = false
                    }

                    onGearClicked: {
                        _settingsExpanded = !_settingsExpanded
                    }
                }

                // 保存错误提示
                Text {
                    visible: root._lastError.length > 0
                    Layout.fillWidth: true
                    text: root._lastError
                    font.pixelSize: 12
                    color: Colors.statusDisconnected
                    wrapMode: Text.Wrap
                }

                // 已保存数量 — 采集中实时显示
                Text {
                    visible: _collecting
                    Layout.alignment: Qt.AlignHCenter
                    text: qsTr("已保存 %1 张").arg(CollectBridge.savedCount)
                    font.pixelSize: 12
                    color: Colors.textSecondary
                }

                // 采集/保存设置面板 — 齿轮展开，采集前可先设置
                SaveSettingsPanel {
                    id: savePanel
                    Layout.fillWidth: true
                    expanded: _settingsExpanded
                    saving: _collecting
                    savedCount: CollectBridge.savedCount
                }
            }
        }
    }

    // 当前采集帧率：1s 读一次相机特征
    Timer {
        id: fpsTimer
        interval: 1000
        repeat: true
        running: _collecting
        onTriggered: {
            var fps = CameraBridge.getFeature("GX_FLOAT_CURRENT_ACQUISITION_FRAME_RATE")
            root._captureFps = (fps > 0) ? fps : 0
        }
    }
}
