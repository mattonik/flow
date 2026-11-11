# UTM graphics validation record

Status: **PASS for the initial single-display test** (2026-11-11). UTM 5.0.8, QEMU ARM64 `virt`, HVF, 8 GiB RAM and `virtio-ramfb-gl`; NixOS 26.11 pinned generation (kernel 6.18.62, virtio-pci DRM). `glxinfo -B` reported Mesa 26.1.8, renderer `virgl`, direct rendering `Yes`, acceleration `yes`, OpenGL ES 3.0. Niri 26.04 launched directly from SDDM with its tty/DRM backend, selected `/dev/dri/renderD128` and output `Virtual-1`, and displayed Quickshell and two real Foot windows. Niri IPC confirmed two horizontal columns and focus moving from the second to the first. Fuzzel displayed installed desktop entries. This is a functional smoke test, not a frame-rate or physical-input measurement.

The initial Flow config was rejected at login due to a KDL syntax error; the corrected deployed `/etc/niri/config.kdl` passed `niri validate` and was loaded in a fresh session. Niri logged `NoSupportedPlaneFormat` for a separate `Unknown-1` connector on the software framebuffer, while `Virtual-1` was active; it also throttled unexpectedly fast virtual vblanks. Multi-output and animation performance remain unverified.

Record the following when a disposable guest is available:

- Mac model and macOS version; UTM version; QEMU backend and display device/3D settings.
- NixOS revision, guest kernel, Mesa and Niri versions; active DRM device.
- `eglinfo -B` and, where available, `glxinfo -B` output, including renderer; `wayland-info` output.
- Niri starts on the guest console, a real Wayland application renders, and horizontal movement works; note visual glitches and frame pacing.
- Relevant Niri and user-journal errors; whether renderer is hardware-accelerated or software.
- Result: PASS only when all acceleration and interactive checks pass, otherwise FAIL with reproduction steps.

Do not enter account secrets or private file contents in the record. UTM's host renderer and guest renderer are distinct; the host's Metal backend name alone does not establish guest acceleration.

The first locked system build was stopped before activation when the 20 GB guest approached capacity; a full Plasma closure was pulled because the flake pins a newer nixpkgs revision. Unreferenced downloads were garbage-collected. After expanding the guest disk and filesystem to 35 GB, the locked build succeeded and was activated. A guest-local `/etc/nixos/flake.nix` and lock file point at `/opt/flow`; Plasma remains available and is the configured default session.
