#!/usr/bin/env python3
"""Activities (ADR-0010) from the flow CLI to the island of a development shell, on a private bus.

Run inside a Niri session from the Flow checkout under ``dbus-run-session``, with ``FLOW_GI_PYTHON`` set
to a Python with PyGObject and ``FLOW_SHELL`` to the development ``shell.qml``. The test runs with its own ``XDG_RUNTIME_DIR``
(Wayland and Niri are reached by absolute paths), so a real Flow API service or shell never sees
its activities. It is a child subreaper, so any event stream a stopped shell or service leaks is
re-parented to the test and caught. Flow state is isolated and real state files are checksummed.
"""

import ctypes
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from isolated_state import IsolatedState

CODE = Path(__file__).resolve().parents[1] / "session"
NAME = "io.github.mattonik.Flow1"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def wait_for(test, label, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = test()
        if value:
            return value
        time.sleep(0.2)
    raise RuntimeError(f"Timed out waiting for {label() if callable(label) else label}")


def main():
    python, shell = os.environ.get("FLOW_GI_PYTHON"), os.environ.get("FLOW_SHELL")
    bus = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    if not python or not shell or "unix:" not in bus:
        raise RuntimeError("Run under dbus-run-session with FLOW_GI_PYTHON and FLOW_SHELL set")
    real_runtime = Path(os.environ["XDG_RUNTIME_DIR"])
    wayland = os.environ.get("WAYLAND_DISPLAY", "wayland-1")
    ctypes.CDLL(None).prctl(36, 1, 0, 0, 0)  # PR_SET_CHILD_SUBREAPER: orphans of our children come to us
    real_published_before = (real_runtime / "flow-activities.json").exists()
    real_state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "flow"
    real_files = [real_state / name for name in ("spaces.json", "groups.json", "session.json")]
    real_digests = [digest(path) for path in real_files]
    steps, result, processes = [], None, []
    me = Path("/proc/self/comm").read_text().strip()  # the CLI is run by this test, so it is the owner

    def orphans():
        """Niri event streams and controllers re-parented to this test: a shell or service that leaked one."""
        out = subprocess.run(["ps", "-eo", "pid,ppid,cmd"], text=True, stdout=subprocess.PIPE).stdout
        return {line.split()[0] for line in out.splitlines()[1:]
                if line.split()[1] == str(os.getpid())
                and ("event-stream" in line or "flow_controller" in line)}

    with IsolatedState("flow-activities-") as tmp:
        runtime = Path(tmp) / "runtime"
        runtime.mkdir(mode=0o700)
        published = runtime / "flow-activities.json"
        env = dict(os.environ, XDG_STATE_HOME=tmp, PYTHONPATH=str(CODE), XDG_RUNTIME_DIR=str(runtime),
                   WAYLAND_DISPLAY=wayland if wayland.startswith("/") else str(real_runtime / wayland))

        def start(*command, **kw):
            process = subprocess.Popen(list(command), env=env, text=True, **kw)
            processes.append(process)
            return process

        def flow(*args, **kw):
            return start(python, str(CODE / "flow_cli.py"), *args, **kw)

        def shown():
            try:
                return json.loads(subprocess.run(["quickshell", "ipc", "-p", shell, "call", "desk", "activity_list"],
                                                 env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                                 timeout=10).stdout or "null")
            except (subprocess.SubprocessError, ValueError):
                return None

        def owner():
            """The name's owner, asked of the bus driver, which never starts a service. An installed Flow
            puts an activation file for the same name on every bus; that service must not stand in."""
            answer = subprocess.run(["busctl", f"--address={bus}", "call", "org.freedesktop.DBus", "/org/freedesktop/DBus",
                                     "org.freedesktop.DBus", "GetConnectionUnixProcessID", "s", NAME], text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            return int(answer.stdout.split()[1]) if answer.returncode == 0 else None

        try:
            service = start(python, str(CODE / "flow_api.py"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            wait_for(lambda: owner() == service.pid, lambda: f"this checkout's service to own the name ({owner()})")
            wait_for(lambda: published.exists(), "the service to publish its activity file")
            start("quickshell", "-p", shell, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                  stderr=subprocess.DEVNULL)
            wait_for(lambda: shown() == [], "the development shell with no activities", timeout=20)

            copy = flow("activity", "progress", "Copy photos", stdin=subprocess.PIPE)
            copy.stdin.write("25\n")
            copy.stdin.flush()
            wait_for(lambda: shown() == [f"{me}  ·  Copy photos  25%"], f"the island to show 25% ({shown()})")
            time.sleep(0.6)
            copy.stdin.write("Copying IMG_2041.jpg\n60\n")
            copy.stdin.flush()
            wait_for(lambda: shown() == [f"{me}  ·  Copy photos  60%"], f"the island to show 60% ({shown()})")
            if json.loads(published.read_text())["activities"][0]["detail"] != "Copying IMG_2041.jpg":
                raise RuntimeError("The detail line did not reach the published activity")
            copy.stdin.close()
            copy.wait(timeout=10)
            wait_for(lambda: shown() == [], "the finished copy to leave the island")
            steps.append("flow activity progress showed 25% then 60% with a detail line, and ended at end of input")

            build = flow("activity", "run", "Build", "--", "sleep", "3")
            wait_for(lambda: shown() == [f"{me}  ·  Build  …"], f"an indeterminate build ({shown()})")
            if build.wait(timeout=15) != 0:
                raise RuntimeError("flow activity run did not pass on the command's exit code")
            wait_for(lambda: shown() == [], "the build to end with its command")
            steps.append("flow activity run showed Build while the command ran and ended with it")

            holder = ("import time\nfrom gi.repository import Gio, GLib\n"
                      "bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)\n"
                      "def start(title):\n"
                      f"    return bus.call_sync({NAME!r}, '/io/github/mattonik/Flow1', '{NAME}.Activities', 'Start',"
                      " GLib.Variant('(sa{sv})', (title, {})), GLib.VariantType('(s)'), Gio.DBusCallFlags.NONE, 5000,"
                      " None).unpack()[0]\n"
                      "for n in range(3):\n    start(f'Job {n}')\n"
                      "try:\n    start('Job 4')\n    print('fourth accepted', flush=True)\n"
                      "except GLib.Error as exc:\n    print('RateLimited' in exc.message, flush=True)\n"
                      "time.sleep(60)\n")
            jobs = start(python, "-c", holder, stdout=subprocess.PIPE)
            if jobs.stdout.readline().strip() != "True":
                raise RuntimeError("A fourth activity from one caller was not refused")
            wait_for(lambda: shown() is not None and len(shown()) == 3, f"three jobs in the island ({shown()})")
            jobs.send_signal(signal.SIGKILL)
            jobs.wait(timeout=10)
            wait_for(lambda: shown() == [], "a killed caller's activities to end with its connection")
            steps.append("a caller was refused a fourth activity, and its three ended when it was killed")

            # A service killed outright must not leave its event stream behind either. The first service
            # gives up the name, so the new one owns it and starts its stream before it is killed. Its Flow
            # state is its own, so no shell's controller serves it and it follows Niri itself.
            service.terminate()
            service.wait(timeout=10)

            wait_for(lambda: owner() is None, "the first service to give up its name")
            alone = Path(tmp) / "alone"
            alone.mkdir()
            doomed = subprocess.Popen([python, str(CODE / "flow_api.py")], env=dict(env, XDG_STATE_HOME=str(alone)),
                                      text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            processes.append(doomed)
            wait_for(lambda: owner() == doomed.pid, lambda: f"the second service to own the name ({owner()})")
            wait_for(lambda: "event-stream" in subprocess.run(["ps", "-o", "cmd=", "--ppid", str(doomed.pid)], text=True,
                                                              stdout=subprocess.PIPE).stdout, "its event stream")
            doomed.send_signal(signal.SIGKILL)
            doomed.wait(timeout=10)
            time.sleep(1)
            if orphans():
                raise RuntimeError(f"A killed service left its event stream running: {orphans()}")
            steps.append("a service killed with SIGKILL left no event stream behind")
            steps.append("the service and the development shell stopped without leaving an event stream")
            result = {"result": "pass", "steps": steps}
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
        time.sleep(1)
        if orphans():
            raise RuntimeError(f"The service or shell left its event stream running: {orphans()}")
        if (real_runtime / "flow-activities.json").exists() != real_published_before:
            raise RuntimeError("The test touched the real runtime directory's activity file")
    if [digest(path) for path in real_files] != real_digests:
        raise RuntimeError("A real Flow Group, pin, or session file changed")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, KeyError, subprocess.SubprocessError, ValueError) as exc:
        print(f"Activities test failed: {exc}", file=sys.stderr)
        sys.exit(1)
