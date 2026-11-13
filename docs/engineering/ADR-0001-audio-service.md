# ADR 0001: Reuse Quickshell's PipeWire audio service

Status: adopted for the current output-volume control after an ARM64 guest test on 2026-11-13; device hotplug and physical touch remain pending.

**User journey:** adjust or mute the active audio output from Flow with touch, mouse or keyboard and see changes made by other apps; report missing audio without stealing focus.

| Option | License / upstream | NixOS and Niri | ARM64 | Touch | Security | Decision |
|---|---|---|---|---|---|---|
| Quickshell 0.3.2 PipeWire service ([upstream](https://quickshell.org/docs/v0.3.2/types/Quickshell.Services.Pipewire/Pipewire/), LGPL-3.0-only in pinned nixpkgs) | Existing Quickshell API, no copied code | Package and existing shell run in guest; audio API not yet verified | Guest probe required | Existing Flow buttons; real hardware pending | Session PipeWire access; no new daemon or permissions | PROTOTYPE |
| Existing `wpctl` / WirePlumber CLI | Existing system service; verify package-specific licenses | Existing control exercised in guest | Exercised | Same UI; real hardware pending | Existing user-session access | Fallback; rollback via prior NixOS generation |
| Pond [audio state](https://github.com/bflycomputer/pond-interface/blob/9277356200ac02c5367587bc7b68a079cb2574e7/shell/audio/State.qml) | BSD-3-Clause; source reference only | Not integrated | Not tested | Not established | Its full shell is trusted in-process code | RESEARCH ONLY |
| Omarchy [media service](https://github.com/omacom/omarchy/blob/ac6cb9c712848741f994d7ecbaccdbab90b526e8/shell/plugins/services/media/Service.qml) | MIT at repository level; source reference only | Hyprland/Arch integration assumptions | Not tested | Not established | Its plugins are not sandboxes | RESEARCH ONLY |

The NixOS lock pins nixpkgs `f4a03c008967841f2a0b3cb7c436fc6fc31671f6` and Quickshell 0.3.2. PipeWire's tracked node is the source of actual output state; the Flow shell presents it, and this does not define or change the toolkit-independent Flow API. A disconnected/missing default output must be clearly unavailable, not a fake zero. Do not copy a foreign shell or add a background daemon.

**Evidence:** Quickshell 0.3.2 loaded in the ARM64 NixOS/Niri guest and the reusable `tests/audio-probe.qml` passed against the default sink. The system build activated and the shell restarted without closing apps. In Quick Settings, an external `wpctl` change from 30% to 40% appeared in the panel; keyboard activation of +5% and mute produced 45% muted, confirmed by `wpctl`. External commands restored the original 30% unmuted, reflected in the panel. UI screenshots were observed during the test. The existing Python suite passed. No process-count, CPU or battery improvement is claimed without measurement.

**Remaining:** exercise −5%, mouse and real touch, missing sink, service restart/reconnect, alternate outputs and hardware audio. The prior NixOS generation remains the rollback; `wpctl` is retained as a diagnostic and fallback tool.
