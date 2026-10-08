import QtQuick
import QtQuick.Layouts
import DuAD_Software

/*
    检测结论卡 —— DetectPage 图像下方那一条。

    2026-10-08 评审：原来的状态栏是一行 12px 小字（帧率 / 分数 / ● 正常），
    整页最重要的结论"正常还是异常"反而是最不起眼的元素；而且没跑推理时
    也亮着绿色「正常」（分数默认 0 < 阈值）。现在：
      · 左 = 大号结论 pill（三态：idle 待机 / normal 正常 / anomaly 异常）
      · 中 = 分数大号数字 + 阈值 + 与阈值的差
      · 右 = 采集帧率 / 推理频率 / 推理耗时

    ⚠ 只吃属性、不读任何桥（DetectPage 注入）—— 测试与目检可以单独摆它。
    ⚠ 分数是**带符号、无界、未归一化**的判别器输出（AGENTS：onnx_infer），
      所以这里只画"数值 + 与阈值的差"，**不画** 0~1 进度条（那等于偷偷做了 min-max）。
*/
Item {
    id: root

    property string verdict: "idle"          // "idle" | "normal" | "anomaly"
    property real score: 0
    property real threshold: 0
    property string captureFps: "0"
    property string inferFps: "0"
    property string latencyText: "—"

    implicitHeight: 76

    readonly property bool _idle: verdict === "idle"
    readonly property bool _bad: verdict === "anomaly"
    readonly property color _tone: _idle ? Colors.textPlaceholder
                                         : (_bad ? Colors.statusDisconnected : Colors.statusConnected)
    readonly property color _toneSoft: _idle ? Colors.contentBg
                                             : (_bad ? Colors.cardDangerBg : Colors.successSoft)

    // 一个"标签在上、数值在下"的小读数（右侧三组共用）
    component Stat: ColumnLayout {
        property string label: ""
        property string value: ""
        spacing: 2
        Text { text: parent.label; font.pixelSize: 11; color: Colors.textSecondary }
        Text { text: parent.value; font.pixelSize: 15; font.bold: true; color: Colors.textPrimary }
    }

    CardSurface {
        anchors.fill: parent
        cornerRadius: 10
        // 异常时整张卡描边变红：余光就能看到，不用盯着字
        borderColor: root._bad ? Colors.statusDisconnected : Colors.cardBorderStrong
    }

    RowLayout {
        anchors { fill: parent; leftMargin: 12; rightMargin: 20; topMargin: 10; bottomMargin: 12 }
        spacing: 20

        // ── 结论 pill ──
        Rectangle {
            Layout.preferredWidth: 132
            Layout.fillHeight: true
            radius: 8
            color: root._toneSoft
            Behavior on color { ColorAnimation { duration: 150 } }

            Row {
                anchors.centerIn: parent
                spacing: 10
                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    width: 12; height: 12; radius: 6
                    color: root._tone
                }
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: root._idle ? qsTr("待机") : (root._bad ? qsTr("异常") : qsTr("正常"))
                    font.pixelSize: 22
                    font.bold: true
                    color: root._tone
                }
            }
        }

        // ── 分数 ──
        ColumnLayout {
            spacing: 2
            Text { text: qsTr("异常分数"); font.pixelSize: 11; color: Colors.textSecondary }
            RowLayout {
                spacing: 12
                Text {
                    text: root._idle ? "—" : root.score.toFixed(3)
                    font.pixelSize: 24
                    font.bold: true
                    color: root._bad ? Colors.statusDisconnected : Colors.textPrimary
                }
                Text {
                    Layout.alignment: Qt.AlignBottom
                    Layout.bottomMargin: 4
                    text: {
                        var t = qsTr("阈值 %1").arg(root.threshold.toFixed(3))
                        if (root._idle) return t
                        var d = root.score - root.threshold
                        return t + "  ·  " + (d > 0 ? qsTr("高出 %1").arg(d.toFixed(3))
                                                   : qsTr("低于 %1").arg((-d).toFixed(3)))
                    }
                    font.pixelSize: 12
                    color: Colors.textSecondary
                }
            }
        }

        Item { Layout.fillWidth: true }

        // ── 运行指标 ──
        Stat { label: qsTr("采集帧率"); value: root.captureFps + " fps" }
        Stat { label: qsTr("推理频率"); value: root.inferFps + " fps" }
        Stat { label: qsTr("推理耗时"); value: root.latencyText }
    }
}
