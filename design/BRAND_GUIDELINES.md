# Floe — Arctic Minimalism | Identity and interface language

**Version:** 1.0 · 13 November 2026 · **Status:** reviewed design direction, code assets proposed; not a trademark clearance or shipping screenshot.

## Positioning and relationships

**IceBear** is the creator. **Floe** (pronounced “flow”) is the personal computing environment built around open Linux software. **Flow** is the horizontal spatial interaction and navigation concept within Floe. **Nival** is a reserved *candidate* for a broader connected platform; do not productize or ship that name yet.

Our belief: **Technology that makes room for people.** Our short headline: **Room to think.** These are draft brand messages, not claims about production features.

Floe should feel like a designed instrument that allows users to work rather than manage their OS: calm, spatial, capable, dependable, touch-aware. Arctic imagery is a conceptual language, not a wallpaper theme.

### The identity in five adjectives
**Spacious · Clear · Tactile · Precise · Resilient**

### What Floe is not
No glowing cyan gaming aesthetic, faux snow, ice-crystal buttons, blurred glass on every surface, exaggerated shadows, endless widgets, gratuitous parallax, or sterile pure-white “productivity” screens. Avoid visually merging Floe with the user's unrelated third-party apps.

## Mark and wordmark

The current proposed symbol is **three offset ice plates**. Each element is independent; they form a memorable whole through proximity. Their arrangement echoes adjacent spaces and semantic Groups. The symbol must remain legible as a monochrome app icon at small sizes and as a moving launch sequence.

Assets:
- `design/brand/floe-mark-dark.svg` — three-tonal mark, dark background.
- `design/brand/floe-mark-mono.svg` — single-ink mark for print or system icons.
- `design/brand/floe-lockup-dark.svg` and `floe-lockup-light.svg` — starter lockups, editable vector sources.
- `design/brand/floe-identity-board.svg` — presentation board, not the definitive design system.

Brand typography: lower-case **floe.**, short and quiet. Keep the full stop only in the **wordmark** or brand composition—not in filenames, accessibility labels, application descriptions, or running text. Use “Floe” in prose and in product names such as “Floe Desk”.

- Symbol clear space: at least the apparent width of one plate's leading edge (suggested 12% of symbol height).
- Minimum practical symbol size: 24 × 24 logical px, test again at 16px before shipping a tray icon.
- Never place a text overlay or another badge atop the mark.
- Never stretch, rotate randomly, add outer glow, or apply stock snow/ice effects.
- Use dark lockup against dark backgrounds and daylight lockup against light backgrounds; preserve contrast.

**These are exploratory source assets:** refine kerning, shape balance and small-size appearance in a visual design review.

## Modes: Polar Night and Arctic Daylight

**Polar Night** is the default desktop environment, particularly for focused work in the evening. Polar Night stays near-black without a crushing pure black panel look.

**Arctic Daylight** is a first-class, *low-glare* light mode. It must not appear like white cards on a white canvas. Its foundation is fogged blue-green light, chalked mineral panels and dark, legible ink. Use soft elevation and clear borders rather than brightness to establish hierarchy.

| Role | Polar Night | Arctic Daylight |
|---|---|---|
| Canvas | `#0B1218` | `#DFE9E6` |
| Surface | `#1C2D36` | `#EAF0EC` |
| Raised surface | `#13232C` | `#F2F5F0` |
| Selected surface | `#263C46` | `#D1E1DA` |
| Primary text | `#EBF1EB` | `#153139` |
| Secondary text | `#BDDAD1` | `#28584C` |
| Muted text | `#8FA8AA` | `#405A5F` |
| Focus / accent | `#BDDAD1` | `#28584C` |

The five original brand primitives remain **Polar Night, Deep Ice, Glacial Stone, Sea Glass, Snow Light**. Arctic Daylight adds softer companion neutrals; do not alter the five-color brand signature simply because the interface has another mode.

### Theme semantics

Never rely on literal colors copied into widgets. Use the semantic `canvas`, `surface`, `surface-raised`, `text-primary`, `text-muted`, `focus`, `danger`, etc. from `design/tokens/floe.tokens.json`.

Color is a relationship: a mint intended for dark surfaces cannot automatically be used as accessible small text on daylight surfaces. Do not put `#667F86` as tiny text on Arctic Daylight. Use `#405A5F` or darker instead.

Provide strong keyboard focus rings; never communicate selected state by tint alone. Danger, warning, and success colors must be verified separately against each surface. Use `prefers-reduced-motion` and accessible fallbacks.

