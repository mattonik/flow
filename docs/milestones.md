# Milestones

- **0 — graphics gate: single-display smoke test passed.** See [graphics validation](graphics-validation.md) for the VirtIO renderer, direct Niri session and remaining performance limitations.
- **1 — session foundation: partially verified.** Flake and NixOS module, Niri fullscreen-width columns, real app picker and terminal, minimal Quickshell capsule, and an evaluation fixture. Build, login, logout, manual and system-requested locking, shell startup, launcher rendering and focus via IPC have been exercised. Verify the remaining hardware shortcuts, island click, suspend/resume and fullscreen overlay before calling this complete.
- **1a — v0.9 Desk: in progress.** A Quickshell Desk overlays the existing Niri session, with app search, files, overview and return controls. Work and Niri overview remain native compositor views. Confirm keyboard and pointer behavior on the installed guest; spatial transitions and touch gestures remain future work.
- **2 — pinned spaces: experimental single-display slice.** Per-user metadata and create, unpin, reorder and focus controls exist for single-window apps; Niri IPC maintains open pins at the left of the focused workspace. Test compositor restart and additional physical shortcuts. Absent apps have no reserved compositor position or automatic launch, duplicate app windows cannot be unambiguously restored, and Groups and multiple displays remain unsupported.
- **3 — minimum system surface: in progress.** The Desk, island, output audio controls, network status, connection-profile editor, notification inbox and lock integration have initial guest checks. Wi-Fi selection, secure notification behavior, and comprehensive keyboard and touch validation remain open.
- **4 — product MVP and device maturity: not started.** Follow Stage B onward in the brief: Groups, display lifecycle, touch/rotation, accessibility, Flatpak/XWayland, and restoration. Track Sync, DisplayLink and iPad hardware separately; no compatibility is implied by this repository.

The prototype includes many future features; none are implemented merely because they are depicted there.

## v0.9 VM check (2026-11-11)

On the existing ARM64 UTM/NixOS guest, the updated system configuration built and activated, `niri validate` accepted the generated configuration, and Quickshell loaded the installed QML. The Desk was displayed through Quickshell IPC; two Foot windows and a Nautilus window remained visible to Niri IPC after changing views. This is a smoke test, not a complete interaction or accessibility test. Keyboard shortcuts, pointer controls, touch, small displays, session restart and animation still need end-to-end verification. An initial keyboard navigation path has since been added; full accessibility and touch coverage remain open.

## Desk interaction check (2026-11-11)

In the UTM guest, Tab showed the focus ring on a Desk control and Enter activated the Files button. Calling the app-search action while Desk was open hid Desk and displayed Fuzzel. The content can scroll on shorter screens, but portrait, touch and screen-reader behavior still need testing. Pin identity needs to handle duplicate application windows and compositor restarts before it can provide the persistent, ordered spaces described in the brief.

## Pinned-space VM check (2026-11-11)

On the ARM64 guest, pinned a Nautilus window and a Foot window, reordered both using the service, and checked Niri window positions and retained focus. The Desk displayed saved pins, and its numbered focus IPC returned from Desk to Nautilus. The guest rebuilt successfully and the Nix test check passed. Automated tests cover single-window eligibility, duplicate-app ambiguity, ordering and unpin behavior. `Super+1` also focused the first pinned terminal through the guest keyboard. Full compositor restart, lock-screen interactions, other physical shortcuts, multiple displays and touch remain unverified.

## App launcher recovery (2026-11-11)

A manually restarted shell inherited an incomplete `XDG_DATA_DIRS`, leaving Fuzzel's application list empty. The shell was restarted using the graphical user manager's environment, and the launcher now adds the standard NixOS profile paths while preserving any supplied data paths. In the VM, Fuzzel displayed installed apps even with an intentionally invalid inherited data path; typing `firefox` and pressing Enter launched Firefox. Prompt and input colors were also set explicitly for legibility against the dark background.

## v1.0 experimental application menu (2026-11-11)

The v1.0 brief and prototype are included as references. The opt-in Desk setting persists across shell restarts, and the top-left menu reads the focused application's AT-SPI menu and invokes exposed actions. On the ARM64 UTM guest, Kate's File and Edit menus were displayed and its New command created a document through Flow; the system build and automated checks passed. Applications without compatible menus and multiple windows sharing one process use the fallback. The prototype's desktop category bar, searchable app commands, Groups, touch validation, and full session restoration remain future work.

## Bottom switcher (2026-11-11)

