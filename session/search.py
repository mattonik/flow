#!/usr/bin/env python3
"""Search local applications and the current Flow workspace."""
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET


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


def entries(snapshot, apps, files=()):
    result = [("Flow Settings  ·  Settings", ("settings", "settings")),
              ("Quick Settings  ·  Audio and network", ("settings", "quick_open"))]
    for pin in snapshot.get("pins", []):
        status = "Pinned space" if pin.get("available") else "Pinned Group · Unavailable" if pin.get("kind") == "group" else "Pinned space · Open"
        result.append((f"{pin['label']}  ·  {status}", ("pin", pin["id"])))
    for window in snapshot.get("windows", []):
        result.append((f"{window['label']}  ·  Open window  ·  {window['app_id']}", ("window", str(window["id"]))))
    for path in files:
        result.append((f"{path.name}  ·  Recent file  ·  {path.parent}", ("file", path.as_uri())))
    for app in sorted(apps, key=lambda a: a.get_display_name().casefold()):
        if app.should_show():
            result.append((f"{app.get_display_name()}  ·  Application", ("app", app)))
    return [(" ".join(label.split()), action) for label, action in result]


def main():
    from gi.repository import Gio, GLib
    try:
        snapshot = json.loads(subprocess.check_output(["flow-spaces", "refresh"], text=True))
    except (OSError, ValueError, subprocess.SubprocessError):
        snapshot = {}
    home = Path.home()
    history = Path(os.environ.get("XDG_DATA_HOME") or home / ".local/share") / "recently-used.xbel"
    choices = entries(snapshot, Gio.AppInfo.get_all(), recent_files(history, home))
    result = subprocess.run(["fuzzel", "--dmenu", "--index", "--only-match", "--prompt", "Search: "],
                            input="".join(label + "\n" for label, _ in choices), text=True, capture_output=True)
    if result.returncode != 0:
        return
    try:
        index = int(result.stdout.strip())
        if not 0 <= index < len(choices):
            return
        kind, value = choices[index][1]
        if kind == "app":
            value.launch([], None)
        elif kind == "file":
            Gio.AppInfo.launch_default_for_uri(value, None)
        else:
            action = {"settings": value, "pin": "focus_pin", "window": "focus_window"}[kind]
            args = ["quickshell", "ipc", "-p", "/etc/flow/shell.qml", "call", "desk", action]
            subprocess.run(args + ([value] if kind != "settings" else []), check=True)
    except (ValueError, OSError, subprocess.SubprocessError, GLib.Error) as exc:
        print(f"Flow search: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
