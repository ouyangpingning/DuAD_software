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
                width: 420
                spacing: 10
                x: Math.max(0, (parent.width - width) / 2)
                y: 24

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    Text {
                        text: qsTr("软件设置")
                        font.pixelSize: 16
                        font.bold: true
                        color: Colors.textPrimary
                    }
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