The bottom handle and Super+Tab open a scrollable list of real windows on the active workspace; selecting a window requests focus and Escape dismisses the list. The ARM64 UTM guest displayed its running applications. The list uses titles rather than live previews. Restart restoration, gesture navigation, touch, cross-workspace and multi-display behavior remain unfinished.

## Experimental session recovery (2026-11-11)

Desk now offers explicit save and restore of the active workspace's app list, order and focus. On the ARM64 guest, saving four real windows and restoring the already-running unique applications worked, with duplicate terminals reported; a separate test relaunched KWrite from its exact desktop entry. Ten Python tests passed. A full VM restart, document recovery, duplicate windows, multiple workspaces and displays remain unverified or unsupported; snapshots never replace application document saves.

## Search across apps and open work (2026-11-11)

Super+K and the Desk search use one picker for installed applications, available pinned spaces and open windows on the current workspace. In the ARM64 guest, filtering Kate returned both the app and its existing window, selecting the window focused it, and selecting Ark launched the app. The system build and eleven Python tests passed. Files, app commands, settings, unavailable pins, other workspaces and multiple displays are not searched yet.

## Performance check (2026-11-11)

A lightweight UTM guest sample is recorded in [performance observations](performance.md). Focused-app identity no longer eagerly traverses accessibility menus: the measured identity request took 41–55 ms instead of roughly 0.9 seconds for a full menu traversal. Reading the full menu is still on demand and remains slow. Frame pacing and physical device performance have not been measured.

## Two-window stacking (2026-11-11)

The bottom switcher can combine the focused window and the next window into a Niri column, and separate a two-window column again. It rejects pinned, floating or ambiguous windows and supports Super+Up/Down for switching focus within the column. In the ARM64 guest, KWrite and Kate formed a two-window column and were separated back into their original columns. Fourteen Python tests passed. This is a reversible portrait-style building block, not yet a named, persistent or orientation-adaptive Flow Group; pins, session recovery and multi-display Groups remain unsupported.

## Group-aware switcher (2026-11-11)

The switcher now displays each Niri column as a single space, with individual focus controls for members of a stacked column. In the ARM64 guest, the stacked KWrite and Kate windows appeared together in one card. Search continues to find individual windows. Group naming, persistence, side-by-side layout and adaptive orientation are still future work.

## Navigation reliability (2026-11-11)

The switcher, Desk and Niri overview now close competing Flow surfaces when switching views. User actions arriving during a spaces refresh are queued, and pending app-menu requests are discarded after a focus or view change. On the ARM64 guest, keyboard navigation focused Kate within a stack, Desk opened without the switcher, and Kate's app menu opened after returning. The queue has not been stress-tested under prolonged compositor delays.

## Stack recovery and first OS-backed controls (2026-11-11)

Session snapshots record the tiled column of each window. Recovery preserves an already-matching two-window stack or rejoins a pair only when both are uniquely identified, unpinned and adjacent in the saved order; it reports other cases and preserves current column ordering when stacks are present. In the ARM64 guest, KWrite and Kate were saved as a stack, separated and rejoined by recovery. A full reboot has not yet been tested, and this does not restore unsaved documents or provide named/adaptive Groups.

## Cold-boot recovery and stack ordering (2026-11-11)

After saving five windows, an attempted warm reboot hung in UTM; a forced stop and cold boot reached Niri. Recovery relaunched Nautilus, KWrite and Kate and re-created the KWrite/Kate stack with the saved focus. The two terminal windows shared an app ID and were reported for manual recovery. Recovery now also moves uniquely identified, unpinned stack members together when they start in different positions, verifying the result before stacking; this ordering change is covered by unit tests, not yet tested in the guest. Adaptive landscape/portrait Groups, complete app state, automatic restore and a reliable warm reboot remain open work.

## Reopened pins and stack ordering (2026-11-11)

A pinned app that closes and reopens now rebinds to its single matching window in the same session; several matches remain unavailable until resolved. On the ARM64 guest, an isolated pin state reattached to Nautilus, and recovery rearranged a reversed Kate/KWrite pair and rebuilt its stack. The fixes were installed in a new NixOS generation, and the guest's Flow input lock was updated. Nineteen Python tests pass. Automatic app relaunch from a missing pin and adaptive Groups remain future work.

## Opening missing pinned apps (2026-11-11)

Selecting a missing pin launches its exact installed desktop entry and focuses the resulting unique window. Existing ambiguous windows, missing desktop entries and windows on other workspaces are reported instead of launching duplicates or moving unrelated windows. In the ARM64 guest, an isolated test pin launched and focused a real Foot terminal. This does not restore documents or app state; adaptive Groups remain future work.

