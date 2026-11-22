#!/usr/bin/env python3
"""What a development Flow shell costs at idle and under a stream of Niri events, and how fast its helpers answer.

Run inside a Niri session from the Flow checkout with the installed shell stopped (``systemctl --user stop
flow-shell``: it would react to the same events), ``FLOW_SHELL`` naming the development ``shell.qml`` and the
``flow-spaces`` and ``flow-search`` to measure first on PATH, with ``foot``. The shell gets its own runtime
directory, state and configuration, with the opt-in app menu turned on. The work happens on the focused
display's empty workspace, which must be free: three terminals open there, one of them shaped as a Layer
(``flow-spaces make-layer``), and are closed afterwards.

Every ``flow-spaces``, ``flow-search`` and ``flow-appmenu`` the shell starts goes through a recorder first on its
PATH, which logs the time and arguments and runs the real one. ``flow-appmenu`` is a stand-in that answers
``null``, as the real one does while the focused workspace has no window: the shell, holding no menu, must not
keep asking. Helper timings are medians of 11 runs after 3 that warm the guest up. CPU time comes from /proc:
the shell's own, and its children's once it has reaped them. Surface timings are the shell's own (its
``open_took`` IPC call): from the request to the surface's content, so IPC does not count.

Checked (the limits are the PR's: docs/performance.md):
- idle 60 s: the shell under 1% of a core, at most 3 flow-spaces and at most 3 flow-appmenu runs;
- a title changing every 0.3 s for 10 s: at most 12 flow-spaces runs, the children under 10% of a core and the
  shell under 5%;
- a title changing every 0.05 s for 7.5 s: the shell's label for that window never unchanged for 1.5 s, and the
  shell under 10% of a core;
- focus moving every 0.05 s for 6 s: never 1.5 s without a refresh (a maximum wait);
- focus moving every 0.25 s for 6 s: at most 16 refreshes (a minimum interval);
- ``flow-spaces refresh`` and ``observe`` medians under 30 ms; ``flow-search --json`` under 200 ms, and it
  asks flow-spaces only to observe;
- Overview shown (after the snapshot of the active space it takes first) under 200 ms, Desk showing a fresh
  flow-spaces answer under 150 ms and search showing its rows under 300 ms (medians of 5).

``FLOW_PERF_IDLE`` shortens the idle phase (seconds) while developing; the limits stay those of 60 s.
Prints one JSON line per check and a final ``{"result": ...}``; exits 1 if any check failed.
"""

import json
import os
from pathlib import Path
import shutil
import socket
import statistics
import subprocess
import sys
import time

from isolated_state import IsolatedState

TICK = os.sysconf("SC_CLK_TCK")
RECORDER = """#!{bash}
printf '%s %s %s %s\\n' "$EPOCHREALTIME" "$PPID" "$$" "$*" >> {log}
exec {real} "$@"
"""
APPMENU = """#!{bash}
printf '%s %s %s %s\\n' "$EPOCHREALTIME" "$PPID" "$$" "$*" >> {log}
echo null
"""
# The storm terminal waits for "PERIOD COUNT" in its go file, then retitles itself COUNT times, PERIOD s apart.
STORM = r"""d={dir}; while :; do
  if [ -e "$d/go" ]; then read p n < "$d/go"; rm -f "$d/go"; i=1
    while [ "$i" -le "$n" ]; do printf '\033]2;storm %d\007' "$i"; sleep "$p"; i=$((i+1)); done
    echo "$n" > "$d/done"; fi
  sleep 0.1; done"""


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


