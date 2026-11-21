#!/usr/bin/env python3
"""Generate Floe semantic theme consumers from the editable JSON source.

Usage: python3 design/scripts/generate_theme.py [--check]
No third-party dependencies. --check returns nonzero if generated files are stale.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "design/tokens/floe.tokens.json"
QML_PATH = ROOT / "session/FloeTheme.qml"
CSS_PATH = ROOT / "design/generated/floe.css"
FUZZEL_PATHS = {mode: ROOT / f"design/generated/fuzzel-{mode}.ini" for mode in ("dark", "light")}
NIRI_PATHS = {mode: ROOT / f"design/generated/niri-{mode}.kdl" for mode in ("dark", "light")}
SWAYLOCK_PATHS = {mode: ROOT / f"design/generated/swaylock-{mode}.conf" for mode in ("dark", "light")}

def camel(name):
    parts = name.split("-")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])

def render_qml(tokens):
    dark = tokens["semantic"]["dark"]
    light = tokens["semantic"]["light"]
    if set(dark) != set(light):
        raise ValueError("Dark and light semantic roles must match")
    return "\n".join([
        "// Generated from design/tokens/floe.tokens.json. Keep synchronized with semantic roles.",
        "import QtQuick", "",
        "QtObject {",
        "    property bool light: false",
        f'    readonly property string interfaceFont: "{tokens["typography"]["family"]["interface"]["$value"][0]}"',
        f'    readonly property string technicalFont: "{tokens["typography"]["family"]["technical"]["$value"][0]}"',
        f'    readonly property string brandFont: "{tokens["typography"]["family"]["brand"]["$value"][0]}"',
        *(f'    readonly property color {camel(key)}: light ? "{light[key]["$value"]}" : "{dark[key]["$value"]}"' for key in dark),
        "}", ""
    ])

def render_css(tokens):
    dark = tokens["semantic"]["dark"]
    light = tokens["semantic"]["light"]
    if set(dark) != set(light):
        raise ValueError("Dark and light semantic roles must match")
    return "\n".join([
        "/* Floe Arctic Minimalism — GENERATED from design/tokens/floe.tokens.json. Do not edit by hand. */",
        ":root, .theme-dark { color-scheme: dark;",
        *(f'  --floe-{k}: {v["$value"]};' for k, v in dark.items()),
        f'  --floe-font-ui: "{tokens["typography"]["family"]["interface"]["$value"][0]}", system-ui, sans-serif;',
        f'  --floe-font-brand: "{tokens["typography"]["family"]["brand"]["$value"][0]}", "{tokens["typography"]["family"]["interface"]["$value"][0]}", system-ui, sans-serif;',
        f'  --floe-font-mono: "{tokens["typography"]["family"]["technical"]["$value"][0]}", ui-monospace, monospace;',
        '  --floe-radius-card: 20px; --floe-radius-control: 14px; --floe-radius-pill: 999px;',
        '  --floe-space-unit: 4px; --floe-ease-spatial: cubic-bezier(.22, 1, .36, 1); --floe-time-spatial: 380ms;',
        "}",
        ".theme-light { color-scheme: light;",
        *(f'  --floe-{k}: {v["$value"]};' for k, v in light.items()),
        "}",
        "@media (prefers-color-scheme: light) { :root:not(.theme-dark):not(.theme-light) { color-scheme: light;",
        *(f'  --floe-{k}: {v["$value"]};' for k, v in light.items()),
        "}}",
        "@media (prefers-reduced-motion: reduce) { :root { --floe-time-spatial: 0ms; } }",
        ""
    ])

def px(token):
    return int(token["$value"].removesuffix("px"))

def render_fuzzel(tokens, mode):
    """The app launcher and search (fuzzel): a Floe panel, in one appearance."""
    colors = tokens["semantic"][mode]
    def rgba(role, alpha="ff"):
        return colors[role]["$value"].lstrip("#").lower() + alpha
    family = tokens["typography"]["family"]["interface"]["$value"][0]
    body = px(tokens["typography"]["size"]["body"])
    return "\n".join([
        "# Generated from design/tokens/floe.tokens.json by design/scripts/generate_theme.py. Do not edit.",
        "[main]",
        f"font={family}:pixelsize={body}",
        'prompt=""',
        "placeholder=Search apps",
        "icons-enabled=yes",
        "lines=8",
        "width=36",
        "horizontal-pad=24",
        "vertical-pad=20",
        "inner-pad=14",
        # Rows as tall as the minimum touch target; a bare number would be points.
        f"line-height={px(tokens['sizing']['target-min'])}px",
        "",
        "[colors]",
        f"background={rgba('surface')}",
        f"text={rgba('text-primary')}",
        f"prompt={rgba('accent')}",
        f"placeholder={rgba('text-muted')}",
        f"input={rgba('text-primary')}",
        f"match={rgba('accent')}",
        # The selected row is an accent fill with on-accent text, never a tint: fuzzel draws no outline.
        f"selection={rgba('accent')}",
        f"selection-text={rgba('on-accent')}",
        f"selection-match={rgba('on-accent')}",
        f"counter={rgba('text-muted')}",
        # Full strength: in Arctic Daylight the panel's edge is what separates it from the canvas.
        f"border={rgba('border')}",
        "",
        "[border]",
        "width=1",
        f"radius={px(tokens['radius']['md'])}",
        f"selection-radius={px(tokens['radius']['sm'])}",
        "",
    ])

def render_niri(tokens, mode):
    """Niri's colours in one appearance; niri.kdl includes one, and the shell keeps a per-user copy in
    step with the Desk's appearance switch."""
    c = {role: entry["$value"] for role, entry in tokens["semantic"][mode].items()}
    return "\n".join([
        "// Generated from design/tokens/floe.tokens.json by design/scripts/generate_theme.py. Do not edit.",
        "layout {",
        # Niri draws the inactive colour on the focused window of an unfocused display: where focus returns.
        f'    focus-ring {{ active-color "{c["focus"]}"; inactive-color "{c["border"]}"; }}',
        f'    background-color "{c["canvas"]}"',
        "}",
        # Calm and distinct: the selected-surface tone keeps Overview dark and quiet. Workspaces are told
        # apart by their windows and the workspace shadow pinned here, not by 3:1 against the backdrop
        # (a design decision of 2026-11-21; see docs/IMPLEMENTATION_STATUS.md). The shadow is the ink of
        # the appearance: the canvas in Polar Night, primary text in Arctic Daylight.
        "overview {",
        f'    backdrop-color "{c["surface-selected"]}"',
        "    workspace-shadow {",
        "        softness 40",
        "        spread 10",
        "        offset x=0 y=10",
        f'        color "{c["canvas"] if mode == "dark" else c["text-primary"]}{"99" if mode == "dark" else "40"}"',
        "    }",
        "}",
        "",
    ])

