#!/usr/bin/env python3
"""Exercise Niri's window-targeted stack/split actions with disposable Foot windows.

Run inside a disposable Niri session, from the Flow checkout:
    nix shell --inputs-from . nixpkgs#python3 -c python3 tests/niri_group_layout_smoke.py
Requires `niri`, `foot`, an empty workspace on the focused output, and no
concurrent interaction. Only temporary windows are opened; the Flow pin file is
checked for changes and never written. Workspace and focus are restored when safe.
The ARM64 UTM guest with Niri 26.04 passed with two 636-pixel columns.
This tests compositor primitives, not Flow Group navigation, persistence,
rotation, multi-display behavior, physical touch, or recovery after a crash.
"""

import json
import hashlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid


def niri(*args):
    return subprocess.check_output(["niri", "msg", *map(str, args)], text=True, timeout=10)


def read(kind):
    return json.loads(niri("-j", kind))


def focused_workspace():
    return next(w for w in read("workspaces") if w["is_focused"])


def wait_for(test, description):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = test()
        if result:
            return result
        time.sleep(0.1)
    raise RuntimeError(f"Timed out waiting for {description}")


def main():
    original = focused_workspace()
    initial_windows = read("windows")
    pin_path = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "flow/spaces.json"
    def pin_digest():
        return hashlib.sha256(pin_path.read_bytes()).digest() if pin_path.exists() else None
    initial_pins = pin_digest()
    occupied = {w.get("workspace_id") for w in initial_windows}
    empty = [w for w in read("workspaces") if w.get("output") == original.get("output")
             and w["id"] != original["id"] and w["id"] not in occupied]
    if not empty:
        raise RuntimeError("An empty workspace on the focused output is required")
    target = empty[0]
    output = read("outputs").get(original.get("output"), {}).get("logical")
    if not output or output["width"] < 600:
        raise RuntimeError("A focused output at least 600 logical pixels wide is required")
    token = uuid.uuid4().hex
    app_ids = [f"flow-layout-test-{token}-{suffix}" for suffix in ("a", "b")]
    processes = []
    test_workspace = None
    interrupted = False

    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    old_handlers = {sig: signal.signal(sig, interrupt) for sig in (signal.SIGTERM, signal.SIGHUP)}

    def checked_windows():
        nonlocal interrupted
        if focused_workspace()["id"] != test_workspace:
            interrupted = True
            raise RuntimeError("Workspace changed during the test")
        windows = read("windows")
        strangers = [w for w in windows if w.get("workspace_id") == test_workspace
                     and w.get("app_id") not in app_ids]
        if strangers:
            interrupted = True
            raise RuntimeError("Another window appeared on the test workspace")
        return windows

    def pair():
        windows = checked_windows()
        matches = [[w for w in windows if w.get("app_id") == app_id
                    and w.get("workspace_id") == test_workspace] for app_id in app_ids]
        if any(len(items) > 1 for items in matches):
            raise RuntimeError("Disposable app IDs are ambiguous")
        return [items[0] for items in matches] if all(matches) else None

    def positions(expected_same):
        windows = pair()
        if not windows:
            return None
        pos = [w.get("layout", {}).get("pos_in_scrolling_layout") for w in windows]
        if not all(pos):
            return None
        if expected_same:
            return windows if pos[0][0] == pos[1][0] and pos[0][1] < pos[1][1] else None
        return windows if pos[1][0] == pos[0][0] + 1 else None

    def sized_pair():
        windows = positions(False)
        if not windows:
            return None
        widths = [w.get("layout", {}).get("tile_size", [0])[0] for w in windows]
        return windows if all(0.3 * output["width"] < width <= 0.55 * output["width"]
                              for width in widths) and abs(widths[0] - widths[1]) < 2 else None

    try:
        # Recheck before moving; never intentionally select a workspace with windows.
        if any(w.get("workspace_id") == target["id"] for w in read("windows")):
            raise RuntimeError("The test workspace is no longer empty")
        niri("action", "focus-workspace", target["idx"])
        test_workspace = focused_workspace()["id"]
        if test_workspace != target["id"]:
            interrupted = True
            raise RuntimeError("A different workspace was focused")
        for app_id in app_ids:
            checked_windows()
            processes.append(subprocess.Popen(["foot", "--app-id", app_id],
                                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                              stderr=subprocess.DEVNULL))
            wait_for(lambda: any(w.get("app_id") == app_id for w in checked_windows()), app_id)
        windows = wait_for(lambda: positions(False), "two adjacent windows")
        first, second = (w["id"] for w in windows)
        niri("action", "focus-window", "--id", first)
        checked_windows()
        niri("action", "consume-or-expel-window-left", "--id", second)
        wait_for(lambda: positions(True), "a stack in saved order")
        if focused_workspace().get("active_window_id") != first:
            raise RuntimeError("Stacking changed the focused window")
        checked_windows()
        niri("action", "consume-or-expel-window-right", "--id", second)
        wait_for(lambda: positions(False), "the side-by-side columns")
        if focused_workspace().get("active_window_id") != first:
            raise RuntimeError("Splitting changed the focused window")
        for window_id in (first, second):
            checked_windows()
            niri("action", "set-window-width", "--id", window_id, "50%")
        windows = wait_for(sized_pair, "half-width side-by-side windows")
        widths = [w["layout"]["tile_size"][0] for w in windows]
        if focused_workspace().get("active_window_id") != first:
            raise RuntimeError("Sizing changed the focused window")
        result = {"result": "pass", "niri": niri("version").strip(),
                  "tile_widths": widths, "focus_preserved": True}
    finally:
        # Popen handles identify only the test-owned Foot processes, even if focus changed.
        for process in processes:
            try:
                if process.poll() is None:
                    process.terminate()
            except ProcessLookupError:
                pass
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        try:
            if not interrupted and test_workspace is not None and focused_workspace()["id"] == test_workspace:
                live = read("windows")
                if any(w.get("id") == original.get("active_window_id") for w in live):
                    niri("action", "focus-window", "--id", original["active_window_id"])
                else:
                    old = next((w for w in read("workspaces") if w["id"] == original["id"]), None)
                    if old:
                        niri("action", "focus-workspace", old["idx"])
        finally:
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)
    if pin_digest() != initial_pins:
        raise RuntimeError("Flow pin data changed during the test")
    def layout(windows):
        return {w["id"]: (w.get("workspace_id"),
                          w.get("layout", {}).get("pos_in_scrolling_layout")) for w in windows}
    if layout(read("windows")) != layout(initial_windows):
        raise RuntimeError("Original windows or their positions changed during the test")
    after = focused_workspace()
    if (after["id"] != original["id"] or
            after.get("active_window_id") != original.get("active_window_id")):
        raise RuntimeError("Original workspace or focus was not restored")
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, StopIteration, subprocess.SubprocessError, ValueError) as exc:
        print(f"Niri Group primitive test failed: {exc}", file=sys.stderr)
        sys.exit(1)
