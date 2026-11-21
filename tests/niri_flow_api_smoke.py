#!/usr/bin/env python3
"""Exercise the Flow Platform API (ADR-0009) with non-Qt clients on a private session bus.

Run inside a Niri session from the Flow checkout under ``dbus-run-session``, with the focused output's
active workspace empty and ``flow-spaces`` on PATH running this checkout's ``session/spaces.py``.
``FLOW_GI_PYTHON`` names a Python with PyGObject (``GI_TYPELIB_PATH`` set for it). The service and the
``flow`` CLI run from this checkout; ``busctl`` is the second, C client. Flow state is isolated, real
state files are checksummed, and disposable Foot windows are closed afterward.
"""

import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import uuid

CODE = Path(__file__).resolve().parents[1] / "session"
NAME = "io.github.mattonik.Flow1"
OBJECT = "/io/github/mattonik/Flow1"


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
    python = os.environ.get("FLOW_GI_PYTHON")
    bus = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    if not python or "unix:" not in bus:
        raise RuntimeError("Run under dbus-run-session with FLOW_GI_PYTHON set")
    workspace = focus()
    real_state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "flow"
    real_files = [real_state / name for name in ("spaces.json", "groups.json", "session.json")]
    real_digests = [digest(path) for path in real_files]
    if any(w.get("workspace_id") == workspace["id"] for w in read("windows")):
        raise RuntimeError("The focused output's active workspace must be empty")
    token = uuid.uuid4().hex[:8]
    apps = {s: f"flow-api-{token}-{s}" for s in "abc"}
    processes, steps, result, service = [], [], None, None

    def window(suffix):
        return next(w for w in read("windows") if w.get("app_id") == apps[suffix])

    with tempfile.TemporaryDirectory(prefix="flow-api-") as tmp:
        env = dict(os.environ, XDG_STATE_HOME=tmp, PYTHONPATH=str(CODE))

        def flow(*args, check=True):
            process = subprocess.run([python, str(CODE / "flow_cli.py"), *args], env=env, text=True,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            if check and process.returncode:
                raise RuntimeError(f"flow {args}: {process.stderr}")
            return process

        def busctl(*args):
            return subprocess.run(["busctl", f"--address={bus}", *args], text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)

        try:
            for suffix in "ab":
                processes.append(subprocess.Popen(["foot", "--app-id", apps[suffix]], stdin=subprocess.DEVNULL,
                                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
                wait_for(lambda: any(w.get("app_id") == apps[suffix] for w in read("windows")), suffix)
            niri("action", "focus-window", "--id", window("a")["id"])
            pinned = subprocess.run(["flow-spaces", "pin"], env=env, text=True, stdout=subprocess.PIPE, timeout=60)
            pin = json.loads(pinned.stdout)["pins"][0]["id"]
            service = subprocess.Popen([python, str(CODE / "flow_api.py")], env=env, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.PIPE, text=True)
            wait_for(lambda: NAME in busctl("list", "--no-pager").stdout, "the API service on the bus")

            version = json.loads(flow("version").stdout)
            if version != {"version": "1.0", "features": ["spaces.list", "spaces.focus", "spaces.changed",
                                                          "control.spaces"]}:
                raise RuntimeError(f"Unexpected capabilities: {version}")
            steps.append(f"capabilities negotiated: {version}")

            listed = json.loads(flow("spaces", "list").stdout)
            ids = {s["label"] if s["pinned"] else s["apps"][0]: s["id"] for s in listed}
            b_id = ids[apps["b"]]
            if ids.get(next(s["label"] for s in listed if s["pinned"])) != f"pin:{pin}" or not b_id.startswith("window:") \
                    or str(window("b")["id"]) == b_id.split(":")[1]:
                raise RuntimeError(f"Spaces were not listed with stable or opaque IDs: {listed}")
            steps.append(f"Control listed {len(listed)} spaces with a pin ID and an opaque window ID")

            focused = busctl("call", NAME, OBJECT, f"{NAME}.Control", "FocusSpace", "s", b_id)
            wait_for(lambda: focus()["active_window_id"] == window("b")["id"], "busctl focusing b")
            if focused.returncode:
                raise RuntimeError(f"busctl FocusSpace failed: {focused.stderr}")
            steps.append("busctl (a C client) focused the second app's space through Control")

            # A command run inside window c acts as app c: it sees only its own space.
            out = Path(tmp) / "mine.json"
            err = Path(tmp) / "denied.txt"
            cli = f"{shlex.quote(python)} {shlex.quote(str(CODE / 'flow_cli.py'))}"
            script = (f"{cli} spaces list --mine > {out}.part && mv {out}.part {out}; "
                      f"{cli} spaces focus {shlex.quote(b_id)} --mine 2> {err}; sleep 60")
            processes.append(subprocess.Popen(["foot", "--app-id", apps["c"], "sh", "-c", script], env=env,
                                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                              stderr=subprocess.DEVNULL))
            mine = json.loads(wait_for(lambda: out.exists() and out.read_text(), "the app's own list", timeout=30))
            if [s["apps"] for s in mine] != [[apps["c"]]]:
                raise RuntimeError(f"An app saw spaces other than its own: {mine}")
            denied = json.loads(wait_for(lambda: err.exists() and err.read_text(), "the refused focus", timeout=30))
            if denied["error"] != "NotAuthorized" or focus()["active_window_id"] != window("c")["id"]:
                raise RuntimeError(f"An app was allowed to focus another app's space: {denied}")
            steps.append("a command in app c saw only c's space, and focusing b's space was NotAuthorized")

            missing = flow("spaces", "focus", "pin:nope", check=False)
            if json.loads(missing.stderr)["error"] != "NotFound":
                raise RuntimeError(f"An unknown space was not reported as NotFound: {missing.stderr}")
            steps.append("an unknown space was reported as NotFound")

            watcher = subprocess.Popen([python, str(CODE / "flow_cli.py"), "events", "watch"], env=env, text=True,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            processes.append(watcher)
            time.sleep(1)
            niri("action", "focus-window", "--id", window("a")["id"])
            line = watcher.stdout.readline()
            if json.loads(line) != {"event": "spaces-changed"}:
                raise RuntimeError(f"No spaces-changed event after a focus change: {line!r}")
            steps.append("flow events watch reported a spaces-changed event after focus moved")

            burst = ("from gi.repository import Gio, GLib\n"
                     "bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)\n"
                     "limited = 0\n"
                     "for _ in range(25):\n"
                     "    try:\n"
                     f"        bus.call_sync({NAME!r}, {OBJECT!r}, {NAME + '.Spaces'!r}, 'GetCapabilities', None, None,"
                     " Gio.DBusCallFlags.NONE, 5000, None)\n"
                     "    except GLib.Error as exc:\n"
                     "        limited += 'RateLimited' in exc.message\n"
                     "print(limited)\n")
            limited = int(subprocess.run([python, "-c", burst], env=env, text=True, stdout=subprocess.PIPE,
                                         timeout=60).stdout)
            if limited != 5:
                raise RuntimeError(f"25 rapid calls on one connection gave {limited} RateLimited replies, not 5")
            steps.append("25 rapid calls on one connection: the last 5 were RateLimited")

            children = subprocess.run(["ps", "-o", "pid=", "--ppid", str(service.pid)], text=True,
                                      stdout=subprocess.PIPE).stdout.split()
            service.terminate()
            service.wait(timeout=10)
            time.sleep(0.5)
            left = [pid for pid in children if Path(f"/proc/{pid}").exists()]
            if not children or left:
                raise RuntimeError(f"Stopping the service left its children running: {left} of {children}")
            steps.append(f"SIGTERM stopped the service and its {len(children)} child process(es)")
            result = {"result": "pass", "niri": niri("version").strip(), "steps": steps}
        finally:
            if service and service.poll() is None:
                service.terminate()
                service.wait(timeout=5)
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
    except (OSError, RuntimeError, StopIteration, KeyError, subprocess.SubprocessError, ValueError) as exc:
        print(f"Flow API test failed: {exc}", file=sys.stderr)
        sys.exit(1)
