import "../components"
import DuAD_Software
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

/*
    相机卡片 — 显示当前检测到的相机信息。

    用法:
        CameraCard {
            hasCamera: true
            cameraName: "Basler acA1920-150uc"
            serialNumber: "23948723"
            connected: true
            resolution: "2248 × 2048"
        }
*/
Item {
    id: root

    // ============================================================
    // 公有 API
    // ============================================================
    property bool hasCamera: false
    property bool connecting: false     // 正在连接相机中
    property string cameraName: ""
    property string serialNumber: ""
    property bool connected: false
    property string resolution: ""
    // ============================================================
    // 点击交互 — 有相机时卡牌可点击
    // ============================================================
    property bool _hovered: false

    // 点击卡片触发 — 后端监听此信号创建 CameraDevice
    signal clicked()

    // ============================================================
    // 固定尺寸 — 不随窗口缩放
    // ============================================================
    implicitWidth: 420
    implicitHeight: 120

    // ============================================================
    // 卡片本体
    // ============================================================
    Rectangle {
        id: cardBg

        anchors.fill: parent
        radius: 12
        // 背景色区分三种状态：默认 / 可连接(青) / 可断开(红)
        color: {
            if (root.connected && _hovered) return Colors.cardDangerHover  // 断开警告
            if (root.connected)             return Colors.cardDangerBg     // 已连接
            if (_hovered && root.hasCamera) return Colors.interactiveHover // 可连接
            return Colors.contentBg                                       // 默认
        }

        RowLayout {
            spacing: 16

            anchors {
                fill: parent
                margins: 16
            }

            // ── 左侧相机图标（共用，两种状态都显示；连接中隐藏让位给居中文案）──
            Rectangle {
                visible: !root.connecting
                Layout.preferredWidth: 48
                Layout.preferredHeight: 48
                Layout.alignment: Qt.AlignVCenter
                radius: 10
                color: cardBg.color

                IconImage {
                    anchors.centerIn: parent
                    source: "../../images/摄像头.svg"
                    width: 40
                    height: 40
                    imageOpacity: root.hasCamera ? 1 : 0.45
                }

            }

            // ── 无相机：骨架屏占位条 ────────────────────
            ColumnLayout {
                visible: !root.hasCamera
                spacing: 12
                Layout.alignment: Qt.AlignVCenter

                SkeletonBar {
                    Layout.preferredWidth: 160
                    Layout.preferredHeight: 15
                }

                SkeletonBar {
                    Layout.preferredWidth: 100
                    Layout.preferredHeight: 12
                }

                SkeletonBar {
                    Layout.preferredWidth: 120
                    Layout.preferredHeight: 12
                }

            }

            // ── 有相机：信息列 ──────────────────────────
            ColumnLayout {
                visible: root.hasCamera && !root.connecting
                spacing: 4
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignVCenter

                Text {
                    text: root.cameraName || qsTr("未知相机")
                    font.pixelSize: 15
                    font.bold: true
                    color: Colors.textPrimary
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 1
                    color: Colors.cardBorder
                }

                InfoRow {
                    label: qsTr("序列号")
                    value: root.serialNumber || "—"
                }

                RowLayout {
                    spacing: 8

                    Text {
                        text: qsTr("状态")
                        font.pixelSize: 12
                        color: Colors.textSecondary
                    }

                    Rectangle {
                        width: 8
                        height: 8
                        radius: 4
                        color: root.connected ? Colors.statusConnected : Colors.statusDisconnected
                    }

                    Text {
                        text: root.connected ? qsTr("已连接") : qsTr("已断开")
                        font.pixelSize: 12
                        color: root.connected ? Colors.statusConnected : Colors.statusDisconnected
                    }

                }

            }

        }

        Behavior on color {
            ColorAnimation {
                duration: 200
            }

        }

        Behavior on border.width {
            NumberAnimation {
                duration: 200
            }

        }

        // ── 正在连接：覆盖层整体居中于卡片（不在 RowLayout 内，
        //    否则左侧图标占位会让文案整体偏右）──
        ColumnLayout {
            visible: root.connecting
            anchors.centerIn: parent
            spacing: 8

            Text {
                text: qsTr("正在连接相机...")
                font.pixelSize: 14
                color: Colors.textSecondary
                horizontalAlignment: Text.AlignHCenter
            }
            BusyIndicator {
                implicitWidth: 28
                implicitHeight: 28
                Layout.alignment: Qt.AlignHCenter
                running: root.connecting
            }
        }

    }

    MouseArea {
        anchors.fill: parent
        enabled: root.hasCamera
        hoverEnabled: true
        cursorShape: root.hasCamera ? Qt.PointingHandCursor : Qt.ArrowCursor
        onEntered: _hovered = true
        onExited: _hovered = false
        onClicked: {
            if (root.hasCamera) {
                console.log("[DEBUG] CameraCard: 点击创建相机对象 —", root.serialNumber);
                root.clicked();
            }
        }
    }

    // ── 悬停提示 ─────────────────────────────────────
    Rectangle {
        visible: _hovered && root.hasCamera
        anchors {
            horizontalCenter: parent.horizontalCenter
            bottom: parent.top
            bottomMargin: -6
        }
        width: tipText.implicitWidth + 16
        height: 26
        radius: 6
        color: root.connected ? "#e74c3c" : Colors.textSecondary

        Behavior on opacity { NumberAnimation { duration: 150 } }

        Text {
            id: tipText
            anchors.centerIn: parent
            text: root.connected ? qsTr("点击断开连接") : qsTr("点击连接相机")
            font.pixelSize: 11
            color: "#ffffff"
        }
    }

}
