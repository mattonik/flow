#!/usr/bin/env python3
"""Restart a development Flow shell, gracefully and by SIGKILL, and check nothing about the work changed.

Run inside a Niri session from the Flow checkout, with the focused output's active workspace empty.
``FLOW_SHELL`` names the development ``shell.qml``; ``flow-spaces`` on PATH must run this checkout's
``session/spaces.py``. A pinned app, a pinned side-by-side Group and an unpinned app are set up with
isolated Flow state. Across each restart the pins, Groups, window IDs, columns, widths and focus must
be unchanged, and Super+number must still reach the same pin. Real state files are checksummed.
The isolated state folder must be gone afterwards: a stopped shell's helpers can outlive it and write there.
"""

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

from isolated_state import IsolatedState


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
    shell = os.environ.get("FLOW_SHELL")
    if not shell:
        raise RuntimeError("Set FLOW_SHELL to the development shell.qml")
    workspace = focus()
    real_state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "flow"
    real_files = [real_state / name for name in ("spaces.json", "groups.json", "session.json")]
    real_digests = [digest(path) for path in real_files]
    if any(w.get("workspace_id") == workspace["id"] for w in read("windows")):
        raise RuntimeError("The focused output's active workspace must be empty")
    token = uuid.uuid4().hex[:8]
    apps = {s: f"flow-restart-{token}-{s}" for s in "pabu"}
    processes, steps, result, shell_process = [], [], None, None
    with IsolatedState("flow-shell-restart-") as tmp:
        env = dict(os.environ, XDG_STATE_HOME=tmp)
        state = Path(tmp) / "flow"

        def window(suffix):
            found = [w for w in read("windows") if w.get("app_id") == apps[suffix]]
            if len(found) != 1:
                raise RuntimeError(f"Window {suffix} is missing or duplicated")
            return found[0]

        def work():
            """Everything about the test windows that a shell restart must not change."""
            windows = sorted((w for w in read("windows") if w.get("app_id") in apps.values()), key=lambda w: w["id"])
            if any(w["workspace_id"] != workspace["id"] for w in windows) or len(windows) != len(apps):
                raise RuntimeError("A test window closed or left the workspace")
            return ({w["app_id"][-1]: (w["id"], tuple(w["layout"]["pos_in_scrolling_layout"]),
                                       round(w["layout"]["tile_size"][0])) for w in windows},
                    focus()["active_window_id"])

        def ipc(*args):
            return subprocess.run(["quickshell", "ipc", "-p", shell, *map(str, args)], text=True, timeout=10,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout.strip()

        try:
            for suffix in "pabu":
                processes.append(subprocess.Popen(["foot", "--app-id", apps[suffix]], stdin=subprocess.DEVNULL,
                                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
                wait_for(lambda: any(w.get("app_id") == apps[suffix] for w in read("windows")), suffix)
            for index, suffix in enumerate("pabu", 1):
                niri("action", "focus-window", "--id", window(suffix)["id"])
                niri("action", "move-column-to-index", index)
            def spaces(*args):
                process = subprocess.run(["flow-spaces", *args], env=env, text=True, stdout=subprocess.PIPE, timeout=60)
                value = json.loads(process.stdout)
                if process.returncode or "error" in value:
                    raise RuntimeError(f"flow-spaces {args}: {value}")
                return value

            def saved():
                return (json.loads((state / "spaces.json").read_text()), json.loads((state / "groups.json").read_text()))

            niri("action", "focus-window", "--id", window("p")["id"])
            spaces("pin")
            niri("action", "focus-window", "--id", window("a")["id"])
            spaces("stack")
            group = next(s["group_id"] for s in spaces("create-group")["spaces"] if s.get("group_id"))
            spaces("pin-group", group)
            spaces("group-layout", group)
            spaces("member-wider", str(window("a")["id"]))
            niri("action", "focus-window", "--id", window("b")["id"])
            wait_for(lambda: focus()["active_window_id"] == window("b")["id"], "focus on the second Group app")
            spaces("refresh")  # record the focused Group app, as any refresh does, before the baseline

            def start():
                process = subprocess.Popen(["quickshell", "-p", shell], env=env, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                def ready():
                    if process.poll() is not None:
                        raise RuntimeError("The development shell exited")
                    try:
                        return ipc("call", "desk", "toast") == ""
                    except subprocess.CalledProcessError:
                        return False

                wait_for(ready, "development shell", timeout=20)
                time.sleep(3)  # let the shell's startup refresh and event-driven refreshes run
                return process

            before_work, before_state = work(), saved()
            shell_process = start()
            if work() != before_work or saved() != before_state:
                raise RuntimeError(f"Starting the shell changed the work or Flow state: {before_work} → {work()}")
            steps.append(f"set up {len(before_state[0]['pins'])} pins and a side-by-side Group; the shell started without changing them")
            for how, sig in (("graceful restart", signal.SIGTERM), ("crash restart", signal.SIGKILL)):
                shell_process.send_signal(sig)
                shell_process.wait(timeout=10)
                time.sleep(1)
                if work() != before_work:
                    raise RuntimeError(f"Stopping the shell changed the work: {before_work} → {work()}")
                shell_process = start()
                if work() != before_work or saved() != before_state:
                    raise RuntimeError(f"{how} changed the work or Flow state: {before_work} → {work()}")
                steps.append(f"{how}: same pins, Group, window IDs, columns, widths and focus")
            ipc("call", "desk", "focus", 1)
            wait_for(lambda: focus()["active_window_id"] == window("p")["id"], "Super+1 after the restarts")
            ipc("call", "desk", "focus", 2)
            wait_for(lambda: focus()["active_window_id"] in (window("a")["id"], window("b")["id"]),
                     "Super+2 reaching the pinned Group")
            steps.append("Super+1 and Super+2 still reached the pinned app and the pinned Group")
            result = {"result": "pass", "niri": niri("version").strip(), "steps": steps}
        finally:
            if shell_process and shell_process.poll() is None:
                shell_process.terminate()
                try:
                    shell_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    shell_process.kill()
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
    steps.append("the shell's helpers exited before the isolated state folder was removed, and it stayed gone")
    if [digest(path) for path in real_files] != real_digests:
        raise RuntimeError("A real Flow Group, pin, or session file changed")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, StopIteration, subprocess.SubprocessError, ValueError) as exc:
        print(f"Niri shell restart test failed: {exc}", file=sys.stderr)
        sys.exit(1)
