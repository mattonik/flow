# Flow Groups: gap analysis and implementation plan

Status: implementation plan, 2026-11-13. This document describes the work needed to meet the [Flow Master Specification](../Flow_MASTER_SPEC_v1.1.md), not a claim that those features already work. The [contextual Group guidance](../Flow_contextual_menus_groups.md) and [reuse-first reference atlas](REUSE_FIRST_REFERENCE_ATLAS.md) also apply. NixOS, Niri and Quickshell remain the committed stack; the Flow controller and its public API must be toolkit-independent.

## Where we are

The current implementation is a useful two-app **stack prototype**, not yet the Group model described in the specification. A Group is recognized only when two distinct, uniquely identified app IDs occupy one Niri column, and its identity is created as part of an explicit save of the entire current workspace. The controller reads Group records from that session snapshot; pin data is separate. See [`session/spaces.py`](../../session/spaces.py) (`group_windows`, `pin_group`, `display_spaces`) and [`session/restore.py`](../../session/restore.py) (`save`, `restore`). This relationship is why side-by-side layout cannot be added safely by simply separating the windows: the current switcher, pinning and recovery would see two spaces or an unavailable Group.

Recorded ARM64 UTM guest evidence includes real two-app stacks, member focus, Group rename and pin/unpin, manual tabs/stack switching, limited reconstruction, and simulated portrait keyboard navigation. The [milestones](../milestones.md) describe the exact scenarios, including the limitations of in-memory and disposable-window recovery tests. A prior cold boot restored three unique applications and one stack, but full pinned-Group reboot recovery has not been established; a warm UTM reboot hung. Unit tests cover additional cases, but cannot establish end-to-end compositor or physical-device behavior. [Implementation status](../IMPLEMENTATION_STATUS.md) keeps F-003, F-005, F-007 and F-018 partial and F-006, F-016 and F-017 open.

## Gaps against the specification

| Requirement | Actual status | Remaining work |
|---|---|---|
| Independent Group identity and lifecycle | Partial. Saved IDs and names live inside the latest explicit session snapshot. The current create path is stack windows, then save the whole session. | Create, persist, rename and explicitly dissolve a Group independently of workspace snapshots; retain membership on member close and report a repairable state. |
| One logical destination | Partial. A two-app stack in one Niri column is shown and pinned as one destination. | Preserve one destination, order and last member focus when a Group spans columns; distinguish that explicit layout from intentional ungrouping. |
| Membership | Partial. Exactly two distinct, globally unambiguous app IDs are supported. | Add/remove/reorder members and handle multiple windows of the same app without guessing identities; support larger Groups as a separately tested extension. |
| Arrangement | Partial. Manual Niri tab/stack works for a saved active Group. | Side-by-side landscape, portrait stacking, adjustable ratios, saved layout and orientation-driven reflow. Preserve IDs and application state during transitions. |
| Pinned positions and recovery | Partial. A saved two-app Group can be pinned; recovery handles a narrow set of adjacent end-column arrangements. Missing/ambiguous cases can require manual intervention. | Preserve pin position across every supported layout and restart; provide a visible placeholder and repair path when a member cannot be resolved or launched. Do not silently move other pinned spaces. |
| Displays | Missing. Pins and Group actions are limited to one active display and focused Niri workspace. | Per-display logical strips, Group movement and reflow across outputs, disconnect/reconnect policy and display-scoped pin order. |
| Input and accessibility | Partial. Keyboard controls and several 44-pixel targets exist; simulated portrait keyboard checks passed. | Direct touch grouping, member resizing and navigation, real touch/pen and gesture arbitration, complete keyboard and screen-reader alternatives, and physical rotation tests. |
| Layers and app lifecycle | Missing end-to-end Group-preserving Layer journey. Native apps and their own menus remain usable; experimental app-menu work is separately deferred. | Open/dismiss a transient app over a Group without changing it; closing one member must leave the other running and the Group repairable. |
| Flow Platform API | Missing. The current shell invokes project-specific CLI processes; this is not the specified versioned public controller API. | Expose stable logical IDs and scoped operations/events through a toolkit-independent interface; verify a non-Qt client and authorization. |

The required acceptance journey F-005 is **two real apps reflowed portrait/landscape while remaining one logical Group**. None of the current isolated stack or rotation checks establishes that journey. F-003 additionally requires pinned positions after restart, F-006 a dismissible Layer, F-011 display reconnect, F-016 real touch, F-017 a non-Qt API client, and F-018 shell restart integrity. These are separate acceptance gates, not synonyms for passing unit tests.

## Implementation plan

### M1 — Explicit, independently persisted stack Groups

Make a real, immediately useful Group without requiring **Save session**. Implement explicit creation and dissolution for an existing two-app Niri stack, using a stable logical Group ID and durable ordered membership separate from the whole-workspace recovery snapshot. Keep the present two-distinct-app restriction for this first milestone; rejecting an ambiguous match is safer than choosing a window. Store enough session-scoped window information to rebind safely, while keeping durable identity independent of transient Niri IDs. The Group record, pin relationship and snapshot must have a clearly defined authority and locking/write order; do not silently overwrite a newer session snapshot or lose the old pin metadata.

