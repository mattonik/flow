#!/usr/bin/env python3
"""The Flow Platform API's sandbox promises (ADR-0009, ADR-0010), kept against a real Flatpak app.

Run inside a Niri session from the Flow checkout under ``dbus-run-session``, with the focused output's
active workspace empty, ``flow-spaces`` on PATH running this checkout's ``session/spaces.py``, and the
``org.freedesktop.Platform//25.08`` runtime installed for the user. ``FLOW_GI_PYTHON`` names a Python with
PyGObject (``GI_TYPELIB_PATH`` set for it), and ``FLOW_SHELL`` the development ``shell.qml`` that System
requests are sent to.

Another app's windows share a column with FlowProbe, stand alone, and one even sets FlowProbe's app ID
without running in its sandbox: FlowProbe must never see their titles or act while they have focus.

The test builds FlowProbe (``tests/flatpak/flow_probe.py``) as the Flatpak app
``io.github.mattonik.FlowProbe``, with only ``--socket=wayland`` and ``--talk-name`` for the API, and
installs it for the user in place of any earlier build, and uninstalls it with its data afterward. FlowProbe opens two GTK windows and makes the
calls the test asks for, through Flatpak's D-Bus proxy, onto this private bus. The service, the
development shell and FlowProbe each get their own ``XDG_RUNTIME_DIR``, so neither a real Flow service
nor the real runtime directory sees them. Flow state is isolated, and real state files are checksummed.
Run one copy at a time: two runs would install and uninstall the same app under each other.
"""

import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid

from isolated_state import IsolatedState, shell_config

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "session"
NAME = "io.github.mattonik.Flow1"
APP = "io.github.mattonik.FlowProbe"
RUNTIME = "org.freedesktop.Platform"
BRANCH = "25.08"
sys.path.insert(0, str(CODE))
import flow_api_core as core  # noqa: E402  (no PyGObject needed)

RATE_WINDOW = core.RATE[1] + 0.5  # wait this long and the rate limit has forgotten every earlier call


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


def run(*command, **kw):
    process = subprocess.run(list(command), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             timeout=kw.pop("timeout", 120), **kw)
    if process.returncode:
        raise RuntimeError(f"{' '.join(command[:3])} failed: {process.stderr.strip()[-500:]}")
    return process.stdout


def build_probe(tmp):
    """Build FlowProbe on the bare runtime as a bundle, to install for the user in place of any earlier build."""
    build = Path(tmp) / "probe-build"
    build.mkdir()
    app = build / "app"
    run("flatpak", "build-init", str(app), APP, RUNTIME, RUNTIME, BRANCH)
    target = app / "files" / "bin" / "flow-probe"
    target.parent.mkdir(parents=True)
    shutil.copy(ROOT / "tests" / "flatpak" / "flow_probe.py", target)
    target.chmod(0o755)
    run("flatpak", "build-finish", str(app), "--command=flow-probe", "--socket=wayland", f"--talk-name={NAME}")
    run("flatpak", "build-export", str(build / "repo"), str(app))
    run("flatpak", "build-bundle", str(build / "repo"), str(build / "probe.flatpak"), APP)
    return build / "probe.flatpak"


