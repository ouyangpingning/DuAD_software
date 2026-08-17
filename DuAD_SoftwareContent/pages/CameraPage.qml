import "components"
import "minipages"
import DuAD_Software
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    // ============================================================
    // UI 状态（驱动源为 CameraBridge 后端）
    // ============================================================
    property bool searching: false
    property bool _hasCamera: false
    property bool _cameraConnected: false
    property bool _connecting: false      // 正在连接相机中
    property string _cameraName: ""
    property string _serialNumber: ""

    // ── 后端信号接线（Component.onCompleted 里 connect，防 GC/时序问题）──
    Component.onCompleted: {
        CameraBridge.camerasFound.connect(function(cams) {
            searching = false
            _hasCamera = cams.length > 0
            if (_hasCamera) {
                _cameraName = cams[0].model
                _serialNumber = cams[0].sn
            }
            console.log("[CameraPage] 搜索完成:", cams.length, "台")
        })

        CameraBridge.cameraOpened.connect(function() {
            _connecting = false
            _cameraConnected = true
            // 读实际分辨率显示 + 初始化参数面板
            var w = CameraBridge.getFeature("GX_INT_WIDTH")
            var h = CameraBridge.getFeature("GX_INT_HEIGHT")
            cameraCard.resolution = (w >= 0 && h >= 0) ? (w + " × " + h) : "—"
            settingsPanel.initFromCamera()
            console.log("[CameraPage] 相机连接成功")
        })

        CameraBridge.cameraError.connect(function(msg) {
            _connecting = false
            console.log("[CameraPage] 相机错误:", msg)
        })

        CameraBridge.cameraClosed.connect(function() {
            _cameraConnected = false
            console.log("[CameraPage] 相机已断开")
        })
    }

    Rectangle {
        anchors.fill: parent
        color: Colors.pageBg

        Flickable {
            id: flickable
            anchors.fill: parent
            contentWidth: width
            contentHeight: contentColumn.implicitHeight + 40
            clip: true
            boundsBehavior: Flickable.StopAtBounds

            ColumnLayout {
                id: contentColumn
                width: 420
                spacing: 10
                x: Math.max(0, (flickable.width - width) / 2)
                y: 20

                // 标题行
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    Text {
                        text: qsTr("可用的相机")
                        font.pixelSize: 16
                        font.bold: true
                        color: Colors.textPrimary
                    }

                    AnimatedRefreshButton {
                        id: refreshBtn
                        iconSource: "../images/refresh.svg"
                        size: 36
                        running: searching
                        onClicked: {
                            console.log("[CameraPage] 搜索相机...")
                            searching = true
                            CameraBridge.search()
                        }
                    }
                }

                // 相机卡片
                CameraCard {
                    id: cameraCard
                    Layout.fillWidth: true
                    height: 120
                    hasCamera: _hasCamera
                    cameraName: _cameraName
                    serialNumber: _serialNumber
                    connected: _cameraConnected
                    connecting: _connecting

                    onClicked: {
                        if (_connecting) return   // 连接中，忽略点击

                        if (!_cameraConnected) {
                            console.log("[CameraPage] 正在连接相机 — SN:", _serialNumber)
                            _connecting = true
                            CameraBridge.connectCamera(_serialNumber)
                        } else {
                            console.log("[CameraPage] 断开相机")
                            CameraBridge.disconnectCamera()
                        }
                    }
                }

                // 相机参数设置面板 — 连接相机后才展开
                CameraSettingsPanel {
                    id: settingsPanel
                    Layout.fillWidth: true
                    expanded: _cameraConnected
                }
            }
        }
    }
}
