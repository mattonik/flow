# Milestones

- **0 — graphics gate: single-display smoke test passed.** See [graphics validation](graphics-validation.md) for the VirtIO renderer, direct Niri session and remaining performance limitations.
- **1 — session foundation: partially verified.** Flake and NixOS module, Niri fullscreen-width columns, real app picker and terminal, minimal Quickshell capsule, and an evaluation fixture. Build, login, shell startup, launcher rendering and focus via IPC have been exercised. Verify hardware shortcuts, island click, lock, fullscreen overlay and logout before calling this complete.
- **1a — v0.9 Desk: in progress.** A Quickshell Desk overlays the existing Niri session, with app search, files, overview and return controls. Work and Niri overview remain native compositor views. Confirm keyboard and pointer behavior on the installed guest; spatial transitions and touch gestures remain future work.
- **2 — pinned spaces: experimental single-display slice.** Per-user metadata and create, unpin, reorder and focus controls exist for single-window apps; Niri IPC maintains open pins at the left of the focused workspace. Test compositor restart and additional physical shortcuts. Absent apps have no reserved compositor position or automatic launch, duplicate app windows cannot be unambiguously restored, and Groups and multiple displays remain unsupported.
- **3 — minimum system surface: not started.** Replace the capsule's placeholder interactions with accessible Home, useful OS-backed status and controls, and notification handling; integrate secure session services and validate keyboard and touch access.
- **4 — product MVP and device maturity: not started.** Follow Stage B onward in the brief: Groups, display lifecycle, touch/rotation, accessibility, Flatpak/XWayland, and restoration. Track Sync, DisplayLink and iPad hardware separately; no compatibility is implied by this repository.

The prototype includes many future features; none are implemented merely because they are depicted there.

## v0.9 VM check (2026-11-11)

On the existing ARM64 UTM/NixOS guest, the updated system configuration built and activated, `niri validate` accepted the generated configuration, and Quickshell loaded the installed QML. The Desk was displayed through Quickshell IPC; two Foot windows and a Nautilus window remained visible to Niri IPC after changing views. This is a smoke test, not a complete interaction or accessibility test. Keyboard shortcuts, pointer controls, touch, small displays, session restart and animation still need end-to-end verification. An initial keyboard navigation path has since been added; full accessibility and touch coverage remain open.

## Desk interaction check (2026-11-11)

In the UTM guest, Tab showed the focus ring on a Desk control and Enter activated the Files button. Calling the app-search action while Desk was open hid Desk and displayed Fuzzel. The content can scroll on shorter screens, but portrait, touch and screen-reader behavior still need testing. Pin identity needs to handle duplicate application windows and compositor restarts before it can provide the persistent, ordered spaces described in the brief.

## Pinned-space VM check (2026-11-11)

On the ARM64 guest, pinned a Nautilus window and a Foot window, reordered both using the service, and checked Niri window positions and retained focus. The Desk displayed saved pins, and its numbered focus IPC returned from Desk to Nautilus. The guest rebuilt successfully and the Nix test check passed. Automated tests cover single-window eligibility, duplicate-app ambiguity, ordering and unpin behavior. `Super+1` also focused the first pinned terminal through the guest keyboard. Full compositor restart, lock-screen interactions, other physical shortcuts, multiple displays and touch remain unverified.

## App launcher recovery (2026-11-11)

A manually restarted shell inherited an incomplete `XDG_DATA_DIRS`, leaving Fuzzel's application list empty. The shell was restarted using the graphical user manager's environment, and the launcher now adds the standard NixOS profile paths while preserving any supplied data paths. In the VM, Fuzzel displayed installed apps even with an intentionally invalid inherited data path; typing `firefox` and pressing Enter launched Firefox. Prompt and input colors were also set explicitly for legibility against the dark background.

## v1.0 experimental application menu (2026-11-11)

The v1.0 brief and prototype are included as references. The opt-in Desk setting persists across shell restarts, and the top-left menu reads the focused application's AT-SPI menu and invokes exposed actions. On the ARM64 UTM guest, Kate's File and Edit menus were displayed and its New command created a document through Flow; the system build and automated checks passed. Applications without compatible menus and multiple windows sharing one process use the fallback. The prototype's desktop category bar, searchable app commands, Groups, touch validation, and full session restoration remain future work.
