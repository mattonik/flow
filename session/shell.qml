import QtQuick
import QtQuick.Controls
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
            switcherOpen = false; deskOpen = false
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
        command: ["niri", "msg", "-j", "event-stream"]
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
    property string renameGroupId: ""
    property bool switcherOpen: false
    onSwitcherOpenChanged: if (!switcherOpen) renameGroupId = ""
    property var windows: []
    property var displaySpaces: []
    property var pins: []
    property string spacesError: ""
    property var pendingSpaceActions: []
    function renameGroup(id, label) {
        if (recoveryProcess.running || spacesProcess.running) { spacesError = "Wait for the current action"; return }
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
                        if (spacesProcess.command[1] !== "refresh" && !root.switcherOpen) root.deskOpen = true
                    } else {
                        if (JSON.stringify(root.windows) !== JSON.stringify(result.windows)) root.windows = result.windows
                        if (JSON.stringify(root.displaySpaces) !== JSON.stringify(result.spaces)) root.displaySpaces = result.spaces
                        if (JSON.stringify(root.pins) !== JSON.stringify(result.pins)) root.pins = result.pins
                        if (result.action !== "refresh") root.spacesError = ""
                        if (result.action === "focus" || result.action === "focus-window") { root.deskOpen = false; root.switcherOpen = false }
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
        id: refreshAfterEvent
        interval: 150
        onTriggered: {
            if (spacesProcess.running || recoveryProcess.running || root.pendingSpaceActions.length) restart()
            else root.spaceAction("refresh")
        }
    }
    Timer { interval: niriEvents.running ? 30000 : 3000; running: true; repeat: true; triggeredOnStart: true; onTriggered: root.spaceAction("refresh") }
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

    function openSearch() {
        notificationsOpen = false
        quickSettingsOpen = false
        openMenuWhenReady = false
        appMenuOpen = false
        deskOpen = false
        switcherOpen = false
        Quickshell.execDetached(["flow-search"])
    }

    function openApps() {
        notificationsOpen = false
        quickSettingsOpen = false
        openMenuWhenReady = false
        appMenuOpen = false
        switcherOpen = false
        deskOpen = false
        Quickshell.execDetached(["flow-apps"])
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

    component FlowButton: Button {
        id: control
        focusPolicy: Qt.StrongFocus
        Keys.onReturnPressed: control.clicked()
        Keys.onEnterPressed: control.clicked()
        implicitHeight: 58
        contentItem: Text {
            text: control.text; textFormat: Text.PlainText; color: "#d7e5df"; font.pixelSize: 14; elide: Text.ElideRight
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
        function focus(index: int): void {
            if (index > 0 && index <= root.pins.length) root.spaceAction("focus", root.pins[index - 1].id)
            else { root.spacesError = "No pinned space at that position"; root.deskOpen = true }
        }
        function toggle(): void { root.toggleDesk() }
        function switcher(): void { root.toggleSwitcher() }
        function overview(): void {
            const action = root.deskOpen ? "open-overview" : "toggle-overview"
            root.deskOpen = false
            root.switcherOpen = false
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
                    spacing: 16
                    Text { text: "●  FLOW / YOUR DESK"; color: "#a0d9c0"; font.bold: true; font.letterSpacing: 2; font.pixelSize: 13 }
                    Text {
                        text: "Everything<br>in its <font color='#9fe2c5'>place.</font>"
                        textFormat: Text.RichText
                        color: "#eff5f2"; font.bold: true
                        font.pixelSize: Math.max(36, Math.min(72, parent.width / 9))
                        lineHeight: 0.95
                    }
                    Text { text: "Your tools are here. Your work is right where you left it."; color: "#9baea7"; font.pixelSize: 17; wrapMode: Text.Wrap; width: parent.width }
                    FlowButton {
                        id: searchButton
                        onActiveFocusChanged: if (activeFocus) deskScroller.ensureVisible(searchButton)
                        width: Math.min(parent.width, 590)
                        text: "⌕   Search apps and open spaces   ·   Super K"
                        onClicked: root.openSearch()
                    }
                    Text { text: "PINNED SPACES"; color: "#91a59d"; font.bold: true; font.letterSpacing: 1.5; font.pixelSize: 12 }
                    Text { visible: root.pins.length === 0; text: "Pin the current space to keep its place"; color: "#80948b"; font.pixelSize: 14 }
                    Flow {
                        width: parent.width
                        spacing: 8
                        Repeater {
                            model: root.pins
                            Flow {
                                required property var modelData
                                required property int index
                                width: Math.min(parent.width, root.editPins ? 321 : 180)
                                spacing: 3
                                FlowButton {
                                    id: pinnedSpaceButton
                                    onActiveFocusChanged: if (activeFocus) deskScroller.ensureVisible(pinnedSpaceButton)
                                    width: 180; text: (index + 1) + "  " + (modelData.label.length > 15 ? modelData.label.slice(0, 15) + "…" : modelData.label) + (modelData.available ? "" : modelData.kind === "group" ? " · Unavailable" : " · Open")
                                    enabled: !spacesProcess.running || spacesProcess.command[1] === "refresh"
                                    Accessible.name: (modelData.available ? "Focus " : modelData.kind === "group" ? "Unavailable Group " : "Open ") + modelData.label
                                    onClicked: root.spaceAction("focus", modelData.id)
                                }
                                FlowButton { id: pinLeftButton; onActiveFocusChanged: if (activeFocus) deskScroller.ensureVisible(pinLeftButton); visible: root.editPins; width: 44; text: "‹"; Accessible.name: "Move " + modelData.label + " left"; enabled: !spacesProcess.running || spacesProcess.command[1] === "refresh"; onClicked: root.spaceAction("left", modelData.id) }
                                FlowButton { id: pinRightButton; onActiveFocusChanged: if (activeFocus) deskScroller.ensureVisible(pinRightButton); visible: root.editPins; width: 44; text: "›"; Accessible.name: "Move " + modelData.label + " right"; enabled: !spacesProcess.running || spacesProcess.command[1] === "refresh"; onClicked: root.spaceAction("right", modelData.id) }
                                FlowButton { id: unpinButton; onActiveFocusChanged: if (activeFocus) deskScroller.ensureVisible(unpinButton); visible: root.editPins; width: 44; text: "×"; enabled: !spacesProcess.running || spacesProcess.command[1] === "refresh"; Accessible.name: "Unpin " + modelData.label; onClicked: root.spaceAction("unpin", modelData.id) }
                            }
                        }
                    }
                    Flow {
                        width: parent.width
                        spacing: 8
                        FlowButton { id: pinCurrentButton; onActiveFocusChanged: if (activeFocus) deskScroller.ensureVisible(pinCurrentButton); width: 200; text: "Pin current space · Super P"; enabled: !spacesProcess.running || spacesProcess.command[1] === "refresh"; onClicked: root.spaceAction("pin") }
                        FlowButton { id: editPinsButton; onActiveFocusChanged: if (activeFocus) deskScroller.ensureVisible(editPinsButton); visible: root.pins.length > 0; width: 100; text: root.editPins ? "Done" : "Edit pins"; onClicked: root.editPins = !root.editPins }
                    }
                    Row {
                        visible: root.spacesError !== ""; width: parent.width; spacing: 8
                        Text { text: root.spacesError; textFormat: Text.PlainText; color: "#f1baaa"; wrapMode: Text.Wrap; width: parent.width - dismissSpacesError.width - 8; font.pixelSize: 14 }
                        FlowButton { id: dismissSpacesError; width: 96; implicitHeight: 44; text: "Dismiss"; Accessible.name: "Dismiss spaces error"; onClicked: root.spacesError = "" }
                    }
                    Text { text: "TOOLS"; color: "#91a59d"; font.bold: true; font.letterSpacing: 1.5; font.pixelSize: 12 }
                    Flow {
                        width: parent.width
                        spacing: 12
                        Repeater {
                            model: ["Apps", "Files", "Overview", "Notifications", "Settings", "Return to work"]
                            FlowButton {
                                id: toolButton
                                required property string modelData
                                onActiveFocusChanged: if (activeFocus) deskScroller.ensureVisible(toolButton)
                                width: modelData === "Return to work" ? 145 : modelData === "Notifications" ? 130 : 100
                                text: modelData
                                onClicked: {
                                    if (modelData === "Settings") {
                                        root.settingsOpen = !root.settingsOpen
                                        if (root.settingsOpen) Qt.callLater(function() { deskScroller.ensureVisible(menuSwitch) })
                                        return
                                    }
                                    root.deskOpen = false
                                    if (modelData === "Apps") root.openApps()
                                    else if (modelData === "Notifications") root.toggleNotifications()
                                    else if (modelData === "Files") Quickshell.execDetached(["nautilus"])
                                    else if (modelData === "Overview") Quickshell.execDetached(["niri", "msg", "action", "open-overview"])
                                }
                            }
                        }
                    }
                    Column {
                        visible: root.settingsOpen; width: parent.width; spacing: 12
                        Text { text: "SETTINGS"; color: "#91a59d"; font.bold: true; font.letterSpacing: 1.5; font.pixelSize: 12 }
                        Row {
                            spacing: 12
                            Switch {
                                id: menuSwitch
                                checked: menuPreferences.appMenuEnabled
                                Accessible.name: "Experimental application menu"
                                onActiveFocusChanged: if (activeFocus) deskScroller.ensureVisible(menuSwitch)
                                onToggled: { menuPreferences.appMenuEnabled = checked; menuPreferences.sync(); root.appMenuOpen = false }
                            }
                            Text { anchors.verticalCenter: parent.verticalCenter; text: "Experimental application menu"; color: "#d7e5df"; font.pixelSize: 14 }
                        }
                        Text { text: "SESSION RECOVERY"; color: "#91a59d"; font.bold: true; font.letterSpacing: 1.5; font.pixelSize: 12 }
                        Text { text: "Save your documents, then save the current window list before restarting."; color: "#9baea7"; width: parent.width; wrapMode: Text.Wrap }
                        Flow {
                            width: parent.width; spacing: 8
                            FlowButton { id: saveSession; width: 180; text: "Save session"; enabled: !recoveryProcess.running && !spacesProcess.running; onActiveFocusChanged: if (activeFocus) deskScroller.ensureVisible(saveSession); onClicked: { recoveryProcess.command = ["flow-recovery", "save"]; recoveryProcess.running = true } }
                            FlowButton { id: restoreSession; width: 180; text: "Restore session"; enabled: !recoveryProcess.running && !spacesProcess.running; onActiveFocusChanged: if (activeFocus) deskScroller.ensureVisible(restoreSession); onClicked: { root.recoveryMessage = "Restoring applications…"; recoveryProcess.command = ["flow-recovery", "restore"]; recoveryProcess.running = true } }
                        }
                        Text { visible: root.recoveryMessage !== ""; text: root.recoveryMessage; textFormat: Text.PlainText; color: "#b8ddcb"; width: parent.width; wrapMode: Text.Wrap }
                    }
                    Text { text: "Tab to navigate · Esc or Super H to return"; color: "#788d84"; font.pixelSize: 12 }
                }
            }
        }
    }
    // shortcut: window titles stand in for live previews until compositor capture is available.
    PanelWindow {
        visible: !root.deskOpen && !root.switcherOpen && !root.appMenuOpen && !root.overviewOpen
        anchors.bottom: true
        implicitWidth: 100; implicitHeight: 28
        color: "transparent"; exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        Rectangle { anchors.centerIn: parent; width: 64; height: 5; radius: 3; color: "#b8ddcb" }
        MouseArea { anchors.fill: parent; onClicked: root.toggleSwitcher() }
    }
    PanelWindow {
        visible: root.switcherOpen
        anchors { bottom: true; left: true; right: true }
        implicitHeight: Math.min(screen.height - 16, Math.max(300, switcherContent.implicitHeight + 24)); color: "#101a18"; exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive
        FocusScope {
            anchors.fill: parent; focus: true
            Keys.onEscapePressed: root.switcherOpen = false
            Flickable {
                id: switcherScroller
                anchors.fill: parent; anchors.margins: 12; clip: true
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
                width: switcherScroller.width; spacing: 8
                Flow {
                    width: parent.width; spacing: 12
                    Text { text: "OPEN SPACES  ·  " + root.displaySpaces.length; color: "#b8ddcb"; font.bold: true; font.pixelSize: 13 }
                    Text { text: "Select a space or app · Esc to close"; color: "#91a59d"; font.pixelSize: 13 }
                }
                Flow {
                    width: parent.width; spacing: 8
                    FlowButton { id: stackButton; onActiveFocusChanged: if (activeFocus) switcherScroller.ensureVisible(stackButton); width: 160; implicitHeight: 44; text: "Stack with next"; onClicked: root.spaceAction("stack") }
                    FlowButton { id: unstackButton; onActiveFocusChanged: if (activeFocus) switcherScroller.ensureVisible(unstackButton); width: 160; implicitHeight: 44; text: "Separate stack"; onClicked: root.spaceAction("unstack") }
                    FlowButton { id: overviewButton; onActiveFocusChanged: if (activeFocus) switcherScroller.ensureVisible(overviewButton); width: 130; implicitHeight: 44; text: "Live overview"; onClicked: { root.switcherOpen = false; Quickshell.execDetached(["niri", "msg", "action", "open-overview"]) } }
                    Row {
                        visible: spaceScroller.contentWidth > spaceScroller.width
                        spacing: 6
                        FlowButton {
                            id: previousSpaces
                            onActiveFocusChanged: if (activeFocus) switcherScroller.ensureVisible(previousSpaces)
                            width: 44; implicitHeight: 44; text: "‹"; Accessible.name: "Show earlier spaces"
                            enabled: spaceScroller.contentX > 0; opacity: enabled ? 1 : 0.4
                            onClicked: spaceScroller.contentX = Math.max(0, spaceScroller.contentX - spaceScroller.width * 0.8)
                        }
                        FlowButton {
                            id: nextSpaces
                            onActiveFocusChanged: if (activeFocus) switcherScroller.ensureVisible(nextSpaces)
                            width: 44; implicitHeight: 44; text: "›"; Accessible.name: "Show later spaces"
                            enabled: spaceScroller.contentX < spaceScroller.contentWidth - spaceScroller.width; opacity: enabled ? 1 : 0.4
                            onClicked: spaceScroller.contentX = Math.min(spaceScroller.contentWidth - spaceScroller.width, spaceScroller.contentX + spaceScroller.width * 0.8)
                        }
                    }
                    Row {
                        id: switcherSpacesError
                        visible: root.spacesError !== ""; width: parent.width; spacing: 8
                        Text { text: root.spacesError; textFormat: Text.PlainText; color: "#f1baaa"; wrapMode: Text.Wrap; width: parent.width - dismissSwitcherSpacesError.width - 8 }
                        FlowButton { id: dismissSwitcherSpacesError; onActiveFocusChanged: if (activeFocus) switcherScroller.ensureVisible(dismissSwitcherSpacesError); width: 96; implicitHeight: 44; text: "Dismiss"; Accessible.name: "Dismiss spaces error"; onClicked: root.spacesError = "" }
                    }
                }
                Text { visible: root.displaySpaces.length === 0; text: "No open spaces"; color: "#91a59d" }
                Flickable {
                    id: spaceScroller
                    width: parent.width; height: Math.max(180, windowRow.implicitHeight); contentWidth: windowRow.width; contentHeight: height
                    boundsBehavior: Flickable.StopAtBounds; clip: true
                    function ensureVisible(item) {
                        switcherScroller.ensureVisible(item)
                        const left = item.mapToItem(windowRow, 0, 0).x
                        if (left < contentX) contentX = left
                        else if (left + item.width > contentX + width) contentX = Math.min(contentWidth - width, left + item.width - width)
                    }
                    Row {
                        id: windowRow; spacing: 8
                        Repeater {
                            model: root.displaySpaces
                            Column {
                                id: spaceCard
                                required property var modelData
                                width: modelData.members.length > 1 ? modelData.members.length * 136 - 4 : 200
                                spacing: 4
                                FlowButton {
                                    id: spaceButton
                                    onActiveFocusChanged: if (activeFocus) spaceScroller.ensureVisible(spaceButton)
                                    width: parent.width; height: modelData.members.length > 1 ? 50 : 90
                                    text: (modelData.active ? "●  " : "") + modelData.label.slice(0, 35) + (modelData.members.length > 1 ? "  ·  Group" : "")
                                    Accessible.name: "Switch to " + modelData.label
                                    onClicked: root.spaceAction("focus-window", String(modelData.id))
                                }
                                Row {
                                    visible: modelData.members.length > 1; spacing: 4
                                    Repeater {
                                        model: parent.parent.modelData.members
                                        FlowButton {
                                            id: memberButton
                                            required property var modelData
                                            onActiveFocusChanged: if (activeFocus) spaceScroller.ensureVisible(memberButton)
                                            width: 132; implicitHeight: 44
                                            text: (modelData.active ? "● " : "") + modelData.label.slice(0, 14)
                                            Accessible.name: "Focus " + modelData.label + " in group"
                                            onClicked: root.spaceAction("focus-window", String(modelData.id))
                                        }
                                    }
                                }
                                Row {
                                    visible: !!spaceCard.modelData.group_id; spacing: 4
                                    FlowButton {
                                        id: renameButton
                                        onActiveFocusChanged: if (activeFocus) spaceScroller.ensureVisible(renameButton)
                                        width: 132; implicitHeight: 44; text: "Rename Group"
                                        onClicked: { groupName.text = spaceCard.modelData.label; root.renameGroupId = spaceCard.modelData.group_id; groupName.forceActiveFocus() }
                                    }
                                    FlowButton {
                                        id: groupViewButton
                                        onActiveFocusChanged: if (activeFocus) spaceScroller.ensureVisible(groupViewButton)
                                        width: 132; implicitHeight: 44; text: "Tabs / stack"
                                        enabled: spaceCard.modelData.active
                                        Accessible.name: "Toggle tabs or stack for " + spaceCard.modelData.label
                                        onClicked: root.spaceAction("group-view")
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
                                        text: spaceCard.modelData.label
                                        placeholderText: "Group name"
                                        Accessible.name: "Group name"
                                        onAccepted: root.renameGroup(spaceCard.modelData.group_id, text)
                                    }
                                    FlowButton { id: saveGroupName; onActiveFocusChanged: if (activeFocus) spaceScroller.ensureVisible(saveGroupName); width: 64; implicitHeight: 44; text: "Save"; onClicked: root.renameGroup(spaceCard.modelData.group_id, groupName.text) }
                                    FlowButton { id: cancelGroupName; onActiveFocusChanged: if (activeFocus) spaceScroller.ensureVisible(cancelGroupName); width: 64; implicitHeight: 44; text: "Cancel"; Accessible.name: "Cancel Group rename"; onClicked: { root.renameGroupId = ""; renameButton.forceActiveFocus() } }
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
        visible: menuPreferences.appMenuEnabled && !root.deskOpen && !root.overviewOpen && root.appMenu && !root.appMenuOpen
        anchors { top: true; left: true }
        implicitWidth: 220; implicitHeight: 44; margins.top: 8; margins.left: 8
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
                x: 8; y: 8; width: Math.min(390, parent.width - 16); height: Math.min(menuColumn.implicitHeight + 32, parent.height - 16)
                radius: 16; color: "#17201e"; border.color: "#3a4d45"
                Flickable {
                    anchors.fill: parent; anchors.margins: 16
                    contentWidth: width; contentHeight: menuColumn.implicitHeight; clip: true
                    Column {
                        id: menuColumn; width: parent.width; spacing: 8
                        Text { text: root.appMenu ? root.appMenu.name : "Application"; textFormat: Text.PlainText; color: "#edf5f0"; font.bold: true; font.pixelSize: 18 }
                        Text { visible: root.appMenuError !== ""; text: root.appMenuError; textFormat: Text.PlainText; color: "#f1baaa"; width: parent.width; wrapMode: Text.Wrap }
                        Text { visible: root.appMenuFocusChanged; text: "Application focus changed. Close this menu to update it."; color: "#f1baaa"; width: parent.width; wrapMode: Text.Wrap }
                        Text {
                            visible: !root.appMenu || root.currentAppItems().length === 0
                            text: "No exposed app commands. Use the application's own menu."
                            color: "#a3b5ad"; width: parent.width; wrapMode: Text.Wrap
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
        implicitWidth: 320; implicitHeight: Math.min(screen.height - 80, Math.max(235, quickSettingsColumn.implicitHeight + 32)); margins.top: 60
        color: "#17201e"; exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive
        FocusScope {
            anchors.fill: parent; focus: true
            Keys.onEscapePressed: root.quickSettingsOpen = false
            ScrollView {
                anchors.fill: parent; anchors.margins: 16; clip: true
                contentWidth: availableWidth; contentHeight: quickSettingsColumn.implicitHeight
                Column {
                id: quickSettingsColumn
                width: parent.width; spacing: 10
                Text { text: "QUICK SETTINGS"; color: "#b8ddcb"; font.bold: true; font.pixelSize: 14 }
                Text { text: "Network: " + root.networkStatus; color: "#d7e5df"; font.pixelSize: 14 }
                FlowButton { width: parent.width; implicitHeight: 48; text: "Choose Wi-Fi network"; onClicked: { root.quickSettingsOpen = false; Quickshell.execDetached(["flow-wifi-settings"]) } }
                FlowButton { width: parent.width; implicitHeight: 48; text: "Toggle on-screen keyboard"; Accessible.name: "Toggle on-screen keyboard"; onClicked: { Quickshell.execDetached(["flow-keyboard"]); root.quickSettingsOpen = false } }
                Text { text: "Output volume: " + root.volumeStatus; color: "#d7e5df"; font.pixelSize: 14 }
                Row {
                    spacing: 6
                    FlowButton { width: 70; implicitHeight: 44; text: "− 5%"; Accessible.name: "Decrease volume"; enabled: root.audioAvailable; onClicked: root.changeVolume(-0.05) }
                    FlowButton { width: 70; implicitHeight: 44; text: "+ 5%"; Accessible.name: "Increase volume"; enabled: root.audioAvailable; onClicked: root.changeVolume(0.05) }
                    FlowButton { width: 110; implicitHeight: 44; text: "Mute / unmute"; enabled: root.audioAvailable; onClicked: root.changeVolume("mute") }
                }
                Row {
                    visible: !!root.mediaPlayer
                    width: parent.width; spacing: 8
                    Column {
                        width: parent.width - mediaToggle.width - parent.spacing; spacing: 2
                        Text {
                            width: parent.width; maximumLineCount: 1; wrapMode: Text.NoWrap; elide: Text.ElideRight
                            text: root.mediaPlayer ? (root.mediaPlayer.trackTitle || root.mediaPlayer.identity || "Media player").slice(0, 160) : ""
                            textFormat: Text.PlainText; color: "#edf5f0"; font.bold: true; font.pixelSize: 14
                        }
                        Text {
                            visible: !!(root.mediaPlayer && root.mediaPlayer.trackArtist)
                            width: parent.width; maximumLineCount: 1; wrapMode: Text.NoWrap; elide: Text.ElideRight
                            text: root.mediaPlayer ? root.mediaPlayer.trackArtist.slice(0, 160) : ""
                            textFormat: Text.PlainText; color: "#91a59d"; font.pixelSize: 12
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
                Text { visible: root.quickError !== ""; text: root.quickError; color: "#f1baaa"; font.pixelSize: 12 }
                Row {
                    spacing: 6
                    FlowButton { width: 180; implicitHeight: 44; text: "Edit connections"; onClicked: { root.quickSettingsOpen = false; Quickshell.execDetached(["nm-connection-editor"]) } }
                    FlowButton { width: 90; implicitHeight: 44; text: "Close"; onClicked: root.quickSettingsOpen = false }
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
        color: "#17201e"; exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: root.notificationsOpen ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None
        FocusScope {
            anchors.fill: parent; focus: root.notificationsOpen
            Keys.onEscapePressed: root.notificationsOpen = false
            ScrollView {
            anchors.fill: parent; anchors.margins: 12; clip: true
            contentWidth: availableWidth; contentHeight: notificationColumn.implicitHeight
            Column {
                id: notificationColumn; width: parent.width; spacing: 8
                Row {
                    width: parent.width; spacing: 8
                    Text { text: "NOTIFICATIONS"; color: "#b8ddcb"; font.bold: true; font.pixelSize: 13; anchors.verticalCenter: parent.verticalCenter }
                    FlowButton { width: 80; implicitHeight: 44; text: "Close"; onClicked: root.notificationsOpen = false }
                }
                Text {
                    visible: notificationServer.trackedNotifications.values.length === 0
                    width: parent.width; height: 88; verticalAlignment: Text.AlignVCenter; horizontalAlignment: Text.AlignHCenter
                    text: "No notifications"; color: "#91a59d"
                }
                Repeater {
                    model: notificationServer.trackedNotifications
                    delegate: Rectangle {
                        required property var modelData
                        required property int index
                        visible: root.notificationsOpen || index === notificationServer.trackedNotifications.values.length - 1
                        width: notificationColumn.width; implicitHeight: cardColumn.implicitHeight + 20
                        radius: 12; color: "#202d29"; border.color: "#3a4d45"
                        Column {
                            id: cardColumn; anchors.fill: parent; anchors.margins: 10; spacing: 5
                            Row {
                                width: parent.width; spacing: 8
                                Text { width: parent.width - dismiss.width - 8; text: modelData.appName + " · " + modelData.summary; textFormat: Text.PlainText; color: "#edf5f0"; font.bold: true; wrapMode: Text.Wrap }
                                FlowButton { id: dismiss; width: 44; implicitHeight: 44; text: "×"; Accessible.name: "Dismiss notification"; onClicked: modelData.dismiss() }
                            }
                            Text { visible: modelData.body !== ""; width: parent.width; text: modelData.body; textFormat: Text.PlainText; color: "#c4d2cb"; wrapMode: Text.Wrap }
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
        anchors.top: true
        implicitWidth: 316; implicitHeight: 44; margins.top: 8
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
                FlowButton { width: 38; implicitHeight: 34; text: "◉"; Accessible.name: "Notifications"; onClicked: root.toggleNotifications() }
                FlowButton { width: 38; implicitHeight: 34; text: "⚙"; Accessible.name: "Quick settings"; onClicked: root.toggleQuickSettings() }
            }
        }
        SystemClock { id: clock; precision: SystemClock.Minutes }
    }
}
