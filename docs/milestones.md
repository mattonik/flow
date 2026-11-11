# Milestones

- **0 — graphics gate: single-display smoke test passed.** See [graphics validation](graphics-validation.md) for the VirtIO renderer, direct Niri session and remaining performance limitations.
- **1 — session foundation: partially verified.** Flake and NixOS module, Niri fullscreen-width columns, real app picker and terminal, minimal Quickshell capsule, and an evaluation fixture. Build, login, shell startup, launcher rendering and focus via IPC have been exercised. Verify hardware shortcuts, island click, lock, fullscreen overlay and logout before calling this complete.
- **1a — v0.9 Desk: in progress.** A Quickshell Desk overlays the existing Niri session, with app search, files, overview and return controls. Work and Niri overview remain native compositor views. Confirm keyboard and pointer behavior on the installed guest; spatial transitions and touch gestures remain future work.
- **2 — pinned spaces: not started.** Implement per-user persistent metadata, stable leftmost anchor ordering and direct navigation for a single display using compositor IPC. Support creating, removing and reordering pins without editing Nix configuration; test restart, missing apps, duplicates and focus. Niri workspace numbers alone are not Flow pins. Do not claim multi-display ordering or Group restoration until separately tested.
- **3 — minimum system surface: not started.** Replace the capsule's placeholder interactions with accessible Home, useful OS-backed status and controls, and notification handling; integrate secure session services and validate keyboard and touch access.
- **4 — product MVP and device maturity: not started.** Follow Stage B onward in the brief: Groups, display lifecycle, touch/rotation, accessibility, Flatpak/XWayland, and restoration. Track Sync, DisplayLink and iPad hardware separately; no compatibility is implied by this repository.

The prototype includes many future features; none are implemented merely because they are depicted there.

## v0.9 VM check (2026-11-11)

On the existing ARM64 UTM/NixOS guest, the updated system configuration built and activated, `niri validate` accepted the generated configuration, and Quickshell loaded the installed QML. The Desk was displayed through Quickshell IPC; two Foot windows and a Nautilus window remained visible to Niri IPC after changing views. This is a smoke test, not a complete interaction or accessibility test. Keyboard shortcuts, pointer controls, touch, small displays, session restart and animation still need end-to-end verification. Desk controls are currently pointer-oriented; keyboard navigation within the Desk is not implemented.