The system capsule's quick settings expose NetworkManager's connectivity status and WirePlumber's default-output volume and mute controls, when those services are available.

## Quick settings interaction (2026-11-11)

In the ARM64 guest, keyboard navigation increased the real output volume from 40% to 45%, then returned it to 40%; Escape closed the panel. Quick settings now closes competing Flow overlays when opened, and is dismissed when opening the Desk, switcher, launcher or app menu.

## Lock integration (2026-11-11)

The VM's Super+L shortcut locked the session; a wrong password left it locked, and the correct password restored the open apps. Previously, a logind lock request did not lock Flow. Niri now starts swayidle to handle system lock and before-sleep requests, and Super+L remains available if the locker crashes. A running instance handled a logind lock request and unlocked successfully. The generated Niri configuration validated. Suspend/resume, locker crash recovery and logout have not been exercised. The previous failed warm reboot's journal reached system shutdown, without identifying the cause of the failure after that point.

## Shell identity across updates (2026-11-11)

After a NixOS rebuild, shortcuts targeted the new shell store path while the running shell still used the previous path. The NixOS module now installs the shell at a stable `/etc/flow/shell.qml` path for both startup and IPC. The guest built and activated the change; after restarting only the shell, Super+H opened the Desk. IPC remained available when the symlink was temporarily pointed at a different file. A full second rebuild was blocked by inode exhaustion, despite available disk capacity; garbage collection recovered unused store paths but left the filesystem at 99% inode use. No rollback generations were removed.

## VM inode recovery and rebuild check (2026-11-11)

The guest had 7.5 GiB free but 99% inode use, mostly in the Nix store. `nix-store --optimise` deduplicated store files without deleting rollback generations, reducing inode use substantially. A NixOS rebuild with a temporary shell configuration change then succeeded, and the running shell accepted its Desk IPC command after the symlink's store target changed. A second rebuild restored the original configuration and the guest's input lock was updated. This verifies shortcut routing across a rebuild; it does not establish that every QML change is hot-reloaded.

## Event-driven space updates (2026-11-11)

The shell refreshes spaces after relevant Niri events, coalescing bursts and retaining a polling fallback. It retries the event stream after disconnection. The ARM64 guest displayed the open apps and stack, refreshed after focus changed, and restarted its event stream after it was deliberately stopped. See [performance observations](performance.md) for the limited idle-process measurement.

## Logout, sign-in and reboot retest (2026-11-11)

On the ARM64 guest, Niri's logout confirmation returned to SDDM, and signing back in started Flow. Desk recovery reopened Nautilus, KWrite, Kate and Foot, retaining the pinned Nautilus position, KWrite/Kate stack and selected Kate window. A subsequent warm reboot again hung on a black screen with QEMU using high host CPU. After a forced stop and cold start, Desk recovery reopened all four windows again. The previous snapshot was backed up before the test. After installing the startup overlay change, another logout and login started Flow without the hotkey overlay, and Super+Shift+/ opened it on demand. Warm reboot reliability remains unresolved.

## Session-scoped window identities (2026-11-11)

New snapshots record window IDs and the compositor session identity. Recovery can reorder and focus multiple still-open windows of one app only while that identity matches; after a compositor restart it still reports duplicates for manual recovery. Existing snapshots remain readable. An isolated ARM64 guest test matched two real Foot windows in the current session and refused to match them with a different session identity. This does not recover closed duplicate windows, terminal processes or documents.

## Recovery workspace safety (2026-11-11)

Recovery stops when the focused workspace changes while an app is launching or before a layout action, without returning focus to the original workspace. Already launched apps are left open. Automated tests cover changes during launch and Group reconstruction; an ARM64 guest test switched workspaces while recovery was waiting and confirmed no recovery actions were sent. Niri IPC calls are separate operations, so a workspace change between individual calls cannot be made atomic by this check.

## Saved Group identity (2026-11-11)

An explicit session save assigns stable IDs and editable names to unambiguous two-app stacks, retaining them when the same members are saved in the opposite order. The switcher shows and renames the saved Group, and recovery reconstructs its stack using the existing safe matching rules. On the ARM64 guest, a real KWrite/Kate stack was saved, separated and recovered; its ID and name survived another save. Renaming it to Writing through the switcher updated the snapshot. Groups are still tied to the latest explicit snapshot: saving while members are missing drops their Group metadata. Pinned Groups, independent Group persistence and adaptive landscape layouts remain open.

