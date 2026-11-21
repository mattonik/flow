#!/usr/bin/env python3
"""A real Group beside other windows of its own apps: it forms, changes layout and is repaired by window.

Run inside a Niri session from the Flow checkout, with the focused output's active workspace empty and
``flow-spaces`` on PATH running this checkout's ``session/spaces.py``. Two Foot app IDs each get two
windows; the Group takes one of each. Flow state and data are isolated; real state files are checksummed
and every test window is closed afterward.
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
    term, edit = f"flow-same-{token}-term", f"flow-same-{token}-edit"
    processes, steps, result = [], [], None

    def ours():
        return [w for w in read("windows") if w.get("app_id") in (term, edit)]

    def columns():
        """Window IDs per column, top to bottom, on the test workspace."""
        found = {}
        for w in ours():
            pos = w["layout"].get("pos_in_scrolling_layout")
            if w["workspace_id"] == workspace["id"] and pos:
                found.setdefault(pos[0], []).append((pos[1], w["id"]))
        return [[i for _, i in sorted(c)] for _, c in sorted(found.items())]

    def spawn(app):
        before = {w["id"] for w in ours()}
        processes.append(subprocess.Popen(["foot", "--app-id", app], stdin=subprocess.DEVNULL,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        return wait_for(lambda: next((w["id"] for w in ours() if w["id"] not in before), None), app)

    try:
        t1, e1, t2, e2 = spawn(term), spawn(edit), spawn(term), spawn(edit)
        for index, ident in enumerate((t1, e1, t2, e2), 1):
            niri("action", "focus-window", "--id", ident)
            niri("action", "move-column-to-index", index)
        with tempfile.TemporaryDirectory(prefix="flow-same-app-") as tmp:
            data = Path(tmp) / "data"
            (data / "applications").mkdir(parents=True)
            (data / "applications" / f"{edit}.desktop").write_text(
                f"[Desktop Entry]\nType=Application\nName=Editor\nExec=foot --app-id {edit}\n")
            env = dict(os.environ, XDG_STATE_HOME=str(Path(tmp) / "state"), XDG_DATA_HOME=str(data))

            def spaces(*args, expect_error=None):
                process = subprocess.run(["flow-spaces", *args], env=env, text=True, stdout=subprocess.PIPE, timeout=60)
                value = json.loads(process.stdout)
                if expect_error:
                    if expect_error not in value.get("error", ""):
                        raise RuntimeError(f"flow-spaces {args} should fail with {expect_error!r}: {value}")
                    return value
                if process.returncode or "error" in value:
                    raise RuntimeError(f"flow-spaces {args}: {value}")
                return value

            def group():
                (saved,) = json.loads((Path(tmp) / "state/flow/groups.json").read_text())["groups"]
                return saved

            niri("action", "focus-window", "--id", t1)
            spaces("stack")
            created = spaces("create-group")
            gid = group()["id"]
            space = [s for s in created["spaces"] if s.get("group_id")]
            if [[m["id"] for m in s["members"]] for s in space] != [[t1, e1]] or len(created["spaces"]) != 3:
                raise RuntimeError(f"The Group did not form beside other windows of its apps: {created['spaces']}")
            steps.append("a Group formed from one window of each app while a second window of each stayed open")

            niri("action", "focus-window", "--id", t2)
            spaces("make-layer")
            if not next(w for w in ours() if w["id"] == t2).get("is_floating") or columns()[0] != [t1, e1]:
                raise RuntimeError("The other terminal was not treated as its own space")
            spaces("layer-to-space", str(t2))
            wait_for(lambda: columns()[0] == [t1, e1] and [t2] in columns(), f"the terminal back as its own space ({columns()})")
            for index, ident in enumerate((t2, e2), 2):
                niri("action", "focus-window", "--id", ident)
                niri("action", "move-column-to-index", index)
            niri("action", "focus-window", "--id", t1)
            steps.append("the other terminal became a Layer and a space again while the Group stayed intact")

            spaces("group-layout", gid)
            wait_for(lambda: columns() == [[t1], [e1], [t2], [e2]], f"side by side ({columns()})")
            spaces("member-wider", str(t1))
            spaces("group-layout", gid)
            wait_for(lambda: columns() == [[t1, e1], [t2], [e2]], f"stacked again ({columns()})")
            steps.append("layout changes and resizing moved only the Group's own windows")

            niri("action", "close-window", "--id", e1)
            wait_for(lambda: e1 not in [w["id"] for w in ours()], "the Group's editor to close")
            refreshed = spaces("refresh")
            (incomplete,) = refreshed["incomplete_groups"]
            if any(s.get("group_id") for s in refreshed["spaces"]) or incomplete["missing_app"] != edit:
                raise RuntimeError(f"The other editor window was adopted, or the gap was not reported: {incomplete}")
            steps.append("closing the Group's editor left a gap; the other editor window was not adopted")

            spaces("open-group-member", gid)
            new = group()["bound"]["windows"][edit]
            if new in (e1, e2) or new not in [w["id"] for w in ours()]:
                raise RuntimeError(f"The reopened editor was not the window bound to the Group: {new}")
            steps.append("opening the missing app bound its new window, not the older one")
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
            for w in ours():
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
        print(f"Niri same-app Group test failed: {exc}", file=sys.stderr)
        sys.exit(1)
