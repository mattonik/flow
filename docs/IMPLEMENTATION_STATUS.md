# Implementation status against Flow Master Spec v1.1

Updated 2026-11-13. This is a repository and recorded-evidence audit, not a new full acceptance run. **Partial** means some relevant behavior exists but the whole journey is not established. **Open** means the acceptance journey has not been demonstrated. See [milestones](milestones.md) and [graphics validation](graphics-validation.md) for prior evidence and its limits. Recent targeted checks ran in the UTM guest; they do not validate every journey below.

The recorded test environment is an Apple Silicon Mac running UTM 5.0.8, QEMU ARM64 with HVF, 8 GiB RAM and virtio-ramfb-gl; NixOS 26.11 with Niri 26.04 and virgl acceleration. Exact host model and physical input details were not recorded. No journey below is promoted to a full pass solely from the HTML prototypes or from unit tests.

| ID | Status | Evidence and remaining acceptance gap |
|---|---|---|
| F-001 Native session | Partial | Niri, Quickshell, real apps and login were exercised; Plasma remains installed as a fallback. Warm reboot hung in UTM; fallback login was not newly verified. |
| F-002 Three real apps | Partial | Files, terminal, editors and browser ran in the guest. A single-workspace Niri IPC check focused running Firefox, Kate and Files in turn, then moved right and left between Files and Firefox and returned to Files. The complete navigation journey, including touch, remains to be checked. |
| F-003 Pinned positions | Partial | Single apps and saved Groups of two to four apps can be pinned and ordered as one column; each display keeps its own pin order (2026-11-20). Recovery rebuilt a pinned Group from adjacent end columns in either order, restoring saved member order while preserving its pin, focus and a neighboring space. Other missing or changed layouts can require manual recovery. A pinned three-app Group was recovered in the guest after its members were separated. Per-display ordering was exercised on two real Niri outputs in the guest. The complete restart journey remains open. |
| F-004 Shortcut accuracy | Partial | Displays have user-stable numbers: Main and External 1, 2, …, kept while a display is disconnected; the user can make another display Main. Super+number focuses Main's pins from any display, and holding Super+Alt, then pressing a display digit and a space digit, focuses that external display's pin directly, with an indicator, a timeout and feedback for unknown displays and spaces. Search lists other displays' pins and routes focus to their display first. The sequence was exercised through the shell's IPC on two real Niri outputs in the guest, and the bindings pass `niri validate`; physical key presses, keyboard layouts and remapping are not verified. |
| F-005 Group identity | Partial | Groups of two to four apps can be explicitly created; since 2026-11-20 they are bound to their windows within a Niri session (see *Groups beside other windows of their apps*), and automatic layout reflows them by orientation. Originally, two unambiguous apps could be explicitly grouped, named, pinned and dissolved using a registry independent of session snapshots. An isolated ARM64 guest test exercised migration and the Group lifecycle without changing the real snapshot; the existing guest Group also migrated. Manual stack/tabs remains available. The switcher can open an absent app and explicitly rejoin two adjacent, unambiguous single-window columns, including a pinned Group at the end of the strip. A manual side-by-side layout now appears as one switcher and keyboard-navigation destination; an ARM64 guest test preserved its pin, focus and neighboring pin across a stack → side-by-side → stack journey. If another app separates the two columns, the switcher can explicitly repair the Group when intervening columns are safe; recovery, touch navigation, automatic orientation reflow and the full lifecycle remain open. |
| F-006 Layer dismissal | Partial | A Layer is a compact floating window over the current space; floating windows are no longer counted as spaces (2026-11-20). Search can open any app as a Layer, and the switcher can make the focused standalone app one. In the guest, a Layer was opened over a pinned, resized side-by-side Group and closed again. The Group, its pin, every column and width, and the Flow state were unchanged, and focus returned to the same Group app. Portrait outputs get a bottom sheet, open Layers reshape when the output rotates, and a Layer can join the Group beneath it. Touch is not implemented. |
| F-007 Desk/Overview | Partial | In the ARM64 guest, opening and closing Desk and Niri Overview retained the same four window IDs, workspace, scrolling positions and focused window. At a simulated 320×512 logical portrait size, Group switcher keyboard navigation and pin editing remained visible; the original display and window state were restored. Gesture, physical touch, multi-display and visual-transition acceptance remain open. |
| F-008 Search | Partial | Search covers recent local files within the user’s home folder, Flow settings, quick settings, installed applications, open windows and saved pins, including closed apps. File indexing and commands are missing. The settings shell actions were checked through IPC. A recent-file result from an isolated history was selected in the guest and opened in Kate; naturally populated history and the settings results still need end-to-end validation. |
| F-009 Island/system | Partial | Island, quick settings, lock and a temporary notification inbox exist. Apps can show Activities with progress in the island through the Platform API (ADR-0010, 2026-11-21); they are labelled with their owner and end with the app's connection. Quick Settings opens GNOME's Wi-Fi panel and the connection-profile editor, and controls one unambiguous MPRIS player. The guest showed the no-adapter Wi-Fi state; real scanning/authentication, durable notification history and independent availability across all contexts remain unverified. |
| F-010 Real accounts | Open | Two-user and guest isolation and switching have not been verified. |
| F-011 Display hotplug | Partial | In the guest, a second virtual output was disconnected and reconnected through its DRM connector (2026-11-20). Its pins stayed with it and nothing on the other display was reordered, then the pins came back in order. A pinned three-app Group and a single app were moved between the two outputs as one destination each. Physical monitors, docks, connector changes and serial-number identity are unit-tested only. |
| F-012 Portal correctness | Partial | The guest's portal broker is active and owns its session D-Bus name. A disposable unsandboxed chooser request returned the selected test-file URI, and a second request returned a cancel response. Sandboxed file access, saving, screenshots, screenshare, clipboard and drag/drop remain unverified. |
| F-013 Audio/peripherals | Open | Volume control was exercised; a Bluetooth headset and microphone were not tested on physical hardware. |
| F-014 Figma day | Open | No recorded Figma workload, screenshare or browser-load test. |
| F-015 Development day | Open | Terminal and editors run, but the native-host development, container and VM workflow is not verified. |
| F-016 Touch day | Open | A manually invoked on-screen keyboard entered a letter, Shift and Enter into a guest terminal with a simulated pointer. Real touch, automatic text-field activation, rotation and tablet Group testing remain open. |
| F-017 API app test | Partial | Version 1.1 of the Flow Platform API (ADR-0009) is a session D-Bus service with a spaces interface scoped to the caller's own app and a control interface for unsandboxed callers (2026-11-21). Two non-Qt clients were used in the guest on a private bus: the Python/Gio `flow` CLI and `busctl`. A command run inside one app listed only its own part of that app's space and focused it while the app had focus. Its attempt to focus another app's space was refused as `NotAuthorized`. The packaged service also started by D-Bus activation. A real Flatpak app was not tested, because Flatpak is not installed in the guest; sandbox detection is unit-tested with a fake `/proc`. |
| F-018 Shell restart | Partial | `niri_shell_restart_smoke.py` (2026-11-20) started a development shell and restarted it with SIGTERM, then SIGKILL. It ran beside a pinned app, a pinned, resized side-by-side Group and an unpinned app, with isolated state. Pins, Groups, window IDs, columns, widths and focus were unchanged from before the first start, and Super+1 and Super+2 still reached both pins. A shell that moves focus on startup fails the test. A restart during a Flow action was not tested. The installed shell is the `flow-shell` user unit, restarted by a NixOS switch when its files or Quickshell change; see *Installed generation*. |
| F-019 DisplayLink hardware | Open | No named dock or monitor test. |
| F-020 App menu fallback | Partial | The opt-in AT-SPI experiment showed and invoked Kate commands; native menus are retained. Coverage, same-process ambiguity and the complete fallback journey remain unverified. |
| F-021 Sync conflict | Open | No sync provider or conflict-resolution journey. |

