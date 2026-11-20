#!/usr/bin/env python3
"""Exercise the display-number shortcut grammar through a development Flow shell on two Niri outputs.

Run inside a Niri session with exactly two active outputs and the first one focused, from the Flow
checkout. Both outputs' active workspaces must be empty. ``FLOW_SHELL`` names the development
``shell.qml``; ``flow-spaces`` on PATH must run this checkout's ``session/spaces.py``. The script calls
the same shell IPC functions that Niri's Super+number and Super+Alt+digit bindings spawn; it does not
press physical keys. Flow state is isolated; real state files are checksummed and windows are closed.
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
    shell = os.environ.get("FLOW_SHELL")
    if not shell:
        raise RuntimeError("Set FLOW_SHELL to the development shell.qml")
    outputs = {name: out for name, out in read("outputs").items() if out.get("logical")}
    first = focus()["output"]
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
    apps = {s: f"flow-shortcut-{token}-{s}" for s in ("m", "t1", "t2")}
    processes, steps, result, shell_process = [], [], None, None

    def window(suffix):
        found = [w for w in read("windows") if w.get("app_id") == apps[suffix]]
        if len(found) != 1:
            raise RuntimeError(f"Window {suffix} is missing or duplicated")
        return found[0]

    def spawn(suffix, output):
        niri("action", "focus-monitor", output)
        processes.append(subprocess.Popen(["foot", "--app-id", apps[suffix]], stdin=subprocess.DEVNULL,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        wait_for(lambda: any(w.get("app_id") == apps[suffix] for w in read("windows")), suffix)

    def ipc(*args):
        return subprocess.run(["quickshell", "ipc", "-p", shell, *map(str, args)], text=True, timeout=10,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout.strip()

    def focused_on(output, suffix):
        return lambda: focus()["output"] == output and focus()["active_window_id"] == window(suffix)["id"]

    try:
        spawn("m", first)
        spawn("t1", second)
        spawn("t2", second)
        with tempfile.TemporaryDirectory(prefix="flow-display-shortcut-") as tmp:
            env = dict(os.environ, XDG_STATE_HOME=tmp)
            for suffix in ("m", "t1", "t2"):
                niri("action", "focus-window", "--id", window(suffix)["id"])
                pinned = subprocess.run(["flow-spaces", "pin"], env=env, text=True, stdout=subprocess.PIPE, timeout=60)
                if pinned.returncode:
                    raise RuntimeError(f"Could not pin {suffix}: {pinned.stdout}")
            names = {d["name"]: d for d in json.loads(pinned.stdout)["displays"]}
            if set(names) != {"Main", "External 1"} or names["Main"]["label"] == names["External 1"]["label"]:
                raise RuntimeError(f"Displays were not numbered Main and External 1: {names}")
            steps.append("displays numbered Main and External 1 in screen order")
            shell_process = subprocess.Popen(["quickshell", "-p", shell], env=env, stdin=subprocess.DEVNULL,
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            def ready():
                if shell_process.poll() is not None:
                    raise RuntimeError("The development shell exited")
                try:
                    return ipc("call", "desk", "shortcut_hint") == ""
                except subprocess.CalledProcessError:
                    return False

            wait_for(ready, "development shell", timeout=20)
            niri("action", "focus-window", "--id", window("m")["id"])

            ipc("call", "desk", "display_digit", 1)
            hint = ipc("call", "desk", "shortcut_hint")
            if "External 1" not in hint or not focused_on(first, "m")():
                raise RuntimeError(f"The display digit moved focus or showed no indicator: {hint!r}")
            ipc("call", "desk", "display_digit", 2)
            wait_for(focused_on(second, "t2"), "External 1, space 2")
            steps.append("Super+Alt 1 then 2 showed an indicator, then focused External 1's second pin directly")

            ipc("call", "desk", "focus", 1)
            wait_for(focused_on(first, "m"), "Super+1 from the external display")
            steps.append("Super+1 on External 1 focused Main's first pin")

            ipc("call", "desk", "display_digit", 1)
            time.sleep(2)
            if ipc("call", "desk", "shortcut_hint") != "Display shortcut cancelled":
                raise RuntimeError("A display digit without a space digit was not cancelled")
            ipc("call", "desk", "display_digit", 2)
            if "External 2 is not connected" not in ipc("call", "desk", "shortcut_hint"):
                raise RuntimeError("An unknown display was not reported")
            time.sleep(0.5)
            if not focused_on(first, "m")():
                raise RuntimeError("A cancelled or unknown display shortcut moved focus")
            steps.append("an unfinished sequence timed out; an unknown display was reported; focus stayed")

            ipc("call", "desk", "display_digit", 1)
            ipc("call", "desk", "display_digit", 3)
            wait_for(lambda: "no pinned space 3" in ipc("call", "desk", "spaces_error"), "missing space error")
            if not focused_on(first, "m")():
                raise RuntimeError("A missing pinned space moved focus")
            ipc("call", "desk", "leave")
            steps.append("a missing pinned space was reported without moving focus")
            result = {"result": "pass", "niri": niri("version").strip(), "outputs": [first, second], "steps": steps}
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
        print(f"Niri display shortcut test failed: {exc}", file=sys.stderr)
        sys.exit(1)
