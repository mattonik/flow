#!/usr/bin/env python3
"""What a development Flow shell costs with many windows, how fast it starts, and how smoothly its surfaces open.

Phase 3 of the performance plan (docs/performance.md): the measurements meant for real hardware. It runs the same
in the UTM guest and on a laptop, and reports what it measured with the display it ran on, so runs on different
machines can be put side by side.

Run inside a Niri session from the Flow checkout, as tests/niri_performance_smoke.py is run: the installed shell
stopped, ``FLOW_SHELL`` naming the development ``shell.qml``, ``flow-spaces`` and ``foot`` on PATH, and an empty
workspace on the focused display. The shell gets its own runtime directory, state and configuration. Terminals
open on the empty workspace as spaces, each showing a screenful of text, and are closed afterwards.

The shell runs with Qt's render-loop timing on (``qt.scenegraph.time.renderloop``): for every frame of every
shell window Qt logs how long the frame took to sync, render and swap, and the time since the window's previous
frame. The probe reads that log as the shell writes it.

Measured, for each count of spaces in ``FLOW_PROBE_SPACES`` (default ``5 24``):
- startup (three launches): from starting the shell to its IPC answering, to its first frame, and to its
  surfaces holding every space; its memory once settled;
- idle, 20 s: frames the shell drew (none, but for a clock's minute) and wakeups per second of the shell, its controller
  and Niri, a stand-in for battery use where there is no battery;
- focus moving every 0.25 s across every space: the shell's and its controller's CPU, the controller's refresh
  time and the size of the state it sends the shell;
- Overview, Desk and search opening (five times each, timed by the shell's ``open_took``), and what the shell
  drew in the first 0.8 s of each opening and each closing, across all its windows: how many windows it built
  afresh, the frames, the render threads' work (sync and render, not the swap), the longest frame, and for the window that drew the most,
  the longest gap between two frames and how many gaps were longer than one and a half refresh periods;
- the snapshot Overview takes before it shows, as the shell takes it (``grim`` at the display's resolution), and
  scaled by ``grim`` to 640 px wide as the shell took it before, with the size of each file.

Checked against the budgets of docs/performance.md where there is one (Overview under 200 ms, Desk under 150 ms,
search under 300 ms, at most two frames at idle); the rest is reported. Prints one JSON line per measurement and a final
``{"result": ...}``; exits 1 if a check failed.
"""

import json
import os
from pathlib import Path
import re
import shutil
import socket
import statistics
import subprocess
import sys
import threading
import time

from isolated_state import IsolatedState
from niri_performance_smoke import NiriSocket, controller_socket, controller_stats, cpu, niri, read, wait_for

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


def frames_of(entries, period, known):
    """What one opening or closing drew, across every shell window: how many windows were new (Quickshell builds a
    layer-shell window afresh each time it is shown; a lower bound, as a new window can take a freed one's
    address), the frames drawn, the render threads' work over them (ms: syncing and rendering, not the swap,
    which mostly waits for the display), the longest such frame, and, for the window that drew the most, the
    longest gap between two of its frames and how many such gaps exceeded 1.5 refresh periods (waits, such as
    for its first frame, included). ``known`` collects the windows seen so far."""
    by_window = {}
    for _, window, kind, numbers in entries:
        by_window.setdefault(window, []).append((kind, numbers))
    new = [window for window in by_window if window not in known]
    known.update(by_window)
    frames = [numbers for lines in by_window.values() for kind, numbers in lines if kind == "frame"]
    result = {"new_windows": len(new), "frames": len(frames), "render_work": sum(f[1] + f[2] for f in frames),
              "longest_frame": max((f[1] + f[2] for f in frames), default=0), "longest_gap": 0, "late": 0}
    if by_window:
        lines = max(by_window.values(), key=lambda lines: sum(kind == "frame" for kind, _ in lines))
        # The first start's gap is since the window's last frame before: not part of this.
        gaps = [numbers[0] for kind, numbers in lines if kind == "start"][1:]
        result.update(longest_gap=max(gaps, default=0), late=sum(gap > 1.5 * period for gap in gaps))
    return result


