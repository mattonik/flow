# ADR-0008: Floe semantic light/dark theming

**Status:** Implemented in the shell; native validation is partial (13 November 2026)

Floe's Arctic Minimalism uses **Polar Night** as the default dark appearance and **Arctic Daylight** as a low-glare, mist-green light appearance. Both are specified in `design/tokens/floe.tokens.json`.

The shell now uses the `FloeTheme.qml` QtObject with semantic readonly color properties, staged by the existing NixOS module beside `shell.qml`. The user selects Dark or Light from Floe Desk → Settings. A per-user Core.Settings `floe-appearance.ini` persists `lightMode`. This does not close running apps or change semantic spaces.

The integration intentionally **does not** change Niri configuration colors, Flatpak/GTK/Qt themes, Fuzzel, XDG portal settings, OS-wide light/dark preferences, or authentication UI. These need separate implementation and platform testing. Do not infer that the entire system becomes light with this shell-only toggle.

**Validation so far:** In the ARM64 UTM guest, both Desk themes rendered, keyboard switching worked, Arctic Daylight persisted through a Quickshell restart, and the existing Group remained visible in the switcher. The original dark preference was restored. This does not establish a full-system theme or validate all panels.

**Remaining verification:** Run the static tests in `tests/test_floe_theme.py` (token parity and AA text contrast). Codex must launch the actual Quickshell/Niri guest, validate QML loading from `/etc/flow/FloeTheme.qml`, check all shell panels and native menu text, change the theme, close/reopen Desk, restart shell and log in again, confirm per-user persistence, and verify app focus and window arrangement remain unchanged. Screenshot both modes at desktop and portrait dimensions. Roll back using the last working NixOS generation if QML fails to load.

**Future:** Generate QML/CSS deterministically from tokens; expose XDG appearance preference with permission boundaries and an opt-in follow-system mode; align Niri focus rings and Fuzzel; support reduced motion and configurable high contrast.
