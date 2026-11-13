# ADR 0002: Use Quickshell for session notifications

Status: prototype; the NixOS guest build activated and a D-Bus notification call returned an ID. Inbox interaction, persistence and lock-screen privacy remain open.

**User journey:** receive desktop notifications without focus theft, open a compact inbox and dismiss items. Notification content is rendered as plain text and remains in the current shell process only.

| Option | Reuse / fit | Privacy and safety | Decision |
|---|---|---|---|
| Quickshell 0.3.2 `NotificationServer` | Existing shell dependency; implements the freedesktop notification service and exposes tracked notifications/actions | One owner in the session; no disk history; cap tracked items; no keyboard focus for incoming alerts | ADOPT prototype |
| Mako | Mature external notification daemon | Would require service ownership/configuration and separate Flow UI/history integration | Fallback only if Quickshell server fails |
| New Flow daemon | Duplicates established protocol/service | Adds another privileged session process and data store | Reject |

The server is declared once in the existing long-lived Quickshell process. Notifications are capped at 20 tracked entries, displayed in-memory only, and use `Text.PlainText`. The initial slice has no automatic content preview; users open the inbox explicitly from the Island or the keyboard-accessible Desk. Actions are invoked only when explicitly tapped. Keyboard focus is taken only while the inbox is open.

**Limitations:** active tracked notifications are not durable history and can disappear on expiry or shell restart; no per-app controls, DND, priority policy, verified lock-screen occlusion, or hardware-touch evidence. The inbox relies on the compositor lock surface to occlude it while locked; validate this in the guest before claiming lock privacy. One notification service must own the session D-Bus name; disable any competing daemon when validating. Before lock-screen display or persistent history, add explicit redaction/retention policy and verify storage permissions. Roll back by removing the Quickshell server and surfaces; no persisted data migration is needed.

**Evidence basis:** Flow reuse atlas and master spec; Quickshell 0.3.2 notification API documents `NotificationServer.notification`, `trackedNotifications`, actions, and plain-text body handling. Runtime behavior still requires validation in the ARM64 guest.
