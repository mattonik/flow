#!/usr/bin/env python3
"""Exercise a pinned Group of three and four disposable Foot windows in a real Niri session.

Run inside a Niri session, from the Flow checkout, with no concurrent interaction:
    python3 tests/niri_larger_group_smoke.py
Requires Foot and an empty workspace on the focused output (the focused one may be used when
it is empty). Flow state is isolated under a temporary XDG_STATE_HOME; real state files are
checksummed, and the original workspace, focus and windows are restored afterward.
"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

CODE = Path(__file__).resolve().parents[1] / "session"


def niri(*args):
    return subprocess.check_output(["niri", "msg", *map(str, args)], text=True, timeout=10)


def read(kind):
    return json.loads(niri("-j", kind))


def focus():
    return next(w for w in read("workspaces") if w["is_focused"])


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def wait_for(test, label, timeout=12):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = test()
        if value:
            return value
        time.sleep(0.1)
    raise RuntimeError(f"Timed out waiting for {label}")


def run_tool(env, script, *args, expect_error=None):
    process = subprocess.run([sys.executable, str(CODE / script), *args], env=env, text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    value = json.loads(process.stdout)
    if expect_error:
        if expect_error not in value.get("error", ""):
            raise RuntimeError(f"{script} {args} should fail with {expect_error!r}: {value}")
        return value
    if process.returncode or "error" in value:
        raise RuntimeError(f"{script} {args}: {value.get('error') or process.stderr}")
    return value


def main():
    original = focus()
    original_windows = read("windows")
    real_state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "flow"
    real_files = [real_state / name for name in ("spaces.json", "groups.json", "session.json")]
    real_digests = [digest(path) for path in real_files]
    occupied = {w.get("workspace_id") for w in original_windows}
    targets = [w for w in read("workspaces") if w.get("output") == original.get("output") and w["id"] not in occupied]
    if not targets:
        raise RuntimeError("An empty workspace on the focused output is required")
    token = uuid.uuid4().hex[:8]
    apps = [f"flow-large-{token}-{s}" for s in ("a", "b", "c", "d")]
    processes, steps = [], []
    test_workspace = None

    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    old_handlers = {sig: signal.signal(sig, interrupt) for sig in (signal.SIGTERM, signal.SIGHUP)}

    def live():
        if focus()["id"] != test_workspace:
            raise RuntimeError("Workspace changed during the test")
        windows = read("windows")
        if any(w.get("workspace_id") == test_workspace and w.get("app_id") not in apps for w in windows):
            raise RuntimeError("Another window appeared on the test workspace")
        return windows

    def layout():
        """App suffixes per column, top to bottom, on the test workspace."""
        columns = {}
        for w in live():
            pos = w.get("layout", {}).get("pos_in_scrolling_layout")
            if w.get("workspace_id") == test_workspace and pos:
                columns.setdefault(pos[0], []).append((pos[1], w["app_id"][-1]))
        return ["".join(app for _, app in sorted(column)) for _, column in sorted(columns.items())]

    def expect(value, label):
        wait_for(lambda: layout() == value, f"{label}: {value} (got {layout()})")
        steps.append(label)

    def ids():
        return {w["app_id"][-1]: w["id"] for w in live() if w.get("app_id") in apps}

    def spawn(app):
        processes.append(subprocess.Popen(["foot", "--app-id", app], stdin=subprocess.DEVNULL,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        wait_for(lambda: any(w.get("app_id") == app for w in live()), app)

    result = None
    try:
        niri("action", "focus-workspace", targets[0]["idx"])
        test_workspace = focus()["id"]
        if test_workspace != targets[0]["id"]:
            raise RuntimeError("A different workspace was focused")
        for app in apps[:3]:
            spawn(app)
        for index, suffix in enumerate("abc", 1):
            niri("action", "focus-window", "--id", ids()[suffix])
            niri("action", "move-column-to-index", index)
        expect(["a", "b", "c"], "three single windows")
        with tempfile.TemporaryDirectory(prefix="flow-large-group-") as tmp:
            env = dict(os.environ, XDG_STATE_HOME=tmp)
            state = Path(tmp) / "flow"

            def spaces(*args, **kw):
                return run_tool(env, "spaces.py", *args, **kw)

            def group():
                groups = json.loads((state / "groups.json").read_text())["groups"]
                if len(groups) != 1:
                    raise RuntimeError(f"Expected one saved Group, got {groups}")
                return groups[0]

            def pins():
                return json.loads((state / "spaces.json").read_text())

            niri("action", "focus-window", "--id", ids()["a"])
            spaces("stack")
            expect(["ab", "c"], "stack two")
            spaces("stack")
            expect(["abc"], "stack three")
            created = spaces("create-group")
            g = group()
            if g["members"] != apps[:3]:
                raise RuntimeError(f"Group member order differs from the stack: {g['members']}")
            space = next(s for s in created["spaces"] if s.get("group_id") == g["id"])
            if len(space["members"]) != 3 or space["can_add"]:
                raise RuntimeError("Three-app Group is not one destination")
            pinned = spaces("pin-group", g["id"])
            if len(pinned["pins"]) != 1 or not pinned["pins"][0]["available"]:
                raise RuntimeError("Three-app Group pin is unavailable")
            display = pinned["display"]["key"]
            if pins()["version"] != 2 or pins()["pins"][0]["display"] != display or not display:
                raise RuntimeError("Pin was not stored for the focused display")
            steps.append("pinned three-app Group on " + display)

            spawn(apps[3])
            niri("action", "focus-window", "--id", ids()["d"])
            niri("action", "move-column-to-index", 2)
            expect(["abc", "d"], "fourth window beside the Group")
            refreshed = spaces("refresh")
            space = next(s for s in refreshed["spaces"] if s.get("group_id") == g["id"])
            if not space["can_add"]:
                raise RuntimeError("Adjacent fourth window was not offered to the Group")
            niri("action", "focus-window", "--id", ids()["a"])
            spaces("add-to-group", g["id"])
            expect(["abcd"], "added fourth member")
            if group()["members"] != apps or pins()["pins"][0]["members"] != apps:
                raise RuntimeError("Registry and pin do not list four members")
            spaces("add-to-group", g["id"], expect_error="up to 4")

            spaces("group-layout", g["id"])
            expect(["a", "b", "c", "d"], "four-app side-by-side")
            widths = {w["app_id"][-1]: w["layout"]["tile_size"][0] for w in live() if w.get("app_id") in apps}
            refreshed = spaces("refresh")
            if [s.get("group_id") for s in refreshed["spaces"]] != [g["id"]] or not refreshed["pins"][0]["available"]:
                raise RuntimeError("Side-by-side Group is not one available pinned destination")
            spaces("member-right", str(ids()["b"]))
            expect(["a", "c", "b", "d"], "reordered side-by-side member")
            if group()["members"] != [apps[0], apps[2], apps[1], apps[3]]:
                raise RuntimeError("Reorder was not saved")
            spaces("remove-from-group", str(ids()["c"]), expect_error="Stack the pinned Group")
            spaces("group-layout", g["id"])
            expect(["acbd"], "stacked in the new order")
            spaces("remove-from-group", str(ids()["d"]))
            expect(["acb", "d"], "removed a member, app kept open")
            if group()["members"] != [apps[0], apps[2], apps[1]] or pins()["pins"][0]["window_ids"] != [ids()[s] for s in "acb"]:
                raise RuntimeError("Removal was not saved to registry and pin")
            spaces("member-left", str(ids()["b"]))
            expect(["abc", "d"], "reordered stacked member back")

            # Interrupt a stack → side-by-side change after one Niri action, then finish it.
            saved = group()
            saved["pending_layout"] = "side-by-side"
            registry = json.loads((state / "groups.json").read_text())
            registry["groups"] = [saved]
            (state / "groups.json").write_text(json.dumps(registry))
            niri("action", "consume-or-expel-window-right", "--id", ids()["c"])
            expect(["ab", "c", "d"], "interrupted layout change")
            refreshed = spaces("refresh")
            entry = next(e for e in refreshed["incomplete_groups"] if e["id"] == g["id"])
            if not entry["can_resume_layout"]:
                raise RuntimeError("Interrupted layout change is not resumable")
            spaces("resume-group-layout", g["id"])
            expect(["a", "b", "c", "d"], "resumed layout change")
            spaces("group-layout", g["id"])
            expect(["abc", "d"], "stack after resume")

            # Save, separate the members as after an app restart, and recover the pinned Group.
            run_tool(env, "restore.py", "save")
            snapshot = json.loads((state / "session.json").read_text())
            if snapshot.get("display") != display:
                raise RuntimeError("Session snapshot did not record its display")
            niri("action", "focus-window", "--id", ids()["d"])
            niri("action", "move-column-to-index", 1)
            for suffix in "cb":
                niri("action", "consume-or-expel-window-right", "--id", ids()[suffix])
            # Expelling from the bottom leaves the members in saved order at the end of the strip.
            expect(["d", "a", "b", "c"], "separated pinned Group")
            recovered = run_tool(env, "restore.py", "restore")
            expect(["d", "abc"], "recovered pinned three-app Group")
            steps.append("recovery: " + recovered["message"])
            refreshed = spaces("refresh")
            expect(["abc", "d"], "pinned Group reconciled to position 1")
            if not refreshed["pins"][0]["available"] or group()["id"] != g["id"]:
                raise RuntimeError("Group identity or pin was lost in recovery")

            # A fresh process sees the same identity (shell restart is a new process per call).
            again = spaces("refresh")
            if again["pins"] != refreshed["pins"]:
                raise RuntimeError("Pins changed between independent refreshes")
            spaces("unpin", again["pins"][0]["id"])
            niri("action", "focus-window", "--id", ids()["a"])
            spaces("dissolve-group", g["id"])
            expect(["a", "b", "c", "d"], "dissolved in order, all apps open")
            if json.loads((state / "groups.json").read_text())["groups"]:
                raise RuntimeError("Dissolved Group remains saved")

            # Migrate a copy of the real legacy pin file without touching the original.
            legacy = real_state / "spaces.json"
            migrated = None
            if legacy.exists():
                shutil.copy2(legacy, state / "spaces.json")
                before = legacy.read_bytes()
                spaces("refresh")
                migrated = json.loads((state / "spaces.json").read_text())
                if isinstance(json.loads(before), list):
                    if (state / "spaces.v1.json").read_bytes() != before:
                        raise RuntimeError("Legacy pin file was not kept for rollback")
                    if [p["id"] for p in migrated["pins"]] != [p["id"] for p in json.loads(before)]:
                        raise RuntimeError("Migration changed pin order")
                steps.append("migrated legacy pins: " + json.dumps([p.get("display") for p in migrated["pins"]]))
            result = {"result": "pass", "niri": niri("version").strip(), "display": display,
                      "four_app_widths": widths, "steps": steps}
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
            for window in read("windows"):
                if window.get("app_id") in apps:
                    niri("action", "close-window", "--id", window["id"])
            old = next((w for w in read("workspaces") if w["id"] == original["id"]), None)
            if old:
                niri("action", "focus-workspace", old["idx"])
            if original.get("active_window_id") in {w["id"] for w in read("windows")}:
                niri("action", "focus-window", "--id", original["active_window_id"])
        finally:
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)
    if [digest(path) for path in real_files] != real_digests:
        raise RuntimeError("A real Flow Group, pin, or session file changed")
    if {w["id"] for w in read("windows")} != {w["id"] for w in original_windows}:
        raise RuntimeError("Original window inventory changed")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, StopIteration, subprocess.SubprocessError, ValueError) as exc:
        print(f"Niri larger Group test failed: {exc}", file=sys.stderr)
        sys.exit(1)
