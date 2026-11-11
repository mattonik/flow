import QtQuick
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

    IpcHandler {
        target: "desk"
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
            Column {
                anchors.centerIn: parent
                width: Math.min(parent.width - 80, 910)
                spacing: 28
                Text { text: "●  FLOW / YOUR DESK"; color: "#a0d9c0"; font.bold: true; font.letterSpacing: 2; font.pixelSize: 13 }
                Text {
                    text: "Everything<br>in its <font color='#9fe2c5'>place.</font>"
                    textFormat: Text.RichText
                    color: "#eff5f2"; font.bold: true
                    font.pixelSize: Math.max(44, Math.min(100, parent.width / 7))
                    lineHeight: 0.95
                }
                Text { text: "Your tools are here. Your work is right where you left it."; color: "#9baea7"; font.pixelSize: 17; wrapMode: Text.Wrap; width: parent.width }
                Rectangle {
                    width: Math.min(parent.width, 590); height: 58; radius: 14
                    color: "#171e1e"; border.color: "#38413f"
                    Text { anchors.centerIn: parent; text: "⌕   Search installed apps   ·   Super K"; color: "#b8c7c1"; font.pixelSize: 16 }
                    MouseArea { anchors.fill: parent; onClicked: { root.deskOpen = false; Quickshell.execDetached(["fuzzel"]) } }
                }
                Text { text: "PINNED SPACES"; color: "#91a59d"; font.bold: true; font.letterSpacing: 1.5; font.pixelSize: 12 }
                Text { text: "No pinned spaces yet"; color: "#80948b"; font.pixelSize: 14 }
                Flow {
                    width: parent.width
                    spacing: 12
                    Repeater {
                        model: ["Apps", "Files", "Overview", "Return to work"]
                        Rectangle {
                            required property string modelData
                            width: modelData === "Return to work" ? 145 : 100; height: 62; radius: 12
                            color: "#171f1e"; border.color: "#34423d"
                            Text { anchors.centerIn: parent; text: parent.modelData; color: "#d7e5df"; font.pixelSize: 14 }
                            MouseArea {
                                anchors.fill: parent
                                onClicked: {
                                    root.deskOpen = false
                                    if (modelData === "Apps") Quickshell.execDetached(["fuzzel"])
                                    else if (modelData === "Files") Quickshell.execDetached(["nautilus"])
                                    else if (modelData === "Overview") Quickshell.execDetached(["niri", "msg", "action", "open-overview"])
                                }
                            }
                        }
                    }
                }
                Text { text: "Esc or Super H to return to your work"; color: "#788d84"; font.pixelSize: 12 }
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
                    MouseArea { anchors.fill: parent; onClicked: { root.deskOpen = false; Quickshell.execDetached(["fuzzel"]) } } }
                Text { text: Qt.formatTime(clock.date, "hh:mm"); color: "#ecf2f0"; font.pixelSize: 14; anchors.verticalCenter: parent.verticalCenter }
            }
        }
        SystemClock { id: clock; precision: SystemClock.Minutes }
    }
}
