#!/usr/bin/env python3
"""Search local applications and the current Flow workspace."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

from launching import app_command


def recent_files(history, home):
    try:
        # shortcut: limit the recent-file list to 4 MiB and 100 entries; use an index for broader search.
        if history.stat().st_size > 4 * 1024 * 1024:
            return []
        bookmarks = ET.parse(history).getroot().findall("bookmark")
    except (OSError, ET.ParseError):
        return []
    found = []
    seen = set()
    for bookmark in sorted(bookmarks, key=lambda item: item.get("modified", ""), reverse=True):
        try:
            uri = urlsplit(bookmark.get("href", ""))
            if uri.scheme != "file" or uri.netloc not in ("", "localhost") or uri.query or uri.fragment:
                continue
            path = Path(unquote(uri.path)).resolve(strict=True)
            path.relative_to(home.resolve())
            if not path.is_file() or not os.access(path, os.R_OK) or path in seen:
                continue
        except (OSError, ValueError, RuntimeError):
            continue
        seen.add(path)
        found.append(path)
        if len(found) == 100:
            break
    return found


def results(snapshot, apps, files=()):
    """Everything search offers, in order: (title, detail, kind, value, icon)."""
    result = [("Flow Settings", "Settings", "settings", "settings", ""),
              ("Quick Settings", "Audio and network", "settings", "quick_open", "")]
    for pin in snapshot.get("pins", []):
        status = "Pinned space" if pin.get("available") else "Pinned Group · Unavailable" if pin.get("kind") == "group" else "Pinned space · Open"
        result.append((pin["label"], status, "pin", pin["id"], ""))
    for pin in snapshot.get("other_pins", []):
        # Pins of a disconnected display are listed only while their window is open somewhere.
        if pin.get("display_state") == "connected" or pin.get("available"):
            where = pin.get("display_label") or "another display"
            result.append((pin["label"], f"Pinned on {where}", "pin", pin["id"], ""))
    for window in snapshot.get("windows", []):
        result.append((window["label"], f"Open window · {window['app_id']}", "window", str(window["id"]), ""))
    for path in files:
        result.append((path.name, f"Recent file · {path.parent}", "file", path.as_uri(), ""))
    shown = [app for app in sorted(apps, key=lambda a: a.get_display_name().casefold()) if app.should_show()]
    def icon(app):
        found = app.get_icon()
        return found.to_string() if found is not None else ""
    for app in shown:
        result.append((app.get_display_name(), "Application", "app", app, icon(app)))
    # After every ordinary entry: the same apps opened as a Layer over the current space.
    for app in shown:
        if app.get_id():
            result.append((app.get_display_name(), "Open as Layer", "layer", app.get_id(), icon(app)))
    return [(" ".join(title.split()), " ".join(detail.split()), kind, value, glyph) for title, detail, kind, value, glyph in result]


def entries(snapshot, apps, files=()):
    """Fuzzel's rows: one line per result, and the action it runs."""
    return [(f"{title} · {detail}", (kind, value)) for title, detail, kind, value, _ in results(snapshot, apps, files)]


def as_json(found):
    """The rows Flow's search panel shows. An app is named by its desktop file, which the shell opens."""
    rows = []
    for title, detail, kind, value, glyph in found:
        if kind == "app":
            value = value.get_filename() or ""
            if not value:
                continue
        rows.append({"title": title, "detail": detail, "kind": kind, "value": value, "icon": glyph})
    return json.dumps(rows)


def main():
    from gi.repository import Gio, GLib
    try:
        snapshot = json.loads(subprocess.check_output(["flow-spaces", "refresh"], text=True))
    except (OSError, ValueError, subprocess.SubprocessError):
        snapshot = {}
    home = Path.home()
    history = Path(os.environ.get("XDG_DATA_HOME") or home / ".local/share") / "recently-used.xbel"
    if "--json" in sys.argv[1:]:
        print(as_json(results(snapshot, Gio.AppInfo.get_all(), recent_files(history, home))))
        return
    choices = entries(snapshot, Gio.AppInfo.get_all(), recent_files(history, home))
    # FLOW_FUZZEL is fuzzel in Floe's theme when installed (modules/flow.nix).
    result = subprocess.run([os.environ.get("FLOW_FUZZEL") or "fuzzel", "--keyboard-focus=on-demand",
                             "--override=main.exit-on-keyboard-focus-loss=yes", "--dmenu", "--index", "--only-match",
                             "--placeholder", "Search apps, spaces and files"],
                            input="".join(label + "\n" for label, _ in choices), text=True, capture_output=True)
    if result.returncode != 0:
        return
    try:
        index = int(result.stdout.strip())
        if not 0 <= index < len(choices):
            return
        kind, value = choices[index][1]
        if kind == "app":
            if os.environ.get("FLOW_APP_SCOPES") == "1" and value.get_filename():
                subprocess.run(app_command(["gio", "launch", value.get_filename()]), check=True)
            else:
                value.launch([], None)
        elif kind == "file":
            if os.environ.get("FLOW_APP_SCOPES") == "1":
                subprocess.run(app_command(["gio", "open", value]), check=True)
            else:
                Gio.AppInfo.launch_default_for_uri(value, None)
        else:
            action = {"settings": value, "pin": "focus_pin", "window": "focus_window", "layer": "open_layer"}[kind]
            args = ["quickshell", "ipc", "-p", "/etc/flow/shell.qml", "call", "desk", action]
            subprocess.run(args + ([value] if kind != "settings" else []), check=True)
    except (ValueError, OSError, subprocess.SubprocessError, GLib.Error) as exc:
        print(f"Flow search: {exc}", file=sys.stderr)


if __name__ == "__main__":
    # subprocess.run cleans up Fuzzel when the shell terminates this search.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    main()
