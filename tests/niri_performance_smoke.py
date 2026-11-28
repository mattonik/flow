#!/usr/bin/env python3
"""What a development Flow shell costs at idle and under a stream of Niri events, and how fast its helpers answer.

Run inside a Niri session from the Flow checkout with the installed shell stopped (``systemctl --user stop
flow-shell``: it would react to the same events), ``FLOW_SHELL`` naming the development ``shell.qml`` and the
``flow-spaces`` and ``flow-search`` to measure first on PATH, with ``foot`` and ``nautilus``. The shell gets its own runtime
directory, state and configuration, with the opt-in app menu turned on. The work happens on the focused
display's empty workspace, which must be free: three terminals open there, one of them shaped as a Layer
(``flow-spaces make-layer``), and later more terminals and a Nautilus window; all are closed afterwards.

Every ``flow-spaces``, ``flow-search`` and ``flow-appmenu`` the shell starts goes through a recorder first on its
PATH, which logs the time and arguments and runs the real one (``flow-appmenu`` too, which must be one with
``serve``). The shell runs on a private session bus of the smoke's own, with GSettings in a key file, so the
app-menu reader switches on that bus's accessibility, never the session's. The terminals are on no accessibility
bus, so for the reader they are apps without a menu; so is a Nautilus window the smoke opens on the shell's bus. A refresh is one the shell's resident controller ran
(ADR-0011; it reports them on its socket) or
a ``flow-spaces`` the shell started (before the controller, every refresh was one). Helper timings are medians of
11 runs after 3 that warm the guest up. CPU time comes from /proc: the shell's own, and its helpers': those it
has reaped, its running controller and its app-menu reader. Surface timings are the shell's own (its ``open_took`` IPC call): from
the request to the surface's content, so IPC does not count.

Checked (the limits are the PRs': docs/performance.md):
- idle 60 s: the shell and its helpers each under 1% of a core, at most 3 refreshes and no flow-appmenu run;
- the app menu: one ``flow-appmenu serve`` started for the whole run, so none per focus change; and a probe of an
  app without a menu, as the shell times it from its request to the answer, under 50 ms: the median of the probes
  while Nautilus (GTK 4, on the shell's accessibility bus, no menu bar) opens and focus then moves between it and a
  terminal (on no accessibility bus) every 0.5 s for 5 s;
- a title changing every 0.3 s for 10 s: at most 12 refreshes, the helpers under 10% of a core and the shell
  under 5%;
- a title changing every 0.05 s for 7.5 s: the shell's label for that window never unchanged for 1.5 s, and the
  shell under 10% of a core;
- focus moving every 0.05 s for 6 s: never 1.5 s without a refresh (a maximum wait);
- focus moving every 0.25 s for 6 s: at most 16 refreshes (a minimum interval), the helpers under 2.5% of a core;
- the shell connecting to its controller under 100 ms after the controller listens (it says so on stdout;
  the shell's fallback retries every second);
- ``flow-spaces refresh`` and ``observe`` medians under 30 ms (actions, and the Platform API without a shell,
  still run it); the controller's own refreshes under 10 ms on average; ``flow-search --json`` under 200 ms,
  and it asks flow-spaces only to observe;
- Overview's first frame with every card, each one on screen showing its snapshot (after the snapshot of the
  active space it takes first), under 200 ms, Desk showing a fresh refresh under 150 ms and search showing its
  rows under 300 ms (medians of 5);
- focus moving every 0.25 s across five spaces: the shell under 2.5% of a core (hidden surfaces rebuild nothing);
- an activity without a known amount: while the island or Overview's island shows it, the shell drawing at most
  13 frames a second across all its windows (its dot breathing on a clock stepping every 80 ms is 12.5; something
  animating on every frame is as fast as the render loop goes, 110–130 in the guest), counted from Qt's render-loop log
  (``qt.scenegraph.time.renderloop``, a line or two a frame), the shell's GUI thread (its bindings and JavaScript,
  not the drawing) under 4% of a core, and the clock stepping every 80 ms; on Desk its bar sweeping at 16 ms; the
  clock stops once the activity has a known amount, and with no activity. Frames and the GUI thread, not the whole
  shell's CPU: what drawing a frame costs is the machine's, and it changes with the machine (docs/performance.md,
  2026-11-28).

``FLOW_PERF_IDLE`` shortens the idle phase (seconds) while developing; the limits stay those of 60 s.
Prints one JSON line per check and a final ``{"result": ...}``; exits 1 if any check failed.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import threading
import time

from isolated_state import IsolatedState, started_with

TICK = os.sysconf("SC_CLK_TCK")
# Variables that name a bus directly: never passed to what runs on the smoke's private bus.
BUS_VARIABLES = ("DBUS_SESSION_BUS_ADDRESS", "AT_SPI_BUS_ADDRESS", "DBUS_STARTER_ADDRESS", "DBUS_STARTER_BUS_TYPE")
RECORDER = """#!{bash}
printf '%s %s %s %s\\n' "$EPOCHREALTIME" "$PPID" "$$" "$*" >> {log}
exec {real} "$@"
"""
# The storm terminal waits for "PERIOD COUNT" in its go file, then retitles itself COUNT times, PERIOD s apart.
STORM = r"""d={dir}; while :; do
  if [ -e "$d/go" ]; then read p n < "$d/go"; rm -f "$d/go"; i=1
    while [ "$i" -le "$n" ]; do printf '\033]2;storm %d\007' "$i"; sleep "$p"; i=$((i+1)); done
    echo "$n" > "$d/done"; fi
  sleep 0.1; done"""

FRAME = re.compile(r"\[window (0x[0-9a-f]+)\]\[render thread [^\]]*\] syncAndRender: frame rendered in (\d+)ms, "
                   r"sync=(\d+), render=(\d+), swap=(\d+)")
STARTED = re.compile(r"\[window (0x[0-9a-f]+)\]\[render thread [^\]]*\] syncAndRender: start, elapsed since last call: (\d+) ms")
ANSI = re.compile(r"\x1b\[[0-9;]*m")


class RenderLog:
    """The shell's render-loop log, read as it is written: (arrival, window, kind, numbers) per frame line."""

    def __init__(self, stream):
        self.lines = []
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self.read, args=(stream,), daemon=True)
        self.thread.start()

    def read(self, stream):
        for raw in stream:
            line = ANSI.sub("", raw.decode(errors="replace"))
            now = time.monotonic()
            if (found := FRAME.search(line)):
                entry = (now, found[1], "frame", tuple(int(n) for n in found.groups()[1:]))
            elif (found := STARTED.search(line)):
                entry = (now, found[1], "start", (int(found[2]),))
            else:
                continue
            with self.lock:
                self.lines.append(entry)

    def between(self, start, end):
        with self.lock:
            return [entry for entry in self.lines if start <= entry[0] <= end]


