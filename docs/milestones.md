# Milestones

- **0 — graphics gate: blocked.** Validate accelerated Niri in the target UTM guest as described in [graphics validation](graphics-validation.md). No GPU or session claim is made until recorded evidence exists.
- **1 — session foundation: implemented, unverified.** Flake and NixOS module, Niri fullscreen-width columns, real app picker and terminal, minimal Quickshell capsule, and an evaluation fixture. Test build, login, shell startup, focus, launcher, lock, fullscreen overlay and logout on the guest. Fix failures before calling this complete.
- **2 — pinned spaces: not started.** Implement per-user persistent metadata, stable leftmost anchor ordering and direct navigation for a single display using compositor IPC. Support creating, removing and reordering pins without editing Nix configuration; test restart, missing apps, duplicates and focus. Niri workspace numbers alone are not Flow pins. Do not claim multi-display ordering or Group restoration until separately tested.
- **3 — minimum system surface: not started.** Replace the capsule's placeholder interactions with accessible Home, useful OS-backed status and controls, and notification handling; integrate secure session services and validate keyboard and touch access.
- **4 — product MVP and device maturity: not started.** Follow Stage B onward in the brief: Groups, display lifecycle, touch/rotation, accessibility, Flatpak/XWayland, and restoration. Track Sync, DisplayLink and iPad hardware separately; no compatibility is implied by this repository.

The prototype includes many future features; none are implemented merely because they are depicted there.