Do not recolor third-party apps without their consent or supported theme hooks. NixOS may package desktop settings and portal preferences, but that is separate engineering work.

## Typography

Interface: **Inter** with system sans fallback. Brand / wordmark exploration: **Manrope** or a manually tuned geometric/humanist wordmark. Technical detail: **IBM Plex Mono**, reserved for addresses, shortcuts, device information, and diagnostics—not long paragraphs.

Target UI scale: 11 micro / 12 caption / 14 small / 16 body / 18 title small / 24 medium / 32 large / 52 display (logical px). Use restrained 400/500/600 weights and 1.5 body line-height. Do not force a remote font download. Confirm font package licenses and NixOS availability before bundling.

## Geometry and composition

Grid: 4px with an 8px practical baseline. Default card radius 20px, panels 14px and small controls 10px. The Island is the primary place where a capsule silhouette is meaningful; don't use pill-shaped controls everywhere. Prefer a 1px quiet boundary to strong drop shadows.

- Desk: expansive quiet canvas, universal search as the visual priority, small precise list of pinned spaces, minimal tools.
- Overview: real app previews keep their spatial identities and order; pinned spaces remain leftmost. Groups have one spatial boundary and distinguishable member windows.
- Work: full available area belongs to application content; shell controls are unobtrusive, safe-area aware and never unnecessarily reduce the usable canvas.
- Island: system only. App menus belong to the focused app; Group arrangement controls belong to the Group. Preserve native app menus as fallback.
- Desktop without a notch: quiet system affordance, not a thick permanent panel.
- Tablet: comfortable hit targets, predictable edge gestures and clear focus/hover substitutes. The design is touch-first-class, not a mouse UI scaled up.

## Interaction and motion

One stable spatial model across keyboard, touch and pointer. **Magnetic Flow** navigates between app spaces; motion follows physical intent. App animations must not suggest a change of location if the underlying space did not move.

Suggested motion tokens (design targets, not a claim of native Niri support): quick 120ms, standard 220ms, spatial 380ms, deliberate 520ms, standard curve `cubic-bezier(.22,1,.36,1)`.

- Work → Overview: zoom out around the actual active space.
- Overview → Desk: push app surfaces away to reveal the desk beneath, not a hard route replacement.
- Reversing motion returns to the same anchored space.
- Pin/group actions have short, subtle confirmation; no bouncing or elastic spectacle.
- Reduced motion: instant mode switch / no parallax while retaining order, focus and discoverability.

## Component principles

- Buttons: rest, hover (desktop), active, selected, focused, disabled, loading and destructive states. Use a robust 2px keyboard ring.
- Touch target minimum: 44 × 44 logical pixels; aim for 52px in primary tablet actions.
- Menus: focus-aware, distinguish app vs Group commands; Escape/outside dismissal must be predictable.
- Inputs: clear labels or accessible names; text area must remain visible above on-screen keyboard.
- Notifications: concise, low-interruption, grouped by source; action contrast must be preserved in light and dark.
- Status: do not rely solely on red/green. Audio, network, battery and login status must have text or programmatic labels.

## Voice and writing

Concise, calm and capable. “Your spaces”, “Return to work”, “Pin space”, “Choose a display” and “Save session” are good. Avoid hype (“revolutionary”), anthropomorphic slogans, and poetic error messages. Error text should say what failed, what was preserved, and how to recover.

## Brand across IceBear properties

Share the underlying primitive palette, typography rhythms, accessible contrast and motion values. Don't automatically apply Floe's navigation model to marketing websites. Adapt contrast and density to each product. The separate senior-oriented Android launcher should prioritize legibility and warm, predictable interactions rather than duplicating Floe's dark mode.

## Implementation boundary and review

Source-of-truth: `design/tokens/floe.tokens.json`. Generated outputs: `design/generated/floe.css` and `session/FloeTheme.qml`. Future Figma variables, app SDKs and system themes should derive from these semantic tokens, not create their own values. Generator is in `design/scripts/generate_theme.py`.

Acceptance:
1. Symbol works on near-black and muted light backgrounds, in mono and at realistic sizes.
2. WCAG contrast checks pass for primary, secondary and muted text on canvas/surface/raised.
3. Floe Desk, Overview, Island and app menu remain coherent in both modes.
4. Theme switching persists per user and doesn't disturb live applications; verify in a real Niri+Quickshell session before claiming functional.
5. Run the same design under portrait/tablet and desktop dimensions, including reduced-motion mode.

**Do not confuse token availability with a validated runtime design or test pass.**
