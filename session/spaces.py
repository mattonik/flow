#!/usr/bin/env python3
"""Experimental single-display, single-window Flow anchors for Niri."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
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


def resolve(pin, windows, socket):
    """Never guess which of several same-app windows is the saved space."""
    if pin.get("session") == socket:
        return next((w for w in windows if w["id"] == pin.get("window_id") and w["app_id"] == pin["app_id"]), None)
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
            window = resolve(pins[index], windows, socket)
            if not eligible(window, workspace, windows):
                raise ValueError("This pinned app is unavailable or ambiguous")
            command("action", "focus-window", "--id", window["id"])
            command("action", "close-overview")
        else:
            target = index + (-1 if op == "left" else 1)
            if 0 <= target < len(pins):
                pins[index], pins[target] = pins[target], pins[index]
    elif op == "focus-window":
        window = next((w for w in windows if str(w["id"]) == arg and w["workspace_id"] == workspace["id"]), None)
        if not window:
            raise ValueError("This window is no longer available")
        command("action", "focus-window", "--id", window["id"])
    elif op != "refresh":
        raise ValueError("Unknown action")
    if op not in ("focus", "focus-window"):
        reconcile(pins, workspace, windows, socket)
    return [{"id": p["id"], "label": p["label"],
             "available": eligible(resolve(p, windows, socket), workspace, windows)} for p in pins]


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
        socket_path = os.environ.get("NIRI_SOCKET", "")
        socket = f"{Path('/proc/sys/kernel/random/boot_id').read_text().strip()}:{os.stat(socket_path).st_ino}"
        for pin in pins:
            window = resolve(pin, windows, socket)
            if window and pin.get("session") != socket:
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
        print(json.dumps({"pins": result, "windows": visible, "action": sys.argv[1] if len(sys.argv) > 1 else "refresh"}))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
