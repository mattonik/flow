#!/usr/bin/env python3
"""On a portrait output a Layer is a full-width bottom sheet; in landscape it is a centered window.

Run inside a Niri session from the Flow checkout, with the focused output's active workspace empty and
``flow-spaces`` on PATH running this checkout's ``session/spaces.py``. The focused output is rotated
to portrait and its original transform is restored afterward. Flow state is isolated; real state files
are checksummed and the disposable Foot windows are closed.
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
    workspace = next(w for w in read("workspaces") if w["is_focused"])
    output = workspace["output"]
    original = read("outputs")[output]["logical"]["transform"].lower()
    real_state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "flow"
    real_files = [real_state / name for name in ("spaces.json", "groups.json", "session.json")]
    real_digests = [digest(path) for path in real_files]
    if any(w.get("workspace_id") == workspace["id"] for w in read("windows")):
        raise RuntimeError("The focused output's active workspace must be empty")
    token = uuid.uuid4().hex[:8]
    apps = [f"flow-sheet-{token}-{s}" for s in ("under", "layer")]
    processes, steps, result = [], [], None

    def window(app):
        return next(w for w in read("windows") if w.get("app_id") == app)

    def logical():
        value = read("outputs")[output]["logical"]
        return value["width"], value["height"]

    def when(get, ok):
        """A waiter that returns the value once ``ok(value)`` holds."""
        def check():
            value = get()
            return value if ok(value) else None
        return check

    try:
        for app in apps:
            processes.append(subprocess.Popen(["foot", "--app-id", app], stdin=subprocess.DEVNULL,
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            wait_for(lambda: any(w.get("app_id") == app for w in read("windows")), app)
        with tempfile.TemporaryDirectory(prefix="flow-layer-sheet-") as tmp:
            env = dict(os.environ, XDG_STATE_HOME=tmp)

            def spaces(*args):
                process = subprocess.run(["flow-spaces", *args], env=env, text=True, stdout=subprocess.PIPE, timeout=60)
                value = json.loads(process.stdout)
                if process.returncode or "error" in value:
                    raise RuntimeError(f"flow-spaces {args}: {value}")
                return value

            def geometry():
                layout = window(apps[1])["layout"]
                return layout.get("tile_pos_in_workspace_view"), layout.get("tile_size")

            niri("output", output, "transform", "90" if original in ("normal", "180") else "normal")
            width, height = wait_for(when(logical, lambda v: v[1] > v[0]), "portrait output")
            niri("action", "focus-window", "--id", window(apps[1])["id"])
            spaces("make-layer")
            pos, size = wait_for(when(geometry, lambda g: g[0] and g[0][1] > 0), "the bottom sheet")
            if abs(pos[0]) > 4 or abs(pos[1] - height / 2) > 8 or abs(size[0] - width) > 8 or abs(size[1] - height / 2) > 12:
                raise RuntimeError(f"The portrait Layer is not a bottom sheet: {pos} {size} on {width}x{height}")
            steps.append(f"portrait {width}x{height}: Layer at {pos}, size {size}")
            spaces("layer-to-space", str(window(apps[1])["id"]))
            niri("output", output, "transform", original)
            width, height = wait_for(when(logical, lambda v: v[0] > v[1]), "landscape output")
            niri("action", "focus-window", "--id", window(apps[1])["id"])
            spaces("make-layer")
            pos, size = wait_for(when(geometry, lambda g: g[0] and g[1] and g[1][0] < width * 0.6), "the centered Layer")
            if abs(pos[0] + size[0] / 2 - width / 2) > 8 or abs(pos[1] + size[1] / 2 - height / 2) > 8:
                raise RuntimeError(f"The landscape Layer is not centered: {pos} {size} on {width}x{height}")
            steps.append(f"landscape {width}x{height}: centered Layer at {pos}, size {size}")
            result = {"result": "pass", "niri": niri("version").strip(), "steps": steps}
    finally:
        try:
            niri("output", output, "transform", original)
        except (OSError, subprocess.SubprocessError):
            pass
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
                if w.get("app_id") in apps:
                    niri("action", "close-window", "--id", w["id"])
        except (OSError, subprocess.SubprocessError):
            pass
    if read("outputs")[output]["logical"]["transform"].lower() != original:
        raise RuntimeError("The output transform was not restored")
    if [digest(path) for path in real_files] != real_digests:
        raise RuntimeError("A real Flow Group, pin, or session file changed")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, StopIteration, subprocess.SubprocessError, ValueError, KeyError) as exc:
        print(f"Niri Layer sheet test failed: {exc}", file=sys.stderr)
        sys.exit(1)
