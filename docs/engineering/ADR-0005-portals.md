# ADR-0005: XDG portals for Niri

**Status:** Accepted direction; runtime validation pending  
**Date:** 2026-11-13

## Decision

Use the standard XDG Desktop Portal broker for sandboxed file selection, screen capture/casting, and other mediated desktop capabilities. Keep Niri's NixOS module defaults as the initial backend selection; test them before adding Flow overrides. Portals are a system integration, not a Flow-specific API.

## Checked facts

- Flow's `flake.lock` pins nixpkgs to `f4a03c008967841f2a0b3cb7c436fc6fc31671f6`. Evaluating `nixosConfigurations.test` at this pin confirms that `programs.niri.enable` sets `xdg.portal.enable`, selects `gnome;gtk` as the Niri default, selects GTK for Access/Notification and GNOME Keyring for Secret, and includes `xdg-desktop-portal-gnome` plus `xdg-desktop-portal-gtk`. The Niri module's `useNautilus` default is true; Flow also lists Nautilus in its package set. This proves configured package/module availability only, not a running VM's behavior.
- At that same pin, NixOS exposes `xdg.portal.extraPortals`, per-desktop `xdg.portal.config`, and `xdg.portal.configPackages`. The portal module asserts that enabling portals requires at least one backend. `programs.niri.enable` supplies GNOME but does not explicitly choose a screencast implementation in `config.niri`; upstream Niri's current portal guidance says GNOME is required for screencasting, GTK provides basic fallback functions, and GNOME Keyring or oo7 implements Secret. GNOME's file chooser uses Nautilus by default; GTK can be selected for FileChooser if desired.
- The XDG portal API mediates sandboxed app access over D-Bus and exposes distinct interfaces including FileChooser, ScreenCast, Screenshot and Clipboard. Availability of an interface does not establish a suitable backend, prompt UX or successful capture.
- Niri's documented touchscreen interactions include horizontal view movement by dragging a tiled window and interactive move with Mod+finger drag; its overview supports touch navigation and dragging. Those native gestures are candidate foundations for Flow's interaction model, not evidence of tablet UX quality. Exact Magnetic Flow, touch target sizing, rotation, virtual keyboard and pen behavior still need physical-device validation.

## Options and tradeoffs

| Option | Assessment |
|---|---|
| NixOS Niri defaults: GNOME + GTK + GNOME Keyring | Preferred starting point. Present at the lock pin and matches upstream Niri guidance for screencast plus basic fallback services. Nautilus is currently enabled for GNOME FileChooser. Validate exact portal selection in-session. |
| GTK-only backend | Candidate for basic file chooser/settings fallback, but not the documented Niri screencast path. Don't select it as the sole backend before screenshare tests. |
| `xdg-desktop-portal-wlr` | Not selected for Niri. It is a wlroots-oriented backend; Niri's NixOS module explicitly disables the generic wlroots portal and upstream recommends GNOME for screen casting. Avoid copying Sway/wlroots recipes without testing. |
| Flow-authored portal/backend or direct compositor capture | Rejected for now. It duplicates an established permission broker and would increase security and maintenance surface without demonstrated missing capability. |
| GTK FileChooser instead of GNOME/Nautilus | Viable fallback candidate if Nautilus coupling is undesirable; it needs a targeted chooser test before changing the mapping. |

## Security and touch constraints

Keep sensitive access behind portal requests and user-visible selection/consent. Do not replace the broker with blanket filesystem access or a Flow helper that captures screens directly. Treat screen sharing as an explicit per-session permission; verify stop/revoke behavior, app identity in prompts, and that another local user cannot reuse a session. Portal policy alone does not validate the trustworthiness of backend or shell UI.

Portal dialogs must remain usable with keyboard and touch: focus visibility, reachable buttons, scrollable lists, scaling, dismissal and no accidental consent. Niri's native touch support is useful, but touchscreen and pen coverage in the VM is not proof of physical hardware support. Flow's large-target, orientation, on-screen keyboard and gesture-arbitration requirements remain acceptance work.

## Fallback and validation

If a sandboxed file chooser fails, preserve a normal unsandboxed app's own file dialog as a diagnostic/fallback and keep Nautilus available; do not silently grant broad paths to the sandbox. If screen capture fails, stop the call/recording attempt with a visible explanation and retain ordinary non-capture app use. Fix backend selection/session activation before considering a custom implementation. Keep a graphical recovery path and SSH access while changing portal configuration.

Safe smoke tests on the Niri test session (none run for this ADR):

1. Read-only evaluation: `nix eval --json '.#nixosConfigurations.test.config.xdg.portal.config.niri'` and `nix eval --json '.#nixosConfigurations.test.config.xdg.portal.extraPortals'` to record the effective pinned mapping/packages.
2. In a disposable test user/session, inspect `systemctl --user --no-pager status xdg-desktop-portal.service` and `busctl --user tree org.freedesktop.portal.Desktop`; verify no errors and that a portal owner is present.
3. Open/save a newly created harmless temporary text file through a Flatpak GTK app and an ordinary GTK/Qt app. Confirm the chooser returns only the selected file, cancellation returns no selection, and the ordinary app still has a usable fallback.
4. In a test call, request screen sharing, explicitly choose one window, verify the visible consent/picker and live PipeWire stream, stop sharing, then test cancel/revoke. Use a synthetic screen containing no personal data; do not capture credentials or other users' sessions.
5. Repeat picker and consent flows by keyboard and on a physical touchscreen; record backend/version, test device, prompts, failures and recovery. VM-only results do not close the touch acceptance item.

These are tests to run later; they are not claims that any scenario passed. Avoid mutating portal configuration or rebuilding/restarting the VM as part of this research task.

## References

- [Pinned NixOS Niri module](https://github.com/NixOS/nixpkgs/blob/f4a03c008967841f2a0b3cb7c436fc6fc31671f6/nixos/modules/programs/wayland/niri.nix)
- [Pinned NixOS portal module](https://github.com/NixOS/nixpkgs/blob/f4a03c008967841f2a0b3cb7c436fc6fc31671f6/nixos/modules/config/xdg/portal.nix)
- [Niri upstream: Important Software / Portals](https://github.com/niri-wm/niri/wiki/Important-Software#portals)
- [Niri upstream: Gestures](https://github.com/niri-wm/niri/wiki/Gestures)
- [XDG Desktop Portal documentation](https://flatpak.github.io/xdg-desktop-portal/docs/)
- Local context: `docs/engineering/REUSE_FIRST_REFERENCE_ATLAS.md`, `modules/flow.nix`, `docs/Flow_MASTER_SPEC_v1.1.md` (F-012, F-016).