def cpu(pid):
    """(own, reaped children's) CPU seconds of a process."""
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return (int(fields[11]) + int(fields[12])) / TICK, (int(fields[13]) + int(fields[14])) / TICK


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
    if not shell or not shutil.which("foot"):
        raise RuntimeError("Run with FLOW_SHELL set and foot on PATH")
    if subprocess.run(["systemctl", "--user", "is-active", "--quiet", "flow-shell"]).returncode == 0:
        raise RuntimeError("Stop the installed shell first: it reacts to the same events")
    real = {name: shutil.which(name) for name in ("flow-spaces", "flow-search")}
    if not all(real.values()):
        raise RuntimeError("flow-spaces and flow-search must be on PATH")
    bash = shutil.which("bash")
    idle_seconds = float(os.environ.get("FLOW_PERF_IDLE", 60))
    start_workspace = next(w for w in read("workspaces") if w["is_focused"])
    output = start_workspace["output"]
    empty = max((w for w in read("workspaces") if w["output"] == output), key=lambda w: w["idx"])
    if any(w["workspace_id"] == empty["id"] for w in read("windows")):
        raise RuntimeError("The focused display has no empty workspace")
    results, terminals, shell_process, sock = [], [], None, None

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
        (config / "flow-settings.ini").write_text("[General]\nappMenuEnabled=true\n")
        logs = {name: tmp / f"{name}.log" for name in ("flow-spaces", "flow-search", "flow-appmenu")}
        for name in ("flow-spaces", "flow-search"):
            (bin_dir / name).write_text(RECORDER.format(bash=bash, log=logs[name], real=real[name]))
        (bin_dir / "flow-appmenu").write_text(APPMENU.format(bash=bash, log=logs["flow-appmenu"]))
        for path in bin_dir.iterdir():
            path.chmod(0o755)
        wayland = os.environ.get("WAYLAND_DISPLAY", "wayland-1")
        real_runtime = Path(os.environ["XDG_RUNTIME_DIR"])
        env = dict(os.environ, XDG_STATE_HOME=str(tmp), XDG_CONFIG_HOME=str(config), XDG_RUNTIME_DIR=str(runtime),
                   WAYLAND_DISPLAY=wayland if wayland.startswith("/") else str(real_runtime / wayland),
                   PATH=f"{bin_dir}:{os.environ['PATH']}")
        app_env = dict(os.environ)  # terminals open in the real session, outside Flow's state

        def ipc(*call):
            start = time.perf_counter()
            answer = subprocess.run(["quickshell", "ipc", "-p", shell, "call", "desk", *call], env=env, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10).stdout.strip()
            return answer, (time.perf_counter() - start) * 1000

        def quiet(seconds=1.5, timeout=20):
            """Wait until the shell has started no flow-spaces for this long."""
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                if not runs(logs["flow-spaces"], time.time() - seconds, time.time() + 1):
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
            shell_process = subprocess.Popen(["quickshell", "-p", shell], env=env, stdin=subprocess.DEVNULL,
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            pid = shell_process.pid
            wait_for(lambda: ipc("desk_state")[0] == "closed", "the development shell")
            observed = json.loads(subprocess.check_output([real["flow-spaces"], "observe"], env=env, text=True))
            if not any(item.get("shaped") for item in observed.get("layers", [])):
                raise RuntimeError(f"The Layer was not shaped: {observed.get('layers')}")
            time.sleep(8)  # the app menu's first answers and their retries
            quiet()

            # Idle.
            own, _ = cpu(pid)
            start = time.time()
            time.sleep(idle_seconds)
            end = time.time()
            own_after, _ = cpu(pid)
            check("idle: shell CPU", 100 * (own_after - own) / (end - start), "< 1", 100 * (own_after - own) / (end - start) < 1, "% of a core")
            spaces_runs = runs(logs["flow-spaces"], start, end)
            check("idle: flow-spaces runs", len(spaces_runs), "<= 3", len(spaces_runs) <= 3)
            menu_runs = runs(logs["flow-appmenu"], start, end)
            check("idle: flow-appmenu runs", len(menu_runs), "<= 3", len(menu_runs) <= 3)

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

            surface("Overview shown", "overview", ["overview_open"], lambda: (ipc("overview_close"), closed("overview_state")()), 200)
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

            own, children = cpu(pid)
            start = retitle(0.3, 33)
            wait_for(lambda: (storm_dir / "done").exists(), "the 0.3 s storm", timeout=30)
            end = time.time()
            time.sleep(0.5)
            own_after, children_after = cpu(pid)
            storm_runs = runs(logs["flow-spaces"], start, end + 0.5)
            check("title every 0.3 s: flow-spaces runs", len(storm_runs), "<= 12", len(storm_runs) <= 12)
            share = 100 * (children_after - children) / (time.time() - start)
            check("title every 0.3 s: children CPU", share, "< 10", share < 10, "% of a core")
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

            def flip(period, seconds):
                begin = time.time()
                ident = tile_a
                while time.time() - begin < seconds:
                    ident = tile_b if ident == tile_a else tile_a
                    sock.focus(ident)
                    time.sleep(period)
                return begin, time.time()

            start, end = flip(0.05, 6)
            starts = [start] + [stamp for stamp, _, _ in runs(logs["flow-spaces"], start, end)] + [end]
            gap = max(b - a for a, b in zip(starts, starts[1:]))
            check("focus every 0.05 s: longest gap between refreshes", gap, "< 1.5", gap < 1.5, "s")
            time.sleep(1)
            quiet()
            start, end = flip(0.25, 6)
            count = len(runs(logs["flow-spaces"], start, end))
            check("focus every 0.25 s: flow-spaces runs", count, "<= 16", count <= 16)
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
            niri("action", "focus-workspace", start_workspace["idx"])
    passed = all(results)
    print(json.dumps({"result": "pass" if passed else "fail"}))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
