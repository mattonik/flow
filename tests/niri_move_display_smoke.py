#!/usr/bin/env python3
"""Move a pinned three-app Group and a single app between two real Niri outputs.

Run inside a Niri session with exactly two active outputs and the first one focused, from the
Flow checkout. Both outputs' active workspaces must be empty. Flow state is isolated under a
temporary XDG_STATE_HOME; real state files are checksummed and disposable Foot windows are closed.
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

CODE = Path(__file__).resolve().parents[1] / "session"


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


def run_tool(env, *args, expect_error=None):
    process = subprocess.run([sys.executable, str(CODE / "spaces.py"), *args], env=env, text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    value = json.loads(process.stdout)
    if expect_error:
        if expect_error not in value.get("error", ""):
            raise RuntimeError(f"{args} should fail with {expect_error!r}: {value}")
        return value
    if process.returncode or "error" in value:
        raise RuntimeError(f"{args}: {value.get('error') or process.stderr}")
    return value


def main():
    outputs = {name: out for name, out in read("outputs").items() if out.get("logical")}
    original = focus()
    first = original["output"]
    if len(outputs) != 2:
        raise RuntimeError("Exactly two active outputs are required")
    second = next(name for name in outputs if name != first)
    real_state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "flow"
    real_files = [real_state / name for name in ("spaces.json", "groups.json", "session.json")]
    real_digests = [digest(path) for path in real_files]
    active = {w["output"]: w["id"] for w in read("workspaces") if w.get("is_active")}
    if any(w.get("workspace_id") in active.values() for w in read("windows")):
        raise RuntimeError("Both outputs' active workspaces must be empty")
    token = uuid.uuid4().hex[:8]
    apps = {s: f"flow-move-{token}-{s}" for s in "abctu"}
    processes, steps, result = [], [], None

    def window(suffix):
        found = [w for w in read("windows") if w.get("app_id") == apps[suffix]]
        if len(found) != 1:
            raise RuntimeError(f"Window {suffix} is missing or duplicated")
        return found[0]

    def layout(output):
        """App suffixes per column, top to bottom, on an output's active workspace."""
        workspace = next(w["id"] for w in read("workspaces") if w["output"] == output and w.get("is_active"))
        columns = {}
        for w in read("windows"):
            pos = w.get("layout", {}).get("pos_in_scrolling_layout")
            if w.get("workspace_id") == workspace and pos:
                if w.get("app_id") not in apps.values():
                    raise RuntimeError("Another window appeared on a test workspace")
                columns.setdefault(pos[0], []).append((pos[1], w["app_id"][-1]))
        return ["".join(app for _, app in sorted(column)) for _, column in sorted(columns.items())]

    def expect(first_layout, second_layout, label):
        wait_for(lambda: layout(first) == first_layout and layout(second) == second_layout,
                 f"{label}: {first_layout} / {second_layout} (got {layout(first)} / {layout(second)})")
        steps.append(label)

    def spawn(suffix, output):
        niri("action", "focus-monitor", output)
        processes.append(subprocess.Popen(["foot", "--app-id", apps[suffix]], stdin=subprocess.DEVNULL,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        wait_for(lambda: any(w.get("app_id") == apps[suffix] for w in read("windows")), suffix)

    try:
        for suffix in "abc":
            spawn(suffix, first)
        spawn("t", second)
        for index, suffix in enumerate("abc", 1):
            niri("action", "focus-window", "--id", window(suffix)["id"])
            niri("action", "move-column-to-index", index)
        expect(["a", "b", "c"], ["t"], "three windows on the first display, one on the second")
        ids = {s: window(s)["id"] for s in "abct"}
        with tempfile.TemporaryDirectory(prefix="flow-move-display-") as tmp:
            env = dict(os.environ, XDG_STATE_HOME=tmp)
            state = Path(tmp) / "flow"

            def spaces(*args, **kw):
                return run_tool(env, *args, **kw)

            def saved():
                return (json.loads((state / "groups.json").read_text())["groups"],
                        json.loads((state / "spaces.json").read_text())["pins"])

            niri("action", "focus-window", "--id", ids["t"])
            second_key = spaces("pin")["display"]["key"]
            niri("action", "focus-window", "--id", ids["a"])
            spaces("stack")
            spaces("stack")
            spaces("create-group")
            (group,), _ = saved()
            pinned = spaces("pin-group", group["id"])
            first_key = pinned["display"]["key"]
            spaces("group-layout", group["id"])
            expect(["a", "b", "c"], ["t"], "pinned three-app Group side by side")
            group_pin = next(p["id"] for p in pinned["pins"])

            niri("action", "focus-window", "--id", ids["b"])
            moved = spaces("move-to-display", second_key)
            expect([], ["t", "a", "b", "c"], "Group moved whole and took its pinned place after Terminal")
            groups, pins = saved()
            if (focus()["output"] != second or focus()["active_window_id"] != ids["b"]
                    or [p["id"] for p in moved["pins"]][1:] != [group_pin]
                    or next(p["display"] for p in pins if p["id"] == group_pin) != second_key
                    or groups[0]["id"] != group["id"] or groups[0]["layout"] != "side-by-side"
                    or [s.get("group_id") for s in moved["spaces"]] != [None, group["id"]]
                    or {s: window(s)["id"] for s in "abct"} != ids):
                raise RuntimeError(f"Group move changed identity, focus or pins: {moved}")
            niri("action", "focus-monitor", first)
            if spaces("refresh")["pins"]:
                raise RuntimeError("The first display kept the moved pin")
            steps.append("same Group, pin, windows and focus on the second display")

            spawn("u", first)
            niri("action", "focus-monitor", second)
            niri("action", "focus-window", "--id", ids["a"])
            niri("action", "focus-window", "--id", window("u")["id"])
            spaces("move-to-display", second_key)
            expect([], ["t", "a", "b", "c", "u"], "a moved app landed after the Group, not inside it")

            niri("action", "focus-window", "--id", ids["c"])
            spaces("group-layout", group["id"])
            expect([], ["t", "abc", "u"], "Group stacked on the second display")
            back = spaces("move-to-display", first_key)
            expect(["abc"], ["t", "u"], "stacked Group moved back as one column")
            if ([p["id"] for p in back["pins"]] != [group_pin] or focus()["active_window_id"] != ids["c"]
                    or back["display"]["key"] != first_key):
                raise RuntimeError(f"Stacked Group did not return with its pin and focus: {back}")
            spaces("move-to-display", first_key, expect_error="already on that display")
            steps.append("moving to the same display was refused")
            result = {"result": "pass", "niri": niri("version").strip(), "outputs": [first, second], "steps": steps}
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
            niri("action", "focus-monitor", first)
        except (OSError, subprocess.SubprocessError):
            pass
    if [digest(path) for path in real_files] != real_digests:
        raise RuntimeError("A real Flow Group, pin, or session file changed")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, StopIteration, subprocess.SubprocessError, ValueError) as exc:
        print(f"Niri move display test failed: {exc}", file=sys.stderr)
        sys.exit(1)