class Probe:
    """FlowProbe running in its sandbox; every answer is kept, to search for anything it must not learn."""

    def __init__(self, env, titles):
        self.process = subprocess.Popen(["flatpak", "run", "--user", APP, *titles], env=env, text=True,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.lines, self.transcript = queue.Queue(), []
        threading.Thread(target=self.pump, daemon=True).start()
        self.ready = self.answer(timeout=60)
        if not self.ready.get("ready"):
            raise RuntimeError(f"FlowProbe did not start: {self.ready}")

    def pump(self):
        for line in self.process.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def answer(self, timeout=20):
        try:
            line = self.lines.get(timeout=timeout)
        except queue.Empty:
            raise RuntimeError("FlowProbe did not answer") from None
        if line is None:
            raise RuntimeError("FlowProbe exited")
        self.transcript.append(line)
        return json.loads(line)

    def send(self, **command):
        self.process.stdin.write(json.dumps(command) + "\n")
        self.process.stdin.flush()
        return self.answer()

    def call(self, interface, method, args="()", conn=0):
        return self.send(do="call", conn=conn, interface=interface, method=method, args=args)

    def connect(self):
        return self.send(do="connect")

    def stop(self):
        if self.process.poll() is None:
            try:
                self.process.stdin.close()
                self.process.wait(timeout=15)
            except (OSError, subprocess.TimeoutExpired):
                subprocess.run(["flatpak", "kill", APP], timeout=15)
                self.process.wait(timeout=15)


def refused(answer, kind="NotAuthorized"):
    return answer.get("error") == kind


def main():
    python, shell = os.environ.get("FLOW_GI_PYTHON"), os.environ.get("FLOW_SHELL")
    bus = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    if not python or not shell or "unix:" not in bus:
        raise RuntimeError("Run under dbus-run-session with FLOW_GI_PYTHON and FLOW_SHELL set")
    if subprocess.run(["flatpak", "info", "--user", f"{RUNTIME}//{BRANCH}"], stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL).returncode:
        raise RuntimeError(f"Install the runtime first: flatpak install --user flathub {RUNTIME}//{BRANCH}")
    real_runtime = Path(os.environ["XDG_RUNTIME_DIR"])
    wayland = os.environ.get("WAYLAND_DISPLAY", "wayland-1")
    wayland = wayland if wayland.startswith("/") else str(real_runtime / wayland)
    real_published_before = digest(real_runtime / "flow-activities.json")
    real_state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "flow"
    real_files = [real_state / name for name in ("spaces.json", "groups.json", "session.json")]
    real_digests = [digest(path) for path in real_files]
    workspace = focus()
    if any(w.get("workspace_id") == workspace["id"] for w in read("windows")):
        raise RuntimeError("The focused output's active workspace must be empty")
    token = uuid.uuid4().hex[:8]
    secret = f"secret-{token}"  # in other apps' titles only; FlowProbe must never be told it
    titles = [f"probe-one-{token}", f"probe-two-{token}"]
    others = {s: f"flow-flatpak-{token}-{s}" for s in "ab"}
    others["c"] = APP  # a native window that claims FlowProbe's app ID
    steps, result, processes, probe, installed, overview_opened = [], None, [], None, False, False

    with IsolatedState("flow-flatpak-") as tmp:
        service_runtime, probe_runtime = Path(tmp) / "service-runtime", Path(tmp) / "probe-runtime"
        for directory in (service_runtime, probe_runtime):
            directory.mkdir(mode=0o700)
        published = service_runtime / "flow-activities.json"
        env = dict(os.environ, XDG_STATE_HOME=tmp, PYTHONPATH=str(CODE), FLOW_SHELL_CONFIG=shell,
                   XDG_RUNTIME_DIR=str(service_runtime), WAYLAND_DISPLAY=wayland)
        probe_env = dict(os.environ, XDG_RUNTIME_DIR=str(probe_runtime), WAYLAND_DISPLAY=wayland)

        def start(*command, environment=env, **kw):
            process = subprocess.Popen(list(command), env=environment, text=True, **kw)
            processes.append(process)
            return process

        def flow(*args):
            return json.loads(run(python, str(CODE / "flow_cli.py"), *args, env=env))

        def busctl(*args):
            return subprocess.run(["busctl", f"--address={bus}", *args], text=True, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, timeout=30)

        def pid_of(name):
            answer = busctl("call", "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                            "GetConnectionUnixProcessID", "s", name)
            return int(answer.stdout.split()[1]) if answer.returncode == 0 else None

        def ipc(*args):
            try:
                return subprocess.run(["quickshell", "ipc", "-p", shell, "call", "desk", *args], env=env, text=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10).stdout.strip()
            except subprocess.SubprocessError:
                return ""

        def overview():
            return json.loads(niri("-j", "overview-state"))["is_open"]

        def windows(app):
            return [w for w in read("windows") if w.get("app_id") == app]

        def focused():
            return next((w for w in read("windows") if w.get("is_focused")), None)

        def give_focus(window):
            niri("action", "focus-window", "--id", window["id"])
            wait_for(lambda: (focused() or {}).get("id") == window["id"], f"focus on {window['title']}")

        def activities():
            return json.loads(published.read_text())["activities"] if published.exists() else None

        try:
            bundle = build_probe(tmp)
            installed = True  # from here on, whatever happens, the probe is uninstalled again
            run("flatpak", "install", "--user", "--bundle", "--reinstall", "--no-related", "-y", "--noninteractive",
                str(bundle))
            metadata = run("flatpak", "info", "--user", "--show-metadata", APP)
            if "sockets=wayland;" not in metadata or f"{NAME}=talk" not in metadata or "session-bus" in metadata:
                raise RuntimeError(f"FlowProbe was not installed with only Wayland and the API name: {metadata}")
            steps.append("FlowProbe was built on the bare freedesktop runtime with only --socket=wayland and "
                         f"--talk-name={NAME}, and installed for the user")

            service = start(python, str(CODE / "flow_api.py"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # Asked of the bus driver, which never starts a service: an installed Flow puts an activation
            # file for the same name on every bus, and that service must not answer in this one's place.
            wait_for(lambda: pid_of(NAME) == service.pid, lambda: f"this checkout's service to own the name "
                                                                  f"({pid_of(NAME)})")
            start("quickshell", "-p", shell, environment=dict(env, XDG_CONFIG_HOME=shell_config(tmp)),
                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                  stderr=subprocess.DEVNULL)
            wait_for(lambda: ipc("desk_state") == "closed", "the development shell", timeout=20)

            # The sandbox: the bus names FlowProbe's process, and that process is in a Flatpak sandbox.
            probe = Probe(probe_env, titles)
            mine = wait_for(lambda: len(windows(APP)) == 2 and windows(APP), "FlowProbe's two windows", timeout=30)
            if sorted(w["title"] for w in mine) != titles:
                raise RuntimeError(f"FlowProbe's windows were not its own: {mine}")
            caller_pid = pid_of(probe.ready["name"])
            info = Path(f"/proc/{caller_pid}/root/.flatpak-info")
            if not caller_pid or f"name={APP}" not in info.read_text():
                raise RuntimeError(f"FlowProbe's connection was not from a Flatpak sandbox: pid {caller_pid}")
            comm = Path(f"/proc/{caller_pid}/comm").read_text().strip()
            if comm != "xdg-dbus-proxy":
                raise RuntimeError(f"FlowProbe did not reach the bus through Flatpak's proxy, but through {comm}")
            capabilities = probe.call("Spaces", "GetCapabilities")
            if capabilities.get("ok", [None])[:2] != [1, 2] \
                    or any(f.startswith("control.") for f in capabilities["ok"][2]):
                raise RuntimeError(f"The service did not treat FlowProbe as sandboxed: {capabilities}")
            steps.append(f"the bus named Flatpak's D-Bus proxy ({caller_pid}) as FlowProbe's process, its "
                         ".flatpak-info named FlowProbe's app ID, and the service offered it no control features")

            # Its own spaces only. Another app's window a sits above FlowProbe's second window in its column,
            # b stands alone, and c is a native window that sets FlowProbe's app ID.
            one = next(w for w in mine if w["title"] == titles[0])
            two = next(w for w in mine if w["title"] == titles[1])
            give_focus(two)

            def other(suffix):
                return next(w for w in read("windows") if w.get("title") == f"{secret}-{suffix}")

            for suffix in "abc":
                start("foot", "--app-id", others[suffix], "--title", f"{secret}-{suffix}", "-o", "locked-title=yes",
                      "sleep", "600", environment=os.environ, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL)
                wait_for(lambda: any(w.get("title") == f"{secret}-{suffix}" for w in read("windows")), f"window {suffix}")
                if suffix == "a":
                    give_focus(other("a"))
                    niri("action", "consume-or-expel-window-left")
                    niri("action", "move-window-up")
            spaces = {s["label"]: s for s in flow("spaces", "list")}
            labels = {"one": titles[0], "shared": f"{secret}-a + {titles[1]}", "b": f"{secret}-b", "c": f"{secret}-c"}
            if set(labels.values()) - set(spaces) or spaces[labels["c"]]["apps"] != [APP]:
                raise RuntimeError(f"The layout was not set up: {list(spaces)}")
            spaces = {key: spaces[label] for key, label in labels.items()}
            listed = probe.call("Spaces", "ListSpaces")
            seen = {s["id"]: s for s in listed.get("ok", [[]])[0]}
            expected = {spaces["one"]["id"]: titles[0], spaces["shared"]["id"]: titles[1]}
            if {i: s["label"] for i, s in seen.items()} != expected or any(s["apps"] != [APP] for s in seen.values()):
                raise RuntimeError(f"FlowProbe saw more than its own windows' spaces: {listed}")
            if any(secret in line or others["a"] in line or others["b"] in line for line in probe.transcript):
                raise RuntimeError("FlowProbe learned a title or app ID of another app's windows")
            steps.append("FlowProbe listed exactly its two spaces; in the column it shares with another app it saw "
                         "only its own title and app ID, and a native window with its app ID was not its own")

            # Without focus: Control, System and focusing are all refused, and nothing moves.
            give_focus(other("b"))
            own_id, theirs = spaces["one"]["id"], spaces["b"]["id"]
            calls = [("Spaces", "FocusSpace", f"({theirs!r},)"), ("Spaces", "FocusSpace", f"({own_id!r},)"),
                     ("Control", "ListSpaces", "()"), ("Control", "FocusSpace", f"({theirs!r},)"),
                     ("Control", "PinSpace", f"({own_id!r},)"), ("Control", "UnpinSpace", f"({own_id!r},)"),
                     ("Control", "MoveSpace", f"({own_id!r}, 'left')"), ("System", "ShowDesk", "()"),
                     ("System", "ShowOverview", "()"), ("System", "ShowLauncher", "()")]
            answers = {f"{i}.{m}{a if m == 'FocusSpace' else ''}": probe.call(i, m, a) for i, m, a in calls}
            time.sleep(0.5)
            wrong = {k: v for k, v in answers.items() if not refused(v)}
            if wrong or focused()["id"] != other("b")["id"] or overview() or ipc("desk_state") != "closed" \
                    or ipc("launcher_state") != "closed":
                raise RuntimeError(f"Without focus, FlowProbe was not refused everything, or something moved: {wrong}")
            if any(s["pinned"] for s in flow("spaces", "list") if APP in s["apps"]):
                raise RuntimeError("FlowProbe pinned its own space through Control")
            steps.append("without focus, FlowProbe's FocusSpace (its own and another app's), all five Control "
                         "methods and all three System requests were NotAuthorized, and nothing moved or opened")

            # A native window that sets FlowProbe's app ID has focus: that is not FlowProbe having focus.
            give_focus(other("c"))
            impostor = {"System.ShowDesk": probe.call("System", "ShowDesk"),
                        "Spaces.FocusSpace": probe.call("Spaces", "FocusSpace", f"({own_id!r},)")}
            time.sleep(0.5)
            wrong = {k: v for k, v in impostor.items() if not refused(v)}
            if wrong or focused()["id"] != other("c")["id"] or ipc("desk_state") != "closed":
                raise RuntimeError(f"A native window with FlowProbe's app ID counted as FlowProbe's focus: {wrong}")
            steps.append("while a native window with FlowProbe's app ID had focus, FlowProbe's ShowDesk and "
                         "FocusSpace were NotAuthorized")
            time.sleep(RATE_WINDOW)  # the next phase must not run into the call rate limit

            # With focus: Desk and the overview, and its own spaces; never the launcher or Control.
            give_focus(one)
            launcher = probe.call("System", "ShowLauncher")
            time.sleep(0.5)
            if not refused(launcher) or ipc("launcher_state") != "closed":
                raise RuntimeError(f"FlowProbe opened the launcher: {launcher}")
            control = probe.call("Control", "FocusSpace", f"({theirs!r},)")
            time.sleep(0.5)
            if not refused(control) or focused()["id"] != one["id"]:
                raise RuntimeError(f"FlowProbe used Control while it had focus: {control}")
            shown = probe.call("System", "ShowOverview")
            if "ok" not in shown:
                raise RuntimeError(f"FlowProbe could not open the overview while it had focus: {shown}")
            overview_opened = True
            wait_for(overview, "the overview FlowProbe asked for")
            niri("action", "close-overview")
            wait_for(lambda: not overview(), "the overview to close")
            overview_opened = False
            give_focus(one)
            desk = probe.call("System", "ShowDesk")
            if "ok" not in desk:
                raise RuntimeError(f"FlowProbe could not open Desk while it had focus: {desk}")
            wait_for(lambda: ipc("desk_state") == "open", "Desk, which FlowProbe asked for")
            ipc("leave")
            wait_for(lambda: ipc("desk_state") == "closed", "Desk to close")
            give_focus(one)
            moved = probe.call("Spaces", "FocusSpace", f"({spaces['shared']['id']!r},)")
            if "ok" not in moved:
                raise RuntimeError(f"FlowProbe could not focus its own space while it had focus: {moved}")
            # The other app's window is first in that column; FlowProbe must land on its own window.
            wait_for(lambda: focused()["id"] == two["id"], lambda: f"its own window in the shared column ({focused()})")
            steps.append("with focus, FlowProbe opened the overview and Desk and moved to its own window in the "
                         "shared column, while ShowLauncher and Control.FocusSpace were still NotAuthorized")

            # The call rate limit follows the app across connections: 20 calls in 10 s, wherever they come from.
            time.sleep(RATE_WINDOW)
            extra = [probe.connect(), probe.connect()]
            names = [probe.ready["name"]] + [c["name"] for c in extra]
            if len(set(names)) != 3 or None in map(pid_of, names):
                raise RuntimeError(f"FlowProbe's connections were not three bus connections of their own: {names}")
            lanes = [0] + [c["conn"] for c in extra]
            burst = [probe.call("Spaces", "GetCapabilities", conn=lanes[n % 3]) for n in range(core.RATE[0] + 1)]
            if not all("ok" in a for a in burst[:-1]) or not refused(burst[-1], "RateLimited") \
                    or "Too many requests" not in burst[-1]["message"]:
                raise RuntimeError(f"21 calls over three connections were not limited once: {burst[-3:]}")
            steps.append(f"{core.RATE[0] + 1} calls spread over three connections: only the last was RateLimited")

            # Activities: owned by its verified app ID, three at most across its connections, gone when it exits.
            time.sleep(RATE_WINDOW)  # so a refusal below is the activity limit, not the call rate
            first = probe.call("Activities", "Start", "('Flow shell: update ready', {'progress': <0.25>, "
                                                      "'owner': <'org.gnome.Shell'>, 'app_id': <'org.gnome.Shell'>})")
            second = probe.call("Activities", "Start", "('Second job', @a{sv} {})")
            third = probe.call("Activities", "Start", "('Third job', @a{sv} {})", conn=lanes[1])
            fourth = probe.call("Activities", "Start", "('Fourth job', @a{sv} {})", conn=lanes[2])
            if not all("ok" in a for a in (first, second, third)):
                raise RuntimeError(f"FlowProbe could not start three activities: {first} {second} {third}")
            if not refused(fourth, "RateLimited") or "at most 3 activities" not in fourth["message"]:
                raise RuntimeError(f"A fourth activity on a fourth connection was not refused: {fourth}")
            stolen = probe.call("Activities", "Finish", f"({first['ok'][0]!r},)", conn=lanes[1])
            live = activities()
            if not refused(stolen, "NotFound") or len(live or []) != 3:
                raise RuntimeError(f"One connection finished another's activity: {stolen} {live}")
            if {a["owner"] for a in live} != {APP} or "Flow shell: update ready" not in {a["title"] for a in live}:
                raise RuntimeError(f"FlowProbe's activities did not carry its sandbox's app ID as owner: {live}")
            steps.append("FlowProbe's activities showed its app ID as owner, not the owner it asked for; a fourth "
                         "on another connection was refused, and one connection could not finish another's")

            probe.stop()
            if probe.process.returncode != 0:
                raise RuntimeError(f"FlowProbe did not exit cleanly: {probe.process.returncode}")
            wait_for(lambda: activities() == [], lambda: f"FlowProbe's activities to end with it ({activities()})")
            steps.append("when FlowProbe exited, its three activities ended")
            result = {"result": "pass", "niri": niri("version").strip(),
                      "flatpak": run("flatpak", "--version").strip(), "steps": steps}
        finally:
            if probe:
                probe.stop()
            if overview_opened:
                try:
                    niri("action", "close-overview")
                except (OSError, subprocess.SubprocessError):
                    pass
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
            try:
                for w in read("windows"):
                    if w.get("app_id") in others.values() or w.get("app_id") == APP:
                        niri("action", "close-window", "--id", w["id"])
            except (OSError, subprocess.SubprocessError):
                pass
            if installed:
                subprocess.run(["flatpak", "uninstall", "--user", "--delete-data", "-y", "--noninteractive", APP],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    if digest(real_runtime / "flow-activities.json") != real_published_before:
        raise RuntimeError("The test touched the real runtime directory's activity file")
    if [digest(path) for path in real_files] != real_digests:
        raise RuntimeError("A real Flow Group, pin, or session file changed")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, StopIteration, KeyError, IndexError, TypeError, subprocess.SubprocessError,
            ValueError) as exc:
        print(f"Flatpak API test failed: {exc}", file=sys.stderr)
        sys.exit(1)