## Preserve missing Group members (2026-11-11)

Later session saves retain missing Group members as recovery entries, including when both members are absent. Ambiguous matches, members on another workspace, or a surviving member in another stack stop the save and leave the previous snapshot intact. Automated tests cover absent and ambiguous members; an isolated ARM64 guest test used real compositor windows to verify that omitting one member retains its identity and recovery entry. Restoring still depends on installed application launchers and does not restore document contents. Adaptive layouts and pinned Groups remain open.

## Desk and live overview for demos (2026-11-11)

The Desk puts search, pins and tools ahead of settings; pin management and session recovery remain available on demand. The bottom switcher counts open spaces and links directly to Niri's live overview. On the ARM64 guest, the main Desk fit on screen, keyboard navigation reached session recovery, and the switcher's overview control opened the real application surfaces. The guest rebuild and 35 automated tests passed. Touch and small portrait displays remain unverified.

## Application names in the menu (2026-11-12)

The experimental app menu now uses installed desktop-entry names when available, falling back to the app ID or window title. On the ARM64 guest the menu button displayed Kate instead of its desktop ID, and Kate's exposed commands remained available. The guest rebuild and 36 automated tests passed; app naming and menus still depend on application metadata and accessibility support.

## Overview appearance (2026-11-12)

Niri's live overview now uses the same dark backdrop as the Flow Desk. The generated configuration validated, the guest system rebuilt, and the active overview showed the updated backdrop around real application windows.

## Contextual menu focus safety (2026-11-12)

When Niri reports a different focused window or the menu's window closes, an open application menu keeps its existing context and disables commands until dismissed. The backend rejects invocation if its saved window is no longer active. The ARM64 UTM guest built and activated the updated NixOS configuration, and 37 local Python tests passed including a focus-mismatch guard. An interactive real-window test remains open: the guest's SDDM greeter displayed only its background after startup, so no session was signed in for this test.

## Group menu focus retest (2026-11-13)

After the user restarted and signed into the ARM64 UTM guest, KWrite and Kate were opened and stacked using `flow-spaces stack`. With KWrite's menu open, `niri msg action focus-window --id 3` changed the active member to Kate. Niri emitted `WorkspaceActiveWindowChanged` while the shell's exclusive layer had keyboard focus; the original implementation only handled `WindowFocusChanged` and failed to disable the menu. The app-menu context now includes its workspace ID and handles that workspace event. After rebuilding and restarting the shell, the same focus change kept KWrite's menu visible, displayed the warning and left only Close menu keyboard-focusable. Closing updated the label to Kate. Kate's File → New created an untitled Kate document; `flow-appmenu activate 2 '[0]' New` while Kate was active returned `Application focus changed` with exit status 1. Super+Up also changed the label back to KWrite. `python3 -B -m unittest discover -s tests` passed 37 tests, and the guest build and input-lock update passed. Pointer focus, closure, duplicate-process windows and other toolkits remain unverified.


## Search settings in the ARM64 guest (2026-11-13)

Flow settings and quick settings are discoverable in search. The guest configuration built and activated, and the restarted Quickshell accepted both new actions through IPC. The search results and dispatch are covered by the local Python tests. Selecting each result in the guest UI still needs verification.


## Keyboard navigation in the bottom switcher (2026-11-13)

The bottom switcher scrolls horizontally to keep the focused space, Group member or Group control visible. The ARM64 VM build succeeded; with ten real windows open, tabbing beyond the initially visible cards scrolled to the focused card. Temporary test windows were closed afterwards. Group controls and touch scrolling still need interactive verification.


## Switcher overflow controls (2026-11-13)

The bottom switcher shows earlier and later page buttons when its space list overflows. The ARM64 VM build activated and the shell accepted the switcher command. The host locked before the new buttons could be exercised visually; mouse, touch and keyboard interaction still need a live check.


## Recent files in search (2026-11-13)

Search can list readable, existing recent files under the current user's home using their local recent-file history, and opens the selected file with its default application. It skips non-local, missing and outside-home targets and limits work to a small recent list. The ARM64 VM build activated and the search tests passed in the guest. The guest had no recent-file history at the time, and the host was locked, so an end-to-end UI check remains open. This is not a full file index.


## Switcher and recent-file search checks (2026-11-13)

In the ARM64 VM, a ten-space switcher showed its paging controls; keyboard activation of the later-spaces control moved to the end and updated the arrows' enabled states. A temporary local document and isolated recent-file history appeared in search, and selecting the result opened its contents in Kate. Temporary test windows and files were removed. Reverse paging by mouse, touch and naturally populated recent-file history remain unverified.


