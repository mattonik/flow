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
| `GetCapabilities()` | `→ (u major, u minor, as features)` | Version negotiation; features include `spaces.list`, `spaces.focus`, `spaces.changed`, `control.spaces`. |
| `ListSpaces()` | `→ aa{sv}` | Only spaces that contain one of the caller's own windows. |
| `FocusSpace(s id)` | `→ ()` | Focus one of those spaces. Anything else returns `NotAuthorized` or `NotFound`. |
| `Changed` | signal, no arguments | Something about spaces changed; call `ListSpaces` again. It carries no data, so it leaks nothing beyond timing. |

### `io.github.mattonik.Flow1.Control`: unsandboxed callers only

| Member | Signature | Meaning |
|---|---|---|
| `ListSpaces()` | `→ aa{sv}` | Every space on the focused display's current workspace, plus pinned spaces of all displays. |
| `FocusSpace(s id)` | `→ ()` | Focus any listed space, routing to its display as Super+number does. |

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

- **Sandboxed app.** `/proc/<pid>/root/.flatpak-info` exists. This is the same check xdg-desktop-portal uses, and through Flatpak's D-Bus proxy the process ID is the sandbox's own. Such a caller is an app. Its windows are those whose Wayland app ID is its Flatpak application ID. It may use only `Spaces`, and `Control` returns `NotAuthorized`.
- **Unsandboxed process.** It may use `Control`. It could reach Niri's socket directly anyway, so denying it would add no security. On `Spaces` its own windows are those whose process is the caller or one of its ancestors, for example a command run from a terminal acts as that terminal.

Snap and other confinement mechanisms are not recognised: they must not be granted access to this name until they are.

### Errors

These are D-Bus errors named `io.github.mattonik.Flow1.Error.*`, following spec §12.3:
- `NotFound`
- `NotAuthorized`
- `Unsupported`
- `DisplayUnavailable`
- `AppNotRunning`
- `RateLimited`
- `Failed`

### Implementation

- The service reads state through a read-only `flow-spaces observe`, which neither reconciles pins nor writes state, and caches it for 300 ms.
- Focusing goes through the existing `flow-spaces` actions, so the API and the shell share one implementation and one lock.
- `Changed` is emitted after Niri events, debounced by 250 ms.
- A Flatpak app may make 20 calls in any 10 seconds across all its connections; more get `RateLimited`. Unsandboxed callers are limited per connection, because they could reach Niri directly anyway.
- The CLI `flow` (Python, Gio) is the first non-Qt client: `flow version`, `flow spaces list [--mine]`, `flow spaces focus ID [--mine]`, `flow events watch`. `busctl` works as well.

## Not in this slice

- Pin, unpin, reorder, Group, display and Layer actions.
- Search providers, activities, and requests for Desk or Overview.
- Events with payloads, and event ordering or replay.
- A portal or permission broker for granting Flatpak apps more than their own spaces.
- Moving the shell onto the API: the shell still calls `flow-spaces`.

Flatpak apps also need `--talk-name=io.github.mattonik.Flow1` before they can reach the service at all.

## Consequences

- Apps can query and focus their own spaces without Quickshell or Niri IPC.
- An app cannot see other apps' spaces, titles or identifiers.
- The bus name embeds the project's hosting namespace. If the project moves to a domain of its own, version 2 can take a new name, and the version 1 name can be kept as an alias for a while.
