import QtQuick
import QtQuick.Controls
import QtQuick.Shapes
import QtCore as Core
import Quickshell
import Quickshell.Io
import Quickshell.Services.Pipewire
import Quickshell.Services.Mpris
import Quickshell.Services.Notifications
import Quickshell.Wayland

ShellRoot {
    id: root
    property bool deskOpen: false
    property bool settingsOpen: false
    property bool editPins: false
    property bool quickSettingsOpen: false
    property bool notificationsOpen: false
    readonly property var audioSink: Pipewire.ready ? Pipewire.defaultAudioSink : null
    readonly property bool audioAvailable: !!(audioSink && audioSink.audio)
    readonly property string volumeStatus: !Pipewire.ready ? "Checking audio…" : !audioAvailable ? "Audio unavailable" :
        Math.round(audioSink.audio.volume * 100) + "%" + (audioSink.audio.muted ? " · muted" : "")
    readonly property var controllablePlayers: Mpris.players.values.filter(player => player.canTogglePlaying)
    readonly property var playingPlayers: controllablePlayers.filter(player => player.isPlaying)
    // shortcut: hide media controls when players are ambiguous; add a player picker if this occurs in practice.
    readonly property var mediaPlayer: playingPlayers.length === 1 ? playingPlayers[0] :
        playingPlayers.length === 0 && controllablePlayers.length === 1 ? controllablePlayers[0] : null
    property string networkStatus: "Checking network…"
    property string quickError: ""
    function trimTrackedNotifications() {
        const tracked = [...notificationServer.trackedNotifications.values]
        for (let i = 0; i < tracked.length - 20; i++) tracked[i].dismiss()
    }
    PwObjectTracker { objects: root.audioSink ? [root.audioSink] : [] }
    NotificationServer {
        id: notificationServer
        actionsSupported: true
        bodyMarkupSupported: false
        keepOnReload: false
        onNotification: notification => {
            notification.tracked = true
            // Quickshell adds the notification to its model after this handler returns.
            Qt.callLater(root.trimTrackedNotifications)
        }
    }
    Process {
        id: networkStatusProcess
        command: ["nmcli", "-t", "-f", "CONNECTIVITY", "general"]
        stdout: StdioCollector { onStreamFinished: {
            const state = this.text.trim()
            root.networkStatus = ({full: "Connected", limited: "Limited", portal: "Sign-in required", none: "Offline", unknown: "Unknown"})[state] || "Network unavailable"
        } }
        onExited: (code, status) => { if (code !== 0) root.networkStatus = "Network unavailable" }
    }
    Timer { interval: 4000; running: root.quickSettingsOpen; repeat: true; triggeredOnStart: true; onTriggered: if (!networkStatusProcess.running) networkStatusProcess.running = true }
    function toggleNotifications() {
        if (!notificationsOpen) {
            quickSettingsOpen = false; appMenuOpen = false; openMenuWhenReady = false
            switcherOpen = false
            if (overviewOpen) Quickshell.execDetached(["niri", "msg", "action", "close-overview"])
        }
        notificationsOpen = !notificationsOpen
    }
    function toggleQuickSettings() {
        notificationsOpen = false
        if (!quickSettingsOpen) {
            appMenuOpen = false
            openMenuWhenReady = false
            switcherOpen = false
            quickError = ""
        }
        quickSettingsOpen = !quickSettingsOpen
    }
    function changeVolume(value) {
        if (!audioAvailable) { quickError = "Audio control unavailable"; return }
        quickError = ""
        if (value === "mute") audioSink.audio.muted = !audioSink.audio.muted
        else audioSink.audio.volume = Math.max(0, Math.min(1, audioSink.audio.volume + value))
    }
    property bool appMenuOpen: false
    property bool appMenuFocusChanged: false
    onAppMenuOpenChanged: {
        if (!appMenuOpen && appMenuFocusChanged) {
            appMenu = null
            appMenuFocusChanged = false
        }
    }
    property bool overviewOpen: false
    property var appMenu: null
    property string appMenuError: ""
    property bool openMenuWhenReady: false
    function requestAppMenu() {
        if (!menuPreferences.appMenuEnabled || root.deskOpen || root.overviewOpen || appMenuProcess.running) return
        root.switcherOpen = false
        root.quickSettingsOpen = false
        root.openMenuWhenReady = true
        appMenuProcess.command = ["flow-appmenu"]
        appMenuProcess.running = true
    }
    Core.Settings {
        id: menuPreferences
        location: "file://" + (Quickshell.env("XDG_CONFIG_HOME") || Quickshell.env("HOME") + "/.config") + "/flow-settings.ini"
        property bool appMenuEnabled: false
        property bool autoRecovery: false
    }
    property var appMenuPath: []
    function currentAppItems() {
        let items = root.appMenu ? root.appMenu.menus : []
        for (const index of root.appMenuPath) items = (items[index] && items[index].children) || []
        return items
    }
    Process {
        id: appMenuProcess
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    const result = JSON.parse(this.text)
                    if (result && result.error) {
                        if (root.appMenuOpen) root.appMenuFocusChanged = true
                        else root.appMenu = null
                        root.openMenuWhenReady = false; root.appMenuError = result.error; return
                    }
                    if (appMenuProcess.command.length === 1 && (!root.openMenuWhenReady || !root.appMenu || !result || root.appMenu.id !== result.id)) {
                        root.openMenuWhenReady = false
                        return
                    }
                    root.appMenu = result
                    root.appMenuError = ""
                    if (root.openMenuWhenReady && result) { root.appMenuPath = []; root.appMenuOpen = true }
                    root.openMenuWhenReady = false
                    if (appMenuProcess.command[1] === "activate") root.appMenuOpen = false
                } catch (e) { root.appMenuError = "App menu unavailable" }
            }
        }
    }
    Timer {
        interval: 1500; running: menuPreferences.appMenuEnabled; repeat: true; triggeredOnStart: true
        onTriggered: if (!appMenuProcess.running && !root.appMenu && !root.appMenuOpen && !root.deskOpen && !root.overviewOpen) {
            appMenuProcess.command = ["flow-appmenu", "identity"]
            appMenuProcess.running = true
        }
    }
    Process {
        id: niriEvents
        // The kernel stops the stream when the shell dies, even by SIGKILL, so restarts never leak one.
        // Without setpriv the stream still runs, only without that guarantee.
        command: ["sh", "-c", "command -v setpriv >/dev/null && exec setpriv --pdeathsig TERM niri msg -j event-stream; exec niri msg -j event-stream"]
        running: true
        onExited: eventReconnect.restart()
        stdout: SplitParser {
            onRead: data => {
                try {
                    const event = JSON.parse(data)
                    if (event.WorkspacesChanged || event.WorkspaceActivated || event.WorkspaceActiveWindowChanged ||
                        event.WindowsChanged || event.WindowOpenedOrChanged || event.WindowClosed ||
                        event.WindowFocusChanged || event.WindowLayoutsChanged) refreshAfterEvent.restart()
                    if (event.WindowFocusChanged && event.WindowFocusChanged.id !== null && root.appMenu && root.appMenu.id !== event.WindowFocusChanged.id) {
                        if (root.appMenuOpen) root.appMenuFocusChanged = true
                        else root.appMenu = null
                        root.openMenuWhenReady = false
                    }
                    if (event.WorkspaceActiveWindowChanged && root.appMenu &&
                            event.WorkspaceActiveWindowChanged.workspace_id === root.appMenu.workspace_id &&
                            event.WorkspaceActiveWindowChanged.active_window_id !== null &&
                            event.WorkspaceActiveWindowChanged.active_window_id !== root.appMenu.id) {
                        if (root.appMenuOpen) root.appMenuFocusChanged = true
                        else root.appMenu = null
                        root.openMenuWhenReady = false
                    }
                    if (event.WindowClosed && root.appMenu && root.appMenu.id === event.WindowClosed.id) {
                        if (root.appMenuOpen) root.appMenuFocusChanged = true
                        else root.appMenu = null
                        root.openMenuWhenReady = false
                    }
                    if (event.OverviewOpenedOrClosed) {
                        root.overviewOpen = event.OverviewOpenedOrClosed.is_open
                        if (root.overviewOpen) { root.deskOpen = false; root.notificationsOpen = false; root.appMenuOpen = false; root.openMenuWhenReady = false; root.switcherOpen = false; root.quickSettingsOpen = false }
                    }
                } catch (e) {}
            }
        }
    }
    Timer { id: eventReconnect; interval: 5000; onTriggered: if (!niriEvents.running) niriEvents.running = true }
    property string recoveryMessage: ""
    property bool autoRecoveryBlocked: true
    property string renameGroupId: ""
    property bool switcherOpen: false
    onSwitcherOpenChanged: if (!switcherOpen) renameGroupId = ""
    property var windows: []
    property var displaySpaces: []
    // Activities published by apps through the Platform API (ADR-0010), newest first.
    property var activities: []
    function activityText(activity) {
        const progress = activity.progress >= 0 ? "  " + Math.round(activity.progress * 100) + "%" : "  …"
        return activity.owner + "  ·  " + activity.title + progress
    }
    FileView {
        id: activityFile
        // Without a runtime directory nothing is published; never fall back to a world-writable place.
        path: Quickshell.env("XDG_RUNTIME_DIR") ? Quickshell.env("XDG_RUNTIME_DIR") + "/flow-activities.json" : ""
        watchChanges: true
        onFileChanged: reload()
        onLoaded: {
            try {
                const data = JSON.parse(text())
                root.activities = data && data.version === 1 && Array.isArray(data.activities) ? data.activities : []
            } catch (e) { root.activities = [] }
        }
        onLoadFailed: root.activities = []
    }
    // Floating windows over this workspace's spaces; they are never spaces themselves.
    property var layers: []
    readonly property var activeLayer: layers.find(layer => layer.active) || null
    property var incompleteGroups: []
    property var pins: []
    // Pins owned by other, disconnected or unidentified displays; Super+number uses only `pins`.
    property var otherPins: []
    property string displayLabel: ""
    property int displayCount: 1
    // Connected displays in screen order: {key, label, name, number, current}; number 0 is Main.
    property var displays: []
    property string displayName: ""
    // Super+Alt, display digit, then space digit: the display digit waits here for the space digit.
    property int pendingDisplay: -1
    property string toast: ""
    function showToast(text) { toast = text; toastTimer.restart() }
    function displayDigit(digit) {
        if (pendingDisplay < 0) {
            const target = displays.find(d => d.number === digit)
            if (displays.length && !target) { showToast("External " + digit + " is not connected"); return }
            pendingDisplay = digit
            toastTimer.stop()
            toast = "External " + digit + (target ? " · " + target.label : "") + " — choose a pinned space"
            displayDigitTimer.restart()
            return
        }
        const display = pendingDisplay
        pendingDisplay = -1
        displayDigitTimer.stop()
        showToast("External " + display + " · space " + digit)
        spaceAction("focus-number", display + ":" + digit)
    }
    Timer {
        id: displayDigitTimer; interval: 1500
        onTriggered: if (root.pendingDisplay >= 0) { root.pendingDisplay = -1; root.showToast("Display shortcut cancelled") }
    }
    Timer { id: toastTimer; interval: 1800; onTriggered: root.toast = "" }
    // The active space as Work shows it: its place in this display's Flow and, for a Group, the focused app.
    readonly property int activeSpaceIndex: displaySpaces.findIndex(space => space.active)
    readonly property var activeSpace: activeSpaceIndex >= 0 ? displaySpaces[activeSpaceIndex] : null
    property string lastFocusedGroup: ""
    property var lastFocusedMember: null
    onActiveSpaceChanged: {
        const space = activeSpace
        const member = space && space.group_id ? space.members.find(m => m.active) : null
        if (member && space.group_id === lastFocusedGroup && lastFocusedMember !== null && member.id !== lastFocusedMember)
            showToast((member.app_name || member.label).slice(0, 40) + " is now focused" + (menuPreferences.appMenuEnabled ? " · menus updated" : ""))
        lastFocusedGroup = member ? space.group_id : ""
        lastFocusedMember = member ? member.id : null
    }
    property string spacesError: ""
    property var pendingSpaceActions: []
    function renameGroup(id, label) {
        if (recoveryProcess.running || spacesProcess.running || pendingSpaceActions.length) { spacesError = "Wait for the current action"; return }
        recoveryProcess.command = ["flow-recovery", "rename-group", id, label]
        recoveryProcess.running = true
    }
    function spaceAction(action, id) {
        if (recoveryProcess.running) { if (action !== "refresh") spacesError = "Wait for session recovery to finish"; return }
        if (spacesProcess.running || pendingSpaceActions.length) {
            if (action !== "refresh") {
                if (pendingSpaceActions.length >= 8) spacesError = "Too many pending actions"
                else pendingSpaceActions = pendingSpaceActions.concat([{action: action, id: id}])
            }
            return
        }
        spacesProcess.command = id ? ["flow-spaces", action, id] : ["flow-spaces", action]
        spacesProcess.running = true
    }
    function drainSpaceActions() {
        if (spacesProcess.running || recoveryProcess.running || !pendingSpaceActions.length) return
        const next = pendingSpaceActions[0]
        pendingSpaceActions = pendingSpaceActions.slice(1)
        spacesProcess.command = next.id ? ["flow-spaces", next.action, next.id] : ["flow-spaces", next.action]
        spacesProcess.running = true
    }
    Process {
        id: spacesProcess
        onRunningChanged: if (!running && root.pendingSpaceActions.length) Qt.callLater(root.drainSpaceActions)
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    const result = JSON.parse(this.text)
                    if (result.error) {
                        root.spacesError = result.error
                        if (spacesProcess.command[1] !== "refresh" && spacesProcess.command[1] !== "auto-layout" && !root.switcherOpen) root.deskOpen = true
                    } else {
                        if (JSON.stringify(root.windows) !== JSON.stringify(result.windows)) root.windows = result.windows
                        if (JSON.stringify(root.displaySpaces) !== JSON.stringify(result.spaces)) root.displaySpaces = result.spaces
                        if (JSON.stringify(root.layers) !== JSON.stringify(result.layers || [])) root.layers = result.layers || []
                        if (JSON.stringify(root.incompleteGroups) !== JSON.stringify(result.incomplete_groups || [])) root.incompleteGroups = result.incomplete_groups || []
                        if (JSON.stringify(root.pins) !== JSON.stringify(result.pins)) root.pins = result.pins
                        if (JSON.stringify(root.otherPins) !== JSON.stringify(result.other_pins || [])) root.otherPins = result.other_pins || []
                        root.displayLabel = result.display ? result.display.label : ""
                        root.displayName = result.display ? result.display.name || "" : ""
                        root.displayCount = result.display_count || 1
                        if (JSON.stringify(root.displays) !== JSON.stringify(result.displays || [])) root.displays = result.displays || []
                        if (result.action !== "refresh") root.spacesError = ""
                        if (result.notice) root.showToast(result.notice)
                        if (["focus", "focus-number", "focus-window", "move-to-display"].includes(result.action)) { root.deskOpen = false; root.switcherOpen = false }
                        else if (result.action === "pin") root.deskOpen = true
                    }
                } catch (e) { root.spacesError = "Could not read pinned spaces" }
            }
        }
    }
    Process {
        id: recoveryProcess
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    const result = JSON.parse(this.text)
                    root.recoveryMessage = result.error || result.message || "Recovery unavailable"
                    if (recoveryProcess.command[1] === "auto-start" || recoveryProcess.command[1] === "auto-save" || recoveryProcess.command[1] === "auto-ready") {
                        root.autoRecoveryBlocked = !!result.error
                        if (result.error) { root.deskOpen = true; root.settingsOpen = true; Qt.callLater(function() { deskScroller.ensureVisible(recoveryStatus) }) }
                    }
                    if (recoveryProcess.command[1] === "save" && !result.error && menuPreferences.autoRecovery) autoReadyTimer.restart()
                    if (recoveryProcess.command[1] === "rename-group") {
                        root.spacesError = result.error || ""
                        if (!result.error) root.renameGroupId = ""
                    }
                    if (!result.error) refreshAfterEvent.restart()
                } catch (e) { root.recoveryMessage = "Could not read recovery result" }
            }
        }
    }
    Timer {
        id: autoStartTimer; interval: 3000
        Component.onCompleted: if (menuPreferences.autoRecovery) start()
        onTriggered: {
            if (!menuPreferences.autoRecovery) return
            if (recoveryProcess.running || spacesProcess.running) { restart(); return }
            recoveryProcess.command = ["flow-recovery", "auto-start"]; recoveryProcess.running = true
        }
    }
    Timer {
        id: autoReadyTimer; interval: 200
        onTriggered: {
            if (!menuPreferences.autoRecovery) return
            if (recoveryProcess.running || spacesProcess.running) { restart(); return }
            recoveryProcess.command = ["flow-recovery", "auto-ready"]; recoveryProcess.running = true
        }
    }
    Timer {
        interval: 30000; running: menuPreferences.autoRecovery; repeat: true
        onTriggered: {
            if (root.autoRecoveryBlocked || recoveryProcess.running || spacesProcess.running || root.pendingSpaceActions.length) return
            recoveryProcess.command = ["flow-recovery", "auto-save"]; recoveryProcess.running = true
        }
    }
    Timer {
        id: refreshAfterEvent
        interval: 150
        onTriggered: {
            if (spacesProcess.running || recoveryProcess.running || root.pendingSpaceActions.length) restart()
            else root.spaceAction("refresh")
        }
    }
    Timer { interval: niriEvents.running ? 30000 : 3000; running: true; repeat: true; triggeredOnStart: true; onTriggered: root.spaceAction("refresh") }
    Timer {
        interval: 2000; repeat: true
        running: root.displaySpaces.some(space => space.auto_layout) || root.layers.some(layer => layer.shaped)
        onTriggered: if (!root.deskOpen && !root.switcherOpen && !root.overviewOpen && !root.launcherActive && !root.appMenuOpen && !root.quickSettingsOpen && !root.notificationsOpen && !spacesProcess.running && !recoveryProcess.running && !root.pendingSpaceActions.length) root.spaceAction("auto-layout")
    }
    function toggleDesk() {
        notificationsOpen = false
        quickSettingsOpen = false
        switcherOpen = false
        appMenuOpen = false
        openMenuWhenReady = false
        if (!deskOpen) Quickshell.execDetached(["niri", "msg", "action", "close-overview"])
        deskOpen = !deskOpen
        if (deskOpen) spaceAction("refresh")
    }

    property bool launcherActive: false
    Process { id: appsLauncher; command: ["flow-apps"]; onExited: launcherDismissDelay.restart() }
    Process { id: searchLauncher; command: ["flow-search"]; onExited: launcherDismissDelay.restart() }
    // shortcut: retain the open state for 500ms after exit to handle Island clicks; replace with lifecycle IPC if needed.
    Timer { id: launcherDismissDelay; interval: 500; onTriggered: if (!appsLauncher.running && !searchLauncher.running) root.launcherActive = false }
    function closeLauncher() {
        launcherDismissDelay.stop()
        launcherActive = false
        if (appsLauncher.running) appsLauncher.running = false
        if (searchLauncher.running) searchLauncher.running = false
    }
    function openSearch() {
        if (launcherActive || appsLauncher.running || searchLauncher.running) { closeLauncher(); return }
        notificationsOpen = false
        quickSettingsOpen = false
        openMenuWhenReady = false
        appMenuOpen = false
        deskOpen = false
        switcherOpen = false
        launcherActive = true
        searchLauncher.running = true
    }

    function launcherBusy() { return launcherActive || appsLauncher.running || searchLauncher.running || launcherDismissDelay.running }
    function otherSurfaceOpen() { return switcherOpen || quickSettingsOpen || notificationsOpen || appMenuOpen }
    function openApps() {
        if (launcherActive || appsLauncher.running || searchLauncher.running) { closeLauncher(); return }
        notificationsOpen = false
        quickSettingsOpen = false
        openMenuWhenReady = false
        appMenuOpen = false
        switcherOpen = false
        launcherActive = true
        appsLauncher.running = true
    }

    function toggleSwitcher() {
        notificationsOpen = false
        quickSettingsOpen = false
        if (switcherOpen) { switcherOpen = false; return }
        deskOpen = false
        appMenuOpen = false
        openMenuWhenReady = false
        if (overviewOpen) Quickshell.execDetached(["niri", "msg", "action", "close-overview"])
        switcherOpen = true
        spaceAction("refresh")
    }

    function showOverview(action = "open-overview") {
        closeLauncher()
        deskOpen = false
        switcherOpen = false
        Quickshell.execDetached(["niri", "msg", "action", action])
    }

    // Floe's appearance preference is per-user and does not change app/window state.
    FloeTheme { id: floeTheme; light: appearancePreferences.lightMode }
    Core.Settings {
        id: appearancePreferences
        location: "file://" + (Quickshell.env("XDG_CONFIG_HOME") || Quickshell.env("HOME") + "/.config") + "/floe-appearance.ini"
        property bool lightMode: false
        property bool reduceMotion: false
    }
    // Motion follows the brand's quick token (120 ms). Linux offers Quickshell no reduced-motion signal,
    // so it is a per-user preference in Desk settings; with it on, every change is instant.
    readonly property int motionQuick: appearancePreferences.reduceMotion ? 0 : 120
    readonly property int motionStandard: appearancePreferences.reduceMotion ? 0 : 220
    // The brand's standard curve, cubic-bezier(.22, 1, .36, 1).
    readonly property var standardCurve: [0.22, 1, 0.36, 1, 1, 1]
    // One clock for every "busy, amount unknown" indicator, so a card rebuilt by an activity update
    // picks up where the last one was instead of starting its sweep again. 0–1, looping.
    property real busyPhase: 0
    NumberAnimation on busyPhase {
        from: 0; to: 1; duration: 1600; loops: Animation.Infinite
        running: root.motionQuick > 0 && (root.deskOpen ? root.activities.some(a => !(a.progress >= 0))
            : root.activities.length > 0 && !(root.activities[0].progress >= 0))
    }
    // Line icons on a 24-unit grid, drawn by the shell so no icon font is needed.
    readonly property var iconPaths: ({
        "bell": "M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 2h-15z M10 21h4",
        "sliders": "M4 8h8 M18 8h2 M4 16h2 M12 16h8 M13 8a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0 -5 0 M6 16a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0 -5 0",
        "search": "M4.5 11a6.5 6.5 0 1 0 13 0a6.5 6.5 0 1 0 -13 0 M16 16l4 4",
        "left": "M14.5 6l-6 6l6 6", "right": "M9.5 6l6 6l-6 6",
        "up": "M6 14.5l6 -6l6 6", "down": "M6 9.5l6 6l6 -6",
        "close": "M7 7l10 10 M17 7l-10 10", "plus": "M12 5.5v13 M5.5 12h13", "minus": "M5.5 12h13"
    })
    component FlowIcon: Item {
        id: flowIcon
        property string name
        property color color: floeTheme.textPrimary
        property real size: 18
        implicitWidth: size; implicitHeight: size
        Shape {
            // Geometry rendering with 4× multisampling: the curve renderer's strokes break up under virgl.
            anchors.fill: parent
            preferredRendererType: Shape.GeometryRenderer
            layer.enabled: true; layer.samples: 4
            ShapePath {
                scale: Qt.size(flowIcon.size / 24, flowIcon.size / 24)
                strokeColor: flowIcon.color; strokeWidth: 1.6; fillColor: "transparent"
                capStyle: ShapePath.RoundCap; joinStyle: ShapePath.RoundJoin
                PathSvg { path: root.iconPaths[flowIcon.name] || "" }
            }
        }
    }
    // Small capitals that name a section; they never compete with the content below them.
    component SectionLabel: Text {
        textFormat: Text.PlainText; color: floeTheme.textMuted
        font.pixelSize: 11; font.weight: Font.DemiBold; font.letterSpacing: 1.6; font.family: floeTheme.interfaceFont
    }
    component FlowButton: Button {
        id: control
        // secondary: the ordinary control. primary: the one main action of a surface. quiet: a soft tint, for
        // repeated and low-priority actions. danger: a quiet action that removes or closes. plain: no fill at
        // rest, for a control whose container already draws its boundary (a switcher card).
        property string variant: "secondary"
        // The current choice among siblings (the focused app, the active Layer): a leading dot, a 2 px
        // border, a heavier label and "Current" for assistive technology, so it never depends on tint alone.
        property bool selected: false
        // The panel this button opens is showing (Desk, search, quick settings): the same fill and border as
        // a selected button, no dot, and "Open" for assistive technology. It is not a choice among siblings.
        property bool open: false
        property string glyph: ""
        property real radius: 10
        readonly property color labelColor: variant === "primary" ? floeTheme.onAccent : variant === "danger" ? floeTheme.danger
            : selected || variant === "secondary" ? floeTheme.textPrimary : floeTheme.textSecondary
        focusPolicy: Qt.StrongFocus
        hoverEnabled: true
        Keys.onReturnPressed: control.clicked()
        Keys.onEnterPressed: control.clicked()
        implicitHeight: 58
        leftPadding: 16; rightPadding: 16
        opacity: enabled ? 1 : 0.45
        scale: control.down && root.motionQuick ? 0.96 : 1
        Behavior on scale { NumberAnimation { duration: root.motionQuick; easing.type: Easing.BezierSpline; easing.bezierCurve: root.standardCurve } }
        Accessible.description: selected ? "Current" : open ? "Open" : ""
        contentItem: Item {
            readonly property real dotRoom: control.selected && !control.glyph ? 12 : 0
            implicitWidth: control.glyph ? 18 : label.implicitWidth + dotRoom; implicitHeight: label.implicitHeight
            Loader {
                // Only glyph buttons pay for a Shape and its multisampled layer.
                active: !!control.glyph; anchors.centerIn: parent
                sourceComponent: Component { FlowIcon { name: control.glyph; color: control.labelColor } }
            }
            Rectangle {
                visible: parent.dotRoom > 0
                x: Math.max(0, (parent.width - parent.implicitWidth) / 2); anchors.verticalCenter: parent.verticalCenter
                width: 6; height: 6; radius: 3; color: control.labelColor
            }
            Text {
                id: label
                visible: !control.glyph
                x: parent.dotRoom ? Math.max(12, (parent.width - parent.implicitWidth) / 2 + 12) : 0
                width: parent.width - x; height: parent.height
                text: control.text; textFormat: Text.PlainText; color: control.labelColor; elide: Text.ElideRight
                font.pixelSize: 14; font.family: floeTheme.interfaceFont
                font.weight: control.selected || control.variant === "primary" ? Font.DemiBold : Font.Medium
                horizontalAlignment: parent.dotRoom ? Text.AlignLeft : Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
            }
        }
        background: Item {
            Rectangle {
                anchors.fill: parent; radius: control.radius
                // Quiet and danger buttons rest on a soft tint, so they read as controls on touch screens too,
                // where there is no hover to reveal them.
                color: control.variant === "primary" ? (control.down || control.hovered ? floeTheme.accentStrong : floeTheme.accent)
                    : control.selected || control.open || control.down || control.hovered ? floeTheme.surfaceSelected
                    : control.variant === "secondary" ? floeTheme.surface
                    : control.variant === "danger" ? Qt.alpha(floeTheme.danger, 0.1)
                    : control.variant === "plain" ? "transparent" : Qt.alpha(floeTheme.surfaceSelected, 0.6)
                // Functional borders stay at full strength (3:1 against the surfaces, brand guidelines).
                border.width: control.selected || control.open ? 2 : control.variant === "secondary" ? 1 : 0
                border.color: control.selected || control.open || control.hovered ? floeTheme.borderStrong : floeTheme.border
                Behavior on color { ColorAnimation { duration: root.motionQuick } }
            }
            Rectangle {
                // Keyboard focus: a 2 px ring 3 px outside the control, concentric with it.
                visible: control.activeFocus
                anchors.fill: parent; anchors.margins: -3; radius: control.radius + 3
                color: "transparent"; border.width: 2; border.color: floeTheme.focus
            }
        }
    }
    component IslandButton: FlowButton {
        // A capsule inset 4 px in a capsule bar: 44 px to touch, 36 px to see, radius 22 − 4 − 4 = concentric.
        id: islandButton
        variant: "quiet"
        leftPadding: 12; rightPadding: 12
        background: Item {
            Rectangle {
                anchors.fill: parent; anchors.margins: 4; radius: height / 2
                color: islandButton.selected || islandButton.open || islandButton.down || islandButton.hovered ? floeTheme.surfaceSelected : "transparent"
                border.width: islandButton.selected || islandButton.open ? 2 : 0; border.color: floeTheme.borderStrong
                Behavior on color { ColorAnimation { duration: root.motionQuick } }
            }
            Rectangle {
                visible: islandButton.activeFocus
                anchors.fill: parent; anchors.margins: 1; radius: height / 2
                color: "transparent"; border.width: 2; border.color: floeTheme.focus
            }
        }
    }
    // A capsule bar for system and position controls: the island, the work indicator, the bottom controls.
    component CapsuleBar: Rectangle {
        radius: height / 2; color: floeTheme.canvas
        border.width: 1; border.color: Qt.alpha(floeTheme.border, 0.55)
    }
    SystemClock { id: clock; precision: SystemClock.Minutes }
    component SystemIsland: CapsuleBar {
        // The width of the screen the island is on; an activity chip only takes what is left.
        property real room: 1280
        implicitWidth: islandControls.implicitWidth + 8; implicitHeight: 44
        Row {
            id: islandControls
            anchors.centerIn: parent; spacing: 0
            IslandButton {
                id: wordmark
                implicitHeight: 44; text: "floe."
                Accessible.name: root.deskOpen ? "Return to work" : "Open Floe Desk"
                open: root.deskOpen
                contentItem: Text {
                    text: wordmark.text; textFormat: Text.PlainText
                    color: floeTheme.textPrimary; font.pixelSize: 16; font.weight: Font.DemiBold; font.letterSpacing: -0.4
                    font.family: floeTheme.interfaceFont; verticalAlignment: Text.AlignVCenter; horizontalAlignment: Text.AlignHCenter
                }
                onClicked: root.toggleDesk()
            }
            IslandButton {
                property bool closeOnRelease: false
                implicitHeight: 44
                text: root.launcherActive ? "Close" : "Apps"
                open: root.launcherActive
                Accessible.name: root.launcherActive ? "Close search and apps" : "Open apps"
                onPressed: closeOnRelease = root.launcherActive
                onClicked: { if (closeOnRelease) root.closeLauncher(); else root.openApps(); closeOnRelease = false }
                onCanceled: closeOnRelease = false
            }
            Item {
                // The newest activity: who it is from, its title and its progress; "+N" when there are more.
                id: activityChip
                property var current: root.activities.length ? root.activities[0] : null
                // The other controls take about 300 px; on a narrower screen the chip becomes a dot.
                property real budget: Math.min(260, room - 300)
                property bool compact: budget < 120
                property bool determinate: !!current && current.progress >= 0
                visible: !!current
                width: (compact ? 36 : Math.min(budget, activityLabel.implicitWidth + 30)) + 8; height: 44
                Rectangle {
                    // Island radius 22, inset 6: radius 16.
                    id: chipBody
                    anchors.fill: parent; anchors.margins: 6; anchors.leftMargin: 4; anchors.rightMargin: 4
                    radius: height / 2; color: floeTheme.surface; clip: true
                    Rectangle {
                        anchors { left: parent.left; top: parent.top; bottom: parent.bottom }
                        width: activityChip.determinate ? parent.width * activityChip.current.progress : 0
                        radius: Math.min(parent.radius, width / 2); color: Qt.alpha(floeTheme.accent, 0.22)
                        Behavior on width { NumberAnimation { duration: root.motionStandard; easing.type: Easing.BezierSpline; easing.bezierCurve: root.standardCurve } }
                    }
                    Rectangle {
                        // Busy without a known amount: a dot that breathes with the shared busy clock, and
                        // stays solid when motion is reduced.
                        visible: !activityChip.determinate && !activityChip.compact
                        x: 10; anchors.verticalCenter: parent.verticalCenter; width: 6; height: 6; radius: 3
                        color: floeTheme.accent
                        opacity: root.motionQuick ? 0.675 + 0.325 * Math.cos(root.busyPhase * 2 * Math.PI) : 1
                    }
                    Text {
                        visible: activityChip.compact
                        anchors.centerIn: parent; textFormat: Text.PlainText
                        text: activityChip.determinate ? Math.round(activityChip.current.progress * 100) : "…"
                        color: floeTheme.textPrimary; font.pixelSize: 11; font.weight: Font.DemiBold
                        font.family: floeTheme.interfaceFont; font.features: { "tnum": 1 }
                    }
                    Text {
                        id: activityLabel
                        visible: !activityChip.compact
                        x: activityChip.determinate ? 12 : 22; anchors.verticalCenter: parent.verticalCenter
                        width: Math.min(implicitWidth, Math.max(0, activityChip.budget - 30))
                        elide: Text.ElideRight; textFormat: Text.PlainText
                        text: activityChip.current ? root.activityText(activityChip.current) + (root.activities.length > 1 ? "  +" + (root.activities.length - 1) : "") : ""
                        color: floeTheme.textPrimary; font.pixelSize: 13; font.family: floeTheme.interfaceFont; font.features: { "tnum": 1 }
                    }
                }
                Accessible.role: Accessible.ProgressBar
                Accessible.name: activityChip.current ? "Activity from " + activityChip.current.owner + ": " + activityChip.current.title : ""
                Accessible.description: activityChip.current ? (activityChip.current.progress >= 0
                    ? Math.round(activityChip.current.progress * 100) + " percent" : "in progress")
                    + (root.activities.length > 1 ? ", and " + (root.activities.length - 1) + " more in Desk" : "") : ""
            }
            Text {
                width: 56; height: 44; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                text: Qt.formatTime(clock.date, "hh:mm"); color: floeTheme.textPrimary
                font.pixelSize: 14; font.weight: Font.Medium; font.family: floeTheme.interfaceFont; font.features: { "tnum": 1 }
            }
            IslandButton {
                width: 44; implicitHeight: 44; glyph: "bell"; open: root.notificationsOpen
                Accessible.name: notificationServer.trackedNotifications.values.length
                    ? "Notifications, " + notificationServer.trackedNotifications.values.length + " waiting" : "Notifications"
                onClicked: root.toggleNotifications()
                Rectangle {
                    // Something is waiting: a dot, and the count in the accessible name.
                    visible: notificationServer.trackedNotifications.values.length > 0
                    x: 26; y: 11; width: 7; height: 7; radius: 3.5
                    color: floeTheme.accent; border.width: 1.5; border.color: floeTheme.canvas
                }
            }
            IslandButton { width: 44; implicitHeight: 44; glyph: "sliders"; open: root.quickSettingsOpen; Accessible.name: "Quick settings"; onClicked: root.toggleQuickSettings() }
        }
    }
    component FlowSwitch: Switch {
        id: toggle
        focusPolicy: Qt.StrongFocus
        hoverEnabled: true
        implicitWidth: contentItem.implicitWidth; implicitHeight: 44
        opacity: enabled ? 1 : 0.45
        contentItem: Text {
            text: toggle.text; textFormat: Text.PlainText; color: floeTheme.textPrimary
            font.pixelSize: 14; font.family: floeTheme.interfaceFont
            leftPadding: toggle.indicator.width + 12; verticalAlignment: Text.AlignVCenter
        }
        indicator: Item {
            x: 0; y: (toggle.height - height) / 2
            width: 52; height: 44
            Rectangle {
                anchors.fill: parent; anchors.margins: -1; radius: 14
                visible: toggle.activeFocus; color: "transparent"; border.width: 2; border.color: floeTheme.focus
            }
            Rectangle {
                // The track keeps a functional border in both states, so "off" is never just a darker tint.
                anchors.centerIn: parent; width: 44; height: 26; radius: 13
                color: toggle.checked ? floeTheme.accent : toggle.hovered ? floeTheme.surfaceSelected : floeTheme.surface
                border.width: toggle.checked ? 0 : 1; border.color: floeTheme.border
                Behavior on color { ColorAnimation { duration: root.motionQuick } }
                Rectangle {
                    x: toggle.checked ? parent.width - width - 4 : 4; anchors.verticalCenter: parent.verticalCenter
                    width: 18; height: 18; radius: 9
                    color: toggle.checked ? floeTheme.onAccent : floeTheme.textMuted
                    Behavior on x { NumberAnimation { duration: root.motionQuick; easing.type: Easing.BezierSpline; easing.bezierCurve: root.standardCurve } }
                }
            }
        }
    }

    // Apps get a systemd scope of their own when the installed shell's unit asks for it
    // (session/launching.py), so restarting the shell never touches them.
    function launchApp(command) {
        Quickshell.execDetached(Quickshell.env("FLOW_APP_SCOPES") === "1"
            ? ["systemd-run", "--user", "--scope", "--collect", "--quiet", "--"].concat(command) : command)
    }
    // The installed shell does not live-reload: a NixOS switch restarts its user unit instead.
    // Quickshell cannot reload a configuration that adds an inline component, and its file watching
    // does not reliably notice a switch. A development shell keeps live reload.
    Component.onCompleted: {
        if (Quickshell.shellDir !== "/etc/flow") return
        Quickshell.watchFiles = false
        root.syncCompositorAppearance()
    }
    // Niri includes ~/.config/flow/niri-appearance.kdl (session/niri.kdl) and reloads it when it changes:
    // the installed shell keeps it pointing at its appearance's Niri colours (session/flow-niri-appearance.sh).
    // A development shell never touches the user's compositor.
    function syncCompositorAppearance() {
        if (Quickshell.shellDir === "/etc/flow") Quickshell.execDetached(["flow-niri-appearance"])
    }
    IpcHandler {
        target: "desk"
        // Read-only, for scripted checks of shell feedback.
        function toast(): string { return root.toast }
        function spaces_error(): string { return root.spacesError }
        function desk_state(): string { return root.deskOpen ? "open" : "closed" }
        function activity_list(): string { return JSON.stringify(root.activities.map(a => root.activityText(a))) }
        function launcher_state(): string { return root.launcherBusy() ? "open" : "closed" }
        function menu(): void {
            if (menuPreferences.appMenuEnabled && root.appMenu && !root.deskOpen && !root.overviewOpen) {
                if (root.appMenuOpen) root.appMenuOpen = false
                else root.requestAppMenu()
            }
        }
        function quick(): void { root.toggleQuickSettings() }
        function quick_open(): void { if (!root.quickSettingsOpen) root.toggleQuickSettings() }
        function settings(): void {
            if (!root.deskOpen) root.toggleDesk()
            root.settingsOpen = true
            Qt.callLater(function() { deskScroller.ensureVisible(menuSwitch) })
        }
        function apps(): void { root.openApps() }
        function search(): void { root.openSearch() }
        function pin(): void { root.spaceAction("pin") }
        function focus_pin(id: string): void { root.spaceAction("focus", id) }
        function focus_window(id: string): void { root.spaceAction("focus-window", id) }
        // Open an installed app (desktop ID) as a Layer over the current space.
        function open_layer(id: string): void { root.spaceAction("open-layer", id) }
        // Super+number: pinned space on the main display, from any display.
        function focus(index: int): void {
            if (index > 0 && index <= 9) root.spaceAction("focus-number", "0:" + index)
            else { root.spacesError = "No pinned space at that position"; root.deskOpen = true }
        }
        function display_digit(digit: int): void { if (digit > 0 && digit <= 9) root.displayDigit(digit) }
        function toggle(): void { root.toggleDesk() }
        // One step along the row, for the wheel: a notch that arrives while a step is still running or
        // queued is dropped, so a fast spin never queues moves that keep going after the wheel stops.
        function navigate(direction: string): void {
            if (direction !== "left" && direction !== "right") return
            if ((spacesProcess.running && spacesProcess.command[1] === "navigate")
                || root.pendingSpaceActions.some(a => a.action === "navigate")) return
            root.spaceAction("navigate", direction)
        }
        // Show-only requests (the Platform API's System interface). They never close anything: while
        // any other shell surface is open they do nothing and answer "busy"; otherwise "open" or "opened".
        function desk_open(): string {
            if (root.overviewOpen || root.launcherBusy() || root.otherSurfaceOpen()) return "busy"
            if (root.deskOpen) return "open"
            root.toggleDesk(); return "opened"
        }
        function overview_open(): string {
            if (root.deskOpen || root.launcherBusy() || root.otherSurfaceOpen()) return "busy"
            if (root.overviewOpen) return "open"
            root.showOverview("open-overview"); return "opened"
        }
        function apps_open(): string {
            if (root.deskOpen || root.overviewOpen || root.otherSurfaceOpen()) return "busy"
            if (root.launcherBusy()) return "open"
            root.openApps(); return "opened"
        }
        function leave(): void { root.deskOpen = false }
        function switcher(): void { root.toggleSwitcher() }
        function overview(): void {
            root.showOverview(root.deskOpen ? "open-overview" : "toggle-overview")
        }
    }

    // shortcut: Desk content settles in (220 ms fade and rise) but apps do not move away; add the spatial
    // transition when compositor integration supports it.
    PanelWindow {
        visible: root.deskOpen
        anchors { top: true; bottom: true; left: true; right: true }
        color: floeTheme.canvas
        exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: root.notificationsOpen ? WlrKeyboardFocus.None : root.launcherActive ? WlrKeyboardFocus.OnDemand : WlrKeyboardFocus.Exclusive
        onVisibleChanged: if (visible && root.motionQuick) deskEnter.restart()
        ParallelAnimation {
            id: deskEnter
            NumberAnimation { target: deskContent; property: "opacity"; from: 0; to: 1; duration: 220; easing.type: Easing.BezierSpline; easing.bezierCurve: root.standardCurve }
            NumberAnimation { target: deskShift; property: "y"; from: 12; to: 0; duration: 220; easing.type: Easing.BezierSpline; easing.bezierCurve: root.standardCurve }
        }

        FocusScope {
            anchors.fill: parent
            focus: true
            Keys.onEscapePressed: root.deskOpen = false
            SystemIsland {
                room: parent.width
                anchors.top: parent.top; anchors.horizontalCenter: parent.horizontalCenter; anchors.topMargin: 8
            }
            Flickable {
                id: deskScroller
                function ensureVisible(item) {
                    const top = item.mapToItem(contentItem, 0, 0).y
                    if (top < contentY) contentY = Math.max(0, top)
                    else if (top + item.height > contentY + height)
                        contentY = Math.min(contentHeight - height, top + item.height - height)
                }
                anchors.fill: parent; anchors.topMargin: 60
                contentWidth: width
                contentHeight: Math.max(height, deskContent.implicitHeight + 40)
                boundsBehavior: Flickable.StopAtBounds
                clip: true
                Column {
                    id: deskContent
                    x: (parent.width - width) / 2
                    y: Math.max(20, (parent.height - height) / 2)
                    width: Math.max(200, Math.min(parent.width - 48, 910))
                    spacing: 12
                    transform: Translate { id: deskShift }
                    SectionLabel { text: "FLOE  /  YOUR DESK"; color: floeTheme.accent }
                    Text {
                        text: "Everything<br>in its <font color='" + floeTheme.textSecondary + "'>place.</font>"
                        textFormat: Text.RichText
                        color: floeTheme.textPrimary; font.weight: Font.DemiBold; font.family: floeTheme.interfaceFont
                        font.pixelSize: parent.width >= 520 ? 52 : 32
                        // Display type tracks tighter: −3% of its size.
                        font.letterSpacing: -(parent.width >= 520 ? 52 : 32) * 0.03
                        lineHeight: 0.98
                    }
                    Text {
                        text: "Your tools are here. Your work is right where you left it."
                        color: floeTheme.textMuted; font.pixelSize: 16; font.family: floeTheme.interfaceFont
                        wrapMode: Text.Wrap; width: parent.width; bottomPadding: 8
                    }
                    FlowButton {
                        id: searchButton
                        onActiveFocusChanged: if (activeFocus) deskScroller.ensureVisible(searchButton)
                        width: Math.min(parent.width, 620); implicitHeight: 60; radius: 14
                        leftPadding: 18; rightPadding: 12
                        text: width < 380 ? "Search" : "Search apps and open spaces"
                        Accessible.name: "Search apps and open spaces"
                        contentItem: Item {
                            FlowIcon {
                                id: searchGlyph
                                anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                                name: "search"; size: 20; color: floeTheme.accent
                            }
                            Text {
                                anchors.left: searchGlyph.right; anchors.leftMargin: 12
                                anchors.right: searchShortcut.left; anchors.rightMargin: 8
                                anchors.verticalCenter: parent.verticalCenter
                                text: searchButton.text; textFormat: Text.PlainText; elide: Text.ElideRight
                                color: floeTheme.textSecondary; font.pixelSize: 16; font.family: floeTheme.interfaceFont
                            }
                            Rectangle {
                                id: searchShortcut
                                anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                width: shortcutLabel.implicitWidth + 16; height: 28; radius: 6
                                color: floeTheme.surfaceRaised; border.color: Qt.alpha(floeTheme.border, 0.6)
                                Text {
                                    id: shortcutLabel
                                    anchors.centerIn: parent; text: "Super K"; textFormat: Text.PlainText
                                    color: floeTheme.textSecondary; font.pixelSize: 12; font.family: floeTheme.technicalFont
                                }
                            }
                        }
                        onClicked: root.openSearch()
                    }
                    SectionLabel {
                        topPadding: 12
                        text: "PINNED SPACES" + (root.displayCount > 1 && root.displayName ? "  ·  " + root.displayName.toUpperCase() + (root.displayLabel ? "  ·  " + root.displayLabel.toUpperCase() : "") : "")
                    }
                    Text {
                        visible: root.displayCount > 1 && root.displayName !== ""
                        text: root.displayName === "Main" ? "Super 1–9 open these spaces from any display"
                            : "Hold Super Alt, press " + root.displayName.replace("External ", "") + ", then a space number"
                        textFormat: Text.PlainText; color: floeTheme.textMuted; font.pixelSize: 12; font.family: floeTheme.interfaceFont
                    }
                    Text { visible: root.pins.length === 0; text: "Pin the current space to keep its place"; color: floeTheme.textMuted; font.pixelSize: 14; font.family: floeTheme.interfaceFont }
                    Flow {
                        width: parent.width
                        spacing: 8
                        Repeater {
                            model: root.pins
                            Flow {
                                required property var modelData
                                required property int index
                                width: Math.min(parent.width, root.editPins ? 381 : 240)
                                spacing: 3
                                FlowButton {
                                    id: pinnedSpaceButton
                                    onActiveFocusChanged: if (activeFocus) deskScroller.ensureVisible(pinnedSpaceButton)
                                    width: 240; implicitHeight: 64; radius: 14; leftPadding: 14
                                    text: modelData.label
                                    property string detail: !modelData.available ? (modelData.kind === "group" ? "Unavailable" : "Open")
                                        : modelData.kind === "group" ? modelData.size + " apps in this space" : "Always here"
                                    contentItem: Row {
                                        spacing: 12; leftPadding: 4
                                        Text {
                                            anchors.verticalCenter: parent.verticalCenter
                                            text: (index < 9 ? "0" : "") + (index + 1); textFormat: Text.PlainText
                                            color: floeTheme.accent; font.weight: Font.DemiBold; font.pixelSize: 13
                                            font.family: floeTheme.interfaceFont; font.features: { "tnum": 1 }
                                        }
                                        Column {
                                            anchors.verticalCenter: parent.verticalCenter; width: 180
                                            Text { width: parent.width; text: pinnedSpaceButton.text; textFormat: Text.PlainText; elide: Text.ElideRight; color: floeTheme.textPrimary; font.weight: Font.DemiBold; font.pixelSize: 14; font.family: floeTheme.interfaceFont }
                                            Text { width: parent.width; text: pinnedSpaceButton.detail; textFormat: Text.PlainText; elide: Text.ElideRight; color: floeTheme.textMuted; font.pixelSize: 12; font.family: floeTheme.interfaceFont }
                                        }
                                    }
                                    enabled: !spacesProcess.running || spacesProcess.command[1] === "refresh"
                                    Accessible.name: (modelData.available ? "Focus " : modelData.kind === "group" ? "Unavailable Group " : "Open ") + modelData.label + ", pinned space " + (index + 1)
                                    onClicked: root.spaceAction("focus", modelData.id)
                                }
                                FlowButton { id: pinLeftButton; onActiveFocusChanged: if (activeFocus) deskScroller.ensureVisible(pinLeftButton); visible: root.editPins; width: 44; implicitHeight: 64; variant: "quiet"; glyph: "left"; text: "‹"; Accessible.name: "Move " + modelData.label + " left"; enabled: !spacesProcess.running || spacesProcess.command[1] === "refresh"; onClicked: root.spaceAction("left", modelData.id) }
                                FlowButton { id: pinRightButton; onActiveFocusChanged: if (activeFocus) deskScroller.ensureVisible(pinRightButton); visible: root.editPins; width: 44; implicitHeight: 64; variant: "quiet"; glyph: "right"; text: "›"; Accessible.name: "Move " + modelData.label + " right"; enabled: !spacesProcess.running || spacesProcess.command[1] === "refresh"; onClicked: root.spaceAction("right", modelData.id) }
                                FlowButton { id: unpinButton; onActiveFocusChanged: if (activeFocus) deskScroller.ensureVisible(unpinButton); visible: root.editPins; width: 44; implicitHeight: 64; variant: "danger"; glyph: "close"; text: "×"; enabled: !spacesProcess.running || spacesProcess.command[1] === "refresh"; Accessible.name: "Unpin " + modelData.label; onClicked: root.spaceAction("unpin", modelData.id) }
                            }
                        }
                    }
                    SectionLabel {
                        visible: root.otherPins.length > 0; topPadding: 12
                        text: root.displayCount > 1 ? "OTHER DISPLAYS" : "PINNED ON OTHER DISPLAYS"
                    }
                    Flow {
                        visible: root.otherPins.length > 0
                        width: parent.width
                        spacing: 8
                        Repeater {
                            model: root.otherPins
                            Flow {
                                required property var modelData
                                width: Math.min(parent.width, 381); spacing: 3
                                FlowButton {
                                    id: otherPinButton
                                    onActiveFocusChanged: if (activeFocus) deskScroller.ensureVisible(otherPinButton)
                                    width: 240; implicitHeight: 64; radius: 14; leftPadding: 14; text: modelData.label
                                    property string detail: (modelData.display_name ? modelData.display_name + " · " : "") + modelData.display_label + (modelData.display_state === "connected" ? ""
                                        : modelData.display_state === "unassigned" ? " · not assigned"
                                        : modelData.display_state === "ambiguous" ? " · cannot be told apart" : " · disconnected")
                                    enabled: (modelData.display_state === "connected" || modelData.available) && (!spacesProcess.running || spacesProcess.command[1] === "refresh")
                                    contentItem: Column {
                                        topPadding: 12
                                        Text { width: 210; text: otherPinButton.text; textFormat: Text.PlainText; elide: Text.ElideRight; color: otherPinButton.enabled ? floeTheme.textPrimary : floeTheme.textMuted; font.weight: Font.DemiBold; font.pixelSize: 14; font.family: floeTheme.interfaceFont }
                                        Text { width: 210; text: otherPinButton.detail; textFormat: Text.PlainText; elide: Text.ElideRight; color: floeTheme.textMuted; font.pixelSize: 12; font.family: floeTheme.interfaceFont }
                                    }
                                    Accessible.name: (modelData.display_state === "connected" ? "Go to " : "Focus ") + modelData.label + " on " + otherPinButton.detail
                                    onClicked: root.spaceAction("focus", modelData.id)
                                }
                                FlowButton {
                                    id: movePinHereButton
                                    onActiveFocusChanged: if (activeFocus) deskScroller.ensureVisible(movePinHereButton)
                                    width: 132; implicitHeight: 64; variant: "quiet"; text: "Pin here"
                                    Accessible.name: "Pin " + modelData.label + " on this display instead"
                                    enabled: !spacesProcess.running || spacesProcess.command[1] === "refresh"
                                    onClicked: root.spaceAction("move-pin-here", modelData.id)
                                }
                            }
                        }
                    }
                    Flow {
                        width: parent.width
                        spacing: 8
                        FlowButton {
                            id: pinCurrentButton
                            onActiveFocusChanged: if (activeFocus) deskScroller.ensureVisible(pinCurrentButton)
                            width: Math.min(parent.width, implicitWidth); implicitHeight: 44; text: "Pin current space · Super P"
                            contentItem: Text {
                                text: "Pin current space · <span style=\"font-family:'" + floeTheme.technicalFont + "'\">Super P</span>"
                                textFormat: Text.RichText; color: floeTheme.textPrimary
                                font.pixelSize: 14; font.weight: Font.Medium; font.family: floeTheme.interfaceFont
                                horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
                            }
                            enabled: !spacesProcess.running || spacesProcess.command[1] === "refresh"
                            onClicked: root.spaceAction("pin")
                        }
                        FlowButton { id: editPinsButton; onActiveFocusChanged: if (activeFocus) deskScroller.ensureVisible(editPinsButton); visible: root.pins.length > 0; implicitHeight: 44; variant: root.editPins ? "primary" : "quiet"; text: root.editPins ? "Done" : "Edit pins"; onClicked: root.editPins = !root.editPins }
                        FlowButton {
                            id: makeMainButton
                            onActiveFocusChanged: if (activeFocus) deskScroller.ensureVisible(makeMainButton)
                            visible: root.displayCount > 1 && root.displayName !== "" && root.displayName !== "Main"
                            implicitHeight: 44; variant: "quiet"; text: "Make this the main display"
                            Accessible.name: "Make " + root.displayLabel + " the main display; Super and a number will open its pinned spaces"
                            enabled: !spacesProcess.running || spacesProcess.command[1] === "refresh"
                            onClicked: root.spaceAction("make-main", (root.displays.find(d => d.current) || {}).key)
                        }
                    }
                    Row {
                        visible: root.spacesError !== ""; width: parent.width; spacing: 8
                        Text { text: root.spacesError; textFormat: Text.PlainText; color: floeTheme.danger; wrapMode: Text.Wrap; width: parent.width - dismissSpacesError.width - 8; height: Math.max(44, implicitHeight); verticalAlignment: Text.AlignVCenter; font.pixelSize: 14; font.family: floeTheme.interfaceFont }
                        FlowButton { id: dismissSpacesError; width: 96; implicitHeight: 44; text: "Dismiss"; variant: "quiet"; Accessible.name: "Dismiss spaces error"; onClicked: root.spacesError = "" }
                    }
                    SectionLabel { visible: root.activities.length > 0; topPadding: 12; text: "ACTIVITIES" }
                    Flow {
                        visible: root.activities.length > 0
                        width: parent.width; spacing: 8
                        Repeater {
                            model: root.activities
                            Rectangle {
                                id: activityCard
                                required property var modelData
                                property bool determinate: modelData.progress >= 0
                                width: Math.min(parent.width, 300); height: activityColumn.implicitHeight + 24; radius: 14
                                color: floeTheme.surface; border.width: 1; border.color: Qt.alpha(floeTheme.border, 0.45)
                                Column {
                                    id: activityColumn
                                    x: 14; y: 12; width: parent.width - 28; spacing: 4
                                    Row {
                                        width: parent.width; spacing: 8
                                        Text {
                                            width: parent.width - percent.width - 8; elide: Text.ElideRight; textFormat: Text.PlainText
                                            text: activityCard.modelData.title; color: floeTheme.textPrimary
                                            font.pixelSize: 14; font.weight: Font.DemiBold; font.family: floeTheme.interfaceFont
                                        }
                                        Text {
                                            id: percent
                                            text: activityCard.determinate ? Math.round(activityCard.modelData.progress * 100) + "%" : "Working"
                                            textFormat: Text.PlainText; color: floeTheme.textSecondary
                                            font.pixelSize: 13; font.family: floeTheme.interfaceFont; font.features: { "tnum": 1 }
                                        }
                                    }
                                    Text {
                                        width: parent.width; elide: Text.ElideRight; textFormat: Text.PlainText
                                        text: activityCard.modelData.owner + (activityCard.modelData.detail ? "  ·  " + activityCard.modelData.detail : "")
                                        color: floeTheme.textMuted; font.pixelSize: 12; font.family: floeTheme.interfaceFont
                                    }
                                    Item { width: 1; height: 4 }
                                    Rectangle {
                                        // Progress track. Without a known amount a short segment sweeps across it,
                                        // driven by the shared busy clock (cards are rebuilt on every update);
                                        // with motion reduced the whole track is dimly filled instead.
                                        id: progressTrack
                                        width: parent.width; height: 4; radius: 2; color: floeTheme.surfaceSelected; clip: true
                                        readonly property bool sweeping: !activityCard.determinate && root.motionQuick > 0
                                        Rectangle {
                                            height: parent.height; radius: 2
                                            color: activityCard.determinate || progressTrack.sweeping ? floeTheme.accent : Qt.alpha(floeTheme.accent, 0.45)
                                            width: activityCard.determinate ? parent.width * activityCard.modelData.progress
                                                : progressTrack.sweeping ? parent.width * 0.3 : parent.width
                                            x: progressTrack.sweeping ? -width + root.busyPhase * (parent.width + width) : 0
                                        }
                                    }
                                }
                                Accessible.role: Accessible.ProgressBar
                                Accessible.name: "Activity from " + modelData.owner + ": " + modelData.title
                                Accessible.description: determinate ? Math.round(modelData.progress * 100) + " percent" : "in progress"
                            }
                        }
                    }
                    SectionLabel { text: "TOOLS"; topPadding: 12 }
                    Flow {
                        width: parent.width
                        spacing: 8
                        Repeater {
                            model: ["Apps", "Files", "Overview", "Notifications", "Settings", "Return to work"]
                            FlowButton {
                                id: toolButton
                                required property string modelData
                                onActiveFocusChanged: if (activeFocus) deskScroller.ensureVisible(toolButton)
                                implicitHeight: 44; leftPadding: 18; rightPadding: 18
                                open: modelData === "Settings" && root.settingsOpen
                                text: modelData
                                onClicked: {
                                    if (modelData === "Settings") {
                                        root.settingsOpen = !root.settingsOpen
                                        if (root.settingsOpen) Qt.callLater(function() { deskScroller.ensureVisible(menuSwitch) })
                                        return
                                    }
                                    if (modelData === "Apps") root.openApps()
                                    else if (modelData === "Notifications") root.toggleNotifications()
                                    else if (modelData === "Overview") root.showOverview()
                                    else {
                                        root.deskOpen = false
                                        if (modelData === "Files") root.launchApp(["nautilus"])
                                    }
                                }
                            }
                        }
                    }
                    Column {
                        visible: root.settingsOpen; width: parent.width; spacing: 12
                        SectionLabel { text: "SETTINGS"; topPadding: 12 }
                        FlowSwitch {
                            id: appearanceSwitch
                            text: appearancePreferences.lightMode ? "Arctic Daylight · Light" : "Polar Night · Dark"
                            checked: appearancePreferences.lightMode
                            Accessible.name: "Use Arctic Daylight light mode"
                            onActiveFocusChanged: if (activeFocus) deskScroller.ensureVisible(appearanceSwitch)
                            onToggled: { appearancePreferences.lightMode = checked; appearancePreferences.sync(); root.syncCompositorAppearance() }
                        }
                        FlowSwitch {
                            id: reduceMotionSwitch
                            text: "Reduce motion"
                            checked: appearancePreferences.reduceMotion
                            Accessible.name: "Reduce motion: buttons and progress change instantly"
                            onActiveFocusChanged: if (activeFocus) deskScroller.ensureVisible(reduceMotionSwitch)
                            onToggled: { appearancePreferences.reduceMotion = checked; appearancePreferences.sync() }
                        }
                        FlowSwitch {
                            id: menuSwitch
                            text: "Experimental application menu"
                            checked: menuPreferences.appMenuEnabled
                            Accessible.name: text
                            onActiveFocusChanged: if (activeFocus) deskScroller.ensureVisible(menuSwitch)
                            onToggled: { menuPreferences.appMenuEnabled = checked; menuPreferences.sync(); root.appMenuOpen = false }
                        }
                        SectionLabel { text: "SESSION RECOVERY"; topPadding: 12 }
                        FlowSwitch {
                            id: autoRecoverySwitch
                            text: "Automatically save and restore session"
                            checked: menuPreferences.autoRecovery
                            enabled: !recoveryProcess.running && !spacesProcess.running
                            Accessible.name: text
                            onActiveFocusChanged: if (activeFocus) deskScroller.ensureVisible(autoRecoverySwitch)
                            onToggled: {
                                menuPreferences.autoRecovery = checked; menuPreferences.sync()
                                root.autoRecoveryBlocked = true
                                if (checked) { recoveryProcess.command = ["flow-recovery", "auto-pause"]; recoveryProcess.running = true }
                            }
                        }
                        Text { text: "Save your documents, then save the current window list before restarting."; color: floeTheme.textMuted; width: parent.width; wrapMode: Text.Wrap; font.pixelSize: 14; font.family: floeTheme.interfaceFont }
                        Flow {
                            width: parent.width; spacing: 8
                            FlowButton { id: saveSession; implicitHeight: 44; text: "Save session"; enabled: !recoveryProcess.running && !spacesProcess.running; onActiveFocusChanged: if (activeFocus) deskScroller.ensureVisible(saveSession); onClicked: { recoveryProcess.command = ["flow-recovery", "save"]; recoveryProcess.running = true } }
                            FlowButton { id: restoreSession; implicitHeight: 44; variant: "quiet"; text: "Restore session"; enabled: !recoveryProcess.running && !spacesProcess.running; onActiveFocusChanged: if (activeFocus) deskScroller.ensureVisible(restoreSession); onClicked: { root.recoveryMessage = "Restoring applications…"; recoveryProcess.command = ["flow-recovery", "restore"]; recoveryProcess.running = true } }
                        }
                        Text { id: recoveryStatus; visible: root.recoveryMessage !== ""; text: root.recoveryMessage; textFormat: Text.PlainText; color: floeTheme.accent; width: parent.width; wrapMode: Text.Wrap }
                    }
                    Text { text: "<span style=\"font-family:'" + floeTheme.technicalFont + "'\">Tab</span> to navigate · <span style=\"font-family:'" + floeTheme.technicalFont + "'\">Esc</span> or <span style=\"font-family:'" + floeTheme.technicalFont + "'\">Super H</span> to return"; textFormat: Text.RichText; color: floeTheme.textMuted; font.pixelSize: 12; font.family: floeTheme.interfaceFont; topPadding: 16 }
                }
            }
        }
    }
    PanelWindow {
        visible: !root.deskOpen && !root.switcherOpen && !root.appMenuOpen && !root.launcherActive
        anchors.bottom: true
        implicitWidth: navigationControls.implicitWidth + 8; implicitHeight: 52; margins.bottom: 8
        color: "transparent"; exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        CapsuleBar {
            anchors.fill: parent
            Row {
                id: navigationControls
                anchors.centerIn: parent
                IslandButton {
                    implicitHeight: 44
                    text: root.overviewOpen ? "Desk" : "Overview"
                    onClicked: {
                        if (root.overviewOpen) root.toggleDesk()
                        else root.showOverview()
                    }
                }
                IslandButton { implicitHeight: 44; text: "Spaces"; onClicked: root.toggleSwitcher() }
            }
        }
    }
    PanelWindow {
        // Work indicator: the focused app of an active Group, and the space's place in this display's Flow.
        // It stays in Overview, where its arrows and position are the way along the row for a pointer
        // or a finger, next to Super+Left/Right and the wheel.
        id: workIndicatorWindow
        visible: !root.deskOpen && !root.switcherOpen && !root.appMenuOpen && !root.launcherActive && (!!root.activeSpace || !!root.activeLayer)
        anchors { bottom: true; right: true }
        // Where the bottom controls and this indicator cannot share a row, it rises above them: below 560 px,
        // or below 900 px for an active Layer's wider controls.
        readonly property bool narrow: !!screen && screen.width < (root.activeLayer ? 900 : 560)
        margins { bottom: narrow ? 68 : 8; right: 12 }
        implicitWidth: workIndicator.implicitWidth + 8; implicitHeight: 52
        color: "transparent"; exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        CapsuleBar {
            anchors.fill: parent
            Row {
                id: workIndicator
                anchors.centerIn: parent; spacing: 0
                Row {
                    // Layer actions belong to the work view; in Overview the indicator is the way along the row.
                    visible: !!root.activeLayer && !root.overviewOpen
                    spacing: 4
                    SectionLabel { visible: !workIndicatorWindow.narrow; anchors.verticalCenter: parent.verticalCenter; leftPadding: 14; rightPadding: 4; text: "LAYER" }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter; rightPadding: 6; leftPadding: workIndicatorWindow.narrow ? 14 : 0
                        width: Math.min(implicitWidth, workIndicatorWindow.narrow ? 120 : 220); elide: Text.ElideRight
                        text: root.activeLayer ? (root.activeLayer.app_name || root.activeLayer.label).slice(0, 24) : ""
                        textFormat: Text.PlainText; color: floeTheme.textPrimary; font.weight: Font.DemiBold; font.pixelSize: 13; font.family: floeTheme.interfaceFont
                    }
                    IslandButton {
                        implicitHeight: 44; text: "Make space"
                        Accessible.name: "Make this Layer its own space"
                        onClicked: root.spaceAction("layer-to-space", String(root.activeLayer.id))
                    }
                    IslandButton {
                        implicitHeight: 44; variant: "danger"; text: "Close"
                        Accessible.name: "Close this Layer; the app is asked to close and the space under it stays as it is"
                        onClicked: root.spaceAction("close-layer", String(root.activeLayer.id))
                    }
                }
                Row {
                    // Screens under 800 px keep only the position, so the indicator stays short.
                    visible: !root.activeLayer && !!(root.activeSpace && root.activeSpace.group_id) && workIndicatorWindow.screen && workIndicatorWindow.screen.width >= 800
                    spacing: 2
                    SectionLabel { anchors.verticalCenter: parent.verticalCenter; leftPadding: 14; rightPadding: 6; text: "GROUP" }
                    Repeater {
                        model: root.activeSpace && root.activeSpace.group_id ? root.activeSpace.members : []
                        IslandButton {
                            id: groupFocusButton
                            required property var modelData
                            width: Math.min(148, Math.max(64, implicitWidth)); implicitHeight: 44
                            selected: modelData.active
                            text: (modelData.app_name || modelData.label).slice(0, 16)
                            Accessible.name: (modelData.active ? "Focused: " : "Focus ") + modelData.label + " in Group " + root.activeSpace.label
                            onClicked: if (!modelData.active) root.spaceAction("focus-window", String(modelData.id))
                        }
                    }
                    Rectangle { width: 1; height: 20; anchors.verticalCenter: parent.verticalCenter; color: Qt.alpha(floeTheme.border, 0.5) }
                }
                IslandButton {
                    // From a Layer, navigation starts at the space beneath it (spaces.py), so in Overview the
                    // arrows stay even then.
                    visible: !root.activeLayer || root.overviewOpen
                    width: 44; implicitHeight: 44; glyph: "left"; text: "‹"
                    enabled: root.activeLayer ? root.displaySpaces.length > 0 : root.activeSpaceIndex > 0
                    Accessible.name: "Previous space"
                    onClicked: root.spaceAction("navigate", "left")
                }
                Text {
                    visible: !root.activeLayer
                    anchors.verticalCenter: parent.verticalCenter
                    text: (root.activeSpaceIndex < 9 ? "0" : "") + (root.activeSpaceIndex + 1) + "  /  " + (root.displaySpaces.length < 10 ? "0" : "") + root.displaySpaces.length
                    textFormat: Text.PlainText; color: floeTheme.textPrimary; font.pixelSize: 13; font.letterSpacing: 0.5
                    font.family: floeTheme.interfaceFont; font.features: { "tnum": 1 }
                    Accessible.name: "Space " + (root.activeSpaceIndex + 1) + " of " + root.displaySpaces.length
                }
                IslandButton {
                    visible: !root.activeLayer || root.overviewOpen
                    width: 44; implicitHeight: 44; glyph: "right"; text: "›"
                    enabled: root.activeLayer ? root.displaySpaces.length > 0
                        : root.activeSpaceIndex >= 0 && root.activeSpaceIndex < root.displaySpaces.length - 1
                    Accessible.name: "Next space"
                    onClicked: root.spaceAction("navigate", "right")
                }
            }
        }
    }
    // shortcut: window titles stand in for live previews until compositor capture is available.
    PanelWindow {
        visible: root.switcherOpen
        anchors { bottom: true; left: true; right: true }
        implicitHeight: Math.min(screen.height - 16, Math.max(300, switcherContent.implicitHeight + 40)); color: "transparent"; exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive
        Rectangle {
            // A bottom sheet: radius 20 on top, square where it meets the screen edge.
            anchors.fill: parent; anchors.bottomMargin: -20; radius: 20
            color: floeTheme.surface; border.width: 1; border.color: Qt.alpha(floeTheme.border, 0.5)
        }
        FocusScope {
            anchors.fill: parent; focus: true
            Keys.onEscapePressed: root.switcherOpen = false
            Flickable {
                id: switcherScroller
                // 4 px of the margin is inside the clip, so focus rings on the outer controls stay visible.
                anchors.fill: parent; anchors.margins: 16; anchors.topMargin: 20; clip: true
                leftMargin: 4; rightMargin: 4; topMargin: 4
                contentWidth: width; contentHeight: switcherContent.implicitHeight
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                function ensureVisible(item) {
                    const top = item.mapToItem(switcherContent, 0, 0).y
                    if (top < contentY) contentY = top
                    else if (top + item.height > contentY + height) contentY = Math.max(0, Math.min(contentHeight - height, top + item.height - height))
                }
            Column {
                id: switcherContent
                width: switcherScroller.width - 8; spacing: 12
                Flow {
                    width: parent.width; spacing: 12
                    Text {
                        height: 28; verticalAlignment: Text.AlignVCenter
                        text: "Spaces"; textFormat: Text.PlainText; color: floeTheme.textPrimary
                        font.pixelSize: 18; font.weight: Font.DemiBold; font.family: floeTheme.interfaceFont
                    }
                    Item {
                        width: spaceCountChip.width; height: 28
                        Rectangle {
                            id: spaceCountChip
                            anchors.verticalCenter: parent.verticalCenter
                            width: spaceCount.implicitWidth + 16; height: 22; radius: 11; color: floeTheme.surfaceSelected
                            Text {
                                id: spaceCount; anchors.centerIn: parent; text: root.displaySpaces.length; textFormat: Text.PlainText
                                color: floeTheme.textSecondary; font.pixelSize: 12; font.weight: Font.DemiBold; font.family: floeTheme.interfaceFont; font.features: { "tnum": 1 }
                            }
                        }
                        Accessible.name: root.displaySpaces.length + " open spaces"
                    }
                    Text {
                        height: 28; verticalAlignment: Text.AlignVCenter
                        text: "Select a space or app · <span style=\"font-family:'" + floeTheme.technicalFont + "'\">Esc</span> to close"
                        textFormat: Text.RichText; color: floeTheme.textMuted; font.pixelSize: 13; font.family: floeTheme.interfaceFont
                    }
                }
                Flow {
                    width: parent.width; spacing: 8
                    FlowButton { id: stackButton; onActiveFocusChanged: if (activeFocus) switcherScroller.ensureVisible(stackButton); variant: "quiet"; implicitHeight: 44; text: "Stack with next"; onClicked: root.spaceAction("stack") }
                    FlowButton { id: unstackButton; onActiveFocusChanged: if (activeFocus) switcherScroller.ensureVisible(unstackButton); variant: "quiet"; implicitHeight: 44; text: "Separate stack"; onClicked: root.spaceAction("unstack") }
                    FlowButton { id: overviewButton; onActiveFocusChanged: if (activeFocus) switcherScroller.ensureVisible(overviewButton); variant: "quiet"; implicitHeight: 44; text: "Live overview"; onClicked: root.showOverview() }
                    FlowButton {
                        id: makeLayerButton
                        onActiveFocusChanged: if (activeFocus) switcherScroller.ensureVisible(makeLayerButton)
                        variant: "quiet"; implicitHeight: 44; text: "Make Layer"
                        visible: !root.activeLayer
                        Accessible.name: "Float the focused app as a Layer over its neighbors"
                        enabled: !spacesProcess.running && !recoveryProcess.running && !root.pendingSpaceActions.length
                        onClicked: root.spaceAction("make-layer")
                    }
                    FlowButton { id: closeSwitcher; onActiveFocusChanged: if (activeFocus) switcherScroller.ensureVisible(closeSwitcher); variant: "quiet"; implicitHeight: 44; text: "Close"; Accessible.name: "Close Spaces switcher"; onClicked: root.switcherOpen = false }
                    Row {
                        visible: spaceScroller.contentWidth > spaceScroller.width
                        spacing: 6
                        FlowButton {
                            id: previousSpaces
                            onActiveFocusChanged: if (activeFocus) switcherScroller.ensureVisible(previousSpaces)
                            width: 44; implicitHeight: 44; variant: "quiet"; glyph: "left"; text: "‹"; Accessible.name: "Show earlier spaces"
                            enabled: spaceScroller.contentX > 0
                            onClicked: spaceScroller.contentX = Math.max(0, spaceScroller.contentX - spaceScroller.width * 0.8)
                        }
                        FlowButton {
                            id: nextSpaces
                            onActiveFocusChanged: if (activeFocus) switcherScroller.ensureVisible(nextSpaces)
                            width: 44; implicitHeight: 44; variant: "quiet"; glyph: "right"; text: "›"; Accessible.name: "Show later spaces"
                            enabled: spaceScroller.contentX < spaceScroller.contentWidth - spaceScroller.width
                            onClicked: spaceScroller.contentX = Math.min(spaceScroller.contentWidth - spaceScroller.width, spaceScroller.contentX + spaceScroller.width * 0.8)
                        }
                    }
                    Row {
                        id: switcherSpacesError
                        visible: root.spacesError !== ""; width: parent.width; spacing: 8
                        Text { text: root.spacesError; textFormat: Text.PlainText; color: floeTheme.danger; wrapMode: Text.Wrap; width: parent.width - dismissSwitcherSpacesError.width - 8; height: Math.max(44, implicitHeight); verticalAlignment: Text.AlignVCenter; font.pixelSize: 14; font.family: floeTheme.interfaceFont }
                        FlowButton { id: dismissSwitcherSpacesError; onActiveFocusChanged: if (activeFocus) switcherScroller.ensureVisible(dismissSwitcherSpacesError); width: 96; implicitHeight: 44; text: "Dismiss"; variant: "quiet"; Accessible.name: "Dismiss spaces error"; onClicked: root.spacesError = "" }
                    }
                }
                Flow {
                    visible: root.layers.length > 0
                    width: parent.width; spacing: 8
                    SectionLabel { width: 72; height: 44; verticalAlignment: Text.AlignVCenter; text: "LAYERS" }
                    Repeater {
                        model: root.layers
                        Flow {
                            required property var modelData
                            // Room for one Layer's four controls; narrower screens wrap them.
                            width: Math.min(parent.width, 540); spacing: 4
                            FlowButton {
                                id: layerButton
                                onActiveFocusChanged: if (activeFocus) switcherScroller.ensureVisible(layerButton)
                                width: 180; implicitHeight: 44; selected: modelData.active
                                text: (modelData.app_name || modelData.label).slice(0, 24)
                                Accessible.name: "Focus Layer " + (modelData.app_name || modelData.label)
                                onClicked: root.spaceAction("focus-window", String(modelData.id))
                            }
                            FlowButton {
                                id: layerSpaceButton
                                onActiveFocusChanged: if (activeFocus) switcherScroller.ensureVisible(layerSpaceButton)
                                variant: "quiet"; implicitHeight: 44; text: "Make space"
                                Accessible.name: "Make Layer " + (modelData.app_name || modelData.label) + " its own space"
                                enabled: !spacesProcess.running && !recoveryProcess.running && !root.pendingSpaceActions.length
                                onClicked: root.spaceAction("layer-to-space", String(modelData.id))
                            }
                            FlowButton {
                                id: layerGroupButton
                                onActiveFocusChanged: if (activeFocus) switcherScroller.ensureVisible(layerGroupButton)
                                variant: "quiet"; implicitHeight: 44; text: "Add to Group"
                                Accessible.name: "Add Layer " + (modelData.app_name || modelData.label) + " to the Group beneath it"
                                enabled: !spacesProcess.running && !recoveryProcess.running && !root.pendingSpaceActions.length
                                onClicked: root.spaceAction("layer-to-group", String(modelData.id))
                            }
                            FlowButton {
                                id: layerCloseButton
                                onActiveFocusChanged: if (activeFocus) switcherScroller.ensureVisible(layerCloseButton)
                                variant: "danger"; implicitHeight: 44; text: "Close"
                                Accessible.name: "Close Layer " + (modelData.app_name || modelData.label) + "; the app is asked to close"
                                enabled: !spacesProcess.running && !recoveryProcess.running && !root.pendingSpaceActions.length
                                onClicked: root.spaceAction("close-layer", String(modelData.id))
                            }
                        }
                    }
                }
                Text { visible: root.displaySpaces.length === 0; text: "No open spaces"; color: floeTheme.textMuted; font.pixelSize: 14; font.family: floeTheme.interfaceFont }
                Flickable {
                    id: spaceScroller
                    width: parent.width; height: Math.max(180, windowRow.implicitHeight + 8); contentWidth: windowRow.width + 8; contentHeight: height
                    leftMargin: 4; topMargin: 4
                    boundsBehavior: Flickable.StopAtBounds; clip: true
                    function ensureVisible(item) {
                        switcherScroller.ensureVisible(item)
                        const left = item.mapToItem(windowRow, 0, 0).x
                        if (left < contentX) contentX = left
                        else if (left + item.width > contentX + width) contentX = Math.min(contentWidth - width, left + item.width - width)
                    }
                    Row {
                        id: windowRow; spacing: 12
                        Repeater {
                            model: root.displaySpaces
                            Rectangle {
                                id: spaceCard
                                required property var modelData
                                property var groupPin: root.pins.find(p => p.kind === "group" && p.group_id === modelData.group_id) || null
                                // Card radius 20 with 10 px padding: the controls inside use radius 10.
                                width: cardColumn.width + 20; height: cardColumn.implicitHeight + 20; radius: 20
                                color: floeTheme.surfaceRaised
                                border.width: modelData.active ? 2 : 1
                                // The card is the boundary of its focus button, so it keeps the functional border.
                                border.color: modelData.active ? floeTheme.borderStrong : floeTheme.border
                                Column {
                                    id: cardColumn
                                    x: 10; y: 10
                                    width: spaceCard.modelData.members.length > 1 ? spaceCard.modelData.members.length * 144 - 4 : 200
                                    spacing: 6
                                    FlowButton {
                                        id: spaceButton
                                        onActiveFocusChanged: if (activeFocus) spaceScroller.ensureVisible(spaceButton)
                                        width: parent.width; height: modelData.members.length > 1 ? 52 : 84; radius: 10
                                        variant: "plain"; leftPadding: 10; rightPadding: 10
                                        text: modelData.label
                                        readonly property string meta: [modelData.active ? "Current" : "",
                                            modelData.group_id ? "Group" : modelData.members.length > 1 ? "Stack" : "",
                                            spaceCard.groupPin ? "Pinned" : "", modelData.members.length > 1 ? modelData.members.length + " apps" : ""]
                                            .filter(x => x).join("  ·  ")
                                        contentItem: Item {
                                            implicitHeight: spaceMeta.implicitHeight
                                            Column {
                                                id: spaceMeta
                                                anchors.verticalCenter: parent.verticalCenter; width: parent.width; spacing: 2
                                                Text {
                                                    width: parent.width; text: spaceButton.text; textFormat: Text.PlainText; elide: Text.ElideRight
                                                    color: floeTheme.textPrimary; font.pixelSize: 16; font.weight: Font.DemiBold; font.family: floeTheme.interfaceFont
                                                }
                                                Text {
                                                    visible: spaceButton.meta !== ""
                                                    width: parent.width; text: spaceButton.meta; textFormat: Text.PlainText; elide: Text.ElideRight
                                                    color: modelData.active ? floeTheme.accent : floeTheme.textMuted; font.pixelSize: 12; font.family: floeTheme.interfaceFont
                                                }
                                            }
                                        }
                                        Accessible.name: "Switch to " + modelData.label + (spaceCard.groupPin ? ", pinned Group" : "")
                                        onClicked: root.spaceAction("focus-window", String(modelData.id))
                                    }
                                    Row {
                                        visible: modelData.members.length > 1; spacing: 4
                                        Repeater {
                                            model: spaceCard.modelData.members
                                            Column {
                                                id: memberColumn
                                                required property var modelData
                                                required property int index
                                                property var space: spaceCard.modelData
                                                property bool grouped: !!space.group_id
                                                spacing: 2
                                                FlowButton {
                                                    id: memberButton
                                                    onActiveFocusChanged: if (activeFocus) spaceScroller.ensureVisible(memberButton)
                                                    width: 140; implicitHeight: 44; radius: 10; selected: memberColumn.modelData.active
                                                    text: memberColumn.modelData.label.slice(0, 14)
                                                    Accessible.name: "Focus " + memberColumn.modelData.label + " in group"
                                                    onClicked: root.spaceAction("focus-window", String(memberColumn.modelData.id))
                                                }
                                                Row {
                                                    visible: memberColumn.grouped; spacing: 4
                                                    FlowButton {
                                                        id: memberLeftButton
                                                        onActiveFocusChanged: if (activeFocus) spaceScroller.ensureVisible(memberLeftButton)
                                                        width: 44; implicitHeight: 44; radius: 10; variant: "quiet"; glyph: memberColumn.space.group_layout === "side-by-side" ? "left" : "up"; text: memberColumn.space.group_layout === "side-by-side" ? "‹" : "↑"
                                                        enabled: memberColumn.index > 0 && !memberColumn.space.order_changed && !spacesProcess.running && !recoveryProcess.running && !root.pendingSpaceActions.length
                                                        Accessible.name: "Move " + memberColumn.modelData.label + " earlier in Group " + memberColumn.space.label
                                                        onClicked: root.spaceAction("member-left", String(memberColumn.modelData.id))
                                                    }
                                                    FlowButton {
                                                        id: memberRightButton
                                                        onActiveFocusChanged: if (activeFocus) spaceScroller.ensureVisible(memberRightButton)
                                                        width: 44; implicitHeight: 44; radius: 10; variant: "quiet"; glyph: memberColumn.space.group_layout === "side-by-side" ? "right" : "down"; text: memberColumn.space.group_layout === "side-by-side" ? "›" : "↓"
                                                        enabled: memberColumn.index < memberColumn.space.members.length - 1 && !memberColumn.space.order_changed && !spacesProcess.running && !recoveryProcess.running && !root.pendingSpaceActions.length
                                                        Accessible.name: "Move " + memberColumn.modelData.label + " later in Group " + memberColumn.space.label
                                                        onClicked: root.spaceAction("member-right", String(memberColumn.modelData.id))
                                                    }
                                                    FlowButton {
                                                        id: memberRemoveButton
                                                        onActiveFocusChanged: if (activeFocus) spaceScroller.ensureVisible(memberRemoveButton)
                                                        width: 44; implicitHeight: 44; radius: 10; variant: "danger"; glyph: "close"; text: "×"
                                                        visible: !!memberColumn.space.can_remove
                                                        enabled: !spacesProcess.running && !recoveryProcess.running && !root.pendingSpaceActions.length
                                                        Accessible.name: "Remove " + memberColumn.modelData.label + " from Group " + memberColumn.space.label + "; the app stays open"
                                                        onClicked: root.spaceAction("remove-from-group", String(memberColumn.modelData.id))
                                                    }
                                                }
                                                Row {
                                                    // Width within a side-by-side Group; each step trades width with the next app.
                                                    visible: memberColumn.grouped && memberColumn.space.group_layout === "side-by-side" && !!memberColumn.space.shares
                                                    spacing: 4
                                                    FlowButton {
                                                        id: memberNarrowerButton
                                                        onActiveFocusChanged: if (activeFocus) spaceScroller.ensureVisible(memberNarrowerButton)
                                                        width: 44; implicitHeight: 44; radius: 10; variant: "quiet"; glyph: "minus"; text: "−"
                                                        enabled: !!(memberColumn.space.can_narrow && memberColumn.space.can_narrow[memberColumn.index]) && !spacesProcess.running && !recoveryProcess.running && !root.pendingSpaceActions.length
                                                        Accessible.name: "Make " + memberColumn.modelData.label + " narrower in Group " + memberColumn.space.label
                                                        onClicked: root.spaceAction("member-narrower", String(memberColumn.modelData.id))
                                                    }
                                                    Text {
                                                        width: 44; height: 44
                                                        text: memberColumn.space.shares ? memberColumn.space.shares[memberColumn.index] + "%" : ""
                                                        textFormat: Text.PlainText; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                                                        color: floeTheme.textSecondary; font.pixelSize: 13; font.family: floeTheme.interfaceFont; font.features: { "tnum": 1 }
                                                        Accessible.name: memberColumn.modelData.label + " uses " + text + " of the Group's width"
                                                    }
                                                    FlowButton {
                                                        id: memberWiderButton
                                                        onActiveFocusChanged: if (activeFocus) spaceScroller.ensureVisible(memberWiderButton)
                                                        width: 44; implicitHeight: 44; radius: 10; variant: "quiet"; glyph: "plus"; text: "+"
                                                        enabled: !!(memberColumn.space.can_widen && memberColumn.space.can_widen[memberColumn.index]) && !spacesProcess.running && !recoveryProcess.running && !root.pendingSpaceActions.length
                                                        Accessible.name: "Make " + memberColumn.modelData.label + " wider in Group " + memberColumn.space.label
                                                        onClicked: root.spaceAction("member-wider", String(memberColumn.modelData.id))
                                                    }
                                                }
                                            }
                                        }
                                    }
                                    FlowButton {
                                        id: createGroupButton
                                        visible: spaceCard.modelData.members.length >= 2 && spaceCard.modelData.members.length <= 4 && !spaceCard.modelData.group_id
                                        onActiveFocusChanged: if (activeFocus) spaceScroller.ensureVisible(createGroupButton)
                                        width: parent.width; implicitHeight: 44; radius: 10; text: "Create Group"
                                        Accessible.name: "Create Group from " + spaceCard.modelData.label
                                        enabled: spaceCard.modelData.active && !spacesProcess.running && !recoveryProcess.running && !root.pendingSpaceActions.length
                                        onClicked: root.spaceAction("create-group")
                                    }
                                    FlowButton {
                                        id: addMemberButton
                                        visible: !!spaceCard.modelData.can_add
                                        onActiveFocusChanged: if (activeFocus) spaceScroller.ensureVisible(addMemberButton)
                                        width: parent.width; implicitHeight: 44; radius: 10; text: "Add " + (spaceCard.modelData.add_label || "next app").slice(0, 24) + " to Group"
                                        Accessible.name: "Add the next space, " + spaceCard.modelData.add_label + ", to Group " + spaceCard.modelData.label
                                        enabled: !spacesProcess.running && !recoveryProcess.running && !root.pendingSpaceActions.length
                                        onClicked: root.spaceAction("add-to-group", spaceCard.modelData.group_id)
                                    }
                                    FlowButton {
                                        id: keepOrderButton
                                        visible: !!spaceCard.modelData.order_changed
                                        onActiveFocusChanged: if (activeFocus) spaceScroller.ensureVisible(keepOrderButton)
                                        width: parent.width; implicitHeight: 44; radius: 10; text: "Keep new app order"
                                        Accessible.name: "Keep the new app order for Group " + spaceCard.modelData.label
                                        enabled: !spacesProcess.running && !recoveryProcess.running && !root.pendingSpaceActions.length
                                        onClicked: root.spaceAction("adopt-group-order", spaceCard.modelData.group_id)
                                    }
                                    Grid {
                                        visible: !!spaceCard.modelData.group_id
                                        columns: 2; spacing: 4
                                        FlowButton {
                                            id: renameButton
                                            onActiveFocusChanged: if (activeFocus) spaceScroller.ensureVisible(renameButton)
                                            width: (cardColumn.width - 4) / 2; implicitHeight: 44; radius: 10; leftPadding: 6; rightPadding: 6; variant: "quiet"; text: "Rename"
                                            Accessible.name: "Rename Group " + spaceCard.modelData.label
                                            enabled: !spacesProcess.running && !recoveryProcess.running && !root.pendingSpaceActions.length
                                            onClicked: { groupName.text = spaceCard.modelData.label; root.renameGroupId = spaceCard.modelData.group_id; groupName.forceActiveFocus() }
                                        }
                                        FlowButton {
                                            id: groupViewButton
                                            onActiveFocusChanged: if (activeFocus) spaceScroller.ensureVisible(groupViewButton)
                                            width: (cardColumn.width - 4) / 2; implicitHeight: 44; radius: 10; leftPadding: 6; rightPadding: 6; variant: "quiet"; text: "Tabs / stack"
                                            visible: spaceCard.modelData.group_layout !== "side-by-side"
                                            enabled: spaceCard.modelData.active && !spacesProcess.running && !recoveryProcess.running && !root.pendingSpaceActions.length
                                            Accessible.name: "Toggle tabs or stack for " + spaceCard.modelData.label
                                            onClicked: root.spaceAction("group-view", spaceCard.modelData.group_id)
                                        }
                                        FlowButton {
                                            id: groupLayoutButton
                                            visible: !!spaceCard.modelData.group_id
                                            onActiveFocusChanged: if (activeFocus) spaceScroller.ensureVisible(groupLayoutButton)
                                            width: (cardColumn.width - 4) / 2; implicitHeight: 44; radius: 10; leftPadding: 6; rightPadding: 6; variant: "quiet"
                                            text: spaceCard.modelData.group_layout === "side-by-side" ? "Stack" : "Side by side"
                                            Accessible.name: text + " Group " + spaceCard.modelData.label
                                            enabled: spaceCard.modelData.active && !spacesProcess.running && !recoveryProcess.running && !root.pendingSpaceActions.length
                                            onClicked: root.spaceAction("group-layout", spaceCard.modelData.group_id)
                                        }
                                        FlowButton {
                                            id: autoLayoutButton
                                            visible: !!spaceCard.modelData.group_id
                                            onActiveFocusChanged: if (activeFocus) spaceScroller.ensureVisible(autoLayoutButton)
                                            width: (cardColumn.width - 4) / 2; implicitHeight: 44; radius: 10; leftPadding: 6; rightPadding: 6; variant: "quiet"
                                            text: spaceCard.modelData.auto_layout ? "Auto layout: on" : "Auto layout: off"
                                            Accessible.name: text + " for " + spaceCard.modelData.label
                                            enabled: spaceCard.modelData.active && !spacesProcess.running && !recoveryProcess.running && !root.pendingSpaceActions.length
                                            onClicked: root.spaceAction("toggle-auto-layout", spaceCard.modelData.group_id)
                                        }
                                        FlowButton {
                                            id: groupPinButton
                                            visible: !!spaceCard.modelData.group_id
                                            onActiveFocusChanged: if (activeFocus) spaceScroller.ensureVisible(groupPinButton)
                                            width: (cardColumn.width - 4) / 2; implicitHeight: 44; radius: 10; leftPadding: 6; rightPadding: 6; variant: "quiet"
                                            text: spaceCard.groupPin ? "Unpin Group" : spaceCard.modelData.group_layout === "side-by-side" ? "Stack to pin" : "Pin Group"
                                            Accessible.name: text + " " + spaceCard.modelData.label
                                            enabled: (spaceCard.groupPin || spaceCard.modelData.group_layout !== "side-by-side") && !spacesProcess.running && !recoveryProcess.running && !root.pendingSpaceActions.length
                                            onClicked: root.spaceAction(spaceCard.groupPin ? "unpin" : "pin-group", spaceCard.groupPin ? spaceCard.groupPin.id : spaceCard.modelData.group_id)
                                        }
                                        FlowButton {
                                            id: dissolveGroupButton
                                            visible: !!spaceCard.modelData.group_id
                                            onActiveFocusChanged: if (activeFocus) spaceScroller.ensureVisible(dissolveGroupButton)
                                            width: (cardColumn.width - 4) / 2; implicitHeight: 44; radius: 10; leftPadding: 6; rightPadding: 6; variant: "danger"
                                            text: spaceCard.groupPin ? "Unpin first" : "Dissolve"
                                            Accessible.name: (spaceCard.groupPin ? "Unpin first to dissolve Group " : "Dissolve Group ") + spaceCard.modelData.label
                                            enabled: spaceCard.modelData.active && !spaceCard.groupPin && !spacesProcess.running && !recoveryProcess.running && !root.pendingSpaceActions.length
                                            onClicked: root.spaceAction("dissolve-group", spaceCard.modelData.group_id)
                                        }
                                    }
                                    Repeater {
                                        // Moving acts on the active space, so the choice appears on that card only.
                                        model: spaceCard.modelData.active ? root.displays.filter(d => !d.current) : []
                                        FlowButton {
                                            id: moveDisplayButton
                                            required property var modelData
                                            onActiveFocusChanged: if (activeFocus) spaceScroller.ensureVisible(moveDisplayButton)
                                            width: cardColumn.width; implicitHeight: 44; radius: 10; variant: "quiet"
                                            text: "Move to " + modelData.label.slice(0, 22)
                                            Accessible.name: "Move " + spaceCard.modelData.label + (spaceCard.modelData.group_id ? ", the whole Group," : "") + " to display " + modelData.label
                                            enabled: !spacesProcess.running && !recoveryProcess.running && !root.pendingSpaceActions.length
                                            onClicked: root.spaceAction("move-to-display", modelData.key)
                                        }
                                    }
                                    Row {
                                        visible: !!spaceCard.modelData.group_id && root.renameGroupId === spaceCard.modelData.group_id
                                        width: parent.width; spacing: 4
                                        Keys.onEscapePressed: { root.renameGroupId = ""; renameButton.forceActiveFocus() }
                                        TextField {
                                            id: groupName
                                            onActiveFocusChanged: if (activeFocus) spaceScroller.ensureVisible(groupName)
                                            width: parent.width - 136; height: 44; maximumLength: 80
                                            leftPadding: 10; rightPadding: 10
                                            color: floeTheme.textPrimary; placeholderTextColor: floeTheme.textMuted
                                            selectionColor: floeTheme.accent; selectedTextColor: floeTheme.onAccent
                                            font.pixelSize: 14; font.family: floeTheme.interfaceFont
                                            background: Rectangle {
                                                radius: 10; color: floeTheme.surface
                                                border.width: groupName.activeFocus ? 2 : 1
                                                border.color: groupName.activeFocus ? floeTheme.focus : floeTheme.border
                                            }
                                            text: spaceCard.modelData.label
                                            placeholderText: "Group name"
                                            Accessible.name: "Group name"
                                            onAccepted: root.renameGroup(spaceCard.modelData.group_id, text)
                                        }
                                        FlowButton { id: saveGroupName; onActiveFocusChanged: if (activeFocus) spaceScroller.ensureVisible(saveGroupName); width: 64; implicitHeight: 44; radius: 10; variant: "primary"; text: "Save"; Accessible.name: "Save name for Group " + spaceCard.modelData.label; enabled: !spacesProcess.running && !recoveryProcess.running && !root.pendingSpaceActions.length; onClicked: root.renameGroup(spaceCard.modelData.group_id, groupName.text) }
                                        FlowButton { id: cancelGroupName; onActiveFocusChanged: if (activeFocus) spaceScroller.ensureVisible(cancelGroupName); width: 64; implicitHeight: 44; radius: 10; variant: "quiet"; text: "Cancel"; Accessible.name: "Cancel rename for Group " + spaceCard.modelData.label; onClicked: { root.renameGroupId = ""; renameButton.forceActiveFocus() } }
                                    }
                                }
                            }
                        }
                    }
                }
                Column {
                    visible: root.incompleteGroups.length > 0
                    width: parent.width; spacing: 8
                    SectionLabel { text: "GROUPS NEEDING ATTENTION"; topPadding: 4 }
                    Repeater {
                        model: root.incompleteGroups
                        Rectangle {
                            id: attentionCard
                            required property var modelData
                            width: Math.min(parent.width, 720); height: attentionColumn.implicitHeight + 24; radius: 20
                            color: floeTheme.surfaceRaised; border.width: 1; border.color: Qt.alpha(floeTheme.warning, 0.55)
                            Column {
                                id: attentionColumn
                                x: 12; y: 12; width: parent.width - 24; spacing: 6
                                Text {
                                    width: parent.width; text: modelData.label; textFormat: Text.PlainText
                                    color: floeTheme.textPrimary; font.weight: Font.DemiBold; font.pixelSize: 16; font.family: floeTheme.interfaceFont
                                    wrapMode: Text.WrapAnywhere
                                }
                                Text {
                                    width: parent.width; text: modelData.status; textFormat: Text.PlainText
                                    color: floeTheme.textMuted; font.pixelSize: 13; font.family: floeTheme.interfaceFont
                                    wrapMode: Text.WrapAnywhere
                                }
                                Repeater {
                                    // Several windows of a member app: the user picks the one in the Group.
                                    model: modelData.choices || []
                                    Column {
                                        id: memberChoice
                                        required property var modelData
                                        property string groupId: attentionCard.modelData.id
                                        width: parent.width; spacing: 4
                                        Text {
                                            width: parent.width; wrapMode: Text.Wrap; textFormat: Text.PlainText
                                            text: "Which " + memberChoice.modelData.app_name + " window belongs to this Group?"
                                            color: floeTheme.textSecondary; font.pixelSize: 14; font.family: floeTheme.interfaceFont
                                        }
                                        Flow {
                                            width: parent.width; spacing: 4
                                            Repeater {
                                                model: memberChoice.modelData.windows
                                                FlowButton {
                                                    id: chooseWindowButton
                                                    required property var modelData
                                                    width: Math.min(memberChoice.width, 220); implicitHeight: 44; radius: 10
                                                    text: modelData.label.slice(0, 28)
                                                    Accessible.name: "Use " + modelData.label + " as the Group's " + memberChoice.modelData.app_name + " window"
                                                    enabled: !spacesProcess.running && !recoveryProcess.running && !root.pendingSpaceActions.length
                                                    onActiveFocusChanged: if (activeFocus) switcherScroller.ensureVisible(chooseWindowButton)
                                                    onClicked: root.spaceAction("choose-member", memberChoice.groupId + ":" + modelData.id)
                                                }
                                            }
                                        }
                                    }
                                }
                                FlowButton {
                                    id: forgetGroupChoice
                                    visible: !!modelData.can_forget_choice
                                    width: Math.min(parent.width, 200); implicitHeight: 44; radius: 10; variant: "quiet"; text: "Choose again"
                                    Accessible.name: "Forget the windows chosen for " + modelData.label + " and choose again"
                                    enabled: !spacesProcess.running && !recoveryProcess.running && !root.pendingSpaceActions.length
                                    onActiveFocusChanged: if (activeFocus) switcherScroller.ensureVisible(forgetGroupChoice)
                                    onClicked: root.spaceAction("forget-choice", modelData.id)
                                }
                                FlowButton {
                                    id: openGroupMember
                                    visible: modelData.can_open
                                    width: Math.min(parent.width, 200); implicitHeight: 44; radius: 10; text: "Open missing app"
                                    Accessible.name: "Open missing app for " + modelData.label
                                    enabled: !spacesProcess.running && !recoveryProcess.running && !root.pendingSpaceActions.length
                                    onActiveFocusChanged: if (activeFocus) switcherScroller.ensureVisible(openGroupMember)
                                    onClicked: root.spaceAction("open-group-member", modelData.id)
                                }
                                FlowButton {
                                    id: rejoinGroup
                                    visible: modelData.can_rejoin
                                    width: Math.min(parent.width, 200); implicitHeight: 44; radius: 10; text: "Rejoin Group"
                                    Accessible.name: "Rejoin Group for " + modelData.label
                                    enabled: !spacesProcess.running && !recoveryProcess.running && !root.pendingSpaceActions.length
                                    onActiveFocusChanged: if (activeFocus) switcherScroller.ensureVisible(rejoinGroup)
                                    onClicked: root.spaceAction("rejoin-group", modelData.id)
                                }
                                FlowButton {
                                    id: resumeGroupLayout
                                    visible: modelData.can_resume_layout
                                    width: Math.min(parent.width, 220); implicitHeight: 44; radius: 10; text: "Finish layout change"
                                    Accessible.name: "Finish layout change for Group " + modelData.label
                                    enabled: !spacesProcess.running && !recoveryProcess.running && !root.pendingSpaceActions.length
                                    onActiveFocusChanged: if (activeFocus) switcherScroller.ensureVisible(resumeGroupLayout)
                                    onClicked: root.spaceAction("resume-group-layout", modelData.id)
                                }
                                FlowButton {
                                    id: adoptGroupOrder
                                    visible: modelData.can_adopt_order
                                    width: Math.min(parent.width, 220); implicitHeight: 44; radius: 10; text: "Keep current order"
                                    Accessible.name: "Keep the current app order for Group " + modelData.label
                                    enabled: !spacesProcess.running && !recoveryProcess.running && !root.pendingSpaceActions.length
                                    onActiveFocusChanged: if (activeFocus) switcherScroller.ensureVisible(adoptGroupOrder)
                                    onClicked: root.spaceAction("adopt-group-order", modelData.id)
                                }
                                FlowButton {
                                    id: repairGroupLayout
                                    visible: modelData.can_repair
                                    width: Math.min(parent.width, 220); implicitHeight: 44; radius: 10; text: "Repair Group layout"
                                    Accessible.name: "Repair layout for Group " + modelData.label
                                    enabled: !spacesProcess.running && !recoveryProcess.running && !root.pendingSpaceActions.length
                                    onActiveFocusChanged: if (activeFocus) switcherScroller.ensureVisible(repairGroupLayout)
                                    onClicked: root.spaceAction("repair-side-group", modelData.id)
                                }
                            }
                        }
                    }
                }
            }
            }
        }
    }
    // shortcut: only published accessibility menus are shown; add toolkit-specific bridges when verified.
    PanelWindow {
        id: appMenuTrigger
        visible: menuPreferences.appMenuEnabled && !root.deskOpen && !root.overviewOpen && root.appMenu && !root.appMenuOpen
        anchors { top: true; left: true }
        implicitWidth: 220; implicitHeight: 44; margins.left: 8
        // Leave 8px between the left trigger and the centered Island on the shared row.
        margins.top: screen && screen.width < workIslandContent.implicitWidth + 472 ? 60 : 8
        color: "transparent"; exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        FlowButton {
            anchors.fill: parent
            text: root.appMenu ? root.appMenu.name + "  ▾" : "Application"
            onClicked: root.requestAppMenu()
        }
    }
    PanelWindow {
        visible: menuPreferences.appMenuEnabled && root.appMenuOpen && !root.deskOpen && !root.overviewOpen
        anchors { top: true; bottom: true; left: true; right: true }
        color: "transparent"; exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive
        MouseArea { anchors.fill: parent; onClicked: root.appMenuOpen = false }
        FocusScope {
            anchors.fill: parent; focus: true
            Keys.onEscapePressed: root.appMenuOpen = false
            Rectangle {
                x: 8; y: appMenuTrigger.margins.top + appMenuTrigger.implicitHeight + 8
                width: Math.min(390, parent.width - 16); height: Math.min(menuColumn.implicitHeight + 32, parent.height - y - 8)
                radius: 16; color: floeTheme.surface; border.color: floeTheme.border
                Flickable {
                    anchors.fill: parent; anchors.margins: 16
                    contentWidth: width; contentHeight: menuColumn.implicitHeight; clip: true
                    Column {
                        id: menuColumn; width: parent.width; spacing: 8
                        Text { text: root.appMenu ? root.appMenu.name : "Application"; textFormat: Text.PlainText; color: floeTheme.textPrimary; font.bold: true; font.pixelSize: 18 }
                        Text { visible: root.appMenuError !== ""; text: root.appMenuError; textFormat: Text.PlainText; color: floeTheme.danger; width: parent.width; wrapMode: Text.Wrap }
                        Text { visible: root.appMenuFocusChanged; text: "Application focus changed. Close this menu to update it."; color: floeTheme.danger; width: parent.width; wrapMode: Text.Wrap }
                        Text {
                            visible: !root.appMenu || root.currentAppItems().length === 0
                            text: "No exposed app commands. Use the application's own menu."
                            color: floeTheme.textMuted; width: parent.width; wrapMode: Text.Wrap
                        }
                        FlowButton {
                            visible: root.appMenuPath.length > 0
                            width: menuColumn.width; text: "‹ Back"
                            onClicked: root.appMenuPath = root.appMenuPath.slice(0, -1)
                        }
                        Repeater {
                            model: root.currentAppItems()
                            FlowButton {
                                required property var modelData
                                required property int index
                                width: menuColumn.width
                                text: modelData.label + (modelData.children ? "   ›" : "")
                                enabled: modelData.enabled && !appMenuProcess.running && !root.appMenuFocusChanged
                                onClicked: {
                                    if (modelData.children) root.appMenuPath = root.appMenuPath.concat([index])
                                    else {
                                        appMenuProcess.command = ["flow-appmenu", "activate", String(root.appMenu.id), JSON.stringify(modelData.path), modelData.label]
                                        appMenuProcess.running = true
                                    }
                                }
                            }
                        }
                        FlowButton { width: menuColumn.width; text: "Close menu"; onClicked: root.appMenuOpen = false }
                    }
                }
            }
        }
    }
    PanelWindow {
        visible: root.quickSettingsOpen
        anchors.top: true
        implicitWidth: Math.max(0, Math.min(340, screen.width - 24)); implicitHeight: Math.min(screen.height - 80, Math.max(235, quickSettingsColumn.implicitHeight + 40)); margins.top: 60
        color: "transparent"; exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive
        Rectangle {
            // Panels hang from the island: radius 14, hairline edge, the surface tone.
            anchors.fill: parent; radius: 14
            color: floeTheme.surface; border.width: 1; border.color: Qt.alpha(floeTheme.border, 0.5)
        }
        FocusScope {
            anchors.fill: parent; focus: true
            Keys.onEscapePressed: root.quickSettingsOpen = false
            ScrollView {
                anchors.fill: parent; anchors.margins: 16; anchors.topMargin: 20; clip: true
                contentWidth: availableWidth; contentHeight: quickSettingsColumn.implicitHeight
                Column {
                id: quickSettingsColumn
                // 4 px inside the clip keeps focus rings whole.
                x: 4; width: parent.width - 8; topPadding: 4; bottomPadding: 4; spacing: 10
                SectionLabel { text: "NETWORK" }
                Text { text: root.networkStatus; color: floeTheme.textPrimary; font.pixelSize: 16; font.weight: Font.DemiBold; font.family: floeTheme.interfaceFont }
                FlowButton { width: parent.width; implicitHeight: 44; text: "Choose Wi-Fi network"; onClicked: { root.quickSettingsOpen = false; root.launchApp(["flow-wifi-settings"]) } }
                FlowButton { width: parent.width; implicitHeight: 44; variant: "quiet"; text: "Toggle on-screen keyboard"; Accessible.name: "Toggle on-screen keyboard"; onClicked: { root.launchApp(["flow-keyboard"]); root.quickSettingsOpen = false } }
                SectionLabel { text: "SOUND"; topPadding: 8 }
                Text { text: root.volumeStatus; color: floeTheme.textPrimary; font.pixelSize: 16; font.weight: Font.DemiBold; font.family: floeTheme.interfaceFont; font.features: { "tnum": 1 } }
                Flow {
                    width: parent.width; spacing: 6
                    FlowButton { width: 70; implicitHeight: 44; text: "− 5%"; Accessible.name: "Decrease volume"; enabled: root.audioAvailable; onClicked: root.changeVolume(-0.05) }
                    FlowButton { width: 70; implicitHeight: 44; text: "+ 5%"; Accessible.name: "Increase volume"; enabled: root.audioAvailable; onClicked: root.changeVolume(0.05) }
                    FlowButton { width: Math.max(70, parent.width - 152); implicitHeight: 44; variant: "quiet"; text: root.audioAvailable && root.audioSink.audio.muted ? "Unmute" : "Mute"; enabled: root.audioAvailable; onClicked: root.changeVolume("mute") }
                }
                Row {
                    visible: !!root.mediaPlayer
                    width: parent.width; spacing: 8
                    Column {
                        width: parent.width - mediaToggle.width - parent.spacing; spacing: 2
                        Text {
                            width: parent.width; maximumLineCount: 1; wrapMode: Text.NoWrap; elide: Text.ElideRight
                            text: root.mediaPlayer ? (root.mediaPlayer.trackTitle || root.mediaPlayer.identity || "Media player").slice(0, 160) : ""
                            textFormat: Text.PlainText; color: floeTheme.textPrimary; font.weight: Font.DemiBold; font.pixelSize: 14; font.family: floeTheme.interfaceFont
                        }
                        Text {
                            visible: !!(root.mediaPlayer && root.mediaPlayer.trackArtist)
                            width: parent.width; maximumLineCount: 1; wrapMode: Text.NoWrap; elide: Text.ElideRight
                            text: root.mediaPlayer ? root.mediaPlayer.trackArtist.slice(0, 160) : ""
                            textFormat: Text.PlainText; color: floeTheme.textMuted; font.pixelSize: 12
                        }
                    }
                    FlowButton {
                        id: mediaToggle; width: 76; implicitHeight: 44
                        text: root.mediaPlayer && root.mediaPlayer.isPlaying ? "Pause" : "Play"
                        Accessible.name: root.mediaPlayer && root.mediaPlayer.isPlaying ? "Pause media" : "Play media"
                        enabled: !!(root.mediaPlayer && root.mediaPlayer.canTogglePlaying)
                        onClicked: if (root.mediaPlayer && root.mediaPlayer.canTogglePlaying) root.mediaPlayer.togglePlaying()
                    }
                }
                Text { visible: root.quickError !== ""; text: root.quickError; color: floeTheme.danger; font.pixelSize: 12; font.family: floeTheme.interfaceFont; width: parent.width; wrapMode: Text.Wrap }
                Rectangle { width: parent.width; height: 1; color: Qt.alpha(floeTheme.border, 0.35) }
                Flow {
                    width: parent.width; spacing: 6
                    FlowButton { implicitHeight: 44; variant: "quiet"; text: "Edit connections"; onClicked: { root.quickSettingsOpen = false; root.launchApp(["nm-connection-editor"]) } }
                    FlowButton { implicitHeight: 44; variant: "quiet"; text: "Close"; onClicked: root.quickSettingsOpen = false }
                }
                }
            }
        }
    }
    PanelWindow {
        visible: root.notificationsOpen
        anchors.top: true
        implicitWidth: Math.min(420, screen.width - 24); implicitHeight: Math.min(notificationColumn.implicitHeight + 24, screen.height - 80)
        margins.top: 60
        color: "transparent"; exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: root.notificationsOpen ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None
        Rectangle {
            // Panels hang from the island: radius 14, hairline edge, the surface tone.
            anchors.fill: parent; radius: 14
            color: floeTheme.surface; border.width: 1; border.color: Qt.alpha(floeTheme.border, 0.5)
        }
        FocusScope {
            anchors.fill: parent; focus: root.notificationsOpen
            Keys.onEscapePressed: root.notificationsOpen = false
            ScrollView {
            anchors.fill: parent; anchors.margins: 8; clip: true
            contentWidth: availableWidth; contentHeight: notificationColumn.implicitHeight
            Column {
                id: notificationColumn; x: 4; width: parent.width - 8; topPadding: 4; bottomPadding: 4; spacing: 8
                Row {
                    width: parent.width; spacing: 8
                    SectionLabel { leftPadding: 4; width: parent.width - 88; text: "NOTIFICATIONS"; anchors.verticalCenter: parent.verticalCenter }
                    FlowButton { width: 80; implicitHeight: 44; text: "Close"; variant: "quiet"; onClicked: root.notificationsOpen = false }
                }
                Text {
                    visible: notificationServer.trackedNotifications.values.length === 0
                    width: parent.width; height: 88; verticalAlignment: Text.AlignVCenter; horizontalAlignment: Text.AlignHCenter
                    text: "No notifications"; color: floeTheme.textMuted; font.pixelSize: 14; font.family: floeTheme.interfaceFont
                }
                Repeater {
                    model: notificationServer.trackedNotifications
                    delegate: Rectangle {
                        required property var modelData
                        required property int index
                        visible: root.notificationsOpen || index === notificationServer.trackedNotifications.values.length - 1
                        width: notificationColumn.width; implicitHeight: cardColumn.implicitHeight + 20
                        // Panel radius 14, inset 8: radius 6.
                        radius: 6; color: floeTheme.surfaceRaised; border.color: Qt.alpha(floeTheme.border, 0.45)
                        Column {
                            id: cardColumn; anchors.fill: parent; anchors.margins: 12; anchors.rightMargin: 4; spacing: 4
                            Row {
                                width: parent.width; spacing: 8
                                Text { width: parent.width - dismiss.width - 8; text: modelData.appName + " · " + modelData.summary; textFormat: Text.PlainText; color: floeTheme.textPrimary; font.weight: Font.DemiBold; font.pixelSize: 14; font.family: floeTheme.interfaceFont; wrapMode: Text.Wrap; height: Math.max(44, implicitHeight); verticalAlignment: Text.AlignVCenter }
                                FlowButton { id: dismiss; width: 44; implicitHeight: 44; variant: "quiet"; glyph: "close"; text: "×"; Accessible.name: "Dismiss notification"; onClicked: modelData.dismiss() }
                            }
                            Text { visible: modelData.body !== ""; width: parent.width; text: modelData.body; textFormat: Text.PlainText; color: floeTheme.textSecondary; wrapMode: Text.Wrap; font.pixelSize: 14; font.family: floeTheme.interfaceFont; rightPadding: 8 }
                            Flow {
                                width: parent.width; spacing: 6
                                Repeater {
                                    model: modelData.actions
                                    delegate: FlowButton {
                                        required property var modelData
                                        width: Math.min(200, implicitWidth); implicitHeight: 44
                                        text: modelData.text; Accessible.name: modelData.text
                                        onClicked: modelData.invoke()
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        }
    }
    PanelWindow {
        id: workIsland
        visible: !root.deskOpen
        anchors.top: true
        implicitWidth: workIslandContent.implicitWidth; implicitHeight: workIslandContent.implicitHeight; margins.top: 8
        color: "transparent"
        exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        SystemIsland { id: workIslandContent; anchors.fill: parent; room: workIsland.screen ? workIsland.screen.width : 1280 }
    }
    PanelWindow {
        // Temporary feedback for shortcuts and Group focus changes; it never takes keyboard focus.
        visible: root.toast !== ""
        anchors.bottom: true
        // Above the bottom controls (52 + 8), and above the raised work indicator on narrow screens.
        margins.bottom: screen && screen.width < 560 ? 128 : 68
        implicitWidth: toastText.implicitWidth + 36; implicitHeight: 44
        color: "transparent"
        exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
        Rectangle {
            // The capsule belongs to the island and its bars; feedback is a panel.
            anchors.fill: parent; radius: 14
            color: floeTheme.surfaceRaised; border.width: 1; border.color: floeTheme.borderStrong
            Text {
                id: toastText
                anchors.centerIn: parent
                text: root.toast; textFormat: Text.PlainText
                color: floeTheme.textPrimary; font.pixelSize: 14; font.family: floeTheme.interfaceFont
                Accessible.role: Accessible.AlertMessage; Accessible.name: text
            }
        }
    }
}