def threads_switches(pid):
    """Context switches (voluntary and not) of every thread of a process: each one a wakeup or a preemption."""
    total = 0
    for task in Path(f"/proc/{pid}/task").iterdir():
        try:
            for line in (task / "status").read_text().splitlines():
                if line.startswith(("voluntary_ctxt_switches", "nonvoluntary_ctxt_switches")):
                    total += int(line.split()[1])
        except OSError:
            pass
    return total


def rss_mb(pid):
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return round(int(line.split()[1]) / 1024, 1)
    return 0.0


def state_bytes(runtime, state_home):
    """The size of the state the controller sends a watching shell, or 0 without a controller."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(5)
            connection.connect(str(controller_socket(runtime, state_home)))
            connection.sendall(b'{"watch": true}\n')
            with connection.makefile("rb") as answers:
                for line in answers:
                    if line.startswith(b'{"state"'):
                        return len(line)
    except OSError:
        pass
    return 0


def main():
    shell = os.environ.get("FLOW_SHELL")
    if not shell or not shutil.which("foot") or not shutil.which("flow-spaces") or not shutil.which("grim"):
        raise RuntimeError("Run with FLOW_SHELL set and foot, grim and flow-spaces on PATH")
    if subprocess.run(["systemctl", "--user", "is-active", "--quiet", "flow-shell"]).returncode == 0:
        raise RuntimeError("Stop the installed shell first: it reacts to the same events")
    counts = [int(n) for n in os.environ.get("FLOW_PROBE_SPACES", "5 24").split()]
    start_workspace = next(w for w in read("workspaces") if w["is_focused"])
    output_name = start_workspace["output"]
    empty = max((w for w in read("workspaces") if w["output"] == output_name), key=lambda w: w["idx"])
    if any(w["workspace_id"] == empty["id"] for w in read("windows")):
        raise RuntimeError("The focused display has no empty workspace")
    output = read("outputs")[output_name]
    mode = output["modes"][output["current_mode"]]
    period = 1000 / (mode["refresh_rate"] / 1000)
    logical = output["logical"]
    niri_pid = int(subprocess.check_output(["pgrep", "-o", "-x", "niri"], text=True))
    results, terminals, shell_process = [], [], None

    def report(name, value, unit="", limit=None, ok=None, **extra):
        if ok is not None:
            results.append(ok)
        print(json.dumps({"measure": name, "value": round(value, 2) if isinstance(value, float) else value,
                          "unit": unit, **({"limit": limit, "ok": ok} if limit else {}), **extra}), flush=True)

    report("display", f"{mode['width']}x{mode['height']}@{mode['refresh_rate'] / 1000:.0f} scale {logical['scale']}",
           renderer=os.environ.get("FLOW_PROBE_RENDERER", ""))

    with IsolatedState("flow-probe-") as tmp:
        tmp = Path(tmp)
        runtime, config = tmp / "runtime", tmp / "config"
        runtime.mkdir(mode=0o700)
        config.mkdir()
        (config / "flow-settings.ini").write_text("[General]\nsessionRecovery=false\n")  # automatic recovery is on by default; not this smoke's subject
        wayland = os.environ.get("WAYLAND_DISPLAY", "wayland-1")
        real_runtime = Path(os.environ["XDG_RUNTIME_DIR"])
        env = dict(os.environ, XDG_STATE_HOME=str(tmp), XDG_CONFIG_HOME=str(config), XDG_RUNTIME_DIR=str(runtime),
                   WAYLAND_DISPLAY=wayland if wayland.startswith("/") else str(real_runtime / wayland),
                   QT_LOGGING_RULES="qt.scenegraph.time.renderloop=true")
        app_env = dict(os.environ)

        def ipc(*call):
            return subprocess.run(["quickshell", "ipc", "-p", shell, "call", "desk", *call], env=env, text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10).stdout.strip()

        def terminal(title):
            # A screenful of text, as work leaves it: what a snapshot costs to encode depends on it.
            process = subprocess.Popen(["foot", "-T", title, "sh", "-c", "ls -la /etc /usr/bin /nix/store 2>/dev/null | head -400; sleep 3600"],
                                       env=app_env,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            terminals.append(process)
            return wait_for(lambda: next((w["id"] for w in read("windows") if w.get("pid") == process.pid), None),
                            f"terminal {title}")

        def spaces_shown():
            try:
                return len(json.loads(ipc("spaces_view"))["spaces"])
            except ValueError:
                return -1

        def launch():
            """Start the shell; (process, render log, seconds to IPC, to first frame, to every space shown)."""
            began = time.monotonic()
            process = subprocess.Popen(["quickshell", "-p", shell], env=env, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            log = RenderLog(process.stdout)
            wait_for(lambda: ipc("desk_state") == "closed", "the shell's IPC", timeout=30)
            answered = time.monotonic()
            wait_for(lambda: spaces_shown() >= len(tiles), "every space in the shell", timeout=30)
            shown = time.monotonic()
            first = next((entry[0] for entry in log.between(began, shown + 5) if entry[2] == "frame"), None)
            return process, log, (answered - began) * 1000, (first - began) * 1000 if first else -1, (shown - began) * 1000

        def stop(process):
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

        def quiet(seconds=1.5, timeout=20):
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                stats = controller_stats(runtime, tmp)
                if not stats or not [s for s in stats["recent"] if s > time.time() - seconds]:
                    return
                time.sleep(0.2)
            raise RuntimeError("The shell did not settle")

        tiles = []
        try:
            niri("action", "focus-workspace", empty["idx"])
            for count in counts:
                while len(tiles) < count:
                    tiles.append(terminal(f"probe tile {len(tiles)}"))
                time.sleep(1)
                label = f"{count} spaces"

                # Startup, three times; the last shell stays for the rest.
                timings = []
                for attempt in range(3):
                    if shell_process:
                        stop(shell_process)
                        time.sleep(1)
                    shell_process, log, answered, first, shown = launch()
                    timings.append((answered, first, shown))
                pid = shell_process.pid
                for index, name in enumerate(("IPC answering", "first frame", "every space shown")):
                    values = [round(t[index]) for t in timings]
                    report(f"{label}: startup to {name}", statistics.median(values), "ms", samples=values)
                time.sleep(5)
                quiet()
                stats = controller_stats(runtime, tmp)
                report(f"{label}: memory", rss_mb(pid), "MB",
                       controller=rss_mb(stats["pid"]) if stats else None)

                # Idle: nothing should draw; wakeups stand in for battery.
                watched = {"shell": pid, "niri": niri_pid}
                if stats:
                    watched["controller"] = stats["pid"]
                before = {name: threads_switches(p) for name, p in watched.items()}
                start = time.monotonic()
                time.sleep(20)
                seconds = time.monotonic() - start
                drawn = sum(entry[2] == "frame" for entry in log.between(start, time.monotonic()))
                report(f"{label}: idle frames", drawn, "frames in 20 s", "<= 2", drawn <= 2)
                for name, p in watched.items():
                    report(f"{label}: idle wakeups, {name}", (threads_switches(p) - before[name]) / seconds, "per s")

                # Focus moving across every space.
                sock = NiriSocket()
                try:
                    own = cpu(pid)[0]
                    stats = controller_stats(runtime, tmp)
                    helper = sum(cpu(stats["pid"])) if stats else cpu(pid)[1]
                    refreshed, refresh_ms = (stats["refreshes"], stats["refresh_ms"]) if stats else (0, 0)
                    start, step = time.monotonic(), 0
                    while time.monotonic() - start < 6:
                        sock.focus(tiles[step % len(tiles)])
                        step += 1
                        time.sleep(0.25)
                    time.sleep(0.5)
                    seconds = time.monotonic() - start
                    report(f"{label}: focus every 0.25 s, shell CPU", 100 * (cpu(pid)[0] - own) / seconds, "% of a core")
                    stats = controller_stats(runtime, tmp)
                    helper_after = sum(cpu(stats["pid"])) if stats else cpu(pid)[1]
                    report(f"{label}: focus every 0.25 s, helpers CPU", 100 * (helper_after - helper) / seconds, "% of a core")
                    if stats and stats["refreshes"] > refreshed:
                        report(f"{label}: controller refresh mean",
                               (stats["refresh_ms"] - refresh_ms) / (stats["refreshes"] - refreshed), "ms")
                    report(f"{label}: state sent to the shell", state_bytes(runtime, tmp), "bytes")
                finally:
                    sock.close()
                niri("action", "focus-window", "--id", tiles[0])
                time.sleep(2)
                quiet()

                # Surfaces opening: the shell's own time, and the frames they drew.
                def took(key):
                    try:
                        return json.loads(ipc("open_took", key))
                    except ValueError:
                        return {"ms": -1, "count": 0}

                def surface(name, key, open_call, state_call, close_call, limit):
                    samples, openings, closings = [], [], []
                    for _ in range(5):
                        before = took(key)["count"]
                        start = time.monotonic()
                        ipc(*open_call)
                        samples.append(wait_for(lambda: (now := took(key))["count"] > before and now, name, timeout=5)["ms"])
                        time.sleep(max(0, start + 0.8 - time.monotonic()))
                        openings.append(frames_of(log.between(start, start + 0.8), period, known))
                        start = time.monotonic()
                        ipc(*close_call)
                        wait_for(lambda: ipc(state_call) == "closed", f"{name} to close", timeout=5)
                        time.sleep(max(0, start + 0.8 - time.monotonic()))
                        closings.append(frames_of(log.between(start, start + 0.8), period, known))
                    value = statistics.median(samples)
                    report(f"{label}: {name}", value, "ms", f"< {limit}", value < limit, samples=samples)
                    for what, runs in (("opening", openings), ("closing", closings)):
                        report(f"{label}: {name}, {what}: render work", statistics.median(r["render_work"] for r in runs),
                               "ms", samples=[r["render_work"] for r in runs],
                               new_windows=max(r["new_windows"] for r in runs),
                               frames=statistics.median(r["frames"] for r in runs),
                               longest_frame=max(r["longest_frame"] for r in runs),
                               longest_gap=max(r["longest_gap"] for r in runs),
                               late=sum(r["late"] for r in runs), period=round(period, 1))

                known = {entry[1] for entry in log.between(0, time.monotonic())}
                surface("Overview's first frame", "overview", ["overview_open"], "overview_state", ["overview_close"], 200)
                surface("Desk with a fresh answer", "desk", ["desk_open"], "desk_state", ["leave"], 150)
                surface("search rows shown", "search", ["search"], "search_state", ["search"], 300)
                # Overview's cards decode their snapshots: the shell's memory with them, Overview open again.
                ipc("overview_open")
                wait_for(lambda: ipc("overview_state") == "open", "Overview", timeout=5)
                time.sleep(1)
                report(f"{label}: memory with Overview open", rss_mb(pid), "MB")
                ipc("overview_close")
                wait_for(lambda: ipc("overview_state") == "closed", "Overview to close", timeout=5)

            # Overview's snapshot, as the shell takes it (at the display's resolution), and as it took it before
            # 2026-11-22 (scaled by grim to 640 px wide).
            shot = tmp / "shot.jpg"
            for name, scaling in (("Overview's snapshot (grim)", []),
                                  ("a snapshot scaled by grim to 640 px", ["-s", str(min(1, 640 / max(1, logical["width"])))])):
                took_ms = []
                for _ in range(8):
                    start = time.perf_counter()
                    subprocess.run(["grim", "-o", output_name, *scaling, "-t", "jpeg", "-q", "72", str(shot)],
                                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    took_ms.append(round((time.perf_counter() - start) * 1000))
                report(name, statistics.median(took_ms[2:]), "ms", samples=took_ms[2:], bytes=shot.stat().st_size)
        finally:
            if shell_process:
                stop(shell_process)
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