## Contextual-menu gap and next investigation

The shell listens to Niri focus and workspace active-window events and keeps an open application menu's context unchanged if focus moves or its window closes. It disables commands and asks the user to close the menu; the current app is re-identified afterward. Invocation also rechecks the active Niri window and command before calling AT-SPI. In the ARM64 VM, KWrite and Kate formed a real two-window stack; changing the active member while KWrite's menu was open retained its label, disabled commands and showed the warning. Dismissing it updated the label to Kate; Kate's New command created a document, and a stale KWrite command was rejected. Window closure, pointer focus, other toolkits and multi-display cases remain unverified. App commands are kept separate from the switcher's stack and rename actions. Two windows sharing a process intentionally fall back rather than being guessed. There is no independently running Flow Controller or versioned command-provider API yet; `flow-spaces` is a local experimental CLI, not the specified API.

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

In the switcher, each app in a side-by-side Group shows its share of the Group's width with *−* and *+* controls. A step moves 10% of the width to or from the next app (the previous one, for the last app) and never leaves an app below 15%; a step that would is disabled and refused. Requested widths are checked against what the apps commit: if an app's size limits refuse, the Group saves the closest size and says so. Proportions are saved with the Group and reapplied when it returns to side-by-side. Stack heights are not adjustable yet.

`niri_group_resize_smoke.py` passed in the guest with three Foot windows: 33/33/33 became 43/33/24 after a wider and a narrower step, matching the saved proportions. A step below the minimum share was not offered and was refused. Stacking and returning to side-by-side restored 43/33/24. With saving disabled, the test fails. The switcher showed the shares and disabled the steps that would go below the minimum.

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

