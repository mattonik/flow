# Milestones

- **0 — graphics gate: single-display smoke test passed.** See [graphics validation](graphics-validation.md) for the VirtIO renderer, direct Niri session and remaining performance limitations.
- **1 — session foundation: partially verified.** Flake and NixOS module, Niri fullscreen-width columns, real app picker and terminal, minimal Quickshell capsule, and an evaluation fixture. Build, login, logout, manual and system-requested locking, shell startup, launcher rendering and focus via IPC have been exercised. Verify the remaining hardware shortcuts, island click, suspend/resume and fullscreen overlay before calling this complete.
- **1a — v0.9 Desk: in progress.** A Quickshell Desk overlays the existing Niri session, with app search, files, overview and return controls. Work and Niri overview remain native compositor views. Confirm keyboard and pointer behavior on the installed guest; spatial transitions and touch gestures remain future work.
- **2 — pinned spaces: experimental single-display slice.** Per-user metadata and create, unpin, reorder and focus controls exist for single-window apps; Niri IPC maintains open pins at the left of the focused workspace. Test compositor restart and additional physical shortcuts. Absent apps have no reserved compositor position or automatic launch, duplicate app windows cannot be unambiguously restored, and Groups and multiple displays remain unsupported.
- **3 — minimum system surface: not started.** Replace the capsule's placeholder interactions with accessible Home, useful OS-backed status and controls, and notification handling; integrate secure session services and validate keyboard and touch access.
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