def render_swaylock(tokens, mode):
    """The lock screen: the canvas, and a quiet indicator whose feedback colours carry the meaning."""
    c = {role: entry["$value"].lstrip("#").lower() for role, entry in tokens["semantic"][mode].items()}
    family = tokens["typography"]["family"]["interface"]["$value"][0]
    clear = "00000000"
    return "\n".join([
        "# Generated from design/tokens/floe.tokens.json by design/scripts/generate_theme.py. Do not edit.",
        f"color={c['canvas']}",
        f"font={family}",
        f"font-size={px(tokens['typography']['size']['title-sm'])}",
        "indicator-radius=72",
        "indicator-thickness=6",
        "indicator-idle-visible",
        "show-failed-attempts",
        "ignore-empty-password",
        f"inside-color={c['surface']}", f"ring-color={c['surface-selected']}", f"line-color={clear}",
        f"separator-color={clear}", f"text-color={c['text-primary']}",
        f"key-hl-color={c['accent']}", f"bs-hl-color={c['text-muted']}",
        f"inside-clear-color={c['surface']}", f"ring-clear-color={c['text-muted']}", f"line-clear-color={clear}",
        f"text-clear-color={c['text-primary']}",
        f"inside-ver-color={c['surface']}", f"ring-ver-color={c['accent']}", f"line-ver-color={clear}",
        f"text-ver-color={c['text-primary']}",
        f"inside-wrong-color={c['surface']}", f"ring-wrong-color={c['danger']}", f"line-wrong-color={clear}",
        f"text-wrong-color={c['danger']}",
        # Caps Lock is a warning; the keyboard layout label sits on the panel tone.
        f"inside-caps-lock-color={c['surface']}", f"ring-caps-lock-color={c['warning']}",
        f"line-caps-lock-color={clear}", f"text-caps-lock-color={c['warning']}",
        f"caps-lock-key-hl-color={c['accent']}", f"caps-lock-bs-hl-color={c['text-muted']}",
        f"layout-bg-color={c['surface']}", f"layout-border-color={c['border']}", f"layout-text-color={c['text-primary']}",
        "",
    ])

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    tokens = json.loads(SOURCE.read_text(encoding="utf-8"))
    outdated = []
    outputs = [(QML_PATH, render_qml(tokens)), (CSS_PATH, render_css(tokens)),
               *((path, render_fuzzel(tokens, mode)) for mode, path in FUZZEL_PATHS.items()),
               *((path, render_niri(tokens, mode)) for mode, path in NIRI_PATHS.items()),
               *((path, render_swaylock(tokens, mode)) for mode, path in SWAYLOCK_PATHS.items())]
    for path, content in outputs:
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                outdated.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            print("generated", path.relative_to(ROOT))
    if outdated:
        print("Stale generated files: " + ", ".join(outdated), file=sys.stderr)
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