def niri(*args):
    return subprocess.check_output(["niri", "msg", *map(str, args)], text=True, timeout=10)


def read(kind):
    return json.loads(niri("-j", kind))


class NiriSocket:
    """Actions sent on one connection, so a storm of them costs no processes."""

    def __init__(self):
        self.connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.connection.connect(os.environ["NIRI_SOCKET"])
        self.stream = self.connection.makefile("rwb")

    def focus(self, ident):
        self.stream.write(json.dumps({"Action": {"FocusWindow": {"id": ident}}}).encode() + b"\n")
        self.stream.flush()
        if "Ok" not in json.loads(self.stream.readline()):
            raise RuntimeError(f"Niri refused to focus {ident}")

    def close(self):
        self.stream.close()
        self.connection.close()


def controller_socket(runtime, state_home):
    """The controller's socket, named as session/flow_controller.py's socket_path() names it."""
    return runtime / f"flow-controller-{hashlib.md5(str(state_home).encode()).hexdigest()[:12]}.sock"


def controller_stats(runtime, state_home):
    """The development shell's controller's counters (ADR-0011), or None: main's shell has none."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(5)
            connection.connect(str(controller_socket(runtime, state_home)))
            connection.sendall(b'{"get": "stats"}\n')
            with connection.makefile("rb") as answers:
                for line in answers:
                    message = json.loads(line)
                    if "reply" in message:
                        return message["reply"]
    except (OSError, ValueError):
        return None
    return None


def cpu(pid):
    """(own, reaped children's) CPU seconds of a process."""
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return (int(fields[11]) + int(fields[12])) / TICK, (int(fields[13]) + int(fields[14])) / TICK


def main_thread_cpu(pid):
    """CPU seconds of a process's main thread alone: for the shell, Qt's GUI thread, where QML's bindings and
    JavaScript run (the render thread, which draws, is another)."""
    fields = Path(f"/proc/{pid}/task/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return (int(fields[11]) + int(fields[12])) / TICK


def runs(log, start, end, parents=None):
    """(time, pid, arguments) of the runs a recorder logged between two times, optionally only those started by
    one of ``parents``."""
    found = []
    for line in log.read_text().splitlines() if log.exists() else []:
        stamp, parent, pid, args = (line.split(" ", 3) + [""])[:4]
        if start <= float(stamp) <= end and (parents is None or int(parent) in parents):
            found.append((float(stamp), int(pid), args))
    return found


def wait_for(test, label, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = test()
        if value:
            return value
        time.sleep(0.1)
    raise RuntimeError(f"Timed out waiting for {label}")


def timed(command, env, repeat=11, warm_up=3):
    """The median wall time of a command, in ms, and every sample.

    The first runs are not counted: in the UTM guest a run after the system was idle takes several times as
    long, as the virtual CPUs wake up.

    No timeout is passed: Python waits for a child with a timeout by polling, at intervals that double (a 20 ms
    run is seen at about 32 ms). The smoke as a whole runs under one.
    """
    took = []
    for index in range(warm_up + repeat):
        start = time.perf_counter()
        subprocess.run(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        if index >= warm_up:
            took.append(round((time.perf_counter() - start) * 1000))
    return statistics.median(took), took


def main():
    shell = os.environ.get("FLOW_SHELL")
    if not shell or not shutil.which("foot") or not shutil.which("nautilus"):
        raise RuntimeError("Run with FLOW_SHELL set and foot and nautilus on PATH")
    if subprocess.run(["systemctl", "--user", "is-active", "--quiet", "flow-shell"]).returncode == 0:
        raise RuntimeError("Stop the installed shell first: it reacts to the same events")
    real = {name: shutil.which(name) for name in ("flow-spaces", "flow-search", "flow-appmenu")}
    if not all(real.values()) or not shutil.which("dbus-daemon"):
        raise RuntimeError("flow-spaces, flow-search, flow-appmenu and dbus-daemon must be on PATH")
    bash = shutil.which("bash")
    idle_seconds = float(os.environ.get("FLOW_PERF_IDLE", 60))
    start_workspace = next(w for w in read("workspaces") if w["is_focused"])
    output = start_workspace["output"]
    empty = max((w for w in read("workspaces") if w["output"] == output), key=lambda w: w["idx"])
    if any(w["workspace_id"] == empty["id"] for w in read("windows")):
        raise RuntimeError("The focused display has no empty workspace")
    results, terminals, shell_process, sock, bus = [], [], None, None, None

    def check(name, value, limit, ok, unit="", samples=None):
        results.append(ok)
        print(json.dumps({"check": name, "value": round(value, 2) if isinstance(value, float) else value,
                          "limit": limit, "unit": unit, "ok": ok, **({"samples": samples} if samples else {})}), flush=True)

    with IsolatedState("flow-perf-") as tmp:
        tmp = Path(tmp)
        runtime, bin_dir, config, storm_dir = tmp / "runtime", tmp / "bin", tmp / "config", tmp / "storm"
        runtime.mkdir(mode=0o700)
        for folder in (bin_dir, config, storm_dir):
            folder.mkdir()
        (config / "flow-settings.ini").write_text("[General]\nappMenuEnabled=true\nsessionRecovery=false\n")  # automatic recovery is on by default; not this smoke's subject
        logs = {name: tmp / f"{name}.log" for name in ("flow-spaces", "flow-search", "flow-appmenu")}
        for name in ("flow-spaces", "flow-search", "flow-appmenu"):
            (bin_dir / name).write_text(RECORDER.format(bash=bash, log=logs[name], real=real[name]))
        for path in bin_dir.iterdir():
            path.chmod(0o755)
        wayland = os.environ.get("WAYLAND_DISPLAY", "wayland-1")
        real_runtime = Path(os.environ["XDG_RUNTIME_DIR"])
        # The shell's own session bus, whose accessibility bus and switch (in a GSettings key file) the app-menu
        # reader may switch on: never the session's, which at-spi persists in the user's dconf.
        bus_env = {k: v for k, v in os.environ.items() if k not in BUS_VARIABLES}
        bus_env.update(XDG_RUNTIME_DIR=str(runtime), XDG_CONFIG_HOME=str(config), GSETTINGS_BACKEND="keyfile",
                       FLOW_PERF_BUS=str(tmp))
        bus = subprocess.Popen(["dbus-daemon", "--session", "--nofork", "--print-address=1"], env=bus_env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        address = bus.stdout.readline().strip()
        if not address:
            raise RuntimeError("The private session bus did not start")
        env = dict(bus_env, XDG_STATE_HOME=str(tmp), DBUS_SESSION_BUS_ADDRESS=address,
                   WAYLAND_DISPLAY=wayland if wayland.startswith("/") else str(real_runtime / wayland),
                   PATH=f"{bin_dir}:{os.environ['PATH']}")
        app_env = dict(os.environ)  # terminals open in the real session, outside Flow's state
        # Nautilus on the shell's bus, so on its accessibility bus, in folders of its own; a session started before
        # at-spi2-core was enabled would switch GTK's accessibility off through these two.
        files_env = {k: v for k, v in env.items() if k not in ("NO_AT_BRIDGE", "GTK_A11Y", "GI_TYPELIB_PATH", "PYTHONPATH")}
        for name in ("HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
            (tmp / name.lower()).mkdir()
            files_env[name] = str(tmp / name.lower())

        def ipc(*call):
            start = time.perf_counter()
            answer = subprocess.run(["quickshell", "ipc", "-p", shell, "call", "desk", *call], env=env, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10).stdout.strip()
            return answer, (time.perf_counter() - start) * 1000

        def refreshes(start, end):
            """When Flow's state was refreshed: by the shell's controller (ADR-0011) if it has one, and by the
            flow-spaces the shell started itself (main's refreshes; actions and automatic layout passes)."""
            stats = controller_stats(runtime, tmp) or {"recent": []}
            started = [stamp for stamp, _, _ in runs(logs["flow-spaces"], start, end, {pid})]
            return sorted(started + [stamp for stamp in stats["recent"] if start <= stamp <= end])

        def reader():
            """The shell's running app-menu reader (flow-appmenu serve), or None."""
            for child in Path(f"/proc/{pid}/task/{pid}/children").read_text().split():
                try:
                    if b"appmenu" in Path(f"/proc/{child}/cmdline").read_bytes():
                        return int(child)
                except OSError:
                    pass
            return None

        def helpers_cpu():
            """CPU seconds of the shell's helpers: those it reaped, its running controller and app-menu reader."""
            total = cpu(pid)[1]
            stats = controller_stats(runtime, tmp)
            if stats:
                total += sum(cpu(stats["pid"]))
            if reader():
                total += sum(cpu(reader()))
            return total

        def app_menu():
            try:
                return json.loads(ipc("app_menu")[0])
            except ValueError:
                return {}  # a shell without the call: main before the resident reader

        def quiet(seconds=1.5, timeout=20):
            """Wait until nothing has refreshed Flow's state for this long."""
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                if not refreshes(time.time() - seconds, time.time() + 1):
                    return
                time.sleep(0.2)
            raise RuntimeError("The shell did not settle")

        def terminal(title, *command):
            process = subprocess.Popen(["foot", "-T", title, *command], env=app_env,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            terminals.append(process)
            return wait_for(lambda: next((w["id"] for w in read("windows") if w.get("pid") == process.pid), None),
                            f"terminal {title}")

        try:
            niri("action", "focus-workspace", empty["idx"])
            tile_a = terminal("perf tile a", "sh", "-c", "sleep 3600")
            tile_b = terminal("perf tile b", "sh", "-c", "sleep 3600")
            layer = terminal("perf layer", "sh", "-c", "sleep 3600")
            subprocess.run([real["flow-spaces"], "make-layer", str(layer)], env=env, check=True,
                           stdout=subprocess.DEVNULL, timeout=30)
            shell_started = time.time()
            # With Qt's render-loop timing on, so what the shell draws can be counted (a line or two a frame).
            shell_process = subprocess.Popen(["quickshell", "-p", shell], stdin=subprocess.DEVNULL,
                                             env=dict(env, QT_LOGGING_RULES="qt.scenegraph.time.renderloop=true"),
                                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            pid = shell_process.pid
            render_log = RenderLog(shell_process.stdout)
            wait_for(lambda: ipc("desk_state")[0] == "closed", "the development shell")
            observed = json.loads(subprocess.check_output([real["flow-spaces"], "observe"], env=env, text=True))
            if not any(item.get("shaped") for item in observed.get("layers", [])):
                raise RuntimeError(f"The Layer was not shaped: {observed.get('layers')}")
            # The shell watches its controller (its only watcher): how soon after the controller listened. Read
            # once the shell shows spaces, so that this smoke's own request is not taken for the shell's.
            wait_for(lambda: '"spaces":[{' in ipc("spaces_view")[0].replace(" ", ""), "the shell's first state", timeout=10)
            stats = controller_stats(runtime, tmp)
            if stats is not None and "first_watcher_ms" not in stats:
                print(json.dumps({"skipped": "the shell connects as its controller listens",
                                  "why": "a controller from before 2026-11-22 does not count it"}), flush=True)
            else:  # no answer from the controller fails it too
                came = (stats or {}).get("first_watcher_ms")
                check("the shell connects as its controller listens", came, "< 100",
                      came is not None and came < 100, "ms")
            time.sleep(8)  # the app menu's first answers and their retries
            quiet()

            # Idle.
            own, helpers = cpu(pid)[0], helpers_cpu()
            start = time.time()
            time.sleep(idle_seconds)
            end = time.time()
            own_after, helpers_after = cpu(pid)[0], helpers_cpu()
            check("idle: shell CPU", 100 * (own_after - own) / (end - start), "< 1", 100 * (own_after - own) / (end - start) < 1, "% of a core")
            share = 100 * (helpers_after - helpers) / (end - start)
            check("idle: helpers CPU", share, "< 1", share < 1, "% of a core")
            spaces_runs = refreshes(start, end)
            check("idle: refreshes", len(spaces_runs), "<= 3", len(spaces_runs) <= 3)
            menu_runs = runs(logs["flow-appmenu"], start, end)
            check("idle: flow-appmenu runs", len(menu_runs), "0", len(menu_runs) == 0)

            # Helpers, run here as the shell runs them.
            for action in ("refresh", "observe"):
                took, samples = timed([real["flow-spaces"], action], env)
                check(f"flow-spaces {action} median", took, "< 30", took < 30, "ms", samples)
            searched = time.time()
            took, samples = timed(["flow-search", "--json"], env)
            check("flow-search --json median", took, "< 200", took < 200, "ms", samples)
            listings = {pid for _, pid, _ in runs(logs["flow-search"], searched, time.time())}
            asked = sorted({args for _, _, args in runs(logs["flow-spaces"], searched, time.time(), listings)})
            check("flow-search asks flow-spaces", asked, "observe only", asked == ["observe"])
            quiet()

            # Shell surfaces, timed by the shell, with a tiled space active: Overview snapshots it before it shows.
            niri("action", "focus-window", "--id", tile_a)
            time.sleep(2)
            quiet()
            def took(key):
                try:
                    return json.loads(ipc("open_took", key)[0])
                except ValueError:
                    return {"ms": -1, "count": 0}

            def surface(name, key, open_call, close, limit):
                samples = []
                for _ in range(5):
                    before = took(key)["count"]
                    ipc(*open_call)
                    samples.append(wait_for(lambda: (now := took(key))["count"] > before and now, name, timeout=5)["ms"])
                    time.sleep(0.4)
                    close()
                    time.sleep(0.8)
                value = statistics.median(samples)
                check(name, value, f"< {limit}", value < limit, "ms", samples)

            def closed(state_call):
                return lambda: wait_for(lambda: ipc(state_call)[0] == "closed", state_call, timeout=5)

            surface("Overview's first frame", "overview", ["overview_open"], lambda: (ipc("overview_close"), closed("overview_state")()), 200)
            surface("Desk with a fresh answer", "desk", ["desk_open"], lambda: (ipc("leave"), closed("desk_state")()), 150)
            surface("search rows shown", "search", ["search"], lambda: (ipc("search"), closed("search_state")()), 300)
            time.sleep(1)
            quiet()

            # Title storms, from a terminal that retitles itself.
            storm = terminal("storm 0", "sh", "-c", STORM.format(dir=storm_dir))
            time.sleep(1)
            quiet()

            def retitle(period, count):
                (storm_dir / "done").unlink(missing_ok=True)
                (storm_dir / "go").write_text(f"{period} {count}\n")
                wait_for(lambda: not (storm_dir / "go").exists(), "the storm to start", timeout=3)
                return time.time()

            own, children = cpu(pid)[0], helpers_cpu()
            start = retitle(0.3, 33)
            wait_for(lambda: (storm_dir / "done").exists(), "the 0.3 s storm", timeout=30)
            end = time.time()
            time.sleep(0.5)
            own_after, children_after = cpu(pid)[0], helpers_cpu()
            storm_runs = refreshes(start, end + 0.5)
            check("title every 0.3 s: refreshes", len(storm_runs), "<= 12", len(storm_runs) <= 12)
            share = 100 * (children_after - children) / (time.time() - start)
            check("title every 0.3 s: helpers CPU", share, "< 10", share < 10, "% of a core")
            share = 100 * (own_after - own) / (time.time() - start)
            check("title every 0.3 s: shell CPU", share, "< 5", share < 5, "% of a core")
            quiet()

            def label():
                answer = ipc("window_labels")[0]
                try:
                    return dict((ident, text) for ident, text in json.loads(answer)).get(storm)
                except ValueError:
                    return None

            own, _ = cpu(pid)
            start = retitle(0.05, 150)
            seen, changes = label(), [time.time()]
            while not (storm_dir / "done").exists() and time.time() - start < 30:
                now = label()
                if now != seen:
                    seen = now
                    changes.append(time.time())
                time.sleep(0.05)
            end = time.time()
            own_after, _ = cpu(pid)
            final = wait_for(lambda: label() == "storm 150", "the final title", timeout=5) and time.time()
            changes.append(min(end, final))
            gap = max(b - a for a, b in zip(changes, changes[1:]))
            check("title every 0.05 s: longest stale label", gap, "< 1.5", gap < 1.5, "s")
            share = 100 * (own_after - own) / (end - start)
            check("title every 0.05 s: shell CPU", share, "< 10", share < 10, "% of a core")
            subprocess.run(["kill", str(terminals[-1].pid)])
            wait_for(lambda: all(w["id"] != storm for w in read("windows")), "the storm terminal to close")
            time.sleep(1)
            quiet()

            # Focus storms between the two tiles.
            sock = NiriSocket()

            tiles = [tile_a, tile_b]

            def flip(period, seconds):
                begin, step = time.time(), 0
                while time.time() - begin < seconds:
                    step += 1
                    sock.focus(tiles[step % len(tiles)])
                    time.sleep(period)
                return begin, time.time()

            start, end = flip(0.05, 6)
            starts = [start] + refreshes(start, end) + [end]
            gap = max(b - a for a, b in zip(starts, starts[1:]))
            check("focus every 0.05 s: longest gap between refreshes", gap, "< 1.5", gap < 1.5, "s")
            time.sleep(1)
            quiet()
            # Five spaces, so what a state push costs the shell shows (each surface lists every space).
            tiles += [terminal(f"perf tile {name}", "sh", "-c", "sleep 3600") for name in "cde"]
            time.sleep(1)
            quiet()
            helpers, own = helpers_cpu(), cpu(pid)[0]
            start, end = flip(0.25, 6)
            time.sleep(0.5)
            share = 100 * (helpers_cpu() - helpers) / (time.time() - start)
            own_share = 100 * (cpu(pid)[0] - own) / (time.time() - start)
            check("focus every 0.25 s: shell CPU", own_share, "< 2.5", own_share < 2.5, "% of a core")
            count = len(refreshes(start, end))
            check("focus every 0.25 s: refreshes", count, "<= 16", count <= 16)
            check("focus every 0.25 s: helpers CPU", share, "< 2.5", share < 2.5, "% of a core")
            stats = controller_stats(runtime, tmp)
            if stats and stats["refreshes"]:
                mean = stats["refresh_ms"] / stats["refreshes"]
                check("controller refresh mean", mean, "< 10", mean < 10, "ms")

            # The app menu: its probes, while focus moves slowly enough for each to settle, between a terminal (on no
            # accessibility bus) and Nautilus (GTK 4: on the shell's accessibility bus, with no menu bar), from
            # before Nautilus opens, so its first probe, which chooses its frame and looks for a menu bar, counts.
            time.sleep(1)
            before = app_menu().get("probes", 0)
            files = subprocess.Popen(["nautilus", "--new-window", str(tmp)], env=files_env, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            terminals.append(files)
            browser = wait_for(lambda: next((w["id"] for w in read("windows") if w.get("pid") == files.pid), None),
                               "Nautilus", timeout=30)
            time.sleep(2)
            tiles[:] = [tile_a, browser]
            flip(0.5, 5)
            time.sleep(0.6)
            state = app_menu()
            fresh = min(state.get("probes", 0) - before, 20)
            probes = state.get("probe_ms", [])[-fresh:] if fresh > 0 else []
            took = statistics.median(probes) if probes else -1
            check("app menu: probe of an app without a menu (a terminal, Nautilus), median", took, "< 50",
                  0 <= took < 50, "ms", probes)
            started = runs(logs["flow-appmenu"], shell_started, time.time())
            check("app menu: flow-appmenu processes started", len(started), "1 (serve)",
                  [args for _, _, args in started] == ["serve"])
            # Closed again, so the phases after this one measure what they did before it was added (Overview's
            # cost grows with its cards).
            niri("action", "close-window", "--id", browser)
            wait_for(lambda: all(w["id"] != browser for w in read("windows")), "Nautilus to close")
            files.terminate()
            time.sleep(1)
            quiet()

            # An activity without a known amount (ADR-0010): the shell reads what the Platform API publishes.
            def publish(activities):
                staged = runtime / "flow-activities.json.tmp"
                staged.write_text(json.dumps({"version": 1, "activities": activities}))
                os.replace(staged, runtime / "flow-activities.json")

            def drawn(seconds):
                """Over ``seconds``: frames a second the shell drew across all its windows, how many windows drew,
                and its GUI thread's CPU (% of a core)."""
                begin, gui = time.monotonic(), main_thread_cpu(pid)
                time.sleep(seconds)
                end = time.monotonic()
                gui = 100 * (main_thread_cpu(pid) - gui) / (end - begin)
                frames = [entry for entry in render_log.between(begin, end) if entry[2] == "frame"]
                return len(frames) / (end - begin), len({entry[1] for entry in frames}), gui

            def clock():
                try:
                    return json.loads(ipc("busy_clock")[0])
                except ValueError:
                    return None  # a shell without the call: main before 2026-11-22

            def ticking(seconds=1.0):
                """The busy clock's interval if its phase moved over ``seconds``, else None."""
                before = clock()
                time.sleep(seconds)
                after = clock()
                return after["interval"] if before and after and after["running"] and after["phase"] != before["phase"] else None

            busy = [{"id": "busy", "owner": "Smoke", "title": "Working", "progress": -1}]
            publish(busy)
            wait_for(lambda: "Working" in ipc("activity_list")[0], "the busy activity in the island")
            ipc("island")  # nothing rests over apps (ADR-0014): open the Island, as Super+B does
            wait_for(lambda: json.loads(ipc("island_state")[0] or "{}").get("open"), "the Island open")
            time.sleep(1)
            rate, windows, gui = drawn(10)
            # None drawn is the log not read (a basic render loop logs another format), not a good result.
            check("busy activity, island showing: frames drawn", rate, "0 < frames <= 13", 0 < rate <= 13, f"a second, by {windows} window(s)")
            check("busy activity, island showing: the shell's GUI thread CPU", gui, "< 4", gui < 4, "% of a core")
            moved = ticking()
            check("busy activity, island showing: the dot breathes", moved, "every 80 ms", moved == 80)
            ipc("overview_open")
            wait_for(lambda: ipc("overview_state")[0] == "open", "Overview, with its own island")
            time.sleep(1)
            rate, windows, gui = drawn(10)
            # None drawn is the log not read (a basic render loop logs another format), not a good result.
            check("busy activity in Overview: frames drawn", rate, "0 < frames <= 13", 0 < rate <= 13, f"a second, by {windows} window(s)")
            check("busy activity in Overview: the shell's GUI thread CPU", gui, "< 4", gui < 4, "% of a core")
            moved = ticking()
            check("busy activity in Overview: the dot breathes", moved, "every 80 ms", moved == 80)
            ipc("overview_close")
            wait_for(lambda: ipc("overview_state")[0] == "closed", "Overview to close")
            ipc("desk_open")
            wait_for(lambda: ipc("desk_state")[0] == "open", "Desk, with the activity's sweeping bar")
            moved = ticking()
            check("busy activity on Desk: the bar sweeps", moved, "every 16 ms", moved == 16)
            ipc("leave")
            wait_for(lambda: ipc("desk_state")[0] == "closed", "Desk to close")
            publish([{**busy[0], "progress": 0.5}])
            time.sleep(1)
            stopped = clock()
            check("an activity with a known amount: the clock stops", stopped and stopped["running"], "stopped",
                  stopped is not None and not stopped["running"])
            publish([])
            time.sleep(1)
            stopped = clock()
            check("no activity: the clock stops", stopped and stopped["running"], "stopped",
                  stopped is not None and not stopped["running"])
        finally:
            if sock:
                sock.close()
            if shell_process:
                shell_process.terminate()
                try:
                    shell_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    shell_process.kill()
            for process in terminals:
                process.terminate()
            for process in terminals:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
            if bus:
                bus.terminate()
                bus.wait(timeout=10)
                time.sleep(0.5)
                for leftover in started_with(f"FLOW_PERF_BUS={tmp}"):  # its accessibility bus
                    os.kill(leftover, signal.SIGTERM)
            niri("action", "focus-workspace", start_workspace["idx"])
    passed = all(results)
    print(json.dumps({"result": "pass" if passed else "fail"}))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
