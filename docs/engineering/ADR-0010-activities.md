# ADR-0010: Activities in the system island (Platform API 1.2)

**Status:** Accepted for the first slice  
**Date:** 2026-11-21

## Context

Spec §12.2 lists Activities: an app publishes, updates and completes progress, music, a build or a file transfer, and the system island surfaces it. The reference scenarios in spec §12.5 include a file manager publishing copy progress and a development tool publishing a build. Spec §12.4 adds two requirements:
- The app owns its activity and its expiry.
- An app's activities cannot pass for shell notifications or for another app.

[ADR-0009](ADR-0009-platform-api-v1.md) already provides the bus name, caller identification and rate limits.

## Decision

A new interface, `io.github.mattonik.Flow1.Activities`, on the same service:

| Member | Signature | Meaning |
|---|---|---|
| `Start(s title, a{sv} options)` | `→ s id` | Start an activity. The optional keys are `progress` (d, 0–1, or −1 for indeterminate) and `detail` (s). |
| `Update(s id, a{sv} changes)` | `→ ()` | Change the title, progress or detail of one of the caller's own activities. |
| `Finish(s id)` | `→ ()` | End it. |

### Ownership and expiry

- An activity belongs to the bus connection that started it, and ends when that connection closes. An app that exits or crashes therefore never leaves one behind.
- An activity also ends after 10 minutes without an update.
- No caller can see, update or finish another caller's activity.

### Limits

- A caller may have at most three activities at once.
- Titles are cut to 80 characters and details to 120. Control characters are removed.
- The rate limit of ADR-0009 applies to every call.

### Who it says it is

The service attaches an owner label to each activity. The app never chooses it:
- **Flatpak app:** the app's name from its desktop entry, else its app ID.
- **Unsandboxed caller:** the process name.

For unsandboxed callers the process name is only a convenience, not a boundary: such a process could rename itself or reach Niri directly. The `flow` CLI names itself `flow`. The island always shows the owner next to the title and draws activities as a progress chip, not as a notification. An app can write any title, but it cannot appear to come from Flow or from another app.

### Delivery to the shell

The service writes every live activity to `$XDG_RUNTIME_DIR/flow-activities.json`, atomically, whenever one changes. The shell watches that file. The file lives on the session's tmpfs and goes away with it. The shell learns nothing it couldn't learn over the bus, and it needs no D-Bus binding of its own.

### Shell presentation

- The island shows the most recently updated activity as a chip with the owner, the title and a progress bar.
- When there are several activities, the chip also shows how many.
- Desk lists every activity under *Activities*.

### CLI

- `flow activity run TITLE -- COMMAND…` keeps an indeterminate activity for as long as the command runs, then finishes it.
- `flow activity progress TITLE` reads numbers from 0 to 100 from standard input, one per line, updating as they arrive, and finishes at end of input.

## Not in this slice

- Media activities. MPRIS already covers players, and quick settings shows them.
- Actions on an activity, such as cancelling it.
- Persistent history of finished activities.
- Notifications.

## Consequences

Any app with access to the bus name can show progress in the island without a Flow SDK. It can't outlive its connection, can't exceed three entries, and can't hide where it came from.
