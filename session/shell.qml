import QtQuick
import QtQuick.Controls
import QtCore as Core
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

ShellRoot {
    id: root
    property bool deskOpen: false
    property bool settingsOpen: false
    property bool quickSettingsOpen: false
    property string volumeStatus: "Checking audio…"
    property string networkStatus: "Checking network…"
    property string quickError: ""
    function refreshQuickSettings() {
        if (!audioStatus.running) audioStatus.running = true
        if (!networkStatusProcess.running) networkStatusProcess.running = true
    }
    Process {
        id: audioStatus
        command: ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"]
        stdout: StdioCollector {
            onStreamFinished: {
                const match = this.text.match(/Volume:\s*([0-9.]+)/)
                root.volumeStatus = match ? Math.round(Number(match[1]) * 100) + "%" + (this.text.indexOf("[MUTED]") >= 0 ? " · muted" : "") : "Audio unavailable"
            }
        }
        onExited: (code, status) => { if (code !== 0) root.volumeStatus = "Audio unavailable" }
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
    Process {
        id: audioControl
        onExited: (code, status) => { root.quickError = code === 0 ? "" : "Audio control unavailable"; root.refreshQuickSettings() }
    }
    Timer { interval: 4000; running: root.quickSettingsOpen; repeat: true; triggeredOnStart: true; onTriggered: root.refreshQuickSettings() }
    function toggleQuickSettings() {
        if (!quickSettingsOpen) {
            appMenuOpen = false
            openMenuWhenReady = false
            switcherOpen = false
            quickError = ""
        }
        quickSettingsOpen = !quickSettingsOpen
    }
    function changeVolume(value) {
        if (audioControl.running) return
        audioControl.command = value === "mute" ? ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"] : ["wpctl", "set-volume", "--limit", "1.0", "@DEFAULT_AUDIO_SINK@", value]
        audioControl.running = true
    }
    property bool appMenuOpen: false
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
                    if (result && result.error) { root.appMenu = null; root.openMenuWhenReady = false; root.appMenuError = result.error; return }
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
                        root.appMenu = null
                        root.appMenuOpen = false
                        root.openMenuWhenReady = false
                    }
                    if (event.OverviewOpenedOrClosed) {
                        root.overviewOpen = event.OverviewOpenedOrClosed.is_open
                        if (root.overviewOpen) { root.appMenuOpen = false; root.openMenuWhenReady = false; root.switcherOpen = false; root.quickSettingsOpen = false }
                    }
                } catch (e) {}
            }
        }
    }
    Timer { id: eventReconnect; interval: 5000; onTriggered: if (!niriEvents.running) niriEvents.running = true }
    property string recoveryMessage: ""
    property string renameGroupId: ""
    property bool switcherOpen: false
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
        quickSettingsOpen = false
        switcherOpen = false
        appMenuOpen = false
        openMenuWhenReady = false
        if (!deskOpen) Quickshell.execDetached(["niri", "msg", "action", "close-overview"])
        deskOpen = !deskOpen
        if (deskOpen) spaceAction("refresh")
    }

    function openSearch() {
        quickSettingsOpen = false
        openMenuWhenReady = false
        appMenuOpen = false
        deskOpen = false
        switcherOpen = false
        Quickshell.execDetached(["flow-search"])
    }

    function openApps() {
        quickSettingsOpen = false
        openMenuWhenReady = false
        appMenuOpen = false
        switcherOpen = false
        deskOpen = false
        Quickshell.execDetached(["flow-apps"])
    }

    function toggleSwitcher() {
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
            text: control.text; color: "#d7e5df"; font.pixelSize: 14; elide: Text.ElideRight
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
        function apps(): void { root.openApps() }
        function search(): void { root.openSearch() }
        function pin(): void { root.spaceAction("pin") }
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
                        text: "⌕   Search apps and open spaces   ·   Super K"
                        onClicked: root.openSearch()
                    }
                    FlowButton {
                        width: 150; text: root.settingsOpen ? "Close settings" : "Settings"
                        onClicked: root.settingsOpen = !root.settingsOpen
                    }
                    Row {
                        visible: root.settingsOpen
                        spacing: 12
                        Switch {
                            id: menuSwitch
                            checked: menuPreferences.appMenuEnabled
                            Accessible.name: "Experimental application menu"
                            onToggled: { menuPreferences.appMenuEnabled = checked; menuPreferences.sync(); root.appMenuOpen = false }
                        }
                        Text { anchors.verticalCenter: parent.verticalCenter; text: "Experimental application menu"; color: "#d7e5df"; font.pixelSize: 14 }
                    }
                    Text { text: "SESSION RECOVERY"; color: "#91a59d"; font.bold: true; font.letterSpacing: 1.5; font.pixelSize: 12 }
                    Text { text: "Save your documents, then save the current window list before restarting."; color: "#9baea7"; width: parent.width; wrapMode: Text.Wrap }
                    Row {
                        spacing: 8
                        FlowButton { width: 180; text: "Save session"; enabled: !recoveryProcess.running && !spacesProcess.running; onClicked: { recoveryProcess.command = ["flow-recovery", "save"]; recoveryProcess.running = true } }
                        FlowButton { width: 180; text: "Restore session"; enabled: !recoveryProcess.running && !spacesProcess.running; onClicked: { root.recoveryMessage = "Restoring applications…"; recoveryProcess.command = ["flow-recovery", "restore"]; recoveryProcess.running = true } }
                    }
                    Text { visible: root.recoveryMessage !== ""; text: root.recoveryMessage; color: "#b8ddcb"; width: parent.width; wrapMode: Text.Wrap }
                    Text { text: "PINNED SPACES"; color: "#91a59d"; font.bold: true; font.letterSpacing: 1.5; font.pixelSize: 12 }
                    Text { visible: root.pins.length === 0; text: "Pin the current app to keep its place"; color: "#80948b"; font.pixelSize: 14 }
                    Flow {
                        width: parent.width
                        spacing: 8
                        Repeater {
                            model: root.pins
                            Row {
                                required property var modelData
                                required property int index
                                spacing: 3
                                FlowButton {
                                    width: 180; text: (index + 1) + "  " + (modelData.label.length > 15 ? modelData.label.slice(0, 15) + "…" : modelData.label) + (modelData.available ? "" : " · Open")
                                    enabled: !spacesProcess.running || spacesProcess.command[1] === "refresh"
                                    Accessible.name: (modelData.available ? "Focus " : "Open ") + modelData.label
                                    onClicked: root.spaceAction("focus", modelData.id)
                                }
                                FlowButton { width: 42; text: "‹"; Accessible.name: "Move " + modelData.label + " left"; enabled: !spacesProcess.running || spacesProcess.command[1] === "refresh"; onClicked: root.spaceAction("left", modelData.id) }
                                FlowButton { width: 42; text: "›"; Accessible.name: "Move " + modelData.label + " right"; enabled: !spacesProcess.running || spacesProcess.command[1] === "refresh"; onClicked: root.spaceAction("right", modelData.id) }
                                FlowButton { width: 42; text: "×"; enabled: !spacesProcess.running || spacesProcess.command[1] === "refresh"; Accessible.name: "Unpin " + modelData.label; onClicked: root.spaceAction("unpin", modelData.id) }
                            }
                        }
                    }
                    FlowButton { width: 200; text: "Pin current app · Super P"; enabled: !spacesProcess.running || spacesProcess.command[1] === "refresh"; onClicked: root.spaceAction("pin") }
                    Text { visible: root.spacesError !== ""; text: root.spacesError; color: "#f1baaa"; wrapMode: Text.Wrap; width: parent.width; font.pixelSize: 14 }
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
        implicitHeight: 290; color: "#101a18"; exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive
        FocusScope {
            anchors.fill: parent; focus: true
            Keys.onEscapePressed: root.switcherOpen = false
            Column {
                anchors.fill: parent; anchors.margins: 12; spacing: 8
                Row {
                    spacing: 12
                    Text { text: "OPEN SPACES"; color: "#b8ddcb"; font.bold: true; font.pixelSize: 13 }
                    Text { text: "Select a space or app · Esc to close"; color: "#91a59d"; font.pixelSize: 13 }
                }
                Row {
                    spacing: 8
                    FlowButton { width: 160; implicitHeight: 38; text: "Stack with next"; onClicked: root.spaceAction("stack") }
                    FlowButton { width: 160; implicitHeight: 38; text: "Separate stack"; onClicked: root.spaceAction("unstack") }
                    Text { text: root.spacesError; color: "#f1baaa"; anchors.verticalCenter: parent.verticalCenter }
                }
                Text { visible: root.displaySpaces.length === 0; text: "No open spaces"; color: "#91a59d" }
                Flickable {
                    width: parent.width; height: 180; contentWidth: windowRow.width; contentHeight: height
                    boundsBehavior: Flickable.StopAtBounds; clip: true
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
                                            required property var modelData
                                            width: 132; implicitHeight: 36
                                            text: (modelData.active ? "● " : "") + modelData.label.slice(0, 14)
                                            Accessible.name: "Focus " + modelData.label + " in group"
                                            onClicked: root.spaceAction("focus-window", String(modelData.id))
                                        }
                                    }
                                }
                                FlowButton {
                                    visible: !!spaceCard.modelData.group_id
                                    width: 132; implicitHeight: 32; text: "Rename Group"
                                    onClicked: { root.renameGroupId = spaceCard.modelData.group_id; groupName.forceActiveFocus() }
                                }
                                Row {
                                    visible: !!spaceCard.modelData.group_id && root.renameGroupId === spaceCard.modelData.group_id
                                    width: parent.width; spacing: 4
                                    TextField {
                                        id: groupName
                                        width: parent.width - 68; height: 36; maximumLength: 80
                                        text: spaceCard.modelData.label
                                        placeholderText: "Group name"
                                        Accessible.name: "Group name"
                                        onAccepted: root.renameGroup(spaceCard.modelData.group_id, text)
                                    }
                                    FlowButton { width: 64; implicitHeight: 36; text: "Save"; onClicked: root.renameGroup(spaceCard.modelData.group_id, groupName.text) }
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
                        Text { text: root.appMenu ? root.appMenu.name : "Application"; color: "#edf5f0"; font.bold: true; font.pixelSize: 18 }
                        Text { visible: root.appMenuError !== ""; text: root.appMenuError; color: "#f1baaa"; width: parent.width; wrapMode: Text.Wrap }
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
                                enabled: modelData.enabled && !appMenuProcess.running
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
        implicitWidth: 320; implicitHeight: 235; margins.top: 60
        color: "#17201e"; exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive
        FocusScope {
            anchors.fill: parent; focus: true
            Keys.onEscapePressed: root.quickSettingsOpen = false
            Column {
                anchors.fill: parent; anchors.margins: 16; spacing: 10
                Text { text: "QUICK SETTINGS"; color: "#b8ddcb"; font.bold: true; font.pixelSize: 14 }
                Text { text: "Network: " + root.networkStatus; color: "#d7e5df"; font.pixelSize: 14 }
                Text { text: "Output volume: " + root.volumeStatus; color: "#d7e5df"; font.pixelSize: 14 }
                Row {
                    spacing: 6
                    FlowButton { width: 70; implicitHeight: 42; text: "− 5%"; Accessible.name: "Decrease volume"; onClicked: root.changeVolume("5%-") }
                    FlowButton { width: 70; implicitHeight: 42; text: "+ 5%"; Accessible.name: "Increase volume"; onClicked: root.changeVolume("5%+") }
                    FlowButton { width: 110; implicitHeight: 42; text: "Mute / unmute"; onClicked: root.changeVolume("mute") }
                }
                Text { visible: root.quickError !== ""; text: root.quickError; color: "#f1baaa"; font.pixelSize: 12 }
                FlowButton { width: 90; implicitHeight: 38; text: "Close"; onClicked: root.quickSettingsOpen = false }
            }
        }
    }
    PanelWindow {
        anchors.top: true
        implicitWidth: 270; implicitHeight: 44; margins.top: 8
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
                FlowButton { width: 38; implicitHeight: 34; text: "⚙"; Accessible.name: "Quick settings"; onClicked: root.toggleQuickSettings() }
            }
        }
        SystemClock { id: clock; precision: SystemClock.Minutes }
    }
}
