// A visual prototype of the thin-margin model, for screenshots only: not wired to Flow's state. One rule: anything
// that stays on screen lives in the margins; anything over content is temporary, summoned or gone after a moment.
// Run as a development shell (its own state and config), with the installed shell stopped:
//   quickshell -p prototype/thin-margin.qml
// and switch what it shows with:  quickshell ipc -p prototype/thin-margin.qml call proto view <state>
// States: rest, expanded, chip, notify, moving. `quickshell ipc ... call proto homeline true|false` shows or hides the
// home line (a gesture zone, only on outputs with a touch device). Margins come from layer-shell exclusive zones.
import QtQuick
import QtQuick.Shapes
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

ShellRoot {
    id: root
    property string state: "rest"
    property bool touch: true
    readonly property int topMargin: 10
    readonly property int bottomMargin: 6
    FloeTheme { id: floeTheme; light: false }
    SystemClock { id: clock; precision: SystemClock.Minutes }

    IpcHandler {
        target: "proto"
        function view(name: string): void { root.state = name }
        function homeline(on: bool): void { root.touch = on }
        function margins(): string { return JSON.stringify([root.topMargin, root.bottomMargin]) }
    }

    // The safe area: a band at the top and one at the bottom that tiled windows keep out of.
    PanelWindow {
        id: topBand
        anchors { top: true; left: true; right: true }
        implicitHeight: root.topMargin
        exclusiveZone: root.topMargin
        color: floeTheme.canvas
        WlrLayershell.layer: WlrLayer.Top
        WlrLayershell.namespace: "flow-proto-margin"
        // The pill at rest: a short tab joined to the edge, square at the top and rounded below, as the Island is.
        // Pushing the pointer to the top edge, or clicking it, expands it.
        Rectangle {
            visible: root.state !== "expanded" && root.state !== "notify"
            anchors { horizontalCenter: parent.horizontalCenter; top: parent.top }
            width: 132; height: root.topMargin
            color: floeTheme.surfaceRaised
            topLeftRadius: 0; topRightRadius: 0; bottomLeftRadius: 5; bottomRightRadius: 5
            border.width: 1; border.color: Qt.alpha(floeTheme.border, 0.6)
            Rectangle {
                // Something waiting: the bell's dot, at rest.
                anchors { right: parent.right; rightMargin: 12; verticalCenter: parent.verticalCenter }
                width: 4; height: 4; radius: 2; color: floeTheme.accent
            }
        }
    }
    PanelWindow {
        id: bottomBand
        anchors { bottom: true; left: true; right: true }
        implicitHeight: root.bottomMargin
        exclusiveZone: root.bottomMargin
        color: floeTheme.canvas
        WlrLayershell.layer: WlrLayer.Top
        WlrLayershell.namespace: "flow-proto-margin"
        // The home line: a gesture zone (swipe up, or push to the edge and click), only with a touch device.
        Rectangle {
            visible: root.touch
            anchors.centerIn: parent
            width: 72; height: 4; radius: 2
            color: floeTheme.textSecondary
        }
    }

    // The Island's own icons (shell.qml's FlowIcon and paths), drawn the same way.
    readonly property var iconPaths: ({
        "bell": "M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 2h-15z M10 21h4",
        "sliders": "M4 8h8 M18 8h2 M4 16h2 M12 16h8 M13 8a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0 -5 0 M6 16a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0 -5 0",
        "left": "M14.5 6l-6 6l6 6", "right": "M9.5 6l6 6l-6 6"
    })
    component Glyph: Item {
        id: glyph
        property string name
        property color color: floeTheme.textMuted
        implicitWidth: 44; implicitHeight: 44
        Shape {
            anchors.centerIn: parent; width: 20; height: 20
            preferredRendererType: Shape.GeometryRenderer
            layer.enabled: true; layer.samples: 4
            ShapePath {
                scale: Qt.size(20 / 24, 20 / 24)
                strokeColor: glyph.color; strokeWidth: 1.6; fillColor: "transparent"
                capStyle: ShapePath.RoundCap; joinStyle: ShapePath.RoundJoin
                PathSvg { path: root.iconPaths[glyph.name] || "" }
            }
        }
    }
    // A capsule button in the Island's style, drawn only.
    component Cell: Item {
        property string text
        property bool strong: false
        property bool muted: false
        implicitWidth: label.implicitWidth + 28; implicitHeight: 44
        Text {
            id: label
            anchors.centerIn: parent
            text: parent.text; textFormat: Text.PlainText
            color: parent.muted ? floeTheme.textMuted : floeTheme.textPrimary
            font.pixelSize: parent.strong ? 16 : 14; font.weight: parent.strong ? Font.DemiBold : Font.Medium
            font.family: floeTheme.interfaceFont; font.features: { "tnum": 1 }
        }
    }
    component Rule: Rectangle { width: 1; height: 20; anchors.verticalCenter: parent ? parent.verticalCenter : undefined; color: Qt.alpha(floeTheme.border, 0.6) }

    // The Island expanded, summoned (edge push, click on the pill, edge swipe, Super+B): today's controls, the App
    // button and the space position, hanging from the top edge over content until the pointer leaves or Escape.
    PanelWindow {
        id: expanded
        visible: root.state === "expanded" || root.state === "notify"
        anchors.top: true
        exclusionMode: ExclusionMode.Ignore
        implicitWidth: Math.min(card.implicitWidth, (screen ? screen.width : 1280) - 16)
        implicitHeight: card.implicitHeight
        color: "transparent"
        WlrLayershell.layer: WlrLayer.Overlay
        Rectangle {
            id: card
            anchors.fill: parent
            implicitWidth: column.implicitWidth + 16; implicitHeight: column.implicitHeight
            color: floeTheme.canvas
            topLeftRadius: 0; topRightRadius: 0; bottomLeftRadius: 16; bottomRightRadius: 16
            border.width: 1; border.color: Qt.alpha(floeTheme.border, 0.55)
            Column {
                id: column
                anchors.horizontalCenter: parent.horizontalCenter
                readonly property bool narrow: !!expanded.screen && expanded.screen.width < 900
                Row {
                    anchors.horizontalCenter: parent.horizontalCenter
                    Cell { text: "floe."; strong: true }
                    Cell { text: "Apps"; muted: true }
                    Cell { text: Qt.formatTime(clock.date, "hh:mm") }
                    Glyph { name: "bell" }
                    Glyph { name: "sliders" }
                    Rule {}
                    Cell { visible: !column.narrow; text: "Kate  ▾" }
                    Rule { visible: !column.narrow }
                    Glyph { name: "left" }
                    Cell { text: "03 / 04" }
                    Glyph { name: "right" }
                }
                Row {
                    // On a narrow screen the App button takes a second row.
                    visible: column.narrow
                    anchors.horizontalCenter: parent.horizontalCenter
                    Cell { text: "Kate  ▾" }
                }
                Item {
                    // A notification expands the Island for a moment, then it collapses again.
                    visible: root.state === "notify"
                    width: Math.min(420, (expanded.screen ? expanded.screen.width : 1280) - 48); height: 56
                    anchors.horizontalCenter: parent.horizontalCenter
                    Rectangle { anchors { left: parent.left; right: parent.right; top: parent.top } height: 1; color: Qt.alpha(floeTheme.border, 0.4) }
                    Column {
                        anchors { left: parent.left; leftMargin: 8; verticalCenter: parent.verticalCenter }
                        spacing: 2
                        Text { text: "Mail · Ada Lovelace"; color: floeTheme.textMuted; font.pixelSize: 12; font.family: floeTheme.interfaceFont }
                        Text { text: "The notes for Thursday are in the shared folder"; color: floeTheme.textPrimary; font.pixelSize: 14; font.family: floeTheme.interfaceFont }
                    }
                }
            }
        }
    }

    // The compact activity: while something runs (a build, a timer, music), a chip just below the pill, over
    // content. It takes no input, so it never covers a click; to act on it, summon the Island.
    PanelWindow {
        visible: root.state === "chip"
        anchors.top: true
        margins.top: root.topMargin + 4
        exclusionMode: ExclusionMode.Ignore
        implicitWidth: chipRow.implicitWidth + 24; implicitHeight: 26
        color: "transparent"
        WlrLayershell.layer: WlrLayer.Overlay
        mask: Region {}
        Rectangle {
            anchors.fill: parent; radius: height / 2
            color: Qt.alpha(floeTheme.canvas, 0.92); border.width: 1; border.color: Qt.alpha(floeTheme.border, 0.55)
            Row {
                id: chipRow
                anchors.centerIn: parent; spacing: 8
                Rectangle { width: 7; height: 7; radius: 3.5; color: floeTheme.accent; anchors.verticalCenter: parent.verticalCenter }
                Text { text: "Build · flow  62%"; color: floeTheme.textPrimary; font.pixelSize: 12; font.family: floeTheme.interfaceFont; font.features: { "tnum": 1 } }
            }
        }
    }

    // The work indicator, only while moving between spaces: page dots above the bottom margin, then gone.
    PanelWindow {
        visible: root.state === "moving"
        anchors.bottom: true
        margins.bottom: root.bottomMargin + 10
        exclusionMode: ExclusionMode.Ignore
        implicitWidth: dots.implicitWidth + 28; implicitHeight: 22
        color: "transparent"
        WlrLayershell.layer: WlrLayer.Overlay
        mask: Region {}
        Rectangle {
            anchors.fill: parent; radius: height / 2
            color: Qt.alpha(floeTheme.canvas, 0.85)
            Row {
                id: dots
                anchors.centerIn: parent; spacing: 8
                Repeater {
                    model: 4
                    Rectangle {
                        required property int index
                        width: index === 2 ? 18 : 7; height: 7; radius: 3.5
                        color: index === 2 ? floeTheme.textPrimary : Qt.alpha(floeTheme.textPrimary, 0.4)
                    }
                }
            }
        }
    }
}
