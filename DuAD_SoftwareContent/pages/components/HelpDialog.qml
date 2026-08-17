import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software

/*
    使用说明页 — 首次启动弹出 + 设置页"使用说明"按钮可重开。
    逐页介绍功能用法（精简版）。模态铺满主窗口，观感与其他卡片一致。
    注意：须声明在 App.qml 窗口根（场景坐标=窗口坐标，天然对齐）；
    嵌套声明会像 DirectoryPickerDialog 一样相对声明父偏移。
    说明文字直接写在 Text 绑定里（qsTr 随语言切换重译），
    不用 Repeater+ListModel（静态数据不会重新求值）。
*/
Dialog {
    id: root

    property var hostWindow: null

    modal: true
    title: qsTr("使用说明")

    width: root.hostWindow ? root.hostWindow.width : 800
    height: root.hostWindow ? root.hostWindow.height : 600

    // 首次展示（或任何时候打开）都标记已看，避免下次启动再弹
    onOpened: AppBridge.markHelpShown()

    contentItem: ColumnLayout {
        spacing: 0

        // ── 说明条目列表 ────────────────────────────
        Flickable {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.leftMargin: 24; Layout.rightMargin: 24
            Layout.topMargin: 8
            clip: true
            contentWidth: width
            contentHeight: entries.implicitHeight
            boundsBehavior: Flickable.StopAtBounds

            ColumnLayout {
                id: entries
                width: parent.width
                spacing: 14

                // 相机设置
                RowLayout {
                    spacing: 12
                    IconImage {
                        source: "../../images/camerasetting.svg"
                        Layout.preferredWidth: 26; Layout.preferredHeight: 26
                    }
                    ColumnLayout {
                        spacing: 2
                        Layout.fillWidth: true
                        Text {
                            text: qsTr("相机设置")
                            font.pixelSize: 14; font.bold: true
                            color: Colors.textPrimary
                        }
                        Text {
                            text: qsTr("搜索并连接大恒相机（目前仅支持该品牌），调节曝光、增益等相机参数")
                            font.pixelSize: 12
                            color: Colors.textSecondary
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                        }
                    }
                }

                // 光源设置
                RowLayout {
                    spacing: 12
                    IconImage {
                        source: "../../images/lightsetting.svg"
                        Layout.preferredWidth: 26; Layout.preferredHeight: 26
                    }
                    ColumnLayout {
                        spacing: 2
                        Layout.fillWidth: true
                        Text {
                            text: qsTr("光源设置")
                            font.pixelSize: 14; font.bold: true
                            color: Colors.textPrimary
                        }
                        Text {
                            text: qsTr("连接光源控制器，调节 4 路光源亮度")
                            font.pixelSize: 12
                            color: Colors.textSecondary
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                        }
                    }
                }

                // 通信设置
                RowLayout {
                    spacing: 12
                    IconImage {
                        source: "../../images/MQTT.svg"
                        Layout.preferredWidth: 26; Layout.preferredHeight: 26
                    }
                    ColumnLayout {
                        spacing: 2
                        Layout.fillWidth: true
                        Text {
                            text: qsTr("通信设置")
                            font.pixelSize: 14; font.bold: true
                            color: Colors.textPrimary
                        }
                        Text {
                            text: qsTr("配置串口与 MQTT 参数，连接云服务器并发送测试消息")
                            font.pixelSize: 12
                            color: Colors.textSecondary
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                        }
                    }
                }

                // 异常检测
                RowLayout {
                    spacing: 12
                    IconImage {
                        source: "../../images/Detec.svg"
                        Layout.preferredWidth: 26; Layout.preferredHeight: 26
                    }
                    ColumnLayout {
                        spacing: 2
                        Layout.fillWidth: true
                        Text {
                            text: qsTr("异常检测")
                            font.pixelSize: 14; font.bold: true
                            color: Colors.textPrimary
                        }
                        Text {
                            text: qsTr("检测功能开发中，敬请期待")
                            font.pixelSize: 12
                            color: Colors.textSecondary
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                        }
                    }
                }

                // 图像采集
                RowLayout {
                    spacing: 12
                    IconImage {
                        source: "../../images/pictures.svg"
                        Layout.preferredWidth: 26; Layout.preferredHeight: 26
                    }
                    ColumnLayout {
                        spacing: 2
                        Layout.fillWidth: true
                        Text {
                            text: qsTr("图像采集")
                            font.pixelSize: 14; font.bold: true
                            color: Colors.textPrimary
                        }
                        Text {
                            text: qsTr("开启相机连续采集，按设定间隔自动保存图像到指定目录")
                            font.pixelSize: 12
                            color: Colors.textSecondary
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                        }
                    }
                }

                // 设置
                RowLayout {
                    spacing: 12
                    IconImage {
                        source: "../../images/settings.svg"
                        Layout.preferredWidth: 26; Layout.preferredHeight: 26
                    }
                    ColumnLayout {
                        spacing: 2
                        Layout.fillWidth: true
                        Text {
                            text: qsTr("设置")
                            font.pixelSize: 14; font.bold: true
                            color: Colors.textPrimary
                        }
                        Text {
                            text: qsTr("主题、配色、语言等软件偏好设置")
                            font.pixelSize: 12
                            color: Colors.textSecondary
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                        }
                    }
                }
            }
        }

        // ── 底部按钮 ────────────────────────────────
        RowLayout {
            Layout.fillWidth: true
            Layout.margins: 24
            spacing: 8

            Item { Layout.fillWidth: true }

            Button {
                text: qsTr("开始使用")
                implicitHeight: 32
                font.pixelSize: 12

                contentItem: Text {
                    text: parent.text
                    font: parent.font
                    color: "#ffffff"
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }

                onClicked: root.close()

                background: Rectangle {
                    radius: 4
                    color: parent.hovered ? Colors.interactivePressed : Colors.statusConnected
                }
            }
        }
    }
}
