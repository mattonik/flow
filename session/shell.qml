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
    // The notification the banner shows: the newest that arrived, or changed, while the panel was closed.
    property var bannerNotification: null
    function showBanner(notification) {
        // A low-urgency notification waits quietly in the panel, with the bell's dot.
        if (notification.urgency === NotificationUrgency.Low || notificationsOpen) return
        // A critical banner gives way only to another critical one; anything else waits in the panel.
        if (bannerNotification && bannerNotification !== notification
            && bannerNotification.urgency === NotificationUrgency.Critical
            && notification.urgency !== NotificationUrgency.Critical) return
        bannerNotification = notification
        if (bannerCounting) bannerTimer.restart()
    }
    // The banner's 5 s count only while it can be seen, and never for a critical one. Each time it starts, it starts
    // from the full 5 s.
    readonly property bool bannerCounting: notificationBanner.visible
        && !!bannerNotification && bannerNotification.urgency !== NotificationUrgency.Critical
    onBannerCountingChanged: bannerCounting ? bannerTimer.restart() : bannerTimer.stop()
    Timer { id: bannerTimer; interval: 5000; onTriggered: root.bannerNotification = null }
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
            root.showBanner(notification)
            // A notification that replaces this one (replaces_id: a chat's next message, a download's
            // progress) updates this object in place, with no new signal: show it again when its text
            // changes. The connections end with the object.
            const changed = () => root.showBanner(notification)
            notification.summaryChanged.connect(changed)
            notification.bodyChanged.connect(changed)
            // Quickshell adds the notification to its model after this handler returns.
            Qt.callLater(root.trimTrackedNotifications)
        }
    }
    Process {
        id: networkStatusProcess
        command: ["nmcli", "-t", "-f", "CONNECTIVITY", "general"]
        stderr: SplitParser { onRead: line => console.warn("nmcli: " + line) }
        stdout: StdioCollector { onStreamFinished: {
            const state = this.text.trim()
            root.networkStatus = ({full: "Connected", limited: "Limited", portal: "Sign-in required", none: "Offline", unknown: "Unknown"})[state] || "Network unavailable"
        } }
        onExited: (code, status) => { if (code !== 0) root.networkStatus = "Network unavailable" }
    }
    Timer { interval: 4000; running: root.quickSettingsOpen; repeat: true; triggeredOnStart: true; onTriggered: if (!networkStatusProcess.running) networkStatusProcess.running = true }
    function toggleNotifications() {
        overviewPending = false
        spacesOverviewOpen = false   // one surface at a time
        if (!notificationsOpen) {
            bannerNotification = null   // the panel shows it now
            quickSettingsOpen = false; appMenuOpen = false; openMenuWhenReady = false
            switcherOpen = false
            if (overviewOpen) Quickshell.execDetached(["niri", "msg", "action", "close-overview"])
        }
        notificationsOpen = !notificationsOpen
    }
    function toggleQuickSettings() {
        overviewPending = false
        spacesOverviewOpen = false   // one surface at a time
        notificationsOpen = false
        if (!quickSettingsOpen) {
            appMenuOpen = false
            openMenuWhenReady = false
            switcherOpen = false
            quickError = ""
        }
        quickSettingsOpen = !quickSettingsOpen
    }
    function setVolume(level) {
        if (!audioAvailable) { quickError = "Audio control unavailable"; return }
        quickError = ""
        audioSink.audio.volume = Math.max(0, Math.min(1, level))
    }
    function toggleMute() {
        if (!audioAvailable) { quickError = "Audio control unavailable"; return }
        quickError = ""
        audioSink.audio.muted = !audioSink.audio.muted
    }
    property bool appMenuOpen: false
    property bool appMenuFocusChanged: false
    onAppMenuOpenChanged: {
        if (!appMenuOpen) {   // a menu is read afresh each time it opens
            appMenu = null
            appMenuFocusChanged = false
            appMenuError = ""
            appMenuActivateAsked = 0
        }
    }
    property bool overviewOpen: false
    property var appMenu: null   // the open menu: {id (window), name, menus}
    property string appMenuError: ""
    // One setting per Core.Settings. Qt 6.12's Settings, loading a file without one of its keys, writes back the
    // values its later properties had before they were loaded: their defaults, over what the file held. Alone in
    // its object, a missing key is only ever written with its own default.
    readonly property string settingsFile: "file://" + (Quickshell.env("XDG_CONFIG_HOME") || Quickshell.env("HOME") + "/.config") + "/flow-settings.ini"
    Core.Settings {
        id: menuPreferences
        location: root.settingsFile
        property bool appMenuEnabled: false
    }
    // Automatic recovery, on unless switched off. Its key was autoRecovery, off by default; every shell that
    // started without it wrote autoRecovery=false back (above), so an old false was rarely the user's own. That
    // key is no longer read: each install starts with recovery on once, and a later switch-off is kept.
    Core.Settings {
        id: recoveryPreferences
        location: root.settingsFile
        property bool sessionRecovery: true
    }
    property var appMenuPath: []
    function currentAppItems() {
        let items = root.appMenu ? root.appMenu.menus : []
        for (const index of root.appMenuPath) items = (items[index] && items[index].children) || []
        return items
    }
    // The opt-in app menu (F-020). While the setting is on, one resident reader (session/appmenu.py, `flow-appmenu
    // serve`) answers JSON lines: a probe when focus settles, the menu when it opens, and a command. A probe answers
    // for the window Niri has focused: the first probe of a window chooses its accessible frame, which only works
    // while the window has the keyboard, so probes are asked only while no Flow surface has it. Niri says which
    // window has focus only sometimes (not for a window that opens focused), so every settle asks, and so does a
    // change of the active window in Flow's state; the reader answers a window it knows from memory. The App button
    // shows only for a window whose probe found a menu. Answers are kept per window until it closes; Super+M on a
    // window with no menu known asks afresh before it says so.
    property int appMenuWindow: -1   // the focused window as the last probe (or Niri's event) gave it; -1 for none
    property int appMenuFocusMoves: 0   // Niri's focus events; a probe asked before the latest one is stale
    property int appMenuProbeMoves: 0
    property double appMenuProbeSent: 0
    property var appMenuProbeTimes: []   // the last probes' round trips in ms, for tests/niri_performance_smoke.py
    property int appMenuProbesAnswered: 0
    property var appMenuProbes: ({})   // window id → the reader's probe answer
    readonly property var appMenuProbe: appMenuProbes[appMenuWindow] || null
    readonly property bool appMenuAvailable: menuPreferences.appMenuEnabled && !!appMenuProbe && appMenuProbe.has_menu === true
    // The request each kind of answer is waited for by, 0 for none.
    property int appMenuProbeAsked: 0
    property int appMenuReadAsked: 0
    property int appMenuActivateAsked: 0
    property bool openMenuWhenReady: false   // cleared when another surface opens before the menu's answer
    property int appMenuRequests: 0
    property bool openMenuAfterProbe: false   // Super+M on a window with no menu known: open it if a fresh probe finds one
    // A probe that found nothing final is asked again, 1.5, 4 and 10 s later, while focus stays: a window that opens
    // focused gets the keyboard a moment after Niri's event, and its app registers on the bus after that.
    readonly property var appMenuRetryDelays: [1500, 4000, 10000]
    property int appMenuRetryMove: -1   // the focus move those retries are for
    property int appMenuRetries: 0
    function setAppMenuProbe(window, answer) {
        const probes = Object.assign({}, appMenuProbes)
        if (answer) probes[window] = answer
        else delete probes[window]
        appMenuProbes = probes
    }
    function appMenuSend(op, fields) {
        if (!appMenuReader.running) return 0
        appMenuRequests += 1
        appMenuReader.write(JSON.stringify(Object.assign({ id: appMenuRequests, op: op }, fields)) + "\n")
        return appMenuRequests
    }
    function sendAppMenuProbe(fresh) {
        appMenuProbeMoves = appMenuFocusMoves
        appMenuProbeSent = Date.now()
        appMenuProbeAsked = appMenuSend("probe", fresh ? { fresh: true } : {})
    }
    function requestAppMenu() {
        if (!menuPreferences.appMenuEnabled || root.deskOpen || root.overviewOpen || root.spacesOverviewOpen || root.searchOpen || appMenuReadAsked) return
        if (!appMenuReader.running) { showToast("App menu unavailable"); return }
        if (!appMenuAvailable) {
            // Asked afresh, as the window still has the keyboard: its app may have registered, or shown its menu
            // bar, since its last probe. Under another Flow surface the frame cannot be chosen.
            if (appIdentityBlocked || appMenuProbeAsked) { showToast("No app menu"); return }
            openMenuAfterProbe = true
            sendAppMenuProbe(true)
            return
        }
        root.switcherOpen = false
        root.quickSettingsOpen = false
        root.notificationsOpen = false
        appMenuReadAsked = appMenuSend("read", { window: appMenuWindow })
        openMenuWhenReady = appMenuReadAsked !== 0
        if (!appMenuReadAsked) showToast("App menu unavailable")
    }
    function activateAppMenuItem(item) {
        if (!root.appMenu || appMenuActivateAsked) return
        appMenuError = ""
        appMenuActivateAsked = appMenuSend("activate", { window: root.appMenu.id, path: item.path, label: item.label })
        if (!appMenuActivateAsked) appMenuError = "App menu unavailable"
    }
    function appMenuAnswer(line) {
        let answer = null
        try { answer = JSON.parse(line) } catch (e) { return }
        if (!answer || !answer.id) return
        if (answer.id === appMenuProbeAsked) {
            appMenuProbeAsked = 0
            appMenuProbeTimes = appMenuProbeTimes.slice(-19).concat([Date.now() - appMenuProbeSent])
            appMenuProbesAnswered += 1
            if (answer.error || answer.window === null || !answer.final) {
                if (appMenuRetryMove !== appMenuProbeMoves) { appMenuRetryMove = appMenuProbeMoves; appMenuRetries = 0 }
                if (appMenuRetries < appMenuRetryDelays.length) {
                    appMenuRetry.interval = appMenuRetryDelays[appMenuRetries]
                    appMenuRetries += 1
                    appMenuRetry.restart()
                }
            }
            const opening = openMenuAfterProbe
            openMenuAfterProbe = false
            if (!answer.error) {
                if (appMenuProbeMoves === appMenuFocusMoves) appMenuWindow = answer.window === null ? -1 : answer.window
                if (answer.window !== null)
                    setAppMenuProbe(answer.window, { name: answer.name, has_menu: answer.has_menu, final: answer.final })
            }
            if (opening) {
                if (appMenuAvailable) requestAppMenu()
                else showToast("No app menu")
            }
        } else if (answer.id === appMenuReadAsked) {
            appMenuReadAsked = 0
            const wanted = openMenuWhenReady
            openMenuWhenReady = false
            if (answer.error) {
                setAppMenuProbe(answer.window, null)   // probed again when focus next settles
                showToast(answer.error)
                requestAppIdentity()
            } else if (wanted && answer.window === appMenuWindow && !appIdentityBlocked) {
                appMenu = { id: answer.window, workspace: answer.workspace, name: answer.name, menus: answer.menus }
                appMenuPath = []
                appMenuOpen = true
            }
        } else if (answer.id === appMenuActivateAsked) {
            appMenuActivateAsked = 0
            // "unknown": the app has not answered yet, as with a modal dialog. The command was sent.
            if (answer.done) appMenuOpen = false
            else appMenuError = answer.error || "App menu unavailable"
        }
    }
    function requestAppIdentity() { appIdentityTimer.restart() }
    // Any Flow surface that takes the keyboard: the window's frame is no longer `active` under it.
    readonly property bool appIdentityBlocked: appMenuOpen || deskOpen || overviewOpen || spacesOverviewOpen || searchOpen
                                               || switcherOpen || quickSettingsOpen || notificationsOpen
    onAppIdentityBlockedChanged: if (!appIdentityBlocked) requestAppIdentity()
    Connections {
        target: menuPreferences
        function onAppMenuEnabledChanged() {
            root.appMenuOpen = false
            root.appMenuProbes = ({})
            if (menuPreferences.appMenuEnabled) {
                root.appMenuExits = 0
                if (!appMenuRelease.running) appMenuReader.running = true   // else once the release has run
            } else if (appMenuReader.running) appMenuReader.running = false   // its exit releases accessibility
            else appMenuRelease.running = true
        }
    }
    Timer {
        id: appIdentityTimer; interval: 300
        onTriggered: {
            if (!menuPreferences.appMenuEnabled || root.appIdentityBlocked) return
            if (root.appMenuProbeAsked || root.appMenuReadAsked) { restart(); return }
            root.sendAppMenuProbe(false)
        }
    }
    Timer { id: appMenuRetry; onTriggered: if (root.appMenuRetryMove === root.appMenuFocusMoves) root.requestAppIdentity() }
    // Started only while the setting is on, and again if it stops, as the controller is (below). When the setting
    // goes off, the reader is stopped and `flow-appmenu release` switches accessibility off, if Flow switched it on.
    // A reader started again meanwhile waits for the release: started first, it would see accessibility still on,
    // record nothing, and then run with it switched off.
    Process {
        id: appMenuRelease
        stderr: SplitParser { onRead: line => console.warn("flow-appmenu: " + line) }
        command: ["flow-appmenu", "release"]
        onExited: if (menuPreferences.appMenuEnabled && !appMenuReader.running) appMenuReader.running = true
    }
    Process {
        id: appMenuReader
        command: ["sh", "-c", "command -v setpriv >/dev/null && exec setpriv --pdeathsig TERM flow-appmenu serve; exec flow-appmenu serve"]
        Component.onCompleted: if (menuPreferences.appMenuEnabled && !appMenuRelease.running) running = true
        stdinEnabled: true
        stdout: SplitParser { onRead: line => root.appMenuAnswer(line) }
        stderr: SplitParser { onRead: line => console.warn("flow-appmenu: " + line) }
        onStarted: { appMenuHealthy.restart(); root.requestAppIdentity() }
        onExited: {
            appMenuHealthy.stop()
            const opening = root.openMenuWhenReady
            root.appMenuProbeAsked = 0; root.appMenuReadAsked = 0; root.appMenuActivateAsked = 0
            root.openMenuWhenReady = false
            root.appMenuProbes = ({})
            if (root.appMenuOpen) root.appMenuError = "App menu unavailable"
            if (!menuPreferences.appMenuEnabled) { appMenuRelease.running = true; return }
            if (opening) root.showToast("App menu unavailable")
            appMenuRestart.interval = Math.min(2000 * Math.pow(2, root.appMenuExits), 60000)
            root.appMenuExits += 1
            appMenuRestart.restart()
        }
    }
    property int appMenuExits: 0   // since one last served for 30 s
    Timer { id: appMenuRestart; onTriggered: if (menuPreferences.appMenuEnabled && !appMenuReader.running && !appMenuRelease.running) appMenuReader.running = true }
    Timer { id: appMenuHealthy; interval: 30000; onTriggered: root.appMenuExits = 0 }
    // Flow's resident controller (session/flow_controller.py) reads Niri's events, refreshes Flow's state
    // in-process when they ask for it, and sends that state here, with the few Niri events the shell reacts to.
    // The shell starts it, and starts it again if it stops: 2 s later, then twice as long each time it stops
    // again (at most a minute), until it has served for 30 s. While another controller serves this runtime
    // folder and Flow state (a shell restarting), a new one waits for it to go.
    Process {
        id: controllerProcess
        // The kernel stops the controller when the shell dies, even by SIGKILL, so restarts never leak one.
        // Without setpriv it still runs, only without that guarantee.
        command: ["sh", "-c", "command -v setpriv >/dev/null && exec setpriv --pdeathsig TERM flow-controller; exec flow-controller"]
        running: true
        onExited: {
            controllerRestart.interval = Math.min(2000 * Math.pow(2, root.controllerExits), 60000)
            root.controllerExits += 1
            controllerRestart.restart()
        }
        stderr: SplitParser { onRead: line => console.warn("flow-controller: " + line) }
        // It serves: connect now (startup used to wait up to a quarter of a second for a timer).
        stdout: SplitParser { onRead: line => { if (line === "listening" && !controller.connected) controller.connected = true } }
    }
    property int controllerExits: 0   // since one last served for 30 s
    Timer { id: controllerRestart; onTriggered: if (!controllerProcess.running) controllerProcess.running = true }
    Timer { id: controllerHealthy; interval: 30000; onTriggered: if (controller.connected) root.controllerExits = 0 }
    // session/flow_controller.py, socket_path(): one controller per runtime folder and Flow state.
    readonly property string stateHome: Quickshell.env("XDG_STATE_HOME") || Quickshell.env("HOME") + "/.local/state"
    Socket {
        id: controller
        path: Quickshell.env("XDG_RUNTIME_DIR")
            ? Quickshell.env("XDG_RUNTIME_DIR") + "/flow-controller-" + Qt.md5(root.stateHome).slice(0, 12) + ".sock" : ""
        onConnectionStateChanged: {
            if (connected) { write('{"watch": true, "auto_save": ' + root.autoSaveWanted + '}\n'); flush(); controllerHealthy.restart() }
            else { controllerHealthy.stop(); controllerConnect.restart() }
        }
        parser: SplitParser {
            onRead: data => {
                let message = null
                try { message = JSON.parse(data) } catch (e) { return }
                if (message.state) {
                    root.applySpaces(message.state)
                    if (message.refreshed !== undefined && message.refreshed >= root.deskRefresh && root.deskOpen) root.openEnds("desk")
                } else if (message.event) root.niriEvent(message.event)
                else if (message.auto_saved) root.autoSaved(message.auto_saved)
            }
        }
    }
    // The shell connects when the controller says it listens (it starts with the shell, and again after it
    // stops). Should that line not come, or the connection drop, it tries again every second.
    Timer { id: controllerConnect; interval: 1000; running: true; repeat: true; onTriggered: if (controller.connected) stop(); else controller.connected = true }
    property int refreshAsked: 0
    property int deskRefresh: 0
    // A refresh now, answered even if nothing changed: Desk opening, an action that finished, a display resized.
    function requestRefresh() {
        if (!controller.connected) return 0
        refreshAsked += 1
        controller.write(JSON.stringify({ refresh: refreshAsked }) + "\n")
        controller.flush()
        return refreshAsked
    }
    function niriEvent(event) {
        if (event.WindowFocusChanged || event.WorkspaceActiveWindowChanged || event.WindowsChanged) {
            appMenuFocusMoves += 1
            requestAppIdentity()
        }
        // A Flow surface taking the keyboard (the menu's own) moves Niri's focus to no window: not a move.
        if (event.WindowFocusChanged && (event.WindowFocusChanged.id !== null || !appIdentityBlocked)) {
            appMenuWindow = event.WindowFocusChanged.id === null ? -1 : event.WindowFocusChanged.id
            if (appMenu && event.WindowFocusChanged.id !== null && appMenu.id !== appMenuWindow) appMenuFocusChanged = true
        }
        // Focus moving to another window of the menu's workspace while the menu holds the keyboard: Niri sends no
        // WindowFocusChanged then, only this.
        const moved = event.WorkspaceActiveWindowChanged
        if (moved && appMenu && moved.workspace_id === appMenu.workspace && moved.active_window_id !== null
                && moved.active_window_id !== appMenu.id)
            appMenuFocusChanged = true
        if (event.WindowsChanged) {
            const focused = event.WindowsChanged.windows.find(w => w.is_focused)
            appMenuWindow = focused ? focused.id : -1
            const live = event.WindowsChanged.windows.map(w => String(w.id))
            const probes = {}
            for (const id in appMenuProbes) if (live.includes(id)) probes[id] = appMenuProbes[id]
            appMenuProbes = probes
        }
        if (event.WindowClosed) {
            setAppMenuProbe(event.WindowClosed.id, null)
            if (appMenu && appMenu.id === event.WindowClosed.id) appMenuFocusChanged = true
        }
        if (event.OverviewOpenedOrClosed) {
            overviewOpen = event.OverviewOpenedOrClosed.is_open
            if (overviewOpen) { deskOpen = false; spacesOverviewOpen = false; overviewPending = false; searchOpen = false; notificationsOpen = false; appMenuOpen = false; openMenuWhenReady = false; switcherOpen = false; quickSettingsOpen = false }
        }
    }
    property string recoveryMessage: ""
    property bool autoRecoveryBlocked: true
    // Why automatic saving is paused, kept apart from recoveryMessage, which the next recovery command replaces
    // (a Group rename, say). Settings shows it while the pause lasts.
    property string recoveryPauseReason: ""
    // A saved session recovery set aside instead of reopening it, shown in Settings for this shell's life (the first
    // automatic save soon replaces recoveryMessage), with Reopen, which restores from that file itself: a copy put
    // back as session.json would be replaced by the next automatic save before Restore session read it.
    property string recoverySetAside: ""
    // What a Reopen it that opened something left out: the line says so, and Reopen it is not offered again,
    // since a second run would open what the first did twice.
    property string recoverySetAsideLeft: ""
    // Settings' Reopen it, and tests/niri_set_aside_reopen_smoke.py: restores from the set-aside file itself, on
    // the display's empty workspace (the work is open), and is offered until every window is back ("complete").
    // Not an automatic command, so automatic saving waits while it runs (recoveryWorking).
    function reopenSetAside() {
        const busy = recoveryProcess.running ? "Wait for session recovery to finish" : spacesWorking ? "Wait for the current action" : ""
        if (busy) { recoveryMessage = busy; return }
        if (!recoverySetAside || recoverySetAsideLeft) return
        recoveryMessage = "Reopening applications…"
        recoveryProcess.command = ["flow-recovery", "restore", recoverySetAside]
        recoveryProcess.running = true
    }
    onAutoRecoveryBlockedChanged: if (!autoRecoveryBlocked) recoveryPauseReason = ""
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
    // The focused display's screen, from the last refresh. The work view's Island, indicators and app-menu trigger are put
    // there: a layer surface without a screen goes wherever the compositor's focus is when it maps, so after Desk opened
    // on the second display and closed, the Island and indicators had moved there from the first (seen in the guest,
    // F-007). So they follow the display the work indicator describes, a refresh (0.15 to 0.6 s) after focus moves.
    // Desk and Overview keep no screen of their own: they map where Niri's focus is as they open, which is never late.
    property string displayOutput: ""
    readonly property var workScreen: Quickshell.screens.find(screen => screen.name === displayOutput) || null

    // Resting chrome by device (ADR-0014). Each screen decides how the Island appears over apps. A plain screen (a
    // keyboard and pointer, no cutout) shows nothing at rest: the 1 px strip along its top edge, Super+B or the
    // optional clock pill opens the Island over the app, and it goes again when the pointer leaves. A notched MacBook
    // in Asahi's full-panel mode (appledrm.show_notch=1) keeps the band beside the camera for the shell, as an
    // exclusive zone, so apps get the rectangle Asahi gives them by default and the Island lives in the band.
    // Known notched panels, matched on the full-panel size an output's current mode has, in physical pixels (from Niri,
    // through the controller's displays: Qt's screen ratio is a whole number on Wayland, 2 at a scale of 1.6). The band is the panel's
    // height less Asahi's default, the largest 16:10 area below the notch (m1n1 crops to it). No Asahi source has the
    // cutout's width or corner: those are approximate, from third-party measurements (the 15-inch Air's and 16-inch
    // Pro's taken as the 13-inch's and 14-inch's). None of this has been checked on a MacBook.
    readonly property var notchPanels: [
        { id: "air-13", name: "MacBook Air 13-inch", size: [2560, 1664], band: 64, cutout: 312, radius: 14 },
        { id: "air-15", name: "MacBook Air 15-inch", size: [2880, 1864], band: 64, cutout: 312, radius: 14 },
        { id: "pro-14", name: "MacBook Pro 14-inch", size: [3024, 1964], band: 74, cutout: 360, radius: 17 },
        { id: "pro-16", name: "MacBook Pro 16-inch", size: [3456, 2234], band: 74, cutout: 360, radius: 17 }
    ]
    // A screen's band in its logical pixels, or null for a plain screen. notchPanel in flow-settings.ini overrides the
    // match, for testing: "none" (every screen plain), or a panel's id (every screen that panel, scaled to its width,
    // with the cutout drawn, since there is no hole to see).
    function bandFor(screen) {
        if (!screen || devicePreferences.notchPanel === "none") return null
        const forced = devicePreferences.notchPanel
        const mode = (displays.find(d => d.output === screen.name) || {}).mode
        const panel = forced ? notchPanels.find(p => p.id === forced)
            : mode ? notchPanels.find(p => p.size[0] === mode[0] && p.size[1] === mode[1]) : null
        if (!panel) return null
        // The panel's pixels in this screen's logical pixels, whatever its scale.
        const k = screen.width / panel.size[0]
        return { panel: panel.id, name: panel.name, simulated: !!forced, height: Math.round(panel.band * k),
                 cutout: Math.round(panel.cutout * k), radius: Math.round(panel.radius * k) }
    }
    Core.Settings {
        id: devicePreferences
        location: root.settingsFile
        property string notchPanel: ""
    }
    // The clock pill on plain screens: "off" (the default), "main" (the main display only) or "all".
    Core.Settings {
        id: pillPreferences
        location: root.settingsFile
        property string clockPill: "off"
    }
    // The main display's connector, as Super+number has it (spaces.py): Main (number 0), or with Main away the
    // lowest-numbered connected one. Before the first refresh, the first screen.
    readonly property string mainDisplayName: {
        const ranked = displays.filter(d => d.output).sort((a, b) => (a.number ?? 99) - (b.number ?? 99))
        return ranked.length ? ranked[0].output : (Quickshell.screens.length ? Quickshell.screens[0].name : "")
    }
    function pillOn(screen) {
        if (!screen || bandFor(screen)) return false
        return pillPreferences.clockPill === "all" || (pillPreferences.clockPill === "main" && screen.name === mainDisplayName)
    }
    // The opened Island on a plain screen: where (a screen's name), and what opened it: "edge" (the pointer resting
    // against the top edge), "press" (a press there), "pill" or "shortcut".
    property bool islandOpen: false
    property string islandScreenName: ""
    property string islandOpenedBy: ""
    property double islandAskedAt: 0
    readonly property var islandScreen: Quickshell.screens.find(screen => screen.name === islandScreenName) || null
    // What the Island opened keeps it open until it closes: a panel, search or the app menu.
    readonly property bool islandHeld: notificationsOpen || quickSettingsOpen || appMenuOpen || searchOpen
    readonly property int islandDwell: 150
    function openIsland(screenName, by) {
        const screen = Quickshell.screens.find(s => s.name === screenName)
        if (!screen || bandFor(screen) || workCovered || switcherOpen) return
        islandScreenName = screenName
        islandOpenedBy = by
        islandAskedAt = Date.now()
        islandOpen = true
    }
    function closeIsland() { islandOpen = false; islandOpenedBy = "" }
    function toggleIsland() {
        const name = displayOutput || (Quickshell.screens.length ? Quickshell.screens[0].name : "")
        if (islandOpen) closeIsland()
        else if (bandFor(Quickshell.screens.find(s => s.name === name))) showToast("The Island is in the band beside the camera")
        else openIsland(name, "shortcut")
    }
    onWorkCoveredChanged: if (workCovered) closeIsland()
    // The opened Island shows what the banner did (the bell's dot, the activity): a banner, critical or not, is done.
    onIslandOpenChanged: if (islandOpen) bannerNotification = null
    // Its screen unplugged: nothing would close it.
    onIslandScreenChanged: if (!islandScreen) closeIsland()
    // A touchscreen: a direct input device (INPUT_PROP_DIRECT) with multitouch positions. The home line
    // shows only with one; looked for at start, every 30 s and when the screens change.
    property bool touchPresent: false
    Process {
        id: touchProbe
        command: ["sh", "-c", 'for d in /sys/class/input/event*/device; do p=$(cat "$d/properties" 2>/dev/null); p=${p##* }; a=$(cat "$d/capabilities/abs" 2>/dev/null); a=${a##* }; [ -n "$p" ] && [ $((0x$p & 2)) -ne 0 ] && [ $((0x${a:-0} & 0x20000000000000)) -ne 0 ] && { echo touch; exit 0; }; done; echo none']
        stderr: SplitParser { onRead: line => console.warn("touch-probe: " + line) }
        stdout: StdioCollector { onStreamFinished: root.touchPresent = this.text.trim() === "touch" }
    }
    Timer { interval: 30000; running: true; repeat: true; triggeredOnStart: true; onTriggered: if (!touchProbe.running) touchProbe.running = true }
    readonly property int screenCount: Quickshell.screens.length
    onScreenCountChanged: if (!touchProbe.running) touchProbe.running = true
    // Moving between spaces on a display: the work indicator shows for a moment, then goes.
    property bool spacesMoving: false
    property string movedFrom: ""
    readonly property string activeSpaceKey: displayOutput + "\n" + (activeSpace ? activeSpace.id : "")
    onActiveSpaceKeyChanged: {
        if (movedFrom && activeSpace && movedFrom.split("\n")[0] === displayOutput && movedFrom !== activeSpaceKey) {
            spacesMoving = true
            movingTimer.restart()
        }
        movedFrom = activeSpace ? activeSpaceKey : ""
    }
    Timer { id: movingTimer; interval: 1200; onTriggered: root.spacesMoving = false }
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
        // Overview's snapshot of this space once it has settled; a refresh hands over a new object
        // for the same space (a title changes), which is not a move.
        if (space && String(space.id) !== settledSpaceId) { settledSpaceId = String(space.id); snapshotSettle.restart() }
    }
    property string spacesError: ""
    property var pendingSpaceActions: []
    // Actions of the user's, not background jobs. Controls stay enabled through refreshes, automatic layout
    // (when a Group asks for it and its display turned) and automatic recovery: a control that
    // turned disabled would drop keyboard focus. Space actions queue behind them, and flow-recovery waits for
    // flow-spaces on spaces.lock.
    readonly property bool spacesWorking: spacesProcess.running && !["refresh", "auto-layout"].includes(spacesProcess.command[1])
    readonly property bool recoveryWorking: recoveryProcess.running && !String(recoveryProcess.command[1]).startsWith("auto-")
    // Desk's Save session button, and search's. From search, Desk is closed, so the result is a toast.
    property bool recoveryToast: false
    function startSaveSession(fromSearch) {
        const busy = recoveryProcess.running ? "Wait for automatic recovery to finish" : spacesWorking ? "Wait for the current action" : ""
        if (busy) { if (fromSearch) showToast(busy); else recoveryMessage = busy; return }
        recoveryToast = fromSearch
        recoveryProcess.command = ["flow-recovery", "save"]
        recoveryProcess.running = true
    }
    function renameGroup(id, label) {
        if (recoveryProcess.running || spacesWorking || pendingSpaceActions.length) { spacesError = "Wait for the current action"; return }
        recoveryProcess.command = ["flow-recovery", "rename-group", id, label]
        recoveryProcess.running = true
    }
    function spaceAction(action, id) {
        if (action === "refresh") { requestRefresh(); return }
        // A recovery the user started refuses; an automatic one (every 30 s) is waited for.
        if (recoveryWorking) { if (action !== "refresh") spacesError = "Wait for session recovery to finish"; return }
        if (recoveryProcess.running || spacesProcess.running || pendingSpaceActions.length) {
            if (action !== "refresh") {
                if (pendingSpaceActions.length >= 8) spacesError = "Too many pending actions"
                else pendingSpaceActions = pendingSpaceActions.concat([{action: action, id: id}])
            }
            return
        }
        runSpaces(action, id)
    }
    function drainSpaceActions() {
        if (spacesProcess.running || recoveryProcess.running || !pendingSpaceActions.length) return
        const next = pendingSpaceActions[0]
        pendingSpaceActions = pendingSpaceActions.slice(1)
        runSpaces(next.action, next.id)
    }
    function runSpaces(action, id) {
        spacesProcess.command = id ? ["flow-spaces", action, id] : ["flow-spaces", action]
        spacesProcess.running = true
    }
    // Flow's state as flow-spaces or the controller reported it.
    function applySpaces(result) {
        if (result.error) { spacesError = result.error; return }
        if (JSON.stringify(windows) !== JSON.stringify(result.windows)) {
            const active = w => (w.find(item => item.active) || { id: -1 }).id
            if (active(windows) !== active(result.windows)) { appMenuFocusMoves += 1; requestAppIdentity() }
            windows = result.windows
        }
        if (JSON.stringify(displaySpaces) !== JSON.stringify(result.spaces)) displaySpaces = result.spaces
        if (JSON.stringify(layers) !== JSON.stringify(result.layers || [])) layers = result.layers || []
        if (JSON.stringify(incompleteGroups) !== JSON.stringify(result.incomplete_groups || [])) incompleteGroups = result.incomplete_groups || []
        if (JSON.stringify(pins) !== JSON.stringify(result.pins)) pins = result.pins
        if (JSON.stringify(otherPins) !== JSON.stringify(result.other_pins || [])) otherPins = result.other_pins || []
        displayLabel = result.display ? result.display.label : ""
        displayName = result.display ? result.display.name || "" : ""
        displayOutput = result.display ? result.display.output || "" : ""
        displayCount = result.display_count || 1
        if (JSON.stringify(displays) !== JSON.stringify(result.displays || [])) displays = result.displays || []
        displayOrientation = result.orientation || ""
    }
    Process {
        id: spacesProcess
        // The controller reads what the action changed, which Niri may not have sent an event for (a pin).
        onRunningChanged: if (!running) { root.requestRefresh(); if (root.pendingSpaceActions.length) Qt.callLater(root.drainSpaceActions) }
        // Its messages and tracebacks reach the shell's log (the flow-shell unit's journal), as the controller's do.
        stderr: SplitParser { onRead: line => console.warn("flow-spaces: " + line) }
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    const result = JSON.parse(this.text)
                    if (result.error) {
                        root.spacesError = result.error
                        if (spacesProcess.command[1] !== "refresh" && spacesProcess.command[1] !== "auto-layout" && !root.switcherOpen) {
                            // Over Overview or search the error is a toast; elsewhere Desk shows it.
                            if (root.spacesOverviewOpen || root.searchOpen) root.showToast(result.error)
                            else root.deskOpen = true
                        }
                    } else {
                        root.applySpaces(result)
                        if (result.action !== "refresh") root.spacesError = ""
                        if (result.notice) root.showToast(result.notice)
                        if (["focus", "focus-number", "focus-window", "move-to-display"].includes(result.action)) { root.deskOpen = false; root.switcherOpen = false }
                        else if (result.action === "pin" && !root.spacesOverviewOpen) root.deskOpen = true
                    }
                } catch (e) { root.spacesError = "Could not read pinned spaces" }
            }
        }
    }
    Process {
        id: recoveryProcess
        onRunningChanged: if (!running && root.pendingSpaceActions.length) Qt.callLater(root.drainSpaceActions)
        stderr: SplitParser { onRead: line => console.warn("flow-recovery: " + line) }
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    const result = JSON.parse(this.text)
                    root.recoveryMessage = result.error || result.message || "Recovery unavailable"
                    if (recoveryProcess.command[1] === "save" && root.recoveryToast) { root.recoveryToast = false; root.showToast(root.recoveryMessage) }
                    if (recoveryProcess.command[1] === "auto-start" || recoveryProcess.command[1] === "auto-ready") {
                        root.autoRecoveryBlocked = !!result.error
                        if (result.error) root.recoveryPauseReason = result.error
                        // Nothing was reopened, and the saved session was moved aside: said, as the user expected it back.
                        if (result.set_aside) {
                            root.recoverySetAside = result.set_aside
                            root.recoverySetAsideLeft = ""
                            root.showToast("Nothing was reopened; the saved session was kept aside. See Settings")
                        }
                        // A pause from earlier in this session (a shell restart since): said, as when it came.
                        // A recovery that ran and could not finish opens Settings: it runs only at a Niri instance's first
                        // shell start, and not into windows open past its first ten minutes (restore.automatic), so as a
                        // rule before work; a shell restarted early with no marker is the exception.
                        if (result.error && result.paused) root.showToast("Automatic saving is paused. Open Settings to review the session")
                        else if (result.error) { root.deskOpen = true; root.settingsOpen = true; Qt.callLater(function() { root.reveal(recoveryStatus) }) }
                    }
                    if (recoveryProcess.command[1] === "save" && !result.error && recoveryPreferences.sessionRecovery) autoReadyTimer.restart()
                    if (recoveryProcess.command[1] === "restore" && recoveryProcess.command.length > 2) {
                        if (result.complete) root.recoverySetAside = ""
                        else if (result.launched || result.recorded) root.recoverySetAsideLeft = result.recorded ? result.error
                            : "Reopening " + root.recoverySetAside + " did not bring everything back (" + (result.error || result.message).replace(/\.$/, "") + "). Apps that started may still open. Open the rest yourself: reopening it again would open those twice. Once you have checked your workspaces, remove " + root.recoverySetAside + ".reopened to try again."
                    }
                    if (recoveryProcess.command[1] === "rename-group") {
                        root.spacesError = result.error || ""
                        if (!result.error) root.renameGroupId = ""
                    }
                    if (!result.error) root.requestRefresh()
                } catch (e) { root.recoveryMessage = "Could not read recovery result" }
            }
        }
    }
    // At each start, whatever the setting: auto-start when it is on, auto-seen when it is off. Either marks this
    // Niri instance as having had a shell, so a shell started again in it (a crash, an upgrade, the setting
    // switched on by hand) never reopens the saved session into the windows already open (restore.automatic).
    Timer {
        id: autoStartTimer; interval: 3000
        Component.onCompleted: start()
        onTriggered: {
            if (recoveryProcess.running || spacesProcess.running) { restart(); return }
            recoveryProcess.command = ["flow-recovery", recoveryPreferences.sessionRecovery ? "auto-start" : "auto-seen"]
            recoveryProcess.running = true
        }
    }
    Timer {
        id: autoReadyTimer; interval: 200
        onTriggered: {
            if (!recoveryPreferences.sessionRecovery) return
            if (recoveryProcess.running || spacesProcess.running) { restart(); return }
            recoveryProcess.command = ["flow-recovery", "auto-ready"]; recoveryProcess.running = true
        }
    }
    // Automatic saving is the controller's (flow_controller.py): it saves while the shell asks it to, with the
    // setting on, recovery not paused and no Save session, Restore session or rename running (a save during a
    // restore would replace the session it restores, and stop it). It says how each save went. A save that went
    // well leaves the pause as it is: one that ended as the setting was switched must not undo that pause.
    readonly property bool autoSaveWanted: recoveryPreferences.sessionRecovery && !autoRecoveryBlocked && !recoveryWorking
    onAutoSaveWantedChanged: if (controller.connected) {
        controller.write(JSON.stringify({ auto_save: autoSaveWanted }) + "\n")
        controller.flush()
    }
    function autoSaved(result) {
        if (!recoveryPreferences.sessionRecovery) return  // switched off since
        recoveryMessage = result.error || result.message || "Recovery unavailable"
        if (!result.error) return
        autoRecoveryBlocked = true
        recoveryPauseReason = result.error
        // Said, not shown: the pause comes while the user works (two windows of one app is enough), so the Desk
        // stays closed. Settings holds the reason and Save session.
        showToast("Automatic saving paused. Open Settings to review the session")
    }
    // A display that changes size (a rotation) reshapes the Layers Flow shaped on the next refresh, and Niri
    // may send no window event for a workspace with only floating windows.
    readonly property string screenSizes: Quickshell.screens.map(screen => screen.name + " " + screen.width + "x" + screen.height).join(",")
    onScreenSizesChanged: requestRefresh()
    // Automatic layout runs when a Group that asks for it does not suit its display's orientation: after a
    // rotation, a move to another display or the toggle. It waits while a shell surface is open or another
    // action runs, and a pass that leaves the same Groups unsuited is not repeated until something changes.
    property string displayOrientation: ""
    readonly property string autoLayoutDue: {
        if (!displayOrientation) return ""
        const target = displayOrientation === "portrait" ? "stack" : "side-by-side"
        const unsuited = displaySpaces.filter(space => space.auto_layout && space.group_layout !== target)
        return unsuited.length ? displayOrientation + " " + unsuited.map(space => space.group_id + ":" + space.group_layout).join(",") : ""
    }
    readonly property bool autoLayoutIdle: !deskOpen && !switcherOpen && !overviewOpen && !spacesOverviewOpen && !searchOpen
        && !appMenuOpen && !quickSettingsOpen && !notificationsOpen && !spacesProcess.running && !recoveryProcess.running
        && !pendingSpaceActions.length
    property string autoLayoutTried: ""
    onAutoLayoutDueChanged: { if (!autoLayoutDue) autoLayoutTried = ""; autoLayoutCheck.restart() }
    onAutoLayoutIdleChanged: if (autoLayoutIdle) autoLayoutCheck.restart()
    Timer {
        id: autoLayoutCheck; interval: 300
        onTriggered: if (root.autoLayoutDue && root.autoLayoutDue !== root.autoLayoutTried && root.autoLayoutIdle) {
            root.autoLayoutTried = root.autoLayoutDue
            root.spaceAction("auto-layout")
        }
    }
    // For tests/niri_performance_smoke.py: how long the last opening of each surface took inside the shell, from
    // the request to its content. Desk: until it shows the refresh it asked the controller for. Overview:
    // until its first frame with every card, after its snapshot. Search: until it shows a listing. Each entry
    // is {ms, count}.
    property var openStarted: ({})
    property var openTook: ({})
    function openBegins(name) { openStarted[name] = Date.now() }
    function openEnds(name) {
        if (!openStarted[name]) return
        openTook[name] = { ms: Date.now() - openStarted[name], count: ((openTook[name] || {}).count || 0) + 1 }
        delete openStarted[name]
    }
    // Toggles that came while a transition was still running, for tests/niri_desk_overview_smoke.py: each must count.
    property int deskTogglesMidRise: 0
    // Desk and Overview are on screen: shown, and their first frame drawn. The work view's Island and indicators stay
    // until then (Desk and Overview are mapped above them), so the screen never shows work without them first: with
    // Reduce motion, five frames (about 110 ms) of that came before Desk in the guest.
    property bool deskDrawn: false
    property bool overviewDrawn: false
    // Between Overview and Desk, the one going stays on screen until the one coming has drawn its first frame, so the
    // work never shows between them.
    property bool deskHeld: false
    property bool overviewHeld: false
    readonly property bool workCovered: (deskOpen && deskDrawn) || (spacesOverviewOpen && overviewDrawn) || deskHeld || overviewHeld
    onDeskDrawnChanged: if (deskDrawn) overviewHeld = false
    onOverviewDrawnChanged: if (overviewDrawn) deskHeld = false
    onDeskOpenChanged: if (!deskOpen) overviewHeld = false
    onSpacesOverviewOpenChanged: if (!spacesOverviewOpen && !overviewPending) deskHeld = false
    onOverviewPendingChanged: if (!overviewPending && !spacesOverviewOpen) deskHeld = false
    // Held where the change is made, whoever asks for it (a key, a button, search, the IPC): toggleDesk from Overview,
    // openSpacesOverview from Desk. Never longer than 0.6 s from the latest hold.
    function holdDesk() { deskHeld = true; holdTimer.restart() }
    function holdOverview() { overviewHeld = true; holdTimer.restart() }
    Timer { id: holdTimer; interval: 600; onTriggered: { root.deskHeld = false; root.overviewHeld = false } }
    property int overviewAgainWhilePending: 0
    function toggleDesk() {
        if (deskOpen && deskEnter.running) deskTogglesMidRise += 1
        if (!deskOpen && spacesOverviewOpen) holdOverview()   // Overview to Desk: Overview stays until Desk has drawn
        overviewPending = false
        spacesOverviewOpen = false   // one surface at a time
        notificationsOpen = false
        quickSettingsOpen = false
        switcherOpen = false
        appMenuOpen = false
        openMenuWhenReady = false
        if (!deskOpen) { Quickshell.execDetached(["niri", "msg", "action", "close-overview"]); openBegins("desk") }
        deskOpen = !deskOpen
        if (deskOpen) deskRefresh = requestRefresh()
    }

    // The search panel is the launcher; closeLauncher remains the name its callers know.
    function closeLauncher() { closeSearch() }
    function openSearch() {
        if (searchOpen) { closeSearch(); return }
        openSearchPanel(false)
    }

    function launcherBusy() { return searchOpen }
    function otherSurfaceOpen() { return switcherOpen || quickSettingsOpen || notificationsOpen || appMenuOpen }
    function openApps() {
        if (searchOpen) { closeSearch(); return }
        openSearchPanel(true)
    }

    function toggleSwitcher() {
        overviewPending = false
        spacesOverviewOpen = false   // one surface at a time
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

    // Niri's live Overview: real windows, zoomed out. It stays a button in the Spaces switcher.
    function showOverview(action = "open-overview") {
        overviewPending = false
        closeLauncher()
        deskOpen = false
        switcherOpen = false
        spacesOverviewOpen = false
        Quickshell.execDetached(["niri", "msg", "action", action])
    }

    // Flow's Overview, as in the prototype: this display's spaces in one row. Niri offers no capture of
    // a single window, but a space fills its display while it is focused, so each card shows the
    // snapshot taken the last time its space was on screen, the way a phone's app switcher does.
    // Snapshots are taken by grim (wlr-screencopy) into the session's runtime directory: Qt's own
    // grabToImage crashed Quickshell 0.3.2 on Niri, and Niri's screenshot action fills the clipboard.
    property bool spacesOverviewOpen: false
    // Opening waits for a fresh snapshot; any other surface opening meanwhile cancels it.
    property bool overviewPending: false
    // The selection is a space, not a position: refreshes replace the list (a title changes, a window
    // opens) and the selection must stay on the same space.
    property string overviewSpaceId: ""
    readonly property int overviewIndex: {
        const found = displaySpaces.findIndex(space => String(space.id) === overviewSpaceId)
        return found >= 0 ? found : Math.max(0, Math.min(displaySpaces.length - 1, activeSpaceIndex))
    }
    property var spaceSnapshots: ({})   // space id -> {version, look, at} of its snapshot file
    // Snapshots are screen contents: only in the session's private runtime directory, never /tmp.
    readonly property string snapshotDir: Quickshell.env("XDG_RUNTIME_DIR") ? Quickshell.env("XDG_RUNTIME_DIR") + "/flow-snapshots" : ""
    function snapshotUrl(space) {
        const taken = space ? spaceSnapshots[String(space.id)] : null
        const version = taken ? taken.version : 0
        return version ? "file://" + snapshotDir + "/" + space.id + ".jpg?v=" + version : ""
    }
    readonly property var overviewSpace: displaySpaces[overviewIndex] || null
    function pinOfSpace(space) {
        return space ? pins.find(pin => (pin.window_ids || []).some(id => space.members.some(m => m.id === id))) || null : null
    }
    function openSpacesOverview() {
        // Only the plain work view shows the space itself; from Desk or a panel the screen shows them instead.
        const plain = workViewOnly
        if (deskOpen) holdDesk()   // Desk to Overview: Desk stays until Overview has drawn
        notificationsOpen = false
        quickSettingsOpen = false
        switcherOpen = false
        appMenuOpen = false
        openMenuWhenReady = false
        closeLauncher()
        closeSearch()
        if (overviewOpen) Quickshell.execDetached(["niri", "msg", "action", "close-overview"])
        deskOpen = false
        overviewPending = true
        openBegins("overview")
        const show = () => {
            // Desk, search or another panel opened meanwhile: they win, Overview does not stack on them.
            if (!overviewPending || deskOpen || searchOpen || switcherOpen || quickSettingsOpen
                || notificationsOpen || appMenuOpen || overviewOpen) { overviewPending = false; return }
            // Open first, then no longer pending: the cards built during the snapshot stay.
            overviewSpaceId = activeSpace ? String(activeSpace.id) : ""
            spacesOverviewOpen = true
            overviewPending = false
        }
        // The space on screen now is the freshest: snapshot it, then show the row. Not while a move is
        // still settling, which would catch the scroll halfway under the old space's name.
        if (plain && !snapshotSettle.running) snapshotActiveSpace(show)
        else show()
    }
    function closeSpacesOverview() { overviewPending = false; spacesOverviewOpen = false }
    function selectOverview(index) {
        const space = displaySpaces[Math.max(0, Math.min(displaySpaces.length - 1, index))]
        if (space) overviewSpaceId = String(space.id)
    }
    function moveOverview(step) { selectOverview(overviewIndex + step) }
    function chooseOverviewSpace(index) {
        const space = displaySpaces[index]
        closeSpacesOverview()
        if (space && !space.active) spaceAction("focus-window", String(space.id))
    }
    // "Again to reveal your desk": the same gesture or key that opened Overview goes one level further.
    function revealDeskFromOverview() {
        if (deskOpen) closeSpacesOverview()
        else toggleDesk()   // which closes Overview, held until Desk has drawn
    }
    function toggleSpacesOverview() {
        // Again while Overview is still opening (taking its snapshot) is again all the same: on to Desk. Ignored, a
        // quick Super+O Super+O left Overview open, the second press lost.
        if (overviewPending) { overviewAgainWhilePending += 1; revealDeskFromOverview(); return }
        if (spacesOverviewOpen) revealDeskFromOverview()
        else if (deskOpen) toggleDesk()
        else openSpacesOverview()
    }

    // Snapshots: only in the plain work view, after a space has settled, never of the shell's own surfaces.
    property string settledSpaceId: ""
    // The work view with no shell surface over it but what rests there (the clock pill, the band, the edge strip).
    readonly property bool restingView: !deskOpen && !switcherOpen && !spacesOverviewOpen && !overviewOpen
        && !searchOpen && !appMenuOpen && !quickSettingsOpen && !notificationsOpen
    readonly property bool workViewOnly: restingView && !islandOpen
    // A settled space is not taken again while its picture is current: under a minute old, and of the same look
    // (what the shell knows of the space, and the display's size). Opening Overview takes the space on screen
    // afresh (openSpacesOverview).
    readonly property int snapshotFresh: 60000
    readonly property var snapshotIgnores: ["add_label", "can_add", "can_remove", "can_widen", "can_narrow"]  // controls' state
    function spaceLook(space, screen) {
        return JSON.stringify(space, (key, value) => snapshotIgnores.includes(key) ? undefined : value)
            + " " + screen.name + " " + screen.width + "x" + screen.height
    }
    function snapshotCurrent(space, screen) {
        const taken = space && screen ? spaceSnapshots[String(space.id)] : null
        return !!taken && taken.look === spaceLook(space, screen) && Date.now() - taken.at < snapshotFresh
    }
    Timer {
        id: snapshotSettle; interval: 700
        onTriggered: if (root.workViewOnly && !root.snapshotCurrent(root.activeSpace, root.workScreen)) root.snapshotActiveSpace(null)
    }
    Component.onDestruction: if (snapshotDir) Quickshell.execDetached(["rm", "-rf", "--", snapshotDir])
    function snapshotActiveSpace(done) {
        const space = activeSpace
        const screen = workScreen
        if (!space || !workViewOnly || !screen || !snapshotDir || snapshotProcess.running) { if (done) done(); return }
        snapshotProcess.key = String(space.id)
        snapshotProcess.look = spaceLook(space, screen)
        snapshotProcess.done = done
        // At the display's own resolution: asking grim for a smaller picture costs more than the whole capture
        // (docs/performance.md), and Overview waits for it. Its card decodes it at its own size. Written
        // whole, then renamed, so a card never shows half.
        snapshotProcess.command = ["sh", "-c", 'mkdir -p -m 700 "$1" && grim -o "$2" -t jpeg -q 72 "$1/$3.tmp" && mv -f "$1/$3.tmp" "$1/$3.jpg"',
            "sh", root.snapshotDir, screen.name, snapshotProcess.key]
        snapshotProcess.running = true
    }
    Process {
        id: snapshotProcess
        stderr: SplitParser { onRead: line => console.warn("snapshot: " + line) }
        property string key: ""
        property string look: ""
        property var done: null
        onExited: code => {
            const done = snapshotProcess.done
            snapshotProcess.done = null
            if (code === 0) {
                const next = {}, gone = []
                // Keep only spaces that still exist; the others' pictures are deleted.
                for (const id in root.spaceSnapshots)
                    if (root.displaySpaces.some(space => String(space.id) === id)) next[id] = root.spaceSnapshots[id]
                    else gone.push(root.snapshotDir + "/" + id + ".jpg")
                const before = root.spaceSnapshots[snapshotProcess.key]
                next[snapshotProcess.key] = { version: (before ? before.version : 0) + 1, look: snapshotProcess.look, at: Date.now() }
                root.spaceSnapshots = next
                if (gone.length) Quickshell.execDetached(["rm", "-f", "--"].concat(gone))
            }
            if (done) done()
        }
    }

    // Flow's search, as in the prototype: one palette for apps, spaces, windows, files and settings.
    // search.py --json lists the results; the shell filters them as you type and runs the chosen one.
    property bool searchOpen: false
    property bool searchAppsOnly: false
    property var searchResults: []
    property bool searchFailed: false   // flow-search has not printed a list of results: say so, not "Nothing matches"
    property string searchQuery: ""
    property int searchIndex: 0
    readonly property var searchMatches: {
        const query = searchQuery.trim().toLowerCase()
        // Files found by name while typing come after search.py's results, less the recent files among them.
        const listed = new Set(searchResults.filter(r => r.kind === "file").map(r => r.value))
        const pool = searchAppsOnly ? searchResults.filter(r => r.kind === "app")
            : searchResults.concat(searchFileResults.filter(r => !listed.has(r.value)))
        if (!query) return pool.slice(0, 60)
        const scored = []
        pool.forEach((result, index) => {
            const title = result.title.toLowerCase(), detail = result.detail.toLowerCase()
            const words = title.split(/[\s·:/._-]+/)
            const score = title === query ? 0 : title.startsWith(query) ? 1 : words.some(w => w.startsWith(query)) ? 2
                : title.includes(query) ? 3 : detail.includes(query) ? 4
                : query.split(/\s+/).every(w => title.includes(w)) ? 5 : -1
            if (score >= 0) scored.push({score: score, index: index, result: result})
        })
        // Ties keep search.py's order (an app before the same app as a Layer): V4's sort is not stable.
        scored.sort((a, b) => a.score - b.score || a.index - b.index)
        return scored.slice(0, 60).map(entry => entry.result)
    }
    // The chosen row stays chosen while results arrive for the same query (files come in as they are found);
    // a new query chooses the first row again.
    property string searchChosenKey: ""
    property string searchChosenFor: ""
    onSearchIndexChanged: {
        const chosen = searchMatches[searchIndex]
        // Choosing another row disarms a command armed to ask first; the same row moving down does not.
        if (!(chosen && chosen.confirm && chosen.value === searchArmed)) searchArmed = ""
        searchChosenKey = chosen ? chosen.kind + "\n" + chosen.value : ""
        searchChosenFor = searchQuery
    }
    onSearchMatchesChanged: {
        const kept = searchChosenFor === searchQuery
            ? searchMatches.findIndex(r => r.kind + "\n" + r.value === searchChosenKey) : -1
        if (kept >= 0) searchIndex = kept
        else { searchIndex = 0; searchIndexChanged() }
        // A new model scrolls the list back to its top; the chosen row stays in sight.
        if (searchMatches.length) Qt.callLater(() => searchView.positionViewAtIndex(searchIndex, ListView.Contain))
    }
    // One listing per opening of search. A listing still running when search opens again lists what was there
    // before (windows since closed): it is sent SIGTERM and its answer dropped, so only the newest one is shown.
    // That stops search.py, not the `flow-spaces refresh` it started, which finishes under spaces.lock.
    property var searchListing: null   // the listing this opening waits for; null once it has answered
    Component {
        id: searchLister
        Process {
            id: lister
            stderr: SplitParser { onRead: line => console.warn("flow-search: " + line) }
            command: ["flow-search", "--json"]
            // Quickshell finishes the stream before running turns false (a stopped listing's ends at once,
            // empty); one that never starts only turns running false, and search says it failed.
            stdout: StdioCollector {
                onStreamFinished: {
                    if (root.searchListing !== lister) return
                    let rows = null
                    try { rows = JSON.parse(this.text) } catch (error) {}
                    root.searchFailed = !Array.isArray(rows)
                    root.searchResults = Array.isArray(rows) ? rows : []
                    if (root.searchResults.length) root.openEnds("search")
                }
            }
            onRunningChanged: if (!running) {
                if (root.searchListing === lister) root.searchListing = null
                destroy()
            }
        }
    }
    // Files by name, walked in the user folders while typing (flow-search --files): one walk per query. A walk for
    // an older query is stopped as soon as the query changes, and an answer it still gives is dropped. Nothing is
    // indexed or stored, and the query goes on stdin: a command line is readable by every user of the machine.
    property var searchFileResults: []
    property var searchWalk: null   // the walk for the current query; null once it has answered
    Component {
        id: searchWalker
        Process {
            id: walker
            stderr: SplitParser { onRead: line => console.warn("flow-search: " + line) }
            property string query: ""
            command: ["flow-search", "--files"]
            stdinEnabled: true
            onStarted: write(query + "\n")
            stdout: StdioCollector {
                onStreamFinished: {
                    if (root.searchWalk !== walker) return
                    let rows = null
                    try { rows = JSON.parse(this.text) } catch (error) {}
                    root.searchFileResults = Array.isArray(rows) ? rows : []
                }
            }
            onRunningChanged: if (!running) {
                if (root.searchWalk === walker) root.searchWalk = null
                destroy()
            }
        }
    }
    Timer { id: searchWalkTimer; interval: 150; onTriggered: root.walkSearchFiles() }
    onSearchQueryChanged: {
        searchArmed = ""
        dropSearchWalk()
        if (searchQuery.trim().length < 3) searchFileResults = []
        searchWalkTimer.restart()
    }
    onSearchOpenChanged: if (!searchOpen) { searchArmed = ""; walkSearchFiles() }
    function dropSearchWalk() {
        const stale = searchWalk
        searchWalk = null
        if (stale) stale.running = false
    }
    function walkSearchFiles() {
        const query = searchQuery.trim()
        dropSearchWalk()
        if (!searchOpen || searchAppsOnly || query.length < 3) { searchFileResults = []; return }
        searchWalk = searchWalker.createObject(root, { query: query })
        searchWalk.running = true
    }
    function listSearchResults() {
        const stale = searchListing
        searchListing = searchLister.createObject(root)
        if (stale) stale.running = false
        searchListing.running = true
    }
    // Apps and files open through flow-search, whose wrapper has gio. One process per open, so each failure
    // is shown: one that ran and failed, and one that never started (a Process that cannot start never exits).
    Component {
        id: searchOpener
        Process {
            // Why an open failed: flow-search --open says it on stderr.
            stderr: SplitParser { onRead: line => console.warn("flow-search: " + line) }
            property string failure: ""
            property bool began: false
            property bool done: false
            function finish(failed) {
                if (done) return
                done = true
                if (failed) root.showToast(failure)
                destroy()
            }
            onStarted: began = true
            onExited: exitCode => finish(exitCode !== 0)
            onRunningChanged: if (!running && !began) finish(true)
        }
    }
    function openSearchResult(result) {
        const failure = result.kind === "command" ? "Couldn’t " + result.title.toLowerCase() : "Couldn’t open " + result.title
        const opener = searchOpener.createObject(root, { failure: failure, command: ["flow-search", "--open", result.kind, result.value] })
        opener.running = true
    }
    function openSearchPanel(appsOnly) {
        overviewPending = false
        notificationsOpen = false
        quickSettingsOpen = false
        openMenuWhenReady = false
        appMenuOpen = false
        switcherOpen = false
        spacesOverviewOpen = false
        closeLauncher()
        searchAppsOnly = appsOnly
        searchQuery = ""
        searchIndex = 0
        searchResults = []   // never the last run's: its windows may have closed
        searchFileResults = []
        searchFailed = true    // until flow-search prints its list: one that never starts never answers
        searchOpen = true
        openBegins("search")
        listSearchResults()
    }
    function closeSearch() { searchOpen = false }
    // A command that ends the session or stops the computer runs only when chosen twice within 5 s: the first
    // choice arms it and its row says so. Typing, choosing another row or closing search disarms it.
    property string searchArmed: ""
    property real searchArmedAt: 0
    Timer { id: searchArmTimer; interval: 5000; onTriggered: root.searchArmed = "" }
    function runSearchResult(result) {
        if (!result) return
        if (result.confirm && searchArmed !== result.value) {
            searchArmed = result.value
            searchArmedAt = Date.now()
            searchArmTimer.restart()
            return
        }
        // A double-click, or Enter pressed twice at once, is one choice, not a second one.
        if (result.confirm && Date.now() - searchArmedAt < 500) return
        searchArmed = ""
        searchOpen = false
        if (result.kind !== "settings" && !(result.kind === "action" && (result.value === "desk" || result.value === "overview"))) deskOpen = false
        if (result.kind === "app" || result.kind === "file" || result.kind === "command") openSearchResult(result)
        else if (result.kind === "pin") spaceAction("focus", result.value)
        else if (result.kind === "window") spaceAction("focus-window", result.value)
        else if (result.kind === "layer") spaceAction("open-layer", result.value)
        else if (result.kind === "action") {
            if (result.value === "desk") { if (!deskOpen) toggleDesk() }
            else if (result.value === "overview") openSpacesOverview()
            else if (result.value === "notifications") { if (!notificationsOpen) toggleNotifications() }
            else if (result.value === "pin") spaceAction("pin")
            else if (result.value === "save-session") startSaveSession(true)
        }
        else if (result.kind === "settings") {
            if (result.value === "quick_open") { if (!quickSettingsOpen) toggleQuickSettings() }
            else {
                if (!deskOpen) toggleDesk()
                settingsOpen = true
                Qt.callLater(function() { root.reveal(menuSwitch) })
            }
        }
    }

    // Floe's appearance preference is per-user and does not change app/window state.
    FloeTheme { id: floeTheme; light: appearancePreferences.lightMode }
    // One setting per Core.Settings, as for flow-settings.ini above.
    readonly property string appearanceFile: "file://" + (Quickshell.env("XDG_CONFIG_HOME") || Quickshell.env("HOME") + "/.config") + "/floe-appearance.ini"
    Core.Settings {
        id: appearancePreferences
        location: root.appearanceFile
        property bool lightMode: false
    }
    Core.Settings {
        id: motionPreferences
        location: root.appearanceFile
        property bool reduceMotion: false
    }
    // Motion follows the brand's quick token (120 ms). Linux offers Quickshell no reduced-motion signal,
    // so it is a per-user preference in Desk settings; with it on, every change is instant.
    readonly property int motionQuick: motionPreferences.reduceMotion ? 0 : 120
    readonly property int motionStandard: motionPreferences.reduceMotion ? 0 : 220
    // The brand's standard curve, cubic-bezier(.22, 1, .36, 1).
    readonly property var standardCurve: [0.22, 1, 0.36, 1, 1, 1]
    // One clock for every "busy, amount unknown" indicator, so a card rebuilt by an activity update
    // picks up where the last one was instead of starting its sweep again. 0–1, looping every 1.6 s, by
    // elapsed time. It runs only while an indicator is on screen, since each step repaints the surface showing
    // it: a breathing dot in the island, Overview's or Desk's (each island says whether its dot breathes), or a
    // sweeping bar on Desk. Dots step 12.5 times a second; a sweeping bar needs 60. docs/performance.md has
    // the measurements.
    property real busyPhase: 0
    readonly property bool busySweeping: deskWindow.visible && root.motionQuick > 0 && root.activities.some(a => !(a.progress >= 0))
    readonly property bool busyBreathing: (workIsland.visible && workIslandContent.breathing)
        || (spacesOverview.visible && overviewIsland.breathing) || (deskWindow.visible && deskIsland.breathing)
    property real busyLast: 0
    Timer {
        id: busyClock
        interval: root.busySweeping ? 16 : 80; repeat: true
        running: root.busySweeping || root.busyBreathing
        onRunningChanged: root.busyLast = Date.now()
        onTriggered: {
            const now = Date.now()
            root.busyPhase = (root.busyPhase + Math.max(0, Math.min(now - root.busyLast, 200)) / 1600) % 1
            root.busyLast = now
        }
    }
    // Line icons on a 24-unit grid, drawn by the shell so no icon font is needed.
    readonly property var iconPaths: ({
        "bell": "M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 2h-15z M10 21h4",
        "sliders": "M4 8h8 M18 8h2 M4 16h2 M12 16h8 M13 8a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0 -5 0 M6 16a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0 -5 0",
        "search": "M4.5 11a6.5 6.5 0 1 0 13 0a6.5 6.5 0 1 0 -13 0 M16 16l4 4",
        "left": "M14.5 6l-6 6l6 6", "right": "M9.5 6l6 6l-6 6",
        "up": "M6 14.5l6 -6l6 6", "down": "M6 9.5l6 6l6 -6",
        "close": "M7 7l10 10 M17 7l-10 10", "plus": "M12 5.5v13 M5.5 12h13", "minus": "M5.5 12h13",
        // Search result kinds.
        "app": "M8 4.5h8a3.5 3.5 0 0 1 3.5 3.5v8a3.5 3.5 0 0 1 -3.5 3.5h-8a3.5 3.5 0 0 1 -3.5 -3.5v-8a3.5 3.5 0 0 1 3.5 -3.5z",
        "window": "M7 4.5h10a2.5 2.5 0 0 1 2.5 2.5v10a2.5 2.5 0 0 1 -2.5 2.5h-10a2.5 2.5 0 0 1 -2.5 -2.5v-10a2.5 2.5 0 0 1 2.5 -2.5z M4.5 9h15",
        "file": "M7.5 3.5h6l5 5v10.5a1.5 1.5 0 0 1 -1.5 1.5h-9.5a1.5 1.5 0 0 1 -1.5 -1.5v-14a1.5 1.5 0 0 1 1.5 -1.5z M13.5 3.5v5h5",
        "pin": "M9 4h6 M10 4l-1 6l-3 3h12l-3 -3l-1 -6 M12 13v7",
        "layer": "M12 4.5l7.5 4l-7.5 4l-7.5 -4z M4.5 12.5l7.5 4l7.5 -4 M4.5 16.5l7.5 4l7.5 -4",
        "power": "M12 4v7.5 M7.8 6.8a7 7 0 1 0 8.4 0",
        // Desk's tools.
        "grid": "M5 5h5v5h-5z M14 5h5v5h-5z M5 14h5v5h-5z M14 14h5v5h-5z",
        "folder": "M4 7.5a2 2 0 0 1 2 -2h3.5l2 2h6.5a2 2 0 0 1 2 2v7a2 2 0 0 1 -2 2h-12a2 2 0 0 1 -2 -2z"
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
    // How far a control scrolled into view keeps from the edge: its focus ring is 3 px outside it.
    readonly property real focusRoom: 8
    // Scroll every Flickable around a control (Desk, the switcher and its row of cards, the panels' scroll
    // views) so the control shows whole, with room for its focus ring. Controls call it when they take focus.
    function reveal(item) {
        for (let view = item.parent; view; view = view.parent) {
            if (view.flickableDirection === undefined) continue
            const at = item.mapToItem(view.contentItem, 0, 0)
            if (view.contentHeight > view.height) {
                const top = at.y - focusRoom, bottom = at.y + item.height + focusRoom
                if (top < view.contentY) view.contentY = Math.max(-view.topMargin, top)
                else if (bottom > view.contentY + view.height)
                    view.contentY = Math.min(view.contentHeight + view.bottomMargin - view.height, bottom - view.height)
            }
            if (view.contentWidth > view.width) {
                const left = at.x - focusRoom, right = at.x + item.width + focusRoom
                if (left < view.contentX) view.contentX = Math.max(-view.leftMargin, left)
                else if (right > view.contentX + view.width)
                    view.contentX = Math.min(view.contentWidth + view.rightMargin - view.width, right - view.width)
            }
        }
    }
    component FlowButton: Button {
        id: control
        // secondary: the ordinary control. primary: the one main action of a surface. quiet: a soft tint, for
        // repeated and low-priority actions. danger: a quiet action that removes or closes. plain: no fill at
        // rest, for a control whose container already draws its boundary (a switcher card), or a link-like
        // way on in a quiet row of them (Desk's foot), which hover and focus still reveal. glass: the
        // prototype's Desk field, cards and tiles: a faint wash of the text colour over the backdrop, with a
        // quiet edge that turns to the accent under the pointer. Each one is named by its text or icon.
        property string variant: "secondary"
        // The current choice among siblings (the focused app, the active Layer): a leading dot, a 2 px
        // border, a heavier label and "Current" for assistive technology, so it never depends on tint alone.
        property bool selected: false
        // The panel this button opens is showing (Desk, search, quick settings): the same fill and border as
        // a selected button, no dot, and "Open" for assistive technology. It is not a choice among siblings.
        property bool open: false
        property string glyph: ""
        property real radius: 10
        // Where the 2 px focus ring starts, from the control's edge: 3 px outside, concentric.
        property real focusRingInset: -3
        readonly property color labelColor: variant === "primary" ? floeTheme.onAccent : variant === "danger" ? floeTheme.danger
            : selected || variant === "secondary" ? floeTheme.textPrimary : floeTheme.textSecondary
        focusPolicy: Qt.StrongFocus
        hoverEnabled: true
        // A control that takes focus from the keyboard scrolls into view; a press does not scroll it away.
        onActiveFocusChanged: if (activeFocus && control.focusReason !== Qt.MouseFocusReason) root.reveal(control)
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
                    : control.selected || control.open ? floeTheme.surfaceSelected
                    : control.variant === "glass" ? Qt.alpha(floeTheme.textPrimary, control.down || control.hovered ? 0.08 : 0.04)
                    : control.down || control.hovered ? floeTheme.surfaceSelected
                    : control.variant === "secondary" ? floeTheme.surface
                    : control.variant === "danger" ? Qt.alpha(floeTheme.danger, 0.1)
                    : control.variant === "plain" ? "transparent" : Qt.alpha(floeTheme.surfaceSelected, 0.6)
                // Functional borders stay at full strength (3:1 against the surfaces, brand guidelines), except
                // glass, whose controls are named by their text and icons (design/ACCESSIBILITY_AUDIT.md).
                border.width: control.selected || control.open ? 2 : control.variant === "secondary" || control.variant === "glass" ? 1 : 0
                border.color: control.selected || control.open ? floeTheme.borderStrong
                    : control.variant === "glass" ? (control.hovered ? Qt.alpha(floeTheme.accent, 0.6) : Qt.alpha(floeTheme.border, 0.5))
                    : control.hovered ? floeTheme.borderStrong : floeTheme.border
                Behavior on color { ColorAnimation { duration: root.motionQuick } }
                Behavior on border.color { ColorAnimation { duration: root.motionQuick } }
            }
            Rectangle {
                // Keyboard focus: a 2 px ring 3 px outside the control, concentric with it.
                visible: control.activeFocus
                anchors.fill: parent; anchors.margins: control.focusRingInset; radius: control.radius - control.focusRingInset
                color: "transparent"; border.width: 2; border.color: floeTheme.focus
            }
        }
    }
    component IslandButton: FlowButton {
        // Inset 4 px in the Island's tab: 44 px to touch (the brand's minimum), 36 px to see, and radius
        // 14 − 4 = 10, concentric with the tab's lower corners.
        id: islandButton
        variant: "quiet"
        leftPadding: 12; rightPadding: 12
        // Inside the 44 px target, around the 36 px pill: the tab leaves no room outside.
        focusRingInset: 1
        readonly property bool capsule: true
        background: Item {
            Rectangle {
                anchors.fill: parent; anchors.margins: 4; radius: 10
                color: islandButton.selected || islandButton.open || islandButton.down || islandButton.hovered ? floeTheme.surfaceSelected : "transparent"
                border.width: islandButton.selected || islandButton.open ? 2 : 0; border.color: floeTheme.borderStrong
                Behavior on color { ColorAnimation { duration: root.motionQuick } }
            }
            Rectangle {
                visible: islandButton.activeFocus
                anchors.fill: parent; anchors.margins: islandButton.focusRingInset; radius: 13
                color: "transparent"; border.width: 2; border.color: floeTheme.focus
            }
        }
    }
    // A control in a notched MacBook's band: as tall as the band, on black whatever the theme (the band joins the
    // camera's cutout), a glyph or a word, with a soft tint under the pointer.
    component BandButton: Button {
        id: bandButton
        property string glyph: ""
        property real band: 32
        property bool open: false
        readonly property color ink: open || hovered || down ? floeTheme.bandText : floeTheme.bandMuted
        implicitHeight: band
        implicitWidth: glyph ? Math.max(band, bandIcon.size + 16) : bandLabel.implicitWidth + 18
        focusPolicy: Qt.StrongFocus
        hoverEnabled: true
        Accessible.description: open ? "Open" : ""
        background: Rectangle {
            anchors.fill: parent; anchors.margins: 2; radius: height / 2
            color: bandButton.open || bandButton.down ? floeTheme.bandPressed : bandButton.hovered ? floeTheme.bandHover : "transparent"
            border.width: bandButton.activeFocus ? 2 : 0; border.color: floeTheme.bandFocus
        }
        contentItem: Item {
            FlowIcon { id: bandIcon; visible: !!bandButton.glyph; anchors.centerIn: parent; name: bandButton.glyph; color: bandButton.ink; size: Math.max(16, Math.round(bandButton.band * 0.55)) }
            Text {
                id: bandLabel
                visible: !bandButton.glyph; anchors.centerIn: parent
                text: bandButton.text; textFormat: Text.PlainText; color: bandButton.ink
                font.pixelSize: Math.max(12, Math.round(bandButton.band * 0.42)); font.weight: Font.DemiBold
                font.family: floeTheme.interfaceFont; font.features: { "tnum": 1 }
            }
        }
    }
    // The home indicator: a short line, after iOS. Tap it or drag it up: from work to Overview, from
    // Overview to Desk ("again to reveal your desk"), from Desk back to work.
    component HomeIndicator: MouseArea {
        id: homeArea
        hoverEnabled: true
        property real pressY: 0
        property bool dragged: false
        // Drawn as under the pointer or pressed: the line at its full width, in the accent (pointer_target).
        readonly property bool lit: line.width === 65 && Qt.colorEqual(line.color, floeTheme.accent)
        onPressed: mouse => { pressY = mouse.y; dragged = false }
        onPositionChanged: mouse => { if (pressed && pressY - mouse.y > 14 && !dragged) { dragged = true; root.toggleSpacesOverview() } }
        onClicked: if (!dragged) root.toggleSpacesOverview()
        Accessible.role: Accessible.Button
        Accessible.name: root.spacesOverviewOpen ? "Reveal your Desk" : root.deskOpen ? "Return to work" : "Show Overview"
        Rectangle {
            // The halo: the line stands out on light and dark apps alike.
            anchors.centerIn: line
            width: line.width + 6; height: line.height + 6; radius: height / 2
            color: Qt.alpha(floeTheme.canvas, 0.55)
        }
        Rectangle {
            id: line
            anchors { horizontalCenter: parent.horizontalCenter; bottom: parent.bottom; bottomMargin: 8 }
            width: homeArea.containsMouse || homeArea.pressed ? 65 : 48; height: 4; radius: 2
            color: homeArea.containsMouse || homeArea.pressed ? floeTheme.accent : floeTheme.textSecondary
            Behavior on width { NumberAnimation { duration: root.motionQuick; easing.type: Easing.BezierSpline; easing.bezierCurve: root.standardCurve } }
            Behavior on color { ColorAnimation { duration: root.motionQuick } }
        }
    }
    // A row of a system panel, after the prototype's: a label, a value at the right and a chevron when it
    // opens something, over a hairline. A row that only shows a value is a PanelValue.
    component PanelRow: Button {
        id: panelRow
        property string value: ""
        property color valueColor: floeTheme.textMuted
        property bool chevron: true
        property bool last: false
        focusPolicy: Qt.StrongFocus
        hoverEnabled: true
        Keys.onReturnPressed: panelRow.clicked()
        Keys.onEnterPressed: panelRow.clicked()
        implicitHeight: 48; padding: 0
        Accessible.name: value ? text + ", " + value : text
        contentItem: Item {
            Text {
                id: rowLabel
                anchors.verticalCenter: parent.verticalCenter
                width: Math.min(implicitWidth, parent.width * 0.6)
                text: panelRow.text; textFormat: Text.PlainText; elide: Text.ElideRight
                color: floeTheme.textPrimary; font.pixelSize: 14; font.weight: Font.Medium; font.family: floeTheme.interfaceFont
            }
            Text {
                anchors { left: rowLabel.right; leftMargin: 12; right: rowChevron.visible ? rowChevron.left : parent.right
                          rightMargin: rowChevron.visible ? 4 : 0; verticalCenter: parent.verticalCenter }
                text: panelRow.value; textFormat: Text.PlainText; elide: Text.ElideRight; horizontalAlignment: Text.AlignRight
                color: panelRow.valueColor; font.pixelSize: 14; font.family: floeTheme.interfaceFont; font.features: { "tnum": 1 }
            }
            FlowIcon {
                id: rowChevron
                visible: panelRow.chevron
                anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                name: "right"; size: 16; color: floeTheme.textMuted
            }
        }
        background: Item {
            Rectangle {
                // The tint reaches 10 px past the text, as a list row's does.
                anchors { fill: parent; leftMargin: -10; rightMargin: -10; topMargin: 2; bottomMargin: 2 }
                radius: 10
                color: panelRow.down || panelRow.hovered ? Qt.alpha(floeTheme.textPrimary, 0.06) : "transparent"
                Behavior on color { ColorAnimation { duration: root.motionQuick } }
            }
            Rectangle {
                visible: !panelRow.last
                anchors.bottom: parent.bottom; width: parent.width; height: 1
                color: Qt.alpha(floeTheme.border, 0.3)
            }
            Rectangle {
                visible: panelRow.activeFocus
                anchors { fill: parent; leftMargin: -13; rightMargin: -13; topMargin: -1; bottomMargin: -1 }
                radius: 13; color: "transparent"; border.width: 2; border.color: floeTheme.focus
            }
        }
    }
    // A row that only shows a value: the same layout as PanelRow, without a button.
    component PanelValue: Item {
        id: panelValue
        property string text
        property string value
        implicitHeight: 48
        Accessible.role: Accessible.StaticText
        Accessible.name: text + ", " + value
        Text {
            id: valueLabel
            anchors.verticalCenter: parent.verticalCenter
            width: Math.min(implicitWidth, parent.width * 0.6)
            text: panelValue.text; textFormat: Text.PlainText; elide: Text.ElideRight
            color: floeTheme.textPrimary; font.pixelSize: 14; font.weight: Font.Medium; font.family: floeTheme.interfaceFont
        }
        Text {
            anchors { left: valueLabel.right; leftMargin: 12; right: parent.right; verticalCenter: parent.verticalCenter }
            text: panelValue.value; textFormat: Text.PlainText; elide: Text.ElideRight; horizontalAlignment: Text.AlignRight
            color: floeTheme.textMuted; font.pixelSize: 14; font.family: floeTheme.interfaceFont; font.features: { "tnum": 1 }
        }
    }
    // A system panel, after the prototype's: it hangs 12 px below the Island, 370 px wide (less on a
    // narrow screen), radius 19 with 23 px inside, a title, any tools, a close button, then its rows. It
    // drops in over 220 ms, or at once when motion is reduced. Escape and the close button ask to close.
    component SystemPanel: PanelWindow {
        id: systemPanel
        property bool shown: false
        property string title
        property alias tools: panelTools.data
        default property alias content: panelBody.data
        signal closeRequested()
        // Tab can reach a control below the fold (a long list of notifications): scroll it into view.
        readonly property Item focusedItem: panelCard.Window.activeFocusItem
        onFocusedItemChanged: {
            let item = focusedItem
            while (item && item !== panelBody) item = item.parent
            if (!item) return
            const flick = panelScroller.contentItem
            const top = focusedItem.mapToItem(panelBody, 0, 0).y - 8
            const bottom = top + focusedItem.height + 16
            if (top < flick.contentY) flick.contentY = Math.max(0, top)
            else if (bottom > flick.contentY + flick.height) flick.contentY = Math.min(bottom - flick.height, Math.max(0, flick.contentHeight - flick.height))
        }
        visible: shown
        // With the Island open, on its screen (it may be another display's); else on the focused display's.
        screen: root.panelScreen
        anchors.top: true
        implicitWidth: Math.max(0, Math.min(370, screen.width - 24))
        // 13 above the header, 44 for it, 6 below it, and 19 under the rows (23 with their padding).
        implicitHeight: Math.min(screen.height - 80, panelBody.implicitHeight + 82)
        // Below the opened Island, or the band, or near the top edge.
        margins.top: root.panelTop
        color: "transparent"; exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: shown ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None
        onVisibleChanged: if (visible && root.motionStandard) panelEnter.restart()
        ParallelAnimation {
            id: panelEnter
            NumberAnimation { target: panelCard; property: "opacity"; from: 0; to: 1; duration: root.motionStandard; easing.type: Easing.BezierSpline; easing.bezierCurve: root.standardCurve }
            NumberAnimation { target: panelCard; property: "y"; from: -8; to: 0; duration: root.motionStandard; easing.type: Easing.BezierSpline; easing.bezierCurve: root.standardCurve }
        }
        Rectangle {
            id: panelCard
            width: parent.width; height: parent.height; radius: 19
            color: floeTheme.surface; border.width: 1; border.color: Qt.alpha(floeTheme.border, 0.5)
            FocusScope {
                anchors.fill: parent; focus: systemPanel.shown
                Keys.onEscapePressed: systemPanel.closeRequested()
                Item {
                    id: panelHeader
                    // Its right edge is the rows' right edge, so the close glyph lines up with each dismiss glyph.
                    x: 23; y: 13; width: parent.width - 46; height: 44
                    Text {
                        anchors { left: parent.left; right: panelTools.left; rightMargin: 8 }
                        height: 44; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
                        text: systemPanel.title; textFormat: Text.PlainText; color: floeTheme.textPrimary
                        font.pixelSize: 18; font.weight: Font.DemiBold; font.letterSpacing: -0.4; font.family: floeTheme.interfaceFont
                        Accessible.role: Accessible.Heading; Accessible.name: text
                    }
                    Row { id: panelTools; anchors.right: panelClose.left; height: 44; spacing: 4 }
                    FlowButton {
                        id: panelClose
                        anchors.right: parent.right
                        width: 44; implicitHeight: 44; radius: 12; variant: "plain"; glyph: "close"; text: "×"
                        Accessible.name: "Close " + systemPanel.title.toLowerCase()
                        onClicked: systemPanel.closeRequested()
                    }
                }
                ScrollView {
                    // 13 px between the clip and the rows, so their tints and focus rings stay whole.
                    id: panelScroller
                    // A focus scope, so it holds active focus with the control inside and would be announced
                    // as an unnamed focused panel. Its controls are exposed without it.
                    Accessible.ignored: true
                    anchors { fill: parent; topMargin: 63; leftMargin: 10; rightMargin: 10; bottomMargin: 19 }
                    clip: true
                    contentWidth: availableWidth; contentHeight: panelBody.implicitHeight
                    Column { id: panelBody; x: 13; width: parent.width - 26; topPadding: 4; bottomPadding: 4 }
                }
            }
        }
    }
    // The volume, as a slider: drag or tap the track, or use the arrow keys (5% a step).
    component VolumeSlider: Slider {
        id: volumeSlider
        from: 0; to: 1; stepSize: 0.05
        enabled: root.audioAvailable
        implicitHeight: 44
        focusPolicy: Qt.StrongFocus
        opacity: enabled ? 1 : 0.45
        Accessible.name: "Volume"
        onMoved: root.setVolume(value)
        // The volume follows changes made elsewhere, except while the slider is pressed, so a drag never
        // jumps back to a PipeWire update that is a step behind. RestoreNone: Qt 6's default would put back
        // the value from when the Binding last took over, and a key press counts as a press.
        Binding on value { value: root.audioAvailable ? root.audioSink.audio.volume : 0; when: !volumeSlider.pressed; restoreMode: Binding.RestoreNone }
        background: Rectangle {
            x: volumeSlider.leftPadding; y: volumeSlider.topPadding + volumeSlider.availableHeight / 2 - height / 2
            width: volumeSlider.availableWidth; height: 6; radius: 3
            color: Qt.alpha(floeTheme.border, 0.45)
            Rectangle { width: volumeSlider.visualPosition * parent.width; height: parent.height; radius: 3; color: floeTheme.accent }
        }
        handle: Rectangle {
            x: volumeSlider.leftPadding + volumeSlider.visualPosition * (volumeSlider.availableWidth - width)
            y: volumeSlider.topPadding + volumeSlider.availableHeight / 2 - height / 2
            width: 22; height: 22; radius: 11
            color: floeTheme.accentStrong; border.width: 2; border.color: floeTheme.accent
            Rectangle {
                visible: volumeSlider.activeFocus
                anchors { fill: parent; margins: -4 }
                radius: 15; color: "transparent"; border.width: 2; border.color: floeTheme.focus
            }
        }
    }
    // A capsule bar for position controls: the work indicator.
    component CapsuleBar: Rectangle {
        radius: height / 2; color: floeTheme.canvas
        border.width: 1; border.color: Qt.alpha(floeTheme.border, 0.55)
    }
    SystemClock { id: clock; precision: SystemClock.Minutes }
    // The Island, as in the prototype on a desktop: a tab hanging from the screen's top edge, square at
    // the top and rounded below, the way a camera notch would be. System controls only.
    component SystemIsland: Rectangle {
        // The width of the screen the island is on; an activity chip only takes what is left.
        property real room: 1280
        // Only its controls, drawn on a card of the surface's own (the opened Island on a plain screen).
        property bool flat: false
        // Its chip shows a breathing dot (the shared busy clock moves it), with motion on.
        readonly property bool breathing: activityChip.visible && !activityChip.determinate && !activityChip.compact
            && root.motionQuick > 0
        implicitWidth: islandControls.implicitWidth + 12; implicitHeight: 44
        color: flat ? "transparent" : floeTheme.canvas
        topLeftRadius: 0; topRightRadius: 0; bottomLeftRadius: 14; bottomRightRadius: 14
        border.width: flat ? 0 : 1; border.color: Qt.alpha(floeTheme.border, 0.55)
        Rectangle {
            // The tab is joined to the edge: no line along the top.
            visible: !parent.flat
            anchors { left: parent.left; right: parent.right; top: parent.top; leftMargin: 1; rightMargin: 1 }
            height: 1; color: parent.color
        }
        Row {
            id: islandControls
            anchors.centerIn: parent; spacing: 0
            IslandButton {
                id: wordmark
                implicitHeight: 44; text: "floe."
                Accessible.name: root.deskOpen ? "Floe: return to work" : "Open Floe Desk"
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
                text: root.searchOpen ? "Close" : "Apps"
                open: root.searchOpen
                Accessible.name: root.searchOpen ? "Close search and apps" : "Open apps"
                onPressed: closeOnRelease = root.searchOpen
                onClicked: { if (closeOnRelease) { root.closeLauncher(); root.closeSearch() } else root.openApps(); closeOnRelease = false }
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
                    // Inset like the buttons: radius 10.
                    id: chipBody
                    anchors.fill: parent; anchors.margins: 4
                    radius: 10; color: floeTheme.surface; clip: true
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
        // The focus ring surrounds the indicator, 1 px outside it.
        readonly property real focusRingInset: -1
        // Return and Enter flip it like Space and a click, as they activate every button; a held key once.
        function activate() { if (enabled) { toggle.toggle(); toggle.toggled() } }
        Keys.onReturnPressed: event => { if (!event.isAutoRepeat) activate() }
        Keys.onEnterPressed: event => { if (!event.isAutoRepeat) activate() }
        onActiveFocusChanged: if (activeFocus && toggle.focusReason !== Qt.MouseFocusReason) root.reveal(toggle)
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
                anchors.fill: parent; anchors.margins: toggle.focusRingInset; radius: 14
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
        // A new shell starts with no pictures: an earlier session's window ids must never show its screens.
        if (root.snapshotDir) Quickshell.execDetached(["rm", "-rf", "--", root.snapshotDir])
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
        // Automatic recovery as the shell holds it, for tests/niri_shell_restart_controller_smoke.py.
        function recovery_state(): string {
            return JSON.stringify({ setting: recoveryPreferences.sessionRecovery, paused: root.autoRecoveryBlocked,
                                    working: root.recoveryWorking, saving: root.autoSaveWanted,
                                    message: root.recoveryMessage, pause_reason: root.recoveryPauseReason,
                                    set_aside: root.recoverySetAside, connected: controller.connected })
        }
        // Settings' Reopen it, and Settings scrolled to it, for tests/niri_set_aside_reopen_smoke.py.
        function reopen_set_aside(): void { root.reopenSetAside() }
        function show_set_aside(): void {
            if (!root.deskOpen) root.toggleDesk()
            root.settingsOpen = true
            Qt.callLater(function() { root.reveal(reopenAside) })
        }
        // What the shell last read from flow-spaces: pins with their windows, spaces with their Group layout, Layers.
        function spaces_view(): string {
            return JSON.stringify({ pins: root.pins.map(p => ({ id: p.id, kind: p.kind, window_ids: p.window_ids || [] })),
                                    spaces: root.displaySpaces.map(s => ({ id: s.id, group_id: s.group_id || "", group_layout: s.group_layout || "", active: !!s.active })),
                                    layers: root.layers.map(l => l.id) })
        }
        // Each window of the workspace with the label the shell shows for it, for tests/niri_performance_smoke.py.
        // The shared busy clock, for tests/niri_performance_smoke.py.
        function busy_clock(): string {
            return JSON.stringify({ running: busyClock.running, interval: busyClock.interval, phase: root.busyPhase,
                                    breathing: root.busyBreathing, sweeping: root.busySweeping })
        }
        function open_took(name: string): string { return JSON.stringify(root.openTook[name] || { ms: -1, count: 0 }) }
        function window_labels(): string { return JSON.stringify(root.windows.map(w => [w.id, w.label])) }
        function desk_state(): string { return root.deskOpen ? "open" : "closed" }
        // Read-only, for tests/niri_desk_overview_smoke.py: whether Desk's content is still settling in, and where it is.
        function desk_motion(): string {
            return JSON.stringify({ entering: deskEnter.running, opacity: deskContent.opacity, shift: deskShift.y, shown: deskWindow.visible,
                                    toggled_mid_rise: root.deskTogglesMidRise, again_while_pending: root.overviewAgainWhilePending })
        }
        function overview_state(): string { return root.spacesOverviewOpen ? "open" : "closed" }
        // Back to work without choosing, as Escape does.
        function overview_close(): void { root.closeSpacesOverview() }
        function overview_selected(): string { return root.spacesOverviewOpen && root.overviewSpace ? String(root.overviewSpace.id) : "" }
        function overview_move(step: int): void { if (root.spacesOverviewOpen) root.moveOverview(step) }
        // Which shell surfaces are open, for checks that two never are at once.
        function surfaces(): string {
            return JSON.stringify({desk: root.deskOpen, overview: root.spacesOverviewOpen, pending: root.overviewPending,
                                   search: root.searchOpen, switcher: root.switcherOpen, quick: root.quickSettingsOpen,
                                   notifications: root.notificationsOpen, niri_overview: root.overviewOpen})
        }
        function search_state(): string { return root.searchOpen ? (root.searchAppsOnly ? "apps" : "open") : "closed" }
        // What the search panel shows, for scripted checks: its rows, or the message in their place.
        function search_view(): string {
            // armed: the row as drawn asks to be chosen again (a command that asks first).
            return JSON.stringify({ rows: root.searchMatches.map((r, i) => ({ kind: r.kind, title: r.title, icon: r.icon,
                                                                              armed: !!searchView.itemAtIndex(i)?.armed })),
                                    message: root.searchMatches.length ? "" : searchEmpty.text,
                                    // The message, as laid out, ends above the footer's rule.
                                    message_clear: searchEmpty.y + Math.max(searchEmpty.height, searchEmpty.contentHeight) <= searchFooter.y - 1 })
        }
        function activity_list(): string { return JSON.stringify(root.activities.map(a => root.activityText(a))) }
        // Read-only, for tests/niri_focus_walk_smoke.py: the visible buttons and switches of every shell surface
        // but the opt-in app menu, with the words each shows, its state, its place in its window and where its
        // focus ring is drawn, so a check over AT-SPI can hold each accessible name to its visible label.
        function controls(): string {
            const found = []
            // The scrolling views a control is in, outermost first, each as drawn in its window: the walk reads a
            // view's controls as one block in the view's place, so its expected order does not depend on the scroll.
            const viewsOf = item => {
                const views = []
                for (let p = item.parent; p; p = p.parent)
                    if (p.flickableDirection !== undefined && p.clip) {
                        const c = p.mapToItem(null, 0, 0)
                        views.unshift([c.x, c.y, p.width, p.height])
                    }
                return views
            }
            const visit = (item, surface, size, card) => {
                if (!item || !item.visible) return
                if (item.readingGroup === true) {
                    const corner = item.mapToItem(null, 0, 0)
                    card = [corner.x, corner.y, item.width, item.height]
                }
                const isSwitch = item.checkable === true && item.indicator !== undefined
                // A text field shows what was typed, not a label; its focus ring is its own border.
                if (item.placeholderText !== undefined && item.echoMode !== undefined) {
                    const corner = item.mapToItem(null, 0, 0)
                    found.push({ surface: surface, window: size, kind: "field", text: "", icon: false, words: [],
                                 enabled: item.enabled, tabbable: (item.focusPolicy & Qt.TabFocus) !== 0,
                                 selected: false, open: false, card: card || null,
                                 rect: [corner.x, corner.y, item.width, item.height], views: viewsOf(item),
                                 ring: [corner.x, corner.y, item.width, item.height, 0] })
                    return
                }
                // A system panel's row draws its ring 13 px past its sides and 1 px past its top and bottom; a
                // slider draws it 4 px around its handle. Both are reported as the ring's own rectangle.
                if (item.chevron !== undefined && item.value !== undefined) {
                    const words = []
                    for (const child of item.contentItem.children)
                        if (child.visible && typeof child.text === "string" && child.text !== "") words.push(child.text)
                    const corner = item.mapToItem(null, 0, 0)
                    found.push({ surface: surface, window: size, kind: "row", text: item.text, icon: false, words: words,
                                 enabled: item.enabled, tabbable: (item.focusPolicy & Qt.TabFocus) !== 0,
                                 selected: false, open: false, card: card || null,
                                 rect: [corner.x, corner.y, item.width, item.height], views: viewsOf(item),
                                 ring: [corner.x - 13, corner.y - 1, item.width + 26, item.height + 2, 0] })
                    return
                }
                if (item.stepSize !== undefined && item.handle !== undefined) {
                    const corner = item.mapToItem(null, 0, 0), knob = item.handle.mapToItem(null, 0, 0)
                    found.push({ surface: surface, window: size, kind: "slider", text: "", icon: true, words: [],
                                 enabled: item.enabled, tabbable: (item.focusPolicy & Qt.TabFocus) !== 0,
                                 selected: false, open: false, card: card || null,
                                 rect: [corner.x, corner.y, item.width, item.height], views: viewsOf(item),
                                 ring: [knob.x, knob.y, item.handle.width, item.handle.height, -4] })
                    return
                }
                if (item.glyph !== undefined || isSwitch) {
                    const words = []
                    const read = node => {
                        if (!node || !node.visible) return
                        if (typeof node.text === "string" && node.textFormat !== undefined && node.text !== "")
                            words.push(node.text.replace(/<[^>]*>/g, ""))
                        for (const child of node.children) read(child)
                    }
                    read(item.contentItem)
                    const at = item.mapToItem(null, 0, 0)
                    // The ring surrounds a switch's indicator and a button itself.
                    const host = isSwitch ? item.indicator : item
                    const ring = host.mapToItem(null, 0, 0)
                    found.push({ surface: surface, window: size, kind: isSwitch ? "switch" : item.capsule ? "capsule" : item.glyph ? "glyph" : "button",
                                 text: item.text, icon: !!item.glyph, words: words, enabled: item.enabled,
                                 tabbable: (item.focusPolicy & Qt.TabFocus) !== 0, selected: item.selected === true, open: item.open === true,
                                 rect: [at.x, at.y, item.width, item.height], card: card || null, views: viewsOf(item),
                                 ring: [ring.x, ring.y, host.width, host.height, item.focusRingInset] })
                    return
                }
                for (const child of item.children) visit(child, surface, size, card)
            }
            for (const [surface, window] of [["desk", deskWindow], ["switcher", switcherWindow],
                                             ["quick", quickSettingsWindow], ["notifications", notificationPanel],
                                             ["overview", spacesOverview], ["search", searchPanel],
                                             ["island", workIsland], ["indicator", workIndicatorWindow]])
                if (window.visible) {
                    const first = found.length
                    visit(window.contentItem, surface, [window.width, window.height], null)
                    // Where the layer sits on its screen follows from its anchors and margins.
                    const place = { anchors: [window.anchors.top, window.anchors.bottom, window.anchors.left, window.anchors.right],
                                    margins: [window.margins.top, window.margins.bottom, window.margins.left, window.margins.right] }
                    const screen = window.screen ? [window.screen.x, window.screen.y, window.screen.width, window.screen.height] : null
                    for (let i = first; i < found.length; i++) { found[i].place = place; found[i].screen = screen }
                }
            return JSON.stringify(found)
        }
        // Read-only, for tests that point at the shell with tests/pointer.py: where a named item is (its window's
        // size, place and screen, and its rectangle in that window), or "" while it is hidden. "hovered" is what
        // the item shows of the pointer: a home indicator lit, a search row tinted, a banner held (its time
        // stopped), the banner's × in its hover state, a panel row its hover tint. Names: home, overview_home
        // and desk_home (the home indicators), overview_row, search_shade, search_row:N (only while the row is
        // wholly in view), banner, banner_dismiss, quick_network (Quick settings' Network row), app_button (the App
        // button) and app_item:N (the open app menu's row N, only while wholly in view; "hovered": its hover state).
        function pointer_target(name: string): string {
            let row = name.startsWith("search_row:") ? searchView.itemAtIndex(Number(name.slice(11))) : null
            if (row) {
                const top = row.mapToItem(searchView, 0, 0).y
                if (top < 0 || top + row.height > searchView.height) row = null
            }
            let entry = name.startsWith("app_item:") ? appMenuItems.itemAt(Number(name.slice(9))) : null
            if (entry) {
                const top = entry.mapToItem(appMenuFlick, 0, 0).y
                if (top < 0 || top + entry.height > appMenuFlick.height) entry = null
            }
            if (entry) row = null
            const found = ({
                home: () => [homeIndicator, workHome, workHome.lit],
                island: () => [workIsland, islandCard, islandHover.hovered],
                island_overview: () => [workIsland, islandOverview, islandOverview.hovered],
                island_desk: () => [workIsland, islandDesk, islandDesk.hovered],
                pill: () => [(clockPills.instances.find(w => w.visible) || null), (clockPills.instances.find(w => w.visible) || {}).contentItem, false],
                overview_home: () => [spacesOverview, overviewHome, overviewHome.lit],
                desk_home: () => [deskWindow, deskHome, deskHome.lit],
                overview_row: () => [spacesOverview, overviewRow, false],
                search_shade: () => [searchPanel, searchShade, false],
                banner: () => [notificationBanner, bannerCard, false],
                quick_network: () => [quickSettingsWindow, networkRow, networkRow.hovered],
                app_button: () => [appMenuTrigger, appMenuButton, appMenuButton.hovered]
            })[name]
            const [window, item, hovered] = found ? found() : row ? [searchPanel, row, row.tinted]
                : entry ? [appMenuSurface, entry, entry.hovered] : [null, null, false]
            if (!window || !window.visible || !item.visible || !window.screen) return ""
            const corner = item.mapToItem(null, 0, 0)
            return JSON.stringify({ window: [window.width, window.height],
                                    screen: [window.screen.x, window.screen.y, window.screen.width, window.screen.height],
                                    place: { anchors: [window.anchors.top, window.anchors.bottom, window.anchors.left, window.anchors.right],
                                             margins: [window.margins.top, window.margins.bottom, window.margins.left, window.margins.right] },
                                    rect: [corner.x, corner.y, item.width, item.height], hovered: hovered === true })
        }
        function launcher_state(): string { return root.launcherBusy() ? "open" : "closed" }
        // The app menu as the shell holds it, for scripted checks: the focused window, its probe, whether the
        // App button shows, the open menu's labels at its current level, and its error.
        function app_menu(): string {
            return JSON.stringify({ enabled: menuPreferences.appMenuEnabled, reader: appMenuReader.running,
                                    window: root.appMenuWindow, probe: root.appMenuProbe, button: appMenuTrigger.visible,
                                    available: root.appMenuAvailable,
                                    open: root.appMenuOpen, menu_window: root.appMenu ? root.appMenu.id : null,
                                    labels: root.appMenuOpen ? root.currentAppItems().map(i => i.label) : [],
                                    error: root.appMenuError, focus_changed: root.appMenuFocusChanged,
                                    waiting: root.appMenuActivateAsked !== 0, probe_ms: root.appMenuProbeTimes,
                                    probes: root.appMenuProbesAnswered })
        }
        function app_menu_enabled(on: bool): void { menuPreferences.appMenuEnabled = on; menuPreferences.sync() }
        // Super+M: the focused window's menu, or a toast when it has none.
        function menu(): void {
            if (menuPreferences.appMenuEnabled && !root.deskOpen && !root.overviewOpen && !root.spacesOverviewOpen && !root.searchOpen) {
                if (root.appMenuOpen) root.appMenuOpen = false
                else root.requestAppMenu()
            }
        }
        function quick(): void { root.toggleQuickSettings() }
        function notifications(): void { root.toggleNotifications() }
        // The summary (or app name) the banner is set to show, or "" when it is hidden. This is the shell's
        // state; whether the compositor draws it on top is checked by screenshots.
        function banner(): string {
            return notificationBanner.visible ? root.bannerNotification.summary || root.bannerNotification.appName : ""
        }
        function quick_open(): void { if (!root.quickSettingsOpen) root.toggleQuickSettings() }
        function settings(): void {
            if (!root.deskOpen) root.toggleDesk()
            root.settingsOpen = true
            Qt.callLater(function() { root.reveal(menuSwitch) })
        }
        function apps(): void { root.openApps() }
        function search(): void { root.openSearch() }
        function pin(): void { root.spaceAction("pin") }
        function focus_pin(id: string): void { root.spaceAction("focus", id) }
        function focus_window(id: string): void { root.spaceAction("focus-window", id) }
        // Open an installed app (desktop ID) as a Layer over the current space.
        function open_layer(id: string): void { root.spaceAction("open-layer", id) }
        // Stack ↔ side by side, as the Group's layout button does.
        function group_layout(id: string): void { root.spaceAction("group-layout", id) }
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
            if (root.overviewOpen || root.spacesOverviewOpen || root.overviewPending || root.launcherBusy() || root.otherSurfaceOpen()) return "busy"
            if (root.deskOpen) return "open"
            root.toggleDesk(); return "opened"
        }
        function overview_open(): string {
            if (root.deskOpen || root.overviewOpen || root.launcherBusy() || root.otherSurfaceOpen()) return "busy"
            if (root.spacesOverviewOpen || root.overviewPending) return "open"
            root.openSpacesOverview(); return "opened"
        }
        function apps_open(): string {
            if (root.deskOpen || root.overviewOpen || root.spacesOverviewOpen || root.overviewPending || root.otherSurfaceOpen()) return "busy"
            if (root.launcherBusy()) return "open"
            root.openApps(); return "opened"
        }
        function leave(): void { root.deskOpen = false }
        function switcher(): void { root.toggleSwitcher() }
        // Super+O and the home indicator: Overview, then again for Desk, then back to work.
        function overview(): void { root.toggleSpacesOverview() }
        // The Island (ADR-0014): Super+B opens it on a plain screen, or closes it.
        function island(): void { root.toggleIsland() }
        // Read-only, for tests: the resting chrome on each screen, and the opened Island. asked_at and shown_at are
        // Date.now() when it was asked to open and when its first frame was drawn.
        function island_state(): string {
            const on = variants => variants.instances.filter(w => w.visible).map(w => w.screen ? w.screen.name : "")
            return JSON.stringify({ open: workIsland.visible, asked_open: root.islandOpen, screen: root.islandScreenName, by: root.islandOpenedBy,
                                    asked_at: root.islandAskedAt, shown_at: root.islandShownAt, dwell_ms: root.islandDwell,
                                    edges: on(edgeStrips), pills: on(clockPills), clock_pill: pillPreferences.clockPill,
                                    main: root.mainDisplayName,
                                    bands: bands.instances.filter(w => w.visible).map(w => ({ screen: w.screen ? w.screen.name : "",
                                        height: w.geometry.height, cutout: w.geometry.cutout, panel: w.geometry.panel, simulated: w.geometry.simulated })),
                                    indicator: workIndicatorWindow.visible, moving: root.spacesMoving,
                                    home: homeIndicator.visible, touch: root.touchPresent,
                                    banner: notificationBanner.visible,
                                    panel_screen: root.panelScreen ? root.panelScreen.name : "" })
        }
        // For tests: the clock pill's setting ("off", "main" or "all"), the notch override ("" to match, "none", or a
        // panel's id), and a fresh look for a touch device.
        function clock_pill(mode: string): void {
            if (!["off", "main", "all"].includes(mode)) return
            pillPreferences.clockPill = mode; pillPreferences.sync()
        }
        function notch_panel(id: string): void {
            if (id !== "" && id !== "none" && !root.notchPanels.some(p => p.id === id)) return
            devicePreferences.notchPanel = id; devicePreferences.sync()
        }
        function touch_probe(): void { if (!touchProbe.running) touchProbe.running = true }
    }

    // shortcut: Desk content settles in (220 ms fade and rise) but apps do not move away; add the spatial
    // transition when compositor integration supports it.
    PanelWindow {
        visible: root.deskOpen || root.deskHeld
        id: deskWindow
        anchors { top: true; bottom: true; left: true; right: true }
        color: floeTheme.canvas
        exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: root.notificationsOpen || root.quickSettingsOpen || root.searchOpen ? WlrKeyboardFocus.None : WlrKeyboardFocus.Exclusive
        onVisibleChanged: { if (!visible) root.deskDrawn = false; else if (root.motionQuick) deskEnter.restart() }
        Connections {
            target: deskContent.Window.window
            function onFrameSwapped() { if (deskWindow.visible && !root.deskDrawn) root.deskDrawn = true }
        }
        ParallelAnimation {
            id: deskEnter
            NumberAnimation { target: deskContent; property: "opacity"; from: 0; to: 1; duration: 220; easing.type: Easing.BezierSpline; easing.bezierCurve: root.standardCurve }
            NumberAnimation { target: deskShift; property: "y"; from: 12; to: 0; duration: 220; easing.type: Easing.BezierSpline; easing.bezierCurve: root.standardCurve }
        }

        Item {
            // The prototype's ambient light behind Desk: a soft glow above the headline, a fainter one low
            // on the right, and a faint 46 px grid. Decoration only; it takes no input.
            id: deskAmbient
            anchors.fill: parent
            Shape {
                anchors.fill: parent
                ShapePath {
                    strokeColor: "transparent"
                    fillGradient: RadialGradient {
                        centerX: deskAmbient.width * 0.54; centerY: deskAmbient.height * 0.12
                        focalX: centerX; focalY: centerY; centerRadius: Math.max(deskAmbient.width, deskAmbient.height) * 0.5
                        GradientStop { position: 0; color: Qt.alpha(floeTheme.accent, 0.08) }
                        GradientStop { position: 1; color: "transparent" }
                    }
                    PathSvg { path: "M0 0H" + deskAmbient.width + "V" + deskAmbient.height + "H0Z" }
                }
                ShapePath {
                    strokeColor: "transparent"
                    fillGradient: RadialGradient {
                        centerX: deskAmbient.width * 0.85; centerY: deskAmbient.height * 0.95
                        focalX: centerX; focalY: centerY; centerRadius: Math.max(deskAmbient.width, deskAmbient.height) * 0.4
                        GradientStop { position: 0; color: Qt.alpha(floeTheme.surface, 0.7) }
                        GradientStop { position: 1; color: "transparent" }
                    }
                    PathSvg { path: "M0 0H" + deskAmbient.width + "V" + deskAmbient.height + "H0Z" }
                }
            }
            Repeater {
                model: Math.ceil(deskAmbient.width / 46)
                Rectangle { required property int index; x: index * 46; width: 1; height: deskAmbient.height; color: Qt.alpha(floeTheme.textPrimary, 0.022) }
            }
            Repeater {
                model: Math.ceil(deskAmbient.height / 46)
                Rectangle { required property int index; y: index * 46; height: 1; width: deskAmbient.width; color: Qt.alpha(floeTheme.textPrimary, 0.022) }
            }
        }
        FocusScope {
            anchors.fill: parent
            focus: true
            Keys.onEscapePressed: root.deskOpen = false
            SystemIsland {
                id: deskIsland
                room: parent.width
                anchors.top: parent.top; anchors.horizontalCenter: parent.horizontalCenter
            }
            HomeIndicator {
                id: deskHome
                visible: root.touchPresent
                z: 2; width: 160; height: 44
                anchors { bottom: parent.bottom; horizontalCenter: parent.horizontalCenter }
            }
            Flickable {
                id: deskScroller
                // Below the Island's 44 px tab, and above the home indicator's 44 px band: content never
                // scrolls under either, whose input would take its clicks.
                anchors.fill: parent; anchors.topMargin: 44 + 12; anchors.bottomMargin: 44
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
                    Row {
                        // The prototype's eyebrow: a small accent light before the section name.
                        spacing: 11
                        Item {
                            width: 8; height: 8; anchors.verticalCenter: parent.verticalCenter
                            Rectangle { anchors.centerIn: parent; width: 18; height: 18; radius: 9; color: Qt.alpha(floeTheme.accent, 0.07) }
                            Rectangle { anchors.centerIn: parent; width: 13; height: 13; radius: 6.5; color: Qt.alpha(floeTheme.accent, 0.14) }
                            Rectangle { anchors.fill: parent; radius: 4; color: floeTheme.accent }
                        }
                        SectionLabel { text: "FLOE  /  YOUR DESK"; color: floeTheme.textSecondary; font.letterSpacing: 2 }
                    }
                    Text {
                        // Display type in the brand face, sized to the screen as in the prototype (7.9% of
                        // its width, at most 116 px, and at most 11% of its height), set tight: −5% of its
                        // size, lines at 0.97. Styled text, not rich text: rich text ignores the line height.
                        readonly property real size: deskScroller.width < 600 ? 44
                            : Math.round(Math.max(40, Math.min(116, deskScroller.width * 0.079, deskScroller.height * 0.11)))
                        topPadding: 10; bottomPadding: 6
                        text: "Everything<br>in its <font color='" + floeTheme.accent + "'>place.</font>"
                        textFormat: Text.StyledText
                        color: floeTheme.textPrimary; font.weight: Font.DemiBold; font.family: floeTheme.brandFont
                        font.pixelSize: size; font.letterSpacing: -size * 0.05
                        // As CSS's line-height: a fixed 0.97 of the size. A proportional one multiplies the
                        // face's own line gap (Noto Sans: about 1.36), which leaves the lines far apart.
                        lineHeightMode: Text.FixedHeight; lineHeight: Math.round(size * 0.97)
                    }
                    Text {
                        text: "Your tools are here. Your work is right where you left it."
                        color: floeTheme.textMuted; font.pixelSize: deskScroller.width < 600 ? 15 : 18; font.family: floeTheme.interfaceFont
                        wrapMode: Text.Wrap; width: Math.min(parent.width, 560); lineHeight: 1.3
                        bottomPadding: deskScroller.height < 600 ? 20 : 34
                    }
                    FlowButton {
                        id: searchButton
                        width: Math.min(parent.width, 590); implicitHeight: 59; radius: 15; variant: "glass"
                        leftPadding: 19; rightPadding: 12
                        text: width < 380 ? "Search" : "Search anything, start anywhere…"
                        Accessible.name: "Search anything, start anywhere: apps, spaces, windows and files"
                        contentItem: Item {
                            FlowIcon {
                                id: searchGlyph
                                anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                                name: "search"; size: 20; color: floeTheme.textSecondary
                            }
                            Text {
                                anchors.left: searchGlyph.right; anchors.leftMargin: 14
                                anchors.right: searchShortcut.left; anchors.rightMargin: 8
                                anchors.verticalCenter: parent.verticalCenter
                                text: searchButton.text; textFormat: Text.PlainText; elide: Text.ElideRight
                                color: floeTheme.textMuted; font.pixelSize: 15; font.family: floeTheme.interfaceFont
                            }
                            Rectangle {
                                id: searchShortcut
                                anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                width: shortcutLabel.implicitWidth + 14; height: 26; radius: 6
                                color: "transparent"; border.color: Qt.alpha(floeTheme.border, 0.6)
                                Text {
                                    id: shortcutLabel
                                    anchors.centerIn: parent; text: "Super K"; textFormat: Text.PlainText
                                    color: floeTheme.textSecondary; font.pixelSize: 11; font.family: floeTheme.technicalFont
                                }
                            }
                        }
                        onClicked: root.openSearch()
                    }
                    Item { width: 1; height: deskScroller.height < 600 ? 20 : 36 }
                    Item {
                        // Pinned spaces and tools side by side, as in the prototype (1 : 0.72, 37 px apart);
                        // one above the other on a narrow screen.
                        id: deskLower
                        readonly property bool wide: width >= 700
                        width: parent.width
                        height: wide ? Math.max(deskPinsColumn.height, deskToolsColumn.height) : deskPinsColumn.height + 32 + deskToolsColumn.height
                        Column {
                            id: deskPinsColumn
                            // Two cards to a row, as the prototype's pins share it; one on a narrow screen.
                            readonly property real pinCardWidth: width >= 310 ? Math.floor((width - 8) / 2) : width
                            width: deskLower.wide ? Math.floor((parent.width - 37) / 1.72) : parent.width
                            spacing: 12
                            SectionLabel {
                                                        text: "PINNED SPACES" + (root.displayCount > 1 && root.displayName ? "  ·  " + root.displayName.toUpperCase() + (root.displayLabel ? "  ·  " + root.displayLabel.toUpperCase() : "") : "")
                                                        width: parent.width; elide: Text.ElideRight
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
                                    model: deskWindow.visible ? root.pins : []  // hidden, it rebuilds nothing
                                    Flow {
                                        required property var modelData
                                        required property int index
                                        width: root.editPins ? parent.width : deskPinsColumn.pinCardWidth
                                        spacing: 3
                                        FlowButton {
                                            id: pinnedSpaceButton
                                            width: root.editPins ? Math.max(120, parent.width - 3 * 47) : deskPinsColumn.pinCardWidth
                                            implicitHeight: 64; radius: 13; leftPadding: 14; variant: "glass"
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
                                                    anchors.verticalCenter: parent.verticalCenter; width: pinnedSpaceButton.width - 62
                                                    Text { width: parent.width; text: pinnedSpaceButton.text; textFormat: Text.PlainText; elide: Text.ElideRight; color: floeTheme.textPrimary; font.weight: Font.DemiBold; font.pixelSize: 14; font.family: floeTheme.interfaceFont }
                                                    Text { width: parent.width; text: pinnedSpaceButton.detail; textFormat: Text.PlainText; elide: Text.ElideRight; color: floeTheme.textMuted; font.pixelSize: 12; font.family: floeTheme.interfaceFont }
                                                }
                                            }
                                            enabled: !root.spacesWorking
                                            Accessible.name: (modelData.available ? "Focus " : modelData.kind === "group" ? "Unavailable Group " : "Open ") + modelData.label + ", pinned space " + (index + 1)
                                            onClicked: root.spaceAction("focus", modelData.id)
                                        }
                                        FlowButton { id: pinLeftButton; visible: root.editPins; width: 44; implicitHeight: 64; variant: "quiet"; glyph: "left"; text: "‹"; Accessible.name: "Move " + modelData.label + " left"; enabled: !root.spacesWorking; onClicked: root.spaceAction("left", modelData.id) }
                                        FlowButton { id: pinRightButton; visible: root.editPins; width: 44; implicitHeight: 64; variant: "quiet"; glyph: "right"; text: "›"; Accessible.name: "Move " + modelData.label + " right"; enabled: !root.spacesWorking; onClicked: root.spaceAction("right", modelData.id) }
                                        FlowButton { id: unpinButton; visible: root.editPins; width: 44; implicitHeight: 64; variant: "danger"; glyph: "close"; text: "×"; enabled: !root.spacesWorking; Accessible.name: "Unpin " + modelData.label; onClicked: root.spaceAction("unpin", modelData.id) }
                                    }
                                }
                            }
                            SectionLabel {
                                visible: root.otherPins.length > 0;                         text: root.displayCount > 1 ? "OTHER DISPLAYS" : "PINNED ON OTHER DISPLAYS"
                            }
                            Flow {
                                visible: root.otherPins.length > 0
                                width: parent.width
                                spacing: 8
                                Repeater {
                                    model: deskWindow.visible ? root.otherPins : []  // hidden, it rebuilds nothing
                                    Flow {
                                        required property var modelData
                                        width: parent.width; spacing: 3
                                        FlowButton {
                                            id: otherPinButton
                                            width: Math.max(120, parent.width - 135); implicitHeight: 64; radius: 13; leftPadding: 14; variant: "glass"; text: modelData.label
                                            property string detail: (modelData.display_name ? modelData.display_name + " · " : "") + modelData.display_label + (modelData.display_state === "connected" ? ""
                                                : modelData.display_state === "unassigned" ? " · not assigned"
                                                : modelData.display_state === "ambiguous" ? " · cannot be told apart" : " · disconnected")
                                            enabled: (modelData.display_state === "connected" || modelData.available) && !root.spacesWorking
                                            contentItem: Column {
                                                topPadding: 12
                                                Text { width: otherPinButton.width - 28; text: otherPinButton.text; textFormat: Text.PlainText; elide: Text.ElideRight; color: otherPinButton.enabled ? floeTheme.textPrimary : floeTheme.textMuted; font.weight: Font.DemiBold; font.pixelSize: 14; font.family: floeTheme.interfaceFont }
                                                Text { width: otherPinButton.width - 28; text: otherPinButton.detail; textFormat: Text.PlainText; elide: Text.ElideRight; color: floeTheme.textMuted; font.pixelSize: 12; font.family: floeTheme.interfaceFont }
                                            }
                                            Accessible.name: (modelData.display_state === "connected" ? "Go to " : "Focus ") + modelData.label + " on " + otherPinButton.detail
                                            onClicked: root.spaceAction("focus", modelData.id)
                                        }
                                        FlowButton {
                                            id: movePinHereButton
                                            width: 132; implicitHeight: 64; variant: "quiet"; text: "Pin here"
                                            Accessible.name: "Pin here: " + modelData.label + ", on this display instead"
                                            enabled: !root.spacesWorking
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
                                    width: Math.min(parent.width, implicitWidth); implicitHeight: 44; variant: "quiet"; text: "Pin current space · Super P"
                                    contentItem: Text {
                                        text: "Pin current space · <span style=\"font-family:'" + floeTheme.technicalFont + "'\">Super P</span>"
                                        textFormat: Text.RichText; color: floeTheme.textPrimary
                                        font.pixelSize: 14; font.weight: Font.Medium; font.family: floeTheme.interfaceFont
                                        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
                                    }
                                    enabled: !root.spacesWorking
                                    onClicked: root.spaceAction("pin")
                                }
                                FlowButton { id: editPinsButton; visible: root.pins.length > 0; implicitHeight: 44; variant: root.editPins ? "primary" : "quiet"; text: root.editPins ? "Done" : "Edit pins"; onClicked: root.editPins = !root.editPins }
                                FlowButton {
                                    id: makeMainButton
                                    visible: root.displayCount > 1 && root.displayName !== "" && root.displayName !== "Main"
                                    implicitHeight: 44; variant: "quiet"; text: "Make this the main display"
                                    Accessible.name: "Make this the main display: " + root.displayLabel + "; Super and a number will open its pinned spaces"
                                    enabled: !root.spacesWorking
                                    onClicked: root.spaceAction("make-main", (root.displays.find(d => d.current) || {}).key)
                                }
                            }
                            Row {
                                visible: root.spacesError !== ""; width: parent.width; spacing: 8
                                Text { text: root.spacesError; textFormat: Text.PlainText; color: floeTheme.danger; wrapMode: Text.Wrap; width: parent.width - dismissSpacesError.width - 8; height: Math.max(44, implicitHeight); verticalAlignment: Text.AlignVCenter; font.pixelSize: 14; font.family: floeTheme.interfaceFont }
                                FlowButton { id: dismissSpacesError; width: 96; implicitHeight: 44; text: "Dismiss"; variant: "quiet"; Accessible.name: "Dismiss spaces error"; onClicked: root.spacesError = "" }
                            }
                        }
                        Column {
                            id: deskToolsColumn
                            x: deskLower.wide ? deskPinsColumn.width + 37 : 0
                            y: deskLower.wide ? 0 : deskPinsColumn.height + 32
                            width: parent.width - x
                            spacing: 12
                            SectionLabel { text: "TOOLS" }
                            Grid {
                                id: toolGrid
                                columns: 4; spacing: 9
                                readonly property real tile: Math.floor((parent.width - 3 * spacing) / 4)
                                Repeater {
                                    model: [{ label: "Search", icon: "search" }, { label: "Apps", icon: "grid" },
                                            { label: "Files", icon: "folder" }, { label: "Settings", icon: "sliders" }]
                                    FlowButton {
                                        id: toolButton
                                        required property var modelData
                                        width: toolGrid.tile; implicitHeight: 80; radius: 13; variant: "glass"
                                        leftPadding: 4; rightPadding: 4
                                        open: modelData.label === "Settings" && root.settingsOpen
                                        text: modelData.label
                                        Accessible.name: modelData.label
                                        contentItem: Item {
                                            Column {
                                                anchors.centerIn: parent; width: parent.width; spacing: 10
                                                FlowIcon { anchors.horizontalCenter: parent.horizontalCenter; name: toolButton.modelData.icon; size: 21; color: floeTheme.textPrimary }
                                                Text {
                                                    width: parent.width; horizontalAlignment: Text.AlignHCenter; elide: Text.ElideRight
                                                    text: toolButton.text; textFormat: Text.PlainText
                                                    color: floeTheme.textSecondary; font.pixelSize: 12; font.family: floeTheme.interfaceFont
                                                }
                                            }
                                        }
                                        onClicked: {
                                            const tool = modelData.label
                                            if (tool === "Settings") {
                                                root.settingsOpen = !root.settingsOpen
                                                if (root.settingsOpen) Qt.callLater(function() { root.reveal(menuSwitch) })
                                            } else if (tool === "Search") root.openSearch()
                                            else if (tool === "Apps") root.openApps()
                                            else { root.deskOpen = false; root.launchApp(["nautilus"]) }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    SectionLabel { visible: root.activities.length > 0; topPadding: 28; bottomPadding: 12; text: "ACTIVITIES" }
                    Flow {
                        visible: root.activities.length > 0
                        width: parent.width; spacing: 8
                        Repeater {
                            model: deskWindow.visible ? root.activities : []  // hidden, it rebuilds nothing
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
                    Column {
                        visible: root.settingsOpen; width: parent.width; spacing: 12
                        SectionLabel { text: "SETTINGS"; topPadding: 12 }
                        FlowSwitch {
                            id: appearanceSwitch
                            // A fixed label: the switch shows the state, so the words never contradict the accessible name.
                            text: "Arctic Daylight · Light"
                            checked: appearancePreferences.lightMode
                            Accessible.name: "Use Arctic Daylight light mode"
                            onToggled: { appearancePreferences.lightMode = checked; appearancePreferences.sync(); root.syncCompositorAppearance() }
                        }
                        FlowSwitch {
                            id: reduceMotionSwitch
                            text: "Reduce motion"
                            checked: motionPreferences.reduceMotion
                            Accessible.name: "Reduce motion: buttons and progress change instantly"
                            onToggled: { motionPreferences.reduceMotion = checked; motionPreferences.sync(); root.syncCompositorAppearance() }
                        }
                        // The clock pill (ADR-0014): plain screens show nothing at rest unless it is on.
                        Text {
                            text: "Clock pill"; color: floeTheme.textPrimary; font.pixelSize: 16; font.weight: Font.Medium
                            font.family: floeTheme.interfaceFont; topPadding: 4
                        }
                        Row {
                            id: clockPillChoice
                            spacing: 6
                            Repeater {
                                model: [{ value: "off", label: "Off" }, { value: "main", label: "Main screen" }, { value: "all", label: "All screens" }]
                                FlowButton {
                                    required property var modelData
                                    implicitHeight: 44; variant: "quiet"
                                    text: modelData.label
                                    // The current one: its dot, border and "Current", as other choices among siblings.
                                    selected: pillPreferences.clockPill === modelData.value
                                    Accessible.name: "Clock pill: " + modelData.label
                                    onClicked: { pillPreferences.clockPill = modelData.value; pillPreferences.sync() }
                                }
                            }
                        }
                        Text {
                            text: "A small clock at the top of the screen. A click on it opens the Island, as pushing the pointer against the top edge or Super+B does. On a MacBook with a notch the clock is in the band beside the camera, so the pill is not shown there."
                            color: floeTheme.textMuted; width: parent.width; wrapMode: Text.Wrap; font.pixelSize: 14; font.family: floeTheme.interfaceFont
                        }
                        FlowSwitch {
                            id: menuSwitch
                            text: "Experimental application menu"
                            checked: menuPreferences.appMenuEnabled
                            Accessible.name: text
                            Accessible.description: menuSwitchNote.text
                            onToggled: { menuPreferences.appMenuEnabled = checked; menuPreferences.sync(); root.appMenuOpen = false }
                        }
                        // What turning it on changes (coverage report, findings 6 and 9): at-spi keeps the switch
                        // for the user, Qt 5 reads it only at start, and apps of a session started before at-spi2-core
                        // was installed have accessibility switched off in their environment.
                        Text {
                            id: menuSwitchNote
                            text: "Shows an App button when the focused app has a menu Flow can read, such as Kate, Mousepad or LibreOffice. Turning it on switches accessibility on for your user, and it stays on in later sessions. Qt 5 apps that are already running need a restart, and apps started in a session from before accessibility support was installed need a new login. Turning it off switches accessibility off again, if Flow switched it on."
                            color: floeTheme.textMuted; width: parent.width; wrapMode: Text.Wrap; font.pixelSize: 14; font.family: floeTheme.interfaceFont
                        }
                        SectionLabel { text: "SESSION RECOVERY"; topPadding: 12 }
                        FlowSwitch {
                            id: autoRecoverySwitch
                            text: "Automatically save and restore session"
                            checked: recoveryPreferences.sessionRecovery
                            enabled: !root.recoveryWorking && !root.spacesWorking
                            Accessible.name: text
                            onToggled: {
                                // Enabled through automatic recovery so focus stays, but a change waits for it:
                                // the run's result would otherwise undo the pause below.
                                if (recoveryProcess.running) {
                                    checked = Qt.binding(() => recoveryPreferences.sessionRecovery)
                                    root.recoveryMessage = "Wait for automatic recovery to finish"
                                    return
                                }
                                recoveryPreferences.sessionRecovery = checked; recoveryPreferences.sync()
                                root.autoRecoveryBlocked = true
                                root.recoveryPauseReason = ""  // a reason from before the switch no longer holds
                                if (checked) { recoveryProcess.command = ["flow-recovery", "auto-pause"]; recoveryProcess.running = true }
                            }
                        }
                        Text { text: "Save your documents, then save the current window list before restarting."; color: floeTheme.textMuted; width: parent.width; wrapMode: Text.Wrap; font.pixelSize: 14; font.family: floeTheme.interfaceFont }
                        Flow {
                            width: parent.width; spacing: 8
                            FlowButton { id: saveSession; implicitHeight: 44; text: "Save session"; enabled: !root.recoveryWorking && !root.spacesWorking; onClicked: root.startSaveSession(false) }
                            FlowButton { id: restoreSession; implicitHeight: 44; variant: "quiet"; text: "Restore session"; enabled: !root.recoveryWorking && !root.spacesWorking; onClicked: { if (recoveryProcess.running) { root.recoveryMessage = "Wait for automatic recovery to finish"; return } root.recoveryMessage = "Restoring applications…"; recoveryProcess.command = ["flow-recovery", "restore"]; recoveryProcess.running = true } }
                        }
                        Text { id: recoveryPause; visible: recoveryPreferences.sessionRecovery && root.autoRecoveryBlocked && root.recoveryPauseReason !== "" && root.recoveryPauseReason !== root.recoveryMessage; text: root.recoveryPauseReason; textFormat: Text.PlainText; color: floeTheme.accent; width: parent.width; wrapMode: Text.Wrap }
                        Text { id: recoveryAside; visible: root.recoverySetAside !== ""; text: root.recoverySetAsideLeft ? root.recoverySetAsideLeft : "Not reopened when the shell started: the saved session is kept as " + root.recoverySetAside + "."; textFormat: Text.PlainText; color: floeTheme.textMuted; width: parent.width; wrapMode: Text.Wrap; font.pixelSize: 14; font.family: floeTheme.interfaceFont }
                        FlowButton { id: reopenAside; visible: root.recoverySetAside !== "" && root.recoverySetAsideLeft === ""; implicitHeight: 44; variant: "quiet"; text: "Reopen it"; enabled: !root.recoveryWorking && !root.spacesWorking; onClicked: root.reopenSetAside() }
                        Text { id: recoveryStatus; visible: root.recoveryMessage !== ""; text: root.recoveryMessage; textFormat: Text.PlainText; color: floeTheme.accent; width: parent.width; wrapMode: Text.Wrap }
                    }
                    Item {
                        // The prototype's foot: what is on this display, and the ways on from here, with Niri's
                        // list of keys among them. On a narrow screen the summary takes a line of its own.
                        id: deskFoot
                        readonly property bool stacked: width < 560
                        width: parent.width; height: 32 + (stacked ? 24 : 0) + 44
                        Text {
                            anchors.left: parent.left
                            y: deskFoot.stacked ? 32 : deskFoot.height - 44
                            width: deskFoot.stacked ? parent.width : parent.width - deskWays.width - 12
                            height: deskFoot.stacked ? 24 : 44; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
                            textFormat: Text.PlainText; color: floeTheme.textMuted; font.pixelSize: 12; font.family: floeTheme.interfaceFont
                            font.features: { "tnum": 1 }
                            text: {
                                const spaces = root.displaySpaces.length, groups = root.displaySpaces.filter(space => !!space.group_id).length
                                return spaces + (spaces === 1 ? " space" : " spaces") + "  ·  " + root.pins.length + " pinned"
                                    + (groups ? "  ·  " + groups + (groups === 1 ? " Group" : " Groups") : "")
                            }
                        }
                        Row {
                            id: deskWays
                            x: deskFoot.stacked ? -8 : parent.width - width; anchors.bottom: parent.bottom
                            spacing: 2
                            Repeater {
                                model: ["Overview", "Spaces", "Shortcuts", "Return to work"]
                                FlowButton {
                                    id: deskWay
                                    required property string modelData
                                    variant: "plain"; implicitHeight: 44
                                    leftPadding: deskFoot.stacked ? 8 : 12; rightPadding: deskFoot.stacked ? 8 : 12
                                    text: modelData
                                    contentItem: Text {
                                        text: (deskWay.modelData === "Shortcuts" ? "?  " : "") + deskWay.text
                                            + (deskWay.modelData === "Return to work" && !deskFoot.stacked ? "  ↓" : ""); textFormat: Text.PlainText
                                        color: deskWay.hovered || deskWay.activeFocus ? floeTheme.textPrimary : floeTheme.textMuted
                                        font.pixelSize: 12; font.family: floeTheme.interfaceFont
                                        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                                    }
                                    Accessible.name: modelData === "Shortcuts" ? "Show keyboard shortcuts" : modelData
                                    onClicked: {
                                        if (modelData === "Overview") root.openSpacesOverview()
                                        else if (modelData === "Spaces") root.toggleSwitcher()
                                        else if (modelData === "Shortcuts") {
                                            root.deskOpen = false
                                            Quickshell.execDetached(["niri", "msg", "action", "show-hotkey-overlay"])
                                        } else root.deskOpen = false
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
        // The home indicator (the prototype's, after iOS): a short line. Tap it, or drag it up, for
        // Overview; again for Desk. Only the bottom 126 × 24 takes input, so it covers little of the app
        // beneath (Flow's columns reach the screen's edge).
        id: homeIndicator
        screen: root.workScreen
        // Desk and Overview carry their own (a surface opened later stacks above this one).
        // Only with a touch device (ADR-0014): without one, Super+O and the Island reach Overview.
        visible: root.touchPresent && !root.workCovered && !root.switcherOpen && !root.appMenuOpen && !root.searchOpen
        anchors.bottom: true
        implicitWidth: 126; implicitHeight: 24
        color: "transparent"; exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        HomeIndicator { id: workHome; anchors.fill: parent }
    }
    PanelWindow {
        // Work indicator: the space's place in this display's Flow (and a Group's focused app), for a moment while
        // moving between spaces (ADR-0014). It takes no clicks, so it shows no buttons: the opened Island has the
        // position and its arrows, a Group's apps and a Layer's actions.
        id: workIndicatorWindow
        screen: root.workScreen
        visible: !root.workCovered && !root.switcherOpen && !root.appMenuOpen && !root.searchOpen && !root.overviewOpen
            && !!root.activeSpace && !root.activeLayer && root.spacesMoving
        mask: Region {}
        anchors { bottom: true; right: true }
        margins { bottom: 8; right: 12 }
        implicitWidth: workIndicator.implicitWidth + 32; implicitHeight: 36
        color: "transparent"; exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
        CapsuleBar {
            anchors.fill: parent
            Row {
                id: workIndicator
                anchors.centerIn: parent; spacing: 10
                Text {
                    // A Group's focused app, on screens of 800 px and wider.
                    readonly property var member: root.activeSpace && root.activeSpace.group_id
                        ? root.activeSpace.members.find(m => m.active) || null : null
                    visible: !!member && !!workIndicatorWindow.screen && workIndicatorWindow.screen.width >= 800
                    anchors.verticalCenter: parent.verticalCenter
                    text: member ? (member.app_name || member.label).slice(0, 24) : ""
                    textFormat: Text.PlainText; color: floeTheme.textSecondary; font.pixelSize: 13; font.family: floeTheme.interfaceFont
                }
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: (root.activeSpaceIndex < 9 ? "0" : "") + (root.activeSpaceIndex + 1) + "  /  " + (root.displaySpaces.length < 10 ? "0" : "") + root.displaySpaces.length
                    textFormat: Text.PlainText; color: floeTheme.textPrimary; font.pixelSize: 13; font.letterSpacing: 0.5
                    font.family: floeTheme.interfaceFont; font.features: { "tnum": 1 }
                    Accessible.name: "Space " + (root.activeSpaceIndex + 1) + " of " + root.displaySpaces.length
                }
            }
        }
    }
    PanelWindow {
        // Flow's Overview: this display's spaces in one row, the selected one centred, each with its last
        // snapshot (openSpacesOverview). Left/Right or the wheel choose, Enter or a click returns to it,
        // Up or the home indicator goes on to Desk, Escape returns to work unchanged.
        id: spacesOverview
        visible: root.spacesOverviewOpen || root.overviewHeld
        anchors { top: true; bottom: true; left: true; right: true }
        color: floeTheme.canvas
        exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive
        readonly property real edge: Math.max(20, Math.min(52, width * 0.03))
        readonly property real cardWidth: Math.max(220, Math.min(480, width * 0.3))
        // A snapshot without the shell's bands: the Island's 44 px tab (and the app-menu trigger on its row)
        // above, and the home indicator below (44).
        readonly property real bandTop: 44
        readonly property real bandBottom: 44
        readonly property real cardHeight: cardWidth * Math.max(0.3, (height - bandTop - bandBottom) / Math.max(1, width))
        onVisibleChanged: if (visible) { overviewRow.positionViewAtIndex(root.overviewIndex, ListView.Center); overviewFocus.forceActiveFocus() } else root.overviewDrawn = false
        FocusScope {
            id: overviewFocus
            anchors.fill: parent; focus: true
            // For open_took: the first frame shown with every card, each card on screen with its snapshot.
            Connections {
                target: overviewFocus.Window.window
                function onFrameSwapped() {
                    if (spacesOverview.visible && !root.overviewDrawn) root.overviewDrawn = true
                    if (!root.openStarted.overview || !spacesOverview.visible || overviewRow.count !== root.displaySpaces.length) return
                    for (let i = 0; i < overviewRow.count; i++) {
                        const card = overviewRow.itemAtIndex(i)
                        if (card && !card.pictured) return
                    }
                    root.openEnds("overview")
                }
            }
            Keys.onEscapePressed: root.closeSpacesOverview()
            Keys.onLeftPressed: root.moveOverview(-1)
            Keys.onRightPressed: root.moveOverview(1)
            Keys.onUpPressed: root.revealDeskFromOverview()
            Keys.onReturnPressed: root.chooseOverviewSpace(root.overviewIndex)
            Keys.onEnterPressed: root.chooseOverviewSpace(root.overviewIndex)
            Keys.onPressed: event => {
                if (event.key === Qt.Key_Home) { root.selectOverview(0); event.accepted = true }
                else if (event.key === Qt.Key_End) { root.selectOverview(root.displaySpaces.length - 1); event.accepted = true }
            }
            SystemIsland {
                id: overviewIsland
                room: parent.width; z: 3
                anchors { top: parent.top; horizontalCenter: parent.horizontalCenter }
            }
            Column {
                x: spacesOverview.edge; y: 72; spacing: 12
                Row {
                    spacing: 11
                    Rectangle { anchors.verticalCenter: parent.verticalCenter; width: 8; height: 8; radius: 4; color: floeTheme.accent }
                    SectionLabel {
                        text: "YOUR WORK  /  " + (root.displayName ? root.displayName.toUpperCase() + " DISPLAY" : "THIS DISPLAY")
                        color: floeTheme.textSecondary; font.letterSpacing: 2
                    }
                }
                Text {
                    text: "Overview<font color='" + floeTheme.accent + "'>.</font>"
                    textFormat: Text.RichText; color: floeTheme.textPrimary
                    font.pixelSize: spacesOverview.width >= 720 ? 52 : 32; font.weight: Font.DemiBold
                    font.letterSpacing: -(spacesOverview.width >= 720 ? 52 : 32) * 0.05; font.family: floeTheme.interfaceFont
                }
                Text {
                    text: root.displaySpaces.length ? "Every space in its place. Select one to return." : "No open spaces yet. Open an app from Desk or search."
                    textFormat: Text.PlainText; color: floeTheme.textMuted; font.pixelSize: 14; font.family: floeTheme.interfaceFont
                }
            }
            ListView {
                id: overviewRow
                anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; verticalCenterOffset: 24 }
                height: spacesOverview.cardHeight + 52
                orientation: ListView.Horizontal
                spacing: 32
                // Hidden, it rebuilds nothing; the cards are built while Overview takes its snapshot.
                model: spacesOverview.visible || root.overviewPending ? root.displaySpaces : []
                // Assigning a new model resets currentIndex to 0, so it is set from the selection, which is
                // a space id (root.overviewIndex), whenever either changes; only a drag writes it back.
                function follow() { if (currentIndex !== root.overviewIndex) currentIndex = root.overviewIndex }
                Connections { target: root; function onOverviewIndexChanged() { overviewRow.follow() } }
                onCountChanged: { follow(); if (count) positionViewAtIndex(currentIndex, ListView.Center) }
                onModelChanged: Qt.callLater(follow)
                onCurrentIndexChanged: if (moving && currentIndex >= 0) root.selectOverview(currentIndex)
                highlightRangeMode: ListView.StrictlyEnforceRange
                preferredHighlightBegin: width / 2 - spacesOverview.cardWidth / 2
                preferredHighlightEnd: width / 2 + spacesOverview.cardWidth / 2
                highlightMoveDuration: root.motionStandard
                boundsBehavior: Flickable.StopAtBounds
                WheelHandler {
                    // One space per notch, either direction.
                    acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
                    property real carry: 0
                    onWheel: event => {
                        carry += (event.angleDelta.y || event.angleDelta.x)
                        while (Math.abs(carry) >= 120) { root.moveOverview(carry > 0 ? -1 : 1); carry -= carry > 0 ? 120 : -120 }
                    }
                }
                delegate: Item {
                    id: spaceCard
                    required property var modelData
                    required property int index
                    readonly property bool selected: index === root.overviewIndex
                    readonly property string snapshot: root.snapshotUrl(modelData)
                    // Its snapshot is drawn (or it has none, or it could not be read).
                    readonly property bool pictured: !snapshot || snapshotImage.status === Image.Ready || snapshotImage.status === Image.Error
                    readonly property var pin: root.pinOfSpace(modelData)
                    width: spacesOverview.cardWidth; height: overviewRow.height
                    Rectangle {
                        // The selection ring: 2 px, 3 px outside the card, concentric with it.
                        visible: spaceCard.selected
                        x: -5; y: -5; width: card.width + 10; height: card.height + 10; radius: card.radius + 5
                        color: "transparent"; border.width: 2; border.color: floeTheme.focus
                    }
                    Rectangle {
                        id: card
                        width: parent.width; height: spacesOverview.cardHeight; radius: 14
                        color: floeTheme.surfaceRaised; clip: true
                        border.width: 1; border.color: spaceCard.selected ? floeTheme.borderStrong : Qt.alpha(floeTheme.border, 0.6)
                        Item {
                            // The snapshot, below the Island's band and above the home indicator's, covering the
                            // card and centred in it, in the picture's own proportions (a space can have been
                            // snapshot on another display).
                            id: snapshotFrame
                            anchors.fill: parent; anchors.margins: 1; clip: true
                            visible: !!spaceCard.snapshot
                            readonly property real shotWidth: spacesOverview.width
                            readonly property real shotHeight: snapshotImage.implicitWidth > 0
                                ? shotWidth * snapshotImage.implicitHeight / snapshotImage.implicitWidth : spacesOverview.height
                            readonly property real shown: Math.max(1, shotHeight - spacesOverview.bandTop - spacesOverview.bandBottom)
                            readonly property real zoom: Math.max(width / Math.max(1, shotWidth), height / shown)
                            Image {
                                id: snapshotImage
                                width: snapshotFrame.shotWidth * snapshotFrame.zoom; height: snapshotFrame.shotHeight * snapshotFrame.zoom
                                x: (snapshotFrame.width - width) / 2
                                y: snapshotFrame.height / 2 - (spacesOverview.bandTop + snapshotFrame.shown / 2) * snapshotFrame.zoom
                                source: spaceCard.snapshot
                                // The snapshot has the display's resolution (snapshotActiveSpace); decoded at the
                                // card's width (Qt adds the display's scale), which JPEG does while decoding.
                                sourceSize.width: Math.ceil(card.width)
                                // Each new snapshot is a new URL (its version), so caching never shows a stale one.
                                fillMode: Image.Stretch; smooth: true; asynchronous: true
                            }
                        }
                        Column {
                            // Before its first snapshot a space shows its app and title.
                            visible: !spaceCard.snapshot
                            anchors.centerIn: parent; width: parent.width - 48; spacing: 6
                            Text {
                                width: parent.width; horizontalAlignment: Text.AlignHCenter; elide: Text.ElideRight
                                text: spaceCard.modelData.members.map(m => m.app_name || m.app_id).join("  +  ")
                                textFormat: Text.PlainText; color: floeTheme.textPrimary
                                font.pixelSize: 18; font.weight: Font.DemiBold; font.family: floeTheme.interfaceFont
                            }
                            Text {
                                width: parent.width; horizontalAlignment: Text.AlignHCenter; elide: Text.ElideRight
                                text: spaceCard.modelData.label; textFormat: Text.PlainText
                                color: floeTheme.textMuted; font.pixelSize: 12; font.family: floeTheme.interfaceFont
                            }
                        }
                        MouseArea {
                            anchors.fill: parent; hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: spaceCard.selected ? root.chooseOverviewSpace(spaceCard.index) : root.selectOverview(spaceCard.index)
                            onDoubleClicked: root.chooseOverviewSpace(spaceCard.index)
                        }
                        Accessible.role: Accessible.Button
                        Accessible.name: "Return to " + spaceCard.modelData.label
                        Accessible.description: spaceCard.selected ? "Current" : ""
                    }
                    Row {
                        // The caption: its pin number (or a dot), its name, what it is.
                        anchors { top: card.bottom; topMargin: 14; left: parent.left; right: parent.right }
                        spacing: 10
                        Rectangle {
                            width: Math.max(26, number.implicitWidth + 12); height: 22; radius: 6
                            color: "transparent"; border.width: 1; border.color: Qt.alpha(floeTheme.accent, 0.5)
                            Text {
                                id: number; anchors.centerIn: parent; textFormat: Text.PlainText
                                text: spaceCard.pin ? ((root.pins.indexOf(spaceCard.pin) < 9 ? "0" : "") + (root.pins.indexOf(spaceCard.pin) + 1)) : "·"
                                color: floeTheme.accent; font.pixelSize: 11; font.weight: Font.DemiBold
                                font.family: floeTheme.interfaceFont; font.features: { "tnum": 1 }
                            }
                        }
                        Text {
                            width: parent.width - 36 - 10 - kind.implicitWidth - 10; elide: Text.ElideRight
                            anchors.verticalCenter: parent.verticalCenter
                            text: spaceCard.modelData.group_id || spaceCard.modelData.members.length === 1 ? spaceCard.modelData.label
                                : spaceCard.modelData.members.map(m => m.app_name || m.label).join(" + ")
                            textFormat: Text.PlainText; color: floeTheme.textPrimary
                            font.pixelSize: 14; font.weight: spaceCard.selected ? Font.DemiBold : Font.Medium; font.family: floeTheme.interfaceFont
                        }
                        SectionLabel {
                            id: kind; anchors.verticalCenter: parent.verticalCenter
                            text: spaceCard.pin ? "PINNED" : spaceCard.modelData.group_id ? "GROUP" : "SPACE"
                        }
                    }
                }
            }
            FlowButton {
                // The rails: half pills at the screen's edges, as in the prototype.
                id: railPrevious
                anchors { left: parent.left; leftMargin: -14; verticalCenter: overviewRow.verticalCenter; verticalCenterOffset: -26 }
                width: 58; implicitHeight: 64; radius: 14; glyph: "left"; text: "‹"; leftPadding: 18
                // For a pointer or a finger. Half off the screen, they could not show a whole focus ring, and the
                // keyboard has the arrow keys and the tools' ← →; screen readers have those tools too.
                focusPolicy: Qt.NoFocus
                Accessible.ignored: true
                enabled: root.overviewIndex > 0
                Accessible.name: "Previous space"
                onClicked: root.moveOverview(-1)
            }
            FlowButton {
                id: railNext
                anchors { right: parent.right; rightMargin: -14; verticalCenter: overviewRow.verticalCenter; verticalCenterOffset: -26 }
                width: 58; implicitHeight: 64; radius: 14; glyph: "right"; text: "›"; rightPadding: 18
                focusPolicy: Qt.NoFocus
                Accessible.ignored: true
                enabled: root.overviewIndex < root.displaySpaces.length - 1
                Accessible.name: "Next space"
                onClicked: root.moveOverview(1)
            }
            Rectangle {
                // The tools: Open selected · Pin · ← → · Your desk.
                anchors { horizontalCenter: parent.horizontalCenter; bottom: parent.bottom; bottomMargin: 56 }
                width: overviewTools.implicitWidth + 14; height: 58; radius: 15
                color: floeTheme.surface; border.width: 1; border.color: Qt.alpha(floeTheme.border, 0.6)
                Row {
                    id: overviewTools
                    anchors.centerIn: parent; spacing: 4
                    FlowButton {
                        variant: "quiet"; focusPolicy: Qt.TabFocus; implicitHeight: 44; radius: 8; text: "↗  Open selected"
                        enabled: !!root.overviewSpace
                        Accessible.name: root.overviewSpace ? "Open selected: " + root.overviewSpace.label : "Open selected"
                        onClicked: root.chooseOverviewSpace(root.overviewIndex)
                    }
                    Rectangle { width: 1; height: 24; anchors.verticalCenter: parent.verticalCenter; color: Qt.alpha(floeTheme.border, 0.5) }
                    FlowButton {
                        readonly property var pin: root.pinOfSpace(root.overviewSpace)
                        variant: "quiet"; focusPolicy: Qt.TabFocus; implicitHeight: 44; radius: 8
                        // Pinning acts on the focused space, so it is offered for that one; any pin can be removed.
                        text: pin ? "Unpin" : "Pin space"
                        visible: !!root.overviewSpace && (!!pin || root.overviewSpace.active)
                        Accessible.name: (pin ? "Unpin " : "Pin space: ") + (root.overviewSpace ? root.overviewSpace.label : "")
                        onClicked: pin ? root.spaceAction("unpin", pin.id) : root.spaceAction(root.overviewSpace.group_id ? "pin-group" : "pin", root.overviewSpace.group_id || undefined)
                    }
                    FlowButton { variant: "quiet"; focusPolicy: Qt.TabFocus; implicitHeight: 44; width: 44; radius: 8; glyph: "left"; text: "←"; Accessible.name: "Previous space"; enabled: root.overviewIndex > 0; onClicked: root.moveOverview(-1) }
                    FlowButton { variant: "quiet"; focusPolicy: Qt.TabFocus; implicitHeight: 44; width: 44; radius: 8; glyph: "right"; text: "→"; Accessible.name: "Next space"; enabled: root.overviewIndex < root.displaySpaces.length - 1; onClicked: root.moveOverview(1) }
                    Rectangle { width: 1; height: 24; anchors.verticalCenter: parent.verticalCenter; color: Qt.alpha(floeTheme.border, 0.5) }
                    FlowButton { variant: "primary"; focusPolicy: Qt.TabFocus; implicitHeight: 44; radius: 8; text: "↓  Your desk"; onClicked: root.revealDeskFromOverview() }
                }
            }
            FlowButton {
                anchors { right: parent.right; bottom: parent.bottom; rightMargin: spacesOverview.edge; bottomMargin: 62 }
                variant: "quiet"; focusPolicy: Qt.TabFocus; implicitHeight: 44; radius: 22; text: "?  Shortcuts"
                Accessible.name: "Show keyboard shortcuts"
                onClicked: { root.closeSpacesOverview(); Quickshell.execDetached(["niri", "msg", "action", "show-hotkey-overlay"]) }
            }
            Text {
                visible: spacesOverview.width >= 720
                anchors { left: parent.left; bottom: parent.bottom; leftMargin: spacesOverview.edge; bottomMargin: 22 }
                text: "↑  Again to reveal your desk  ·  ← →  choose  ·  Enter  return  ·  Esc  close"
                textFormat: Text.PlainText; color: floeTheme.textMuted; font.pixelSize: 12; font.family: floeTheme.interfaceFont
            }
            HomeIndicator {
                id: overviewHome
                visible: root.touchPresent
                z: 2; width: 160; height: 44
                anchors { bottom: parent.bottom; horizontalCenter: parent.horizontalCenter }
            }
        }
    }
    PanelWindow {
        // Flow's search, as in the prototype: a shade over everything and one palette. Typing filters,
        // Up/Down choose, Enter opens, Escape or the shade closes.
        id: searchPanel
        visible: root.searchOpen
        screen: root.panelScreen
        anchors { top: true; bottom: true; left: true; right: true }
        color: "transparent"
        exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive
        onVisibleChanged: if (visible) searchField.forceActiveFocus()
        FocusScope {
        anchors.fill: parent; focus: true
        Keys.onEscapePressed: root.closeSearch()
        Rectangle {
            id: searchShade
            anchors.fill: parent; color: Qt.alpha(floeTheme.canvas, 0.72)
            MouseArea { anchors.fill: parent; onClicked: root.closeSearch() }
        }
        Rectangle {
            id: palette
            x: (parent.width - width) / 2; y: Math.max(64, Math.min(190, parent.height * 0.17))
            width: Math.min(620, parent.width - 28)
            // The results area; with no rows it keeps room for its message, which never meets the footer.
            readonly property real resultsHeight: root.searchMatches.length ? Math.min(searchView.contentHeight + 20, 7 * 58 + 20) : 76
            height: searchHeader.height + 1 + resultsHeight + 1 + searchFooter.height
            radius: 18; color: floeTheme.surface; border.width: 1; border.color: floeTheme.border
            clip: true
            MouseArea { anchors.fill: parent }   // clicks on the palette never reach the shade
            Item {
                id: searchHeader
                width: parent.width; height: 64
                FlowIcon { x: 20; anchors.verticalCenter: parent.verticalCenter; name: "search"; size: 20; color: floeTheme.accent }
                TextField {
                    id: searchField
                    x: 54; width: parent.width - 54 - 56; anchors.verticalCenter: parent.verticalCenter; height: 44
                    text: root.searchQuery; onTextEdited: root.searchQuery = text
                    placeholderText: root.searchAppsOnly ? "Search apps…" : "Search apps, spaces, windows and files…"
                    placeholderTextColor: floeTheme.textMuted; color: floeTheme.textPrimary
                    selectionColor: floeTheme.accent; selectedTextColor: floeTheme.onAccent
                    font.pixelSize: 18; font.family: floeTheme.interfaceFont
                    // No box at rest: the palette is the field. Focus draws the 2 px ring at its edge, as the
                    // Group name field does, since a blinking caret alone is easy to lose.
                    background: Rectangle {
                        radius: 10; color: "transparent"
                        border.width: searchField.activeFocus ? 2 : 0; border.color: floeTheme.focus
                    }
                    Accessible.name: root.searchAppsOnly ? "Search apps" : "Search apps, spaces, windows and files"
                    Keys.onDownPressed: root.searchIndex = Math.max(0, Math.min(root.searchMatches.length - 1, root.searchIndex + 1))
                    Keys.onUpPressed: root.searchIndex = Math.max(0, root.searchIndex - 1)
                    // A held key repeats: only its first press chooses.
                    Keys.onReturnPressed: event => { if (!event.isAutoRepeat) root.runSearchResult(root.searchMatches[root.searchIndex]) }
                    Keys.onEnterPressed: event => { if (!event.isAutoRepeat) root.runSearchResult(root.searchMatches[root.searchIndex]) }
                }
                FlowButton {
                    anchors { right: parent.right; rightMargin: 10; verticalCenter: parent.verticalCenter }
                    width: 44; implicitHeight: 44; radius: 10; variant: "plain"; glyph: "close"; text: "×"
                    Accessible.name: "Close search"
                    onClicked: root.closeSearch()
                }
            }
            Rectangle { anchors.top: searchHeader.bottom; width: parent.width; height: 1; color: Qt.alpha(floeTheme.border, 0.5) }
            ListView {
                id: searchView
                anchors { top: searchHeader.bottom; topMargin: 11; left: parent.left; right: parent.right; margins: 10 }
                height: Math.min(contentHeight, 7 * 58)
                clip: true; boundsBehavior: Flickable.StopAtBounds
                model: root.searchMatches
                currentIndex: root.searchIndex
                onCurrentIndexChanged: positionViewAtIndex(currentIndex, ListView.Contain)
                delegate: Rectangle {
                    id: resultRow
                    required property var modelData
                    required property int index
                    readonly property bool chosen: index === root.searchIndex
                    readonly property bool armed: !!modelData.confirm && root.searchArmed === modelData.value
                    // Drawn as under the pointer: tinted without being the chosen row (pointer_target).
                    readonly property bool tinted: !chosen && Qt.colorEqual(color, floeTheme.surfaceSelected)
                    width: searchView.width; height: 58; radius: 10
                    color: chosen || rowArea.containsMouse ? floeTheme.surfaceSelected : "transparent"
                    // The chosen row also gets the focus colour's edge, never tint alone.
                    border.width: chosen ? 2 : 0; border.color: floeTheme.focus
                    Rectangle {
                        id: resultIcon
                        x: 13; anchors.verticalCenter: parent.verticalCenter; width: 32; height: 32; radius: 9
                        color: floeTheme.surfaceSelected
                        Image {
                            id: appIcon
                            anchors.centerIn: parent; width: 22; height: 22; sourceSize: Qt.size(44, 44)
                            visible: status === Image.Ready
                            source: resultRow.modelData.icon ? Quickshell.iconPath(resultRow.modelData.icon, true) : ""
                        }
                        FlowIcon {
                            visible: !appIcon.visible; anchors.centerIn: parent; size: 16; color: floeTheme.accent
                            name: ({ "pin": "pin", "window": "window", "file": "file", "settings": "sliders", "layer": "layer", "app": "app",
                                     "action": "grid", "command": "power" })[resultRow.modelData.kind] || "search"
                        }
                    }
                    Column {
                        anchors { left: resultIcon.right; leftMargin: 13; right: jump.left; rightMargin: 10; verticalCenter: parent.verticalCenter }
                        spacing: 3
                        Text {
                            width: parent.width; elide: Text.ElideRight; textFormat: Text.PlainText
                            text: resultRow.modelData.title; color: floeTheme.textPrimary
                            font.pixelSize: 14; font.weight: resultRow.chosen ? Font.DemiBold : Font.Medium; font.family: floeTheme.interfaceFont
                        }
                        Text {
                            width: parent.width; elide: Text.ElideRight; textFormat: Text.PlainText
                            text: resultRow.armed ? "Press Enter or click again to " + resultRow.modelData.title.toLowerCase()
                                : resultRow.modelData.detail
                            color: resultRow.armed ? floeTheme.textPrimary : floeTheme.textMuted
                            font.pixelSize: 12; font.family: floeTheme.interfaceFont
                        }
                    }
                    Text {
                        id: jump
                        anchors { right: parent.right; rightMargin: 14; verticalCenter: parent.verticalCenter }
                        text: "↗"; textFormat: Text.PlainText; color: floeTheme.textMuted; font.pixelSize: 14
                    }
                    MouseArea {
                        id: rowArea
                        anchors.fill: parent; hoverEnabled: true
                        onClicked: root.runSearchResult(resultRow.modelData)
                    }
                    Accessible.role: Accessible.Button
                    Accessible.name: resultRow.modelData.title + ", " + (resultRow.armed
                        ? "press Enter or click again to " + resultRow.modelData.title.toLowerCase() : resultRow.modelData.detail)
                    Accessible.description: chosen ? "Current" : ""
                }
            }
            Text {
                id: searchEmpty
                visible: root.searchMatches.length === 0
                anchors { top: searchHeader.bottom; topMargin: 1; left: parent.left; right: parent.right; margins: 20 }
                height: palette.resultsHeight
                horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
                text: root.searchListing || root.searchWalk ? "Looking…"
                    : root.searchFailed ? "Search couldn’t list anything. Close it and try again."
                    : root.searchQuery.trim() ? "Nothing matches “" + root.searchQuery.trim() + "”"
                    : "Nothing to show"
                textFormat: Text.PlainText; color: floeTheme.textMuted; font.pixelSize: 14; font.family: floeTheme.interfaceFont
                Accessible.role: Accessible.StaticText; Accessible.name: text
            }
            Rectangle { anchors.bottom: searchFooter.top; width: parent.width; height: 1; color: Qt.alpha(floeTheme.border, 0.5) }
            Item {
                id: searchFooter
                anchors.bottom: parent.bottom; width: parent.width; height: 44
                Text {
                    x: 20; anchors.verticalCenter: parent.verticalCenter
                    text: "Enter to open  ·  ↑ ↓ to choose  ·  Esc to close"
                    textFormat: Text.PlainText; color: floeTheme.textMuted; font.pixelSize: 12; font.family: floeTheme.interfaceFont
                }
            }
        }
        }
    }
    // shortcut: window titles stand in for live previews until compositor capture is available.
    PanelWindow {
        visible: root.switcherOpen
        id: switcherWindow
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
                    FlowButton { id: stackButton; variant: "quiet"; implicitHeight: 44; text: "Stack with next"; onClicked: root.spaceAction("stack") }
                    FlowButton { id: unstackButton; variant: "quiet"; implicitHeight: 44; text: "Separate stack"; onClicked: root.spaceAction("unstack") }
                    FlowButton { id: overviewButton; variant: "quiet"; implicitHeight: 44; text: "Live overview"; onClicked: root.showOverview() }
                    FlowButton {
                        id: makeLayerButton
                        variant: "quiet"; implicitHeight: 44; text: "Make Layer"
                        visible: !root.activeLayer
                        Accessible.name: "Make Layer: float the focused app over its neighbors"
                        enabled: !root.spacesWorking && !root.recoveryWorking && !root.pendingSpaceActions.length
                        onClicked: root.spaceAction("make-layer")
                    }
                    FlowButton { id: closeSwitcher; variant: "quiet"; implicitHeight: 44; text: "Close"; Accessible.name: "Close Spaces switcher"; onClicked: root.switcherOpen = false }
                    Row {
                        visible: spaceScroller.contentWidth > spaceScroller.width
                        spacing: 6
                        FlowButton {
                            id: previousSpaces
                            width: 44; implicitHeight: 44; variant: "quiet"; glyph: "left"; text: "‹"; Accessible.name: "Show earlier spaces"
                            enabled: spaceScroller.contentX > 0
                            onClicked: spaceScroller.contentX = Math.max(0, spaceScroller.contentX - spaceScroller.width * 0.8)
                        }
                        FlowButton {
                            id: nextSpaces
                            width: 44; implicitHeight: 44; variant: "quiet"; glyph: "right"; text: "›"; Accessible.name: "Show later spaces"
                            enabled: spaceScroller.contentX < spaceScroller.contentWidth - spaceScroller.width
                            onClicked: spaceScroller.contentX = Math.min(spaceScroller.contentWidth - spaceScroller.width, spaceScroller.contentX + spaceScroller.width * 0.8)
                        }
                    }
                    Row {
                        id: switcherSpacesError
                        visible: root.spacesError !== ""; width: parent.width; spacing: 8
                        Text { text: root.spacesError; textFormat: Text.PlainText; color: floeTheme.danger; wrapMode: Text.Wrap; width: parent.width - dismissSwitcherSpacesError.width - 8; height: Math.max(44, implicitHeight); verticalAlignment: Text.AlignVCenter; font.pixelSize: 14; font.family: floeTheme.interfaceFont }
                        FlowButton { id: dismissSwitcherSpacesError; width: 96; implicitHeight: 44; text: "Dismiss"; variant: "quiet"; Accessible.name: "Dismiss spaces error"; onClicked: root.spacesError = "" }
                    }
                }
                Flow {
                    visible: root.layers.length > 0
                    width: parent.width; spacing: 8
                    SectionLabel { width: 72; height: 44; verticalAlignment: Text.AlignVCenter; text: "LAYERS" }
                    Repeater {
                        model: switcherWindow.visible ? root.layers : []  // hidden, it rebuilds nothing
                        Flow {
                            required property var modelData
                            // Room for one Layer's four controls; narrower screens wrap them.
                            width: Math.min(parent.width, 540); spacing: 4
                            FlowButton {
                                id: layerButton
                                width: 180; implicitHeight: 44; selected: modelData.active
                                text: (modelData.app_name || modelData.label).slice(0, 24)
                                Accessible.name: "Focus Layer " + (modelData.app_name || modelData.label)
                                onClicked: root.spaceAction("focus-window", String(modelData.id))
                            }
                            FlowButton {
                                id: layerSpaceButton
                                variant: "quiet"; implicitHeight: 44; text: "Make space"
                                Accessible.name: "Make space: Layer " + (modelData.app_name || modelData.label) + " becomes a space of its own"
                                enabled: !root.spacesWorking && !root.recoveryWorking && !root.pendingSpaceActions.length
                                onClicked: root.spaceAction("layer-to-space", String(modelData.id))
                            }
                            FlowButton {
                                id: layerGroupButton
                                variant: "quiet"; implicitHeight: 44; text: "Add to Group"
                                Accessible.name: "Add to Group: Layer " + (modelData.app_name || modelData.label) + " joins the Group beneath it"
                                enabled: !root.spacesWorking && !root.recoveryWorking && !root.pendingSpaceActions.length
                                onClicked: root.spaceAction("layer-to-group", String(modelData.id))
                            }
                            FlowButton {
                                id: layerCloseButton
                                variant: "danger"; implicitHeight: 44; text: "Close"
                                Accessible.name: "Close Layer " + (modelData.app_name || modelData.label) + "; the app is asked to close"
                                enabled: !root.spacesWorking && !root.recoveryWorking && !root.pendingSpaceActions.length
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
                    Row {
                        id: windowRow; spacing: 12
                        Repeater {
                            model: switcherWindow.visible ? root.displaySpaces : []  // hidden, it rebuilds nothing
                            Rectangle {
                                id: spaceCard
                                required property var modelData
                                property var groupPin: root.pins.find(p => p.kind === "group" && p.group_id === modelData.group_id) || null
                                // Its controls are read, and reached by Tab, as one block: the card, then the next.
                                readonly property bool readingGroup: true
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
                                        width: parent.width; height: modelData.members.length > 1 ? 52 : 84; radius: 10
                                        variant: "plain"; leftPadding: 10; rightPadding: 10
                                        text: modelData.label
                                        // The card shows "Current" in its meta line; assistive technology hears it too.
                                        Accessible.description: modelData.active ? "Current" : ""
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
                                                    width: 140; implicitHeight: 44; radius: 10; selected: memberColumn.modelData.active
                                                    text: memberColumn.modelData.label.slice(0, 14)
                                                    Accessible.name: "Focus " + memberColumn.modelData.label + " in group"
                                                    onClicked: root.spaceAction("focus-window", String(memberColumn.modelData.id))
                                                }
                                                Row {
                                                    visible: memberColumn.grouped; spacing: 4
                                                    FlowButton {
                                                        id: memberLeftButton
                                                        width: 44; implicitHeight: 44; radius: 10; variant: "quiet"; glyph: memberColumn.space.group_layout === "side-by-side" ? "left" : "up"; text: memberColumn.space.group_layout === "side-by-side" ? "‹" : "↑"
                                                        enabled: memberColumn.index > 0 && !memberColumn.space.order_changed && !root.spacesWorking && !root.recoveryWorking && !root.pendingSpaceActions.length
                                                        Accessible.name: "Move " + memberColumn.modelData.label + " earlier in Group " + memberColumn.space.label
                                                        onClicked: root.spaceAction("member-left", String(memberColumn.modelData.id))
                                                    }
                                                    FlowButton {
                                                        id: memberRightButton
                                                        width: 44; implicitHeight: 44; radius: 10; variant: "quiet"; glyph: memberColumn.space.group_layout === "side-by-side" ? "right" : "down"; text: memberColumn.space.group_layout === "side-by-side" ? "›" : "↓"
                                                        enabled: memberColumn.index < memberColumn.space.members.length - 1 && !memberColumn.space.order_changed && !root.spacesWorking && !root.recoveryWorking && !root.pendingSpaceActions.length
                                                        Accessible.name: "Move " + memberColumn.modelData.label + " later in Group " + memberColumn.space.label
                                                        onClicked: root.spaceAction("member-right", String(memberColumn.modelData.id))
                                                    }
                                                    FlowButton {
                                                        id: memberRemoveButton
                                                        width: 44; implicitHeight: 44; radius: 10; variant: "danger"; glyph: "close"; text: "×"
                                                        visible: !!memberColumn.space.can_remove
                                                        enabled: !root.spacesWorking && !root.recoveryWorking && !root.pendingSpaceActions.length
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
                                                        width: 44; implicitHeight: 44; radius: 10; variant: "quiet"; glyph: "minus"; text: "−"
                                                        enabled: !!(memberColumn.space.can_narrow && memberColumn.space.can_narrow[memberColumn.index]) && !root.spacesWorking && !root.recoveryWorking && !root.pendingSpaceActions.length
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
                                                        width: 44; implicitHeight: 44; radius: 10; variant: "quiet"; glyph: "plus"; text: "+"
                                                        enabled: !!(memberColumn.space.can_widen && memberColumn.space.can_widen[memberColumn.index]) && !root.spacesWorking && !root.recoveryWorking && !root.pendingSpaceActions.length
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
                                        width: parent.width; implicitHeight: 44; radius: 10; text: "Create Group"
                                        Accessible.name: "Create Group from " + spaceCard.modelData.label
                                        enabled: spaceCard.modelData.active && !root.spacesWorking && !root.recoveryWorking && !root.pendingSpaceActions.length
                                        onClicked: root.spaceAction("create-group")
                                    }
                                    FlowButton {
                                        id: addMemberButton
                                        visible: !!spaceCard.modelData.can_add
                                        width: parent.width; implicitHeight: 44; radius: 10; text: "Add " + (spaceCard.modelData.add_label || "next app") + " to Group"
                                        Accessible.name: "Add " + (spaceCard.modelData.add_label || "next app") + " to Group " + spaceCard.modelData.label
                                        enabled: !root.spacesWorking && !root.recoveryWorking && !root.pendingSpaceActions.length
                                        onClicked: root.spaceAction("add-to-group", spaceCard.modelData.group_id)
                                    }
                                    FlowButton {
                                        id: keepOrderButton
                                        visible: !!spaceCard.modelData.order_changed
                                        width: parent.width; implicitHeight: 44; radius: 10; text: "Keep new app order"
                                        Accessible.name: "Keep new app order for Group " + spaceCard.modelData.label
                                        enabled: !root.spacesWorking && !root.recoveryWorking && !root.pendingSpaceActions.length
                                        onClicked: root.spaceAction("adopt-group-order", spaceCard.modelData.group_id)
                                    }
                                    Grid {
                                        visible: !!spaceCard.modelData.group_id
                                        columns: 2; spacing: 4
                                        FlowButton {
                                            id: renameButton
                                            width: (cardColumn.width - 4) / 2; implicitHeight: 44; radius: 10; leftPadding: 6; rightPadding: 6; variant: "quiet"; text: "Rename"
                                            Accessible.name: "Rename Group " + spaceCard.modelData.label
                                            enabled: !root.spacesWorking && !root.recoveryWorking && !root.pendingSpaceActions.length
                                            onClicked: { groupName.text = spaceCard.modelData.label; root.renameGroupId = spaceCard.modelData.group_id; groupName.forceActiveFocus() }
                                        }
                                        FlowButton {
                                            id: groupViewButton
                                            width: (cardColumn.width - 4) / 2; implicitHeight: 44; radius: 10; leftPadding: 6; rightPadding: 6; variant: "quiet"; text: "Tabs / stack"
                                            visible: spaceCard.modelData.group_layout !== "side-by-side"
                                            enabled: spaceCard.modelData.active && !root.spacesWorking && !root.recoveryWorking && !root.pendingSpaceActions.length
                                            Accessible.name: "Tabs / stack: switch the view of " + spaceCard.modelData.label
                                            onClicked: root.spaceAction("group-view", spaceCard.modelData.group_id)
                                        }
                                        FlowButton {
                                            id: groupLayoutButton
                                            visible: !!spaceCard.modelData.group_id
                                            width: (cardColumn.width - 4) / 2; implicitHeight: 44; radius: 10; leftPadding: 6; rightPadding: 6; variant: "quiet"
                                            text: spaceCard.modelData.group_layout === "side-by-side" ? "Stack" : "Side by side"
                                            Accessible.name: text + " Group " + spaceCard.modelData.label
                                            enabled: spaceCard.modelData.active && !root.spacesWorking && !root.recoveryWorking && !root.pendingSpaceActions.length
                                            onClicked: root.spaceAction("group-layout", spaceCard.modelData.group_id)
                                        }
                                        FlowButton {
                                            id: autoLayoutButton
                                            visible: !!spaceCard.modelData.group_id
                                            width: (cardColumn.width - 4) / 2; implicitHeight: 44; radius: 10; leftPadding: 6; rightPadding: 6; variant: "quiet"
                                            text: spaceCard.modelData.auto_layout ? "Auto layout: on" : "Auto layout: off"
                                            Accessible.name: text + " for " + spaceCard.modelData.label
                                            enabled: spaceCard.modelData.active && !root.spacesWorking && !root.recoveryWorking && !root.pendingSpaceActions.length
                                            onClicked: root.spaceAction("toggle-auto-layout", spaceCard.modelData.group_id)
                                        }
                                        FlowButton {
                                            id: groupPinButton
                                            visible: !!spaceCard.modelData.group_id
                                            width: (cardColumn.width - 4) / 2; implicitHeight: 44; radius: 10; leftPadding: 6; rightPadding: 6; variant: "quiet"
                                            text: spaceCard.groupPin ? "Unpin Group" : spaceCard.modelData.group_layout === "side-by-side" ? "Stack to pin" : "Pin Group"
                                            Accessible.name: text + " " + spaceCard.modelData.label
                                            enabled: (spaceCard.groupPin || spaceCard.modelData.group_layout !== "side-by-side") && !root.spacesWorking && !root.recoveryWorking && !root.pendingSpaceActions.length
                                            onClicked: root.spaceAction(spaceCard.groupPin ? "unpin" : "pin-group", spaceCard.groupPin ? spaceCard.groupPin.id : spaceCard.modelData.group_id)
                                        }
                                        FlowButton {
                                            id: dissolveGroupButton
                                            visible: !!spaceCard.modelData.group_id
                                            width: (cardColumn.width - 4) / 2; implicitHeight: 44; radius: 10; leftPadding: 6; rightPadding: 6; variant: "danger"
                                            text: spaceCard.groupPin ? "Unpin first" : "Dissolve"
                                            Accessible.name: (spaceCard.groupPin ? "Unpin first to dissolve Group " : "Dissolve Group ") + spaceCard.modelData.label
                                            enabled: spaceCard.modelData.active && !spaceCard.groupPin && !root.spacesWorking && !root.recoveryWorking && !root.pendingSpaceActions.length
                                            onClicked: root.spaceAction("dissolve-group", spaceCard.modelData.group_id)
                                        }
                                    }
                                    Repeater {
                                        // Moving acts on the active space, so the choice appears on that card only.
                                        model: spaceCard.modelData.active ? root.displays.filter(d => !d.current) : []
                                        FlowButton {
                                            id: moveDisplayButton
                                            required property var modelData
                                            width: cardColumn.width; implicitHeight: 44; radius: 10; variant: "quiet"
                                            text: "Move to " + modelData.label.slice(0, 22)
                                            Accessible.name: "Move to " + modelData.label + ": " + spaceCard.modelData.label + (spaceCard.modelData.group_id ? ", the whole Group" : "")
                                            enabled: !root.spacesWorking && !root.recoveryWorking && !root.pendingSpaceActions.length
                                            onClicked: root.spaceAction("move-to-display", modelData.key)
                                        }
                                    }
                                    Row {
                                        visible: !!spaceCard.modelData.group_id && root.renameGroupId === spaceCard.modelData.group_id
                                        width: parent.width; spacing: 4
                                        Keys.onEscapePressed: { root.renameGroupId = ""; renameButton.forceActiveFocus() }
                                        TextField {
                                            id: groupName
                                            onActiveFocusChanged: if (activeFocus && groupName.focusReason !== Qt.MouseFocusReason) root.reveal(groupName)
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
                                        FlowButton { id: saveGroupName; width: 64; implicitHeight: 44; radius: 10; variant: "primary"; text: "Save"; Accessible.name: "Save name for Group " + spaceCard.modelData.label; enabled: !root.spacesWorking && !root.recoveryWorking && !root.pendingSpaceActions.length; onClicked: root.renameGroup(spaceCard.modelData.group_id, groupName.text) }
                                        FlowButton { id: cancelGroupName; width: 64; implicitHeight: 44; radius: 10; variant: "quiet"; text: "Cancel"; Accessible.name: "Cancel rename for Group " + spaceCard.modelData.label; onClicked: { root.renameGroupId = ""; renameButton.forceActiveFocus() } }
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
                        model: switcherWindow.visible ? root.incompleteGroups : []  // hidden, it rebuilds nothing
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
                                                    enabled: !root.spacesWorking && !root.recoveryWorking && !root.pendingSpaceActions.length
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
                                    enabled: !root.spacesWorking && !root.recoveryWorking && !root.pendingSpaceActions.length
                                    onClicked: root.spaceAction("forget-choice", modelData.id)
                                }
                                FlowButton {
                                    id: openGroupMember
                                    visible: modelData.can_open
                                    width: Math.min(parent.width, 200); implicitHeight: 44; radius: 10; text: "Open missing app"
                                    Accessible.name: "Open missing app for " + modelData.label
                                    enabled: !root.spacesWorking && !root.recoveryWorking && !root.pendingSpaceActions.length
                                    onClicked: root.spaceAction("open-group-member", modelData.id)
                                }
                                FlowButton {
                                    id: rejoinGroup
                                    visible: modelData.can_rejoin
                                    width: Math.min(parent.width, 200); implicitHeight: 44; radius: 10; text: "Rejoin Group"
                                    Accessible.name: "Rejoin Group for " + modelData.label
                                    enabled: !root.spacesWorking && !root.recoveryWorking && !root.pendingSpaceActions.length
                                    onClicked: root.spaceAction("rejoin-group", modelData.id)
                                }
                                FlowButton {
                                    id: resumeGroupLayout
                                    visible: modelData.can_resume_layout
                                    width: Math.min(parent.width, 220); implicitHeight: 44; radius: 10; text: "Finish layout change"
                                    Accessible.name: "Finish layout change for Group " + modelData.label
                                    enabled: !root.spacesWorking && !root.recoveryWorking && !root.pendingSpaceActions.length
                                    onClicked: root.spaceAction("resume-group-layout", modelData.id)
                                }
                                FlowButton {
                                    id: adoptGroupOrder
                                    visible: modelData.can_adopt_order
                                    width: Math.min(parent.width, 220); implicitHeight: 44; radius: 10; text: "Keep current order"
                                    Accessible.name: "Keep current order for Group " + modelData.label
                                    enabled: !root.spacesWorking && !root.recoveryWorking && !root.pendingSpaceActions.length
                                    onClicked: root.spaceAction("adopt-group-order", modelData.id)
                                }
                                FlowButton {
                                    id: repairGroupLayout
                                    visible: modelData.can_repair
                                    width: Math.min(parent.width, 220); implicitHeight: 44; radius: 10; text: "Repair Group layout"
                                    Accessible.name: "Repair Group layout for " + modelData.label
                                    enabled: !root.spacesWorking && !root.recoveryWorking && !root.pendingSpaceActions.length
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
        screen: root.islandOpen && root.islandScreen ? root.islandScreen : root.workScreen
        readonly property var band: root.bandFor(screen)
        // In a notched screen's band, at its left (§8.1); on a plain screen only with the opened Island (ADR-0014).
        visible: root.appMenuAvailable && !root.workCovered && !root.overviewOpen && !root.appMenuOpen
            && (!!band || (root.islandOpen && workIsland.visible))
        anchors { top: true; left: true }
        implicitWidth: 220; implicitHeight: band ? band.height : 44
        // On the opened Island's row, 8px right of the card: the button shows only for apps with a menu bar, and
        // Qt's and GTK's menu bars start at the left, so the left of the row would cover the app's own File and
        // Edit menus. Below the card on a narrow screen, where panels hang too.
        readonly property bool narrow: !band && (!screen || screen.width < islandCard.width + 472)
        margins.left: band || narrow ? 8 : Math.round((screen.width + islandCard.width) / 2) + 8
        margins.top: band ? 0 : narrow ? islandCard.height + 12 : 6
        color: "transparent"; exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        FlowButton {
            id: appMenuButton
            anchors.fill: parent
            text: (root.appMenuProbe ? root.appMenuProbe.name : "Application") + "  ▾"
            onClicked: root.requestAppMenu()
        }
    }
    PanelWindow {
        id: appMenuSurface
        visible: menuPreferences.appMenuEnabled && root.appMenuOpen && !root.deskOpen && !root.overviewOpen
        screen: appMenuTrigger.screen
        anchors { top: true; bottom: true; left: true; right: true }
        color: "transparent"; exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive
        MouseArea { anchors.fill: parent; onClicked: root.appMenuOpen = false }
        FocusScope {
            anchors.fill: parent; focus: true
            Keys.onEscapePressed: root.appMenuOpen = false
            Rectangle {
                x: Math.max(8, Math.min(appMenuTrigger.margins.left, parent.width - width - 8))
                y: appMenuTrigger.margins.top + appMenuTrigger.implicitHeight + 8
                width: Math.min(390, parent.width - 16); height: Math.min(menuColumn.implicitHeight + 32, parent.height - y - 8)
                radius: 16; color: floeTheme.surface; border.color: floeTheme.border
                Flickable {
                    id: appMenuFlick
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
                            id: appMenuItems
                            model: root.currentAppItems()
                            FlowButton {
                                required property var modelData
                                required property int index
                                width: menuColumn.width
                                text: modelData.label + (modelData.children ? "   ›" : "")
                                enabled: modelData.enabled && !root.appMenuActivateAsked && !root.appMenuFocusChanged
                                onClicked: {
                                    if (modelData.children) root.appMenuPath = root.appMenuPath.concat([index])
                                    else root.activateAppMenuItem(modelData)
                                }
                            }
                        }
                        FlowButton { width: menuColumn.width; text: "Close menu"; onClicked: root.appMenuOpen = false }
                    }
                }
            }
        }
    }
    SystemPanel {
        id: quickSettingsWindow
        shown: root.quickSettingsOpen; title: "Quick settings"
        onCloseRequested: root.quickSettingsOpen = false
        PanelRow {
            id: networkRow
            width: parent.width; text: "Network"; value: root.networkStatus
            valueColor: root.networkStatus === "Connected" ? floeTheme.success : floeTheme.textMuted
            Accessible.description: "Choose a Wi-Fi network"
            onClicked: { root.quickSettingsOpen = false; root.launchApp(["flow-wifi-settings"]) }
        }
        PanelRow {
            width: parent.width; text: "Edit connections"
            onClicked: { root.quickSettingsOpen = false; root.launchApp(["nm-connection-editor"]) }
        }
        PanelRow {
            width: parent.width; text: "On-screen keyboard"; value: "Show or hide"; valueColor: floeTheme.accent; chevron: false
            onClicked: { root.launchApp(["flow-keyboard"]); root.quickSettingsOpen = false }
        }
        PanelValue { width: parent.width; text: "Sound"; value: root.volumeStatus }
        Row {
            width: parent.width; spacing: 12
            VolumeSlider { width: parent.width - muteButton.width - parent.spacing }
            FlowButton {
                id: muteButton
                width: 84; implicitHeight: 44; variant: "quiet"; enabled: root.audioAvailable
                text: root.audioAvailable && root.audioSink.audio.muted ? "Unmute" : "Mute"
                onClicked: root.toggleMute()
            }
        }
        Row {
            visible: !!root.mediaPlayer
            width: parent.width; spacing: 8; topPadding: 8
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
        Text {
            visible: root.quickError !== ""; topPadding: 8
            text: root.quickError; color: floeTheme.danger; font.pixelSize: 12; font.family: floeTheme.interfaceFont; width: parent.width; wrapMode: Text.Wrap
        }
    }
    SystemPanel {
        id: notificationPanel
        shown: root.notificationsOpen; title: "Notifications"
        onCloseRequested: root.notificationsOpen = false
        tools: FlowButton {
            visible: notificationServer.trackedNotifications.values.length > 0
            // On a narrow screen the short label leaves the title room.
            implicitHeight: 44; leftPadding: 12; rightPadding: 12; variant: "plain"; text: notificationPanel.implicitWidth < 360 ? "Clear" : "Clear all"
            Accessible.name: "Clear all notifications"
            onClicked: [...notificationServer.trackedNotifications.values].forEach(n => n.dismiss())
        }
        Column {
            visible: notificationServer.trackedNotifications.values.length === 0
            width: parent.width; topPadding: 16; bottomPadding: 20; spacing: 8
            FlowIcon { anchors.horizontalCenter: parent.horizontalCenter; name: "bell"; size: 22; color: floeTheme.textMuted }
            Text {
                width: parent.width; horizontalAlignment: Text.AlignHCenter
                text: "No notifications"; color: floeTheme.textSecondary; font.pixelSize: 14; font.weight: Font.Medium; font.family: floeTheme.interfaceFont
            }
            Text {
                width: parent.width; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
                text: "They gather here, above every space."; color: floeTheme.textMuted; font.pixelSize: 12; font.family: floeTheme.interfaceFont
            }
        }
        Repeater {
            id: notificationList
            // Newest first, so reading and Tab order match what is on screen. The reversed copy rebuilds the
            // list on each change (at most 20 items, by the trim above), so focus inside it does not survive
            // a notification arriving or going.
            model: notificationPanel.visible ? [...notificationServer.trackedNotifications.values].reverse() : []  // hidden, it rebuilds nothing
            delegate: Item {
                required property var modelData
                required property int index
                width: parent.width; implicitHeight: notificationItem.implicitHeight + 20
                Column {
                    id: notificationItem
                    y: 10; width: parent.width; spacing: 2
                    Row {
                        width: parent.width; spacing: 8
                        Column {
                            width: parent.width - dismiss.width - parent.spacing; anchors.verticalCenter: parent.verticalCenter; spacing: 2
                            SectionLabel {
                                visible: modelData.appName !== ""; width: parent.width; elide: Text.ElideRight
                                text: modelData.appName.toUpperCase()
                            }
                            Text {
                                width: parent.width; text: modelData.summary; textFormat: Text.PlainText; wrapMode: Text.Wrap
                                maximumLineCount: 3; elide: Text.ElideRight
                                color: floeTheme.textPrimary; font.weight: Font.DemiBold; font.pixelSize: 14; font.family: floeTheme.interfaceFont
                            }
                        }
                        FlowButton {
                            id: dismiss; width: 44; implicitHeight: 44; radius: 12; variant: "plain"; glyph: "close"; text: "×"
                            Accessible.name: "Dismiss notification"; onClicked: modelData.dismiss()
                        }
                    }
                    Text {
                        visible: modelData.body !== ""; width: parent.width; rightPadding: 52
                        text: modelData.body; textFormat: Text.PlainText; wrapMode: Text.Wrap; maximumLineCount: 6; elide: Text.ElideRight
                        color: floeTheme.textSecondary; font.pixelSize: 13; font.family: floeTheme.interfaceFont
                    }
                    Flow {
                        visible: modelData.actions.some(action => action.text !== "")
                        width: parent.width; spacing: 6; topPadding: 6
                        Repeater {
                            model: modelData.actions
                            delegate: FlowButton {
                                required property var modelData
                                // An action without a label (often "default") would be a blank, nameless button.
                                visible: modelData.text !== ""
                                width: Math.max(44, Math.min(200, implicitWidth)); implicitHeight: 44
                                text: modelData.text; Accessible.name: modelData.text
                                onClicked: modelData.invoke()
                            }
                        }
                    }
                }
                Rectangle {
                    visible: index < notificationList.count - 1
                    anchors.bottom: parent.bottom; width: parent.width; height: 1
                    color: Qt.alpha(floeTheme.border, 0.3)
                }
            }
        }
    }
    // A banner for a notification that arrives while the panel is closed, as a phone shows one: the
    // newest, near the top edge (below the band on a notched screen), for 5 s of being seen, and a critical one
    // until the notifications are opened. It takes no clicks and never the keyboard (ADR-0014): it sits over an app,
    // so a click there is the app's. To act on a notification, open the Island and its bell, or Super+N.
    PanelWindow {
        id: notificationBanner
        readonly property var notification: root.bannerNotification
        // Only in the plain work view (workViewOnly): Desk, Overview and the switcher would cover it (the
        // surface mapped later is on top), and the panels, search and the app menu sit where it does.
        // A notification the app closes is destroyed, which empties the property, so the banner leaves.
        visible: !!notification && root.workViewOnly
        anchors.top: true
        implicitWidth: Math.max(0, Math.min(370, screen.width - 24))
        implicitHeight: Math.max(bannerText.implicitHeight, 44) + 24
        margins.top: root.topInset(screen)
        mask: Region {}
        color: "transparent"; exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
        onVisibleChanged: if (visible && root.motionStandard) bannerEnter.restart()
        ParallelAnimation {
            id: bannerEnter
            NumberAnimation { target: bannerCard; property: "opacity"; from: 0; to: 1; duration: root.motionStandard; easing.type: Easing.BezierSpline; easing.bezierCurve: root.standardCurve }
            NumberAnimation { target: bannerCard; property: "y"; from: -8; to: 0; duration: root.motionStandard; easing.type: Easing.BezierSpline; easing.bezierCurve: root.standardCurve }
        }
        Rectangle {
            id: bannerCard
            width: parent.width; height: parent.height; radius: 19
            color: floeTheme.surface; border.width: 1
            border.color: Qt.alpha(floeTheme.border, 0.5)
            Accessible.role: Accessible.AlertMessage
            Accessible.name: notificationBanner.notification ? notificationBanner.notification.appName + ": "
                + notificationBanner.notification.summary : ""
            Accessible.description: notificationBanner.notification ? notificationBanner.notification.body : ""
            Column {
                id: bannerText
                x: 23; y: 12; width: parent.width - 46; spacing: 2
                SectionLabel {
                    visible: !!notificationBanner.notification && notificationBanner.notification.appName !== ""
                    width: parent.width; elide: Text.ElideRight
                    text: notificationBanner.notification ? notificationBanner.notification.appName.toUpperCase() : ""
                }
                Text {
                    width: parent.width; maximumLineCount: 2; wrapMode: Text.Wrap; elide: Text.ElideRight
                    text: notificationBanner.notification ? notificationBanner.notification.summary : ""; textFormat: Text.PlainText
                    color: floeTheme.textPrimary; font.weight: Font.DemiBold; font.pixelSize: 14; font.family: floeTheme.interfaceFont
                }
                Text {
                    visible: text !== ""
                    width: parent.width; maximumLineCount: 2; wrapMode: Text.Wrap; elide: Text.ElideRight
                    text: notificationBanner.notification ? notificationBanner.notification.body : ""; textFormat: Text.PlainText
                    color: floeTheme.textSecondary; font.pixelSize: 13; font.family: floeTheme.interfaceFont
                }
            }
        }
    }
    // The edge strip of each plain screen (ADR-0014): one row of pixels along the top edge, drawn as nothing. The
    // pointer pushed against the edge rests there, and after 150 ms the Island opens on that screen; a press there
    // opens it at once. Row 1 and below are the app's. A screen with a band has none: the band is the shell's.
    property string edgeHoveredScreen: ""
    property string pillHoveredScreen: ""
    Variants {
        id: edgeStrips
        model: Quickshell.screens
        PanelWindow {
            id: edgeStrip
            required property var modelData
            readonly property bool hovered: edgeArea.containsMouse
            screen: modelData
            visible: !root.bandFor(modelData) && !root.workCovered && !root.switcherOpen && !root.searchOpen && !root.overviewOpen
            anchors { top: true; left: true; right: true }
            implicitHeight: 1
            color: "transparent"; exclusionMode: ExclusionMode.Ignore
            WlrLayershell.layer: WlrLayer.Overlay
            WlrLayershell.namespace: "flow-edge"
            WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
            MouseArea {
                id: edgeArea
                anchors.fill: parent; hoverEnabled: true
                onContainsMouseChanged: {
                    if (containsMouse) { root.edgeHoveredScreen = edgeStrip.modelData.name; edgeDwell.restart() }
                    else { edgeDwell.stop(); if (root.edgeHoveredScreen === edgeStrip.modelData.name) root.edgeHoveredScreen = "" }
                }
                onPressed: if (!(root.islandOpen && root.islandScreenName === edgeStrip.modelData.name)) root.openIsland(edgeStrip.modelData.name, "press")
            }
            Timer {
                id: edgeDwell; interval: root.islandDwell
                onTriggered: if (edgeArea.containsMouse && !(root.islandOpen && root.islandScreenName === edgeStrip.modelData.name))
                    root.openIsland(edgeStrip.modelData.name, "edge")
            }
        }
    }
    // The opened Island on a plain screen: a card floating 6 px below the top edge, round on every corner, so it never
    // fakes a camera notch (§7.4). Its surface starts at the edge, so the way down from the strip stays on it. It
    // closes 500 ms after the pointer has left it (and the strip, the pill and the App button), once whatever it
    // opened has closed. Opened by Super+B it takes the keyboard (Escape closes it), and like a menu its surface
    // covers the screen, so a click anywhere else closes it (and goes no further): the keyboard is never left with an
    // Island the user has stopped looking at.
    property double islandShownAt: 0
    PanelWindow {
        id: workIsland
        readonly property bool modal: root.islandOpenedBy === "shortcut"
        screen: root.islandScreen || root.workScreen
        visible: root.islandOpen && !!root.islandScreen && !root.workCovered
        anchors { top: true; left: modal; right: modal; bottom: modal }
        implicitWidth: islandCard.width
        implicitHeight: islandCard.height + 6
        color: "transparent"
        exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.namespace: "flow-island"
        WlrLayershell.keyboardFocus: visible && root.islandOpenedBy === "shortcut" ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None
        onVisibleChanged: {
            root.islandShownAt = 0
            islandAway.entered = !modal
            if (visible && modal) islandFocus.forceActiveFocus()
        }
        Connections {
            target: islandCard.Window.window
            function onFrameSwapped() { if (workIsland.visible && !root.islandShownAt) root.islandShownAt = Date.now() }
            // The compositor gives the keyboard a moment after the surface maps: take focus then too.
            function onActiveChanged() { if (islandCard.Window.window.active && workIsland.modal) islandFocus.forceActiveFocus() }
        }
        Item {
            // The whole surface, from the top edge down: a pointer resting on rows 0 to 5, above the card, is on it.
            anchors.fill: parent
            HoverHandler { id: islandSurfaceHover }
        }
        Shortcut {
            // Escape closes Super+B's Island whatever item inside it has focus, as long as its window has the keyboard.
            sequence: "Escape"
            enabled: workIsland.visible && workIsland.modal
            onActivated: root.closeIsland()
        }
        MouseArea {
            // Opened by Super+B: a press beside the card closes the Island, and what it opened.
            enabled: workIsland.modal
            anchors.fill: parent
            onPressed: { root.notificationsOpen = false; root.quickSettingsOpen = false; root.closeIsland() }
        }
        Rectangle {
            id: islandCard
            x: Math.round((parent.width - width) / 2)
            y: 6
            width: Math.min(implicitWidth, (workIsland.screen ? workIsland.screen.width : 1280) - 16); height: implicitHeight
            HoverHandler { id: islandHover }
            implicitWidth: islandColumn.implicitWidth + 12; implicitHeight: islandColumn.implicitHeight
            radius: 22; color: floeTheme.canvas
            border.width: 1; border.color: Qt.alpha(floeTheme.border, 0.55)
            FocusScope {
                id: islandFocus
                anchors.fill: parent; focus: true
                Keys.onEscapePressed: root.closeIsland()
                Column {
                    id: islandColumn
                    anchors.horizontalCenter: parent.horizontalCenter
                    // Its system controls (as Desk's and Overview's islands), then the work: Overview and Desk,
                    // the space's place and the way along it, a Group's apps or a Layer's actions.
                    SystemIsland { id: workIslandContent; flat: true; anchors.horizontalCenter: parent.horizontalCenter; room: workIsland.screen ? workIsland.screen.width - 40 : 1240 }
                    Rectangle { width: parent.width - 24; height: 1; anchors.horizontalCenter: parent.horizontalCenter; color: Qt.alpha(floeTheme.border, 0.35) }
                    Item {
                        id: islandWork
                        anchors.horizontalCenter: parent.horizontalCenter
                        implicitWidth: islandWorkRow.implicitWidth; implicitHeight: islandWorkRow.implicitHeight
                        width: implicitWidth; height: implicitHeight
                        Row {
                            id: islandWorkRow
                            IslandButton {
                                id: islandOverview
                                implicitHeight: 44; text: "Overview"
                                Accessible.name: "Show Overview"
                                onClicked: root.toggleSpacesOverview()
                            }
                            IslandButton {
                                id: islandDesk
                                implicitHeight: 44; text: "Desk"
                                Accessible.name: "Open Floe Desk"
                                onClicked: root.toggleDesk()
                            }
                            Rectangle { visible: islandPosition.visible; width: 1; height: 20; anchors.verticalCenter: parent.verticalCenter; color: Qt.alpha(floeTheme.border, 0.5) }
                            Row {
                                // The space's place in this display's Flow, and the way along it.
                                id: islandPosition
                                visible: !!root.activeSpace && !root.activeLayer
                                IslandButton {
                                    width: 44; implicitHeight: 44; glyph: "left"; text: "‹"
                                    enabled: root.activeSpaceIndex > 0
                                    Accessible.name: "Previous space"
                                    onClicked: root.spaceAction("navigate", "left")
                                }
                                Text {
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: (root.activeSpaceIndex < 9 ? "0" : "") + (root.activeSpaceIndex + 1) + " / " + (root.displaySpaces.length < 10 ? "0" : "") + root.displaySpaces.length
                                    textFormat: Text.PlainText; color: floeTheme.textPrimary; font.pixelSize: 13; font.letterSpacing: 0.5
                                    font.family: floeTheme.interfaceFont; font.features: { "tnum": 1 }
                                    Accessible.name: "Space " + (root.activeSpaceIndex + 1) + " of " + root.displaySpaces.length
                                }
                                IslandButton {
                                    width: 44; implicitHeight: 44; glyph: "right"; text: "›"
                                    enabled: root.activeSpaceIndex >= 0 && root.activeSpaceIndex < root.displaySpaces.length - 1
                                    Accessible.name: "Next space"
                                    onClicked: root.spaceAction("navigate", "right")
                                }
                            }
                            Rectangle { visible: islandGroup.visible || islandLayer.visible; width: 1; height: 20; anchors.verticalCenter: parent.verticalCenter; color: Qt.alpha(floeTheme.border, 0.5) }
                            Row {
                                // A Group's apps: the focused one selected, a click focuses another.
                                id: islandGroup
                                visible: !root.activeLayer && !!(root.activeSpace && root.activeSpace.group_id)
                                spacing: 2
                                SectionLabel { anchors.verticalCenter: parent.verticalCenter; leftPadding: 10; rightPadding: 4; text: "GROUP" }
                                Repeater {
                                    model: workIsland.visible && islandGroup.visible ? root.activeSpace.members : []
                                    IslandButton {
                                        required property var modelData
                                        width: Math.min(148, Math.max(64, implicitWidth)); implicitHeight: 44
                                        selected: modelData.active
                                        text: (modelData.app_name || modelData.label).slice(0, 16)
                                        Accessible.name: (modelData.active ? "Focused: " : "Focus ") + (modelData.app_name || modelData.label) + " in Group " + root.activeSpace.label
                                        onClicked: if (!modelData.active) root.spaceAction("focus-window", String(modelData.id))
                                    }
                                }
                            }
                            Row {
                                // A Layer's actions.
                                id: islandLayer
                                visible: !!root.activeLayer
                                spacing: 4
                                SectionLabel { anchors.verticalCenter: parent.verticalCenter; leftPadding: 10; rightPadding: 4; text: "LAYER" }
                                Text {
                                    anchors.verticalCenter: parent.verticalCenter; rightPadding: 6
                                    width: Math.min(implicitWidth, 160); elide: Text.ElideRight
                                    text: root.activeLayer ? (root.activeLayer.app_name || root.activeLayer.label).slice(0, 24) : ""
                                    textFormat: Text.PlainText; color: floeTheme.textPrimary; font.weight: Font.DemiBold; font.pixelSize: 13; font.family: floeTheme.interfaceFont
                                }
                                IslandButton {
                                    implicitHeight: 44; text: "Make space"
                                    Accessible.name: "Make space: this Layer becomes a space of its own"
                                    onClicked: root.spaceAction("layer-to-space", String(root.activeLayer.id))
                                }
                                IslandButton {
                                    implicitHeight: 44; variant: "danger"; text: "Close"
                                    Accessible.name: "Close this Layer; the app is asked to close and the space under it stays as it is"
                                    onClicked: root.spaceAction("close-layer", String(root.activeLayer.id))
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    // Opened by Super+B, the Island holds the keyboard: a press on any other screen closes it too.
    Variants {
        id: islandCatchers
        model: Quickshell.screens
        PanelWindow {
            required property var modelData
            screen: modelData
            visible: workIsland.visible && workIsland.modal && modelData.name !== root.islandScreenName
            anchors { top: true; bottom: true; left: true; right: true }
            color: "transparent"; exclusionMode: ExclusionMode.Ignore
            WlrLayershell.layer: WlrLayer.Overlay
            WlrLayershell.namespace: "flow-island-catcher"
            WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
            MouseArea {
                anchors.fill: parent
                onPressed: { root.notificationsOpen = false; root.quickSettingsOpen = false; root.closeIsland() }
            }
        }
    }
    Timer {
        id: islandAway; interval: 100; repeat: true
        running: root.islandOpen && !root.islandHeld
        property int away: 0
        property bool entered: true
        onRunningChanged: away = 0
        onTriggered: {
            // Opened by Super+B its surface is the whole screen: only the card counts then.
            const here = (workIsland.modal ? islandHover.hovered : islandSurfaceHover.hovered) || appMenuButton.hovered
                || (!!root.islandScreenName && (root.edgeHoveredScreen === root.islandScreenName || root.pillHoveredScreen === root.islandScreenName))
            if (here) { away = 0; entered = true; return }
            if (!entered) return
            if (++away >= 5) root.closeIsland()
        }
    }
    // The clock pill (Settings: off by default, the main display only, or every display): small, 4 px below the top
    // edge of a plain screen, taking clicks only on itself, which open the Island there. A screen with a band shows
    // the clock in it instead.
    Variants {
        id: clockPills
        model: Quickshell.screens
        PanelWindow {
            id: clockPill
            required property var modelData
            screen: modelData
            visible: root.pillOn(modelData) && root.restingView && !root.workCovered
                && !(root.islandOpen && root.islandScreenName === modelData.name)
            anchors.top: true
            margins.top: 4
            implicitWidth: pillLabel.width + 20; implicitHeight: 22
            color: "transparent"; exclusionMode: ExclusionMode.Ignore
            WlrLayershell.layer: WlrLayer.Overlay
            WlrLayershell.namespace: "flow-clock-pill"
            WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
            Rectangle {
                anchors.fill: parent; radius: height / 2
                color: Qt.alpha(floeTheme.canvas, 0.9); border.width: 1
                border.color: pillArea.containsMouse ? Qt.alpha(floeTheme.accent, 0.7) : Qt.alpha(floeTheme.border, 0.5)
                Text {
                    // The time, and the newest activity's owner and progress while one runs.
                    id: pillLabel
                    anchors.centerIn: parent
                    readonly property var activity: root.activities.length ? root.activities[0] : null
                    width: Math.min(implicitWidth, 220); elide: Text.ElideRight
                    text: Qt.formatTime(clock.date, "hh:mm") + (activity ? "  ·  " + activity.owner
                        + (activity.progress >= 0 ? " " + Math.round(activity.progress * 100) + "%" : " …") : "")
                    textFormat: Text.PlainText; color: floeTheme.textPrimary
                    font.pixelSize: 12; font.weight: Font.Medium; font.family: floeTheme.interfaceFont; font.features: { "tnum": 1 }
                }
            }
            MouseArea {
                id: pillArea
                anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                onContainsMouseChanged: {
                    if (containsMouse) root.pillHoveredScreen = clockPill.modelData.name
                    else if (root.pillHoveredScreen === clockPill.modelData.name) root.pillHoveredScreen = ""
                }
                onClicked: root.openIsland(clockPill.modelData.name, "pill")
                Accessible.role: Accessible.Button
                Accessible.name: pillLabel.text + ": open the Island"
            }
        }
    }
    // The band of a notched MacBook (ADR-0014, spec §7.3): the rows beside the camera, black like the cutout, kept from
    // windows by an exclusive zone, so apps get the rectangle Asahi gives them by default. The Island lives here: Desk
    // and Apps at the left (after the App button, when it shows), the space's place and an activity left of the
    // camera, the clock, notifications and quick settings right of it. In the guest there is no hole, so a simulated
    // band draws the cutout's outline and lens.
    Variants {
        id: bands
        model: Quickshell.screens
        PanelWindow {
            id: band
            required property var modelData
            readonly property var geometry: root.bandFor(modelData)
            readonly property real h: geometry ? geometry.height : 1
            readonly property real side: geometry ? geometry.cutout / 2 + 10 : 0
            screen: modelData
            visible: !!geometry
            anchors { top: true; left: true; right: true }
            implicitHeight: h
            exclusiveZone: geometry ? geometry.height : 0
            color: floeTheme.bandFill
            WlrLayershell.layer: WlrLayer.Top
            WlrLayershell.namespace: "flow-band"
            WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
            Item {
                // The simulated cutout: square at the panel's edge, rounded below.
                visible: !!band.geometry && band.geometry.simulated
                anchors.horizontalCenter: parent.horizontalCenter
                width: band.geometry ? band.geometry.cutout : 0; height: band.h
                Rectangle {
                    anchors.fill: parent; color: floeTheme.bandFill
                    topLeftRadius: 0; topRightRadius: 0
                    bottomLeftRadius: band.geometry ? band.geometry.radius : 0; bottomRightRadius: band.geometry ? band.geometry.radius : 0
                    border.width: 1; border.color: floeTheme.bandCutoutEdge
                }
                Rectangle {
                    anchors.centerIn: parent
                    width: Math.max(6, Math.round(band.h * 0.22)); height: width; radius: width / 2
                    color: floeTheme.bandLens; border.width: 1; border.color: floeTheme.bandLensEdge
                }
            }
            Row {
                anchors { left: parent.left; leftMargin: appMenuTrigger.visible && appMenuTrigger.screen === band.modelData ? appMenuTrigger.implicitWidth + 16 : 6 }
                height: band.h
                BandButton {
                    band: band.h; text: "floe."; open: root.deskOpen
                    Accessible.name: root.deskOpen ? "Floe: return to work" : "Open Floe Desk"
                    onClicked: root.toggleDesk()
                }
                BandButton {
                    band: band.h; text: root.searchOpen ? "Close" : "Apps"; open: root.searchOpen
                    Accessible.name: root.searchOpen ? "Close search and apps" : "Open apps"
                    onClicked: { if (root.searchOpen) { root.closeLauncher(); root.closeSearch() } else root.openApps() }
                }
                BandButton {
                    band: band.h; text: "Overview"; open: root.spacesOverviewOpen
                    Accessible.name: "Show Overview"
                    onClicked: root.toggleSpacesOverview()
                }
            }
            Row {
                anchors { right: parent.horizontalCenter; rightMargin: band.side }
                height: band.h; spacing: 4
                Repeater {
                    // A Group's apps, the focused one marked; a Layer's actions.
                    model: band.visible && !root.activeLayer && root.activeSpace && root.activeSpace.group_id ? root.activeSpace.members : []
                    BandButton {
                        required property var modelData
                        band: band.h; open: modelData.active
                        text: (modelData.app_name || modelData.label).slice(0, 14)
                        Accessible.name: (modelData.active ? "Focused: " : "Focus ") + (modelData.app_name || modelData.label) + " in Group " + root.activeSpace.label
                        onClicked: if (!modelData.active) root.spaceAction("focus-window", String(modelData.id))
                    }
                }
                BandButton {
                    visible: !!root.activeLayer; band: band.h; text: "Make space"
                    Accessible.name: "Make space: this Layer becomes a space of its own"
                    onClicked: root.spaceAction("layer-to-space", String(root.activeLayer.id))
                }
                BandButton {
                    visible: !!root.activeLayer; band: band.h; text: "Close"
                    Accessible.name: "Close this Layer; the app is asked to close and the space under it stays as it is"
                    onClicked: root.spaceAction("close-layer", String(root.activeLayer.id))
                }
                Text {
                    // The newest activity, short: who it is from and its progress.
                    visible: root.activities.length > 0
                    anchors.verticalCenter: parent.verticalCenter; rightPadding: 6
                    width: Math.min(implicitWidth, Math.max(0, band.width / 2 - band.side - 180)); elide: Text.ElideRight
                    text: root.activities.length ? root.activityText(root.activities[0]) : ""; textFormat: Text.PlainText
                    color: floeTheme.bandText; font.pixelSize: Math.max(12, Math.round(band.h * 0.38)); font.family: floeTheme.interfaceFont; font.features: { "tnum": 1 }
                }
                BandButton {
                    visible: !!root.activeSpace && !root.activeLayer; band: band.h; glyph: "left"; text: "‹"
                    enabled: root.activeSpaceIndex > 0; Accessible.name: "Previous space"
                    onClicked: root.spaceAction("navigate", "left")
                }
                Text {
                    visible: !!root.activeSpace && !root.activeLayer
                    anchors.verticalCenter: parent.verticalCenter
                    text: (root.activeSpaceIndex < 9 ? "0" : "") + (root.activeSpaceIndex + 1) + " / " + (root.displaySpaces.length < 10 ? "0" : "") + root.displaySpaces.length
                    textFormat: Text.PlainText; color: floeTheme.bandMuted; font.pixelSize: Math.max(12, Math.round(band.h * 0.38))
                    font.family: floeTheme.interfaceFont; font.features: { "tnum": 1 }
                    Accessible.name: "Space " + (root.activeSpaceIndex + 1) + " of " + root.displaySpaces.length
                }
                BandButton {
                    visible: !!root.activeSpace && !root.activeLayer; band: band.h; glyph: "right"; text: "›"
                    enabled: root.activeSpaceIndex >= 0 && root.activeSpaceIndex < root.displaySpaces.length - 1
                    Accessible.name: "Next space"
                    onClicked: root.spaceAction("navigate", "right")
                }
            }
            Row {
                anchors { left: parent.horizontalCenter; leftMargin: band.side }
                height: band.h; spacing: 2
                Text {
                    anchors.verticalCenter: parent.verticalCenter; rightPadding: 6
                    text: Qt.formatTime(clock.date, "hh:mm"); textFormat: Text.PlainText; color: floeTheme.bandText
                    font.pixelSize: Math.max(12, Math.round(band.h * 0.42)); font.weight: Font.Medium
                    font.family: floeTheme.interfaceFont; font.features: { "tnum": 1 }
                }
                BandButton {
                    id: bandBell
                    band: band.h; glyph: "bell"; open: root.notificationsOpen
                    Accessible.name: notificationServer.trackedNotifications.values.length
                        ? "Notifications, " + notificationServer.trackedNotifications.values.length + " waiting" : "Notifications"
                    onClicked: root.toggleNotifications()
                    Rectangle {
                        visible: notificationServer.trackedNotifications.values.length > 0
                        x: parent.width / 2 + 3; y: Math.max(2, band.h * 0.18); width: 7; height: 7; radius: 3.5
                        color: floeTheme.bandFocus; border.width: 1.5; border.color: floeTheme.bandFill
                    }
                }
                BandButton {
                    band: band.h; glyph: "sliders"; open: root.quickSettingsOpen; Accessible.name: "Quick settings"
                    onClicked: root.toggleQuickSettings()
                }
            }
        }
    }
    // Panels and the app menu go where the opened Island is, else where Niri's focus is.
    readonly property var panelScreen: islandOpen && islandScreen ? islandScreen : workScreen
    readonly property real panelTop: !bandFor(panelScreen) && islandOpen ? islandCard.height + 12 : topInset(panelScreen)
    // Below the band on a notched screen, else near the top: where panels and the banner hang.
    function topInset(screen) {
        const geometry = bandFor(screen)
        return geometry ? geometry.height + 8 : 8
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
