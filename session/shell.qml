import QtQuick
import Quickshell
import Quickshell.Wayland

ShellRoot {
    PanelWindow {
        anchors.top: true
        implicitWidth: 230
        implicitHeight: 44
        margins.top: 8
        color: "transparent"
        exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay

        Rectangle {
            anchors.fill: parent
            radius: 22
            color: "#0b1015"
            border.color: "#33433e"
            Row {
                anchors.centerIn: parent
                spacing: 18
                Text {
                    text: "FLOW"
                    color: "#b8ddcb"
                    font.bold: true
                    font.letterSpacing: 1.5
                    anchors.verticalCenter: parent.verticalCenter
                }
                Text {
                    text: "Apps"
                    color: "#ecf2f0"
                    font.pixelSize: 14
                    anchors.verticalCenter: parent.verticalCenter
                    MouseArea {
                        anchors.fill: parent
                        onClicked: Quickshell.execDetached(["fuzzel"])
                    }
                }
                Text {
                    text: Qt.formatTime(clock.date, "hh:mm")
                    color: "#ecf2f0"
                    font.pixelSize: 14
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
        }
        SystemClock { id: clock; precision: SystemClock.Minutes }
    }
}
