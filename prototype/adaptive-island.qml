// A visual prototype of a device-adaptive Island, for screenshots and two measurements only: not wired to Flow's
// state. Each kind of device decides how the Island appears:
//   plain  (A) a screen with a keyboard and pointer and no cutout: nothing at rest. A 1 px catcher along the top edge
//              reveals the Island when the pointer is pushed against the edge (or pressed there); the shortcut does
//              too. A clock pill is a setting, off by default.
//   notch  (B) a notched MacBook in Asahi's full-panel mode (apple_dcp.show_notch=1): the band beside the camera is
//              the shell's, kept from apps by an exclusive zone, so apps get the rectangle Asahi gives by default. The
//              Island sits beside the cutout and grows below it.
// On every device the work indicator shows only while moving between spaces, and no home line (no touch device).
// Run as a development shell (its own state and config), with the installed shell stopped:
//   quickshell -p prototype/adaptive-island.qml
// and drive it with:  quickshell ipc -p prototype/adaptive-island.qml call proto <function> [args]
import QtQuick
import QtQuick.Shapes
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

ShellRoot {
    id: root
    property string device: "plain"     // plain | notch
    property string state: "rest"       // rest | expanded | chip | notify | moving
    property string revealedBy: ""      // edge | press | pill | shortcut | view
    property bool clockPill: false      // the plain device's optional clock pill (a setting, off by default)
    property int dwellMs: 150           // how long the pointer rests against the top edge before the Island shows
    property int stripHeight: 1         // the plain device's edge catcher, in logical pixels
    // The notch band, in logical pixels, set per size by prototype/adaptive_shots.py from the panel's geometry.
    property int bandHeight: 32
    property int notchWidth: 160
    property int notchRadius: 10
    // When the pointer reached the edge catcher, and when the Island was told to show (Date.now(), ms).
    property double enteredAt: 0
    property double shownAt: 0
    readonly property bool plain: device === "plain"
    readonly property bool open: state === "expanded"
    FloeTheme { id: floeTheme; light: false }
    SystemClock { id: clock; precision: SystemClock.Minutes }

    function reveal(by: string): void {
        root.revealedBy = by
        root.shownAt = Date.now()
        root.state = "expanded"
    }

    IpcHandler {
        target: "proto"
        function view(name: string): void { root.revealedBy = "view"; root.state = name; if (name === "notify") notifyTimer.restart() }
        function device(name: string): void { root.device = name; root.state = "rest" }
        function clock_pill(on: bool): void { root.clockPill = on }
        function dwell(ms: int): void { root.dwellMs = ms }
        function strip(px: int): void { root.stripHeight = px }
        function geometry(band: int, width: int, radius: int): void {
            root.bandHeight = band; root.notchWidth = width; root.notchRadius = radius
        }
        // The shortcut's path: Niri would spawn this same call from a key binding.
        function reveal(): void { root.reveal("shortcut") }
        function pill_size(): string { return JSON.stringify([pill.implicitWidth, pill.implicitHeight, 4]) }
        function state(): string {
            return JSON.stringify({ device: root.device, state: root.state, revealed_by: root.revealedBy,
                                    entered_at: root.enteredAt, shown_at: root.shownAt, dwell_ms: root.dwellMs,
                                    strip: root.stripHeight, clock: root.clockPill, band: root.bandHeight })
        }
    }

    // A notification leaves on its own after a moment, with no input. It takes no clicks over content: to act on it,
    // reveal the Island.
    Timer { id: notifyTimer; interval: 5000; onTriggered: if (root.state === "notify") root.state = "rest" }

    // The Island's own icons (shell.qml's FlowIcon and paths), drawn the same way.
    readonly property var iconPaths: ({
        "bell": "M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 2h-15z M10 21h4",
        "sliders": "M4 8h8 M18 8h2 M4 16h2 M12 16h8 M13 8a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0 -5 0 M6 16a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0 -5 0",
        "left": "M14.5 6l-6 6l6 6", "right": "M9.5 6l6 6l-6 6",
        "wifi": "M4 10a12 12 0 0 1 16 0 M7 13.5a7.5 7.5 0 0 1 10 0 M10 17a3 3 0 0 1 4 0",
        "battery": "M3 8h15v8h-15z M20 10.5v3"
    })
    component Glyph: Item {
        id: glyph
        property string name
        property color color: floeTheme.textMuted
        property int size: 20
        implicitWidth: size + 24; implicitHeight: 44
        Shape {
            anchors.centerIn: parent; width: glyph.size; height: glyph.size
            preferredRendererType: Shape.GeometryRenderer
            layer.enabled: true; layer.samples: 4
            ShapePath {
                scale: Qt.size(glyph.size / 24, glyph.size / 24)
                strokeColor: glyph.color; strokeWidth: 1.6; fillColor: "transparent"
                capStyle: ShapePath.RoundCap; joinStyle: ShapePath.RoundJoin
                PathSvg { path: root.iconPaths[glyph.name] || "" }
            }
        }
    }
    component Cell: Item {
        property string text
        property bool strong: false
        property bool muted: false
        property int pixelSize: strong ? 16 : 14
        implicitWidth: label.implicitWidth + 28; implicitHeight: 44
        Text {
            id: label
            anchors.centerIn: parent
            text: parent.text; textFormat: Text.PlainText
            color: parent.muted ? floeTheme.textMuted : floeTheme.textPrimary
            font.pixelSize: parent.pixelSize; font.weight: parent.strong ? Font.DemiBold : Font.Medium
            font.family: floeTheme.interfaceFont; font.features: { "tnum": 1 }
        }
    }
    component Rule: Rectangle { width: 1; height: 20; anchors.verticalCenter: parent ? parent.verticalCenter : undefined; color: Qt.alpha(floeTheme.border, 0.6) }
    // Today's Island controls, in one row (two when narrow: the space position goes below).
    component Controls: Column {
        id: controls
        property bool narrow: false
        property bool appButton: true
        Row {
            anchors.horizontalCenter: parent.horizontalCenter
            Cell { text: "floe."; strong: true }
            Cell { text: "Apps"; muted: true }
            Cell { text: Qt.formatTime(clock.date, "hh:mm") }
            Glyph { name: "bell" }
            Glyph { name: "sliders" }
            Rule { visible: controls.appButton && !controls.narrow }
            Cell { visible: controls.appButton && !controls.narrow; text: "Kate  ▾" }
            Rule { visible: !controls.narrow }
            Glyph { visible: !controls.narrow; name: "left" }
            Cell { visible: !controls.narrow; text: "03 / 04" }
            Glyph { visible: !controls.narrow; name: "right" }
        }
        Row {
            visible: controls.narrow
            anchors.horizontalCenter: parent.horizontalCenter
            Cell { visible: controls.appButton; text: "Kate  ▾" }
            Glyph { name: "left" }
            Cell { text: "03 / 04" }
            Glyph { name: "right" }
        }
    }
    component Notification: Item {
        implicitWidth: 380; implicitHeight: 58
        Column {
            anchors { left: parent.left; leftMargin: 16; verticalCenter: parent.verticalCenter }
            spacing: 3
            Text { text: "Mail · Ada Lovelace"; color: floeTheme.textMuted; font.pixelSize: 12; font.family: floeTheme.interfaceFont }
            Text { text: "The notes for Thursday are in the shared folder"; color: floeTheme.textPrimary; font.pixelSize: 14; font.family: floeTheme.interfaceFont }
        }
    }

    // ---- A: plain screen ----------------------------------------------------------------------------------------

    // The edge catcher: a 1 px input region along the top edge, drawn as nothing. The pointer pushed against the edge
    // rests in it; after the dwell the Island shows. A press in it shows the Island at once.
    PanelWindow {
        id: catcher
        visible: root.plain
        anchors { top: true; left: true; right: true }
        implicitHeight: root.stripHeight
        exclusionMode: ExclusionMode.Ignore
        color: "transparent"
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.namespace: "flow-proto-edge"
        MouseArea {
            id: catcherArea
            anchors.fill: parent
            hoverEnabled: true
            onContainsMouseChanged: if (containsMouse) { root.enteredAt = Date.now(); dwell.restart() } else dwell.stop()
            onPressed: if (!root.open) root.reveal("press")
        }
        Timer { id: dwell; interval: Math.max(1, root.dwellMs); onTriggered: if (catcherArea.containsMouse && !root.open) root.reveal("edge") }
    }

    // The Island revealed on a plain screen: a floating card, round on every corner (no fake notch), hanging 6 px
    // below the edge. Its surface starts at the edge, so the way down from the catcher stays inside it. It goes when
    // the pointer has been in neither the catcher nor the card for 500 ms.
    PanelWindow {
        id: plainIsland
        visible: root.plain && root.open
        anchors.top: true
        exclusionMode: ExclusionMode.Ignore
        implicitWidth: Math.min(plainCard.implicitWidth, (screen ? screen.width : 1280) - 16)
        implicitHeight: plainCard.implicitHeight + 6
        color: "transparent"
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.namespace: "flow-proto-island"
        MouseArea { id: plainIslandArea; anchors.fill: parent; hoverEnabled: true }
        Timer {
            interval: 100; repeat: true; running: plainIsland.visible && root.revealedBy !== "view" && root.revealedBy !== "shortcut"
            property int away: 0
            onRunningChanged: away = 0
            onTriggered: {
                away = catcherArea.containsMouse || plainIslandArea.containsMouse ? 0 : away + 1
                if (away >= 5) root.state = "rest"
            }
        }
        Rectangle {
            id: plainCard
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
            height: implicitHeight
            implicitWidth: plainControls.implicitWidth + 16; implicitHeight: plainControls.implicitHeight
            radius: 22
            color: floeTheme.canvas
            border.width: 1; border.color: Qt.alpha(floeTheme.border, 0.55)
            Controls { id: plainControls; anchors.centerIn: parent; narrow: !!plainIsland.screen && plainIsland.screen.width < 900 }
        }
    }

    // A notification on a plain screen: a card near the top that takes no input and leaves on its own.
    PanelWindow {
        visible: root.plain && root.state === "notify"
        anchors.top: true
        margins.top: 8
        exclusionMode: ExclusionMode.Ignore
        implicitWidth: Math.min(plainNote.implicitWidth, (screen ? screen.width : 1280) - 32); implicitHeight: plainNote.implicitHeight
        color: "transparent"
        WlrLayershell.layer: WlrLayer.Overlay
        mask: Region {}
        Rectangle {
            anchors.fill: parent; radius: 18
            color: floeTheme.canvas; border.width: 1; border.color: Qt.alpha(floeTheme.border, 0.55)
            Notification { id: plainNote; anchors.fill: parent }
        }
    }

    // The optional clock pill (a setting, off by default): small, 4 px below the edge, taking clicks only on itself
    // (its surface is its own size). A click reveals the Island.
    PanelWindow {
        id: pill
        visible: root.plain && root.clockPill && !root.open && root.state !== "notify"
        anchors.top: true
        margins.top: 4
        exclusionMode: ExclusionMode.Ignore
        implicitWidth: pillText.implicitWidth + 20; implicitHeight: 22
        color: "transparent"
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.namespace: "flow-proto-pill"
        Rectangle {
            anchors.fill: parent; radius: height / 2
            color: Qt.alpha(floeTheme.canvas, 0.9); border.width: 1; border.color: Qt.alpha(floeTheme.border, 0.5)
            Text {
                id: pillText
                anchors.centerIn: parent
                text: Qt.formatTime(clock.date, "hh:mm"); color: floeTheme.textPrimary
                font.pixelSize: 12; font.weight: Font.Medium; font.family: floeTheme.interfaceFont; font.features: { "tnum": 1 }
            }
        }
        MouseArea { anchors.fill: parent; onClicked: root.reveal("pill") }
    }

    // ---- B: notched MacBook -------------------------------------------------------------------------------------

    // The band beside the camera, black as the cutout. An exclusive zone keeps tiled windows below it, so apps get
    // the rectangle Asahi gives by default and lose nothing. The Island sits either side of the cutout: the App button
    // at the far left (§8.1), the space position and an activity left of the camera, status right of it.
    readonly property color bandText: "#E8ECEA"
    readonly property color bandMuted: "#9AA4A6"
    PanelWindow {
        id: band
        visible: !root.plain
        anchors { top: true; left: true; right: true }
        implicitHeight: root.bandHeight
        exclusiveZone: root.bandHeight
        color: "#000000"
        WlrLayershell.layer: WlrLayer.Top
        WlrLayershell.namespace: "flow-proto-band"
        Cutout { anchors.horizontalCenter: parent.horizontalCenter }
        readonly property int glyph: Math.round(root.bandHeight * 0.5)
        Row {
            // The App button (the focused app's menu), at the left of the band.
            anchors { left: parent.left; leftMargin: 10; verticalCenter: parent.verticalCenter }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: "Kate  ▾"; color: root.bandText
                font.pixelSize: Math.round(root.bandHeight * 0.42); font.weight: Font.DemiBold; font.family: floeTheme.interfaceFont
            }
        }
        Row {
            // Left of the camera: an activity when one runs, then the space position.
            anchors { right: parent.horizontalCenter; rightMargin: root.notchWidth / 2 + 12; verticalCenter: parent.verticalCenter }
            spacing: 14
            Row {
                visible: root.state === "chip"
                anchors.verticalCenter: parent.verticalCenter
                spacing: 6
                Rectangle { width: 7; height: 7; radius: 3.5; color: floeTheme.success; anchors.verticalCenter: parent.verticalCenter }
                Text {
                    text: "Build · flow  62%"; color: root.bandText
                    font.pixelSize: Math.round(root.bandHeight * 0.38); font.family: floeTheme.interfaceFont; font.features: { "tnum": 1 }
                }
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: "‹ 03 / 04 ›"; color: root.bandMuted
                font.pixelSize: Math.round(root.bandHeight * 0.38); font.family: floeTheme.interfaceFont; font.features: { "tnum": 1 }
            }
        }
        Row {
            // Right of the camera: the clock, notifications (a dot when something waits), quick settings, status.
            anchors { left: parent.horizontalCenter; leftMargin: root.notchWidth / 2 + 12; verticalCenter: parent.verticalCenter }
            spacing: 2
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: Qt.formatTime(clock.date, "hh:mm"); color: root.bandText
                font.pixelSize: Math.round(root.bandHeight * 0.42); font.weight: Font.Medium; font.family: floeTheme.interfaceFont; font.features: { "tnum": 1 }
                rightPadding: 6
            }
            Item {
                width: bell.implicitWidth; height: root.bandHeight
                Glyph { id: bell; name: "bell"; size: band.glyph; color: root.bandMuted; anchors.centerIn: parent; implicitHeight: root.bandHeight; implicitWidth: size + 14 }
                Rectangle { visible: root.state === "notify"; width: 6; height: 6; radius: 3; color: floeTheme.accent; x: parent.width - 12; y: root.bandHeight * 0.22 }
            }
            Glyph { name: "sliders"; size: band.glyph; color: root.bandMuted; implicitHeight: root.bandHeight; implicitWidth: size + 14 }
        }
        Row {
            anchors { right: parent.right; rightMargin: 12; verticalCenter: parent.verticalCenter }
            Glyph { name: "wifi"; size: band.glyph; color: root.bandMuted; implicitHeight: root.bandHeight; implicitWidth: size + 10 }
            Glyph { name: "battery"; size: band.glyph; color: root.bandMuted; implicitHeight: root.bandHeight; implicitWidth: size + 10 }
        }
    }

    // The simulated camera cutout: in the guest there is no hole, so it is drawn black with a faint outline and a
    // lens, to show where the hardware's would be. Square at the top (it is the panel's edge), rounded below.
    component Cutout: Item {
        width: root.notchWidth; height: root.bandHeight
        Rectangle {
            anchors.fill: parent
            color: "#000000"
            topLeftRadius: 0; topRightRadius: 0; bottomLeftRadius: root.notchRadius; bottomRightRadius: root.notchRadius
            border.width: 1; border.color: "#2A2F33"
        }
        Rectangle {
            anchors.centerIn: parent
            width: Math.max(6, Math.round(root.bandHeight * 0.22)); height: width; radius: width / 2
            color: "#0E1418"; border.width: 1; border.color: "#1F2A30"
        }
    }

    // The Island expanded below the camera: black like the band and joined to it, so the cutout looks as though it
    // grew. Today's controls; the App button stays in the band. A notification grows the same way, takes no input
    // over content and leaves on its own.
    PanelWindow {
        id: notchIsland
        visible: !root.plain && (root.open || root.state === "notify")
        anchors.top: true
        margins.top: root.bandHeight
        exclusionMode: ExclusionMode.Ignore
        implicitWidth: Math.min(Math.max(notchColumn.implicitWidth + 24, root.notchWidth + 80), (screen ? screen.width : 1280) - 16)
        implicitHeight: notchColumn.implicitHeight + 8
        color: "transparent"
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.namespace: "flow-proto-island"
        mask: root.state === "notify" ? emptyRegion : null
        Region { id: emptyRegion }
        Rectangle {
            anchors.fill: parent
            color: "#000000"
            topLeftRadius: 0; topRightRadius: 0; bottomLeftRadius: 22; bottomRightRadius: 22
            Column {
                id: notchColumn
                anchors.horizontalCenter: parent.horizontalCenter
                Controls {
                    visible: root.open
                    anchors.horizontalCenter: parent.horizontalCenter
                    appButton: false
                    narrow: !!notchIsland.screen && notchIsland.screen.width < 900
                }
                Notification { visible: root.state === "notify"; anchors.horizontalCenter: parent.horizontalCenter }
            }
        }
    }

    // ---- Every device ------------------------------------------------------------------------------------------

    // The work indicator, only while moving between spaces: page dots near the bottom, taking no input, then gone.
    PanelWindow {
        visible: root.state === "moving"
        anchors.bottom: true
        margins.bottom: 14
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
