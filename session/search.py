#!/usr/bin/env python3
"""Search local applications and the current Flow workspace."""
from collections import deque
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
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


# File search walks the XDG user folders by name while the user types: nothing is indexed or stored. Desktop,
# Documents and Downloads come before the media folders, level by level.
USER_FOLDERS = {"DESKTOP": "Desktop", "DOCUMENTS": "Documents", "DOWNLOAD": "Downloads",
                "PICTURES": "Pictures", "MUSIC": "Music", "VIDEOS": "Videos"}
# The walk's bounds: how many results, how long, and how deep below the folder it walks.
WALK = {"results": 50, "seconds": 0.3, "depth": 8}
SHORTEST_QUERY = 3


def user_folders(home, config):
    """The XDG user folders, as user-dirs.dirs names them or by their default names, wherever they are: each
    once, and never the root, the home folder or a folder holding it (xdg-user-dirs sets a folder to $HOME to
    turn it off)."""
    named = {key: home / name for key, name in USER_FOLDERS.items()}
    try:
        lines = (config / "user-dirs.dirs").read_text(errors="replace").splitlines()
    except OSError:
        lines = []
    for line in lines:
        found = re.fullmatch(r'XDG_([A-Z]+)_DIR="(.*)"', line.strip())
        if not found or found[1] not in USER_FOLDERS:
            continue
        value = found[2]
        if value == "$HOME" or value.startswith("$HOME/"):
            named[found[1]] = home / value[6:]
        elif value.startswith("/"):
            named[found[1]] = Path(value)
    try:
        top = home.resolve(strict=True)
    except OSError:
        return []
    folders = []
    for key in USER_FOLDERS:
        try:
            folder = named[key].resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if top.is_relative_to(folder) or not folder.is_dir():
            continue
        # A folder inside another is walked with it, whichever comes first.
        if not any(folder.is_relative_to(f) for f in folders):
            folders = [f for f in folders if not f.is_relative_to(folder)] + [folder]
    return folders


def mount_points():
    """Where file systems are mounted (/proc/self/mountinfo), so the walk never enters another one: a network
    mount that hangs would hang it, and a bind mount could lead out of the user folders."""
    try:
        lines = Path("/proc/self/mountinfo").read_text().splitlines()
    except OSError:
        return frozenset()
    return frozenset(re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), line.split(" ")[4])
                     for line in lines if len(line.split(" ")) > 4)


def name_matches(name, words):
    """Whether a name holds every word of the query; file contents are not searched. Case is folded as the
    shell's ranking folds it."""
    folded = name.lower()
    return all(word in folded for word in words)


def find_files(query, folders, bounds=WALK, clock=time.monotonic, mounts=frozenset()):
    """Files and folders under ``folders`` whose names match ``query``, level by level, within ``bounds``.

    Hidden files and folders count. Symbolic links are neither listed nor followed and other file systems are
    not entered, so the walk stays inside the user folders. The clock is checked while a folder is read too, so
    a huge folder cannot hold the walk past its time; whatever the time leaves unvisited is simply not found."""
    words = query.lower().split()
    if len(query.strip()) < SHORTEST_QUERY:
        return []
    end = clock() + bounds["seconds"]
    queue = deque((folder, 0) for folder in folders)
    found = []
    while queue:
        folder, depth = queue.popleft()
        read, late = [], False
        try:
            with os.scandir(folder) as listing:
                for entry in listing:
                    read.append(entry)
                    if len(read) % 256 == 0 and clock() > end:
                        late = True
                        break
        except OSError:
            pass
        for entry in sorted(read, key=lambda e: e.name.lower()):
            if clock() > end:
                return found
            try:
                is_folder = entry.is_dir(follow_symlinks=False)
                is_file = not is_folder and entry.is_file(follow_symlinks=False)
            except OSError:
                continue
            if (is_folder or is_file) and name_matches(entry.name, words) and os.access(entry.path, os.R_OK):
                found.append(Path(entry.path))
                if len(found) == bounds["results"]:
                    return found
            if is_folder and depth + 1 < bounds["depth"] and entry.path not in mounts:
                queue.append((entry.path, depth + 1))
        if late:
            return found
    return found


def file_rows(paths):
    """The rows the search panel shows for files found by name; they open as recent files do."""
    return json.dumps([{"title": path.name, "detail": f"{'Folder' if path.is_dir() else 'File'} · {path.parent}",
                        "kind": "file", "value": path.as_uri(), "icon": ""} for path in paths])


