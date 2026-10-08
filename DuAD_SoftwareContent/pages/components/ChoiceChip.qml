import QtQuick
import QtQuick.Controls
import DuAD_Software

/*
    单选小 chip —— 「步长 0.1/1/10/50」「回零方向 向上/向下」这类**一组里选一个**的小按钮。

    为什么抽出来：原来 StageJogPanel / ZStageJogPanel 里各手抄了一份（Z 卡还抄了两份），
    三份一模一样。2026-10-08 评审要把选中态从淡薄荷（#aee9e7，和悬停几乎分不出）
    改成实色强调，抄三遍迟早漂移。

    选中态 = 实色 `accent` + `accentContent` 字 + 加粗：步长是**安全相关参数**
    （按一下走多远），必须一眼认出当前档。

    ⚠ `checked` 由使用方绑定驱动；需要时使用方自己设 `checkable: false`
      （否则 Qt 点击时自己翻转 checked、打断绑定 —— ZStageJogPanel 文件头记过这个坑）。
*/
Button {
    id: chip

    implicitHeight: 28
    implicitWidth: 36

    background: Rectangle {
        radius: 6
        color: chip.checked ? (chip.enabled ? Colors.accent : Colors.cardBorderStrong)
                            : (chip.hovered ? Colors.interactiveHover : "transparent")
        border { width: 1; color: chip.checked ? (chip.enabled ? Colors.accentPressed : Colors.cardBorderStrong)
                                               : Colors.cardBorder }
        Behavior on color { ColorAnimation { duration: 120 } }
    }
    contentItem: Text {
        text: chip.text
        font.pixelSize: 12
        font.bold: chip.checked
        color: !chip.enabled ? Colors.textPlaceholder
                             : (chip.checked ? Colors.accentContent : Colors.textPrimary)
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }
}