## Reopen pinned spaces from search (2026-11-13)

Search now includes closed pins and sends pin and window focus requests through the shell, which displays action errors in the Desk. In the ARM64 VM, selecting the existing Home pin focused Files; a temporary terminal pin remained searchable after its window closed and selecting it reopened the terminal. A request for a nonexistent pin displayed an error. The temporary pin and terminal were removed.


## Plain-text shell labels and project license (2026-11-13)

The shared Flow button text and dynamic error and application-name labels explicitly render plain text. The intentionally styled Desk heading remains rich text. In the ARM64 guest a real window with a markup-shaped title appeared literally in the bottom switcher; the NixOS configuration built and activated. Regression checks cover the protected renderers. The user selected the MIT License for Flow; third-party software and assets keep their own licenses. Other app-menu interactions and touch were not retested.


## Live output audio via PipeWire (2026-11-13)

Quick Settings now reads and updates the default audio sink through Quickshell's tracked PipeWire service instead of polling `wpctl`; network polling remains unchanged. A read-only Quickshell probe passed in the ARM64 guest. The NixOS build activated and the shell restarted without closing apps. An external 30% → 40% change appeared in the panel; keyboard +5% and mute gave 45% muted, confirmed by `wpctl`. The original 30% unmuted state was restored. Mouse, −5%, missing/reconnected devices, physical touch and power/performance are not yet tested. See [ADR 0001](engineering/ADR-0001-audio-service.md).


## Coordinated service prototypes (2026-11-13)

A Quickshell notification inbox prototype is built into the guest session and received a test D-Bus notification; it only retains up to 20 active notifications in memory and has no automatic alerts, persistent history or DND. The guest build succeeded, but the host was locked before visual interaction and lock-screen checks could be completed. A separate read-only Quickshell Networking probe observed the NetworkManager backend, full connectivity and two connected wired devices in the ARM64 guest; the existing network UI was not replaced. A standalone Niri event-state projection passed unit tests and matched initial guest window/workspace IDs and focus against separate snapshots; it is not yet connected to Flow's runtime. Touch, network changes and restart/reconnect are unverified. See ADRs 0002–0004.


## Notification inbox interaction (2026-11-13)

On the ARM64 guest, a test notification appeared in the inbox. The inbox was opened from the Desk with the keyboard; Tab reached the dismiss control and Enter removed the notification. Escape closed the inbox.

Follow-up: activating a notification action emitted `ActionInvoked` on D-Bus. The inbox now uses Quickshell's reactive notification model and defers its size limit until the server inserts each notification. A guest test sent 21 notifications and observed the oldest being closed before the remaining test notifications were cleaned up. The empty inbox displayed “No notifications” and closed with Escape on the ARM64 guest. With a synthetic notification visible, a logind lock request covered the inbox; unlocking returned it, and the test notification was dismissed. Expiry, multi-display lock behavior and touch remain open.

## Connection profiles and portal availability (2026-11-13)

Quick Settings now opens NetworkManager's existing connection editor. In the ARM64 NixOS/Niri guest, keyboard activation opened it and displayed the two existing wired profiles; the test made no connection changes. A guest rebuild activated the package and shell, and the portal broker was active and owned its session D-Bus name. This does not verify Wi-Fi selection, profile edits, touch, file chooser or screen sharing. See ADRs 0003 and 0005.

Pin reconciliation now skips unavailable or ineligible pins while maintaining the order of eligible pins on the current workspace. Unit tests cover an off-workspace pin and the case where no pins are eligible. In the ARM64 guest, an isolated live test kept an existing pin on its original workspace, moved an eligible temporary window to the first column of another workspace, preserved focus, and closed the temporary windows. It does not add cross-workspace or Group pinning.

Opening Niri's overview externally now dismisses an open Desk or notification inbox. Both transitions were checked visually in the ARM64 guest after a rebuild and shell restart; the existing application windows remained open. This does not validate touch gestures or all overlay combinations.

A disposable Gio client called the guest's file chooser portal. The Nautilus chooser returned the exact test-file URI after keyboard selection, and a separate request returned a cancel response after Escape. This was an unsandboxed client; sandbox access, file reading, save, screen sharing and touch remain untested. See ADR 0005.

