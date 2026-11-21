#!/usr/bin/env python3
"""Resize apps in a real side-by-side three-app Group and check the saved proportions survive a stack.

Run inside a Niri session from the Flow checkout, with the focused output's active workspace empty.
Flow state is isolated under a temporary XDG_STATE_HOME; real state files are checksummed and
disposable Foot windows are closed afterward.
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
    workspace = focus()
    real_state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "flow"
    real_files = [real_state / name for name in ("spaces.json", "groups.json", "session.json")]
    real_digests = [digest(path) for path in real_files]
    if any(w.get("workspace_id") == workspace["id"] for w in read("windows")):
        raise RuntimeError("The focused output's active workspace must be empty")
    token = uuid.uuid4().hex[:8]
    apps = {s: f"flow-resize-{token}-{s}" for s in "abc"}
    processes, steps, result = [], [], None

    def windows():
        found = {w["app_id"][-1]: w for w in read("windows") if w.get("app_id") in apps.values()}
        if len(found) != 3 or any(w["workspace_id"] != workspace["id"] for w in found.values()):
            raise RuntimeError("A test window is missing or left the workspace")
        return found

    def shares():
        widths = [windows()[s]["layout"]["tile_size"][0] for s in "abc"]
        return [round(100 * w / sum(widths)) for w in widths]

    try:
        for suffix in "abc":
            processes.append(subprocess.Popen(["foot", "--app-id", apps[suffix]], stdin=subprocess.DEVNULL,
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            wait_for(lambda: any(w.get("app_id") == apps[suffix] for w in read("windows")), suffix)
        for index, suffix in enumerate("abc", 1):
            niri("action", "focus-window", "--id", windows()[suffix]["id"])
            niri("action", "move-column-to-index", index)
        with tempfile.TemporaryDirectory(prefix="flow-group-resize-") as tmp:
            env = dict(os.environ, XDG_STATE_HOME=tmp)

            def spaces(*args, **kw):
                return run_tool(env, *args, **kw)

            def saved():
                return json.loads((Path(tmp) / "flow/groups.json").read_text())["groups"][0]

            niri("action", "focus-window", "--id", windows()["a"]["id"])
            spaces("stack")
            spaces("stack")
            spaces("create-group")
            group = saved()["id"]
            spaces("group-layout", group)
            wait_for(lambda: all(abs(s - 33) <= 3 for s in shares()), f"three equal columns (got {shares()})")
            steps.append(f"side by side at {shares()}")

            ids = {s: str(w["id"]) for s, w in windows().items()}
            spaces("member-wider", ids["a"])
            wait_for(lambda: abs(shares()[0] - 43) <= 3 and abs(shares()[1] - 23) <= 3, f"wider first app ({shares()})")
            spaces("member-narrower", ids["c"])
            wait_for(lambda: abs(shares()[2] - 23) <= 3 and abs(shares()[1] - 33) <= 3, f"narrower last app ({shares()})")
            ratios = saved().get("side_ratios")
            if not ratios or any(abs(100 * r - s) > 3 for r, s in zip(ratios, shares())):
                raise RuntimeError(f"Saved proportions differ from the screen: {ratios} vs {shares()}")
            steps.append(f"wider and narrower steps gave {shares()}, saved as {ratios}")

            space = next(s for s in spaces("refresh")["spaces"] if s.get("group_id") == group)
            if any(abs(a - b) > 3 for a, b in zip(space["shares"], shares())) or len(space["can_widen"]) != 3:
                raise RuntimeError(f"The switcher projection does not show the widths: {space}")
            if space["can_narrow"][2]:
                raise RuntimeError("Another narrowing step was offered below the minimum share")
            spaces("member-narrower", ids["c"], expect_error="already as narrow")
            steps.append("a step below the minimum share was not offered and was refused")

            before = shares()
            spaces("group-layout", group)
            wait_for(lambda: len({windows()[s]["layout"]["pos_in_scrolling_layout"][0] for s in "abc"}) == 1, "stack")
            spaces("group-layout", group)
            wait_for(lambda: all(abs(a - b) <= 3 for a, b in zip(shares(), before)), f"proportions restored ({shares()})")
            steps.append(f"stack and back restored {shares()}")
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
        print(f"Niri Group resize test failed: {exc}", file=sys.stderr)
        sys.exit(1)
