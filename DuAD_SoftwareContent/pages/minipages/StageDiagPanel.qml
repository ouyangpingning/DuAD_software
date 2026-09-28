import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software
import "../components"

/*
    诊断面板（默认折叠）—— 排障用，平时收起来。

    内容按"排查顺序"排：
      ① 最近一条错误（用户最需要先看到的）
      ② 板子回的状态摘要（电压/使能/信号/关节角）
      ③ 日志（bridge 的每一句，含被拒绝的原因）

    ⭐ **2026-09-13 大扫除**：这里原来堆着一整套「无限位回零」的操作界面
      （方式下拉 / 回零速度 / 限位电流 / 应用参数 / 目标角落 / 角落回零 /
       中断回零 / 四个单趟按钮）和七八条几百字的注意事项。
      那套方案已经放弃 —— 它的判定靠"相电流越过阈值"，而皮带一打滑电流就上不去，
      判据永远凑不齐（真机 right 轴就是这么失败的）。

      **回零改走限位开关**（固件已有 `seek`，见 Steppermotor/docs/使用说明.md §5.6），
      上位机这边的限位归零界面**以后再加** —— 加的时候只需要在这里放一个按钮，
      调 `StageBridge.homeAll()`（它已经把方向定死成"X 左 + Y 外"这个零点角）。
      ⚠ 现在先不放按钮：固件的 `seek` 目前还是阻塞式的（走 TCP 会把通道占住几秒），
        要等它改成 `nowait` 变体再接界面 —— 否则又是"回零期间按停止没反应"。

    ✅ 保留的三样都是**与回零方案无关**的通用排障设施：
      板子状态摘要、日志、立即刷新。
*/
Item {
    id: root

    property bool expanded: false
    property bool connected: false
    property string lastError: ""
    property string diagText: ""
    property var logLines: []

    signal pollRequested()

    implicitWidth: 460
    implicitHeight: expanded ? contentLayout.implicitHeight + 32 : 0
    clip: true

    Behavior on implicitHeight {
        NumberAnimation { duration: 250; easing.type: Easing.InOutCubic }
    }

    Rectangle {
        anchors.fill: parent
        radius: 12
        color: Colors.contentBg

        ColumnLayout {
            id: contentLayout
            spacing: 10
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 24 }

            Text {
                text: qsTr("诊断")
                font.pixelSize: 14
                font.bold: true
                color: Colors.textPrimary
            }

            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder }

            // ① 最近错误
            Text {
                visible: root.lastError.length > 0
                Layout.fillWidth: true
                text: root.lastError
                font.pixelSize: 12
                color: Colors.statusDisconnected
                wrapMode: Text.Wrap
            }

            // ② 状态摘要
            // 板子状态：原来用 ReadonlyRow，但诊断摘要有 70+ 字符且不能收缩 ——
            // 它把整个 ColumnLayout 撑宽到 628（容器只有 412），导致同列的控件被推出卡片裁掉。
            // 换成"标签 + 自动换行的等宽块"，既不会撑宽，长内容也看得全。
            Text {
                text: qsTr("板子状态")
                font.pixelSize: 12
                color: Colors.textPrimary
            }

            Rectangle {
                Layout.fillWidth: true
                implicitHeight: statusText.implicitHeight + 12
                radius: 6
                color: Colors.pageBg

                Text {
                    id: statusText
                    objectName: "boardStatus"
                    anchors { fill: parent; margins: 6 }
                    text: root.connected ? root.diagText : qsTr("未连接")
                    font.pixelSize: 11
                    font.family: "monospace"
                    color: root.connected ? Colors.textPrimary : Colors.textPlaceholder
                    wrapMode: Text.Wrap
                }
            }

            // ③ 日志
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: 120
                radius: 6
                color: Colors.pageBg
                border { width: 1; color: Colors.cardBorder }

                ListView {
                    id: logView
                    anchors { fill: parent; margins: 8 }
                    clip: true
                    spacing: 2
                    model: root.logLines
                    onCountChanged: positionViewAtEnd()

                    delegate: Text {
                        required property string modelData
                        width: logView.width
                        text: modelData
                        font.pixelSize: 10
                        font.family: "monospace"
                        color: Colors.textSecondary
                        wrapMode: Text.Wrap
                    }
                }

                Text {
                    anchors.centerIn: parent
                    visible: root.logLines.length === 0
                    text: qsTr("暂无日志")
                    font.pixelSize: 11
                    color: Colors.textPlaceholder
                }
            }

            Button {
                objectName: "pollButton"
                Layout.fillWidth: true
                implicitHeight: 30
                enabled: root.connected
                text: qsTr("立即刷新状态")
                onClicked: root.pollRequested()
            }

            // 唯一保留的一句说明：日志里下发的指令是**度**（固件角度域），
            // 而界面上全是 mm。不知道这条换算关系，看日志会以为数值错了。
        }
    }
}
