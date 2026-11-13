# ADR 0007: Investigate an on-screen keyboard

Status: candidate, not integrated; initial ARM64 guest rendering check on 2026-11-13.

First-class touch needs text input, but Flow has no on-screen keyboard. The smallest reuse candidate at the pinned nixpkgs revision is [wvkbd 0.21](https://github.com/jjsullivan5196/wvkbd/blob/v0.21/wvkbd.1.scd), GPL-3.0-or-later. It is a separate Wayland virtual-keyboard client with layer-shell presentation and explicit show/hide signals. The module can package it without copying its code or changing the toolkit-independent Flow API. Its automatic mode relies on input-method support and has not been validated in this session.

In the existing ARM64 NixOS/Niri guest, a temporary store-only package launch displayed the keyboard under a real Foot window. This is a rendering check only: key delivery, modifiers, compositor shortcuts and physical touch were not verified. The default landscape keys were small, so usable sizing and portrait layout need measurement before adopting the UI. No keyboard service or Flow configuration was changed.

The pinned Niri 26.04 predates [Niri's virtual-keyboard keymap and shortcut fix](https://github.com/niri-wm/niri/pull/4548); do not assume virtual keys can activate compositor shortcuts. Test ordinary typing and Enter separately from shortcut handling. Other candidates include Squeekboard and Maliit; their input-method integration, dependencies and touch behavior also need testing on this Niri version.

Next test: with a disposable Foot window focused, use the virtual keyboard to enter harmless text and Enter, check modifiers, show/hide and application focus, then test keyboard shortcuts separately. Repeat with real touch, portrait/landscape and lock-screen behavior before enabling it by default. If the guest probe succeeds, expose a manual, accessible Flow control before considering automatic text-field detection. Reverting the candidate requires closing its process; no persistent configuration is involved.
