#!/usr/bin/env python3
"""Experimental single-display, single-window Flow anchors for Niri."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid


def command(*args):
    return subprocess.check_output(["niri", "msg", *map(str, args)], text=True, stderr=subprocess.PIPE)


def snapshot():
    outputs = json.loads(command("-j", "outputs"))
    workspaces = json.loads(command("-j", "workspaces"))
    windows = json.loads(command("-j", "windows"))
    if sum(output.get("logical") is not None for output in outputs.values()) != 1:
        raise ValueError("Pinned spaces currently require one display")
    workspace = next((w for w in workspaces if w["is_focused"]), None)
    if workspace is None:
        raise ValueError("No active workspace")
    return workspace, windows


def session_id():
    socket_path = os.environ.get("NIRI_SOCKET", "")
    stat = os.stat(socket_path)
    return f"{Path('/proc/sys/kernel/random/boot_id').read_text().strip()}:{socket_path}:{stat.st_ino}:{stat.st_ctime_ns}"


def desktop_file(app_id, dirs):
    # Match only an exact desktop ID; never interpret window titles as commands.
    if not app_id or "/" in app_id or app_id in (".", ".."):
        return None
    filename = app_id if app_id.endswith(".desktop") else app_id + ".desktop"
    return next((p for d in dirs if (p := Path(d) / "applications" / filename).is_file()), None)


def resolve(pin, windows, socket):
    """Never guess which of several same-app windows is the saved space."""
    if pin.get("session") == socket:
        match = next((w for w in windows if w["id"] == pin.get("window_id") and w["app_id"] == pin["app_id"]), None)
        if match:
            return match
    matches = [w for w in windows if w["app_id"] == pin["app_id"]]
    return matches[0] if len(matches) == 1 else None


def eligible(window, workspace, windows):
    if not window or not window.get("app_id") or window.get("is_floating"):
        return False
    pos = window.get("layout", {}).get("pos_in_scrolling_layout")
    if not pos or window["workspace_id"] != workspace["id"]:
        return False
    return sum(1 for other in windows if other["workspace_id"] == workspace["id"] and
               (other.get("layout", {}).get("pos_in_scrolling_layout") or [None])[0] == pos[0]) == 1


def reconcile(pins, workspace, windows, socket):
    # shortcut: absent or ambiguous apps have no physical placeholder; use compositor-backed placeholders when available.
    resolved = [resolve(pin, windows, socket) for pin in pins]
    if any(w and not eligible(w, workspace, windows) for w in resolved):
        return
    active = workspace.get("active_window_id")
    desired = [w for w in resolved if w]
    if all(w["layout"]["pos_in_scrolling_layout"][0] == i for i, w in enumerate(desired, 1)):
        return
    try:
        for index, window in enumerate(desired, 1):
            command("action", "focus-window", "--id", window["id"])
            command("action", "move-column-to-index", index)
    finally:
        if active:
            command("action", "focus-window", "--id", active)


def run(pins, op, arg, workspace, windows, socket):
    if op == "pin":
        window = next((w for w in windows if w["id"] == workspace.get("active_window_id")), None)
        if not eligible(window, workspace, windows):
            raise ValueError("Select one non-floating app window to pin")
        if any(p["app_id"] == window["app_id"] for p in pins):
            raise ValueError("This app is already pinned")
        pins.append({"id": uuid.uuid4().hex, "label": window.get("title") or window["app_id"],
                     "app_id": window["app_id"], "window_id": window["id"], "session": socket})
    elif op in ("unpin", "focus", "left", "right"):
        index = next((i for i, p in enumerate(pins) if p["id"] == arg), None)
        if index is None:
            raise ValueError("Pinned space no longer exists")
        if op == "unpin":
            pins.pop(index)
        elif op == "focus":
            pin = pins[index]
            window = resolve(pin, windows, socket)
            if not window and not any(w.get("app_id") == pin["app_id"] for w in windows):
                dirs = (os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share"),
                        *os.environ.get("XDG_DATA_DIRS", "/run/current-system/sw/share:/usr/share").split(":"))
                desktop = desktop_file(pin["app_id"], dirs)
                if not desktop:
                    raise ValueError("No matching installed application for this pin")
                try:
                    subprocess.run(["gio", "launch", str(desktop)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except (OSError, subprocess.SubprocessError) as exc:
                    raise ValueError("Could not open the pinned application") from exc
                workspace_id = workspace["id"]
                for _ in range(30):
                    time.sleep(0.5)
                    workspace, windows = snapshot()
                    if workspace["id"] != workspace_id:
                        raise ValueError("Workspace changed while opening pinned app")
                    if any(w.get("app_id") == pin["app_id"] for w in windows):
                        break
                window = resolve(pin, windows, socket)
            if not eligible(window, workspace, windows):
                raise ValueError("This pinned app is unavailable or ambiguous")
            pin["session"], pin["window_id"] = socket, window["id"]
            command("action", "focus-window", "--id", window["id"])
            command("action", "close-overview")
        else:
            target = index + (-1 if op == "left" else 1)
            if 0 <= target < len(pins):
                pins[index], pins[target] = pins[target], pins[index]
    elif op in ("stack", "unstack"):
        active = next((w for w in windows if w["id"] == workspace.get("active_window_id")), None)
        pos = active and active.get("layout", {}).get("pos_in_scrolling_layout")
        if not pos or active.get("is_floating") or active.get("workspace_id") != workspace["id"]:
            raise ValueError("Select a tiled window first")
        def column(index):
            return [w for w in windows if w.get("workspace_id") == workspace["id"] and not w.get("is_floating")
                    and (w.get("layout", {}).get("pos_in_scrolling_layout") or [None])[0] == index]
        current = column(pos[0])
        pair = current + column(pos[0] + 1) if op == "stack" else current
        if op == "stack" and (len(current) != 1 or len(pair) != 2):
            raise ValueError("Select a single window with one single window to its right")
        if op == "unstack" and len(pair) != 2:
            raise ValueError("Select a stack containing exactly two windows")
        if any(w.get("app_id") == pin.get("app_id") for pin in pins for w in pair):
            raise ValueError("Unpin these windows before stacking or separating")
        command("action", "consume-window-into-column" if op == "stack" else "expel-window-from-column")
    elif op == "focus-window":
        window = next((w for w in windows if str(w["id"]) == arg and w["workspace_id"] == workspace["id"]), None)
        if not window:
            raise ValueError("This window is no longer available")
        command("action", "focus-window", "--id", window["id"])
    elif op != "refresh":
        raise ValueError("Unknown action")
    if op not in ("focus", "focus-window", "stack", "unstack"):
        reconcile(pins, workspace, windows, socket)
    return [{"id": p["id"], "label": p["label"],
             "available": eligible(resolve(p, windows, socket), workspace, windows)} for p in pins]


def saved_groups(path):
    try:
        data = json.loads(path.read_text())
        groups = data.get("groups", []) if isinstance(data, dict) else []
        if not isinstance(groups, list):
            return []
        return [g for g in groups if isinstance(g, dict) and isinstance(g.get("id"), str)
                and isinstance(g.get("label"), str) and isinstance(g.get("members"), list)
                and len(g["members"]) == 2 and all(isinstance(a, str) and a for a in g["members"])
                and len(set(g["members"])) == 2]
    except (OSError, ValueError):
        return []


def display_spaces(workspace, windows, groups=()):
    """Represent each tiled column once; floating windows remain separate."""
    current = [w for w in windows if w.get("workspace_id") == workspace["id"]]
    current.sort(key=lambda w: w.get("layout", {}).get("pos_in_scrolling_layout") or [99999, 0])
    columns = []
    for w in current:
        pos = w.get("layout", {}).get("pos_in_scrolling_layout")
        key = ("column", pos[0]) if pos and not w.get("is_floating") else ("window", w["id"])
        if not columns or columns[-1][0] != key:
            columns.append((key, []))
        columns[-1][1].append({"id": w["id"], "label": w.get("title") or w.get("app_id") or "Application",
                              "app_id": w.get("app_id") or "", "active": w["id"] == workspace.get("active_window_id")})
    result = []
    counts = {app: sum(w.get("app_id") == app for w in windows) for g in groups for app in g["members"]}
    for _, members in columns:
        selected = next((w for w in members if w["active"]), members[0])
        app_ids = [w["app_id"] for w in members]
        matches = [g for g in groups if g["members"] == app_ids and all(counts.get(a) == 1 for a in app_ids)]
        group = matches[0] if len(matches) == 1 else None
        result.append({"id": selected["id"], "label": group["label"] if group else " + ".join(w["label"] for w in members),
                       "group_id": group["id"] if group else None,
                       "active": any(w["active"] for w in members), "members": members})
    return result


def main():
    state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "flow"
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = state / "spaces.json"
    with (state / "spaces.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        pins = json.loads(path.read_text()) if path.exists() else []
        if not isinstance(pins, list):
            raise ValueError("Invalid saved spaces; state was not changed")
        workspace, windows = snapshot()
        before = json.dumps(pins, sort_keys=True)
        socket = session_id()
        for pin in pins:
            window = resolve(pin, windows, socket)
            if window and (pin.get("session") != socket or pin.get("window_id") != window["id"]):
                pin["session"], pin["window_id"] = socket, window["id"]
        result = run(pins, sys.argv[1] if len(sys.argv) > 1 else "refresh",
                     sys.argv[2] if len(sys.argv) > 2 else None, workspace, windows,
                     socket)
        if before != json.dumps(pins, sort_keys=True):
            with tempfile.NamedTemporaryFile("w", dir=state, delete=False) as tmp:
                try:
                    json.dump(pins, tmp, ensure_ascii=False)
                    tmp.flush()
                    os.fsync(tmp.fileno())
                    os.replace(tmp.name, path)
                finally:
                    if os.path.exists(tmp.name):
                        os.unlink(tmp.name)
        ordered = sorted((w for w in windows if w.get("workspace_id") == workspace["id"]),
                         key=lambda w: (w.get("layout", {}).get("pos_in_scrolling_layout") or [99999, 0]))
        visible = [{"id": w["id"], "label": w.get("title") or w.get("app_id") or "Application",
                    "app_id": w.get("app_id") or "", "active": w["id"] == workspace.get("active_window_id")}
                   for w in ordered]
        print(json.dumps({"pins": result, "windows": visible, "spaces": display_spaces(workspace, windows, saved_groups(state / "session.json")), "action": sys.argv[1] if len(sys.argv) > 1 else "refresh"}))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