Space errors now remain visible until a subsequent successful action or explicit dismissal. The Desk and bottom switcher both provide a keyboard-accessible Dismiss control; in the ARM64 guest, an invalid pin shortcut displayed the message and each control cleared it with Tab and Enter. The switcher retained its visible space cards while the error was shown. Touch and small-screen layouts remain unverified.

Quick Settings now uses Quickshell's MPRIS integration for a single unambiguous controllable media player. In the ARM64 guest, a temporary mpv player displayed its track and responded to keyboard pause and play; its D-Bus state confirmed both changes. Two simultaneous players hid the control until the choice was unambiguous again. Firefox did not expose MPRIS in the local silent-audio test. See ADR 0006 for scope and remaining tests.

A store-only wvkbd probe displayed an on-screen keyboard under a disposable Foot window in the ARM64 guest. Input delivery, accessible sizing, physical touch and compositor shortcuts are not yet verified; the keyboard is not integrated into Flow. The pinned Niri version predates an upstream virtual-keyboard shortcut fix. See ADR 0007.

Saved two-app Groups now retain their name and identity when their members change order in a stack. Unit tests cover reversed order and ambiguous matches. In the ARM64 guest, a real Group retained its identity after its members were reversed; the original member order and focus were restored after the check. This does not add adaptive or pinned Groups.

## Mixed Group recovery ordering (2026-11-13)

Recovery can order a saved mix of single windows and two-app Groups after reconstructing the Groups. It skips the ordering pass and reports manual recovery when saved entries are incomplete, ambiguous or invalid, a Group cannot be reconstructed, or a pinned app is present on the workspace. In the ARM64 guest, four disposable Foot windows were rearranged into a single window, a two-window Group and another single window in saved order, with saved focus restored; the test windows were then closed. Automated tests cover incomplete and ambiguous cases, pins and workspace changes. This does not establish reboot restoration or recover application documents.

## Desk and Overview continuity (2026-11-13)

In the ARM64 guest, opening and closing Desk and Niri Overview left four existing window IDs, their workspace and scrolling positions, and the focused window unchanged. The test observed Desk's exclusive keyboard layer and Niri's open Overview state before closing each surface. This checks single-display IPC transitions, not touch gestures or animation quality.

The same guest also focused the existing Firefox, Kate and Files windows in turn through Niri IPC, moved right from Files to Firefox and left back to Files, and restored the original focus. This checks real application focus and Niri's horizontal actions, not the full touch-navigation journey.

After temporary media-test packages brought the guest to 97% inode usage, `nix-store --optimise` completed successfully and reduced usage to 31% by hard-linking duplicate store files. No system generations were removed.

## Manual keyboard, Wi-Fi settings and pinned Groups (2026-11-13)

Flow adds a manual wvkbd toggle in Quick Settings and on `Super+Shift+K`. On the ARM64 guest, simulated pointer clicks on a larger landscape keyboard entered a lowercase letter, Shift plus the uppercase letter, and Enter in a disposable terminal; an isolated toggle test showed, hid and showed the keyboard, and the installed Quick Settings control showed and hid it. Physical touch, automatic appearance, portrait and compositor shortcuts remain unverified.

Quick Settings opens GNOME's existing Wi-Fi panel with process-local environment adjustments. The installed control was activated with a simulated pointer and the guest displayed its no-adapter state. Actual Wi-Fi scanning, secrets and connection require wireless hardware. The existing connection-profile editor remains available.

A saved, uniquely identifiable two-app Group can be pinned, focused and reordered as one column, or unpinned without closing its apps. In the guest, an isolated three-window test pinned and moved a real Group, focused it, rejected unstack while pinned and unpinned it. A manual Tabs / stack action switched its Niri tile heights while preserving Group identity and pin membership. The installed switcher control also changed an existing Group to tabs and back, restoring its original window sizes. Automatic orientation adaptation, side-by-side Group layout, missing-member relaunch and multiple displays remain open.

## Group focus and conservative pinned recovery (2026-11-13)

Renaming a saved Group now updates its pin label. A pinned Group records the last focused member observed by Flow and returns to that app, including when uniquely matched windows receive new IDs. An isolated guest test switched members, renamed the Group, left it, and returned to the last observed member; after closing, reopening and rejoining one member, the pin matched the new window ID. Group and pin controls use 44-pixel minimum targets where changed; actual touch and portrait usability remain untested.

Session recovery can rebuild a pinned Group without unpinning when both uniquely identified members are already adjacent at the trailing edge. It checks Group identity and overlapping pins, and declines cases that could shift unrelated columns. Isolated ARM64 guest tests rebuilt such a pair in both saved and reversed column order, restored the saved member order, kept the neighboring pinned column and focus unchanged, and retained pin and Group IDs. Pin-order reconciliation and full reboot recovery remain separate limitations.

