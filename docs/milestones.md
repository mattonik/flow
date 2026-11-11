# Milestones

- **0 — graphics gate: single-display smoke test passed.** See [graphics validation](graphics-validation.md) for the VirtIO renderer, direct Niri session and remaining performance limitations.
- **1 — session foundation: partially verified.** Flake and NixOS module, Niri fullscreen-width columns, real app picker and terminal, minimal Quickshell capsule, and an evaluation fixture. Build, login, shell startup, launcher rendering and focus via IPC have been exercised. Verify hardware shortcuts, island click, lock, fullscreen overlay and logout before calling this complete.
- **2 — pinned spaces: not started.** Implement per-user persistent metadata, stable leftmost anchor ordering and direct navigation for a single display using compositor IPC. Support creating, removing and reordering pins without editing Nix configuration; test restart, missing apps, duplicates and focus. Niri workspace numbers alone are not Flow pins. Do not claim multi-display ordering or Group restoration until separately tested.
- **3 — minimum system surface: not started.** Replace the capsule's placeholder interactions with accessible Home, useful OS-backed status and controls, and notification handling; integrate secure session services and validate keyboard and touch access.
- **4 — product MVP and device maturity: not started.** Follow Stage B onward in the brief: Groups, display lifecycle, touch/rotation, accessibility, Flatpak/XWayland, and restoration. Track Sync, DisplayLink and iPad hardware separately; no compatibility is implied by this repository.

The prototype includes many future features; none are implemented merely because they are depicted there.
