# ADR-0009: Flow Platform API, first slice (spaces, version 1)

**Status:** Accepted for the first slice. The bus name is provisional until the project's namespace is settled.  
**Date:** 2026-11-21

## Context

Spec §12 commits Flow to a toolkit-independent, versioned API on the session D-Bus. Quickshell must be a client of that API, not the only place that defines state, and raw Niri identifiers must not become durable IDs. The acceptance gate F-017 asks for a non-Qt client that can query and focus the space it is allowed to, and has unsafe requests denied.

Until now the shell called `flow-spaces`, a project-specific CLI that prints its internal JSON. That is not an API: it has no version, no caller identity, and no access policy.

## Decision

A small session service, `flow-api`, owns the bus name `io.github.mattonik.Flow1` and exports `/io/github/mattonik/Flow1`. It has two interfaces. The major version is part of every name; additive changes raise the minor version reported by `GetCapabilities`.

### `io.github.mattonik.Flow1.Spaces`: any caller, scoped to its own app

| Member | Signature | Meaning |
|---|---|---|
| `GetCapabilities()` | `→ (u major, u minor, as features)` | Version negotiation; features include `spaces.list`, `spaces.focus`, `spaces.changed`, `control.spaces`, and since 1.1 `control.pin`, `control.reorder` and `system.ui`. Sandboxed callers are not offered `control.*` features. |
| `ListSpaces()` | `→ aa{sv}` | Only spaces that contain one of the caller's own windows, and only the caller's part of them. A column or Group shared with other apps shows a label and app list built from the caller's own windows, never their titles or IDs. |
| `FocusSpace(s id)` | `→ ()` | Focus one of those spaces, but only while one of the caller's own windows has the keyboard (Niri's `is_focused`). An app can move between its own spaces but cannot take focus from another app, nor act while Desk, a launcher or the overview holds the keyboard. Anything else returns `NotAuthorized` or `NotFound`. Raising a window on request belongs to xdg-activation, which is not in this slice. |
| `Changed` | signal, no arguments | Something about spaces changed; call `ListSpaces` again. It carries no data, so it leaks nothing beyond timing. |

### `io.github.mattonik.Flow1.Control`: unsandboxed callers only

| Member | Signature | Meaning |
|---|---|---|
| `ListSpaces()` | `→ aa{sv}` | Every space on the focused display's current workspace, plus pinned spaces of all displays. Spaces on other workspaces are not listed in this version, on either interface. |
| `FocusSpace(s id)` | `→ ()` | Focus any listed space, routing to its display as Super+number does. |
| `PinSpace(s id)` | `→ ()` | Since 1.1. Pin a space at the end of the focused display's pins, in one locked step that names its window and never moves focus. |
| `UnpinSpace(s id)` | `→ ()` | Since 1.1. Unpin a pinned space; its windows stay where they are. |
| `MoveSpace(s id, s direction)` | `→ ()` | Since 1.1. Move a pin of the focused display `left` or `right`. A move past either end, or of another display's pin, is `InvalidState`. |

### `io.github.mattonik.Flow1.System`: requests for shell UI (since 1.1)

| Member | Signature | Meaning |
|---|---|---|
| `ShowDesk()`, `ShowOverview()`, `ShowLauncher()` | `→ ()` | Open Desk, Flow's Overview or the app launcher (Flow's search panel, showing apps). Since 2026-11-21 Overview is Flow's own row of spaces; Niri's live overview stays in the Spaces switcher. A request never closes anything: while any other shell surface is open (Desk, overview, launcher, switcher, quick settings, notifications, app menu) it does nothing and returns `InvalidState`. An app may ask for Desk or the overview only while one of its windows has the keyboard, which is Niri's `is_focused` and is false while shell UI holds the keyboard. Apps may not open the launcher, which takes the keyboard. Unsandboxed callers may make all three requests. |

Space dictionaries have these keys:
- `id` (s)
- `kind` (s: `app` or `group`)
- `pinned` (b)
- `position` (u): 1-based within its display's Flow
- `display` (s: `Main`, `External N`)
- `active` (b)
- `available` (b)
- `label` (s)
- `apps` (as)

Clients must ignore keys they don't know.

### Identity of spaces

