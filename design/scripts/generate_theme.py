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
        "import QtQml", "",
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

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    tokens = json.loads(SOURCE.read_text(encoding="utf-8"))
    outdated = []
    for path, content in ((QML_PATH, render_qml(tokens)), (CSS_PATH, render_css(tokens))):
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
