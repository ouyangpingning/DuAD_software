import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software

// 下拉选择行
RowLayout {
    property string label: ""
    property alias model: combo.model
    property alias currentIndex: combo.currentIndex
    property alias enabled: combo.enabled

    /* 显示函数: function(key) -> 显示文本。
       传入时 model 应为稳定 key（不翻译），显示文本由函数实时求值，
       避免语言切换导致 ComboBox 模型重建、currentIndex 重置。 */
    property var displayFunc: null

    signal activated(int index)

    spacing: 8

    Text {
        text: label
        font.pixelSize: 12
        color: Colors.textPrimary
    }

    RowLayout {
        Layout.fillWidth: true
    }

    ComboBox {
        id: combo

        font.pixelSize: 14
        onActivated: function(i) {
            parent.activated(i);
        }

        // 收起时的显示文本
        displayText: parent.displayFunc ? parent.displayFunc(currentValue) : currentText

        // 下拉列表项 — 用 displayFunc 实时翻译，亮/暗色跟随主题
        delegate: ItemDelegate {
            width: combo.width
            height: 32
            text: combo.parent.displayFunc ? combo.parent.displayFunc(modelData) : modelData
            font.pixelSize: 14
            highlighted: combo.highlightedIndex === index

            contentItem: Text {
                text: parent.text
                font: parent.font
                color: Colors.textPrimary
                verticalAlignment: Text.AlignVCenter
                leftPadding: 8
            }

            background: Rectangle {
                radius: 4
                color: parent.highlighted ? Colors.interactiveHover : "transparent"
            }
        }

        // 下拉弹出层 — 亮/暗色跟随主题
        popup: Popup {
            y: combo.height + 2
            width: combo.width
            padding: 2

            // 强制窗口内渲染（Item 类型）：
            // Wayland/KWin 下窗口类型 popup 打开瞬间 delegate 未布局 → 高度 0 且
            // 不随 implicitHeight resize（niri 宽容故正常，KDE 空白）；Item 类型
            // 渲染在主窗口 overlay 内，无独立窗口 resize 问题
            popupType: Popup.Item

            // 高度由 model 长度同步计算（delegate 高 32），不依赖异步 contentHeight；
            // 上限 320 防止超长列表超出窗口
            height: Math.min(combo.count * 32 + 4, 320)

            contentItem: ListView {
                id: listView
                clip: true
                implicitHeight: contentHeight
                model: combo.delegateModel
                // Item 类型 popup 下 highlightedIndex 初始 undefined，需兜底
                currentIndex: combo.highlightIndex || 0
                highlightMoveDuration: 0
            }

            background: Rectangle {
                color: Colors.contentBg
                radius: 6
                border { width: 1; color: Colors.cardBorder }
            }
        }

        contentItem: Text {
            text: combo.displayText
            font: combo.font
            color: combo.enabled ? Colors.textPrimary : Colors.textPlaceholder
            verticalAlignment: Text.AlignVCenter
            leftPadding: 8
        }

        background: Rectangle {
            implicitWidth: 150
            implicitHeight: 32
            color: combo.hovered ? Colors.interactiveHover : Colors.pageBg
            radius: 4
        }
    }
}
