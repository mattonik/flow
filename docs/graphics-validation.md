# UTM graphics validation record

Status: **PARTIAL** (2026-11-11). UTM 5.0.8, QEMU ARM64 `virt`, HVF, 8 GiB RAM and `virtio-ramfb-gl`; installed NixOS 26.05 guest (kernel 6.18.62, virtio-pci DRM). In Plasma, `glxinfo -B` reported Mesa 26.1.8, renderer `virgl`, direct rendering `Yes`, acceleration `yes`, OpenGL ES 3.0. Niri 26.04 launched nested in Plasma and rendered its hotkey overlay; it exited cleanly. The nested run used the winit backend, not the direct DRM/KMS session. The Flow configuration passed `niri validate` with Niri 26.04. No real application, navigation, native console session, or Niri renderer details have been verified yet; this is not a graphics-gate pass. The nested test generated `~/.config/niri/config.kdl`, which takes precedence over the system Flow config and must be moved aside for Flow testing.

Record the following when a disposable guest is available:

- Mac model and macOS version; UTM version; QEMU backend and display device/3D settings.
- NixOS revision, guest kernel, Mesa and Niri versions; active DRM device.
- `eglinfo -B` and, where available, `glxinfo -B` output, including renderer; `wayland-info` output.
- Niri starts on the guest console, a real Wayland application renders, and horizontal movement works; note visual glitches and frame pacing.
- Relevant Niri and user-journal errors; whether renderer is hardware-accelerated or software.
- Result: PASS only when all acceleration and interactive checks pass, otherwise FAIL with reproduction steps.

Do not enter account secrets or private file contents in the record. UTM's host renderer and guest renderer are distinct; the host's Metal backend name alone does not establish guest acceleration.

The first locked system build was stopped before activation when the 20 GB guest approached capacity; a full Plasma closure was pulled because the flake pins a newer nixpkgs revision. Unreferenced downloads were garbage-collected. The existing Plasma system was not switched. A guest-local `/etc/nixos/flake.nix` and lock file point at `/opt/flow`, with Plasma as the default session; these files have not been deployed.
