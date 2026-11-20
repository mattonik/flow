#!/usr/bin/env python3
"""A side-by-side Group moved to a portrait display is stacked to fit, and returns side by side.

Run inside a Niri session with exactly two active outputs and the first one focused, from the Flow
checkout, with both outputs' active workspaces empty and ``flow-spaces`` on PATH running this
checkout's ``session/spaces.py``. The second output is rotated to portrait and its transform restored.
Flow state is isolated; real state files are checksummed and disposable Foot windows are closed.
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
    outputs = [name for name, out in read("outputs").items() if out.get("logical")]
    first = next(w for w in read("workspaces") if w["is_focused"])["output"]
    if len(outputs) != 2:
        raise RuntimeError("Exactly two active outputs are required")
    second = next(name for name in outputs if name != first)
    original = read("outputs")[second]["logical"]["transform"].lower()
    real_state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "flow"
    real_files = [real_state / name for name in ("spaces.json", "groups.json", "session.json")]
    real_digests = [digest(path) for path in real_files]
    active = {w["output"]: w["id"] for w in read("workspaces") if w.get("is_active")}
    if any(w.get("workspace_id") in active.values() for w in read("windows")):
        raise RuntimeError("Both outputs' active workspaces must be empty")
    token = uuid.uuid4().hex[:8]
    apps = {s: f"flow-reflow-{token}-{s}" for s in "abc"}
    processes, steps, result = [], [], None

    def window(suffix):
        return next(w for w in read("windows") if w.get("app_id") == apps[suffix])

    def layout(output):
        workspace = next(w["id"] for w in read("workspaces") if w["output"] == output and w.get("is_active"))
        columns = {}
        for w in read("windows"):
            pos = w.get("layout", {}).get("pos_in_scrolling_layout")
            if w.get("workspace_id") == workspace and pos and w.get("app_id") in apps.values():
                columns.setdefault(pos[0], []).append((pos[1], w["app_id"][-1]))
        return ["".join(app for _, app in sorted(c)) for _, c in sorted(columns.items())]

    try:
        niri("action", "focus-monitor", first)
        for suffix in "abc":
            processes.append(subprocess.Popen(["foot", "--app-id", apps[suffix]], stdin=subprocess.DEVNULL,
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            wait_for(lambda: any(w.get("app_id") == apps[suffix] for w in read("windows")), suffix)
        for index, suffix in enumerate("abc", 1):
            niri("action", "focus-window", "--id", window(suffix)["id"])
            niri("action", "move-column-to-index", index)
        with tempfile.TemporaryDirectory(prefix="flow-move-reflow-") as tmp:
            env = dict(os.environ, XDG_STATE_HOME=tmp)

            def spaces(*args):
                process = subprocess.run(["flow-spaces", *args], env=env, text=True, stdout=subprocess.PIPE, timeout=90)
                value = json.loads(process.stdout)
                if process.returncode or "error" in value:
                    raise RuntimeError(f"flow-spaces {args}: {value}")
                return value

            def saved():
                return json.loads((Path(tmp) / "flow/groups.json").read_text())["groups"][0]

            niri("action", "focus-window", "--id", window("a")["id"])
            spaces("stack")
            spaces("stack")
            group = next(s["group_id"] for s in spaces("create-group")["spaces"] if s.get("group_id"))
            spaces("group-layout", group)
            spaces("member-wider", str(window("a")["id"]))
            spaces("toggle-auto-layout", group)
            ratios = saved()["side_ratios"]
            keys = {d["name"]: d["key"] for d in spaces("refresh")["displays"]}
            wait_for(lambda: layout(first) == ["a", "b", "c"], "side by side on the first display")
            niri("output", second, "transform", "90" if original in ("normal", "180") else "normal")
            wait_for(lambda: (lambda l: l["height"] > l["width"])(read("outputs")[second]["logical"]), "portrait output")

            moved = spaces("move-to-display", keys["External 1"])
            wait_for(lambda: layout(second) == ["abc"], f"stacked on the portrait display ({layout(second)})")
            if "stacked for the portrait" not in moved["notice"] or saved()["layout"] != "stack" \
                    or saved()["side_ratios"] != ratios or saved()["id"] != group:
                raise RuntimeError(f"The reflow was not reported or lost the Group's proportions: {moved['notice']} {saved()}")
            steps.append(f"moved to the portrait display and stacked: {moved['notice']!r}, proportions {ratios} kept")

            back = spaces("move-to-display", keys["Main"])
            wait_for(lambda: layout(first) == ["abc"], "the stack back on the first display")
            if back["notice"]:
                raise RuntimeError(f"A stack moved back was reflowed again: {back['notice']}")
            niri("action", "focus-window", "--id", window("a")["id"])
            spaces("auto-layout")
            wait_for(lambda: layout(first) == ["a", "b", "c"], "side by side again in landscape")
            widths = [window(s)["layout"]["tile_size"][0] for s in "abc"]
            if any(abs(w / sum(widths) - r) > 0.05 for w, r in zip(widths, ratios)):
                raise RuntimeError(f"The saved proportions did not return: {widths} vs {ratios}")
            steps.append("moved back, automatic layout returned it side by side with its proportions")
            result = {"result": "pass", "niri": niri("version").strip(), "steps": steps}
    finally:
        try:
            niri("output", second, "transform", original)
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
                if w.get("app_id") in apps.values():
                    niri("action", "close-window", "--id", w["id"])
            niri("action", "focus-monitor", first)
        except (OSError, subprocess.SubprocessError):
            pass
    if read("outputs")[second]["logical"]["transform"].lower() != original:
        raise RuntimeError("The second output's transform was not restored")
    if [digest(path) for path in real_files] != real_digests:
        raise RuntimeError("A real Flow Group, pin, or session file changed")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, StopIteration, subprocess.SubprocessError, ValueError, KeyError) as exc:
        print(f"Niri move reflow test failed: {exc}", file=sys.stderr)
        sys.exit(1)
