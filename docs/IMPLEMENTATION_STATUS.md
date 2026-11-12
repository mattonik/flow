# Implementation status against Flow Master Spec v1.1

Updated 2026-11-12. This is a repository and recorded-evidence audit, not a new full acceptance run. **Partial** means some relevant behavior exists but the whole journey is not established. **Open** means the acceptance journey has not been demonstrated. See [milestones](milestones.md) and [graphics validation](graphics-validation.md) for prior evidence and its limits. The UTM guest was stopped during this audit; its current runtime state was not checked.

The recorded test environment is an Apple Silicon Mac running UTM 5.0.8, QEMU ARM64 with HVF, 8 GiB RAM and virtio-ramfb-gl; NixOS 26.11 with Niri 26.04 and virgl acceleration. Exact host model and physical input details were not recorded. No journey below is promoted to a full pass solely from the HTML prototypes or from unit tests.

| ID | Status | Evidence and remaining acceptance gap |
|---|---|---|
| F-001 Native session | Partial | Niri, Quickshell, real apps and login were exercised; Plasma remains installed as a fallback. Warm reboot hung in UTM; fallback login was not newly verified. |
| F-002 Three real apps | Partial | Files, terminal, editors and browser ran in the guest; focus movement has been exercised. The exact three-app end-to-end journey needs a recorded repeat with before/after focus. |
| F-003 Pinned positions | Partial | Single-app pins and recovery exist; pinned Groups, display-scoped positions and the specified restart journey are absent. |
| F-004 Shortcut accuracy | Partial | Single-display Super+number pin focus exists; display-number sequences and multi-display direct routing do not. |
| F-005 Group identity | Partial | An unambiguous two-app stack has a saved name and ID in the explicit session snapshot. Adaptive orientation, independent semantic persistence and the full lifecycle remain open. |
| F-006 Layer dismissal | Open | No verified Flow Layer model or Group-preserving dismissal journey. |
| F-007 Desk/Overview | Partial | Desk overlays real windows and Niri overview displays them. Complete before/after checks of IDs, position and focus for both transitions are not recorded. |
| F-008 Search | Partial | Search covers installed applications, open windows and available pins; settings, files and commands are missing. |
| F-009 Island/system | Partial | Island, quick settings and lock exist; notification history and independent availability across all contexts are not established. |
| F-010 Real accounts | Open | Two-user and guest isolation and switching have not been verified. |
| F-011 Display hotplug | Open | No verified external-display disconnect and reconnect journey. |
| F-012 Portal correctness | Open | Representative file chooser, screenshot, screenshare, clipboard and drag/drop matrix is not verified. |
| F-013 Audio/peripherals | Open | Volume control was exercised; a Bluetooth headset and microphone were not tested on physical hardware. |
| F-014 Figma day | Open | No recorded Figma workload, screenshare or browser-load test. |
| F-015 Development day | Open | Terminal and editors run, but the native-host development, container and VM workflow is not verified. |
| F-016 Touch day | Open | No real touch, rotation or tablet Group testing. |
| F-017 API app test | Open | No versioned Flow Platform API, non-Qt client or authorization test exists. |
| F-018 Shell restart | Partial | The shell was restarted without closing guest apps; a recorded before/after pinned-state integrity test is still needed. |
| F-019 DisplayLink hardware | Open | No named dock or monitor test. |
| F-020 App menu fallback | Partial | The opt-in AT-SPI experiment showed and invoked Kate commands; native menus are retained. Coverage, same-process ambiguity and the complete fallback journey remain unverified. |
| F-021 Sync conflict | Open | No sync provider or conflict-resolution journey. |

## Contextual-menu gap and next investigation

The current shell listens to Niri focus events and clears an open application menu when a different window receives focus. The new design note calls for freezing the displayed context until the interaction ends; these behaviors differ and need a focused real-window test and a safe invocation policy. Invocation already rechecks the active Niri window and command before calling AT-SPI. App commands are kept separate from the switcher's stack and rename actions. Two windows sharing a process intentionally fall back rather than being guessed. There is no independently running Flow Controller or versioned command-provider API yet; `flow-spaces` is a local experimental CLI, not the specified API.

The master spec marks the contextual menu as an approved hypothesis, whereas the design note calls its UX direction approved. Treat the focus and separation principles as the supplied direction while retaining prototype validation and implementation status as open; no finalized D-Bus contract or cross-toolkit support is implied.

The smallest next contract to investigate is a read-only, versioned snapshot of the current session's spaces and active application, plus a focus request restricted to the trusted shell. Define stable identity, caller authorization, event ordering, reconnection and structured errors before publishing a D-Bus interface; do not expose window-control capabilities to ordinary applications by default. These are design questions, not implemented method names or security guarantees.
