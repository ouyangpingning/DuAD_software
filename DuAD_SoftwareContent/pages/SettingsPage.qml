import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software
import "components"
import "minipages"

Item {

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
                // 自适应列宽（2026-10-08 评审）：原来写死 420，1280 宽的窗口里两侧各空 ~400px；
                // 封顶 640 —— 再宽表单行就太散（标签与输入框隔得太远）
                width: Math.min(640, Math.max(360, parent.width - 80))
                spacing: 10
                x: Math.max(0, (parent.width - width) / 2)
                y: 24

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    Text {
                        text: qsTr("软件设置")
                        font.pixelSize: 18
                        font.bold: true
                        color: Colors.textPrimary
                    }
                }

                // 副标题：一句话说明这页做什么（文案复用 HelpDialog 已翻译的那句，不新增词条）
                Text {
                    Layout.fillWidth: true
                    Layout.topMargin: -6
                    Layout.bottomMargin: 4
                    text: qsTr("主题、配色、语言等软件偏好设置")
                    font.pixelSize: 12
                    color: Colors.textSecondary
                    wrapMode: Text.WordWrap
                }

                GeneralSettingsCard {
                    Layout.fillWidth: true
                    onLanguageRequested: AppBridge.setLanguage(index)
                }

                AboutCard {
                    Layout.fillWidth: true
                }
            }
        }
    }
}
