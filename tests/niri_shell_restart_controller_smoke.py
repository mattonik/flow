#!/usr/bin/env python3
"""F-018: the shell restarts that the resident controller (ADR-0011) brought, and those of the installed shell.

See *F-018 acceptance* in docs/IMPLEMENTATION_STATUS.md for the checks; each restart below runs all of them.

Development mode (the default) runs inside a Niri session from the Flow checkout, with ``FLOW_SHELL`` naming the
development ``shell.qml`` and ``flow-spaces``, ``flow-recovery`` and ``flow-controller`` on PATH running this
checkout's ``session/``. The focused output's active workspace must be empty. The shell gets its own state,
runtime and configuration folders, with automatic session recovery on, and desktop entries for the test windows
so that automatic saving takes them. A pinned app, a pinned, resized side-by-side Group and an unpinned app are
set up; with a second output connected, a pinned app on it too. Then, in turn:

- a graceful (SIGTERM) and a crash (SIGKILL) restart;
- SIGKILL of the controller alone: the shell starts another;
- SIGKILL of the shell while its controller is mid-refresh, paused (session/pause_point.py) with the new
  ``groups.json`` synced to a temporary file and not yet renamed, and again with ``groups.json`` renamed and the
  new ``spaces.json`` not yet; the refresh records which Group app has focus;
- the same while it is mid-automatic-save, paused before ``session.json`` is renamed;
- five restarts within a few seconds, SIGTERM and SIGKILL alternating, while a sampler checks every 5 ms that no
  two controllers listen on the shell's socket;
- a new shell while the old controller still runs (stopped with SIGSTOP, so its pdeathsig waits): the new
  controller must wait on the old one's lock and take over once it exits, never listening beside it.

``--installed CMD...`` instead restarts the installed shell (the ``flow-shell`` unit, ``/etc/flow/shell.qml``)
with each command in turn, such as ``systemctl --user restart flow-shell`` or a NixOS switch. It works on the real
state, which it backs up first, with the recovery markers, and puts back byte for byte at the end; its Layer is
opened through the installed shell, so it runs in an app scope of its own as the user's apps do.

Prints one JSON line per check and a final ``{"result": ...}``; exits 1 at the first check that fails.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from isolated_state import IsolatedState, started_with

STATE_FILES = ("spaces.json", "groups.json", "session.json")
# What else may be in the state folder. A temporary file of a writer stopped by SIGTERM is not.
KNOWN = {*STATE_FILES, "groups.initialized", "session.lock", "spaces.lock", "layer-shapes.json", "spaces.v1.json"}
INSTALLED_SHELL = "/etc/flow/shell.qml"


class Failed(RuntimeError):
    pass


def niri(*args):
    return subprocess.check_output(["niri", "msg", *map(str, args)], text=True, timeout=10)


def read(kind):
    return json.loads(niri("-j", kind))


def wait_for(test, label, timeout=15, step=0.1):
    end = time.monotonic() + timeout
    while True:
        value = test()
        if value:
            return value
        if time.monotonic() >= end:
            raise Failed(f"Timed out waiting for {label}")
        time.sleep(step)


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:  # a zombie has exited
        return (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def check(name, ok, detail=""):
    """A passing check prints its detail only when it is short; a failing one, always."""
    shown = detail if detail and (not ok or len(detail) < 120) else ""
    print(json.dumps({"check": name, "ok": bool(ok), **({"detail": shown} if shown else {})}), flush=True)
    if not ok:
        raise Failed(f"{name}: {detail}")


def differences(before, after, path=""):
    """Where two JSON values differ, as ``path: before → after`` lines."""
    if isinstance(before, dict) and isinstance(after, dict):
        return [line for key in sorted(set(before) | set(after), key=str)
                for line in differences(before.get(key), after.get(key), f"{path}.{key}")]
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        return [line for i, (x, y) in enumerate(zip(before, after)) for line in differences(x, y, f"{path}[{i}]")]
    return [] if before == after else [f"{path}: {json.dumps(before)} → {json.dumps(after)}"]


def file_differences(before, after):
    lines = []
    for name in STATE_FILES:
        if before[name] != after[name]:
            try:
                lines += [f"{name}{line}" for line in differences(json.loads(before[name] or "null"),
                                                                  json.loads(after[name] or "null"))] or [f"{name}: bytes only"]
            except ValueError:
                lines.append(f"{name}: not JSON: {after[name][:80]!r}")
    return lines


def state_home(env):
    return Path(env.get("XDG_STATE_HOME") or Path(env["HOME"]) / ".local/state")


def controller_socket(env):
    """session/flow_controller.py, socket_path(), for ``env``."""
    home = env.get("XDG_STATE_HOME") or env.get("HOME", "") + "/.local/state"
    return Path(env["XDG_RUNTIME_DIR"]) / f"flow-controller-{hashlib.md5(home.encode()).hexdigest()[:12]}.sock"


def listening(path):
    """How many sockets listen at ``path`` (/proc/net/unix: flag __SO_ACCEPTCON, 00010000)."""
    count = 0
    for line in Path("/proc/net/unix").read_text().splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 8 and fields[-1] == str(path) and fields[3] == "00010000":
            count += 1
    return count


class Bench:
    def __init__(self, shell, env, installed):
        self.shell, self.env, self.installed = shell, env, installed
        self.state = state_home(env) / "flow"
        self.socket = controller_socket(env)
        self.token = uuid.uuid4().hex[:8]
        self.apps = {s: f"flow-restart-{self.token}-{s}" for s in "pabuq"}
        self.layer_app = f"flow-restart-{self.token}-layer"
        self.processes, self.shell_process, self.flag = [], None, None
        self.two = False

    # Reading -------------------------------------------------------------------------------------------------

    def ipc(self, *args):
        return subprocess.run(["quickshell", "ipc", "-p", self.shell, "call", "desk", *map(str, args)], env=self.env,
                              text=True, timeout=10, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              check=True).stdout.strip()

    def view(self):
        return json.loads(self.ipc("spaces_view"))

    def ours(self):
        return [w for w in read("windows") if w.get("app_id") in (*self.apps.values(), self.layer_app)]

    def window(self, suffix):
        app = self.layer_app if suffix == "layer" else self.apps[suffix]
        found = [w for w in read("windows") if w.get("app_id") == app]
        if len(found) != 1:
            raise Failed(f"Window {suffix} is missing or duplicated")
        return found[0]

    def work(self):
        """Everything about the test windows that a shell restart must not change."""
        windows = self.ours()
        workspaces = read("workspaces")
        focused = next(w for w in workspaces if w["is_focused"])
        mine = {w["workspace_id"] for w in windows}
        return {"windows": {w["app_id"].rsplit("-", 1)[1]: (w["id"], w["workspace_id"],
                                                             tuple(w["layout"]["pos_in_scrolling_layout"] or ()),
                                                             round(w["layout"]["tile_size"][0]), w["is_floating"])
                            for w in windows},
                "focused": (focused["output"], focused["active_window_id"]),
                "active": {w["id"]: w["active_window_id"] for w in workspaces if w["id"] in mine}}

    def files(self):
        return {name: (self.state / name).read_bytes() if (self.state / name).exists() else None
                for name in STATE_FILES}

    def stray(self):
        return sorted(p.name for p in self.state.iterdir() if p.name not in KNOWN)

    def controllers(self):
        """The controllers of this shell's state: the installed one's has no XDG_STATE_HOME of its own."""
        found = []
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit():
                continue
            try:
                if b"flow_controller" not in (proc / "cmdline").read_bytes() or not alive(int(proc.name)):
                    continue
                environ = (proc / "environ").read_bytes().split(b"\0")
            except OSError:
                continue
            homes = [e for e in environ if e.startswith(b"XDG_STATE_HOME=")]
            mine = self.env.get("XDG_STATE_HOME")
            if (mine and homes == [f"XDG_STATE_HOME={mine}".encode()]) or (not mine and not homes):
                found.append(int(proc.name))
        return found

    def pins(self):
        """Each pin's shortcut, from spaces.json: (display number, place among its display's pins), and what it
        reaches: the window IDs of its app or Group."""
        data = json.loads((self.state / "spaces.json").read_text())
        found = {}
        for pin in data["pins"]:
            apps = pin.get("members") or [pin.get("app_id")]
            if not all(app in self.apps.values() for app in apps):
                continue  # the real session's own pins, in --installed
            mine = [p for p in data["pins"] if p.get("display") == pin.get("display")]
            number = data["displays"][pin["display"]]["number"]
            found[pin["id"]] = (number, mine.index(pin) + 1, apps)
        return found

    def stats(self):
        """The controller's save counters (session/flow_controller.py, {"get": "stats"})."""
        import socket
        try:
            with socket.socket(socket.AF_UNIX) as connection:
                connection.settimeout(5)
                connection.connect(str(self.socket))
                connection.sendall(b'{"get": "stats"}\n')
                with connection.makefile("rb") as answers:
                    for line in answers:
                        reply = json.loads(line).get("reply")
                        if reply:
                            return {k: reply[k] for k in ("refreshes", "auto_saves", "auto_save_writes", "saves",
                                                          "failures", "busy")}
        except OSError as exc:
            return str(exc)

    def marker(self):
        return {p.name: p.read_text() for p in Path(self.env["XDG_RUNTIME_DIR"]).glob("flow-recovery-*")
                if p.suffix != ".lock"}

    # Checks ---------------------------------------------------------------------------------------------------

    def parse(self, files):
        for name, data in files.items():
            if data is not None:
                try:
                    json.loads(data)
                except ValueError as exc:
                    raise Failed(f"{name} is not valid JSON ({exc}): {data[:80]!r}") from None

    def gone(self, how, base, intended=None):
        """Checks 1, 2 and 5 the moment the old shell is stopped: its controller exits, nothing changed, and a
        file a write was in flight to holds its old bytes or the new ones (``intended``: name → bytes)."""
        wait_for(lambda: not self.controllers(), f"the controller to exit after the {how}", timeout=5)
        files = self.files()
        self.parse(files)
        changed = {name for name in STATE_FILES if files[name] != base["files"][name]}
        allowed = {name for name in changed if intended and files[name] == intended.get(name)}
        check(f"{how}: state files whole and unchanged as the shell's controller exits", changed == allowed,
              "; ".join(file_differences({**base["files"], **{n: files[n] for n in allowed}}, files)))
        check(f"{how}: no temporary file left", not self.stray(), str(self.stray()))
        check(f"{how}: the windows are untouched while no shell runs", self.work()["windows"] == base["work"]["windows"],
              f"{base['work']} → {self.work()}")
        return files

    def settled(self, how, base, timeout=45):
        """Checks 1, 2, 4 and 5 once the new shell has settled: work, files and view as ``base``."""
        def same():
            return (self.work() == base["work"] and self.files() == base["files"] and self.view() == base["view"]
                    and self.ipc("spaces_error") == "" and self.setting() == base["setting"]
                    and len(self.controllers()) == 1 and listening(self.socket) == 1)
        try:
            wait_for(same, f"{how} to settle", timeout=timeout, step=0.25)
        except Failed:
            now = {"work": self.work(), "view": self.view(), "error": self.ipc("spaces_error"),
                   "automatic recovery": json.loads(self.ipc("recovery_state")),
                   "controllers": self.controllers(), "listening": listening(self.socket),
                   "files": file_differences(base["files"], self.files())}
            raise Failed(f"{how}: not the state from before: {now} (before: {base['work']}, {base['view']})") from None
        self.parse(self.files())
        check(f"{how}: same windows, focus, state files, settings and shell view; one controller", True)

    def shortcuts(self, how, base):
        """Check 3: every pin's shortcut reaches it; focus goes back where it was."""
        for number, index, apps in self.pins().values():
            ids = {w["id"] for w in self.ours() if w["app_id"] in apps}
            if number == 0:
                self.ipc("focus", index)
            else:
                self.ipc("display_digit", number)
                self.ipc("display_digit", index)
            wait_for(lambda: next(w for w in read("workspaces") if w["is_focused"])["active_window_id"] in ids,
                     f"{how}: the shortcut of the pin of {apps} (display {number}, space {index})")
        self.focus_back(base)
        check(f"{how}: every pin's shortcut reached it", True, f"{len(self.pins())} pins")

    def focus_back(self, base):
        output, ident = base["work"]["focused"]
        for workspace, active in base["work"]["active"].items():
            if active and active != ident:
                niri("action", "focus-window", "--id", active)
        niri("action", "focus-window", "--id", ident)
        wait_for(lambda: self.work()["focused"] == (output, ident), "focus back where it was")

    def snapshot(self, label):
        """The state everything must come back to; files settled (no write for 3 s)."""
        def quiet():
            first = self.files()
            time.sleep(3)
            return self.files() == first
        wait_for(quiet, f"the state files to settle ({label})", timeout=60, step=0)
        return {"work": self.work(), "files": self.files(), "view": self.view(), "setting": self.setting()}

    def setting(self):
        """Whether the shell has automatic recovery on: its settings file, as a new shell read it."""
        return json.loads(self.ipc("recovery_state"))["setting"]

    def receives(self, how, base):
        """Check 5: the shell gets the controller's updates. Focus another space, see it active, and back."""
        other = self.window("u")["id"]
        niri("action", "focus-window", "--id", other)
        wait_for(lambda: any(s["active"] and s["id"] == other for s in self.view()["spaces"]),
                 f"{how}: the shell to show a focus change")
        self.focus_back(base)
        check(f"{how}: the shell receives the controller's updates", True)

    # The shell ------------------------------------------------------------------------------------------------

    def start(self, pause=None, wait=True):
        env = dict(self.env)
        if pause:
            self.flag = state_home(env) / f"paused-{pause}"
            self.flag.unlink(missing_ok=True)
            env["FLOW_TEST_PAUSE"] = f"{pause}={self.flag}"
        log = open(os.environ["FLOW_RESTART_LOG"], "ab") if os.environ.get("FLOW_RESTART_LOG") else subprocess.DEVNULL
        self.shell_process = subprocess.Popen(["quickshell", "-p", self.shell], env=env, stdin=subprocess.DEVNULL,
                                              stdout=log, stderr=log)
        if log is not subprocess.DEVNULL:
            log.close()
        if wait:
            self.ready()

    def ready(self):
        def up():
            if self.shell_process and self.shell_process.poll() is not None:
                raise Failed("The development shell exited")
            try:
                return self.ipc("toast") == "" and listening(self.socket) == 1 and bool(self.view()["spaces"])
            except subprocess.SubprocessError:
                return False
        wait_for(up, "the shell and its controller", timeout=30, step=0.2)

    def stop(self, sig, wait=True):
        self.shell_process.send_signal(sig)
        if wait:
            self.shell_process.wait(timeout=10)


