# ADR-0012: A portable, semantic workspace snapshot

**Status:** Accepted for step 1 (local only), 25 November 2026

## Context

Spec §16 makes Flow Sync long-term but "architecture-ready now", in three independent layers: files, user configuration, and semantic workspace restoration with no pixel-perfect promise. F-021 (Sync conflict) is Open. The [Luma note](LUMA_SYNC_AND_WIFI_NOTES.md) proposes a first local experiment: import a semantic snapshot that holds an unavailable app and a two-app Group, preview it, and act only on confirmation, without touching the destination's existing work or the network.

Today's recovery snapshot, `session.json` (version 1, `session/restore.py`), is the wrong thing to carry to another machine:

- It names Niri window IDs and the Niri session ID. They mean nothing outside the session that wrote them.
- It keeps window titles as labels. Titles hold document names, mail subjects and page titles: private text.
- It keeps columns of one workspace and the display key, and recovery refuses a session saved on another connected display.
- Its Group records carry their window bindings and IDs.

This ADR defines the third layer's format and a local export and import. It sends nothing anywhere.

## Decision

### The format

A snapshot is one JSON object, UTF-8, at most 64 KiB. Version 1:

```json
{
  "format": "flow-workspace-snapshot",
  "version": 1,
  "spaces": [
    {"apps": ["org.gnome.Calculator.desktop"], "pin": 1},
    {"apps": ["org.gnome.TextEditor.desktop", "foot.desktop"],
     "group": {"name": "Writing", "layout": "side-by-side", "proportions": [0.6, 0.4]}, "pin": 2},
    {"apps": ["firefox.desktop"]}
  ]
}
```

