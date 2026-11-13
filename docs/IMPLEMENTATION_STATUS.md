# Implementation status against Flow Master Spec v1.1

Updated 2026-11-13. This is a repository and recorded-evidence audit, not a new full acceptance run. **Partial** means some relevant behavior exists but the whole journey is not established. **Open** means the acceptance journey has not been demonstrated. See [milestones](milestones.md) and [graphics validation](graphics-validation.md) for prior evidence and its limits. Recent targeted checks ran in the UTM guest; they do not validate every journey below.

The recorded test environment is an Apple Silicon Mac running UTM 5.0.8, QEMU ARM64 with HVF, 8 GiB RAM and virtio-ramfb-gl; NixOS 26.11 with Niri 26.04 and virgl acceleration. Exact host model and physical input details were not recorded. No journey below is promoted to a full pass solely from the HTML prototypes or from unit tests.

| ID | Status | Evidence and remaining acceptance gap |
|---|---|---|
| F-001 Native session | Partial | Niri, Quickshell, real apps and login were exercised; Plasma remains installed as a fallback. Warm reboot hung in UTM; fallback login was not newly verified. |
| F-002 Three real apps | Partial | Files, terminal, editors and browser ran in the guest; focus movement has been exercised. The exact three-app end-to-end journey needs a recorded repeat with before/after focus. |
| F-003 Pinned positions | Partial | Single-app pins and recovery exist; an unavailable pin no longer blocks ordering eligible pins on the current workspace, verified with temporary guest windows. Pinned Groups, display-scoped positions and the complete specified restart journey remain absent. |
| F-004 Shortcut accuracy | Partial | Single-display Super+number pin focus exists; display-number sequences and multi-display direct routing do not. |
| F-005 Group identity | Partial | An unambiguous two-app stack has a saved name and ID in the explicit session snapshot. A mixed Group/single-window ordering was recovered using disposable guest windows; pinned, incomplete or ambiguous cases may need manual ordering. Adaptive orientation, independent semantic persistence and the full lifecycle remain open. |
| F-006 Layer dismissal | Open | No verified Flow Layer model or Group-preserving dismissal journey. |
| F-007 Desk/Overview | Partial | In the ARM64 guest, opening and closing Desk and Niri Overview retained the same four window IDs, workspace, scrolling positions and focused window. Gesture, touch, multi-display and visual-transition acceptance remain open. |
| F-008 Search | Partial | Search covers recent local files within the user’s home folder, Flow settings, quick settings, installed applications, open windows and saved pins, including closed apps. File indexing and commands are missing. The settings shell actions were checked through IPC. A recent-file result from an isolated history was selected in the guest and opened in Kate; naturally populated history and the settings results still need end-to-end validation. |
| F-009 Island/system | Partial | Island, quick settings, lock and a temporary notification inbox exist. Quick Settings opens the connection-profile editor and controls one unambiguous MPRIS player. Flow Wi-Fi selection, durable notification history and independent availability across all contexts are not established. |
| F-010 Real accounts | Open | Two-user and guest isolation and switching have not been verified. |
| F-011 Display hotplug | Open | No verified external-display disconnect and reconnect journey. |
| F-012 Portal correctness | Partial | The guest's portal broker is active and owns its session D-Bus name. A disposable unsandboxed chooser request returned the selected test-file URI, and a second request returned a cancel response. Sandboxed file access, saving, screenshots, screenshare, clipboard and drag/drop remain unverified. |
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

The shell listens to Niri focus and workspace active-window events and keeps an open application menu's context unchanged if focus moves or its window closes. It disables commands and asks the user to close the menu; the current app is re-identified afterward. Invocation also rechecks the active Niri window and command before calling AT-SPI. In the ARM64 VM, KWrite and Kate formed a real two-window stack; changing the active member while KWrite's menu was open retained its label, disabled commands and showed the warning. Dismissing it updated the label to Kate; Kate's New command created a document, and a stale KWrite command was rejected. Window closure, pointer focus, other toolkits and multi-display cases remain unverified. App commands are kept separate from the switcher's stack and rename actions. Two windows sharing a process intentionally fall back rather than being guessed. There is no independently running Flow Controller or versioned command-provider API yet; `flow-spaces` is a local experimental CLI, not the specified API.

The master spec marks the contextual menu as an approved hypothesis, whereas the design note calls its UX direction approved. Treat the focus and separation principles as the supplied direction while retaining prototype validation and implementation status as open; no finalized D-Bus contract or cross-toolkit support is implied.

The smallest next contract to investigate is a read-only, versioned snapshot of the current session's spaces and active application, plus a focus request restricted to the trusted shell. Define stable identity, caller authorization, event ordering, reconnection and structured errors before publishing a D-Bus interface; do not expose window-control capabilities to ordinary applications by default. These are design questions, not implemented method names or security guarantees.