# Commands: Flow's own actions, run by the shell, and the system's, run by ``--open command ID``. Those that end
# the session or stop the computer ask first, in the shell.
COMMANDS = [("Open Desk", "Flow", "action", "desk"), ("Overview", "Flow", "action", "overview"),
            ("Notifications", "Flow", "action", "notifications"), ("Pin current space", "Flow", "action", "pin"),
            ("Save session", "Flow · for recovery after a restart", "action", "save-session"),
            ("Lock screen", "System", "command", "lock"), ("Suspend", "System", "command", "suspend"),
            ("Log out", "System · asks first", "command", "logout"),
            ("Restart", "System · asks first", "command", "restart"),
            ("Power off", "System · asks first", "command", "poweroff")]
ASKS_FIRST = {"logout", "restart", "poweroff"}
# Flow's lock screen, as Super+L starts it; -f returns once the screen is locked.
SYSTEM_COMMANDS = {"lock": ["flow-lock", "-f"], "suspend": ["systemctl", "suspend"],
                   "logout": ["niri", "msg", "action", "quit", "--skip-confirmation"],
                   "restart": ["systemctl", "reboot"], "poweroff": ["systemctl", "poweroff"]}


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
    result.extend((title, detail, kind, value, "") for title, detail, kind, value in COMMANDS)
    # After every ordinary entry: the same apps opened as a Layer over the current space.
    for app in shown:
        if app.get_id():
            result.append((app.get_display_name(), "Open as Layer", "layer", app.get_id(), icon(app)))
    return [(" ".join(title.split()), " ".join(detail.split()), kind, value, glyph) for title, detail, kind, value, glyph in result]


def as_json(found):
    """The rows Flow's search panel shows. An app is named by its desktop file, which the shell opens."""
    rows = []
    for title, detail, kind, value, glyph in found:
        if kind == "app":
            value = value.get_filename() or ""
            if not value:
                continue
        rows.append({"title": title, "detail": detail, "kind": kind, "value": value, "icon": glyph,
                     **({"confirm": True} if kind == "command" and value in ASKS_FIRST else {})})
    return json.dumps(rows)


def open_result(kind, value):
    """Opens an app (by its desktop file) or a file (by its URI) for the shell, in an app scope of its own when
    the installed shell asks for one. The installed wrapper puts gio on PATH; the shell's own PATH lacks it."""
    if kind == "command":
        if value not in SYSTEM_COMMANDS:
            print(f"Flow search: no command {value!r}", file=sys.stderr)
            return 2
        try:
            return subprocess.run(SYSTEM_COMMANDS[value], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL).returncode
        except OSError as exc:
            print(f"Flow search: {exc}", file=sys.stderr)
            return 127
    command = {"app": ["gio", "launch"], "file": ["gio", "open"]}.get(kind)
    if command is None:
        print(f"Flow search: cannot open a {kind!r} result", file=sys.stderr)
        return 2
    try:
        # The app inherits gio's streams: none of them may be the shell's pipe, which it would hold open.
        return subprocess.run(app_command([*command, value]), stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    except OSError as exc:
        print(f"Flow search: {exc}", file=sys.stderr)
        return 127


def main(args):
    """``--json`` lists every result for the shell's search panel; ``--files`` finds files by name in the user
    folders, for the query on its first line of input; ``--open KIND VALUE`` opens an app or file."""
    if len(args) == 3 and args[0] == "--open":
        return open_result(args[1], args[2])
    if args == ["--files"]:
        # The query comes on stdin: a command line is readable by every user of the machine.
        query = sys.stdin.readline().rstrip("\n")
        home = Path.home()
        config = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
        print(file_rows(find_files(query, user_folders(home, config), mounts=mount_points())))
        return 0
    if args != ["--json"]:
        print("usage: flow-search --json | --files (query on stdin) | --open app|file|command VALUE", file=sys.stderr)
        return 2
    from gi.repository import Gio
    try:
        # Observing reads the pins and windows without reconciling or writing Flow's state: listing search
        # results must never move a window or rewrite a pin.
        snapshot = json.loads(subprocess.check_output(["flow-spaces", "observe"], text=True))
    except (OSError, ValueError, subprocess.SubprocessError):
        snapshot = {}
    home = Path.home()
    history = Path(os.environ.get("XDG_DATA_HOME") or home / ".local/share") / "recently-used.xbel"
    print(as_json(results(snapshot, Gio.AppInfo.get_all(), recent_files(history, home))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
