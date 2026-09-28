import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import DuAD_Software
import "../components"

/*
    **公用**协议显示框（2026-09-28）。

    用户原话："板子的协议显示修改为公用的，因为我只要将 usb 线接到不同的板子上，
    这个就会有对应的指令。" —— 两块板子跑同一套行协议，所以界面上只画**一个**框：
    `ProtoHub` 把两路按到达顺序汇成一条，每行带 `[XY]` / `[Z]` 前缀。

    层次（协议框能看见两层，这是它的价值所在）：
      ① 命令层：`→ json` / `← {...}` —— 界面与板子之间**行协议**的原文；
      ② 驱动器层：`@TX` / `@RX`（固件 `trace on` 时镜像回来）—— ESP32 与 PD42S1
         之间**自定义串口帧**（`C5 <addr> <code> … <chk> 5C`）的原文。
      ⚠ 第 ② 层只有 **Z 轴那块板子的固件**有（二轴板没有 `trace` 命令），所以切到
        二轴时那个开关是灰的，并在旁边写明原因（本项目铁律：禁用必须说原因）。

    为什么把"来源选择"做成两个小按钮而不是下拉框：只有两个来源、而且**需要一眼看到
    哪块板子连着**（按钮文字在未连接时变灰）—— 下拉框把这两个信息都藏起来了。
*/
Item {
    id: root
    objectName: "protoPanel"

    // ============================================================
    // 公有 API（页面把 ProtoHub / 两块桥的状态绑进来）
    // ============================================================
    property var lines: []              // ProtoHub.lines（已带 [XY]/[Z] 前缀）
    property bool paused: false
    property bool connected: false      // **当前选中**那块板子连上了没
    property bool traceSupported: true  // 当前来源的固件有没有 trace
    property bool traceOn: false
    property string sourceLabel: ""     // 当前来源的显示名（提示语里用）

    // [{ key: "xy", label: qsTr("二轴相机平台"), connected: true }, …]
    property var sources: []
    // 住在「高级」折叠节里时不重复报标题（折叠头已经写了"协议显示"）
    property bool showTitle: true
    property string sourceKey: "z"

    signal sourcePicked(string key)
    signal pauseToggled(bool paused)
    signal clearRequested()
    signal commandEntered(string text)
    signal traceToggled(bool on)

    // 发送一行（发送按钮与回车都走它）。
    // ⚠ 信号处理里调函数没问题；**绑定里**调函数才会冻结（AGENTS.md 第 6 条）。
    function _send() {
        var t = cmdField.text.trim()
        if (t.length === 0) return
        root.commandEntered(t)
        cmdField.text = ""
    }

    implicitWidth: 460
    // ⚠ 高度必须挂在**内层 ColumnLayout** 上：挂在 Rectangle 上拿到的是 0
    //   （Rectangle 的 implicitHeight 不会被自撑开），整张卡只剩 32px。
    implicitHeight: body.implicitHeight + 32

    // 卡片底板：白底 + 描边 + 硬阴影（与连接卡/手动控制卡同一套，见 CardSurface.qml）
    CardSurface {
        anchors.fill: parent

        ColumnLayout {
            id: body
            objectName: "protoBody"
            spacing: 8
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 24 }

            // ── 标题 + 来源选择 + 行数 ──────────────────────
            // ⚠ 用 Flow 而不是 RowLayout：中列窄的时候（三列版面在 900~1280 之间）
            //   标题 + 两个来源 chip + 行数 会超过卡片宽度，RowLayout 是**硬撑破**
            //   （几何守卫当场抓到：内容区 345，最宽一行 390），Flow 会自动换行。
            Flow {
                Layout.fillWidth: true
                spacing: 8

                Text {
                    visible: root.showTitle
                    text: qsTr("协议显示（两块板子公用）")
                    font.pixelSize: 14
                    font.bold: true
                    color: Colors.textPrimary
                }

                RowLayout {
                    spacing: 4

                    Repeater {
                        model: root.sources

                        delegate: Rectangle {
                            required property var modelData
                            objectName: "protoSource_" + modelData.key
                            // 与「步长」那种 chip 完全同一套样式（radius 6 / 高 28 /
                            // 选中 interactivePressed）—— 用户要求用组件里既有的按钮风格
                            implicitHeight: 28
                            implicitWidth: srcText.implicitWidth + 20
                            radius: 6
                            color: modelData.key === root.sourceKey
                                   ? Colors.interactivePressed
                                   : (srcMa.containsMouse ? Colors.interactiveHover
                                                          : "transparent")
                            border {
                                width: 1
                                color: modelData.key === root.sourceKey
                                       ? Colors.interactivePressed : Colors.cardBorder
                            }

                            Text {
                                id: srcText
                                anchors.centerIn: parent
                                // 未连接的来源文字变灰：一眼看出现在能看到谁的话
                                text: modelData.label
                                font.pixelSize: 11
                                color: modelData.connected ? Colors.textPrimary
                                                           : Colors.textPlaceholder
                            }
                            MouseArea {
                                id: srcMa
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: root.sourcePicked(modelData.key)
                            }
                        }
                    }
                }

                Text {
                    objectName: "protoCount"
                    text: qsTr("%1 行").arg(root.lines.length)
                    font.pixelSize: 10
                    color: Colors.textPlaceholder
                }
            }

            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Colors.cardBorder }

            // ── 开关：帧镜像（只有 Z 轴板支持）+ 暂停 + 清空 ──
            RowLayout {
                Layout.fillWidth: true
                spacing: 10

                SwitchRow {
                    id: traceSwitch
                    objectName: "protoTraceSwitch"
                    Layout.fillWidth: true
                    label: qsTr("驱动器帧镜像（@TX/@RX）")
                    on: root.traceOn
                    enabled: root.connected && root.traceSupported
                    onToggled: root.traceToggled(traceSwitch.on)
                }

                SwitchRow {
                    id: pauseSwitch
                    objectName: "protoPauseSwitch"
                    Layout.fillWidth: true
                    label: qsTr("暂停记录")
                    on: root.paused
                    onToggled: root.pauseToggled(pauseSwitch.on)
                }

                Button {
                    objectName: "protoClearButton"
                    implicitHeight: 32
                    implicitWidth: implicitContentWidth + 28
                    text: qsTr("清空")
                    onClicked: root.clearRequested()

                    background: Rectangle {
                        radius: 8
                        color: parent.pressed ? Colors.interactivePressed
                                              : (parent.hovered ? Colors.interactiveHover : "transparent")
                        border { width: 1; color: Colors.cardBorder }
                    }
                    contentItem: Text {
                        text: parent.text
                        font.pixelSize: 12
                        color: Colors.textPrimary
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                }
            }

            // 帧镜像开关点不动的原因（禁用必须说原因：本项目铁律）
            Text {
                objectName: "protoTraceHint"
                Layout.fillWidth: true
                visible: !root.traceSupported
                text: qsTr("⚠ 当前这块板子的固件没有帧镜像（`trace`）—— 只有 Z 轴那块有。"
                           + "二轴的驱动器报文要在板子 USB 控制台上看。")
                font.pixelSize: 10
                color: Colors.textPlaceholder
                wrapMode: Text.Wrap
            }

            // ── 报文区 ──────────────────────────────────────
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: 170
                radius: 6
                color: Colors.pageBg
                border { width: 1; color: Colors.cardBorder }

                ListView {
                    id: protoView
                    objectName: "protoList"
                    anchors { fill: parent; margins: 8 }
                    clip: true
                    spacing: 1
                    model: root.lines
                    // 新行自动滚到底：否则用户看到的是最旧的一屏
                    onCountChanged: positionViewAtEnd()

                    delegate: Text {
                        required property string modelData
                        width: protoView.width
                        text: modelData
                        font.pixelSize: 10
                        font.family: "monospace"
                        // `←` 是收回来的（含驱动器帧），`→` 是发出去的
                        color: modelData.indexOf("←") >= 0 ? Colors.textPrimary
                                                           : Colors.textSecondary
                        // ⚠ 必须能收缩：一行几百字符的 JSON 否则会把卡片顶宽（AGENTS 第 9 条）
                        elide: Text.ElideRight
                    }
                }

                Text {
                    anchors.centerIn: parent
                    visible: root.lines.length === 0
                    text: root.connected ? qsTr("暂无收发记录（连接后 json 轮询会立刻出现）")
                                         : qsTr("未连接")
                    font.pixelSize: 11
                    color: Colors.textPlaceholder
                }
            }

            // ── 自定义命令（发给当前选中的那块板子）──────────
            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                TextField {
                    id: cmdField
                    objectName: "protoCommandInput"
                    Layout.fillWidth: true
                    enabled: root.connected
                    placeholderText: qsTr("自定义命令（发给本行选中的板子），例如 ver all / json / zcfg")
                    font.pixelSize: 12
                    color: enabled ? Colors.textPrimary : Colors.textPlaceholder
                    onAccepted: root._send()

                    background: Rectangle {
                        implicitHeight: 32
                        radius: 4
                        color: cmdField.enabled ? Colors.pageBg : "transparent"
                        border {
                            width: 1
                            color: cmdField.activeFocus ? Colors.interactivePressed : Colors.cardBorder
                        }
                    }
                }

                Button {
                    objectName: "protoSendButton"
                    implicitHeight: 32
                    implicitWidth: implicitContentWidth + 28
                    enabled: root.connected
                    text: qsTr("发送")
                    onClicked: root._send()

                    background: Rectangle {
                        radius: 8
                        // ⚠ 用字面量 "transparent"：`Colors` 单例里**没有** transparent
                        //   （工程里的既有写法也都是字面量）。写成 `Colors.transparent`
                        //   会得到 undefined，运行时只报一行 `Unable to assign [undefined]
                        //   to QColor` —— 界面看着正常但颜色是错的。
                        color: !parent.enabled
                               ? "transparent"
                               : (parent.pressed ? Colors.interactivePressed
                                                 : (parent.hovered ? Colors.interactiveHover
                                                                   : "transparent"))
                        border { width: 1; color: Colors.cardBorder }
                    }
                    contentItem: Text {
                        text: parent.text
                        font.pixelSize: 12
                        color: parent.enabled ? Colors.textPrimary : Colors.textPlaceholder
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                }
            }

            // 点不动的原因写在旁边（本项目铁律）
            Text {
                objectName: "protoHint"
                Layout.fillWidth: true
                visible: !root.connected
                text: qsTr("⚠ 「%1」未连接 —— 命令发不出去。先点上面的平台卡片连接。")
                      .arg(root.sourceLabel)
                font.pixelSize: 10
                color: Colors.statusDisconnected
                wrapMode: Text.Wrap
            }
        }
    }
}
