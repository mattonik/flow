# Implementation status against Flow Master Spec v1.1

Updated 2026-11-22. This is a repository and recorded-evidence audit, not a new full acceptance run. **Partial** means some relevant behavior exists but the whole journey is not established. **Open** means the acceptance journey has not been demonstrated. See [milestones](milestones.md) and [graphics validation](graphics-validation.md) for prior evidence and its limits. Recent targeted checks ran in the UTM guest; they do not validate every journey below.

The recorded test environment is an Apple Silicon Mac running UTM 5.0.8, QEMU ARM64 with HVF, 8 GiB RAM and virtio-ramfb-gl; NixOS 26.11 with Niri 26.04 and virgl acceleration. Exact host model and physical input details were not recorded. No journey below is promoted to a full pass solely from the HTML prototypes or from unit tests.

| ID | Status | Evidence and remaining acceptance gap |
|---|---|---|
| F-001 Native session | Partial | Niri, Quickshell, real apps and login were exercised; Plasma remains installed as a fallback. Warm reboot hung in UTM; fallback login was not newly verified. |
| F-002 Three real apps | Partial | Files, terminal, editors and browser ran in the guest. A single-workspace Niri IPC check focused running Firefox, Kate and Files in turn, then moved right and left between Files and Firefox and returned to Files. The complete navigation journey, including touch, remains to be checked. |
| F-003 Pinned positions | Partial | Single apps and saved Groups of two to four apps can be pinned and ordered as one column; each display keeps its own pin order (2026-11-20). Recovery rebuilt a pinned Group from adjacent end columns in either order, restoring saved member order while preserving its pin, focus and a neighboring space. Other missing or changed layouts can require manual recovery. A pinned three-app Group was recovered in the guest after its members were separated. On 2026-11-22 a pinned Group beside a pinned app was recovered with every app closed, in a simulated restart. Per-display ordering was exercised on two real Niri outputs in the guest. The complete restart journey remains open. |
| F-004 Shortcut accuracy | Partial | Displays have user-stable numbers: Main and External 1, 2, …, kept while a display is disconnected; the user can make another display Main. Super+number focuses Main's pins from any display, and holding Super+Alt, then pressing a display digit and a space digit, focuses that external display's pin directly, with an indicator, a timeout and feedback for unknown displays and spaces. Search lists other displays' pins and routes focus to their display first. The sequence was exercised through the shell's IPC on two real Niri outputs in the guest, and the bindings pass `niri validate`; physical key presses, keyboard layouts and remapping are not verified. |
| F-005 Group identity | Partial | Groups of two to four apps can be explicitly created; since 2026-11-20 they are bound to their windows within a Niri session (see *Groups beside other windows of their apps*), and automatic layout reflows them by orientation. Originally, two unambiguous apps could be explicitly grouped, named, pinned and dissolved using a registry independent of session snapshots. An isolated ARM64 guest test exercised migration and the Group lifecycle without changing the real snapshot; the existing guest Group also migrated. Manual stack/tabs remains available. The switcher can open an absent app and explicitly rejoin two adjacent, unambiguous single-window columns, including a pinned Group at the end of the strip. A manual side-by-side layout now appears as one switcher and keyboard-navigation destination; an ARM64 guest test preserved its pin, focus and neighboring pin across a stack → side-by-side → stack journey. If another app separates the two columns, the switcher can explicitly repair the Group when intervening columns are safe. Since 2026-11-22 a pinned stacked Group and a side-by-side Group were recovered by automatic recovery in a simulated restart (every app closed, state from an earlier Niri session), then changed and dissolved (see *A Group recovered after its apps closed*). Since then *Rejoin Group* also stacks a pinned Group's apps with other columns after them (see *Rejoin Group for a pinned Group*). That includes a pinned app after them, another display's or this display's. A real reboot, touch navigation and Groups on other workspaces or displays remain open. |
| F-006 Layer dismissal | Partial | A Layer is a compact floating window over the current space; floating windows are no longer counted as spaces (2026-11-20). Search can open any app as a Layer, and the switcher can make the focused standalone app one. In the guest, a Layer was opened over a pinned, resized side-by-side Group and closed again. The Group, its pin, every column and width, and the Flow state were unchanged, and focus returned to the same Group app. Portrait outputs get a bottom sheet, open Layers reshape when the output rotates, and a Layer can join the Group beneath it. Touch is not implemented. |
| F-007 Desk/Overview | Partial | In the ARM64 guest, opening and closing Desk and Niri Overview retained the same four window IDs, workspace, scrolling positions and focused window. At a simulated 320×512 logical portrait size, Group switcher keyboard navigation and pin editing remained visible; the original display and window state were restored. Gesture, physical touch, multi-display and visual-transition acceptance remain open. |
| F-008 Search | Partial | Search covers recent local files within the user’s home folder, files and folders found by name in the XDG user folders while typing (since 2026-11-22, no index; see *Files found by name*), Flow settings, quick settings, installed applications, open windows and saved pins, including closed apps. Since 2026-11-22 it also lists Flow's actions and the system's (lock, suspend, log out, restart, power off; the last three ask first; see *Commands in search*). File contents and the focused app's menu commands are missing. The settings shell actions were checked through IPC. Until 2026-11-21 the installed search listed nothing and opened nothing (see *Search on the installed system*); since then an app was found and opened from it on the installed configuration. A recent-file result from an isolated history was selected in the guest and opened in Kate; naturally populated history and the settings results still need end-to-end validation. |
| F-009 Island/system | Partial | Island, quick settings, lock, a temporary notification inbox and notification banners exist. Apps can show Activities with progress in the island through the Platform API (ADR-0010, 2026-11-21); they are labelled with their owner and end with the app's connection. Quick Settings opens GNOME's Wi-Fi panel and the connection-profile editor, and controls one unambiguous MPRIS player. The guest showed the no-adapter Wi-Fi state; since 2026-11-22 a simulated WPA2 network (`niri_wifi_hwsim_smoke.py`, see *Simulated Wi-Fi*) was found, joined with the right passphrase and refused with a wrong one, and the Network row opened GNOME's panel listing it. Real Wi-Fi hardware, Bluetooth (Flow has no control for it), durable notification history and independent availability across all contexts remain unverified. |
| F-010 Real accounts | Open | Two-user and guest isolation and switching have not been verified. |
| F-011 Display hotplug | Partial | In the guest, a second virtual output was disconnected and reconnected through its DRM connector (2026-11-20). Its pins stayed with it and nothing on the other display was reordered, then the pins came back in order. A pinned three-app Group and a single app were moved between the two outputs as one destination each. Physical monitors, docks, connector changes and serial-number identity are unit-tested only. |
| F-012 Portal correctness | Partial | The guest's portal broker is active and owns its session D-Bus name. A disposable unsandboxed chooser request returned the selected test-file URI, and a second request returned a cancel response. Sandboxed file access, saving, screenshots, screenshare, clipboard and drag/drop remain unverified. |
| F-013 Audio/peripherals | Open | Volume control was exercised; a Bluetooth headset and microphone were not tested on physical hardware. |
| F-014 Figma day | Open | No recorded Figma workload, screenshare or browser-load test. |
| F-015 Development day | Open | Terminal and editors run, but the native-host development, container and VM workflow is not verified. |
| F-016 Touch day | Open | A manually invoked on-screen keyboard entered a letter, Shift and Enter into a guest terminal with a simulated pointer. Real touch, automatic text-field activation, rotation and tablet Group testing remain open. |
| F-017 API app test | Partial | Version 1.1 of the Flow Platform API (ADR-0009) is a session D-Bus service with a spaces interface scoped to the caller's own app and a control interface for unsandboxed callers (2026-11-21). Two non-Qt clients were used in the guest on a private bus: the Python/Gio `flow` CLI and `busctl`. A command run inside one app listed only its own part of that app's space and focused it while the app had focus. Its attempt to focus another app's space was refused as `NotAuthorized`. The packaged service also started by D-Bus activation. Since 2026-11-21 a real Flatpak app, FlowProbe, built on the freedesktop runtime with only Wayland and `--talk-name`, kept the sandbox promises of version 1.2 in the guest (`niri_flatpak_api_smoke.py`, see *Flow Platform API*). A NEMESIS review of that work found that a sandboxed app owned any window carrying its app ID, including a native app's; ownership now follows the window's sandbox, and the test now covers it. Remaining gaps: the shell does not use the API yet; callers and windows are identified by PID, not pidfd; confinement other than Flatpak is not recognised; and a sideloaded Flatpak can take the app ID of a natively installed app. |
| F-018 Shell restart | Partial | `niri_shell_restart_smoke.py` (2026-11-20) started a development shell and restarted it with SIGTERM, then SIGKILL. It ran beside a pinned app, a pinned, resized side-by-side Group and an unpinned app, with isolated state. Pins, Groups, window IDs, columns, widths and focus were unchanged from before the first start, and Super+1 and Super+2 still reached both pins. A shell that moves focus on startup fails the test. Since 2026-11-22 it also restarts the shell during Flow actions the shell started: a Group layout change, a pin and a Layer opening each ran on as a new shell started, finished whole, and the new shell showed them. Flow's helpers now survive the shell's closed pipes (see *Shell restart during a Flow action*). The installed shell is the `flow-shell` user unit, restarted by a NixOS switch when its files or Quickshell change; see *Installed generation*. |
| F-019 DisplayLink hardware | Open | No named dock or monitor test. |
| F-020 App menu fallback | Partial | Opt-in (Settings, off by default; Super+M). While it is on, the shell runs one resident reader, `flow-appmenu serve` (`session/appmenu.py`), supervised like the controller and talking JSON lines; nothing starts per focus change. When focus settles, a probe answers for the focused window: its first probe finds the accessible app (by PID, or by `app_id` and title for X11 windows under xwayland-satellite), chooses the window's frame by title and `active` state while the window still has the keyboard, and remembers it and whether it has a showing menu bar with an item; later probes answer from memory after one check that the frame is still `active`. In the guest a window's first probe took 20–40 ms (LibreOffice's 170 ms) and later ones 4–13 ms; the performance smoke's probes of a terminal and of Nautilus had a median of 4 ms (Nautilus's first, as it opened, 348 ms). A probe that finds nothing final is asked again 1.5, 4 and 10 s later while focus stays, and Super+M on a window with no menu known asks afresh before it says so. The App button shows only when that answer is yes; Super+M on a window without one shows "No app menu". An X11 window is matched only among apps that own no Niri window, so never to a Wayland app's frame, and an app on the bus that does not answer is left out rather than failing every probe. Reads and commands go through the remembered frame, so same-process windows work: in the guest smoke, File > New reached only the focused one of two same-titled Kate windows, each way round. A command whose reply does not come within 1 s (a modal dialog) counts as sent: the menu closes with no error. LibreOffice's second, 0-pixel menu bar is left out. Turning the menu on switches accessibility on for the user (persisted by at-spi); turning it off switches it off again only if Flow switched it on. The setting's text says so, and that running Qt 5 apps need a restart and a session from before at-spi2-core needs a new login. Readable today: Kate, Mousepad, DB Browser (Qt 5, started after accessibility came on), LibreOffice, VLC (X11). Not readable: GTK 4 / libadwaita, Firefox, Chromium, Electron, terminals ([coverage report](engineering/APPMENU_COVERAGE_2026-11.md), measured 2026-11-22). Checked by `tests/niri_appmenu_smoke.py` (pointer-driven) and `tests/niri_performance_smoke.py`. Coverage does not justify permanent menu chrome (open question 8). |
| F-021 Sync conflict | Open | No sync provider or conflict-resolution journey. |

## Contextual-menu gap and next investigation

The shell listens to Niri focus and workspace active-window events and keeps an open application menu's context unchanged if focus moves or its window closes. It disables commands and asks the user to close the menu; the current app is re-identified afterward. Invocation also rechecks the active Niri window and command before calling AT-SPI. In the ARM64 VM, KWrite and Kate formed a real two-window stack; changing the active member while KWrite's menu was open retained its label, disabled commands and showed the warning. Dismissing it updated the label to Kate; Kate's New command created a document, and a stale KWrite command was rejected. Window closure, pointer focus, other toolkits and multi-display cases remain unverified. App commands are kept separate from the switcher's stack and rename actions. Windows sharing a process are told apart by the frame the reader chose when focus settled on each (F-020). There is no independently running Flow Controller or versioned command-provider API yet; `flow-spaces` is a local experimental CLI, not the specified API.

The master spec marks the contextual menu as an approved hypothesis, whereas the design note calls its UX direction approved. Treat the focus and separation principles as the supplied direction while retaining prototype validation and implementation status as open; no finalized D-Bus contract or cross-toolkit support is implied.

The smallest next contract to investigate is a read-only, versioned snapshot of the current session's spaces and active application, plus a focus request restricted to the trusted shell. Define stable identity, caller authorization, event ordering, reconnection and structured errors before publishing a D-Bus interface; do not expose window-control capabilities to ordinary applications by default. These are design questions, not implemented method names or security guarantees.

## Native navigation and Group checks (2026-11-18)

On a fresh ARM64 NixOS UTM 5.0.9 QEMU guest, Niri 26.04 and Quickshell 0.3.2 loaded the shell at commit `15d6014`. At 1280×800, two disposable Foot windows remained open through pointer navigation from Work to Niri Overview, then Desk and Return to work. The focused window ID remained 3, and window IDs 2 and 3 remained present. The shell log exposed an application-menu placement binding loop in the preceding build, fixed in `15d6014`.

The disposable Group layout, side-by-side, and lifecycle smoke scripts passed. They exercised stack and split, Group identity, focus, pinning, neighboring pin preservation, side-by-side repair and recovery, migration, missing-member reopening, and dissolution. These are scripted single-display checks. Physical touch, portrait layouts, interrupted transitions, full reboot recovery, and accelerated renderer validation remain open.

## Automatic Group layout check (2026-11-19)

On the ARM64 UTM 5.0.9 QEMU guest with Niri 26.04 and Quickshell 0.3.2, the isolated Group smoke test enabled automatic layout, rotated the 1280×800 output into portrait and back, and observed the shell switch the Group to a stack and then to side-by-side. A separate neighboring app retained focus, both Group windows and the neighboring pin stayed present, the Group and pin identities remained stable, and a previously set 65/35 split returned in landscape. A manual layout selection disabled automatic mode. The test restored the original display transform and user state. Group size-constraint errors retain a pending layout change for explicit recovery; recovery reports an unsatisfied saved proportion.

Automatic layout is opt-in per Group and applies on the focused workspace when shell overlays are closed. Physical rotation, touch, multi-display, Groups on other workspaces, and application size-constraint behavior on real hardware remain open. The smoke test invokes the switch through the backend and verifies shell-driven transitions; pointer interaction with the new switch has not been separately checked.

## Display-scoped pins and larger Groups (2026-11-20)

`spaces.json` is now version 2: pins carry a display key, and each display orders, reconciles and numbers only its own pins. The key uses make, model and serial when the serial is present and unique among connected outputs, so a monitor keeps its pins on another connector; otherwise it includes the connector, and a monitor on a new port is a new display. Duplicate serials are not trusted, and a serial once seen on two connected monitors keeps both keyed by port, so unplugging one twin does not turn the other into a new display. Legacy pins migrate once with a `spaces.v1.json` rollback copy: a stored connector name waits under a `legacy:` key until that connector appears; a pin without a display is assigned by its live window or, with one display connected, to that display. Pins of a disconnected or ambiguous display are listed in Desk under *Pinned on other displays*. They are focused where their window is, but never launched, reordered or reconciled until the user chooses *Pin here*. Session snapshots record their display; recovery refuses a session saved on another connected display.

Groups hold two to four distinct apps. Recognition, layout changes (resumable from a partial arrangement), rejoin, repair, dissolve, pinning and recovery handle every supported size; the switcher can add the adjacent window, remove a member (it stays open), reorder members and keep an order changed with Niri's own keys. Side-by-side member removal is refused while the Group is pinned, because pin reconciliation is paused for side-by-side layouts.

On the ARM64 UTM guest (Niri 26.04, Quickshell 0.3.2, one 1280×800 output), the new `niri_larger_group_smoke.py` passed with disposable Foot windows and isolated state: create a three-app stack Group, pin it, add a fourth app, switch to four columns, reorder, stack, remove a member, interrupt a stack → side-by-side change after one Niri action and resume it, save, separate the members and recover the pinned Group, reconcile it to position 1, and dissolve it in order. A copy of the guest's real legacy pin file migrated to its display with order and a rollback copy kept. The existing layout, lifecycle and side-by-side smoke tests also passed, the latter with the development shell driving automatic portrait/landscape changes. The development shell loaded without QML errors and showed the numbered pin cards, a disconnected display's pin and per-member controls.

### Two-display journey

The VM's GL virtio GPU was given a second scanout: UTM's QEMU arguments now include `-global virtio-gpu-gl-device.max_outputs=2`, and the original `config.plist` is kept as `.floe-test/utm-config.plist.before-second-display`. (QEMU allows only one GL virtio GPU, and Niri cannot drive outputs on a separate non-GL card, so a second UTM display device does not work.) UTM's viewer shows only the first output, so the guest boots with `card1-Virtual-2` disconnected. Writing `on`, `off` or `detect` to `/sys/class/drm/card1-Virtual-2/status` as root and running `udevadm trigger --action=change /sys/class/drm/card1` connects or disconnects it. Niri treats it as a normal 1024×768 output to the right of the 1280×800 one, with no host window to view it.

`niri_display_pins_smoke.py` passed on Niri 26.04 with three disposable Foot windows, one on the first output and two on the second:
- Each display listed and numbered only its own pins.
- Focusing the second display's pin from the first moved display focus there.
- Reordering on the second display left the first unchanged.
- On disconnect, Niri moved the second display's workspace to the first. Its pins were listed as disconnected, stayed searchable and focused their windows in place, and no window was reordered.
- On reconnect, the workspace returned, and the pins were available again in the same order with every window where it was.
- Pins moved between displays only on request.
- Recovery refused a session saved on the other connected display.

With per-display filtering disabled, the test fails. Niri reports both QEMU outputs with the same make and model and no serial; it renames the second to "Unknown". Identity by serial number and connector changes are therefore covered only by unit tests.

### Moving spaces between displays

The switcher's active space offers *Move to* each other connected display. An app, a stack or a whole Group moves as one destination, keeping its member order, focus, window IDs and Group identity. Niri inserts a moved column after the destination's active column, so each Group column is moved with the previous one active there, and a space never lands inside an active side-by-side Group. A pinned space joins the end of the destination's pinned spaces and is placed there explicitly, because pin reconciliation is paused beside side-by-side Groups. The pin follows as soon as every window has arrived, even if a later step fails. The moved space is the active window's own: another window of a Group app moves alone. Layers are not moved; make one a space first. Saved side-by-side proportions are reapplied on the new output. Niri requests are separate, so an interrupted Group move leaves its first apps on the destination: the error says so, and moving one of the remaining apps again finishes it. A Group that is split in any other way must be repaired first.

`niri_move_display_smoke.py` passed on the two-output guest (Niri 26.04, five disposable Foot windows, isolated state):
- A pinned three-app side-by-side Group moved to the second display and took its pinned place after that display's own pin, with the same Group, pin, window IDs and focused member.
- An unpinned app moved there landed after the Group, not inside it.
- The Group was stacked there and moved back as one column, with its pin and focus.
- Moving to the display a space is already on was refused.

With the side-by-side anchor disabled, the guest test fails: the moved app lands between the Group's first and second apps.

A side-by-side Group that no longer fits its new display is stacked instead of being cut off. This happens when its apps refuse their saved proportions there, or when it follows orientation and the display is portrait. A toast says so (*Trio was stacked for the portrait Dell U2723QE*). The saved proportions are kept for when it returns side by side. A Group with a manual layout that fits stays side by side, even in portrait. `niri_move_reflow_smoke.py` rotated the second guest output to portrait and moved a resized three-app Group with automatic layout onto it:
- It arrived stacked, the toast was shown, and its identity and proportions were kept.
- Moved back to the landscape display, automatic layout returned it side by side at 43/23/33.

With the reflow disabled, the test fails. The size-refusal case is unit-tested only, because Foot accepts any width. The development shell showed the *Move to* button on the active Group's card. The button itself was not clicked in the guest, since no pointer automation is available there; the backend action it calls is what the smoke test exercises. An interrupted move is covered by unit tests only.

### Display numbers and shortcuts

Each display record in `spaces.json` keeps a `number`: 0 is Main, external displays count from 1. The first Main is a built-in panel when one is connected, otherwise the leftmost display; a new display takes the lowest free number, and numbers stay with disconnected displays. *Make this the main display* in Desk swaps numbers with the previous Main. Desk names the display (*Pinned spaces · External 1 · Dell U2723QE*) and lists other displays' pins under their names. Super+1…9 opens Main's pinned spaces from any display. While Main is not connected, for example a closed laptop on a dock, Super+1…9 uses the lowest-numbered connected display and a toast says so. Niri binds Super+Alt+1…9 to the shell's `display_digit`. The first digit chooses a display and shows *External N · label — choose a pinned space* at the bottom of the screen without moving focus. The second digit focuses that display's pin. Holding Super+Alt between the digits is the intended grammar but is not enforced: any Super+Alt+digit within 1.5 seconds completes the sequence, and after that it is cancelled. An unknown display, a disconnected display or a missing space is reported, and focus does not move.

`niri_display_shortcut_smoke.py` passed with the development shell on the two-output guest. It drives the same IPC calls the bindings spawn:
- Displays were numbered Main and External 1.
- External 1 then 2 showed the indicator, then focused External 1's second pin, without stopping on Main.
- Super+1 on External 1 focused Main's first pin.
- An unfinished sequence timed out, an unknown display was reported, and focus stayed.
- A missing pinned space was reported without moving focus.

With Super+number reverted to the focused display's pins, the test fails. Physical key presses were not sent: the guest has no input-injection tool, and the installed Niri configuration points at the installed shell.

### Resizing Group members

In the switcher, each app in a side-by-side Group shows its share of the display's width with *−* and *+* controls. A step moves 10% of the display's width to or from the next app (the previous one, for the last app) and never leaves an app below 15%; a step that would is disabled and refused. Requested widths are checked against what the apps commit: if an app's size limits refuse, both apps go back to their widths before the step, the Group keeps its proportions, and Flow says so. Proportions are saved with the Group and reapplied when it returns to side-by-side. Stack heights are not adjustable yet.

`niri_group_resize_smoke.py` passed in the guest with three Foot windows: 33/33/33 became 43/33/24 after a wider and a narrower step, matching the saved proportions. A step below the minimum share was not offered and was refused. Stacking and returning to side-by-side restored 43/33/24. With saving disabled, the test fails. The switcher showed the shares and disabled the steps that would go below the minimum.

### Side-by-side widths: steps, adding and removing apps, and the switcher's shares (2026-11-24)

The review of the width check found older ways a row could be left wider than the display, or shown wrongly. All widths here are now shares of the display.

- **The switcher showed each app's share of the Group's total.** Now it shows each app's share of the display's width, so a row with a gap or one wider than the display shows as such. When the display's width is unknown, it falls back to shares of the total.
- **A step counted in shares of the row's total.** A step now moves the app by exactly 10% of the display. The app it trades with takes the rest, so the row fills the display even if it had a gap or was too wide. The switcher's −/+ use the same rule, so a button that is shown enabled is a step that is taken.
- **A refused step left the row as the refusing app made it.** If the app losing width keeps a minimum, the two widths add up to more than before, and the row no longer fit. Flow then saved shares of that row's total, which never reached `groups.json`, because the action failed. Now both apps are asked back to the widths they had, measured as shares of the display. The message says the Group keeps its widths; before, it said "keeps the closest size".
- **Adding an app beside a side-by-side Group kept every width as it was.** A neighbor at full width made the row twice the display, and its shares of that total were saved. Now the new app takes an equal share, and the others keep their saved proportions to each other, as a Layer added to the Group already did. That code now lives in Add, which Add to Group on a Layer uses. Before any width changes, Flow checks the Group, the neighbor and focus again, as it already did for a stack. It then waits for the apps to take the widths. If one refuses, the Group's apps go back to the widths they had, the new app becomes a full-width space again, and it is not added.
- **Removing an app from a side-by-side Group left a gap** and saved shares of the total. Now the remaining apps keep their proportions to each other and are asked to fill the row. Flow does not wait for them: the app has already left, so there is nothing to undo.

The tests are in `test_member_resize.py`. One test steps every app both ways on rows that fill the display, have a gap, or are too wide. It checks that each enabled button moves the app by exactly one step and fills the row, and that each disabled one is refused without a change. It fails with steps of the total, without the neighbor taking the gap, and with the buttons' old rule. Further tests fail without each of these: the put-back after a refused step, the width requests, the settle check, the re-check and the put-back when adding, filling the row when removing, and the switcher's shares of the display. In the guest (Niri 26.04, 1280×800), `niri_group_resize_smoke.py`, `niri_larger_group_smoke.py` (adding and removing apps side by side) and `niri_layer_smoke.py` (a Layer joining a side-by-side Group) passed. The resize smoke ended at 43/33/23, against 43/33/24 before: Foot snaps widths to whole cells, and a step is now exactly 10% of the display. The Layer joined at shares of 39/27/33 of the display; main shows 40/26/33 of the total for the same widths. The layer smoke needs `gio` on PATH, as the installed wrapper provides; without it, it fails to open the app on main too. No guest app refuses a width, so refusals are covered by the unit tests only.

### Side-by-side widths are checked against the display (2026-11-24)

After Flow asks for side-by-side widths, it waits for the apps to commit them before it saves the layout. This happens when changing a Group's layout, resizing a member, moving a Group to another display and recovering a session. Until now each app's width was measured as a share of all the Group's widths added together, and a share within 0.05 of the saved one passed. So a row wider than the screen could pass. For example, at 20/80 an app that ignored its 80% and stayed full width measured 0.83 of the total, and the first app 0.17. The change was saved as side by side, wider than the screen. At 25/75 the same thing passed when the app stayed at 99%.

Each width is now compared with its share of the display's logical width, within 0.05. The row must also fit the display, with one logical pixel per app allowed for Niri's rounding. The display's width is read from Niri only once the apps' widths can be measured, and once per wait. Flow's Niri has no gaps or struts, and the shell's layer surfaces reserve no space, so the columns share the whole width. A Group that does not fit is refused as before: the layout change says "Application size constraints prevent the saved proportions" and stays pending, a resize puts both apps back, a moved Group is stacked, and recovery reports the size constraints. If Niri does not report the display's size, Flow does not guess: a layout change or resize stops with that message (the layout change stays pending), a moved Group is stacked, and recovery says the Group's proportions were not checked and goes on with the rest.

`test_group_layout.py` covers both checks. The 20/80 case fails if Flow goes back to comparing with the total. Three apps at 37% each where thirds were asked fail without the row check. Two apps at 40% where 50/50 was asked fail without the per-app check. Three thirds a pixel over each settle and 13 pixels over each do not, which pins the allowance. `settled_ratios`, used by resizing and moving, and session recovery (`test_restore.py`) each have a case that fails with the old comparison; recovery also has one on a 1280-wide display, and one without a reported size. A Group moved to a display whose width is unknown is stacked (`test_move_display.py`). A NEMESIS review found that the first version let a missing size escape from moving and recovery, after the windows had moved, and that the allowance and recovery's display were not pinned by any test; all are fixed. The test fakes of Niri now size tiles from their display's logical width.

In the guest (Niri 26.04, 1280×800), `niri_group_resize_smoke.py`, `niri_group_side_by_side_smoke.py` and `niri_move_reflow_smoke.py` (with the second output connected, then disconnected) passed with this change, and with main. The side-by-side tiles measured 636 logical pixels each at 50%, 0.497 of the display, a row of 1272. The side-by-side smoke needs a window on the focused workspace: on a guest with no windows, Niri removes the empty workspace it starts from, and both main and this change fail its last check, that the workspace was restored. None of these smokes has an app that refuses its width, so the refusal itself is covered by the unit tests only.

After #54 was merged (64ab56c) and installed in the guest as generation 49, `niri_move_display_smoke.py` passed and `niri_move_reflow_smoke.py` passed again, twice each, with the installed code in /etc/floe-test/flow. The second output, Virtual-2 at 1024×768, was connected for the runs and disconnected afterwards. The move-display smoke's three-app Group arrived side by side on the 1024-wide display, so its widths passed the new check against a display other than 1280. The reflow smoke rotates Virtual-2 to portrait (768 wide) and restores it. Its 43/23/33 Group was stacked "for the portrait Virtual-2", not "to fit", so its row passed the check at 768 before the portrait rule stacked it. Neither smoke reaches the "stacked to fit" branch, which, like the refusal, is covered by the unit tests only.

### Work indicator and Group focus

Following the v1.0 demo, Work shows a small capsule at the bottom right with the active space's place in this display's Flow (*02 / 05*) and previous/next buttons. For an active Group it also shows *Group* and a switch with one button per app, the focused one highlighted. Selecting an app focuses it. When focus moves to another app in the same Group, by the switch, Super+Up/Down or a click, a toast names it (*Foot Client is now focused*, adding *menus updated* when the app menu is enabled). Apps are named from their desktop entry, falling back to the window title. On screens narrower than 800 pixels only the position is shown, so it never overlaps the bottom controls. The capsule hides while Desk, Overview, the switcher, the launcher or an app menu is open.

In the guest, the development shell showed *Group · Foot · Foot Client* and *02 / 02* beside a neighboring app. Super+Down's backend action moved focus to Foot Client, the switch followed, and the toast read *Foot Client is now focused*; the toast was also read back through the shell's IPC. The capsule's buttons were not clicked, since there is no pointer automation in the guest. They call the existing focus and navigation actions.

### Layers

Floating windows are Layers: they are listed under *Layers* in the switcher and are not spaces, so they do not count in the Work indicator. Super+Left/Right from a focused Layer moves from the space beneath it; at the end of the strip focus stays on the Layer. Layers are left out of saved sessions and their checks, so an open Layer, including one of a Group app, neither pauses automatic saving nor needs recovery. Search offers *Open as Layer* for each app, after the ordinary entries. The app's new window, recognized by its desktop ID or `StartupWMClass`, is floated at 45% × 60% and centered. Niri first opens it as a column after the active one, possibly inside a side-by-side Group; floating it closes the strip up with every column's size kept. *Make Layer* in the switcher floats the focused app when it is standalone, unpinned and not in a Group. While a Layer has focus, the Work indicator shows *Layer · Name* with *Make space* and *Close*. *Make space* tiles it after the current space as a full-width column. *Close* asks the app to close, and Niri returns focus to the column the Layer was opened from. *Add to Group* in the switcher's Layers row tiles the Layer after the Group beneath it and adds it there. In a stack it joins the bottom; side by side it takes an equal share while the others keep their proportions to each other. Over a space that is not a Group, or a full Group, the Layer stays as it is. In the guest, a Layer joined a pinned, resized side-by-side Group at 40/26/33; without the share the test fails.

Before relying on Niri, its behavior was checked in the guest. A floating window returns to tiling after the active column. Closing a floating window focuses the remembered tiled window. A new window opened from under a floating window goes after the remembered column. `niri_layer_smoke.py` then passed with a pinned app, a pinned, resized side-by-side Group, a neighbor and a Layer from a disposable desktop entry:
- Opening the Layer left every tiled column, width, pin and space unchanged.
- Closing it returned focus to the same Group app, with the same Flow state.
- The neighbor became a Layer and came back as its own full-width space after the Group.

On a portrait output a Layer is a bottom sheet: full width and half height, anchored to the bottom edge so the top of the space stays in view. `niri_layer_sheet_smoke.py` rotated the guest output to 800×1280. The Layer sat at (0, 640) with size 798×634, and back in landscape it was centered at 576×474. The transform was restored afterward. With the portrait case disabled, the test fails. The same test found that Niri centers a window by its size before the app commits a new one, so Flow now waits for the new height before centering. An open Layer reshapes when its output rotates. The automatic-layout pass runs every 2 seconds while a Layer Flow shaped is open; when a Layer's output has rotated, it gets the new shape without moving focus. `layer-shapes.json` in Flow's state directory records which windows Flow shaped as Layers, and for which orientation, within the Niri session. Only those are reshaped; floating dialogs Flow did not make Layers are never touched, and a Layer the user resized is left alone while the orientation stays the same. Every locked action compares each recorded Layer with its output's orientation, so a rotation is noticed whenever Flow next acts. Unit tests cannot reach the file, which is only written inside a locked action. In the guest, a centered Layer became a sheet at (0, 640) after a rotation and focus stayed on the app beneath; with the reshape disabled the test fails. The smoke test found that a promoted Layer kept its compact width, which is now reset. An extra refocus step before closing turned out to be unnecessary on real Niri and was removed. Through the development shell's IPC, `open_layer foot.desktop` opened Foot as a centered Layer over a side-by-side Group, and the indicator showed its controls.

### Groups beside other windows of their apps

Before, a second window of any member app, on any workspace, made a Group unrecognizable. Now `groups.json` binds each Group to its window IDs together with the Niri session. In that session a member is its bound window, so other windows of the same app are separate spaces. Every lookup checks the window: moving, Make Layer, stacking, pin rules and the side-by-side pause all treat another window of a member app as its own space. A window that cannot be told apart from a member, without a binding from this session, is still treated as possibly in the Group. Once a bound member closes, other windows of its app are free. Bindings are made when a Group is created, an app is added or a missing app is opened. They change only when the Group's windows change, so other windows opening do not rewrite the registry. If a bound window closes, another window of that app stands in only when it shares a column with the Group's other windows, i.e. the user stacked it back in. Otherwise the member is reported closed, and opening it from the switcher binds the new window. A binding from another Niri session, as after a restart, is ignored. There each member app must have a single window, or the user chooses: the Group's repair card asks *Which Foot window belongs to this Group?* and lists the candidate windows on this workspace by title. Only tiled windows on this workspace are offered. Choosing one binds it, along with every member that is already unambiguous. *Choose again* forgets a choice that left the Group not working. A member app not chosen yet takes the window stacked with the chosen one, so choosing one window of a stacked Group restores it. A side-by-side Group needs a choice for each ambiguous app. Flow never picks a window itself. Older generations ignore the new `bound` field. Saving a session, manually or automatically, is still refused while a Group app has a second window, and recovery requires one window per app.

`niri_same_app_smoke.py` passed in the guest with two windows each of two Foot app IDs:
- A Group formed from one window of each.
- Side-by-side, resize and stack changes moved only the Group's windows.
- Closing the Group's editor left a reported gap rather than adopting the other editor.
- Opening the missing app bound its new window.

With stand-ins allowed from older windows, the test fails. The same test then marks the Group's binding as coming from an earlier Niri session, simulating a restart without ending the guest session. Both apps' windows were offered as a choice, and choosing the terminal brought the stacked Group back. Ignoring stack evidence makes this step fail. The development shell showed the choice on the Group's repair card. A real Niri restart was not performed, because it would end the guest's session.

### Flow Platform API, version 1

[ADR-0009](engineering/ADR-0009-platform-api-v1.md) defines the first slice. The `flow-api` service owns `io.github.mattonik.Flow1` on the session bus and exports two interfaces:
- `…Spaces`, for any caller: `GetCapabilities`, `ListSpaces`, `FocusSpace` and a `Changed` signal with no payload.
- `…Control`, for unsandboxed callers only: `ListSpaces` and `FocusSpace`.

Through `Spaces`, a space shared with other apps shows only the caller's own windows' titles and app IDs. Focusing requires that one of the caller's windows already has focus, so an app can't take focus from another one. Spaces have stable IDs. A pin is `pin:` plus its ID and a Group is `group:` plus its ID. A space that is neither gets a session-scoped `window:` ID hashed from the window, so raw Niri IDs are never exposed. Errors are structured D-Bus errors such as `NotFound`, `NotAuthorized`, `DisplayUnavailable` and `RateLimited`.

Callers are identified the way XDG portals identify them:
- **Flatpak apps:** `/proc/<pid>/root/.flatpak-info` gives the app ID. A Flatpak app sees and focuses only spaces with its own windows, and `Control` refuses it.
- **Unsandboxed callers:** they may use `Control`. On `Spaces` they act as the app whose process they descend from, for example a command run in a terminal acts as that terminal.

Rate limits follow a Flatpak app across all its connections. The service reads state through a new read-only `flow-spaces observe`, which neither reconciles pins nor writes state, apart from creating an empty Group registry on a fresh installation. It focuses through the same `flow-spaces` actions as the shell, and turns Niri's event stream into debounced `Changed` signals. The NixOS module installs the `flow` CLI, a D-Bus activation file and a `flow-api` systemd user service.

`niri_flow_api_smoke.py` passed in the guest on a private bus from `dbus-run-session`, using the development copy:
- Version 1.1 and its feature list were negotiated.
- `flow spaces list` showed a pin ID and an opaque window ID.
- `busctl` focused a space through `Control`.
- A command inside a third app saw only its own space and title, focused it while it had focus, and focusing another app's space was `NotAuthorized`.
- An unknown space was `NotFound`.
- `flow events watch` reported a change after focus moved.
- Of 25 rapid calls on one connection, the last 5 were `RateLimited`.
- With another app focused, a command in the third app raising its own space was `NotAuthorized` and focus stayed. The step first exposed a real race: a 300 ms snapshot cache let a call made right after another app took focus pass the check on stale state. Focus is now always authorized on fresh state. Five repeated runs pass, and with the cache restored the steal succeeds in three of three runs and the test fails.
- SIGTERM stopped the service together with its event-stream child.

With apps allowed to see every space, or without the child cleanup, the test fails. Unit tests cover the policy against a fake `/proc` and a real `observe` snapshot from the Niri model.

The module was evaluated in the guest from the flake. The built activation file, user unit and CLI were checked, and a private bus started the packaged service by activation on the first `flow version` call. That run found the event-stream child surviving the service, which is now fixed.

Version 1.1 adds `Control.PinSpace`, `UnpinSpace` and `MoveSpace`, and a `System` interface with `ShowDesk`, `ShowOverview` and `ShowLauncher`. The CLI gained the matching commands.

`PinSpace` names the window to pin, through a new `flow-spaces pin <window>`, so it is one locked step that never moves focus. A focus change during the call cannot pin the wrong space. A move past either end of the pins is refused, not silently ignored.

System requests never close anything. While any other shell surface is open they do nothing and return `InvalidState`.

Apps are gated on Niri's per-window `is_focused`, not on the workspace's active window. In the guest, opening the installed shell's Desk or Niri's overview turned the app's `is_focused` false while it stayed the active window, which is the gap the first version had. Sandboxed apps may not open the launcher.

Through `Control`, the guest smoke test pinned an app's space with focus kept on the other app, moved the pin left, and unpinned it; a second unpin was `NotFound`. Against a development shell, `System` then opened Desk, and a second request left it open. `ShowOverview` with Desk open was `InvalidState`, and Desk stayed open. The overview and the launcher each opened, and a second request left them open. With *show* sent through the toggling shell call, the test fails.

A real Flatpak app, FlowProbe (`tests/flatpak/flow_probe.py`), was tested in the guest with Flatpak 1.18 (2026-11-21). `niri_flatpak_api_smoke.py` builds it on the bare `org.freedesktop.Platform` 25.08 runtime with only `--socket=wayland` and `--talk-name=io.github.mattonik.Flow1`, and drives it through Flatpak's D-Bus proxy on a private bus:
- The bus names Flatpak's `xdg-dbus-proxy`, inside the sandbox, as the caller, and its `/proc/<pid>/root/.flatpak-info` names the app; the service offered it no `control.*` features.
- It listed exactly its two spaces. In the column it shares with another app, it saw only its own title and app ID, and no answer contained the titles or app IDs of the test's other windows.
- A native window that sets FlowProbe's app ID is not FlowProbe's: it was not listed, and while it had focus, `ShowDesk` and `FocusSpace` were `NotAuthorized`.
- Without focus, `FocusSpace` (its own space and another app's), all five `Control` methods and all three `System` requests were `NotAuthorized`, and nothing moved or opened.
- With focus, it opened the overview and Desk, and focusing the shared column moved focus to its own window there, not the other app's window above it. `ShowLauncher` and `Control` stayed `NotAuthorized`.
- Of 21 calls spread over three connections, only the last was `RateLimited`.

Each refusal, listing, ownership and limit check failed when the guard it covers was neutralised in a throwaway copy of the service.

NEMESIS reviews of the test found these defects, now fixed:
- **Window ownership by app ID:** a sandboxed app owned every window whose Wayland app ID equalled its Flatpak ID. A Flatpak sideloaded under a native app's ID could read that app's window titles, act whenever that app had focus, and show its name on activities. A window is now the app's only if Niri's PID for it is inside the app's sandbox.
- **Focus in a shared space:** focusing an unpinned space shared with another app focused the space's first window, which could be the other app's. An app now gets its own window. A pinned space is still focused as Super+number does.
- **Unbounded text:** `clean()` read an activity title or detail of any length before cutting it, so a Flatpak could stall the service, which answers one call at a time, with one huge string. Only the first four times the limit is now read, and a cut string ends in "…". This is unit-tested; the smoke does not send such a string.

Not done: the shell does not use the API yet, and the service was not run as the installed systemd unit. Callers and windows are identified by PID, not pidfd. A sideloaded Flatpak can take the app ID of an app installed natively; its windows stay apart, but its activities show that ID. A Flatpak's X11 windows are not its own. Confinement other than Flatpak is not recognised. Calls are answered one at a time. Groups, displays, Layers, search providers and activities are not in this version.

### Activities

[ADR-0010](engineering/ADR-0010-activities.md) adds `io.github.mattonik.Flow1.Activities` to the Platform API (version 1.2) with `Start`, `Update` and `Finish`. An activity belongs to the bus connection that started it:
- It ends when finished or when that connection closes; there is no idle timeout.
- A caller may have three at once, and a Flatpak app counts once across its connections.
- Text is cut to one printable line; blank-looking characters and stacked combining marks are removed.

The service labels each activity with its owner, never with a name the app chose:
- **Flatpak app:** the app ID of its sandbox.
- **Unsandboxed caller:** its process name, or the program that ran the `flow` CLI.

The service publishes live activities to `$XDG_RUNTIME_DIR/flow-activities.json`, and the shell watches that file:
- The island shows the newest as a chip with the owner, the title, a progress fill and a count of the others. The chip fits the space the island has left on its screen and becomes a dot on narrow screens.
- Desk lists every activity under *Activities*.

The CLI adds two commands:
- `flow activity run TITLE -- COMMAND` holds an activity while a command runs. It forwards termination signals and always returns the command's own exit status.
- `flow activity progress TITLE` shows percentages read from standard input, one update a second at most. The last value always goes out, even without a final newline, and carriage-return progress such as rsync's works.

Neither command lets a refused or failed activity call stop the work it reports: standard input is still drained, so a pipeline never breaks.

`niri_activities_smoke.py` passed in the guest on a private bus and its own runtime directory, against a development shell:
- Progress went from 25% to 60% with a detail line and ended at end of input.
- A build showed while its command ran and ended with it.
- A caller was refused a fourth activity, and its three ended when it was killed.
- A service killed with SIGKILL, and a shell and service stopped normally, left no Niri event stream behind. The test is a child subreaper, so a leaked stream would be re-parented to it and caught.

Unit tests cover:
- the activity book: ownership, the per-app limit, text cleaning and owner labels
- the CLI through a stand-in for PyGObject: refused calls that are not fatal, a pipe that never breaks, the last line, carriage returns, non-numeric lines, exit statuses and forwarded signals

A NEMESIS review of the first version found the bugs these tests now pin down:
- The CLI's throttle ran at exactly the service's rate limit, so `flow activity progress` failed after about ten seconds and broke the pipe it was reading.
- `flow activity run` reported failure for any command over the ten-minute expiry, which has been removed.
- A Flatpak app could get around the limit with more connections, and could pick its own owner label.

Stopping the shell had leaked its `niri msg event-stream`, a bug older than Activities. The event streams of the shell and the service now run under `setpriv --pdeathsig`, so they end with their parent, and fall back to a plain stream without `setpriv`.

The same FlowProbe run covered activities from a Flatpak app. They showed its app ID as owner, though it asked for another owner. A fourth activity on a fourth connection was refused, one connection could not finish another's activity, and all three ended when the app exited.

Not tested: the narrow-screen dot outside the layout code.

### The prototype's Overview, home indicator and search (2026-11-21)

Flow used Niri's own Overview (a vertical zoom of workspaces), fuzzel for search and two buttons at the bottom; none of it looked like the prototype (`docs/Flow_v1.0_Contextual_Menus.html`). These are now Flow's own surfaces, styled from the tokens:
- **Overview** (Super+O, the home indicator, Desk's *Overview*, the Platform API's `ShowOverview`):
  - **Header:** "YOUR WORK / MAIN DISPLAY", **Overview.** and "Every space in its place."
  - **The row:** this display's spaces in one row, the selected one centred with a 2 px ring. Each card has a caption with its pin number or a dot, its name, and PINNED, GROUP or SPACE.
  - **Navigation:** half-pill rails at the screen's edges. A tool bar: *Open selected*, *Pin space* or *Unpin*, ← →, and *Your desk*. *Shortcuts* opens Niri's hotkey overlay.
  - **Keys:** Left/Right, Home/End and the wheel choose; Enter or a click returns; Up, or the home indicator again, goes on to Desk; Escape returns unchanged.
  - **The selection** is a space, not a position, so it stays put when a refresh replaces the list, for example when a title changes or a window opens.
  - **Card images:** Niri offers no capture of one window, but a space fills its display while focused. So each card shows the snapshot taken the last time its space was on screen, as a phone's app switcher does: `grim`, at the display's own resolution (scaling in `grim` cost more than the capture), decoded at 640 px wide, cropped exactly to below the Island and above the home indicator.
  - **When snapshots are taken:** only in the plain work view, and only when the focused space changes and has settled for 0.7 s; a title change is not a move. A settled space whose picture is under a minute old and was taken of the same state (its windows, titles, focus and shares, on the same display size) is not taken again (2026-11-23). Opening Overview takes a fresh one unless a move is still settling.
  - **Where snapshots are kept:** in `$XDG_RUNTIME_DIR/flow-snapshots` only, and never without a runtime directory. They are deleted when their space closes and when the shell starts or stops.
  - **Why not Qt's own capture:** Qt's `grabToImage` crashed Quickshell 0.3.2 on Niri, in every run that used it. Niri's screenshot action would fill the clipboard.
- **One surface at a time:**
  - Desk, search, the switcher, quick settings and notifications each close Overview.
  - Any of them opening while Overview is still taking its snapshot cancels it.
  - An error or a pin from Overview is a toast, not a Desk opened underneath.
  - The Desk gives search the keyboard, and the app menu and automatic layout never act under these surfaces.
  - Niri's top-left hot corner is off, because Overview is Flow's. Niri's live overview stays in the Spaces switcher, and the touchpad's four-finger swipe still opens it.
- **The home indicator:** a 48 × 4 line on a dark halo, so it reads over light and dark apps (65 px and accent on hover). It replaces the *Overview* and *Spaces* buttons.
  - It takes input in only the bottom 126 × 24, the prototype's size, because Flow's columns reach the screen's edge.
  - Tap it or drag it up: from work to Overview, from Overview to Desk, from Desk back to work.
  - The Spaces switcher moved to Desk (now its foot, *Spaces*) and stays on Super+Tab.
- **Search** (Super+K, Desk's search, the Island's *Apps*, which shows apps only):
  - **The palette:** a shade and a 620 px palette with an 18 px radius. The field has a search icon and a close button.
  - **Results:** each has an icon tile (the app's icon, or a line icon for its kind), a title, a detail line and ↗. The chosen row has a 2 px focus edge.
  - **Footer:** "Enter to open · ↑ ↓ to choose · Esc to close". Escape works from anywhere in the panel.
  - **Behaviour:** `flow-search --json` lists the results, the shell filters them as you type and runs the choice. Apps and files open through `flow-search --open`, in their own scope. See *Search on the installed system*.
  - **What is listed:** Flow settings, quick settings, pins (also other displays'), open windows, recent files and installed apps, each app also as a Layer. Apps without a desktop file are left out, since they cannot be opened by name.
  - **No fuzzel:** `flow-search`'s fuzzel mode was removed on 2026-11-21. On 2026-11-22 the unused `flow-apps` launcher, its themed wrapper and the generated fuzzel themes went too, and Flow no longer installs fuzzel.
- **Platform API:** `ShowOverview` opens Flow's Overview, and `ShowLauncher` opens the panel's app view. Both count an Overview that is still opening as open.

Tests:
- **`niri_prototype_surfaces_smoke.py`:** drives a development shell in the guest through its IPC calls. It checks that Overview opens on the focused space and keeps the chosen space when a window opens, that pinning from it keeps Overview without a Desk, that Overview again goes on to Desk, and that search opens over Desk. Desk during Overview's opening wins, ShowDesk then is busy, and Desk over an open Overview replaces it. With grim, a settled space gets a snapshot and Desk never does. Four mutations of the review's failure cases each fail it: the selection reset, the pending Overview, a pin opening Desk, and Desk over Overview.
- **`test_prototype_surfaces.py`:** checks the JSON results and the configuration.
- **Screenshots:** both appearances were captured in the guest with three apps.

Not verified: a touch drag on the home indicator, a physical wheel, and the Overview with several displays. A pointer's tap, swipe up and wheel notches have been checked since 2026-11-22 (see *Pointer in the guest tests*). On a second display it shows that display's spaces, but snapshots come from the island's screen.

### The prototype's Island and Desk (2026-11-21)

The Island and Desk now follow the prototype (`docs/Flow_v1.0_Contextual_Menus.html`, desktop mode) instead of the generic capsule and button rows:
- **The Island** is a 44 px tab hanging from the screen's top edge: square above, a 14 px radius below, no line along the top. It is the same in work, Overview and Desk, and has the same controls as before: *floe.*, *Apps*, an activity, the clock, notifications and quick settings.
  - Its buttons are 44 px tall, the brand's touch minimum (the prototype's tab is 36). They draw 36 px with a 10 px radius, concentric with the tab's lower corners.
  - The opt-in app-menu trigger shares the tab's row, flush with the top edge too. Overview's snapshots are cropped below that 44 px row.
  - Quick settings, notifications and, on a narrow screen, the app-menu trigger hang 12 px below it, as the prototype's panels do.
- **Desk:**
  - **Headline:** "Everything in its place." as display type, in DemiBold (600). It is 7.9% of the screen's width, between 40 and 116 px and at most 11% of the screen's height, and 44 px when Desk is narrower than 600 px. On a screen under 600 px tall the space around the search field shrinks too.
    - Lines are at 0.97 of the size. That is a fixed line height: a proportional one multiplies Noto Sans's own line gap.
    - Tracking is −5%, and "place." is in the accent.
  - **Headline font:** the tokens name Manrope as the brand face, but nixpkgs removed it ("its source has been pulled"). The brand guidelines rule out forcing a remote font download, so the headline uses the Noto Sans fallback. On 2026-11-21 it was decided to keep Noto Sans rather than fetch Manrope from Google Fonts.
  - **Eyebrow:** "FLOE / YOUR DESK", after a small accent light.
  - **Search field:** "Search anything, start anywhere…" with *Super K*, 59 px tall with a 15 px radius.
  - **Pinned spaces:** cards, two to a row (one per row in *Edit pins*, with its arrows and ×), with the pin number in the accent. A long display name in the section label is cut short with an ellipsis.
  - **Tools:** four tiles, 80 px tall: *Search*, *Apps*, *Files* and *Settings*. On a screen narrower than about 750 px (content under 700 px), the pins and tools stack.
  - **The foot:** what is on this display ("3 spaces · 2 pinned · 1 Group"), then the ways on: *Overview*, *Spaces* (the switcher), *? Shortcuts* (Niri's hotkey overlay) and *Return to work*. They are quiet, link-like 44 px buttons that hover and focus reveal. On a narrow screen (content under 560 px) the summary takes a line of its own above them.
  - **Notifications** left Desk's tools: the Island's bell, which Desk also shows, opens them.
  - **No overlaps:** Desk's content stays between the Island's 44 px band and the home indicator's 44 px band, and never scrolls under either one. Their input would otherwise take the foot's clicks.
  - **Glass:** the field, cards and tiles share a new `glass` button variant: a 4% wash of the text colour (8% under the pointer), and an edge at half the border's strength that turns to the accent under the pointer. See the accessibility audit for why the edge may be faint.
  - **Backdrop:** the prototype's ambient light, a soft accent glow above the headline and a fainter one low on the right, over a faint 46 px grid. It is decoration and takes no input.

Verified:
- **Screenshots** in the guest, with two pinned spaces:
  - at 1280 × 800 in both appearances, also with no pins;
  - at output scales 1.25, 1.6 and 2 (logical 1024 × 640, 800 × 500 and 640 × 400), each scrolled to the top and to the end;
  - at 2.4 (533 × 333), for the foot's narrow layout.

  Nothing overlaps at any of them. On the short screens Desk scrolls, and its content stops at the Island's and the home indicator's bands.
- **Source checks** in `test_desk_layout.py`, scoped to Desk's window: its Island, the four tools and the four foot entries, the handler each one runs, and that the content keeps clear of the Island's and home indicator's bands. Each assertion was shown to fail when the code it covers was changed.
- **The existing smokes** pass. They drive the shell over IPC and never render Desk, so they cover none of this layout.

Not verified: hover and pressed states of Desk's buttons. Of Desk, the guest's virtual pointer (2026-11-22, see *Pointer in the guest tests*) has so far tapped only its home indicator.

### The prototype's system panels (2026-11-21)

Quick settings and notifications now follow the prototype's Island panel (`hub-panel` in `docs/Flow_v1.0_Contextual_Menus.html`). Both are one component, `SystemPanel`:
- **The panel:** it hangs 12 px below the Island, 370 px wide (less on a narrow screen), with a 19 px radius and 23 px inside. It has a title, any tools, and a close button whose glyph lines up with the notifications' dismiss glyphs. It drops in over 220 ms, or at once when motion is reduced.
- **Height:** it is capped 80 px short of the screen and scrolls beyond that. Tab scrolls the focused control into view, as on Desk.
- **Keyboard:** a shown panel holds the keyboard; Desk underneath gives it up for either panel (before this, only for notifications and search). Escape and the close button ask the panel to close; each panel clears only its own flag.
- **Rows:** a label, a value at the right and a chevron when the row opens something, over a hairline. A row that only shows a value (*Sound*) is plain text, not a button.

**Quick settings:**
- **Rows:** *Network* shows the connectivity (in the success colour when connected) and opens the Wi-Fi chooser. *Edit connections* opens the connection editor. *On-screen keyboard* shows or hides it.
- **Sound:** the level, then a volume slider and *Mute*.
  - The slider replaces the − 5% and + 5% buttons. It can be dragged, tapped, or moved with the arrow keys, 5% a step.
  - It follows changes made elsewhere, except while it is pressed, so a drag never jumps back to a PipeWire update that is a step behind. Its `Binding` restores nothing when it pauses: Qt 6's default would put back the value from when the `Binding` last took over, and a key press counts as a press, so the first arrow key would have set the volume to 5%.
- **Media and errors:** the media player row and any audio error follow, as before. The separate *Close* button is gone, since the header has one.

**Notifications:**
- **Order:** newest first, each with its app in small capitals, its summary (up to three lines), its body (plain text, up to six lines), its actions and a dismiss button. Actions without a label are not shown, since they would be blank buttons without a name; each action is at least 44 px wide.
- **Clearing:** *Clear all* in the header dismisses every one (*Clear* on a narrow panel, so the title keeps its room).
- **Empty:** a bell, "No notifications" and "They gather here, above every space."
- **The list is rebuilt on each change.** Newest first is a reversed copy of the server's list, so reading and Tab order match the screen. A notification arriving or going therefore loses keyboard focus inside the list. It holds at most 20.
- **Opening:** the shell's IPC gained `notifications`, which toggles the panel as `quick` does quick settings, for scripted checks.

Tests:
- **What they check:** `test_system_panels.py` and the rewritten `test_notifications.py` check the source text, because the harness cannot render QML or send input. They cover:
  - the panel's place, screen cap and scrolling, its keyboard focus, and Escape and close;
  - each panel clearing its own flag, and Desk giving up the keyboard;
  - the rows' commands and focus, the Sound row not being a button;
  - the slider's step, its setter, the bodies of `setVolume` and `toggleMute`, and its `Binding` with `RestoreNone`;
  - touch sizes, from each control's own properties: a width must be a plain number of at least 44, or `Math.max(44, …)`;
  - newest first, dismiss, *Clear all*, labelled and sized actions, and the empty state.
- **Mutations:** 31 mutations of the shell each fail one of the two files. Among them are the review's wiring cases: a scope that never takes focus, no layer-shell keyboard, rows or the slider without focus, no-op volume and mute bodies, a 50% step, a removed screen cap, a 30 px button width on its own line, unbounded and blank actions, and the restore mode removed.

Screenshots: both panels in the guest at 1280×800 in both appearances and at scale 2 (640×400), with three notifications (two with actions) and with none. The guest has no audio device, so the slider was shown with a temporary null sink created for the run, at a volume set with `wpctl`; it showed 65%, a change made outside the shell. The scale-2 shot also shows a row's hover tint, from where the pointer happened to rest.

A NEMESIS review of the first version found the slider's restore-mode bug, a false comment and test that ruled out its fix, tests that missed the wiring, a touch-size check that read only a button's first lines, a focus ring clipped by 1 px, no scrolling to the focused control, blank action buttons, Desk keeping the keyboard under quick settings, and stale docs. All are fixed as described above.

Not verified:
- **Slider input:** dragging, tapping or the arrow keys, because the guest has no pointer or key injection. The restore-mode fix rests on Qt 6's documented `Binding` behaviour.
- **Mute:** a click on *Mute*.
- **Long panels:** scrolling to the focused control was not tried with enough notifications to need it.

### Notification banners (2026-11-21)

Before this, a notification that arrived with its panel closed showed only as a dot on the Island's bell. Now a banner shows it, as a phone does:
- **What shows:** the newest notification, 12 px below the Island where the panels hang, in the panels' style. It shows the app in small capitals, the summary and the body (two lines each, plain text) and a dismiss button.
- **Replacements:** a notification that replaces an earlier one (`replaces_id`: a chat's next message, a download's progress) shows again. Quickshell updates the object in place without a new signal, so the shell listens for its summary and body changing.
- **How long:** 5 s of being seen.
  - The count runs only while the banner can be seen and the pointer is not resting on it, and each time it starts it starts from 5 s.
  - A critical notification stays until it is dismissed or opened, and only another critical one replaces it; anything else waits in the panel.
  - A low-urgency one never shows a banner and waits in the panel, with the bell's dot.
  - The notification's own expiry time is ignored; it governs the notification, not the banner.
- **Where it shows:** only in the plain work view, as the work indicator. Desk, Overview and the switcher would cover it (the surface mapped later is on top), and the panels, search and the app menu sit where it does. While any of them is open the banner waits, without counting, and shows when it closes.
- **Opening the panel:** this takes the banner, so it doesn't come back. A notification that arrives with the panel open shows no banner.
- **Clicking:** a click runs the notification's default action, as freedesktop clients expect (a chat opens its conversation), or else opens the panel. × dismisses the notification.
- **Keyboard:** the banner never takes the keyboard, so a notification cannot pull typing away from an app. The new Super+N opens the panel, which also takes the banner. The banner is announced as an alert, with the body as its description.
- **Display:** the banner shows on the display niri chooses, normally the focused one; neither it nor the Island names a screen.
- **IPC:** `banner` returns the summary (or app name) the banner is set to show, or "" when it is hidden. It reports the shell's state, not what the compositor draws.

Tests:
- **`niri_notification_banner_smoke.py`:** drives a development shell on a private bus (`dbus-run-session`), sending notifications with `busctl` and reading the banner's state through the IPC. It checks:
  - a normal notification goes after 5.4 s, and a low-urgency one shows nothing;
  - a newer one replaces the shown one, and one sent with `replaces_id` shows again;
  - one the app closes leaves at once;
  - quick settings hides a banner, which shows when quick settings closes;
  - Desk hides one for 6 s, and it then shows with its time unspent;
  - one that arrives with the panel open shows nothing after;
  - a critical one stays past 9 s, a normal one doesn't push it off, and the panel takes it for good.
- **Mutations:** thirteen mutations each fail the smoke, at the step that covers them:
  - no banner, low urgency shown, a banner shown while the panel is open;
  - no timeout, a 7 s timeout, a critical one timing out, a normal one evicting a critical one;
  - the timer counting while hidden, replacements ignored, the panel not taking the banner;
  - a banner over Desk or over quick settings, and a newer notification not replacing the shown one.
  
  An earlier version also checked that the notification was still tracked. Removing that check broke nothing: a notification the app closes is destroyed, which empties the banner's property. So the check was dropped.
- **`test_notification_banner.py`:** checks the source for what the smoke can't see or reach:
  - the banner never takes the keyboard, and Super+N is bound;
  - the exact set of conditions that hide it, in any order;
  - it sits where the panels sit;
  - the dismiss button is 44 px and calls `dismiss()`;
  - the card has the alert role;
  - new and replaced notifications both reach `showBanner`, and a click runs the default action.
  
  Eleven mutations of the shell and the Niri config were checked against it. Ten fail it, among them an extra `|| root.deskOpen`, quick settings dropped from `workViewOnly`, a scaled or dead dismiss, a hidden card, a missing `showBanner` call, an ignored default action, and Super+N removed. The eleventh, a harmless reordering of the conditions, passes, as it should.

A NEMESIS review of the first version found that replaced notifications never showed, that a normal notification pushed a critical banner off, that the timer ran while the banner was hidden or held, that the banner ignored Desk, Overview, the switcher and the app menu, no keyboard path outside Desk, no default action on click, false claims about the IPC and the display, and weak tests. All are fixed as described above.

Not verified:
- **Pointer:** a physical one. A virtual pointer has clicked the banner, run its default action, held it and dismissed it since 2026-11-22 (see *Pointer in the guest tests*).
- **Screen reader:** the announcement was not heard with a running screen reader.
- **Super+N:** not pressed, because the guest has no key injection; the binding passes `niri validate`.
- **Several displays:** not tried.

### Search on the installed system (2026-11-21)

On an installed Flow, search never worked. Opening it showed the old fuzzel picker over the shell's panel; the panel said "Nothing matches “”" over its own footer, and choosing anything opened nothing. Screenshots: `docs/screenshots/2026-11-21-search`.

Causes, and fixes:
- **The installed `flow-search` dropped its arguments.** Its wrapper ran `search.py` without `"$@"`, so the shell's `flow-search --json` started the fuzzel picker instead of printing results. The wrapper now forwards them.
- **`gio` was not on the installed shell's PATH.** The shell opened apps and files with `gio launch` and `gio open`, which failed silently. They now open through `flow-search --open app|file VALUE`, whose wrapper puts glib on PATH as `flow-spaces` and `flow-recovery` do.
- **The fuzzel mode is gone.** Nothing but the shell called `flow-search`, and the fallback is what put a second picker on screen. Called otherwise, it prints its usage.
- **The guest tests could not see either.** They ran the shell with their own `flow-search` wrapper and glib on PATH. `checks.module` now runs the installed wrapper itself: `--json` must print results, and `--open` must find gio. Without `"$@"`, or without glib, the check fails.
- **Ranking:** an exact title now ranks first, and ties keep `search.py`'s order. Qt's JavaScript sort is not stable, so typing "Foot" put "Foot Client · Open as Layer" first, and Return opened nothing.
- **Messages:** with no results the message has its own space above the footer. A `flow-search` that does not print a list, or never starts, says that search failed, not "Nothing matches". An app or file that fails to open shows a toast naming it, also when `flow-search` cannot start: each open is a process of its own, so two quick opens cannot hide one's failure.
- **Icons:** the missing icons were the fuzzel picker's, which shows text only. With results reaching the panel, apps show their theme icons (14 of 17 results in the guest).

Tests:
- **`niri_search_smoke.py` (guest):** drives a development shell with a stand-in `flow-search` and types with wtype. It checks the listed results, the ranking and Return opening the Notes app through `--open`, the no-match message staying clear of the footer, the failure toast and the failed-search message, and both again with a `flow-search` that cannot start. Mutations each fail it: the unstable sort, no exact rank, `gio launch` from the shell again, no toast, no failed flag, the old empty-state layout, and the two start-failure paths.
- **`checks.module` (CI):** fails with the wrapper's `"$@"` or its glib PATH removed, both shown in the guest.
- **Installed configuration:** this branch, activated in the guest without a new generation, listed 17 results with 14 app icons and no fuzzel. Typing "Foot" and pressing Return opened a Foot window in its own scope. The system was then switched back to generation 26.
- **Unit tests:** `test_search.py` covers `--json`, `--open` and its failures.

The wtype-driven tests had a latent flaw, fixed in both `niri_search_smoke.py` and `niri_focus_walk_smoke.py`. Niri does not resend a virtual keymap equal to the last one, so a run that stopped right after its first typed call left the next run's shell reading keys with the physical keymap. Each run now starts with a keymap no other call sends.

A NEMESIS review found that a `flow-search` that could not start was still silent, both listing and opening; that a second open during a slow first lost its failure; and that the icon handling was guesswork no app in the guest needed. All are fixed as above; the speculative icon code was removed.

Not verified: opening a recent file from the installed shell, and icons given as absolute paths (no app in the guest uses one).

Reopening search while a slow listing was still running showed that listing's results; fixed on 2026-11-22 (*Search opened again during a slow listing*).

### Commands in search (2026-11-22)

Search lists commands after the apps, before the same apps as Layers, so they come up by typing:
- **Flow's actions**, run by the shell: *Open Desk*, *Overview*, *Notifications*, *Pin current space* and *Save session*. Save session shares one function with Desk's button and waits for automatic recovery or the current action the same way; from search, Desk is closed, so its result is a toast.
- **The system's**, run by `flow-search --open command ID`: *Lock screen* (`flow-lock -f`, Flow's lock screen as Super+L starts it, now also on PATH), *Suspend*, *Log out* (Niri's quit), *Restart* and *Power off*. Each runs on its own, as apps and files open, and a failure shows a toast such as "Couldn’t restart". The IDs map to fixed commands; any other is refused.
- **Asking first:** *Log out*, *Restart* and *Power off* lose unsaved work, so they run only when chosen twice within 5 s. The first Enter or click arms the row, which then says "Press Enter or click again to restart". A second choice within 500 ms of arming (a double-click, or Enter pressed twice at once) counts as the same choice, and a held key's repeats are ignored. Typing, choosing another row or closing search disarms it; the armed row moving down as results arrive does not.

The focused app's menu commands are not listed. That menu is opt-in, off by default, slow on its first walk (about 0.9 s) and turns on accessibility for the whole session, so search does not depend on it; a separate plan for the menu is under way.

Tests:
- **`test_search.py`:** every command is listed, only the three that end the session or stop the computer ask first, each system command runs its fixed command outside any app scope with no stream of the shell's, and an unknown ID runs nothing.
- **`niri_search_smoke.py` (guest):** the first Return on *Restart* arms its row and runs nothing; after 5 s it is disarmed, so the next Return only arms it again; a second Return within them runs `flow-search --open command restart` (the stand-in only records it). Two Returns 50 ms apart, and a double-click with the pointer, only arm it. Choosing another row and back, or closing search, disarms it. *Lock screen* runs on one Return, *Open Desk* opens Desk, and *Save session* shows its result as a toast. Four mutations each fail it at their step: no asking first, an arming that never lapses, no 500 ms guard (Restart ran on the second of two quick Returns) and no disarming on another row. The Save session step found a real bug on its first run: the new function shared its name with Desk's button, whose id won, so search's Save session did nothing.

A NEMESIS review found the double-click and double-Enter hole, the disarm left untested, *Lock screen* depending on the idle daemon (`loginctl lock-session` exits 0 even when nothing catches it), the "Couldn’t open Restart" wording, Save session's silent result, and a disarm when the armed row merely moved. All are fixed as above.

Screenshots: `docs/screenshots/2026-11-22-search-commands`.

Not covered: the system commands themselves in the guest (they would lock, suspend or restart the test machine, and the lock needs a password to leave), a held Enter's repeats (wtype cannot hold a key), why a command failed (its error output is not kept), and touch.

### Files found by name (2026-11-22)

The user chose how search finds files: a walk of folders on demand, nothing indexed or stored; the XDG user folders only; hidden files and folders included; names only, with contents perhaps later as an option.
- **The folders:** `flow-search --files` walks Desktop, Documents, Downloads, Pictures, Music and Videos, as `user-dirs.dirs` names them or by their default names, wherever they are (a Documents kept on another disk included). Never the root, the home folder or a folder holding it: xdg-user-dirs turns a folder off by setting it to the home folder. A user folder inside another is walked once, with the outer one.
- **The walk:** level by level across the folders, Desktop, Documents and Downloads first within each level, alphabetically within a folder. It returns files and folders whose names hold every word of the query, ignoring case as the shell's ranking does.
- **It stays inside them:** symbolic links are neither listed nor followed, and the walk does not enter another file system mounted below a user folder (`/proc/self/mountinfo`). A network mount that hangs cannot hang it, and a bind mount cannot lead out. A user folder that is itself a hung mount still would.
- **Bounds:** 50 results, 0.3 s and 8 levels below the folder it walks. The clock is checked while a folder is read too, so a huge one cannot hold the walk past its time; what the time leaves unvisited is not found. On a synthetic home in the guest (12,000 photos, 12,000 tracks, a Downloads with 100,000 files), a file four levels down in Documents was found, and a query with no match ended, both in 0.14 to 0.17 s including Python's start.
- **The query goes on stdin,** not the command line, which every user of the machine can read.
- **In the shell:** typing three letters or more starts a walk 150 ms after the last key. A walk for an older query is stopped as soon as the query changes, and an answer it still gives is dropped. Found files come after the other results, less the recent files already listed, and open as recent files do. A query's words match a title in any order. Not in the apps-only view.
- **The chosen row stays chosen:** results now arrive while the user looks at them, so the chosen row is kept by identity for the same query, and the list scrolls to keep it in sight. Before, every change chose the first row again, and Return could open a row other than the one shown as chosen. A new query still chooses the first row.
- **Contents later:** names are matched in one function, `name_matches`, where an opt-in content search could join without changing the walk.

Tests:
- **`test_search.py`:** which user folders are walked (another disk, the root and the home folder, one inside another in either order, one that is not a folder), hidden files found, links and mounts not entered, unreadable files left out, the order level by level and regardless of case, every word, the shortest query, each bound, the clock while reading, and `--files` reading its query from stdin. Each of these, neutralised, fails a test.
- **`checks.module` (CI):** the installed `flow-search --files` finds a file in a hidden folder under Documents.
- **`niri_search_smoke.py` (guest):** typing walks for the query, given on stdin (never fewer than three letters), and the found file is listed once, though it is also a recent file, and opens with Return. A slow walk's answer for an older query, a file since deleted, never shows, even from a walk that ignores being stopped. A row chosen with the arrow keys stays chosen when a file found later ranks above it. Four mutations of the shell each fail it: taking every walk's answer, choosing the first row on every change (Return opened Notes, not the chosen Notes Client), never walking, and listing a recent file twice.

A NEMESIS review led to the rework: a shared cap on entries looked at, which used the budget up level by level and hid shallow files, was replaced by the time bound alone. The clock was checked only between folders, mounts were entered, older walks were never stopped (so an answer could slip in within the 150 ms before the next walk), the query was on the command line, and a user folder outside the home folder was skipped.

Screenshots: `docs/screenshots/2026-11-22-search-files`.

Not covered: stopping an older walk at once, rather than when the next one starts 150 ms later (nothing measures that window), the list scrolling to the kept row, a real user's folders, and file contents.

### Dissolve Group with columns after it (2026-11-22)

The user asked for *Dissolve Group* to shift other columns too, as *Rejoin Group* and recovery now may. Dissolving a stacked Group refused while a pinned app's window followed it ("Move pinned spaces left before dissolving this Group"). Now the Group's apps get columns of their own as before, and the columns after them, pinned ones included, move right in the same order; this display's pinned spaces go back in pin order at the next refresh. A Group with pinned apps still has to be unpinned first, and a side-by-side Group on the workspace still stops it.

Since this display's pinned spaces lead the strip, a pinned window after an unpinned Group is mostly another display's pin, such as one whose monitor was unplugged; that is the case the old rule refused.

Each step is checked first, as *Rejoin Group* checks its steps. Niri's action splits a window out of a shared column, but joins a window that is alone to the next column, which may now be a pinned space. So right before each step the app must still share the Group's column, or dissolving stops and the Group stays saved. At the end the apps must sit side by side in stack order. Niri has no expel-only action by ID, so a change in the milliseconds between that check and the step can still join a window to the next column, as it always could for unpinned columns.

Tests:
- **`test_groups.py`:** a Group with a pinned window after it dissolves with one expel and is no longer saved; the old rule, put back, fails it. An app that is alone before its step is not acted on, and apps that end up apart are not confirmed; each check, removed, fails its test.
- **`niri_group_recovery_smoke.py` (guest):** the pinned app's pin is moved to another display, so it stays after the unpinned Group; *Dissolve Group* gives each app a column of its own with the pinned window after them, and once the pin is this display's again, the next refresh puts it first. With the old rule put back, the dissolve is refused with its message.

A NEMESIS review found the step's race could now reach a pinned column, a test assertion that checked only its own mock, and the order the final check left unconfirmed. All are fixed or stated as above.

### Recovery beside windows opened at login (2026-11-22)

The user asked for automatic recovery to confirm the saved order with windows opened at login. To confirm it, every tiled window on the workspace had to be one of the saved session's, in its saved column. An app opened at login, before recovery runs, is not, so recovery reported *Pinned spaces keep their positions; session order needs manual recovery* (or, with a side-by-side Group, *Side-by-side Group order needs manual recovery*), and automatic recovery failed and paused saving.

Now a window that is not in the saved session is a space of its own:
- **Its own column is left out.** The saved windows must be in their saved columns and rows counted without it, so before, between or after the saved columns, it does not stop the order from being confirmed. A column that mixes it with saved windows is not the saved column, and a tiled window with no place in the strip confirms nothing.
- **Wherever the order is confirmed:** with a pinned app on the workspace, with a side-by-side Group, and with pin order modelled. A pinned app that maps while recovery works, as one opened at login can, is judged the same way rather than always failing.
- **Between a side-by-side Group's apps** it parts the Group: the report now says *Side-by-side Group order needs manual recovery: another window is between its apps; choose Repair Group layout*, not that the Group is unavailable.
- **Between saved columns, by the user's choice (2026-11-22):** a window between saved columns is accepted where order is confirmed (with a pin or a side-by-side Group), while without a pin recovery packs the saved windows to the front. Nothing is lost either way; automatic saving resumes with what is on screen. Accepting such windows only before or after the saved columns was offered; the user kept this.

Tests:
- **`test_restore.py`:** the order is confirmed with a column of an unsaved window before, between or after the saved columns, and not with one stacked into a saved column or one with no place. A side-by-side Group with a window opened at login before it gets its saved split; with one between its apps, the report points to the repair and nothing is resized. A pinned app mapping during recovery in a column of its own leaves the order confirmed; stacked into the saved window's column, it does not. Six older tests that expected the order note for a window not in the session were updated to the rule.
- **Neutralised, each fails tests:** the strict check (nine tests), the note between the halves, judging a late pin by the order check, and the check for a window with no place (two each).
- **`niri_group_recovery_smoke.py` (guest):** in the simulated restart, an app is opened before recovery runs, as at login. Automatic recovery opens the saved apps after it, rebuilds the pinned Group and succeeds; the next refresh puts the pins first. With the strict check put back, automatic recovery fails there with the order note.

A NEMESIS review found the wrong note between a side-by-side Group's apps, the side-by-side path untested, a late pin still failing outright, and doc claims that did not hold. All are fixed as above.

### Recovery rebuilds a pinned Group as Rejoin Group does (2026-11-22)

The user asked for automatic recovery to rebuild a pinned Group the same way as *Rejoin Group*. Recovery rebuilt one only when no window followed its apps but those it had just opened. Three changes make it the same:
- **Columns after it:** any columns after the Group's apps may now close up as they join one column, in the same order. The apps must still be next to each other, in saved or reversed order; otherwise the report says *Pinned Group apps are apart or out of order*. The set of windows recovery opened, which only the old rule needed, is gone. Dissolving a Group kept its rule until it, too, was changed (see *Dissolve Group with columns after it*).
- **The same checked steps:** recovery used to fire every join from one snapshot and check only at the end. Now it joins the apps through the loop *Rejoin Group* uses (`spaces.stack_columns`), which checks after each step that the column holds exactly the apps joined so far, on the same workspace, and that the rest are still single windows. A step that did not land stops it with *Group members moved unexpectedly*, before the next; any other error, such as a changed workspace or changed Flow state, stops recovery as before.
- **Pin order counts as saved order:** with a pinned app on the workspace, recovery reports *Pinned spaces keep their positions; session order needs manual recovery* unless the columns are in their saved order, and automatic recovery treats any such note as a failure and pauses saving. An order that the next refresh's pin ordering will bring back now counts as the saved one. `spaces.pin_ordered` models `reconcile` for that: it takes only this display's pins, tells Groups by their bindings in this Niri session as a refresh does, and changes nothing while a side-by-side Group pauses pin ordering. So when a refresh had moved a pinned app ahead of the Group's apps, automatic recovery now rebuilds the Group and succeeds. A window that is not in the saved session, such as one opened at login, no longer stops it either (see *Recovery beside windows opened at login*).

Tests:
- **`test_restore.py`:** the exact report for each case. A window already open after the Group, and a user's window mapping during recovery, both close up behind the rebuilt Group; neither is in the saved session (since the next change, a column of its own no longer leaves the order note). After a pinned app was moved ahead, the order pin ordering brings back is accepted with no note, but not when that pin belongs to another display. A join step that did not land, or a later app that is no longer a single window, stops the join before the next step; a workspace switch during the join stops recovery.
- **`test_larger_groups.py`:** `pin_ordered` gives the order `reconcile` leaves, run on a fake Niri: a pinned app moved ahead, a Group bound in this session to windows that have closed (a refresh does not see it), and a side-by-side Group pausing it. *Rejoin Group* stops at a step that did not land, and at a workspace switch, before the next step.
- **Neutralised, each fails a test:** the old rule (the first two restore cases), the session binding in the model, the display's pins, the narrow catch, the workspace check in the join, the single-window check, and the pin-order check.
- **`niri_group_recovery_smoke.py` (guest):** after the rejoin step, the session is saved and the pinned Group's apps are moved apart (a refresh moves the pinned app ahead) with a saved app open after them. Automatic recovery (`auto-start`) rebuilds the Group and succeeds; the next refresh puts the pins back in order and the Group pin reaches the Group. Without the pin-order check, automatic recovery fails there with the order note.

Two NEMESIS reviews shaped this. The first found the first version left automatic recovery failing in every case it tested, joined without rejoin's step checks while calling it the same, and claimed failure proofs that were false. The second found the pin-order model blind to session bindings and to which display a pin belongs (two probes reported success for orders a refresh would not produce), and a catch that swallowed a changed Flow state. All are fixed as above.

### Rejoin Group for a pinned Group (2026-11-22)

The user asked for *Rejoin Group* to work for a pinned Group too. It used to refuse while any column followed the Group's apps ("Move the pinned Group apps to the end before rejoining"), or a pinned app did ("Move other pinned spaces left before rejoining"). So after reopening a pinned Group's apps with recovery off, or with any app opened after them, the card in the switcher offered nothing.

Both rules are gone for rejoining. The user asks for it, so the columns after the Group may close up as its apps join one column (by one column for each app joined). They keep their order. Pins store an order, not columns, so the next refresh puts pinned spaces back in pin order, once no side-by-side Group or unfinished layout change pauses that, and for this display's pins. A pin that claims some of the Group's apps but not as this Group still stops it, and so do apps out of order, apart or on another workspace. Dissolving a Group since may move the columns after it too (see *Dissolve Group with columns after it*); session recovery since rebuilds a pinned Group the same way as Rejoin Group (see *Recovery rebuilds a pinned Group as Rejoin Group does*).

Tests:
- **`test_spaces.py`:** a pinned Group's apps apart, with an app and a pinned app after them, are offered *Rejoin Group* and stacked, and the Group pin reaches both windows. Either old rule, put back, fails it.
- **`test_larger_groups.py`:** a pinned three-app Group with two columns after it is rejoined step by step (each step checked with the columns closing up), and its pin reaches all three windows. The first old rule, put back, fails it.
- **`niri_group_recovery_smoke.py` (guest):** after recovery, the pinned Group's apps are moved apart with an unpinned app after them. The switcher offers *Rejoin Group*, which stacks them; the pins go back in order and the Group pin reaches the Group. With the old rule put back, the step fails with its message. A second step does the same with another display's pinned app still after them, and a third with this display's while a side-by-side Group pauses pin ordering. A fourth moves the pinned Group behind that Group during the pause and checks the pins go back in order once it is stacked, and again once it is dissolved while side by side. Before the dissolving, a change to side by side, killed with its apps split, holds them out of order and stays paused once finished; a change back to a stack, killed with its apps joined, holds them until finishing it puts them back. Before the dissolving, a change to side by side killed while its widths settle finishes at its saved split.

Screenshots: `docs/screenshots/2026-11-22-rejoin-pinned-group`, the switcher's card before (the refusal) and after (*Rejoin Group*).

A NEMESIS review found the first wording claimed the old rules protected nothing, while dissolving and recovery keep the same rules, and that the doc claimed more than the tests show. The reason is now the one above.

A pinned app after the Group at the time of the rejoin was covered by unit tests only, since this display's pin ordering moves it ahead first. Since 2026-11-22 the guest test covers another display's pinned app there: its pin stays where it is, so it follows the pinned Group's apps when *Rejoin Group* runs, which stacks them; the columns after close up in order and the Group pin reaches the Group. With the old "Move other pinned spaces left" rule put back only where the rejoin runs, that step fails while the earlier ones pass. This display's own pinned app is kept after the Group only while pin ordering is paused (otherwise only until the next refresh); a later step covers that too, after checking the pin is this display's: a second Group, side by side after the pinned app, pauses it, and *Rejoin Group* stacks the pinned Group's apps with the pinned app still after them, leaving the side-by-side Group whole. Against the old rule brought back for this display's pins only, the earlier rejoins pass and that step fails. The guest already saw a pause end when the pinned Group, changed to side by side and back, let the next refresh put the app pin first. The next step covers the pause ending by an unpinned side-by-side Group's own change: with the pinned Group moved behind it, a refresh leaves both pins out of order. Stacking that Group puts the pinned Group and the pinned app back first, in pin order, since a layout change ends with a refresh. Dissolving it while side by side moves nothing, since dissolving does not order pins; the next refresh puts them back. With a pause that outlives an unpinned side-by-side Group that started it (narrowed so, because the earlier check fails a pause that outlives the pinned Group), every earlier step passes and the stacking fails; with the side-by-side Group's record kept on dissolving, the dissolving fails. A pause from an unfinished layout change had no test that ran in CI: with it removed, every unit test passed. `test_spaces.py` now refreshes with a stacked Group's change to side by side unfinished, its apps already split and a pinned app behind them, and nothing moves; finished side by side it still pauses, and with neither the pinned app goes first. The guest covers the same with Niri and a real interruption: it kills `flow-spaces` during the change to side by side, once the apps are split and before their widths are set, which leaves the pending layout saved and the stored layout still a stack. With the pinned Group moved behind them, a refresh leaves the pins out of order and offers *Finish layout change*. Finishing puts the Group side by side, so the pins stay paused until the Group goes back to a stack. With the unfinished change no longer pausing pin ordering, the unit test fails, and in the guest every earlier step passes and that refresh puts the pinned Group first. The guest then kills a change back to a stack once the apps are joined, before the stack's full width is set (the measured split and the pending stack saved, the stored layout still side by side). A refresh leaves the pins out of order (held by the stored side-by-side layout; the pending layout alone holds it only in the change to side by side) and offers *Finish layout change*. Finishing it gives the stack its full width and puts the pinned Group and the pinned app back first at once, since a finished change ends with a refresh. If finishing a change to a stack arranges the windows but never saves the result, the guest step fails there and so does `test_resume_finishes_transitions_already_applied_and_both_directions`. If it saves the result without the stack's full width, the guest step's width check fails.

The kill (since 2026-11-23; these steps simulated the interruption before) is a `niri` ahead on the helper's PATH that kills its caller when asked for `set-window-width`, so every earlier Niri action is done and that one is never sent; the test requires the helper to have died by that kill. Since the interruption is the helper's own, the guest also shows that the pending layout is saved before that action: with it kept in memory until the finished layout is saved, the step to side by side fails at its first check, where the simulation, which wrote the pending layout itself, passed. That the pending layout is saved before the first Niri action is pinned by unit tests (`test_change_layout_persists_pending_before_action_and_completes_both_directions`, `test_action_failure_keeps_pending_registry_for_resume`, `test_interrupted_layout_change_resumes_from_a_partial_arrangement`); saved after the joins, it passes the guest. Against the real kill, the three mutants above fail as before: the unfinished change no longer pausing pin ordering, finishing a change to a stack never saving it, and finishing it without the full width. Not covered: an interruption at another action.

Since 2026-11-24 the guest also kills a change to side by side while the widths settle. That is the pause after the last width is sent, in which the apps take their widths before the helper measures them and saves the layout. This time the held `niri` sends the second width, then kills its caller once the helper sleeps (its kernel wait shows `nanosleep`; a socket read shows another). A saved 25/75 split marks the widths sent. Each tile is checked against the output's width, since new columns here are full width and u's 25% beside v's 100% is a 0.20 share of the two. After the kill both apps reach their widths, the pending layout is still saved and the stored layout is still a stack. *Finish layout change* is offered and puts the Group side by side at 25/75, recognised as a side-by-side space. Finishing sends the widths again, so those last checks show that finishing works from this state, not what the kill left.

Mutants against this step:
- **The layout saved before the widths settle (after the last width is sent):** the step fails at the check that the change was left unfinished. So does the unit test `test_size_constraints_leave_transition_pending`, which already pinned this; what the step adds is real Niri and a real kill at that point. Killed inside the last width request instead (an earlier version of this step), this mutant passed, since the save comes after it.
- **The kill at the first width, before any is sent:** fails the widths check.
- **The kill at the second width, before it is sent:** fails the widths check. The share-of-the-two check that came first would have passed it (0.20, within 0.05 of 0.25); a NEMESIS review found that.

Not caught by the guest: the layout saved after the first pause but before the measuring, since the kill lands in that first pause. The unit test catches it.

### A Group recovered after its apps closed (2026-11-22)

Recovery was tested only with the Group's apps still open and moved apart. With every app closed, as a reboot leaves them, it failed: recovery opened all three apps again, then refused the pinned Group with *Pinned Group cannot be restored without shifting other spaces*. (Since then any columns after a pinned Group may close up; see *Recovery rebuilds a pinned Group as Rejoin Group does*.) It opens the apps in saved order, so the pinned app beside the Group opened after the Group's members. Joining the members into one column shifts every column after it, and the rule against that protects columns the user placed. A window that recovery itself opened (one that was not there before it opened that app) was never placed, so it no longer counts.

`niri_group_recovery_smoke.py` (new) simulates a restart without ending the guest's session. It closes every app, marks the saved session, the Groups' window bindings and the pins as from an earlier Niri session, and recovers through `flow-recovery auto-start`, as the shell does when a session starts. That call reports success only when every window was restored. In the guest it passes:
- **A pinned, renamed stacked Group beside a pinned app:** recovery opened all three again and rebuilt the Group as one column in saved member order. It kept its ID and name, its pin and the app's pin reached the new windows, and the next refresh bound the Group to them. The recovered Group then changed to side by side and back, and dissolved with its apps open.
- **A side-by-side Group with a 70/30 split:** recovered with every app closed, with its columns, its split and its binding, then stacked and dissolved.

Without the change, the guest test fails at the first recovery with that message, and so does the new unit test `test_pinned_group_recovery_shifts_only_windows_it_opened`. `test_pinned_group_recovery_still_keeps_a_window_that_was_open_after_it` covers a user's window mapping while recovery opens the apps. That case is still refused; a rule that refused only a tail with no recovery-opened window at all fails it.

A NEMESIS review led to the mixed-tail test, to counting only windows that were not there before their app was opened, to recovering through `auto-start` with the pins marked too, and to calling the restart simulated.

Still open for F-005:
- **A real reboot or Niri restart:** simulated here.
- **The manual path:** done since; see *Rejoin Group for a pinned Group*.
- **Other apps:** apps that start slowly (recovery waits 15 s for each), open several windows, or only activate an existing instance.
- **Elsewhere:** a Group on another workspace or display, and touch.
### Shell restart during a Flow action (2026-11-22)

Every Flow action the shell starts (pin, Group change, Layer, recovery) is one `flow-spaces` or `flow-recovery` process. What happens to it when the shell restarts:
- **It outlives the shell, on the installed system.** Quickshell 0.3.2 leaves its child running when it gets SIGTERM or SIGKILL, and the `flow-shell` unit has `KillMode=process`, so `systemctl restart` (as a NixOS switch does) stops only the shell. This rests on Quickshell not cleaning up on SIGTERM. A live reload does destroy the Process, and Quickshell then kills the action (seen in the guest); only development shells reload, since the installed shell turns file watching off.
- **Its closed pipes no longer stop it.** The action's stdout and stderr are pipes to the dead shell, and Python raises on a write to a closed pipe. One write part-way through, such as a progress line on stderr, left a Group layout change pending. Both helpers now keep stdout and write it once at the end, and drop a write to stderr that fails (`session/orphan_io.py`).
- **The new shell reads the finished state.** A `flow-spaces` action holds `session.lock` from start to end, so the new shell's first refresh waits for it. `flow-recovery restore` takes the locks again for each app it opens. Between apps the new shell can act, and the restore then stops with *Session, Groups or pinned spaces changed* rather than undoing that.
- **What is lost:** the old shell's queue of actions not yet started, and the result of the one in flight. A refused action (for example "Select the Group first") changes nothing and says so to the dead shell; the new shell shows the state as it is. Keeping the last error for the next shell was not built.

The shell gained two IPC calls for the test: `group_layout` (what the Group's layout button does) and the read-only `spaces_view` (the pins, spaces and Layers the shell last read).

`niri_shell_restart_smoke.py` now ends with four restarts during an action the shell started: a Group layout change with SIGTERM, a pin with SIGKILL, a Layer opening with SIGTERM, and the layout change back with SIGKILL. The test holds `session.lock` until the shell has stopped, so the action is in flight, then releases it, so the action runs with its pipes closed while a new shell starts (its startup refresh, automatic layout and recovery). Each must finish whole (the Group's layout saved without `pending_layout` and its columns matching, the pin saved, the Layer floating), and the new shell must show it. In the guest all four pass. Three mutations show what the test sees:
- **A progress line on stderr during the layout change, without the pipe handling:** the change was left pending. With it, the same line passes.
- **Actions started under `setpriv --pdeathsig KILL`:** stopping the shell ended them.
- **`spaces.py` without the pipe handling:** `test_orphan_io.py` fails. That test runs each helper with nobody reading its output.

On the installed system (generation 32), `systemctl --user restart flow-shell` ran while the installed shell's own `flow-spaces refresh` waited on the real `session.lock`. The old process stayed alive in the unit's cgroup and finished once the lock was released.

A NEMESIS review found that the first version of the test killed the shell while the action waited at the lock, and released it only after the new shell had started, so the action never overlapped the start. It also found the pipe hazard left to the test to police, a refresh racing the action, and "by design" claimed where Quickshell's teardown is what decides. All are fixed as above.

Not covered: a restart while `flow-recovery restore` opens apps, a development shell reloading during an action, and the lost error.

### Under a stream of compositor events (2026-11-22)

The shell coalesces the refreshes Niri's events ask for: 150 ms after the last event, at most 1 s after the first unanswered one, and at least 0.5 s after the previous refresh. A window whose only change is its title is relabelled from the event, with no `flow-spaces` run. The new title shows at once, then at most once a second. `flow-spaces` reads Niri over one socket connection, and the helpers run compiled bytecode from the store, so `refresh` and `observe` dropped from about 66 ms to 22 ms. `flow-search` observes instead of refreshing. The opt-in app menu asks for the focused app's identity when focus moves, not every 1.5 s. Automatic layout runs when a Group does not suit its display's orientation, not every 2 s.

`tests/niri_performance_smoke.py` measures it in the guest. Main failed 10 of its 17 checks:

- At idle it ran 30 `flow-spaces` and 40 `flow-appmenu` in 60 s.
- A title changing every 0.3 s caused 37 refreshes, 32% of a core in children.
- Under faster titles, and under fast focus changes, the shell's state was fresh only every 2 s. That was the automatic-layout poll, and only because a shaped Layer was open.

The 2026-11-22 change passes all 17. With each fix neutralised in turn, the smoke fails. The numbers, the method and the review that shaped it are in [Performance](performance.md#under-a-stream-of-compositor-events-2026-11-22).

### Startup, many spaces, frames and HiDPI (2026-11-22)

`tests/niri_scale_probe.py` measures what phase 3 of the performance plan asked of real hardware, on whatever runs it: startup, idle frames and wakeups, the shell with 5 and 24 spaces, the frames each surface draws as it opens and closes (from Qt's own render-loop log), and Overview's snapshot. Only the UTM guest has run it. There, idle draws nothing and the shell wakes less than once a second; 24 spaces stay within every budget, and with Overview open at 4K the shell held 180 MB, as on main. On a 3840 × 2160 display at scale 2, Overview took 347–414 ms against its 200 ms budget, because `grim` scaling the snapshot down cost twice the capture. Snapshots are now taken at the display's resolution and decoded small: 179–218 ms. The shell also connects to its controller when the controller says it listens, not on a 250 ms timer (now a one-second fallback), so startup shows every space 110–150 ms sooner. Quickshell builds a layer-shell window afresh each time it shows; that costs the render threads 10–23 ms per opening in the guest and was left alone. Details: [Performance](performance.md#startup-many-spaces-frames-and-hidpi-2026-11-22).

### Background work: snapshots and automatic saving (2026-11-23)

Two jobs repeated while nothing changed. A settled space was taken again on every arrival, at 16 ms of `grim` at 1280 × 800 and 87 ms at 4K. With automatic recovery on, `flow-recovery auto-save` ran every 30 s, at about 59 ms of CPU each: 0.2% of a core, about what the idle shell itself spends. Now a settled space is taken again only when its picture is over a minute old or of another look of the space. Automatic saving is now the resident controller's, in its own process: 2 s after a refresh Niri's events or an action asked for, never sooner than 30 s after the previous save, and without writing a snapshot that has not changed. A save costs about 1 ms of CPU instead of 57 ms, and no process. `tests/niri_background_smoke.py` measures both in the guest. Main fails two of its nine checks: 5 snapshots on a second walk through unchanged spaces, and 2 saves in 65 s with nothing changed. This change passes all nine, and each fix, neutralised, fails its own check. Details are in [Performance](performance.md#background-work-snapshots-and-automatic-saving-2026-11-23) and [its next section](performance.md#automatic-saving-in-the-controller-2026-11-23).

### Hidden surfaces and the busy indicator (2026-11-22)

An activity without a known amount kept the shell at a fifth of a core for as long as it lasted, even under Overview: its breathing dot advanced on every frame at the guest's 120 Hz. The shared busy clock now runs only while a busy indicator is on screen: an island whose dot breathes in a window that is shown (the work island, Overview's or Desk's), or Desk's sweeping bar. It steps every 80 ms for dots and every 16 ms for the bar, by elapsed time. That is 2.7–3.1% of a core with the island showing the activity, and 4.7–4.9% in Overview, which shows its own island.

Desk, Overview, the switcher and the Notifications panel no longer build their lists while hidden, so a state push rebuilds only what is on screen. Under focus changes every 0.25 s the shell's CPU went from 6.2–7.1% to 1.1–1.2%. Overview builds its cards during the snapshot it takes before showing. Its first frame shows every card's snapshot whether it opens from the work view or from search (screenshots in `docs/screenshots/2026-11-22-overview-first-frame`). The windows were not moved into `LazyLoader`s: that would save at most 11 MB of 175 while they are closed, and rebuild whole windows on every open. Details: [Performance](performance.md#hidden-surfaces-and-the-busy-indicator-2026-11-22).

### A resident controller (2026-11-22)

`flow-controller` holds Flow's state for the session ([ADR-0011](engineering/ADR-0011-resident-controller.md)). The shell starts it. It reads Niri's event stream from Niri's socket into the ADR-0004 projection, refreshes Flow's state in-process when the events ask for it (about 6 ms, where a `flow-spaces` process took about 20), and sends it to the shell and the Platform API. The shell has no event stream of its own and starts `flow-spaces` only for actions. The API reads state and `Changed` from the controller, and falls back to `flow-spaces observe` and its own stream when no shell runs. Under focus changing every 0.25 s the helpers' CPU fell from 8.5% of a core to 1.1%. Every guest smoke that can run in the guest passed with it, the Flow API smoke through both of the API's paths. The two-display smoke needs a second active output, which the guest lacks. A real reboot was not tried. Numbers: [Performance](performance.md#a-resident-controller-2026-11-22).

### Search opened again during a slow listing (2026-11-22)

Each opening of search lists the results again, since windows may have closed. There was one listing process, and an opening while it still ran started none, so the panel showed that listing's answer: what was there before. The shell now starts a listing per opening. A listing still running gets SIGTERM, and its answer is dropped: only the newest is shown. A stopped listing's output ends at once, empty, which without the check would have said that search failed while the new one was still looking. SIGTERM stops `search.py`, not the `flow-spaces` it started, which finishes under `spaces.lock`; the new listing's waits for it. Since 2026-11-22 that is `flow-spaces observe`, which only reads (see *Under a stream of compositor events*); it was a `refresh`, which could reconcile pins and write state.

`niri_search_smoke.py` has a step for it. Its stand-in `flow-search` answers with the rows as they were when it started, after a delay. Search opens during a 4 s listing of a window since closed, closes, and opens again while the new rows take 2 s. Every view in the next 6 s must say *Looking…* or show the new rows, and only the new listing may finish. The step runs again with an old listing that ignores SIGTERM and answers after the new one: its answer must be dropped too. In the guest both pass, and three mutations of the shell each fail: the old single listing (the closed window came back), taking every listing's answer (the late answer replaced the new one), and not stopping the old listing (two finished).

A NEMESIS review found the first version's lifecycle bookkeeping built for an event order Quickshell 0.3.2 does not have (its `onFinished` always ends the stream before `running` turns false), and its docs claiming the whole listing stops. The lifecycle is now one handler and the docs say what SIGTERM stops. Not covered: that a finished listing's Process is destroyed; nothing counts them, so a leak would not fail the test.

### Moving along a row in Overview (2026-11-21)

Niri has no binds that apply only in Overview, so plain arrow keys cannot move through a display's row of spaces there. What does:
- **Super+Left/Right:** Flow's space navigation keeps Overview open. In the guest, with three apps in a row, it moved from the third to the second, then the first.
- **Super+wheel, in every direction:** a step along the row. Down and right go to the next space, up and left to the previous; a display's spaces are one row, so vertical scrolling has nothing else to do.
  - Each notch goes through the running shell's `navigate` IPC call, which drops a notch while a step is running or queued, so a fast spin never queues moves that keep going after the wheel stops.
  - Niri's documentation says Overview takes Mod+wheel binds without the modifier held, and Flow's Mod is Super, as on a TTY. So a plain wheel should step there too. That is unverified, because the guest has no input injection. Niri 26.04 validated the binds.
- **A touchpad:** it scrolls Overview with Niri's own gestures.
- **The work indicator:** it stays visible in Overview, with its arrows and position (02 / 05), for a pointer or a finger. With a Layer focused, Overview shows the arrows instead of the Layer's actions.

`test_overview_navigation.py` checks that every wheel direction is bound and enabled, goes through the shell with a short cooldown, and that a notch is dropped while a step is in progress. It also checks that the indicator and both arrows stay in Overview; seven mutations each fail it.

### Shell visual polish (2026-11-21)

The shell's controls follow one system instead of one bordered rectangle:
- **Buttons** come in four variants: secondary, primary, quiet and danger, plus plain inside cards. A selected button has a leading dot, a 2 px border, a heavier label and "Current" as its accessible description, so selection never depends on tint alone.
- **Borders and targets:** functional borders stay at full token strength (3:1), except Desk's glass controls (see *The prototype's Island and Desk* below). Quiet buttons rest on a soft tint, so they read as controls on touch screens without hover. Every target is still at least 44 px.
- **Focus** is a 2 px ring 3 px outside the control, concentric with it. Clipped panels keep 4 px inside the clip, so rings stay whole.
- **Capsules:** the work indicator is a capsule bar; its 44 px buttons draw 36 px capsules, concentric with the bar. (The island was one too, until it became a tab: see *The prototype's Island and Desk* below.)
- **Radii** are on the token scale: cards 20, panels 14, controls 10, with in-card controls concentric with their card.
- **Type** follows the scale (11/12/14/16/18/32/52), with 13 px kept for compact labels in the island and cards, and weights 500–600. Desk's display headline is the one exception, sized to the screen (see below). Clock, percentages and counts use tabular figures.
- **Icons:** line icons for notifications, quick settings, search, chevrons, close, plus and minus are drawn by the shell. They use multisampled geometry because curve-rendered strokes break up under virgl. Only buttons with a glyph load one.
- **Motion** uses the brand's quick (120 ms) and standard (220 ms) tokens and its standard curve. A *Reduce motion* setting in Desk makes every change instant, because Linux gives Quickshell no system signal for it. All "busy" indicators share one clock, so a card rebuilt by an activity update continues its sweep.

Surface by surface:
- **Niri and the lock screen follow the appearance switch** (audit UI-04). The theme generator writes `design/generated/niri-{dark,light}.kdl` and `swaylock-{dark,light}.conf`, so `session/niri.kdl` holds no colour of its own:
  - **Niri colours:**
    - the focus colour for the active ring
    - the functional border for the inactive one, which marks the focused window of an unfocused display, where focus returns
    - the canvas behind workspaces
    - the selected-surface tone for the Overview backdrop: calm and distinct (1.6:1 against the workspaces in Polar Night, 1.1:1 in Arctic Daylight). A 3:1 backdrop needed the functional border, a mid sea-grey that broke Polar Night's near-black, and it was rejected on 2026-11-21. Workspaces are told apart by their windows and a workspace shadow that the generated files pin, coloured from the appearance's ink. This is a deliberate exception to the audit's 3:1 rule for targets, also listed in `design/ACCESSIBILITY_AUDIT.md`.
  - **How Niri follows the switch:**
    - `niri.kdl` includes Polar Night, then optionally `~/.config/flow/niri-appearance.kdl`.
    - `flow-niri-appearance` (`session/flow-niri-appearance.sh`) keeps that a symlink to `/etc/flow/niri-<mode>.kdl`. It reads the mode under a lock when it runs.
    - The installed shell runs it at startup and on the Desk switch. A development shell never does.
    - Because it is a link, it follows upgrades and rollbacks: a generation without the target leaves a dangling link, which the optional include treats as missing. Niri reloads when the link changes.
  - **The lock screen:** `flow-lock` picks the matching swaylock file:
    - the canvas, with a quiet ring
    - the accent while typing and checking, danger for a wrong password, warning for Caps Lock
    - a themed keyboard-layout label

    A user's own swaylock configuration takes precedence. The colours are no longer on the command line. swayidle runs its commands through `sh`, where a word starting with `#` begins a comment, so a token colour there would silently have stopped locking.
  - **A user's own Niri configuration:** `~/.config/niri/config.kdl` replaces Flow's, so the switch only reaches it if it adds `include optional=true "~/.config/flow/niri-appearance.kdl"`.
  - **Tests:** `test_compositor_appearance.py` covers:
    - the generated files, role by role and for contrast
    - that every swaylock option exists in swaylock 1.8.6, which stops reading a configuration at an unknown line
    - the include order and path, and that the installed shell syncs at startup and on the switch
    - the sync script run for real in a temporary home, including leftovers and an old copy
    - the lock wrapper with a stand-in swaylock

    Thirteen mutations of the wiring each fail it.
  - **In the guest (generation 17):**
    - Niri validated the configuration, and the shell set the link at startup.
    - With the appearance file set to light and the shell restarted, the link moved and Niri switched to the light colours. Setting it back to dark did the reverse.
    - The setting was restored byte for byte.
  - **Not exercised:** the lock screen itself, because unlocking needs a password typed.
  - **Not yet effective:** swayidle is a Niri startup command, so its new lock command applies from the next sign-in.
  - **Focus ring:** one colour cannot stand out against both light and dark app content.
  - **CI:** it evaluates no Nix, so the module is only checked when the guest builds it.
- **fuzzel** (the old app launcher and search) was themed from the same tokens too; that went with `flow-apps` on 2026-11-22.
- **Desk:** a larger search field, pins as cards, activities as cards with a progress track, and tools sized to their labels.
- **Switcher:** a rounded bottom sheet. Each space is a card, and a Group's actions sit in a two-column grid with *Dissolve* as the danger action.
- **Quick settings, notifications and the toast** are rounded panels.
- **Narrow screens:** the work indicator rises above the bottom controls instead of overlapping them, below 560 px, or below 900 px when a Layer is active. That overlap predates this pass. The Layer label is dropped, and Layer controls wrap.

Screenshots were taken before and after in the guest with a development shell, on an isolated runtime directory and state, with the installed shell paused and respawned. They cover the work view, switcher, Desk, Desk settings, quick settings and a focused Layer at 1280×800 in both themes, and the work view, switcher, Desk and a Layer at 320×512 portrait. The output's transform and scale were restored. All eleven guest smokes passed against the polished shell, and real state files were unchanged.

A NEMESIS review of the first version found these problems, which the second version fixes:
- Desk's busy bar restarted on every activity update.
- Borders dropped below 3:1 through reduced opacity.
- Selection was shown by tint alone, and buttons that open panels were marked "Current".
- Focus rings were clipped in panels.
- Labels were cut off, and an accessible name did not contain its visible label.
- The Layer's Close lost its word.
- Radii and type were off the token scale.
- The sheet had a drag handle without a gesture.
- The Mute button kept its label once audio was muted.
- The shared busy clock ticked while nothing visible used it.

**Keyboard and screen reader (2026-11-21).** `tests/niri_focus_walk_smoke.py` checks the shell's keyboard focus and what it tells screen readers. `tests/run_focus_walk_smoke.sh` runs it from a checkout, in a Niri session where Flow is installed and its shell stopped. It drives the keyboard with `wtype`, through Niri's virtual-keyboard protocol, and takes screenshots with `grim`, which leaves the clipboard alone. Flow's module installs `grim` but not `wtype`. In the UTM guest, the machine configuration (`/etc/floe-test`, outside this repository) gained `wtype` in generation 20 and `grim` in generation 21, because the guest's installed Flow predates the module's `grim`. Those generations are kept as guest tooling.

**What it opens.** On the output's empty workspace it opens its own Foot window and six more, two of them stacked in one column, so the switcher has six cards in a scrolling row, one with two members. It sends eight notifications, which overflow their panel. Then it opens, in turn:
- Desk, without and then with its settings;
- Overview, where Tab goes through the Island and the tools (the cards are chosen with the arrow keys);
- search, where focus starts in the field;
- the Spaces switcher, first with the test's window as the current space, then with it as a Layer;
- Quick settings;
- Notifications.

**What it checks on each surface:**
- **Tab and Shift+Tab** go round every enabled control. The test decides which controls Tab must reach, not the shell: every enabled control, except a list the test names (Overview's edge rails), which must stay out of the Tab order. Each step must land on the next control in reading order that is enabled at that moment: rows top to bottom, with a switcher card's controls as one block. Once the card row scrolls, its "‹" button becomes enabled. If the controls change during a walk, the walk fails.
- **The focus ring** surrounds the focused control in the `focus` token, and no other control has one. Screenshot pixels are sampled where the design puts the ring, which the test holds: 3 px outside a button, 1 px inside an Island pill, 1 px outside a switch's indicator, at a text field's edge. Surfaces are placed from the anchors and margins the shell reports.
- **Return and keypad Enter** activate the focused control: they open Desk settings, toggle a switch both ways, move Overview's selection, close the switcher, search and Quick settings, and dismiss a notification.
- **Escape, or Return or Enter on Close,** shuts the surface. Keys then reach the app and focus is back on it, and no key reached it while the surface was open.

**Over AT-SPI.** Accessibility is switched on the way a screen reader does it, through `org.a11y.Status`, not forced in Qt. On every visible surface, including the Island and the work indicator, the smoke checks that:
- each button and switch has a name containing the text it shows (WCAG 2.5.3). Names are compared as normalised strings from a word start, so "Pin" is not found in "Unpin", but a label the shell cuts short still matches;
- selected controls, and cards that show "Current", are described as "Current";
- buttons whose panel is showing are described as "Open";
- activity chips in the Island and in Desk are progress bars;
- every other button it exposes (Overview's cards, search results, the home indicator) has a name.

The shell's read-only `controls` IPC call reports what each control shows. Qt exposes only controls in view, so a control scrolled out of view is checked when it takes focus. `test_focus_walk_helpers.py` checks the smoke's own judgements in CI: label matching, reading order and screenshot decoding.

**Defects it found, now fixed:**
- **No accessibility bus.** The Flow module did not enable at-spi2-core, so on an installed Flow no screen reader could reach the shell or apps, nor could the opt-in app menu. The module now enables it, and CI's `checks.module` asserts the evaluated setting, so another module that forces it off fails the build.
- **Return did nothing on a switch.** Qt's Switch toggles only on Space. Now Return and Enter flip it once, even when the key is held.
- **Clipped or hidden focus.** A control scrolled into view sat flush with the clipped edge, so its ring was cut off (Save session at the bottom of Desk). Quick settings and Notifications never scrolled to the focused control at all. One `reveal` now scrolls every view around a control that takes focus from the keyboard, keeping 8 px clear: Desk, the switcher and its row of cards, and both panels. It replaces some 60 per-control handlers, main's new Desk included.
- **A focused unnamed panel.** Quick settings and Notifications scroll in a ScrollView, a focus scope that reported focus along with the control inside it. Screen readers now skip it.
- **Focus dropped by background work.** Session recovery and most switcher actions were disabled while `flow-spaces` ran, including the periodic refresh (every 30 s, and after Niri events) and automatic layout (then every 2 s while a Group asked for it; since 2026-11-22 only when a Group does not suit its display), and while automatic recovery ran. A focused control that turns disabled loses keyboard focus. Neither now disables them:
  - space actions queue behind a refresh;
  - `flow-recovery` waits for `flow-spaces` on `spaces.lock`;
  - space actions also queue behind automatic layout and automatic recovery, and run when they end;
  - Save session, Restore session and the recovery switch refuse while automatic recovery runs and say so (the switch flips back);
  - renaming a Group no longer refuses during a refresh.
- **Names without their visible label:**
  - the Desk wordmark ("Return to work" for "floe.");
  - Desk's search button;
  - the volume steps;
  - Make Layer;
  - Make space and Add to Group on Layers, in the switcher and the work indicator;
  - Overview's Open selected and Pin space;
  - the appearance switch, whose label changed with its state. It now always reads "Arctic Daylight · Light".

  Names in states the smoke does not reach were fixed the same way but are not checked:
  - Pin here and Make this the main display;
  - a Group's window buttons, now named by the app they show;
  - the Group card and repair actions.

  Add to Group on a card now shows the whole app name, elided, instead of a 24-character cut that the name could not contain.
- **"Current" missing.** The current space's card showed "Current" but did not report it to assistive technology.
- **Search field:** it showed no focus at all. It now draws the 2 px ring at its edge, as the Group name field does.
- **Overview's edge rails:** they sat half off the screen, so their rings could not be whole. They are now for a pointer or a finger only, hidden from screen readers. The keyboard and screen readers have the arrow keys and the tools' ← →.

**Shown to fail.** The final smoke was run against copies of the shell, each with a line or two neutralised. Every copy failed, at the check that covers its change.

Thirty copies were cut from the shell at commit a2f6499. The shell's later changes touch none of the lines they neutralise: the recovery switch, queuing behind automatic work, automatic layout, and the Group name field's pointer guard. The thirty were:
- **Names (one copy):** eleven name fixes and the card's "Current" reverted together. All twelve were reported.
- **States:** "Open" dropped from open buttons, which predates this work (five reports), and separately "Current" dropped from selected ones.
- **Progress roles:** removed from the Island's chip, and separately from Desk's cards.
- **Switches:** Return doing nothing, and a switch that does not scroll itself into view.
- **Scrolling:**
  - no room kept for the ring: Desk's foot;
  - no sideways scrolling: the switcher's last card;
  - no scrolling in the Notifications panel: Tab then moves focus out of view, where AT-SPI does not expose it.
- **Unnamed panels:** the ScrollView of Quick settings, and separately of Notifications, exposed again.
- **Rings:** hidden, always on, in another colour, drawn inside the button, missing from the search field.
- **Tab:**
  - one Quick settings button taken out of the Tab order;
  - every button taken out;
  - an Overview rail put back in;
  - Return doing nothing on a button;
  - a Tab detour (`KeyNavigation.backtab`, which Qt mirrors onto the other control's Tab);
  - Desk's tools laid out right to left while Tab goes left to right.
- **Closing:** Escape doing nothing on each of the six surfaces, and Quick settings handing focus to another window as it closes.

Two more copies were cut from the final shell:
- **Shift+Tab only:** a detour on the backward step alone. It failed in the Shift+Tab walk.
- **Rails exposed:** Overview's rails visible to screen readers again. They were reported.

**CI tests and the module assert.** `test_focus_walk_helpers.py` was shown to fail when:
- labels matched inside words, `%` was dropped, or case was kept;
- a row was not sorted left to right, or items joined rows by their top;
- cards were ignored;
- the ring was sampled with a looser band, or a PNG filter was decoded wrongly.

The `checks.module` assert was built in the guest (aarch64). It passed on this branch, and failed at the assert both when the module's line was deleted and when the test configuration forced at-spi2-core off.

**Not shown to fail:**
- **Things the guest does not have or stage:**
  - Quick settings never overflows in the guest, so its scrolling is unproven;
  - keeping controls enabled through background work, and queuing behind it, would need races the smoke does not stage;
  - the held-key and pointer-focus guards need a held key and a pointer.
- **The leak check:** no key reaching the app while a surface is open. A mutation that lets keys through stops focus moving first.

**Guest check of the module (19:27 UTC).** A build of this branch, main's #19 included, was activated in the guest without a new generation. `org.a11y.Bus` was then activatable on the session bus, and the final smoke passed through `run_focus_walk_smoke.sh` with no extra bus configuration. The previous system was activated again afterward.

**After main's #21 (system panels).** #21 rebuilt Quick settings and Notifications as system panels with rows, a volume slider and their own scrolling to the focused control. After merging it:
- **Shell:** the panels' ScrollView is hidden from screen readers again, and the `controls` call reports the rows and the slider with their rings.
- **Smoke:** it holds their ring positions too, and accepts a hint as a description (the Network row's "Choose a Wi-Fi network") where no state applies.
- **Result:** the unmutated smoke passed on the merged shell. The mutations above ran before #21, so the copies aimed at the old panels (their ScrollViews, and scrolling in Notifications) prove the shell they ran on, not #21's panels. They were not rerun.

**Three quirks of the guest shape the smoke:**
- **Stale keymaps.** Niri does not resend a virtual keymap equal to the previous one, so a client that took focus in between reads the keys with the physical keymap (Tab arrives as Escape). The smoke sends a lone Shift or Control first, alternating, so every call's keymap differs.
- **Shift+Tab is Tab with Shift.** wtype's keymap has no ISO_Left_Tab, so Qt receives Tab with Shift held where a real keyboard sends Backtab. Qt's focus chain treats both as a step back, but a handler for Backtab alone would not see the smoke's key.
- **No audio.** PipeWire does not reach a shell with its own runtime directory, so the volume steps were disabled: their names were checked, but they were not walked.

Not verified:
- **A running screen reader:** the accessibility tree was checked, but Orca was not run, so speech and focus announcements were not heard.
- **The opt-in app menu** and Group card controls: the smoke does not open them.
- **Focus lost when content is rebuilt or a control disables itself:**
  - A window title that changes rebuilds the switcher's cards.
  - A space action started from the keyboard disables the cards until it ends. On Desk, Pin current space, Make this the main display, Pin here and the pin edit buttons disable themselves while their own action runs, and the pins are rebuilt, so reordering pins from the keyboard loses focus at every step.
  - A successful Group rename hides the rename row.
  - Overview's ← → and the switcher's ‹ › disable themselves at the row's ends.

  Each drops the focus of the control involved.
- **Touch and 200% text.**
- **Manrope:** the brand font is not installed in the guest, so the wordmark uses Noto Sans.

### Installed generation (2026-11-21)

The UTM guest now runs Flow from an installed NixOS generation built from `main`, not only from a development copy:
- **Build:** the guest's machine flake (`/etc/floe-test`, with Flow as a path input) built generation 5 from main at a416d04, and later generations 6 to 11 from this branch. Both flakes pin the same nixpkgs, so each build compiled only Flow's own derivations and downloaded nothing. The guest reaches the binary cache through the host network's TLS inspection, so builds pass `--option ssl-cert-file /etc/floe-test/host-trust.pem`. The guest copy of Flow from 18 November is kept as `/etc/floe-test/flow.prev-2026-11-18`.
- **Activation:** before each switch, `switch-to-configuration dry-activate` listed what would change. A switch reloads the D-Bus broker and restarts polkit, which leaves the session running. Generations 1 to 4 remain for rollback.
- **Installed API:** the installed `flow` CLI reported API version 1.2. Its first call started `flow-api.service` by D-Bus activation. An activity started by the installed CLI showed in the installed shell's island and ended at end of input. A switch that changes the service restarts it, as `switch-to-configuration` does for any changed user unit; apps' activities end with the old process.
- **First run:** the first run of the new `spaces.py` on the real state added display numbers (`"number": 0`) to `spaces.json`. That one-time upgrade is why the real `spaces.json` checksum changed. The one real pin was kept.

**The installed shell after a switch.** The first switch left the old shell running, for two reasons:
- **Reloads fail:** Quickshell 0.3.2 cannot reload a configuration that adds an inline component. The reload fails with "X is not a type", soft or hard, and the old configuration keeps running. Generation 5 added several inline components.
- **Reloads are unreliable:** its file watching noticed the generation 4 → 5 switch but not 5 → 6, so even a reload that would work may never happen.

So the shell is now the `flow-shell` user unit instead of a Niri `spawn-at-startup` command:
- **Lifecycle:** it is wanted by and part of `niri.service`, so it starts with a Niri session and stops with it, and never starts in the Plasma fallback. A Niri started some other way (from a TTY, or nested) gets no shell; Flow needs the `niri.service` session that `niri-session` starts.
- **Failures:** it restarts on failure two seconds apart, and gives up after five starts in a minute.
- **Environment:** its launcher takes `PATH` from the user manager, which holds the login session's environment because Niri imports it. Before this, a NixOS unit's own `PATH` would have dropped `~/.nix-profile/bin` and the like for every app opened from Desk or search.
- **Restarts on a switch:** its restart triggers are the shell's files (one store directory, also installed as `/etc/flow`) and the Quickshell package. A switch therefore restarts it when either changes, or when its launcher does (a change to coreutils, procps, gnused or systemd also counts), as `switch-to-configuration` does for any changed user unit. The installed shell turns Quickshell's file watching off, so it never half-reloads; a development shell keeps live reload.
- **Apps get their own scopes:** the unit sets `FLOW_APP_SCOPES=1`. Every launch path then starts the app with `systemd-run --user --scope`: the shell's own launches, search, and the `gio launch` of opening a Layer, a missing Group member or a restored session (`session/launching.py`). So apps are never part of the shell's unit, and stopping or restarting the shell, or a limit on it, never touches them. `KillMode=process` stays as a safeguard for any path that misses a scope. Development shells and tests launch directly.
- **First switch:** a switch does not start a new unit wanted by `niri.service`. Per-user activation therefore starts `flow-shell` while Niri runs, but only if the unit has never run under this user manager, so a user who stopped it to run a development shell keeps it stopped. The launcher first ends any older installed shell, matched by its command line with or without the store path, and waits up to five seconds for it to exit. That also ends a test shell someone started on `/etc/flow/shell.qml`.
- **Rolling back** to a generation from before this one stops the `flow-shell` unit, and that generation only starts its shell when Niri starts. Sign in again after such a rollback.

Checks in the guest, generations 8 to 11 and a cold start:
- The first switch to the unit replaced the shell Niri had spawned with one shell under `flow-shell.service`.
- A switch whose only change was one line in the guest's copy of `shell.qml` restarted the unit. The new shell answered IPC, and a Foot window opened through the shell (in the unit's cgroup) kept running.
- Switching back restarted it again, and re-activating the same generation left the same process running.
- After a clean power-off and cold start, automatic login started Niri, and `flow-shell.service` started the shell by its store path. IPC worked, and no system or user units failed.

The API smokes now ask the bus driver for the name owner's PID and require it to be the service they started. An installed Flow puts an activation file for `io.github.mattonik.Flow1` on every session bus, including `dbus-run-session`'s private one, so a call there starts the installed service. Also, `busctl list` lists activatable names, so the old "name is owned" check was always true.

Not covered:
- Two Niri sessions of one user: the launcher keeps one installed shell per user.
- A switch while Desk or a menu is open: that switch closes them, because the shell restarts.
- A real Quickshell upgrade.
- Startup's `watchFiles` switch has no automated test. A development copy that sets it unconditionally did not reload after an edit, while an unchanged copy did. The `/etc/flow` condition itself relies on Quickshell's `shellDir` not resolving symlinks; Quickshell 0.3.2 does not.

Limitations: An older generation cannot read a version 2 pin file and reports *Invalid saved spaces* without changing it. Restoring `spaces.v1.json` rolls back, but it holds the pins from before the first conversion, so pins made since are lost. An older generation also rejects a `groups.json` that contains a Group of three or four apps, which stops every `flow-spaces` call; dissolve or shrink such Groups to two apps before rolling back. Touch, physical rotation and real hotplug remain open.

### Pointer in the guest tests (2026-11-22)

The guest tests could type with wtype but not point, so every hover, click, swipe and wheel check above was marked *not verified*. They now have a pointer:
- **`flow-pointer`** (`tests/flow-pointer/flow-pointer.c`, the flake's `packages.aarch64-linux.flow-pointer`): a small client of Niri's `zwlr_virtual_pointer_v1`, as wtype is of its virtual keyboard. One call runs a gesture: absolute moves, press, release, click, wheel notches and waits, so a drag keeps its button held. Every step is checked before it connects, so a typo moves nothing, and a lost connection fails the call rather than passing as a move. It is test tooling: the module does not install it. CI builds it, and the build's own check runs nine bad calls and one good one without a display.
- **Why not wlrctl:** it can move, click and scroll, but cannot hold a button, and the home indicator's swipe needs one held. It also moves only relatively; Niri's mapping of absolute motion onto the layout of every output (the area `tests/pointer.py` computes from `niri msg outputs`) reaches any display directly.
- **First probe:** before `flow-pointer`, wlrctl's relative moves showed that Niri accepts a virtual pointer and does not accelerate it (`docs/screenshots/2026-11-22-pointer`). `flow-pointer` sends absolute positions only, so acceleration does not apply to it.
- **Targets:** the shell's new `pointer_target` IPC call reports where a named item is: its rectangle in its layer window, the window's size, anchors, margins and screen, and what the item shows of the pointer: a home indicator lit (full width, accent colour), a search row tinted, a banner held. That is the drawn state, not Qt's: a binding that stopped drawing the hover would fail the checks. `tests/pointer.py` turns the rectangle into a point on the layout, as the focus walk places its controls, and moves onto a target and waits for it to show the pointer before every click, so no click lands on a surface that is not on screen yet. A search row scrolled out of view reports no target. `test_pointer.py` covers that arithmetic, including a second screen and outputs left of and above the origin.
- **The pointer is parked** 8 px in from the layout's bottom left corner, where the shell has nothing to hover, when a test starts and when it exits, failed or not: the pointer stays where a gesture left it, across runs, and one left resting on a banner held the next run's first banner. A run that is killed cannot park, but the next one starts by parking.
- **The installed shell must be stopped** for these tests. Its home indicator, island and banners sit where a development shell's do, so a click could reach either; `Pointer()` refuses to start otherwise. The banner and prototype-surfaces tests did not need that before, and all three now need `flow-pointer` on PATH.

What the pointer now checks, each with a development shell in the guest:
- **Search (`niri_search_smoke.py`):** only the row under the pointer is tinted; a click on the third row opens that row, not the chosen first one, so a click is told apart from Return; a click on the shade outside the palette closes search and opens nothing.
- **Banners (`niri_notification_banner_smoke.py`):** the pointer resting on a banner, from 2.5 s into its 5 s, holds it for 7 s, and it goes 5 s after the pointer leaves, so its count starts again rather than resuming; a click runs the default action (`ActionInvoked`, read from a monitor on the test's private bus) and opens no panel, or opens the panel when there is none; × dismisses it (`NotificationClosed`, reason 2) without running the action.
- **Home indicator and Overview (`niri_prototype_surfaces_smoke.py`):** the home indicator lights under the pointer; a tap opens Overview, a tap on Overview's goes on to Desk, and a tap on Desk's returns to work; one wheel notch over Overview's row moves the selection one space down, and one back up, from the row's start, whose next space the keyboard step names (the row needs three spaces, so a notch that moved two would show); a swipe up opens Overview, and only Overview is open after the release.

Fifteen mutations of the shell were checked against these steps in the guest, after the review's fixes. Fourteen fail them, each at the step for what it breaks:
- **Search:** a click that runs the chosen row instead, rows that ignore hover, a row under the pointer drawn without its tint, and a shade that does not close.
- **Banners:** a banner the pointer does not hold, a count that resumes after the pointer leaves instead of starting again (it went 2.6 s later), a click that ignores the default action, and × that expires the notification (reason 1) instead of dismissing it.
- **Home indicator and Overview:** a tap that does nothing, no swipe, no hover, a line that does not widen under the pointer, a notch that moves two spaces, and the wheel backwards.

The fifteenth passes: `onClicked` also acting on the release after a swipe. The indicator's window hides once Overview opens, so the release never reaches it. In the first run, the click and × mutants failed for the wrong reason: the run before left the pointer on the banner, which held their first banner. Parking fixed that; rerun after it, they fail at their own steps. A first version of the resuming-count mutation shortened the count whenever the banner hid, and so failed an earlier step; the one counted above resumes only after the pointer held the banner.

Not covered: a physical mouse or touchpad (a touchpad's smooth scrolling, and libinput's acceleration, never reach a virtual pointer), touch, the pointer on a second display, hover and pressed states of other controls, and the cursor's shape. Running these tests needs `flow-pointer` on PATH; the guest's machine configuration installs it once Flow's `main` has it.

### Simulated Wi-Fi (2026-11-22)

Flow's part of Wi-Fi is Quick settings' *Network* row, which shows NetworkManager's connectivity and opens
`flow-wifi-settings`, and that wrapper, which starts GNOME's Wi-Fi panel. Networks, passwords and
connections are GNOME's panel, nm-applet (started by its XDG autostart entry, it is the session's secret
agent) and NetworkManager. `niri_wifi_hwsim_smoke.py` (run by `run_wifi_hwsim_smoke.sh`, see
[hardware testing](hardware-testing.md)) tests that path in the guest with no Wi-Fi hardware: the kernel's
`mac80211_hwsim` makes two radios, one runs a WPA2 access point (hostapd) in a network namespace of its own,
and NetworkManager uses the other. With a development shell on a private bus:
- NetworkManager found the test network as WPA2 on the simulated radio.
- The Network row showed *Connected* for NetworkManager's `full` connectivity. The guest's wired
  connection decides that, and the test leaves it alone, so the shell's `nmcli` is then a stub that answers
  each other state, an answer the shell does not know, and a failure; the row showed the right words for
  each (*Limited*, *Sign-in required*, *Offline*, *Unknown*, *Network unavailable*).
- A click on the row (the pointer) closed Quick settings and opened GNOME's Settings at its Wi-Fi panel,
  which listed the network under the simulated radio as a WPA2 network (read over AT-SPI).
- A profile with the right passphrase connected: the access point reported the station, a ping reached the
  access point over the radio, the default route stayed the wired one, and GNOME's panel showed the network
  as connected. Disconnecting ended it on both sides.
- A profile with a wrong passphrase was refused: the access point reported a passphrase mismatch, and the
  radio stopped waiting for a new passphrase (NetworkManager's *need authentication*). No window opened in
  the session.

The test runs this checkout's shell and this checkout's `flow-wifi-settings`, built from the module check.
Four mutations of Flow's part fail it, each at its own step: the row opening the connection editor
instead (GNOME's Settings never opens), the wrapper opening GNOME's Network panel instead of Wi-Fi (the
Wi-Fi panel never lists the network), the row showing *Online* for full connectivity, and the row's status
fixed at *Connected*. That last one passed the first version of the test, which looked only at the real
`full` state; a NEMESIS review found it, and the stub `nmcli` was added for it. The radios, namespace,
hostapd and profiles are removed after every run that ends by itself or by SIGTERM, SIGHUP or SIGINT;
after a SIGKILL, `niri_wifi_hwsim_smoke.py --cleanup` removes them and the development shell. The real profiles, devices, default
route and the session's windows are compared before and after. cfg80211, which hwsim loads, stays loaded.
A new wireless interface makes NixOS restart `wpa_supplicant`, so the test refuses to run beside another
Wi-Fi device.

Seen in its screenshots (`docs/screenshots/2026-11-22-wifi-hwsim`), not fixed here: GNOME's panel shows a
broken-image placeholder where its signal-strength icons belong, and the island covers the top of the
panel's title.

Not covered: real Wi-Fi hardware and drivers, roaming, enterprise (802.1X) networks, captive portals, and
connecting from GNOME's panel itself (the password prompt, which outside GNOME Shell comes from the secret
agent, nm-applet, and its re-prompt after a wrong password). The test connects through `nmcli`. The
development shell launches GNOME's panel directly, while the installed shell launches it in a systemd scope
(`FLOW_APP_SCOPES`); the scoped launch is not covered here. A USB dongle passed through from the Mac covers the first;
see [hardware testing](hardware-testing.md).

## What needs bare metal (2026-11-22)

The UTM guest can test what reaches it as software or as a USB device: a simulated Wi-Fi network, and USB
Wi-Fi and Bluetooth dongles, headsets and DisplayLink docks passed through from the Mac (see
[hardware testing](hardware-testing.md)). These stay untestable there:
- **Touch.** The guest has no touchscreen; UTM gives it a USB keyboard and pointing devices. A uinput
  touchscreen could feed Niri synthetic touches, but not a real digitizer's noise and palm contacts, its
  mapping onto a rotated or second display, or gestures at a real panel's rate.
- **Rotation sensors.** There is no accelerometer for iio-sensor-proxy to read. The rotation tests above
  set the output's transform through Niri, which skips the sensor, its debounce, and the order in which a
  real device reports a turn.
- **Alt-mode docks and real monitor identity.** USB-C alt-mode and Thunderbolt docks never reach the VM:
  their picture is not USB traffic. The guest's displays are virtio-gpu's `Virtual-1` and `Virtual-2`, with
  no real make, model or serial number, so per-display pins have never seen a real monitor's identity, two
  monitors of the same model, a monitor that reports itself differently on another port, or a KVM switch.
  The guest's hotplug is a virtual output disconnected through its DRM connector. A DisplayLink dock is the one real monitor the guest
  can get, through USB redirection.
- **GPU performance.** The guest renders with virgl over HVF, so its frame times say nothing about an
  Intel, AMD or Arm GPU. [Performance](performance.md) records the guest's numbers only;
  `tests/niri_scale_probe.py` is meant to be run on the first real machine.
- **Suspend and resume.** QEMU's guest suspend is not a laptop's firmware path: the lid switch, s2idle or
  S3, Wi-Fi and displays coming back, and the lock screen on resume are untested.
- **Battery and power.** There is no battery or backlight: low-battery behaviour, power profiles and
  brightness are untested.
- **The warm-reboot hang** (F-001): a warm reboot hung in UTM. Whether UTM, the guest's firmware or the
  session causes it needs a real machine to tell.
- **Physical input devices:** a touchpad's smooth scrolling and libinput's acceleration (see *Pointer in
  the guest tests*), and keyboard layouts and media keys from a real keyboard. A USB keyboard or mouse
  could be passed through, but the Mac's own touchpad cannot.
- **Built-in hardware a laptop has and a VM does not:** hybrid or discrete GPUs (PRIME, NVIDIA), a real
  panel's fractional scale, audio jack detection and HDMI or DisplayPort audio, internal PCIe Wi-Fi and
  Bluetooth (such as Intel's iwlwifi) as opposed to USB dongles, and built-in cameras.

**An x86-64 test machine.** Flow's module has nothing architecture-specific, but the flake only builds for
aarch64-linux: the evaluation fixture `nixosConfigurations.test`, `checks.aarch64-linux.*` and
`packages.aarch64-linux.flow-pointer`, and CI's module job runs on an Arm runner. A spare x86-64 laptop as
a second test machine would need:
- the flake's checks and `flow-pointer` for x86_64-linux too, and a CI job on an x86-64 runner that builds
  them;
- a machine flake like the guest's `/etc/floe-test`: its own `hardware-configuration.nix` and boot loader,
  its GPU's Mesa driver and firmware, NetworkManager, automatic login into the Niri session, a test user
  with passwordless `sudo` and SSH by key, Flow as a path input, and the test tools (`wtype`, `grim`,
  `flow-pointer`, `iw`, `hostapd`, `usbutils`);
- a way back in when a test breaks the session: a text console, or the Plasma fallback;
- the hardware the gaps above need: a 2-in-1 with a touchscreen and an accelerometer, a battery and a lid
  that Linux suspends with; two monitors of the same model and a USB-C alt-mode dock.
