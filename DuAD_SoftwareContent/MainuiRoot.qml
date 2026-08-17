import QtQuick
import QtQuick.Controls

Item {
    id: root
    // 不设 anchors.fill，由父组件（App.qml）决定位置和大小
    // 否则会撑满整个 Window，盖住标题栏

    MainWindow {
        id: uiRoot
        anchors.fill: parent
    }

}
