
/*
This is a UI file (.ui.qml) that is intended to be edited in Qt Design Studio only.
It is supposed to be strictly declarative and only uses a subset of QML. If you edit
this file manually, you might introduce QML code that is not supported by Qt Design Studio.
Check out https://doc.qt.io/qtcreator/creator-quick-ui-forms.html for details on .ui.qml files.
*/
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software
import "pages"
import "pages/components"
Row {
    id: row
    anchors.fill: parent
    spacing: 0

    // 侧边栏宽度随窗口自适应：窄屏只显示图标
    readonly property bool sidebarCollapsed: row.width < 900
    readonly property int  sidebarWidth: sidebarCollapsed ? 70 : 240

    // 左列布局
    Rectangle {
        id: rectangle
        width: row.sidebarWidth
        height: parent.height
        color: Colors.sidebarBg

        Behavior on width { NumberAnimation { duration: 200; easing.type: Easing.InOutCubic } }

        Column {
            id: column
            anchors.fill: parent
            anchors.margins: row.sidebarCollapsed ? 10 : 15
            spacing: 10
            height: parent.height

            Rectangle {
                id: rectangle1
                width: parent.width
                height: 60
                color: "transparent"
                anchors.horizontalCenter: parent.horizontalCenter

                Text {
                    anchors.centerIn: parent
                    text: row.sidebarCollapsed ? "Du" : qsTr("𝒟𝓊𝒜𝒟")
                    font.pixelSize: row.sidebarCollapsed ? 18 : 30
                    font.bold: true
                    color: Colors.textPrimary
                    Behavior on font.pixelSize { NumberAnimation { duration: 200 } }
                }
            }

            // 内部使用columnLayout布局
            ColumnLayout {
                id: column2
                width: parent.width
                height: parent.height - rectangle1.height - column.spacing
                NavButton {
                    id: btnCamera
                    text: qsTr("相机设置")
                    iconsource: "images/camerasetting.svg"
                    checked: true
                    collapsed: row.sidebarCollapsed
                }
                NavButton {
                    id: btnLight
                    text: qsTr("光源设置")
                    iconsource: "images/lightsetting.svg"
                    collapsed: row.sidebarCollapsed
                }
                NavButton {
                    id: btnComm
                    text: qsTr("通信设置")
                    iconsource: "images/MQTT.svg"
                    collapsed: row.sidebarCollapsed
                }
                NavButton {
                    id: btnDetect
                    text: qsTr("异常检测")
                    iconsource: "images/Detec.svg"
                    collapsed: row.sidebarCollapsed
                }
                NavButton {
                    id: btnCollect
                    text: qsTr("图像采集")
                    iconsource: "images/pictures.svg"
                    collapsed: row.sidebarCollapsed
                }

                Item { Layout.fillHeight: true }

                NavButton {
                    id: settings
                    text: qsTr("设置")
                    iconsource: "images/settings.svg"
                    collapsed: row.sidebarCollapsed
                }

                ButtonGroup {
                    id: navGroup
                    buttons: [btnCamera, btnLight, btnComm, btnDetect, btnCollect, settings]
                }
            }
        }
    }

    // 右列布局 — 用 StackLayout 切换不同功能页面
    Rectangle {
        id: rectangle2
        width: parent.width - rectangle.width
        height: parent.height
        color: Colors.contentBg

        StackLayout {
            id: pageStack
            anchors.fill: parent
            // 当前页面 = 选中按钮在 ButtonGroup 中的序号
            // 注意：页面顺序必须和 navGroup.buttons 中的按钮顺序一致！
            currentIndex: navGroup.buttons.indexOf(navGroup.checkedButton)

            CameraPage {} // 0 — 相机设置
            LightPage {} // 1 — 光照设置
            CommPage {} // 2 — 通信设置
            DetectPage {} // 3 — 异常检测
            CollectPage {} // 4 — 图像采集
            SettingsPage {} // 5 — 设置
        }
    }
}
