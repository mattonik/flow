#!/usr/bin/env python3
"""Restart a development Flow shell, gracefully and by SIGKILL, and check nothing about the work changed.

Run inside a Niri session from the Flow checkout, with the focused output's active workspace empty.
``FLOW_SHELL`` names the development ``shell.qml``; ``flow-spaces`` on PATH must run this checkout's
``session/spaces.py``. A pinned app, a pinned side-by-side Group and an unpinned app are set up with
isolated Flow state. Across each restart the pins, Groups, window IDs, columns, widths and focus must
be unchanged, and Super+number must still reach the same pin. Real state files are checksummed.

Then the shell restarts during Flow actions it started: a Group layout change (twice), a pin and a Layer
opening. Each action waits on Flow's session lock, held here, while its shell is stopped (SIGTERM or
SIGKILL); the lock is then released, so the action runs, with its output pipes closed, while a new shell
starts. It must finish, its state must be whole (no Group left mid-change), and the new shell must show it.
The isolated state folder must be gone afterwards: a stopped shell's helpers can outlive it and write there.
"""

import fcntl
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


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


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
    layer_app = f"flow-restart-{token}-layer"
    processes, steps, result, shell_process, orphan = [], [], None, None, None
    with IsolatedState("flow-shell-restart-") as tmp:
        data = Path(tmp) / "data"
        (data / "applications").mkdir(parents=True)
        (data / "applications" / f"{layer_app}.desktop").write_text(
            f"[Desktop Entry]\nType=Application\nName=Scratch Notes\nExec=foot --app-id {layer_app}\n")
        env = dict(os.environ, XDG_STATE_HOME=tmp, XDG_DATA_HOME=str(data))
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

            def actions(pattern):
                """The shell's flow-spaces processes whose arguments match ``pattern``: the installed wrapper runs
                ``python3 -m spaces``, a development one may run ``spaces.py`` by its path."""
                found = subprocess.run(["pgrep", "-P", str(shell_process.pid), "-f", f"(spaces\\.py|-m spaces) {pattern}"],
                                       text=True, stdout=subprocess.PIPE).stdout.split()
                return [int(pid) for pid in found]

            def view():
                return json.loads(ipc("call", "desk", "spaces_view"))

            def group_record():
                return next(g for g in json.loads((state / "groups.json").read_text())["groups"] if g["id"] == group)

            def group_shown(layout):
                record = group_record()
                columns = {window(s)["layout"]["pos_in_scrolling_layout"][0] for s in "ab"}
                return (record.get("layout", "stack") == layout and "pending_layout" not in record
                        and len(columns) == (1 if layout == "stack" else 2)
                        and any(s["group_id"] == group and s["group_layout"] == layout for s in view()["spaces"]))

            def pin_shown():
                pins = json.loads((state / "spaces.json").read_text())["pins"]
                ident = window("u")["id"]
                return (any(p.get("app_id") == apps["u"] for p in pins)
                        and any(ident in p["window_ids"] for p in view()["pins"]))

            def layer_shown():
                found = [w for w in read("windows") if w.get("app_id") == layer_app]
                return (len(found) == 1 and found[0]["is_floating"]
                        and found[0]["id"] in view()["layers"])

            # Pins and Groups change only while every Group is a stack, so the layout goes first and last.
            cases = [("group-layout", ("group_layout", group), "a", signal.SIGTERM, lambda: group_shown("stack")),
                     ("pin", ("pin",), "u", signal.SIGKILL, pin_shown),
                     ("open-layer", ("open_layer", layer_app), "u", signal.SIGTERM, layer_shown),
                     ("group-layout", ("group_layout", group), "a", signal.SIGKILL, lambda: group_shown("side-by-side"))]
            for action, call, focused, sig, finished in cases:
                niri("action", "focus-window", "--id", window(focused)["id"])
                with (state / "session.lock").open("a") as lock:
                    # The lock, with none of the shell's refreshes waiting on it: the action is then the next.
                    for _ in range(40):
                        time.sleep(0.5)
                        fcntl.flock(lock, fcntl.LOCK_EX)
                        if not actions(""):
                            break
                        fcntl.flock(lock, fcntl.LOCK_UN)
                    else:
                        raise RuntimeError("The shell's refreshes kept running")
                    ipc("call", "desk", *call)
                    while not (mine := actions(f"{action}( |$)")):
                        # A refresh that began first waits on the lock, the action queued behind it in the shell.
                        # Let it run, and take the lock back as it exits, before the shell starts the action.
                        first = wait_for(lambda: actions(""), lambda: f"the shell's flow-spaces {action}", timeout=10)
                        fcntl.flock(lock, fcntl.LOCK_UN)
                        while any(alive(pid) for pid in first):
                            time.sleep(0.002)
                        fcntl.flock(lock, fcntl.LOCK_EX)
                        if not actions(""):
                            time.sleep(1)
                            if not actions(""):
                                raise RuntimeError(f"flow-spaces {action} ran before the shell could be stopped; run again")
                    orphan = mine[0]
                    shell_process.send_signal(sig)
                    shell_process.wait(timeout=10)
                    if not alive(orphan):
                        raise RuntimeError(f"Stopping the shell ended its flow-spaces {action}")
                # The lock is released: the action runs while the new shell starts, its startup refresh and
                # automatic layout and recovery included.
                shell_process = start()
                wait_for(lambda: not alive(orphan), f"flow-spaces {action} to finish", timeout=60)
                orphan = None
                wait_for(finished, f"{action} to be whole and shown by the new shell", timeout=20)
                steps.append(f"{action}, its shell stopped by {sig.name}: the action ran as a new shell started, "
                             "finished whole, and the new shell showed it")
            result = {"result": "pass", "niri": niri("version").strip(), "steps": steps}
        finally:
            if orphan and alive(orphan):
                os.kill(orphan, signal.SIGKILL)
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
                    if w.get("app_id") in (*apps.values(), layer_app):
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
