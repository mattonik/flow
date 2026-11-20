#!/usr/bin/env python3
"""Open and dismiss a Layer over a real pinned side-by-side Group without changing it (F-006).

Run inside a Niri session from the Flow checkout, with the focused output's active workspace empty and
``flow-spaces`` on PATH running this checkout's ``session/spaces.py``. The Layer app is a disposable
Foot window started from a temporary desktop entry. Flow state and data are isolated; real state files
are checksummed and every test window is closed afterward.
"""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid


def niri(*args):
    return subprocess.check_output(["niri", "msg", *map(str, args)], text=True, timeout=10)


def read(kind):
    return json.loads(niri("-j", kind))


def focus():
    return next(w for w in read("workspaces") if w["is_focused"])


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def wait_for(test, label, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = test()
        if value:
            return value
        time.sleep(0.2)
    raise RuntimeError(f"Timed out waiting for {label}")


def main():
    workspace = focus()
    real_state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "flow"
    real_files = [real_state / name for name in ("spaces.json", "groups.json", "session.json")]
    real_digests = [digest(path) for path in real_files]
    if any(w.get("workspace_id") == workspace["id"] for w in read("windows")):
        raise RuntimeError("The focused output's active workspace must be empty")
    token = uuid.uuid4().hex[:8]
    apps = {s: f"flow-layer-{token}-{s}" for s in "mabul"}
    processes, steps, result = [], [], None

    def window(suffix):
        found = [w for w in read("windows") if w.get("app_id") == apps[suffix]]
        if len(found) != 1:
            raise RuntimeError(f"Window {suffix} is missing or duplicated")
        return found[0]

    def tiles():
        """Each tiled test window's column, row and width; a Layer must leave these alone."""
        return {w["app_id"][-1]: (tuple(w["layout"]["pos_in_scrolling_layout"]), round(w["layout"]["tile_size"][0]))
                for w in read("windows") if w.get("app_id") in apps.values() and not w.get("is_floating")
                and w.get("workspace_id") == workspace["id"]}

    try:
        for suffix in "mabu":
            processes.append(subprocess.Popen(["foot", "--app-id", apps[suffix]], stdin=subprocess.DEVNULL,
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            wait_for(lambda: any(w.get("app_id") == apps[suffix] for w in read("windows")), suffix)
        for index, suffix in enumerate("mabu", 1):
            niri("action", "focus-window", "--id", window(suffix)["id"])
            niri("action", "move-column-to-index", index)
        with tempfile.TemporaryDirectory(prefix="flow-layer-") as tmp:
            data = Path(tmp) / "data"
            (data / "applications").mkdir(parents=True)
            (data / "applications" / f"{apps['l']}.desktop").write_text(
                f"[Desktop Entry]\nType=Application\nName=Scratch Notes\nExec=foot --app-id {apps['l']}\n")
            env = dict(os.environ, XDG_STATE_HOME=str(Path(tmp) / "state"), XDG_DATA_HOME=str(data))

            def spaces(*args):
                process = subprocess.run(["flow-spaces", *args], env=env, text=True, stdout=subprocess.PIPE, timeout=60)
                value = json.loads(process.stdout)
                if process.returncode or "error" in value:
                    raise RuntimeError(f"flow-spaces {args}: {value}")
                return value

            def saved():
                state = Path(tmp) / "state/flow"
                return json.loads((state / "spaces.json").read_text()), json.loads((state / "groups.json").read_text())

            niri("action", "focus-window", "--id", window("m")["id"])
            spaces("pin")
            niri("action", "focus-window", "--id", window("a")["id"])
            spaces("stack")
            group = next(s["group_id"] for s in spaces("create-group")["spaces"] if s.get("group_id"))
            spaces("pin-group", group)
            spaces("group-layout", group)
            spaces("member-wider", str(window("a")["id"]))
            niri("action", "focus-window", "--id", window("a")["id"])
            before = spaces("refresh")
            before_tiles, before_state = tiles(), saved()
            steps.append(f"pinned app, pinned side-by-side Group and a neighbor: {before_tiles}")

            opened = spaces("open-layer", apps["l"])
            layer = window("l")
            if (not layer.get("is_floating") or focus()["active_window_id"] != layer["id"]
                    or [entry["app_name"] for entry in opened["layers"]] != ["Scratch Notes"]):
                raise RuntimeError(f"The app did not open as a focused Layer: {opened['layers']}")
            if tiles() != before_tiles or opened["spaces"] != [{**s, "active": False, "members": [
                    {**m, "active": False} for m in s["members"]]} for s in before["spaces"]]:
                raise RuntimeError(f"Opening the Layer changed the spaces: {before_tiles} → {tiles()}")
            if [p["id"] for p in opened["pins"]] != [p["id"] for p in before["pins"]] or not all(p["available"] for p in opened["pins"]):
                raise RuntimeError("Opening the Layer changed the pins")
            steps.append("Scratch Notes opened as a focused Layer; every tiled column, width and pin was unchanged")

            closed = spaces("close-layer", str(layer["id"]))
            wait_for(lambda: not any(w.get("app_id") == apps["l"] for w in read("windows")), "the Layer to close")
            if tiles() != before_tiles or focus()["active_window_id"] != window("a")["id"]:
                raise RuntimeError("Closing the Layer moved a space or did not return focus to the Group")
            refreshed = spaces("refresh")
            if refreshed["spaces"] != before["spaces"] or refreshed["layers"] or saved() != before_state:
                raise RuntimeError(f"Closing the Layer changed the Flow state: {refreshed['spaces']}")
            steps.append("closing the Layer returned focus to the same Group app with the same Group, pins and state")
            del closed

            niri("action", "focus-window", "--id", window("u")["id"])
            spaces("make-layer")
            if not window("u").get("is_floating") or {k: v for k, v in tiles().items()} != {
                    k: v for k, v in before_tiles.items() if k != "u"}:
                raise RuntimeError("Making the neighbor a Layer moved other spaces")
            niri("action", "focus-window", "--id", window("b")["id"])
            niri("action", "focus-window", "--id", window("u")["id"])
            spaces("layer-to-space", str(window("u")["id"]))
            if tiles() != before_tiles:
                raise RuntimeError(f"The Layer did not return as its own space after the Group: {tiles()}")
            steps.append("the neighbor became a Layer and returned as its own space after the Group")
            result = {"result": "pass", "niri": niri("version").strip(), "steps": steps}
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        try:
            for w in read("windows"):
                if w.get("app_id") in apps.values():
                    niri("action", "close-window", "--id", w["id"])
        except (OSError, subprocess.SubprocessError):
            pass
    if [digest(path) for path in real_files] != real_digests:
        raise RuntimeError("A real Flow Group, pin, or session file changed")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, StopIteration, subprocess.SubprocessError, ValueError) as exc:
        print(f"Niri Layer test failed: {exc}", file=sys.stderr)
        sys.exit(1)