As a feasibility check for adaptive Groups, the guest also merged and separated an unfocused pair through Niri's window-ID-targeted actions while another window retained focus. This single scenario does not establish safe orientation reflow or event handling; Niri's [output](https://docs.rs/niri-ipc/26.4.0/niri_ipc/struct.Output.html) data exposes logical geometry, while its [event stream](https://docs.rs/niri-ipc/26.4.0/niri_ipc/enum.Event.html) does not itself report output configuration changes.

## Narrow portrait Group controls (2026-11-13)

The switcher now grows with wrapped controls and scrolls if the screen is too short, keeping keyboard focus visible. Desk pin editing wraps its controls and scrolls to focused buttons. In the ARM64 guest, a temporary 90-degree output transform with scale 2.5 simulated a 320×512 logical portrait screen: keyboard navigation reached the existing Group controls, and the pin-edit controls wrapped without horizontal clipping. A separate 800×1280 logical rotation retained the Group's window IDs and positions. After each check the original display transform, scale, window positions and focus were restored. These checks used a keyboard and a simulated display, not physical touch or automatic Group reflow.

## Reversed Group recovery and rename editor (2026-11-13)

Pinned Group recovery now handles adjacent members that reopen in reverse order at the end of the workspace, using Niri's window-ID-targeted consume action to restore their saved order. An isolated ARM64 guest test preserved the neighboring pinned column, focus, Group ID and pin ID. Other layouts that could shift existing spaces remain manual. The Group name editor now grows its switcher area and uses 44-pixel controls; the installed guest UI showed the entire field and Save button, and Tab reached Save. This does not verify physical touch, reboot recovery or automatic orientation reflow.

## Missing Group member relaunch (2026-11-13)

An isolated ARM64 guest test used a temporary desktop entry and Gio to reopen a missing member of a pinned two-app Group. Recovery rebuilt the Group while preserving a neighboring pin, focus, and the Group and pin IDs. The test used disposable windows and an in-memory snapshot in an existing session; it does not establish full reboot recovery or restore application documents. Duplicate app windows remain manual when their identity is ambiguous.

The Group rename editor now provides Cancel and handles Escape without closing the switcher. In the installed guest, a keyboard test discarded a changed draft, reopened the editor with its saved name, and used Escape to return to the switcher and then the app. The new control has a 44-pixel target; physical touch was not tested.

## Pin Groups from the switcher (2026-11-13)

Saved Groups can now be pinned and unpinned directly from their switcher card. The control reflects the Group's actual pin identity rather than its label. In the ARM64 guest, keyboard navigation pinned an existing Group and unpinned it again; its original column order and focus were restored after the check. Physical touch and multi-display pinning remain untested.

## Protect separated pinned Groups (2026-11-13)

Saving now refuses to replace the session snapshot if an existing pinned Group was separated outside Flow. Rejoining or unpinning it allows the user to continue. A temporary-state ARM64 guest test used two disposable Niri windows and the recovery command's save path: it checked both lock files, verified the pinned rejection preserved the snapshot byte-for-byte, and confirmed that an unpinned Group could be removed by saving. The test did not exercise concurrent lock contention or reboot.

## Protect pin ordering during recovery (2026-11-13)

Recovery of snapshots without Groups now avoids reordering when a live pinned app occupies the workspace, matching the existing conservative behavior for mixed Group snapshots. In the ARM64 guest, an isolated test kept a disposable pin and restored app in their original columns and retained focus. Automated checks also cover a pin appearing before a move. Niri IPC actions are separate, so a pin appearing between the final check and a move is still a race.

## Ambiguous Group identity (2026-11-13)

If saved metadata contains multiple Group IDs for the same apps, the pin controller now treats the Group as unavailable instead of selecting one. A read-only ARM64 guest check used the real Writing Group windows and temporary in-memory duplicate records: pinning and focus were rejected without compositor actions. Unit tests cover pinning, focusing and availability.

The switcher also labels pinned Groups before their name so the status remains visible when a long name is elided. The installed ARM64 guest showed the label after pinning, and removed it after unpinning; the original workspace order and focus were restored.

## Targeted Group layout control (2026-11-13)

The switcher now sends the selected Group ID when changing tabs or stack, and the controller rechecks the active workspace, focused member and membership immediately before the Niri action. An isolated ARM64 guest test rejected a wrong ID and switched a disposable Group to tabs and back while preserving the original workspace, focus and pins. The installed switcher also changed the Writing Group to tabs and back. Unit tests cover changed focus, workspace and membership. Niri's focused-column toggle is a separate IPC request, so a focus change after the final check remains a race.

## Niri side-by-side primitives (2026-11-13)

The repeatable [disposable-window smoke test](../tests/niri_group_layout_smoke.py) exercised Niri 26.04's window-ID-targeted stack, split and sizing actions in the ARM64 guest. Both half-width columns measured 636 logical pixels, focus stayed on the intended member, and original windows, workspace, focus and pins were unchanged. This verifies compositor primitives only; Flow does not yet treat two columns as one Group or reflow automatically.

## Independent stack Groups (2026-11-13)

Two distinct, unambiguous apps in an active stack can now become a Group through the switcher without saving the whole session. A separate per-user registry retains its ID, name and membership, and existing session Groups migrate without replacing their snapshot or pins. An unpinned Group can be dissolved from the switcher without closing its apps. Session recovery checks the current registry so it will not resurrect an explicitly dissolved Group.

The [lifecycle smoke test](../tests/niri_group_lifecycle_smoke.py) passed in the ARM64 guest with disposable apps and isolated state: it exercised legacy migration, create, rename, pin, unpin and dissolve, and checked the old snapshot and real state remained unchanged. In the installed shell, keyboard navigation created and dissolved a temporary Group while keeping both apps open; the original workspace, focus, snapshot, pins and Group records were restored. The guest's existing Writing Group migrated with its previous identity. A backup of pre-migration guest state was made before the installed shell restarted.

This is partial M1: missing Groups have no complete repair UI, and a newly created Group is not itself a full session snapshot. Niri's targeted consume-or-expel action is conditional; a concurrent external layout change between validation and dissolution can still affect a neighbor. If dissolution is interrupted after separation, the saved Group remains and its apps must be stacked again before retrying. Full reboot, physical touch and multi-display behavior were not verified.

## Merged theme startup fix (2026-11-13)

The concurrent Floe theme integration initially failed to start Quickshell in the guest because its generated QML did not import the module providing the `color` type. The generator now emits the QtQuick import. The rebuilt ARM64 guest started Quickshell, showed its island and the migrated Writing Group, and retained the running apps. This is a startup and Group-switcher check, not full light/dark, portrait or accessibility validation of the theme.


## Floe controls and incomplete Groups (2026-11-13)

The shell uses semantic hover, pressed and disabled button colors, a clearer Desk search control, and keyboard-focusable 56 × 44 logical-pixel appearance switches. The ARM64 UTM guest rendered the updated Desk in Arctic Daylight and Polar Night, switched by keyboard, and retained the light preference through a shell restart. The existing Writing Group remained in the switcher. Third-party app appearance and full-system theming remain separate.

The switcher now reports incomplete Groups. A missing member may be opened via its exact desktop entry; the user must then stack the apps, unpinning first if necessary. Duplicate app windows, apps on another workspace, missing launchers and timeouts are reported without moving windows or deleting Group metadata. An isolated ARM64 guest test used real Gio and disposable Foot windows to reopen one absent app and verified the original windows, focus, workspace, Group and pin state were unchanged. The incomplete-Group switcher control itself has not yet been exercised end-to-end in the installed shell. Automated tests, the ARM64 Nix check and NixOS build passed. Physical touch, full restart recovery, automatic rejoining and adaptive Groups remain open.


## Rejoin an incomplete Group (2026-11-13)

The switcher offers **Rejoin Group** when both saved members are uniquely identified, tiled as single windows in adjacent columns and on the active workspace. It uses Niri's targeted action and confirms the resulting stack and saved member order before rebinding a Group pin. Pinned Groups are currently supported only when the two columns are at the end of the strip; conflicting pins, ambiguous members, other workspaces and unsafe layouts are reported for manual attention. Niri's action is not atomic with validation, so a concurrent external layout change can still affect a neighboring window.

In the ARM64 UTM guest, the lifecycle smoke test rejoined pinned and unpinned disposable Foot windows, including reversed columns, and closed, reopened via Gio and rejoined a missing member without losing the pin or saved identity. The installed switcher button was also exercised with a temporary Group; an intact Group on another workspace no longer appeared as needing attention. Afterward the original windows, focus, Group registry and pin state were restored. This does not implement side-by-side logical Groups, automatic orientation reflow, full reboot recovery or physical touch.