Migrate existing version-1 saved Groups and Group pins in a backwards-compatible, idempotent way. Preserve the original snapshot for recovery, validate before writing, and fail visibly without destroying state if the source is invalid, ambiguous or the write is interrupted. Support existing installations and a fresh installation without manual file edits. Explicit ungroup must not close either application. Show a missing or ambiguous member as repairable rather than automatically assigning an unrelated window.

**Acceptance:** from two real apps, create a named Group in Flow, pin it and return to it, without invoking whole-session save; restart Quickshell and confirm the same ID, label, members and pin; close one member and verify the other remains usable and the Group is retained; explicitly dissolve and verify both apps remain open. Exercise existing version-1 data, repeated migration, corrupt/ambiguous data, write failure, concurrent edits, and the original snapshot and pin state. Run unit tests and an isolated ARM64 guest journey with disposable apps and a neighboring pin.

### M2 — One Group across stack and side-by-side layouts

Introduce an explicit Group layout intent and a logical projection from member windows to one Group destination. Update the switcher, focus handling, pin resolution/reconciliation, ordering and recovery together: they must not infer membership from two adjacent columns. Add an explicit, reversible manual side-by-side/stack action using documented Niri IPC, retaining member order and focus. Treat compositor state as authoritative and make interrupted or partially applied operations visible and recoverable; don't report success or erase Group metadata if a member disappears or changes workspace. Preserve neighboring pins and avoid moving unrelated windows.

**Acceptance:** create and pin a Group; change stack → side-by-side → stack, with the same logical ID, one switcher destination, last member focus, member processes, pin order and neighboring spaces throughout. Repeat with the Group unfocused, a member closed/replaced, duplicated app IDs, a changed workspace, and interruption between Niri actions. Verify a deliberate ungroup creates separate destinations and does not get mistaken for a layout transition. Demonstrate the complete journey in the ARM64 guest, not just in a projection test.

### M3 — Adaptive layout and sizing

Build on M2 to retain per-Group size ratios and select appropriate landscape and portrait arrangements from the actual logical output geometry. Reuse Niri's supported output/window interfaces; investigate and document how output changes are observed in the pinned version rather than assuming Niri's window event stream emits them. Respect application size constraints, manual layout choices and reduced-motion settings. Recovery after a partial transition must be explicit and safe.

**Acceptance:** rotate or change logical output geometry in the VM from landscape to portrait and back with two real apps and a neighboring pin; verify layout, usable proportions, one Group destination, stable Group ID and focus, and unchanged app/window state. Cover repeated/interrupted changes, manual overrides and incompatible size constraints. Then validate the same journey on supported physical touch hardware; simulated rotation is not physical-touch evidence.

### M4 — Complete Group lifecycle across outputs

Extend the model beyond the M1 two-app restriction, with explicit add/remove/reorder/resize operations and safe same-app window identity. Add per-display Flow and pin ownership, moving a Group as one destination, display disconnect/reconnect behavior, and the Group-preserving transient Layer journey. Save durable intent separately from recoverable application/document state, and surface missing launchers or ambiguous windows for repair.

**Acceptance:** create a larger Group, add/remove/reorder/resize members, close and relaunch one member, and explicitly dissolve without closing unrelated apps. Move a pinned Group between displays; disconnect and reconnect one output without losing membership or overriding intervening user changes. Verify F-003 restart recovery, F-006 Layer dismissal and F-011 hotplug with named test setups. Do not promise restoration of unsaved application documents or internal app state.

### M5 — Touch, API and release validation

Complete touch drag-to-group and pull-out, touch resizing and appropriate gesture arbitration, while maintaining equivalent keyboard and accessible controls. Provide the versioned, toolkit-independent Flow controller API and a small non-Qt client. Exercise error paths, focus, app compatibility, shell restart and performance using representative real apps and physical devices.

**Acceptance:** complete the Group lifecycle with real touch and without a mouse, including orientation change; verify keyboard-only and screen-reader paths, minimum target sizes and reduced motion. A non-Qt client must query and focus its allowed space and have unsafe requests denied. Run the F-003/F-005/F-006/F-011/F-016/F-017/F-018 journeys with recorded hardware, software versions and results. Label any unavailable hardware-dependent gate as unverified rather than complete.

## Engineering constraints

- Reuse Niri's actual compositor actions and output information. Quickshell presents controls; it must not invent or animate fake application surfaces. Existing Linux services remain responsible for their established roles.
- Durable identity must be explicit: never infer a Group solely from matching app names, adjacency or current column numbers. Runtime window IDs are session-scoped; a duplicate or missing match is a repair case.
- Niri IPC requests are separate operations. Re-read state around mutations, preserve the pre-operation intent, and document the remaining race; a set of calls is not an atomic compositor transaction.
- Keep old user data and rollback paths safe. Tests use disposable windows and isolated state, restore display/focus afterward, and never treat snapshots as backups of unsaved documents.
- Performance work must be measured: avoid polling per animation frame and unnecessary process or disk churn; confirm that event-driven changes improve real idle and interaction behavior before claiming a gain.
