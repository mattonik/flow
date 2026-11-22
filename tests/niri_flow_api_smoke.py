#!/usr/bin/env python3
"""Exercise the Flow Platform API (ADR-0009) with non-Qt clients on a private session bus.

Run inside a Niri session from the Flow checkout under ``dbus-run-session``, with the focused output's
active workspace empty and ``flow-spaces`` on PATH running this checkout's ``session/spaces.py``.
``FLOW_GI_PYTHON`` names a Python with PyGObject (``GI_TYPELIB_PATH`` set for it), and ``FLOW_SHELL`` the
development ``shell.qml`` that System requests are sent to. The service and the
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
import time
import uuid

from isolated_state import IsolatedState

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
    raise RuntimeError(f"Timed out waiting for {label() if callable(label) else label}")


def main():
    python = os.environ.get("FLOW_GI_PYTHON")
    shell = os.environ.get("FLOW_SHELL")
    bus = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    if not python or not shell or "unix:" not in bus:
        raise RuntimeError("Run under dbus-run-session with FLOW_GI_PYTHON and FLOW_SHELL set")
    workspace = focus()
    real_state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "flow"
    real_files = [real_state / name for name in ("spaces.json", "groups.json", "session.json")]
    real_digests = [digest(path) for path in real_files]
    if any(w.get("workspace_id") == workspace["id"] for w in read("windows")):
        raise RuntimeError("The focused output's active workspace must be empty")
    token = uuid.uuid4().hex[:8]
    apps = {s: f"flow-api-{token}-{s}" for s in "abc"}
    processes, steps, result, service, shell_process = [], [], None, None, None

    def window(suffix):
        return next(w for w in read("windows") if w.get("app_id") == apps[suffix])

    with IsolatedState("flow-api-") as tmp:
        env = dict(os.environ, XDG_STATE_HOME=tmp, PYTHONPATH=str(CODE), FLOW_SHELL_CONFIG=shell)

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
            # Asked of the bus driver, which never starts a service: an installed Flow puts an activation
            # file for the same name on every bus, and that service must not answer in this one's place.
            def owner():
                answer = busctl("call", "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                "GetConnectionUnixProcessID", "s", NAME)
                return int(answer.stdout.split()[1]) if answer.returncode == 0 else None

            wait_for(lambda: owner() == service.pid, lambda: f"this checkout's API service to own the name ({owner()})")

            version = json.loads(flow("version").stdout)
            if version != {"version": "1.2", "features": ["spaces.list", "spaces.focus", "spaces.changed",
                                                          "control.spaces", "control.pin", "control.reorder",
                                                          "system.ui", "activities"]}:
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
            own = Path(tmp) / "own-focus.txt"
            err = Path(tmp) / "denied.txt"
            trigger, steal = Path(tmp) / "trigger", Path(tmp) / "steal.txt"
            cli = f"{shlex.quote(python)} {shlex.quote(str(CODE / 'flow_cli.py'))}"
            first_id = f"$({shlex.quote(python)} -c 'import json; print(json.load(open(\"{out}\"))[0][\"id\"])')"
            script = (f"{cli} spaces list --mine > {out}.part && mv {out}.part {out}; "
                      f"{cli} spaces focus {first_id} --mine 2> {own}.part; echo $? >> {own}.part; mv {own}.part {own}; "
                      f"{cli} spaces focus {shlex.quote(b_id)} --mine 2> {err}; "
                      f"while [ ! -e {trigger} ]; do sleep 0.2; done; "
                      f"{cli} spaces focus {first_id} --mine 2> {steal}.part; echo \"exit $?\" >> {steal}.part; "
                      f"mv {steal}.part {steal}; sleep 60")
            processes.append(subprocess.Popen(["foot", "--app-id", apps["c"], "sh", "-c", script], env=env,
                                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                              stderr=subprocess.DEVNULL))
            mine = json.loads(wait_for(lambda: out.exists() and out.read_text(), "the app's own list", timeout=30))
            if [s["apps"] for s in mine] != [[apps["c"]]] or any(apps["a"] in s["label"] or apps["b"] in s["label"]
                                                                  for s in mine):
                raise RuntimeError(f"An app saw spaces other than its own: {mine}")
            if wait_for(lambda: own.exists() and own.read_text(), "the app's own focus", timeout=30).strip() != "0":
                raise RuntimeError(f"An app could not focus its own space while it had focus: {own.read_text()}")
            denied = json.loads(wait_for(lambda: err.exists() and err.read_text(), "the refused focus", timeout=30))
            if denied["error"] != "NotAuthorized" or focus()["active_window_id"] != window("c")["id"]:
                raise RuntimeError(f"An app was allowed to focus another app's space: {denied}")
            steps.append("a command in app c saw only c's space, focused it while c had focus, "
                         "and focusing b's space was NotAuthorized")

            # Once another app has focus, c may not raise even its own space over it.
            niri("action", "focus-window", "--id", window("a")["id"])
            wait_for(lambda: focus()["active_window_id"] == window("a")["id"], "focus on a")
            trigger.touch()
            attempt = wait_for(lambda: steal.exists() and steal.read_text(), "the steal attempt", timeout=30)
            time.sleep(0.5)
            if ("NotAuthorized" not in attempt or "exit 0" in attempt
                    or focus()["active_window_id"] != window("a")["id"]):
                raise RuntimeError(f"An app without focus raised its space: {attempt!r}")
            steps.append("with another app focused, c raising its own space was NotAuthorized and focus stayed")

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

            # API 1.1: Control pins, reorders and unpins; System opens shell UI without closing it.
            def listed_now():
                return {(s["apps"] or ["?"])[0]: s for s in json.loads(flow("spaces", "list").stdout)}

            niri("action", "focus-window", "--id", window("a")["id"])
            flow("spaces", "pin", b_id)
            now = listed_now()
            b_pin = now[apps["b"]]["id"]
            if not now[apps["b"]]["pinned"] or not b_pin.startswith("pin:") or focus()["active_window_id"] != window("a")["id"]:
                raise RuntimeError(f"Pinning b through Control failed or kept focus on b: {now}")
            flow("spaces", "move", b_pin, "left")
            if [s["apps"][0] for s in sorted(listed_now().values(), key=lambda s: s["position"]) if s["pinned"]] \
                    != [apps["b"], apps["a"]]:
                raise RuntimeError("Moving b's pin left did not reorder the pins")
            flow("spaces", "unpin", b_pin)
            again = flow("spaces", "unpin", b_pin, check=False)
            if listed_now()[apps["b"]]["pinned"] or json.loads(again.stderr)["error"] != "NotFound":
                raise RuntimeError("Unpinning b failed, or unpinning twice was not NotFound")
            steps.append("Control pinned b with focus kept on a, moved its pin before a, and unpinned it")

            def streams():
                """The service's own Niri event streams: it runs one only while no controller serves it."""
                return [line for line in subprocess.run(["ps", "-o", "cmd=", "--ppid", str(service.pid)], text=True,
                                                        stdout=subprocess.PIPE).stdout.splitlines() if "event-stream" in line]

            if len(streams()) != 1:
                raise RuntimeError(f"Without a shell the service did not follow Niri itself: {streams()}")
            steps.append("without a shell, the service read flow-spaces observe and ran its own event stream")
            shell_process = subprocess.Popen(["quickshell", "-p", shell], env=env, stdin=subprocess.DEVNULL,
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # The shell's controller (ADR-0011) serves this state; the service finds it within 5 s, and its own
            # stream, once stopped, is not started again (that would happen 5 s later).
            wait_for(lambda: not streams(), "the service to leave Niri's stream to the shell's controller", timeout=15)
            time.sleep(6)
            if streams():
                raise RuntimeError(f"The service started its own event stream again beside the controller: {streams()}")
            controller = subprocess.run([sys.executable, "-c", "import flow_controller; print(flow_controller.socket_path())"],
                                        env=env, text=True, stdout=subprocess.PIPE, check=True).stdout.strip()

            def served():
                stats = subprocess.run([sys.executable, "-c", "import flow_controller, json; "
                                        "print(json.dumps(flow_controller.ask({'get': 'stats'})))"],
                                       env=env, text=True, stdout=subprocess.PIPE, check=True).stdout
                return json.loads(stats)["observes"]

            before = served()
            if apps["a"] not in {(s["apps"] or ["?"])[0] for s in json.loads(flow("spaces", "list").stdout)}:
                raise RuntimeError("Listing through the shell's controller lost a space")
            if served() <= before:
                raise RuntimeError(f"The service did not read Flow's state from the controller at {controller}")
            watcher = subprocess.Popen([python, str(CODE / "flow_cli.py"), "events", "watch"], env=env, text=True,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            processes.append(watcher)
            time.sleep(1)
            niri("action", "focus-window", "--id", window("b")["id"])
            line = watcher.stdout.readline()
            if json.loads(line) != {"event": "spaces-changed"}:
                raise RuntimeError(f"No spaces-changed event from the controller after a focus change: {line!r}")
            steps.append("with the development shell running, the service left Niri's stream to the shell's controller, "
                         "listed spaces through it, and reported a spaces-changed event from it")

            def desk():
                try:
                    return subprocess.run(["quickshell", "ipc", "-p", shell, "call", "desk", "desk_state"], text=True,
                                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10).stdout.strip()
                except subprocess.SubprocessError:
                    return ""

            wait_for(lambda: desk() == "closed", "the development shell", timeout=20)
            flow("desk", "show")
            wait_for(lambda: desk() == "open", "Desk opened through System")
            flow("desk", "show")
            time.sleep(0.5)
            if desk() != "open":
                raise RuntimeError("A second ShowDesk closed Desk")
            crossed = flow("overview", "show", check=False)
            time.sleep(0.5)
            if json.loads(crossed.stderr or "{}").get("error") != "InvalidState" or desk() != "open":
                raise RuntimeError(f"ShowOverview with Desk open did not refuse, or closed Desk: {crossed.stderr}")
            subprocess.run(["quickshell", "ipc", "-p", shell, "call", "desk", "leave"], timeout=10)
            wait_for(lambda: desk() == "closed", "Desk closed again")
            steps.append("System ShowDesk opened Desk, asking again left it open, and ShowOverview over Desk "
                         "was InvalidState with Desk still open")

            def launcher():
                return subprocess.run(["quickshell", "ipc", "-p", shell, "call", "desk", "launcher_state"], text=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10).stdout.strip()

            flow("launcher", "show")
            wait_for(lambda: launcher() == "open", "the launcher opened through System")
            flow("launcher", "show")
            time.sleep(0.5)
            if launcher() != "open":
                raise RuntimeError("A second ShowLauncher closed the launcher")
            subprocess.run(["quickshell", "ipc", "-p", shell, "call", "desk", "apps"], timeout=10)
            wait_for(lambda: launcher() == "closed", "the launcher closed again")
            steps.append("System ShowLauncher opened the launcher, and asking again left it open")

            def overview():
                """Flow's Overview (the shell's), which ShowOverview opens."""
                return subprocess.run(["quickshell", "ipc", "-p", shell, "call", "desk", "overview_state"], text=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10).stdout.strip() == "open"

            flow("overview", "show")
            wait_for(overview, "Flow's Overview opened through System")
            flow("overview", "show")
            time.sleep(0.5)
            if not overview():
                raise RuntimeError("A second ShowOverview closed the Overview")
            if desk() != "closed":
                raise RuntimeError("A second ShowOverview went on to Desk")
            subprocess.run(["quickshell", "ipc", "-p", shell, "call", "desk", "overview_close"], timeout=10)
            wait_for(lambda: not overview(), "the Overview closed again")
            steps.append("System ShowOverview opened Flow's Overview, and asking again left it open")

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
            if children or left:
                raise RuntimeError(f"While the shell's controller served it, the service had children: {children} "
                                   f"({left} still running after SIGTERM)")
            steps.append(f"SIGTERM stopped the service and its {len(children)} child process(es) "
                         "(none while the shell's controller served it)")
            result = {"result": "pass", "niri": niri("version").strip(), "steps": steps}
        finally:
            if service and service.poll() is None:
                service.terminate()
                service.wait(timeout=5)
            try:
                niri("action", "close-overview")
            except (OSError, subprocess.SubprocessError):
                pass
            if shell_process and shell_process.poll() is None:
                shell_process.terminate()
                shell_process.wait(timeout=5)
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
