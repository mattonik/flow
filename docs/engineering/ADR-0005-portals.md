# ADR-0005: XDG portals for Niri

**Status:** Accepted; amended 2026-11-26 after F-012's runtime tests (Screenshot → Flow's own backend), and
2026-11-27 (the overlap fixed in the shell, ADR-0013)  
**Date:** 2026-11-13, amended 2026-11-26 and 2026-11-27

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

Safe smoke tests on the Niri test session:

1. Read-only evaluation: `nix eval --json '.#nixosConfigurations.test.config.xdg.portal.config.niri'` and `nix eval --json '.#nixosConfigurations.test.config.xdg.portal.extraPortals'` to record the effective pinned mapping/packages.
2. In a disposable test user/session, inspect `systemctl --user --no-pager status xdg-desktop-portal.service` and `busctl --user tree org.freedesktop.portal.Desktop`; verify no errors and that a portal owner is present.
3. Open/save a newly created harmless temporary text file through a Flatpak GTK app and an ordinary GTK/Qt app. Confirm the chooser returns only the selected file, cancellation returns no selection, and the ordinary app still has a usable fallback.
4. In a test call, request screen sharing, explicitly choose one window, verify the visible consent/picker and live PipeWire stream, stop sharing, then test cancel/revoke. Use a synthetic screen containing no personal data; do not capture credentials or other users' sessions.
5. Repeat picker and consent flows by keyboard and on a physical touchscreen; record backend/version, test device, prompts, failures and recovery. VM-only results do not close the touch acceptance item.

The interactive chooser and screen-share scenarios remain untested. Avoid changing portal configuration until there is evidence it is needed.

### Read-only VM access check (2026-11-13)

Using `/Applications/UTM.app/Contents/MacOS/utmctl` without opening or manipulating the VM GUI, `utmctl list` and `utmctl status "Flow NixOS"` reported the existing VM as `started`. `utmctl file pull` successfully read the guest's `/etc/os-release` (NixOS 26.11, build `26.11.20261107.f4a03c0`), and `utmctl ip-address` returned guest addresses. This confirms basic QGA file and network queries only.

Using absolute guest command paths and the logged-in user's session environment, `systemctl --user is-active xdg-desktop-portal.service` returned `active`, and `busctl --user status org.freedesktop.portal.Desktop` showed an owner running xdg-desktop-portal 1.22.1 as that user. `busctl --user tree` showed the desktop portal object. The GTK and GNOME portal services were also active.

A disposable unsandboxed Gio client requested `FileChooser.OpenFile`. The Nautilus chooser appeared and Escape returned response code 1. A second request provided a NUL-terminated temporary directory, and keyboard selection returned success and exactly the URI of the disposable test file. The first probe omitted the required NUL terminator and opened Home instead; the corrected request opened the test directory. The cancel probe did not record whether the returned URI array was empty. These checks do not establish sandbox permissions, file reading, save, capture, or touch behavior. Continue with a sandboxed app and the other interactive scenarios above.

## Amendment after F-012's tests (2026-11-26)

`tests/niri_portals_smoke.py` exercised every portal F-012 names in the guest (see *F-012 acceptance* in
`docs/IMPLEMENTATION_STATUS.md`). Niri's defaults are kept but for Screenshot. The choosers also found a Flow bug,
which is fixed apart.

- **FileChooser stays GNOME's (Nautilus's chooser).** Its Open and Save buttons sit at the bottom right of its
  window, where Flow's work indicator sits over every window (*03 / 03*, or *Layer · Files · Make space · Close* over
  a floating dialog such as Firefox's upload), so a click on Save reaches the indicator, and over Firefox's upload
  dialog it would hit *Close*. That is Flow's bug, not the backend's: the island and the home indicator cover
  apps' controls the same way, and any app's bottom-right buttons are covered. A band reserved for the
  indicators was tried and dropped: the spec has the island float over content, not take a strip of the screen.
  The fix, apart from this change, is indicators that get out of the way. *Fixed 2026-11-27*
  ([ADR-0013](ADR-0013-bands-give-way.md)): the moment the pointer comes onto a band over an app, the band fades
  and its input region shrinks to its screen-edge rows, so a click on Open or Save, with no rest, is the app's
  (`chooser-pointer` passes). The band is the shell's again through its screen edge or Super+B, and comes back once
  the pointer has left it. GTK's chooser
  (`programs.niri.useNautilus = false`) was tried and works by pointer, with its buttons in the header bar, but it
  only moves the problem: the island covers the middle of its Name field, it is GTK 3 and apart from Files (Flow's
  file manager, which the GNOME chooser is), and with the overlap fixed in the shell there is no reason left to
  leave Niri's default.
