# Implementation status against Flow Master Spec v1.1

Updated 2026-11-13. This is a repository and recorded-evidence audit, not a new full acceptance run. **Partial** means some relevant behavior exists but the whole journey is not established. **Open** means the acceptance journey has not been demonstrated. See [milestones](milestones.md) and [graphics validation](graphics-validation.md) for prior evidence and its limits. Recent targeted checks ran in the UTM guest; they do not validate every journey below.

The recorded test environment is an Apple Silicon Mac running UTM 5.0.8, QEMU ARM64 with HVF, 8 GiB RAM and virtio-ramfb-gl; NixOS 26.11 with Niri 26.04 and virgl acceleration. Exact host model and physical input details were not recorded. No journey below is promoted to a full pass solely from the HTML prototypes or from unit tests.

| ID | Status | Evidence and remaining acceptance gap |
|---|---|---|
| F-001 Native session | Partial | Niri, Quickshell, real apps and login were exercised; Plasma remains installed as a fallback. Warm reboot hung in UTM; fallback login was not newly verified. |
| F-002 Three real apps | Partial | Files, terminal, editors and browser ran in the guest. A single-workspace Niri IPC check focused running Firefox, Kate and Files in turn, then moved right and left between Files and Firefox and returned to Files. The complete navigation journey, including touch, remains to be checked. |
| F-003 Pinned positions | Partial | Single apps and saved Groups of two to four apps can be pinned and ordered as one column; each display keeps its own pin order (2026-11-20). Recovery rebuilt a pinned Group from adjacent end columns in either order, restoring saved member order while preserving its pin, focus and a neighboring space. Other missing or changed layouts can require manual recovery. A pinned three-app Group was recovered in the guest after its members were separated. Per-display ordering was exercised on two real Niri outputs in the guest. The complete restart journey remains open. |
| F-004 Shortcut accuracy | Partial | Displays have user-stable numbers: Main and External 1, 2, …, kept while a display is disconnected; the user can make another display Main. Super+number focuses Main's pins from any display, and holding Super+Alt, then pressing a display digit and a space digit, focuses that external display's pin directly, with an indicator, a timeout and feedback for unknown displays and spaces. Search lists other displays' pins and routes focus to their display first. The sequence was exercised through the shell's IPC on two real Niri outputs in the guest, and the bindings pass `niri validate`; physical key presses, keyboard layouts and remapping are not verified. |
| F-005 Group identity | Partial | Two unambiguous apps can be explicitly grouped, named, pinned and dissolved using a registry independent of session snapshots. An isolated ARM64 guest test exercised migration and the Group lifecycle without changing the real snapshot; the existing guest Group also migrated. Manual stack/tabs remains available. The switcher can open an absent app and explicitly rejoin two adjacent, unambiguous single-window columns, including a pinned Group at the end of the strip. A manual side-by-side layout now appears as one switcher and keyboard-navigation destination; an ARM64 guest test preserved its pin, focus and neighboring pin across a stack → side-by-side → stack journey. If another app separates the two columns, the switcher can explicitly repair the Group when intervening columns are safe; recovery, touch navigation, automatic orientation reflow and the full lifecycle remain open. |
| F-006 Layer dismissal | Open | No verified Flow Layer model or Group-preserving dismissal journey. |
| F-007 Desk/Overview | Partial | In the ARM64 guest, opening and closing Desk and Niri Overview retained the same four window IDs, workspace, scrolling positions and focused window. At a simulated 320×512 logical portrait size, Group switcher keyboard navigation and pin editing remained visible; the original display and window state were restored. Gesture, physical touch, multi-display and visual-transition acceptance remain open. |
| F-008 Search | Partial | Search covers recent local files within the user’s home folder, Flow settings, quick settings, installed applications, open windows and saved pins, including closed apps. File indexing and commands are missing. The settings shell actions were checked through IPC. A recent-file result from an isolated history was selected in the guest and opened in Kate; naturally populated history and the settings results still need end-to-end validation. |
| F-009 Island/system | Partial | Island, quick settings, lock and a temporary notification inbox exist. Quick Settings opens GNOME's Wi-Fi panel and the connection-profile editor, and controls one unambiguous MPRIS player. The guest showed the no-adapter Wi-Fi state; real scanning/authentication, durable notification history and independent availability across all contexts remain unverified. |
| F-010 Real accounts | Open | Two-user and guest isolation and switching have not been verified. |
| F-011 Display hotplug | Partial | In the guest, a second virtual output was disconnected and reconnected through its DRM connector (2026-11-20). Its pins stayed with it and nothing on the other display was reordered, then the pins came back in order. A pinned three-app Group and a single app were moved between the two outputs as one destination each. Physical monitors, docks, connector changes and serial-number identity are unit-tested only. |
| F-012 Portal correctness | Partial | The guest's portal broker is active and owns its session D-Bus name. A disposable unsandboxed chooser request returned the selected test-file URI, and a second request returned a cancel response. Sandboxed file access, saving, screenshots, screenshare, clipboard and drag/drop remain unverified. |
| F-013 Audio/peripherals | Open | Volume control was exercised; a Bluetooth headset and microphone were not tested on physical hardware. |
| F-014 Figma day | Open | No recorded Figma workload, screenshare or browser-load test. |
| F-015 Development day | Open | Terminal and editors run, but the native-host development, container and VM workflow is not verified. |
| F-016 Touch day | Open | A manually invoked on-screen keyboard entered a letter, Shift and Enter into a guest terminal with a simulated pointer. Real touch, automatic text-field activation, rotation and tablet Group testing remain open. |
| F-017 API app test | Open | No versioned Flow Platform API, non-Qt client or authorization test exists. |
| F-018 Shell restart | Partial | The shell was restarted without closing guest apps; a recorded before/after pinned-state integrity test is still needed. |
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

