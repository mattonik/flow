# Floe — WCAG 2.2 contrast audit: Polar Night + Arctic Daylight

**Date:** 13 November 2026. **Scope:** Static contrast from semantic design tokens. **Status:** All 82 tested color pairings meet the chosen minimum; not an overall WCAG conformance claim.

## Minimum token contrast by theme

| Mode | Lowest normal/status text | Lowest essential non-text stroke/focus/accent | Text on filled accent |
|---|---:|---:|---:|
| Polar Night | 5.21:1 | 3.65:1 | 12.67:1 |
| Arctic Daylight | 5.04:1 | 3.13:1 | 7.37:1 |

## Functional boundaries

| Mode | Canvas | Surface | Raised | Selected |
|---|---:|---:|---:|---:|
| dark | 5.95:1 | 4.48:1 | 5.08:1 | 3.65:1 |
| light | 3.42:1 | 3.68:1 | 3.86:1 | 3.13:1 |

## Context and limitations

Minimum intended criteria: 4.5:1 for regular text and semantic statuses used as text; 3:1 for visual information required to identify controls, focus, selection and essential icons (WCAG 2.2 SC 1.4.3 and SC 1.4.11). Large text has a 3:1 minimum but this audit deliberately uses the stricter 4.5:1 threshold. A palette token passing against several surfaces does not mean it passes at reduced opacity, over images or with translucent glass/blur.

**Not validated here:** actual Quickshell loading; focus-visible indicator shape, location, thickness, offset or visibility against adjacent two-color boundaries; inactive/disabled opacity; state indicated only by color; anti-aliased 1px strokes; hover/pressed opacity; text truncation and resizing to 200%; 320px reflow; touch gestures and target spacing; screen-reader semantics; keyboard-only focus order; reduced-motion implementation; bidirectional layout; real tablet/desktop hardware. All require component and native-session checks.

Next: capture dark and light screenshots of Desk, Overview, Island, app menus, notifications and Quick Settings with keyboard focus on every interactive element. Test no-color and high-contrast settings, 200% text, reduced motion and keyboard-only traversal.

References: https://www.w3.org/TR/WCAG22/#contrast-minimum and https://www.w3.org/TR/WCAG22/#non-text-contrast

## Verification methodology

Calculated sRGB relative luminance after gamma correction and ratio (Lmax+0.05)/(Lmin+0.05). Values from the semantic token JSON; no raster screenshot or font-renderer sampling is implied.