| ID | Lifetime |
|---|---|
| `pin:<pin id>` | Durable while pinned. |
| `group:<Group id>` | Durable while the Group exists. |
| `window:<opaque>` | Session-scoped. It is a hash of the Niri session and the window, never the raw Niri window ID. |

### Callers and roles

The service asks the bus for the caller's process ID, then classifies it:

- **Sandboxed app.** `/proc/<pid>/root/.flatpak-info` exists, the file xdg-desktop-portal reads too; through Flatpak's D-Bus proxy the process ID is the sandbox's own. If the sandbox root cannot be read, the caller is treated as sandboxed and refused, never as trusted. Such a caller is an app. It may use only `Spaces`, and `Control` returns `NotAuthorized`.
  - Its windows are those drawn from inside its own sandbox: the process Niri reports for the window has a `.flatpak-info` that names the same app.
  - The window's Wayland app ID is not evidence of ownership. Any client may set any value, including the Flatpak ID of another app or of an installed native app, and an app's Wayland window that sets a different one is still its own.
- **Known gaps:**
  - Identification uses the bare PID from `GetConnectionUnixProcessID` and then reads `/proc`, and so does window ownership with the PID Niri reports. Both can race with PID reuse. Current portals use a pidfd, which this service should adopt.
  - An X11 window is drawn through xwayland-satellite, outside the sandbox, so it is not the Flatpak's. An app with only X11 windows lists no spaces and cannot ask for Desk or the overview. The same holds for any window whose process Niri does not report.
  - In a space shared with other apps, `FocusSpace` focuses the caller's own window, except in a pinned space, which is focused as Super+number does.
  - The app ID is chosen by whoever builds the app, and Flatpak keeps it unique only within one installation. A sideloaded bundle can take the ID of an app that is not installed as a Flatpak, such as one installed natively.
- **Unsandboxed process.** It may use `Control`. It could reach Niri's socket directly anyway, so denying it would add no security. On `Spaces` its own windows are those whose process is the caller or one of its ancestors, for example a command run from a terminal acts as that terminal.

Snap, containers, firejail and plain bubblewrap are not recognised, and nothing in this service or the bus policy enforces that they stay away. Such a caller is treated as unsandboxed and may use `Control`.

### Errors

These are D-Bus errors named `io.github.mattonik.Flow1.Error.*`, following spec §12.3:
- `NotFound`
- `NotAuthorized`
- `Unsupported`
- `DisplayUnavailable`
- `AppNotRunning`
- `RateLimited`
- `InvalidState` (since 1.1: already pinned, not pinned, pinned on another display, at an end, other shell UI open)
- `InvalidArgs` (since 1.1)
- `Failed`

### Implementation

- The service reads state through a read-only `flow-spaces observe`, which neither reconciles pins nor writes state, and caches it for 300 ms.
- Focusing goes through the existing `flow-spaces` actions, so the API and the shell share one implementation and one lock. Calls are answered one at a time while `flow-spaces` runs; under load other callers wait.
- `Changed` is emitted after Niri events, debounced by 250 ms, and at least once a second during a steady stream of events.
- The service checks the Niri session before taking its bus name, and exits if none is available, so systemd can restart it. The user unit is part of the graphical session.
- A Flatpak app may make 20 calls in any 10 seconds across all its connections; more get `RateLimited`. Unsandboxed callers are limited per connection, because they could reach Niri directly anyway.
- The CLI `flow` (Python, Gio) is the first non-Qt client: `flow version`, `flow spaces list [--mine]`, `flow spaces focus ID [--mine]`, `flow spaces pin|unpin ID`, `flow spaces move ID left|right`, `flow desk|overview|launcher show`, `flow events watch`. `busctl` works as well.

## Not in this slice

- Group, display and Layer actions.
- Search providers and activities.
- Events with payloads, and event ordering or replay.
- A portal or permission broker for granting Flatpak apps more than their own spaces.
- Moving the shell onto the API: the shell still calls `flow-spaces`.

Flatpak apps also need `--talk-name=io.github.mattonik.Flow1` before they can reach the service at all.

## Consequences

- Apps can query and focus their own spaces without Quickshell or Niri IPC.
- An app cannot see other apps' spaces, titles or identifiers.
- The bus name embeds the project's hosting namespace. If the project moves to a domain of its own, version 2 can take a new name, and the version 1 name can be kept as an alias for a while.
