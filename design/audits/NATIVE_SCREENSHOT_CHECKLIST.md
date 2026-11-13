# Native capture checklist — Floe Desk, Island, menus, Overview

This is a human/agent execution checklist for **Codex on the user's NixOS/UTM guest**. It is not a report of work executed from the GitHub connector. Every screenshot must be a native Niri/Quickshell capture, not a browser design preview.

## Before capture

- Check out the currently intended version of \`main\` in the guest and record its commit SHA. Do **not** assume the working tree is clean.
- Preserve current app work and settings, do not wipe the session or overwrite pinned state.
- In the guest record \`niri msg -j outputs\`, \`niri msg -j windows\`, \`niri msg -j workspaces\`, \`niri --version\`, Quickshell version, and the current appearance preference. Avoid uploading private window titles or tokens; redact before publishing.
- Verify a screenshot destination configured in \`screenshot-path\`. Niri's \`screenshot-screen\` action saves the focused output. If using \`niri msg action\`, first run \`niri msg action --help\` for the installed version. Use this action only after confirming its behavior. No external screenshot tool is necessary.
- Use three disposable apps (e.g. Foot, Kate and Files) with non-sensitive content. One two-window Group makes the focus-menu test meaningful.

## Required matrix

For each logical size 1280×800, 1024×768, 430×932 (portrait), 320×512 (stress case), and each \`Polar Night\` and \`Arctic Daylight\` theme:

1. **Desk:** 3 pins, long labels, short labels, search focused, settings opened, scroll at bottom and at top.
2. **Island:** normal Work view; verify actual collision with top-left app trigger when experimental app menu is enabled.
3. **Focused app menu:** command menu closed and open; changed focus while popup is open, stale-action message and dismissal.
4. **Quick Settings:** output/network state, media control if available, focus ring; note width and safe margins.
5. **Notifications:** empty, and one synthetic notification with a long title and actions.
6. **Overview:** actual Niri \`Super+O\` surface with live window textures; separately capture Floe \`Super+Tab\` text-based switcher.
7. **Desk continuity:** return from Overview to Work, compare focused window ID and Group member selection before/after.
8. **Accessibility:** Tab order, focus outlines, 200% text scaling, 44px touch targets where available. Actual screen-reader and physical touch remain separate acceptance journeys.

For notched devices also record the cutout geometry and ensure controls never appear beneath it. A simulated notch is not physical-device validation.

## Evidence template per screenshot

\`\`\`text
Commit:
NixOS/Niri/Quickshell versions:
Logical output size and scale:
Physical device or VM:
Theme:
Surface:
Focus (keyboard/pointer):
Space / Group (synthetic identifiers):
File:
Observed defects:
Difference from desired behavior:
\`\`\`

Store only privacy-safe screenshots in an auditable test artifact, never change private workspace state solely to make demos look good. A successful static test does not replace visual acceptance.

Sources: Niri's built-in [screenshot actions](https://github.com/niri-wm/niri/blob/main/docs/wiki/Configuration%3A-Key-Bindings.md) and [path configuration](https://github.com/niri-wm/niri/blob/main/docs/wiki/Configuration%3A-Miscellaneous.md).
