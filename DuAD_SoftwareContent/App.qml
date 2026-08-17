import QtQuick
import QtQuick.Controls
import DuAD_Software
import "pages/components"

Window {
    id: window
    width: 1024
    height: 768
    minimumWidth: 800
    minimumHeight: 600

    color: Colors.windowBg
    visible: true
    title: qsTr("融合Dinov2与双分支训练架构的工业异常检测")

    // 隐藏原生标题栏
    flags: Qt.FramelessWindowHint | Qt.Window

    // ============================================================
    // 自定义标题栏
    // ============================================================
    Rectangle {
        id: titleBar
        anchors { top: parent.top; left: parent.left; right: parent.right }
        height: 40
        color: Colors.titleBarBg

        // 拖拽移动窗口
        MouseArea {
            anchors.fill: parent
            onPressed: window.startSystemMove()
            onDoubleClicked: {
                if (window.visibility === Window.Maximized)
                    window.showNormal()
                else
                    window.showMaximized()
            }
        }

        // 标题文字
        Text {
            anchors { left: parent.left; leftMargin: 12; verticalCenter: parent.verticalCenter }
            text: qsTr("𝒟𝓊𝒜𝒟  —  工业异常检测系统")
            color: Colors.titleBarText
            font.pixelSize: 14
            font.bold: true
        }

        // 窗口控制按钮
        Row {
            anchors { right: parent.right; verticalCenter: parent.verticalCenter }

            TitleBtn { text: "─"; onClicked: window.showMinimized() }
            TitleBtn {
                text: window.visibility === Window.Maximized ? "❐" : "□"
                onClicked: {
                    if (window.visibility === Window.Maximized)
                        window.showNormal()
                    else
                        window.showMaximized()
                }
            }
            TitleBtn { text: "✕"; isClose: true; onClicked: window.close() }
        }
    }

    // ============================================================
    // 主内容 — 动态拉伸，随窗口自适应
    // ============================================================
    MainuiRoot {
        id: mainScreen
        anchors {
            top: titleBar.bottom
            left: parent.left
            right: parent.right
            bottom: parent.bottom
        }
    }

    // ============================================================
    // 四边拖拽缩放
    // ============================================================
    // 左边缘
    MouseArea {
        width: 4; anchors { top: parent.top; bottom: parent.bottom; left: parent.left; leftMargin: -4 }
        cursorShape: Qt.SizeHorCursor
        onPressed: window.startSystemResize(Qt.LeftEdge)
    }
    // 右边缘
    MouseArea {
        width: 4; anchors { top: parent.top; bottom: parent.bottom; right: parent.right; rightMargin: -4 }
        cursorShape: Qt.SizeHorCursor
        onPressed: window.startSystemResize(Qt.RightEdge)
    }
    // 上边缘（标题栏区域不参与，避免冲突）
    MouseArea {
        height: 4; anchors { top: parent.top; left: parent.left; right: parent.right; topMargin: -4 }
        cursorShape: Qt.SizeVerCursor
        onPressed: window.startSystemResize(Qt.TopEdge)
    }
    // 下边缘
    MouseArea {
        height: 4; anchors { bottom: parent.bottom; left: parent.left; right: parent.right; bottomMargin: -4 }
        cursorShape: Qt.SizeVerCursor
        onPressed: window.startSystemResize(Qt.BottomEdge)
    }

    // 四角
    MouseArea {
        anchors { top: parent.top; left: parent.left; topMargin: -4; leftMargin: -4 }
        width: 8; height: 8; cursorShape: Qt.SizeFDiagCursor
        onPressed: window.startSystemResize(Qt.TopLeftCorner)
    }
    MouseArea {
        anchors { top: parent.top; right: parent.right; topMargin: -4; rightMargin: -4 }
        width: 8; height: 8; cursorShape: Qt.SizeBDiagCursor
        onPressed: window.startSystemResize(Qt.TopRightCorner)
    }
    MouseArea {
        anchors { bottom: parent.bottom; left: parent.left; bottomMargin: -4; leftMargin: -4 }
        width: 8; height: 8; cursorShape: Qt.SizeBDiagCursor
        onPressed: window.startSystemResize(Qt.BottomLeftCorner)
    }
    MouseArea {
        anchors { bottom: parent.bottom; right: parent.right; bottomMargin: -4; rightMargin: -4 }
        width: 8; height: 8; cursorShape: Qt.SizeFDiagCursor
        onPressed: window.startSystemResize(Qt.BottomRightCorner)
    }

    // ============================================================
    // 使用说明页 — 首次启动自动弹出；设置页按钮经 AppBridge.helpRequested 重开。
    // 声明在窗口根（场景坐标=窗口坐标，铺满无需偏移修正）
    // ============================================================
    HelpDialog {
        id: helpDialog
        hostWindow: window
    }

    // 首次启动延迟弹出（等窗口/Overlay 就绪），并常驻监听设置页的"使用说明"信号
    Component.onCompleted: {
        AppBridge.helpRequested.connect(function () { helpDialog.open() })
        if (AppBridge.shouldShowHelp())
            helpStartTimer.start()
    }

    Timer {
        id: helpStartTimer
        interval: 400
        onTriggered: helpDialog.open()
    }

    // 桥存活诊断 — Python 侧引用丢失时 QML 侧桥变 null（界面"点了没反应"）。
    // 正常运行时不应有任何输出；出现 "桥丢失" 日志即说明 Python 侧被 GC。
    Timer {
        interval: 5000
        repeat: true
        running: true
        onTriggered: {
            if (typeof AppBridge === "undefined" || AppBridge === null)
                console.log("[DIAG] AppBridge 丢失!")
            if (typeof CameraBridge === "undefined" || CameraBridge === null)
                console.log("[DIAG] CameraBridge 丢失!")
            if (typeof AlgorithmBridge === "undefined" || AlgorithmBridge === null)
                console.log("[DIAG] AlgorithmBridge 丢失!")
        }
    }

    // ============================================================
    // 标题栏按钮
    // ============================================================
    component TitleBtn: Rectangle {
        width: 46; height: titleBar.height
        color: {
            if (isClose && ma.containsMouse) return Colors.titleBarCloseBtnHover
            if (ma.containsMouse) return Colors.titleBarBtnHover
            return "transparent"
        }

        property alias text: label.text
        property bool isClose: false
        signal clicked()

        Text {
            id: label
            anchors.centerIn: parent
            color: (isClose && ma.containsMouse) ? Colors.titleBarCloseBtnText : Colors.titleBarBtnText
            font.pixelSize: 16
        }
        MouseArea {
            id: ma
            anchors.fill: parent
            hoverEnabled: true
            onClicked: parent.clicked()
        }
    }
}