`spaces.json` is now version 2: pins carry a display key, and each display orders, reconciles and numbers only its own pins. The key uses make, model and serial when the serial is present and unique among connected outputs, so a monitor keeps its pins on another connector; otherwise it includes the connector, and a monitor on a new port is a new display. Duplicate serials are not trusted. Legacy pins migrate once with a `spaces.v1.json` rollback copy: a stored connector name waits under a `legacy:` key until that connector appears; a pin without a display is assigned by its live window or, with one display connected, to that display. Pins of a disconnected or ambiguous display are listed in Desk under *Pinned on other displays*. They are focused where their window is, but never launched, reordered or reconciled until the user chooses *Pin here*. Session snapshots record their display; recovery refuses a session saved on another connected display.

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

The switcher's active space offers *Move to* each other connected display. An app, a stack or a whole Group moves as one destination, keeping its member order, focus, window IDs and Group identity. Niri inserts a moved column after the destination's active column, so each Group column is moved with the previous one active there, and a space never lands inside an active side-by-side Group. A pinned space joins the end of the destination's pinned spaces; a pinned side-by-side Group is placed there explicitly, because pin reconciliation is paused beside side-by-side Groups. Saved side-by-side proportions are reapplied on the new output. Niri requests are separate, so an interrupted Group move leaves its first apps on the destination: the error says so, and moving one of the remaining apps again finishes it. A Group that is split in any other way must be repaired first.

`niri_move_display_smoke.py` passed on the two-output guest (Niri 26.04, five disposable Foot windows, isolated state):
- A pinned three-app side-by-side Group moved to the second display and took its pinned place after that display's own pin, with the same Group, pin, window IDs and focused member.
- An unpinned app moved there landed after the Group, not inside it.
- The Group was stacked there and moved back as one column, with its pin and focus.
- Moving to the display a space is already on was refused.

With the side-by-side anchor disabled, the guest test fails: the moved app lands between the Group's first and second apps. The development shell showed the *Move to* button on the active Group's card. The button itself was not clicked in the guest, since no pointer automation is available there; the backend action it calls is what the smoke test exercises. An interrupted move is covered by unit tests only.

### Display numbers and shortcuts

Each display record in `spaces.json` keeps a `number`: 0 is Main, external displays count from 1. The first Main is a built-in panel when one is connected, otherwise the leftmost display; a new display takes the lowest free number, and numbers stay with disconnected displays. *Make this the main display* in Desk swaps numbers with the previous Main. Desk names the display (*Pinned spaces · External 1 · Dell U2723QE*) and lists other displays' pins under their names. Super+1…9 opens Main's pinned spaces from any display. Niri binds Super+Alt+1…9 to the shell's `display_digit`. The first digit chooses a display and shows *External N · label — choose a pinned space* at the bottom of the screen without moving focus. The second digit, pressed while Super+Alt is still held, focuses that display's pin. After 1.5 seconds the sequence is cancelled. An unknown display, a disconnected display or a missing space is reported, and focus does not move.

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

Limitations: The changes run from a development copy, not an installed NixOS generation. An older generation cannot read a version 2 pin file and reports *Invalid saved spaces* without changing it; restore `spaces.v1.json` to roll back. Touch, physical rotation and real hotplug remain open.
