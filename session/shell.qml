import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

ShellRoot {
    id: root
    property bool deskOpen: false
    function toggleDesk() {
        if (!deskOpen) Quickshell.execDetached(["niri", "msg", "action", "close-overview"])
        deskOpen = !deskOpen
    }

    function openApps() {
        deskOpen = false
        Quickshell.execDetached(["fuzzel"])
    }

    component FlowButton: Button {
        id: control
        focusPolicy: Qt.StrongFocus
        Keys.onReturnPressed: control.clicked()
        Keys.onEnterPressed: control.clicked()
        implicitHeight: 58
        contentItem: Text {
            text: control.text; color: "#d7e5df"; font.pixelSize: 14
            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 12; color: control.down ? "#29453a" : "#171f1e"
            border.width: control.activeFocus ? 2 : 1
            border.color: control.activeFocus ? "#a0d9c0" : "#34423d"
        }
    }

    IpcHandler {
        target: "desk"
        function apps(): void { root.openApps() }
        function toggle(): void { root.toggleDesk() }
        function overview(): void {
            const action = root.deskOpen ? "open-overview" : "toggle-overview"
            root.deskOpen = false
            Quickshell.execDetached(["niri", "msg", "action", action])
        }
    }

    // shortcut: Desk opens without spatial animation; add transitions when compositor integration supports them.
    PanelWindow {
        visible: root.deskOpen
        anchors { top: true; bottom: true; left: true; right: true }
        color: "#0d1313"
        exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive

        Rectangle {
            anchors.top: parent.top; anchors.horizontalCenter: parent.horizontalCenter; anchors.topMargin: 8
            width: 150; height: 36; radius: 18; color: "#080c0c"; border.color: "#303d38"
            Text { anchors.centerIn: parent; text: "●   Flow   ···"; color: "#b8ddcb"; font.bold: true }
            MouseArea { anchors.fill: parent; onClicked: root.deskOpen = false }
        }
        FocusScope {
            anchors.fill: parent
            focus: true
            Keys.onEscapePressed: root.deskOpen = false
            Flickable {
                id: deskScroller
                function ensureVisible(item) {
                    const top = item.mapToItem(contentItem, 0, 0).y
                    if (top < contentY) contentY = Math.max(0, top)
                    else if (top + item.height > contentY + height)
                        contentY = Math.min(contentHeight - height, top + item.height - height)
                }
                anchors.fill: parent
                contentWidth: width
                contentHeight: Math.max(height, deskContent.implicitHeight + 120)
                boundsBehavior: Flickable.StopAtBounds
                clip: true
                Column {
                    id: deskContent
                    x: (parent.width - width) / 2
                    y: Math.max(70, (parent.height - height) / 2)
                    width: Math.max(200, Math.min(parent.width - 48, 910))
                    spacing: 24
                    Text { text: "●  FLOW / YOUR DESK"; color: "#a0d9c0"; font.bold: true; font.letterSpacing: 2; font.pixelSize: 13 }
                    Text {
                        text: "Everything<br>in its <font color='#9fe2c5'>place.</font>"
                        textFormat: Text.RichText
                        color: "#eff5f2"; font.bold: true
                        font.pixelSize: Math.max(36, Math.min(100, parent.width / 7))
                        lineHeight: 0.95
                    }
                    Text { text: "Your tools are here. Your work is right where you left it."; color: "#9baea7"; font.pixelSize: 17; wrapMode: Text.Wrap; width: parent.width }
                    FlowButton {
                        id: searchButton
                        onActiveFocusChanged: if (activeFocus) deskScroller.ensureVisible(searchButton)
                        width: Math.min(parent.width, 590)
                        text: "⌕   Search installed apps   ·   Super K"
                        onClicked: root.openApps()
                    }
                    Text { text: "PINNED SPACES"; color: "#91a59d"; font.bold: true; font.letterSpacing: 1.5; font.pixelSize: 12 }
                    Text { text: "Pinned spaces are coming soon"; color: "#80948b"; font.pixelSize: 14 }
                    Flow {
                        width: parent.width
                        spacing: 12
                        Repeater {
                            model: ["Apps", "Files", "Overview", "Return to work"]
                            FlowButton {
                                id: toolButton
                                required property string modelData
                                onActiveFocusChanged: if (activeFocus) deskScroller.ensureVisible(toolButton)
                                width: modelData === "Return to work" ? 145 : 100
                                text: modelData
                                onClicked: {
                                    root.deskOpen = false
                                    if (modelData === "Apps") root.openApps()
                                    else if (modelData === "Files") Quickshell.execDetached(["nautilus"])
                                    else if (modelData === "Overview") Quickshell.execDetached(["niri", "msg", "action", "open-overview"])
                                }
                            }
                        }
                    }
                    Text { text: "Tab to navigate · Esc or Super H to return"; color: "#788d84"; font.pixelSize: 12 }
                }
            }
        }
    }
    PanelWindow {
        anchors.top: true
        implicitWidth: 230; implicitHeight: 44; margins.top: 8
        color: "transparent"
        exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        Rectangle {
            anchors.fill: parent; radius: 22; color: "#0b1015"; border.color: "#33433e"
            Row {
                anchors.centerIn: parent; spacing: 18
                Text { text: "●  FLOW"; color: "#b8ddcb"; font.bold: true; anchors.verticalCenter: parent.verticalCenter
                    MouseArea { anchors.fill: parent; onClicked: root.toggleDesk() } }
                Text { text: "Apps"; color: "#ecf2f0"; font.pixelSize: 14; anchors.verticalCenter: parent.verticalCenter
                    MouseArea { anchors.fill: parent; onClicked: root.openApps() } }
                Text { text: Qt.formatTime(clock.date, "hh:mm"); color: "#ecf2f0"; font.pixelSize: 14; anchors.verticalCenter: parent.verticalCenter }
            }
        }
        SystemClock { id: clock; precision: SystemClock.Minutes }
    }
}
