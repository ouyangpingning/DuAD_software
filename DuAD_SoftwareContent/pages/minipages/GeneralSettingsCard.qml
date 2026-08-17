import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software
import "../components"

/*
    通用设置卡片 — 主题、语言、配色方案。

    关键设计：model 用稳定 key（不翻译），显示文本走 displayFunc。
    否则语言切换 → model 文本变化 → ComboBox 重建 → currentIndex 被重置。
*/
Item {
    id: root

    property int themeIndex: 2      // 0=light 1=dark 2=system
    property int languageIndex: 1   // 0=en 1=zh_CN 2=zh_TW
    property int presetIndex: 0     // 0=default 1=ocean 2=forest 3=sunset 4=system

    signal languageRequested(int index)

    // 启动时恢复 Python 端保存的语言选择
    Component.onCompleted: root.languageIndex = AppBridge.languageIndex

    implicitWidth: 420
    implicitHeight: contentLayout.implicitHeight + 32

    // ── 显示函数（实时翻译，不随 model 变化）──────────
    function themeDisplay(key) {
        if (key === "light")   return qsTr("亮色")
        if (key === "dark")    return qsTr("暗色")
        return qsTr("跟随系统")
    }
    function langDisplay(key) {
        // 语言名按惯例用本语言自称，不翻译
        if (key === "en")    return "English"
        if (key === "zh_TW") return "繁體中文"
        return "简体中文"
    }
    function presetDisplay(key) {
        if (key === "ocean")   return qsTr("海洋蓝")
        if (key === "forest")  return qsTr("森林绿")
        if (key === "sunset")  return qsTr("日落橙")
        if (key === "system")  return qsTr("跟随系统")
        return qsTr("默认")
    }

    Rectangle {
        anchors.fill: parent
        radius: 12
        color: Colors.contentBg
        border { width: 0; color: Colors.cardBorder }

        ColumnLayout {
            id: contentLayout
            spacing: 12
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 24 }

            Text { text: qsTr("通用设置"); font.pixelSize: 14; font.bold: true; color: Colors.textPrimary }
            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder }

            ComboRow {
                label: qsTr("主题")
                model: ["light", "dark", "system"]
                displayFunc: root.themeDisplay
                currentIndex: root.themeIndex
                onActivated: {
                    root.themeIndex = index
                    Colors.setTheme(model[index])
                }
            }

            ComboRow {
                label: qsTr("语言")
                model: ["en", "zh_CN", "zh_TW"]
                displayFunc: root.langDisplay
                currentIndex: root.languageIndex
                onActivated: {
                    root.languageIndex = index
                    root.languageRequested(index)
                }
            }

            ComboRow {
                label: qsTr("配色")
                model: ["default", "ocean", "forest", "sunset", "system"]
                displayFunc: root.presetDisplay
                currentIndex: root.presetIndex
                onActivated: {
                    root.presetIndex = index
                    Colors.setPreset(model[index])
                }
            }

        }
    }
}
