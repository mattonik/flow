# Flow

Flow is an experimental, multiplatform Linux session. The authoritative requirements are in [the unmodified v0.9 product brief](docs/Flow_product_brief_v0.9.md); [the v0.9 HTML prototype](docs/Flow_v0.9_Desk_Transition.html) is a design reference, not a native implementation. iPad enablement is a separate project.

**Status: foundation only, not a working MVP.** A single-display ARM64 UTM guest has run the Niri session, Quickshell island, app launcher and two terminal windows; see [graphics validation](docs/graphics-validation.md). Pinned spaces and the remaining MVP requirements are not implemented. See [milestones](docs/milestones.md).

## First: validate the graphics path

Use a disposable ARM64 NixOS guest in UTM's **QEMU** backend with a VirtIO GPU GL/3D-accelerated display. The Apple Virtualization backend is a different graphics path and is not the target of this test. UTM settings and capabilities vary by version. See [UTM's graphics architecture](https://github.com/utmapp/UTM/blob/main/Documentation/Graphics.md) and [Niri's VM guidance](https://github.com/niri-wm/niri/wiki/Getting-Started).

Before installing Flow, enable `programs.niri.enable = true;` and `hardware.graphics.enable = true;` in the guest, install `mesa-demos` and `wayland-utils`, rebuild, and sign into a plain Niri session. In a terminal run `glxinfo -B` (if XWayland is running) and `eglinfo -B`; record the renderer, versions, UTM backend, GPU settings and guest versions. Confirm the renderer is accelerated (not llvmpipe/softpipe), that the active DRM device is the intended VirtIO GPU, and that a real Wayland app opens and moves smoothly. Capture relevant `journalctl --user -b` and compositor errors. A GL device setting alone is not evidence of guest acceleration. Record the outcome in [graphics validation](docs/graphics-validation.md). If Niri fails, stop and diagnose the guest graphics stack before claiming Flow works.

## Install on an existing NixOS guest (after graphics validation)

Add this repository as a flake input to the guest's existing NixOS flake, then add `inputs.flow.nixosModules.default` to the target system's `modules` list and set `services.flow.enable = true;` in that system's configuration. Keep the guest's existing boot, filesystems, networking, account, display-manager and hardware configuration. When combining Niri with Plasma, set `services.displayManager.defaultSession = "plasma";` (or `"niri"`) explicitly to resolve their competing defaults. `nixos-rebuild build --flake .#HOST` first; then `sudo nixos-rebuild switch --flake .#HOST` and select Niri at login. Back up configuration and know how to select the previous NixOS generation before switching. This module installs a system-wide `/etc/niri/config.kdl`; a user's existing `~/.config/niri/config.kdl` takes precedence. It also installs a Fuzzel default. Review both before enabling it on an existing machine.

The initial session uses Niri's horizontal columns and real Linux apps. `Super+Left/Right` navigates, `Super+Return` opens a terminal, `Super+K` opens the installed-app launcher, `Super+Tab` or `Super+O` opens Niri overview, `Super+H` toggles the Desk, `Super+L` locks, and `Super+Shift+/` shows shortcuts. `Super+Shift+E` quits the session. The Desk offers installed-app search, files and a return to work; it overlays existing windows rather than closing them. Its pinned-spaces section is a placeholder. Desk buttons support Tab and Enter or Space; Escape returns to work. The capsule shows an app-picker control and the local clock; the launcher has been verified through Niri IPC, but the capsule click still needs verification. The Desk is an early product surface, and the launcher is not universal search. The app launcher discovers installed desktop entries; it is not a file or action search. Fullscreen, input and lock behavior need guest verification.

## Check and build

From this checkout, with flakes enabled:

```sh
nix flake show
nix eval .#nixosConfigurations.test.config.system.build.toplevel.drvPath
nix build .#nixosConfigurations.test.config.system.build.toplevel
```

The `test` configuration is an **evaluation/build fixture**, not an installable image: it has a placeholder root filesystem, no configured user and no bootloader. Build on ARM64 Linux or configure a suitable remote builder; a macOS ARM64 host cannot execute Linux build steps by itself. After changing the lock file, rebuild and re-run the guest checks. No passwords or private state are included.