- **Spaces** are the workspace's tiled columns, left to right, 1 to 32 of them. Order is intent; positions in pixels are not kept.
- **Apps** are desktop-file IDs (`<id>.desktop`, letters, digits, `_`, `-` and `.`, no `/`), never Niri window IDs or session IDs. Export takes a window's Niri app ID and adds `.desktop`, the exact rule recovery already uses to find a desktop entry. It never guesses from window classes or titles. An app appears at most once in a snapshot: one desktop ID opens one window.
- **Groups** are a space with 2 to 4 apps, in member order, plus `group`: `name` (the user's Group name, 1–80 characters), `layout` (`stack` or `side-by-side`), and for side by side, `proportions`, one fraction of the Group's width per member, each at least Flow's minimum share (0.15), summing to 1. A single-app space has no `group`.
- **Pins** are by order only: `pin` is the space's rank among the snapshot's pins (distinct positive integers; only their order matters). No display key travels. The destination decides which display they go to.
- **Layers** (floating windows) are left out. They are temporary, as in recovery.
- **No private text.** No window titles, no labels derived from them, no file paths, no Group or pin IDs, no session or display identity. The only free text is a Group name, which the user gave. No secrets: nothing in the format can hold one beyond what a user types as a Group name.

The reader is strict. A key the version does not define is an error, not ignored. So a file carrying a `title` or a `window_id` is refused rather than silently passed along, and every field a future version adds comes with a version change, never as an extra key an older Flow would drop without saying so.

### Versions

- The writer always writes the newest version it knows.
- A reader accepts every version it knows. When version 2 exists, a version 1 file is converted in memory when read; the file itself is never rewritten.
- A file of a newer version than the reader knows is refused, with nothing done: "This snapshot comes from a newer Flow (version N); update Flow to import it." A partial reading of a newer format would be a guess about what the user wanted.
- A file without `format: flow-workspace-snapshot`, or without an integer version, is not a snapshot.

### Export, preview and import

`flow-recovery` gets three actions:

- **`export FILE`** writes the focused workspace as a snapshot to a new file (mode 0600; an existing file is never replaced). Pins are this display's pins on that workspace, in their order. The answer says what it left out: Layers, windows without an app ID, a second window of an app, an app ID that is not a desktop-file ID.
- **`preview FILE`** reads the snapshot and this machine's state, changes no state file, runs no Niri action, and answers with a structured report. (On a machine where Flow never ran it creates the state folder and its lock files, as every Flow action does.)
  - `acted: false`;
  - `workspace`: a new, empty workspace on the focused display, named by its label, and how many displays are connected;
  - `open`: each space that will open, in order: its apps, and for a Group, its name, layout and proportions as fractions of that display;
  - `left_out`: each app or Group that will not, and why: not installed (no desktop entry of that ID here that is an app a user opens: `Type=Application`, not `Hidden`, not `NoDisplay`; a snapshot's IDs come from a file, not from this machine's windows, so a helper or a masked entry is never launched); already open here (its window stays where it is); already pinned or already in a Group here (import never changes existing pins or Groups); a Group not created because a member is left out, whose other members open alone; a side-by-side Group opened as a stack, as recovery cannot yet rebuild stacked and side-by-side Groups together;
  - `pins`: each snapshot pin and where it goes: after this display's existing pins, in snapshot order, or not pinned (with the reason). Flow changes no pin while a side-by-side Group is on the workspace, so an import that opens one pins nothing, and the preview says so;
  - `confirm`: a short digest of the plan.
- **`import FILE CONFIRM`** acts only when `CONFIRM` is the digest of the plan it computes again, under Flow's locks, at that moment. If anything that changes the plan changed since the preview (an app installed, a window opened, a pin added), the digests differ and nothing is done: preview again. It then focuses the focused display's empty workspace, registers the new Groups (new IDs, the snapshot's names), opens the apps with recovery's own restore (`restore.restore`: it launches each app by its desktop entry, waits for its window, builds stacks and side-by-side proportions and checks the order), and finally appends the pins. Existing windows, pins and Groups are not changed: recovery's per-step check stops the import if the state files change except by the import's own additions or by the bindings ordinary refreshes update. A new Group that recovery could not arrange (Niri did not join its column, say) is registered no longer: its apps stay open as spaces of their own, and the answer says so, so no separated Group is left to pause automatic saving. The answer lists what opened and, for a partial import, exactly what was left out and why, including what failed while opening, which pins were not added, and recovery's own notes.

No action of the three opens a network connection. They read files, ask Niri over its Unix socket and launch apps through `gio launch`.

### Relation to `session.json`

Local recovery stays separate and is not a client of the snapshot in step 1. Recovery restores *this* session after a restart: it relies on window IDs to tell same-app windows apart within a Niri session, on Group IDs and bindings in `groups.json`, and on the display key to refuse the wrong display. None of that is portable, and none of it should be. Re-basing recovery on the snapshot would change code that many guest smokes cover, for no gain in step 1.

What is shared is code, not files: desktop-ID lookup, Group validation, and `restore.restore()`, which the import uses to open and arrange apps. If recovery later moves onto the snapshot, `session.json` would become a snapshot plus local bindings, and this ADR's format would not change.

The controller's automatic save (ADR-0011) follows focus. After an import, the new workspace is focused, so the next automatic save records it, as it would after the user switched workspace.

### Out of scope, as later steps under spec §16

- **Transport:** how a snapshot reaches another device (a file the user carries today, a sync provider later). Spec §16's file layer is an established provider, not Flow.
- **Device identity:** per-user identity, device registration and revocation, and which device a snapshot came from.
- **Encryption:** keys, what metadata is encrypted, and who can read a snapshot in transit or at rest.
- **Conflicts:** two devices changing the same configuration, version migration across devices, and F-021's journey.
- **User configuration (§16 layer 2):** syncing pins, names, shortcuts and preferences as settings. Step 1's pins are only a snapshot's pins, applied on import.
- **Documents:** app-provided document references, once apps can offer them. Until then a reopened app opens as a new window, not the document the user had.
- Displays beyond the focused one, other workspaces, Activities, and a shell import dialog.

## Consequences

- A snapshot carries no window titles, IDs or display identity, so it can leave the machine without leaking what the user was reading. The price: an imported space opens its app, not its document.
- A strict reader means a hand-edited file with an extra key is refused. The error names the key.
- Import refuses to act on a stale preview, at the cost of a second preview when the machine changed in between.
- Import inherits recovery's limits: an app that opens slowly (15 s), opens several windows, or only activates an instance already open elsewhere is reported as not opened. Apps already open are left where they are, so a single-instance app is never moved off the user's existing workspace.
- An import stopped halfway (the workspace changed, a Niri action failed) keeps the Groups it registered, like a Group whose apps closed, and says which.
- Groups the user renamed before this change carry no `named` mark, so they are exported under their apps' names until renamed again: the safe direction for privacy.
- Import trusts its plan's exclusions: recovery's `restore()` runs with only the new Groups registered, which is safe because no app that is open, pinned or in a Group here takes part.

## References

- Spec §16; [Luma continuity and Wi-Fi notes](LUMA_SYNC_AND_WIFI_NOTES.md)
- `session/snapshot.py` (format, validation, plan), `session/restore.py` (`export`, `preview`, `import`)
- `tests/test_snapshot.py`, `tests/niri_snapshot_import_smoke.py`
- ADR-0011: [Resident controller](ADR-0011-resident-controller.md)