def setup(bench, spaces):
    """Open the test windows and pin them; the second output gets a pinned window of its own."""
    outputs = [name for name, o in read("outputs").items() if o.get("current_mode") is not None]
    start = next(w for w in read("workspaces") if w["is_focused"])
    if any(w.get("workspace_id") == start["id"] for w in read("windows")):
        raise Failed("The focused output's active workspace must be empty")
    other = None
    if len(outputs) > 1:
        other = next(w for w in read("workspaces") if w["output"] != start["output"] and w["is_active"])
        if any(w.get("workspace_id") == other["id"] for w in read("windows")):
            raise Failed(f"The active workspace of {other['output']} must be empty")
    bench.two = other is not None

    def open_app(suffix):
        bench.processes.append(subprocess.Popen(["foot", "--app-id", bench.apps[suffix]], stdin=subprocess.DEVNULL,
                                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        wait_for(lambda: any(w.get("app_id") == bench.apps[suffix] for w in read("windows")), suffix)

    for suffix in "pabu":
        open_app(suffix)
    for index, suffix in enumerate("pabu", 1):
        niri("action", "focus-window", "--id", bench.window(suffix)["id"])
        niri("action", "move-column-to-index", index)
    niri("action", "focus-window", "--id", bench.window("p")["id"])
    spaces("pin")
    niri("action", "focus-window", "--id", bench.window("a")["id"])
    spaces("stack")
    group = next(s["group_id"] for s in spaces("create-group")["spaces"] if s.get("group_id"))
    spaces("pin-group", group)
    if other:
        niri("action", "focus-monitor", other["output"])
        open_app("q")
        niri("action", "focus-window", "--id", bench.window("q")["id"])
        spaces("pin")
        niri("action", "focus-window", "--id", bench.window("a")["id"])
    else:
        bench.apps.pop("q")
    spaces("group-layout", group)
    spaces("member-wider", str(bench.window("a")["id"]))
    niri("action", "focus-window", "--id", bench.window("b")["id"])
    wait_for(lambda: next(w for w in read("workspaces") if w["is_focused"])["active_window_id"] == bench.window("b")["id"],
             "focus on the second Group app")
    spaces("refresh")
    return group


def last_focused(files, group):
    """Which Group app ``groups.json`` and ``spaces.json`` say had focus last."""
    groups = json.loads(files["groups.json"])["groups"]
    pins = json.loads(files["spaces.json"])["pins"]
    return (next(g.get("last_focused_app") for g in groups if g["id"] == group),
            next(p.get("last_focused_app") for p in pins if p.get("group_id") == group))


def saved_focus(files):
    return next((w["app_id"] for w in json.loads(files["session.json"])["windows"] if w["focused"]), None) \
        if files["session.json"] else None


def mid_write(bench, base, group, point):
    """SIGKILL the shell while its controller is paused at ``point``, a write the focus change asked for."""
    how = f"SIGKILL mid-{'automatic-save' if point == 'session.write' else 'refresh'} ({point})"
    bench.stop(signal.SIGTERM)
    bench.gone(f"restart before {how}", base)
    bench.start(pause=point)
    bench.settled(f"a shell that pauses at {point}", base)
    now = bench.work()["focused"][1]
    target = "a" if now == bench.window("b")["id"] else "b"
    niri("action", "focus-window", "--id", bench.window(target)["id"])
    pid = int(wait_for(lambda: bench.flag.exists() and bench.flag.read_text(), f"the controller to pause at {point}",
                       timeout=20))
    check(f"{how}: the pause is the controller's", pid in bench.controllers(), f"pid {pid}")
    during = bench.files()
    bench.stop(signal.SIGKILL)
    after = bench.gone(how, base, intended=during)
    if point == "groups.write":
        check(f"{how}: neither file was replaced", after == base["files"])
    elif point == "spaces.write":
        check(f"{how}: groups.json has the change, spaces.json not yet",
              last_focused(after, group) == (bench.apps[target], last_focused(base["files"], group)[1]),
              str(last_focused(after, group)))
    else:
        check(f"{how}: groups.json and spaces.json have the change, session.json is the old one",
              last_focused(after, group) == (bench.apps[target],) * 2 and after["session.json"] == base["files"]["session.json"],
              f"{last_focused(after, group)}, saved focus {saved_focus(after)}")
    bench.start()
    try:
        wait_for(lambda: last_focused(bench.files(), group) == (bench.apps[target],) * 2
                 and saved_focus(bench.files()) == bench.apps[target],
                 f"{how}: the new shell to record and save the focus change", timeout=40)
    except Failed as exc:
        raise Failed(f"{exc}: Groups and pins say {last_focused(bench.files(), group)}, the session "
                     f"{saved_focus(bench.files())}, the controller {bench.stats()}, the recovery marker "
                     f"{bench.marker()}") from None
    fresh = bench.snapshot(how)
    wrong = [part for part, same in (
        ("windows", fresh["work"]["windows"] == base["work"]["windows"]),
        ("shell's pins and Layers", (fresh["view"]["pins"], fresh["view"]["layers"]) == (base["view"]["pins"], base["view"]["layers"])),
        ("shell's spaces", spaces_shape(fresh["view"]) == spaces_shape(base["view"])),
        ("setting", fresh["setting"] == base["setting"])) if not same]
    wrong += [line for line in file_differences(base["files"], fresh["files"]) if not focus_only(line)]
    check(f"{how}: the change and nothing else", not wrong, "; ".join(wrong))
    return fresh


def spaces_shape(view):
    """The shell's spaces but for their IDs: a Group's is its focused app's window."""
    return [(s["group_id"], s["group_layout"], s["active"]) for s in view["spaces"]]


def focus_only(line):
    """Whether a difference between two state files is which Group app or window had focus."""
    return any(key in line.split(":")[0] for key in ("last_focused_app", "window_id", "focused"))


def rapid(bench, base, count=5):
    """Restarts within a few seconds: never two controllers listening; one afterwards; nothing lost."""
    samples, stop = [], threading.Event()

    def sampler():
        while not stop.is_set():
            samples.append(listening(bench.socket))
            time.sleep(0.005)

    thread = threading.Thread(target=sampler, daemon=True)
    thread.start()
    old = bench.controllers()
    try:
        for index in range(count):
            bench.stop(signal.SIGKILL if index % 2 else signal.SIGTERM)
            bench.start(wait=False)
            time.sleep(0.4 + 0.25 * index)
        bench.ready()
        bench.settled(f"{count} restarts within a few seconds", base)
    finally:
        stop.set()
        thread.join()
    check(f"{count} restarts: never two controllers listening", max(samples) <= 1,
          f"{len(samples)} samples, at most {max(samples)}")
    check(f"{count} restarts: the old controllers are gone", not set(old) & set(bench.controllers()))


def handover(bench, base, hold=4.0):
    """A new shell while the old controller still runs: it must wait on the old one's lock, not serve beside it.

    Its shell's death stops the old controller at once (pdeathsig), so restarts alone never overlap two. SIGSTOP
    keeps it running past its shell: its TERM waits until SIGCONT, ``hold`` s after the new shell started.
    """
    samples, stop = [], threading.Event()

    def sampler():
        while not stop.is_set():
            samples.append(listening(bench.socket))
            time.sleep(0.005)

    old = bench.controllers()
    thread = threading.Thread(target=sampler, daemon=True)
    thread.start()
    try:
        for pid in old:
            os.kill(pid, signal.SIGSTOP)
        bench.stop(signal.SIGKILL)
        bench.start(wait=False)
        wait_for(lambda: set(bench.controllers()) - set(old), "the new shell's controller", timeout=20)
        time.sleep(hold)
        waiting = sorted(set(bench.controllers()) - set(old))
        for pid in old:
            os.kill(pid, signal.SIGCONT)
        bench.ready()
        bench.settled("a new shell beside a stopped controller", base)
    finally:
        stop.set()
        thread.join()
        for pid in old:
            try:
                os.kill(pid, signal.SIGCONT)
            except ProcessLookupError:
                pass
    check("a new shell beside a stopped controller: never two controllers listening", max(samples) <= 1,
          f"{len(samples)} samples, at most {max(samples)}")
    check("a new shell beside a stopped controller: the new controller took over once the old one exited",
          not set(old) & set(bench.controllers()) and set(bench.controllers()) <= set(waiting),
          f"old {old}, waiting {waiting}, now {bench.controllers()}")


def development(args):
    shell = os.environ.get("FLOW_SHELL")
    if not shell:
        raise Failed("Set FLOW_SHELL to the development shell.qml")
    real_state = state_home(os.environ) / "flow"
    real = {name: (real_state / name).read_bytes() if (real_state / name).exists() else None for name in STATE_FILES}
    with IsolatedState("flow-restart-controller-") as tmp:
        tmp = Path(tmp)
        runtime, config, data = tmp / "runtime", tmp / "config", tmp / "data"
        runtime.mkdir(mode=0o700)
        (config).mkdir()
        # Without appMenuEnabled, the key before it: Qt's Settings then wrote autoRecovery=false back (F-018).
        (config / "flow-settings.ini").write_text("[General]\nautoRecovery=true\n")
        wayland = os.environ.get("WAYLAND_DISPLAY", "wayland-1")
        env = dict(os.environ, XDG_STATE_HOME=str(tmp), XDG_RUNTIME_DIR=str(runtime), XDG_CONFIG_HOME=str(config),
                   XDG_DATA_HOME=str(data),
                   WAYLAND_DISPLAY=wayland if wayland.startswith("/") else str(Path(os.environ["XDG_RUNTIME_DIR"]) / wayland))
        bench = Bench(shell, env, installed=False)
        (data / "applications").mkdir(parents=True)
        for app in (*bench.apps.values(), bench.layer_app):
            (data / "applications" / f"{app}.desktop").write_text(
                f"[Desktop Entry]\nType=Application\nName={app}\nExec=foot --app-id {app}\n")

        def spaces(*call):
            process = subprocess.run(["flow-spaces", *call], env=env, text=True, stdout=subprocess.PIPE, timeout=60)
            value = json.loads(process.stdout)
            if process.returncode or "error" in value:
                raise Failed(f"flow-spaces {call}: {value}")
            return value

        start_focus = next(w for w in read("workspaces") if w["is_focused"])
        try:
            group = setup(bench, spaces)
            bench.start()
            # Automatic recovery starts and saves the session once; that is the state every restart keeps.
            wait_for(lambda: bench.files()["session.json"], "the first automatic save", timeout=45)
            # A Group pin's shortcut records which member it reached (its window_id): that is the shortcut's
            # doing, not a restart's, so the state to keep is the one after a first round of them.
            bench.shortcuts("first start", bench.snapshot("first start"))
            base = bench.snapshot("first start, after the shortcuts")
            check("setup: the shell has automatic recovery on", base["setting"] is True)
            check("setup: pins on " + ("both displays" if bench.two else "one display") + ", a side-by-side Group, "
                  "automatic recovery saved the session", len(bench.pins()) == (3 if bench.two else 2),
                  str(bench.pins()))
            for how, sig in (("graceful restart", signal.SIGTERM), ("crash restart", signal.SIGKILL)):
                bench.stop(sig)
                bench.gone(how, base)
                bench.start()
                bench.settled(how, base)
                bench.shortcuts(how, base)
                bench.settled(f"{how}, after the shortcuts", base)
            if args.only_basic:
                return {"result": "pass", "two_displays": bench.two}

            old = bench.controllers()
            os.kill(old[0], signal.SIGKILL)
            wait_for(lambda: bench.controllers() and bench.controllers() != old and listening(bench.socket) == 1,
                     "the shell to start another controller", timeout=20)
            bench.settled("the controller killed alone", base)
            bench.receives("the controller killed alone", base)
            bench.settled("the controller killed alone, after a focus change", base)

            for point in ("groups.write", "spaces.write", "session.write"):
                base = mid_write(bench, base, group, point)
                bench.shortcuts(f"after a SIGKILL at {point}", base)
                base = bench.snapshot(f"after a SIGKILL at {point}, after the shortcuts")  # see first start

            rapid(bench, base)
            handover(bench, base)
            bench.shortcuts("after the rapid restarts", base)
            bench.receives("after the rapid restarts", base)
            bench.settled("after the rapid restarts and a focus change", base)
            return {"result": "pass", "two_displays": bench.two}
        finally:
            cleanup(bench)
            try:
                niri("action", "focus-monitor", start_focus["output"])
                niri("action", "focus-workspace", start_focus["idx"])
            except (OSError, subprocess.SubprocessError):
                pass
            now = {name: (real_state / name).read_bytes() if (real_state / name).exists() else None for name in STATE_FILES}
            if now != real:
                raise Failed("A real Flow pin, Group or session file changed")


def cleanup(bench):
    if bench.shell_process and bench.shell_process.poll() is None:
        bench.shell_process.terminate()
        try:
            bench.shell_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            bench.shell_process.kill()
    for process in bench.processes:
        if process.poll() is None:
            process.terminate()
    for process in bench.processes:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
    try:
        for w in bench.ours():
            niri("action", "close-window", "--id", w["id"])
    except (OSError, subprocess.SubprocessError):
        pass


def unit_shell():
    pid = int(subprocess.check_output(["systemctl", "--user", "show", "-p", "MainPID", "--value", "flow-shell"],
                                      text=True).strip() or 0)
    return pid if pid and alive(pid) else None


def installed(args):
    env = dict(os.environ)
    env.pop("XDG_STATE_HOME", None)
    if subprocess.run(["systemctl", "--user", "is-active", "--quiet", "flow-shell"]).returncode:
        raise Failed("The installed shell, the flow-shell unit, must be running")
    bench = Bench(INSTALLED_SHELL, env, installed=True)
    runtime = Path(env["XDG_RUNTIME_DIR"])
    applications = Path(env.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "applications"
    backup = Path(tempfile.mkdtemp(prefix="flow-restart-installed-"))
    shutil.copytree(bench.state, backup / "flow", symlinks=True)
    markers = {p: p.read_bytes() for p in runtime.glob("flow-recovery-*") if p.is_file()}
    entries = []
    applications.mkdir(parents=True, exist_ok=True)
    for app in (*bench.apps.values(), bench.layer_app):
        entry = applications / f"{app}.desktop"
        entry.write_text(f"[Desktop Entry]\nType=Application\nName={app}\nExec=foot --app-id {app}\n")
        entries.append(entry)

    def spaces(*call):
        process = subprocess.run(["flow-spaces", *call], env=env, text=True, stdout=subprocess.PIPE, timeout=60)
        value = json.loads(process.stdout)
        if process.returncode or "error" in value:
            raise Failed(f"flow-spaces {call}: {value}")
        return value

    start_focus = next(w for w in read("workspaces") if w["is_focused"])
    restored = False
    try:
        setup(bench, spaces)
        # The Layer is opened by the installed shell, so it runs as its apps do (an app scope of its own).
        niri("action", "focus-window", "--id", bench.window("u")["id"])
        bench.ipc("open_layer", bench.layer_app)
        wait_for(lambda: any(w.get("app_id") == bench.layer_app and w["is_floating"] for w in read("windows")),
                 "the Layer the installed shell opened", timeout=20)
        layer = bench.window("layer")
        unit = subprocess.run(["systemctl", "--user", "status", str(layer["pid"])], text=True,
                              stdout=subprocess.PIPE).stdout.splitlines()[:1]
        check("the installed shell's Layer runs outside the shell's unit", unit and "flow-shell" not in unit[0],
              unit[0] if unit else "no unit")
        niri("action", "focus-window", "--id", bench.window("b")["id"])
        bench.shortcuts("installed shell", bench.snapshot("installed shell"))
        base = bench.snapshot("installed shell, after the shortcuts")
        for command in args.installed:
            how = f"`{command}`"
            shell, old = unit_shell(), bench.controllers()
            samples, stop = [], threading.Event()

            def sampler():
                while not stop.is_set():
                    samples.append(listening(bench.socket))
                    time.sleep(0.005)

            thread = threading.Thread(target=sampler, daemon=True)
            thread.start()
            try:
                subprocess.run(command, shell=True, check=True, timeout=1200)
                wait_for(lambda: unit_shell() not in (None, shell), f"{how}: a new installed shell", timeout=60)
                bench.ready()
                check(f"{how}: the old controller is gone", not set(old) & set(bench.controllers()), str(old))
                bench.settled(how, base, timeout=60)
            finally:
                stop.set()
                thread.join()
            check(f"{how}: never two controllers listening", max(samples) <= 1, f"at most {max(samples)}")
            bench.shortcuts(how, base)
            bench.receives(how, base)
            bench.settled(f"{how}, after the shortcuts", base)
        return {"result": "pass", "two_displays": bench.two}
    finally:
        cleanup(bench)
        for entry in entries:
            entry.unlink(missing_ok=True)
        # The real state as it was: the unit's controller may write while it is put back, so stop it first.
        try:
            niri("action", "focus-monitor", start_focus["output"])
            niri("action", "focus-workspace", start_focus["idx"])
        except (OSError, subprocess.SubprocessError):
            pass
        subprocess.run(["systemctl", "--user", "stop", "flow-shell"], check=False)
        wait_for(lambda: not bench.controllers(), "the installed controller to stop", timeout=20)
        shutil.rmtree(bench.state)
        shutil.copytree(backup / "flow", bench.state, symlinks=True)
        for path, data in markers.items():
            path.write_bytes(data)
        for path in runtime.glob("flow-recovery-*"):
            if path.is_file() and path not in markers:
                path.unlink()
        same = all((backup / "flow" / p.relative_to(bench.state)).read_bytes() == p.read_bytes()
                   for p in bench.state.rglob("*") if p.is_file())
        restored = (same and sorted(p.relative_to(bench.state) for p in bench.state.rglob("*")) ==
                    sorted(p.relative_to(backup / "flow") for p in (backup / "flow").rglob("*"))
                    and {p: p.read_bytes() for p in runtime.glob("flow-recovery-*") if p.is_file()} == markers)
        subprocess.run(["systemctl", "--user", "start", "flow-shell"], check=False)
        print(json.dumps({"check": "the real state and recovery markers put back byte for byte", "ok": restored}),
              flush=True)
        if restored:
            shutil.rmtree(backup)
        else:
            print(f"The real state backup stays in {backup}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--installed", nargs="+", metavar="CMD", help="restart the installed shell with each command")
    parser.add_argument("--only-basic", action="store_true", help="only the graceful and crash restarts")
    args = parser.parse_args()
    result = installed(args) if args.installed else development(args)
    print(json.dumps({**result, "niri": niri("version").strip()}))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, StopIteration, subprocess.SubprocessError, ValueError, KeyError) as exc:
        print(f"Niri shell restart (controller) test failed: {exc}", file=sys.stderr)
        sys.exit(1)
