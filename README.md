# Flow

Flow is an experimental, multiplatform Linux session. The authoritative requirements are in [the unmodified v1.0 product brief](docs/Flow_product_brief_v1.0.md); [the v1.0 HTML prototype](docs/Flow_v1.0_Contextual_Menus.html) is a design reference, not a native implementation. iPad enablement is a separate project.

**Status: foundation only, not a working MVP.** A single-display ARM64 UTM guest has run the Niri session, Quickshell island, app launcher and two terminal windows; see [graphics validation](docs/graphics-validation.md). Pinned spaces are an experimental single-display implementation; other MVP requirements remain incomplete. See [milestones](docs/milestones.md).

## First: validate the graphics path

Use a disposable ARM64 NixOS guest in UTM's **QEMU** backend with a VirtIO GPU GL/3D-accelerated display. The Apple Virtualization backend is a different graphics path and is not the target of this test. UTM settings and capabilities vary by version. See [UTM's graphics architecture](https://github.com/utmapp/UTM/blob/main/Documentation/Graphics.md) and [Niri's VM guidance](https://github.com/niri-wm/niri/wiki/Getting-Started).

Before installing Flow, enable `programs.niri.enable = true;` and `hardware.graphics.enable = true;` in the guest, install `mesa-demos` and `wayland-utils`, rebuild, and sign into a plain Niri session. In a terminal run `glxinfo -B` (if XWayland is running) and `eglinfo -B`; record the renderer, versions, UTM backend, GPU settings and guest versions. Confirm the renderer is accelerated (not llvmpipe/softpipe), that the active DRM device is the intended VirtIO GPU, and that a real Wayland app opens and moves smoothly. Capture relevant `journalctl --user -b` and compositor errors. A GL device setting alone is not evidence of guest acceleration. Record the outcome in [graphics validation](docs/graphics-validation.md). If Niri fails, stop and diagnose the guest graphics stack before claiming Flow works.

## Install on an existing NixOS guest (after graphics validation)

Add this repository as a flake input to the guest's existing NixOS flake, then add `inputs.flow.nixosModules.default` to the target system's `modules` list and set `services.flow.enable = true;` in that system's configuration. Keep the guest's existing boot, filesystems, networking, account, display-manager and hardware configuration. When combining Niri with Plasma, set `services.displayManager.defaultSession = "plasma";` (or `"niri"`) explicitly to resolve their competing defaults. `nixos-rebuild build --flake .#HOST` first; then `sudo nixos-rebuild switch --flake .#HOST` and select Niri at login. Back up configuration and know how to select the previous NixOS generation before switching. This module installs a system-wide `/etc/niri/config.kdl`; a user's existing `~/.config/niri/config.kdl` takes precedence. It also installs a Fuzzel default. Review both before enabling it on an existing machine.

The initial session uses Niri's horizontal columns and real Linux apps. `Super+Left/Right` navigates, `Super+Return` opens a terminal, `Super+K` searches installed apps, open windows and available pinned spaces, `Super+Tab` opens the bottom window switcher, `Super+O` opens Niri overview, `Super+H` toggles the Desk, `Super+L` locks, and `Super+Shift+/` shows shortcuts. `Super+Shift+E` quits the session. The Desk offers search, files and a return to work; it overlays existing windows rather than closing them. Pin the current single-window app with `Super+P` or the Desk button, select a pin from the Desk or with `Super+1`–`Super+9`, and use its arrow and × buttons to reorder or unpin. Desk buttons support Tab and Enter or Space; Escape returns to work. The capsule shows an app-picker control and the local clock; the launcher has been verified through Niri IPC, but the capsule click still needs verification. The Desk is an early product surface. Search covers installed applications and windows and pins on the current workspace; it does not yet search files, app commands or settings. The separate Apps launcher still lists installed desktop entries. Fullscreen, input and lock behavior need guest verification.

The bottom handle opens a horizontally scrollable list of the current workspace’s open windows; select one to focus it, or press Escape to dismiss it. This first version shows window names, not live previews, and has not yet been validated for touch or multiple displays.

**Session recovery is experimental.** Before restarting, save your documents in their applications, open the Desk and choose **Save session**. After signing in to Niri, open the Desk and choose **Restore session**. Flow records the current workspace's ordered window list and focused app in `~/.local/state/flow/session.json`, and retains the snapshot until you explicitly save again. It tries to relaunch each missing app through its exact installed desktop entry, order unique single-window apps, and restore focus. The result reports applications that need manual recovery. Existing pinned-space metadata is stored separately.

This does not save unsaved documents, app-specific internal state, terminal processes, multiple windows of the same app, Groups, other workspaces or multiple displays. Apps without a matching desktop entry or with ambiguous windows require manual recovery. Never treat the snapshot as a backup of files. VM restart recovery has not yet been verified end to end; save your work separately.

## Experimental application menu

Open the Desk with `Super+H`, choose **Settings**, and enable **Experimental application menu**. The preference is off by default and saved per user in `$XDG_CONFIG_HOME/flow-settings.ini` (normally `~/.config/flow-settings.ini`). With a work window focused, use the top-left app button or `Super+M` to open its commands. Navigate with Tab and Enter or Space; Escape closes the menu. The switch has been enabled in the current test VM for evaluation.

Flow reads and invokes the focused application's exposed AT-SPI accessibility menu. It enables the user session's accessibility status when first used and leaves it enabled when the switch is turned off so other accessibility tools are not disrupted. Applications that do not expose a compatible menu show a fallback; their own menus remain available. Multiple windows sharing an application process are treated as ambiguous and also fall back. Menu coverage and responsiveness depend on the application; keyboard shortcut labels, checked states, desktop menu-bar layout and command search are not yet implemented. This experiment has been exercised with Kate, not every app toolkit.

Pins currently work on one active display and its focused Niri workspace, one window per app ID. They are stored per user in `$XDG_STATE_HOME/flow/spaces.json` (normally `~/.local/state/flow/spaces.json`). Flow keeps open pinned windows at the left in saved order; it cannot reserve a compositor position for an absent app, automatically launch it, restore multiple same-app windows, or handle Groups and multiple displays yet. A missing or ambiguous app remains listed but cannot be opened from its pin. Reopen the app first; if it was closed during the same compositor session, unpin and pin it again. Pin ordering is reconciled periodically and can briefly move focus.

## Check and build

From this checkout, with flakes enabled:

```sh
python3 -B -m unittest discover -s tests -v
nix build .#checks.aarch64-linux.spaces
nix flake show
nix eval .#nixosConfigurations.test.config.system.build.toplevel.drvPath
nix build .#nixosConfigurations.test.config.system.build.toplevel
```

The `test` configuration is an **evaluation/build fixture**, not an installable image: it has a placeholder root filesystem, no configured user and no bootloader. Build on ARM64 Linux or configure a suitable remote builder; a macOS ARM64 host cannot execute Linux build steps by itself. After changing the lock file, rebuild and re-run the guest checks. No passwords or private state are included.
