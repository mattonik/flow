# UTM graphics validation record

Status: **BLOCKED / not run**. On 2026-11-11 the development host reported `aarch64-darwin` and Nix was available, but no UTM application, `utmctl`, QEMU executable, or accessible UTM guest was found by the local checks. No guest renderer or Niri session was inspected. This does not prove that UTM or accelerated Niri cannot work on this Mac.

Record the following when a disposable guest is available:

- Mac model and macOS version; UTM version; QEMU backend and display device/3D settings.
- NixOS revision, guest kernel, Mesa and Niri versions; active DRM device.
- `eglinfo -B` and, where available, `glxinfo -B` output, including renderer; `wayland-info` output.
- Niri starts on the guest console, a real Wayland application renders, and horizontal movement works; note visual glitches and frame pacing.
- Relevant Niri and user-journal errors; whether renderer is hardware-accelerated or software.
- Result: PASS only when all acceleration and interactive checks pass, otherwise FAIL with reproduction steps.

Do not enter account secrets or private file contents in the record. UTM's host renderer and guest renderer are distinct; the host's Metal backend name alone does not establish guest acceleration.