- **Screenshot → Flow** (`session/screenshot_portal.py`, `org.freedesktop.impl.portal.desktop.flow`). GNOME's
  backend answers `interactive: true` by calling `org.gnome.Shell.Screenshot.InteractiveScreenshot`, which Niri
  26.04 does not implement (it has `Screenshot` and `PickColor`), so every interactive request failed with answer 2.
  Common tools send it: GIMP 3 always ([screenshot-freedesktop.c](https://gitlab.gnome.org/GNOME/gimp/-/blob/c03dfa417c20f31561327df9637c2c730b1c51ca/plug-ins/screenshot/screenshot-freedesktop.c#L187)),
  ksnip in every mode but full screen, and Flathub's OCR and QR tools (Gradia, Decoder, Frog, TextSnatcher).
  Flameshot sends `false`; Firefox, OBS, Krita, Inkscape and LibreOffice do not use the interface; GTK's and Qt's
  colour pickers use PickColor. Niri has no implementation in a release or a pull request
  ([niri#1983](https://github.com/niri-wm/niri/issues/1983), open; [niri#4820](https://github.com/niri-wm/niri/pull/4820)
  lays groundwork). Of the existing backends, GTK's dropped Screenshot in 1.15.0, KDE's needs KWin, and
  `xdg-desktop-portal-wlr` would answer on Niri (grim and slurp) but saves every screenshot to the fixed path
  `/tmp/out.png`, shared between requests and readable by other local users under the default umask. So the
  rejection of a Flow-authored backend above no longer holds, for this one interface. Flow's asks through GTK's
  Access dialog (*Take Screenshot* or *Cancel*, naming the app as the broker passes it) for every interactive
  request, and for any other the broker did not check: the broker skips its own permission check while it has not
  read the backend's version, after the backend left the bus for one, so only `permission_store_checked` lets a
  request through without a dialog. It captures with grim, the whole layout and every display, into a new file under
  `~/Pictures/Screenshots` that it creates itself (mode 0600, never an existing file). Niri's own D-Bus `Screenshot`
  was the first choice and was dropped: it refuses more than one display, never replies when its folder is missing,
  writes wherever Niri's `screenshot-path` says (`/tmp/screenshot.png` when that is null, the defect that ruled out
  `xdg-desktop-portal-wlr`), and hands concurrent callers each other's results. Area and window choices are not
  offered: Niri's own screenshot UI reports nothing on D-Bus when the user cancels it, which would leave the request
  waiting. PickColor goes to Niri's picker; a cancelled pick answers 1. Every request is answered once, whatever
  fails, and nothing is captured once the broker has closed it. The search for an upstream fix was of Niri's issues,
  pull requests and discussions on GitHub, and `src/dbus/gnome_shell_screenshot.rs` on main, on 2026-11-26.
  Retire it once Niri implements `InteractiveScreenshot`.
- **PipeWire.** Screen casting needs it, and NixOS's `graphical-desktop` module, which Niri's session enables,
  turns it on by default (a Flow `mkDefault true` would be a no-op, and `false` conflicts with it). Flow's module
  check asserts it, with WirePlumber, and the portal routing above.

Kept as Niri's module chose them: FileChooser and ScreenCast (GNOME; Niri's Mutter ScreenCast API offers DMA-BUF
buffers only), Access and Notification (GTK), Secret (GNOME Keyring).

## References

- [Pinned NixOS Niri module](https://github.com/NixOS/nixpkgs/blob/f4a03c008967841f2a0b3cb7c436fc6fc31671f6/nixos/modules/programs/wayland/niri.nix)
- [Pinned NixOS portal module](https://github.com/NixOS/nixpkgs/blob/f4a03c008967841f2a0b3cb7c436fc6fc31671f6/nixos/modules/config/xdg/portal.nix)
- [Niri upstream: Important Software / Portals](https://github.com/niri-wm/niri/wiki/Important-Software#portals)
- [Niri upstream: Gestures](https://github.com/niri-wm/niri/wiki/Gestures)
- [XDG Desktop Portal documentation](https://flatpak.github.io/xdg-desktop-portal/docs/)
- Local context: `docs/engineering/REUSE_FIRST_REFERENCE_ATLAS.md`, `modules/flow.nix`, `docs/Flow_MASTER_SPEC_v1.1.md` (F-012, F-016).
