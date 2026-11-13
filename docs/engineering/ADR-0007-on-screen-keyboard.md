# ADR 0007: Investigate an on-screen keyboard

Status: manually invoked experimental integration; ARM64 guest pointer-simulation check on 2026-11-13.

First-class touch needs text input, but Flow has no on-screen keyboard. The smallest reuse candidate at the pinned nixpkgs revision is [wvkbd 0.21](https://github.com/jjsullivan5196/wvkbd/blob/v0.21/wvkbd.1.scd), GPL-3.0-or-later. It is a separate Wayland virtual-keyboard client with layer-shell presentation and explicit show/hide signals. The module can package it without copying its code or changing the toolkit-independent Flow API. Its automatic mode relies on input-method support and has not been validated in this session.

Flow packages wvkbd and exposes a Quick Settings toggle and `Super+Shift+K`. It uses a session-scoped runtime PID file to signal its own keyboard, and requests 200-pixel landscape and 280-pixel portrait heights. In the ARM64 NixOS/Niri guest, simulated pointer clicks on the 200-pixel keyboard entered a lowercase letter, Shift plus the uppercase letter, and Enter into a disposable Foot window. The toggle showed, hid and showed the keyboard in an isolated session. This is not a physical-touch or portrait test.

The pinned Niri 26.04 predates [Niri's virtual-keyboard keymap and shortcut fix](https://github.com/niri-wm/niri/pull/4548); do not assume virtual keys can activate compositor shortcuts. Test ordinary typing and Enter separately from shortcut handling. Other candidates include Squeekboard and Maliit; their input-method integration, dependencies and touch behavior also need testing on this Niri version.

Next test: use a physical touchscreen to type into several application toolkits, check portrait and landscape sizing, lock-screen behavior and compositor shortcuts, then evaluate automatic input-method activation. The keyboard is not automatically started when a text field receives focus. Reverting requires removing the module integration and closing any running keyboard process; no persistent keyboard configuration is involved.