Sandboxed callers of `System` are unit-tested only, because no Flatpak app was available.

Not done: the shell does not use the API yet, the service was not run as the installed systemd unit, and a real Flatpak app was not tested. Callers are identified by PID, not pidfd. Confinement other than Flatpak is not recognised. Calls are answered one at a time. Groups, displays, Layers, search providers and activities are not in this version.

### Activities

[ADR-0010](engineering/ADR-0010-activities.md) adds `io.github.mattonik.Flow1.Activities` to the Platform API (version 1.2) with `Start`, `Update` and `Finish`. An activity belongs to the bus connection that started it:
- It ends when finished or when that connection closes; there is no idle timeout.
- A caller may have three at once, and a Flatpak app counts once across its connections.
- Text is cut to one printable line; blank-looking characters and stacked combining marks are removed.

The service labels each activity with its owner, never with a name the app chose:
- **Flatpak app:** its verified app ID.
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

Not tested: a Flatpak app as the owner, and the narrow-screen dot outside the layout code.

### Shell visual polish (2026-11-21)

The shell's controls follow one system instead of one bordered rectangle:
- **Buttons** come in four variants: secondary, primary, quiet and danger, plus plain inside cards. A selected button has a leading dot, a 2 px border, a heavier label and "Current" as its accessible description, so selection never depends on tint alone.
- **Borders and targets:** functional borders stay at full token strength (3:1). Quiet buttons rest on a soft tint, so they read as controls on touch screens without hover. Every target is still at least 44 px.
- **Focus** is a 2 px ring 3 px outside the control, concentric with it. Clipped panels keep 4 px inside the clip, so rings stay whole.
- **Capsules:** the island, the work indicator and the bottom controls share one capsule bar. Their 44 px buttons draw 36 px capsules, concentric with the bar.
- **Radii** are on the token scale: cards 20, panels 14, controls 10, with in-card controls concentric with their card.
- **Type** follows the scale (11/12/14/16/18/32/52), with 13 px kept for compact labels in the island and cards, and weights 500–600. Clock, percentages and counts use tabular figures.
- **Icons:** line icons for notifications, quick settings, search, chevrons, close, plus and minus are drawn by the shell. They use multisampled geometry because curve-rendered strokes break up under virgl. Only buttons with a glyph load one.
- **Motion** uses the brand's quick (120 ms) and standard (220 ms) tokens and its standard curve. A *Reduce motion* setting in Desk makes every change instant, because Linux gives Quickshell no system signal for it. All "busy" indicators share one clock, so a card rebuilt by an activity update continues its sweep.

Surface by surface:
- **Niri and the lock screen follow the appearance switch** (audit UI-04). The theme generator writes `design/generated/niri-{dark,light}.kdl` and `swaylock-{dark,light}.conf`, so `session/niri.kdl` holds no colour of its own:
  - **Niri colours:**
    - the focus colour for the active ring
    - the functional border for the inactive one, which marks the focused window of an unfocused display, where focus returns
    - the canvas behind workspaces
    - the functional border for the Overview backdrop, so workspaces stand out from it at 3:1, as targets must. In Polar Night that backdrop is a mid sea-grey rather than near-black, a deliberate trade-off.
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
- **App launcher and search (fuzzel):** themed from the same tokens. The theme generator writes `design/generated/fuzzel-dark.ini` and `fuzzel-light.ini`:
  - the panel in the surface tone, with a full-strength border and a 14 px radius
  - Noto Sans at the 16 px body size, with rows as tall as the 44 px touch target
  - the selected row as an accent fill with on-accent text, since fuzzel cannot draw a selection outline
  - placeholder text in place of a prompt

  `session/flow-fuzzel.sh` picks the file that matches the Desk's appearance switch. A user's own `~/.config/fuzzel/fuzzel.ini` takes precedence. `test_launcher_theme.py` checks every colour pair in both files against the audit's thresholds, runs the wrapper with a stand-in fuzzel, and checks that the launcher and search use it. In the guest, the installed launcher (dark) and search (with a light appearance file) were captured; flipping the Desk switch itself was not exercised, because the guest cannot click.
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

Not verified:
- **Keyboard focus walk:** the guest has no key injection tool.
- **Screen reader:** names and descriptions were not checked with a running screen reader.
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
- **Apps get their own scopes:** the unit sets `FLOW_APP_SCOPES=1`. Every launch path then starts the app with `systemd-run --user --scope`: the shell's own launches, the app launcher's prefix, search, and the `gio launch` of opening a Layer, a missing Group member or a restored session (`session/launching.py`). So apps are never part of the shell's unit, and stopping or restarting the shell, or a limit on it, never touches them. `KillMode=process` stays as a safeguard for any path that misses a scope. Development shells and tests launch directly.
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
