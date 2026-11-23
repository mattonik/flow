#!/usr/bin/env python3
"""What a development Flow shell does in the background while nothing changes: Overview's snapshots of spaces
and automatic session saving.

Run inside a Niri session from the Flow checkout with the installed shell stopped (``systemctl --user stop
flow-shell``), ``FLOW_SHELL`` naming the development ``shell.qml``, and ``foot``, ``grim`` and ``flow-recovery``
on PATH. The shell gets its own runtime directory, state and configuration, with automatic session recovery
turned on. Five terminals open on the focused display's empty workspace, which must be free; each has an app ID
of its own with a desktop entry in the smoke's own data folder, so automatic saving finds nothing to stop it.
All are closed afterwards.

Every ``grim`` and ``flow-recovery`` the shell starts goes through a recorder first on its PATH, which logs the
time and arguments and runs the real one.

Checked:
- a walk through the five spaces, 2.5 s on each: a snapshot of each (one may be there from startup); a second
  walk, with nothing on them changed: no snapshot (main took one each time a space settled);
- a space whose window's title changed: its snapshot taken again when it settles;
- once a change has been saved, 65 s of nothing changing: no automatic save (main saved every 30 s);
- a focus change saved by the next automatic save, within 33 s;
- a save that began half a second after a Niri event (focus overshooting to another space and back): saved
  again at the next tick, as it may have read Niri between two states;
- a space whose snapshot is over a minute old: taken again when it settles;
- a window joining another's column with focus unchanged, which changes only the spaces: saved by the next
  automatic save.

Prints one JSON line per check and a final ``{"result": ...}``; exits 1 if any check failed.
"""

import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from isolated_state import IsolatedState
from niri_performance_smoke import RECORDER, NiriSocket, cpu, niri, read, runs, wait_for

SPACES = "abcde"
# s on each space. A focus change reaches the shell with the controller's next refresh, up to 1 s later (its
# maximum wait); the space then settles for 0.7 s before its snapshot, which takes about 0.1 s.
DWELL = 2.5
SAVE_EVERY = 30  # s, the shell's automatic-save timer
# Each terminal retitles itself with what its title file holds, once it appears.
TITLER = r"""f={file}; while :; do
  if [ -e "$f" ]; then printf '\033]2;%s\007' "$(cat "$f")"; rm -f "$f"; fi
  sleep 0.1; done"""


def main():
    shell = os.environ.get("FLOW_SHELL")
    real = {name: shutil.which(name) for name in ("grim", "flow-recovery")}
    if not shell or not shutil.which("foot") or not all(real.values()):
        raise RuntimeError("Run with FLOW_SHELL set and foot, grim and flow-recovery on PATH")
    if subprocess.run(["systemctl", "--user", "is-active", "--quiet", "flow-shell"]).returncode == 0:
        raise RuntimeError("Stop the installed shell first: it reacts to the same events")
    bash = shutil.which("bash")
    start_workspace = next(w for w in read("workspaces") if w["is_focused"])
    output = start_workspace["output"]
    empty = max((w for w in read("workspaces") if w["output"] == output), key=lambda w: w["idx"])
    if any(w["workspace_id"] == empty["id"] for w in read("windows")):
        raise RuntimeError("The focused display has no empty workspace")
    results, terminals, shell_process, sock = [], [], None, None

    def check(name, value, limit, ok, unit=""):
        results.append(ok)
        print(json.dumps({"check": name, "value": value, "limit": limit, "unit": unit, "ok": ok}), flush=True)

    with IsolatedState("flow-background-") as tmp:
        tmp = Path(tmp)
        runtime, bin_dir, config, data, titles = (tmp / name for name in ("runtime", "bin", "config", "data", "titles"))
        runtime.mkdir(mode=0o700)
        for folder in (bin_dir, config, data / "applications", titles):
            folder.mkdir(parents=True)
        (config / "flow-settings.ini").write_text("[General]\nautoRecovery=true\n")
        for name in SPACES:
            (data / "applications" / f"flow-bg-{name}.desktop").write_text(
                f"[Desktop Entry]\nType=Application\nName=Background {name}\nExec=foot --app-id flow-bg-{name}\n")
        logs = {name: tmp / f"{name}.log" for name in real}
        for name in real:
            (bin_dir / name).write_text(RECORDER.format(bash=bash, log=logs[name], real=real[name]))
            (bin_dir / name).chmod(0o755)
        wayland = os.environ.get("WAYLAND_DISPLAY", "wayland-1")
        env = dict(os.environ, XDG_STATE_HOME=str(tmp), XDG_RUNTIME_DIR=str(runtime), XDG_CONFIG_HOME=str(config),
                   XDG_DATA_HOME=str(data), PATH=f"{bin_dir}:{os.environ['PATH']}",
                   WAYLAND_DISPLAY=wayland if wayland.startswith("/") else str(Path(os.environ["XDG_RUNTIME_DIR"]) / wayland))
        session = tmp / "flow" / "session.json"

        def ipc(*call):
            return subprocess.run(["quickshell", "ipc", "-p", shell, "call", "desk", *call], env=env, text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10).stdout.strip()

        def saves(start, end=None):
            return [stamp for stamp, _, args in runs(logs["flow-recovery"], start, end or time.time() + 1)
                    if args == "auto-save"]

        def snapshots(start, end=None):
            return [stamp for stamp, _, _ in runs(logs["grim"], start, end or time.time() + 1)]

        def saved_columns():
            try:
                return {w["window_id"]: w["column"] for w in json.loads(session.read_text())["windows"]}
            except (OSError, ValueError, KeyError):
                return {}

        def saved_focus():
            try:
                return next((w["window_id"] for w in json.loads(session.read_text())["windows"] if w["focused"]), None)
            except (OSError, ValueError, KeyError):
                return None

        def terminal(name):
            process = subprocess.Popen(["foot", "--app-id", f"flow-bg-{name}", "-T", f"background {name}",
                                        "sh", "-c", TITLER.format(file=titles / name)],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            terminals.append(process)
            return wait_for(lambda: next((w["id"] for w in read("windows") if w.get("pid") == process.pid), None),
                            f"terminal {name}")

        try:
            niri("action", "focus-workspace", empty["idx"])
            tiles = [terminal(name) for name in SPACES]
            shell_process = subprocess.Popen(["quickshell", "-p", shell], env=env, stdin=subprocess.DEVNULL,
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            pid = shell_process.pid
            wait_for(lambda: ipc("desk_state") == "closed", "the development shell")
            def spaces_shown():
                try:
                    return len(json.loads(ipc("spaces_view"))["spaces"])
                except (ValueError, KeyError):
                    return 0
            wait_for(lambda: spaces_shown() >= len(SPACES), "the shell's five spaces")
            sock = NiriSocket()

            def visit(ident, seconds=DWELL):
                sock.focus(ident)
                time.sleep(seconds)

            def saved_after(mark, label):
                """Wait for the first automatic save after ``mark`` to finish; its time."""
                stamp = wait_for(lambda: next(iter(saves(mark)), None), label, timeout=SAVE_EVERY + 10)
                time.sleep(2)  # it saves in about 60 ms in the guest; its process ends well within this
                return stamp

            def settles(ident):
                """Focus a space and let it settle: how many snapshots it took."""
                start = time.time()
                sock.focus(ident)
                try:
                    wait_for(lambda: snapshots(start), "a snapshot", timeout=DWELL + 1)
                except RuntimeError:
                    pass
                time.sleep(1)
                return len(snapshots(start))

            def saved_within(test, label):
                """Seconds until the saved session passes ``test``, or None after a save's period and more."""
                start = time.time()
                try:
                    wait_for(test, label, timeout=SAVE_EVERY + 10)
                    return round(time.time() - start, 1)
                except RuntimeError:
                    return None

            # Startup: automatic recovery starts with nothing saved, then saves what it finds.
            saved_after(0, "the first automatic save")

            # Snapshots: every space once, then again with nothing on them changed.
            for ident in tiles:
                visit(ident)
            time.sleep(1)
            first = snapshots(0)
            check("a first walk through the five spaces: snapshots", len(first), f">= {len(SPACES) - 1}",
                  len(first) >= len(SPACES) - 1)
            start = time.time()
            reaped = cpu(pid)[1]
            for ident in tiles:
                visit(ident)
            time.sleep(1)  # a late snapshot of the last space counts
            again = snapshots(start)
            print(json.dumps({"measure": "second walk: CPU of the shell's helpers",
                              "value": round(100 * (cpu(pid)[1] - reaped) / (time.time() - start), 2),
                              "unit": "% of a core"}), flush=True)
            check("a second walk through unchanged spaces: snapshots", len(again), "0", len(again) == 0)

            # A space whose window changed its title: the shell's state for that space is not what it was.
            visit(tiles[1])
            (titles / SPACES[0]).write_text("background a, retitled")
            wait_for(lambda: "a, retitled" in ipc("window_labels"), "the new title in the shell's state")
            taken = settles(tiles[0])
            check("a space whose title changed: snapshots when it settles", taken, "1", taken == 1)

            # Automatic saving: once what changed is saved, nothing more while nothing changes.
            time.sleep(SAVE_EVERY + 5)  # the save of the walk's changes, if one is still due
            start = time.time()
            time.sleep(65)
            idle = saves(start, time.time())
            check("65 s with nothing changed: automatic saves", len(idle), "0", len(idle) == 0)

            # A change is saved by the next save.
            sock.focus(tiles[2])
            took = saved_within(lambda: saved_focus() == tiles[2], "the save of a focus change")
            check("a focus change: saved by the next automatic save", took, f"< {SAVE_EVERY + 3}",
                  took is not None and took < SAVE_EVERY + 3, "s")

            # A save that began within a second of a Niri event may have read Niri between two states (focus
            # overshooting and coming back, which the controller never sends): it leaves the change unsaved.
            time.sleep(SAVE_EVERY + 3)  # a second save of the focus change, if its save ran within a second of it
            last = max(saves(0))
            tick = last + SAVE_EVERY * math.ceil((time.time() + 8 - last) / SAVE_EVERY)  # a tick 8 s away or more
            (titles / SPACES[1]).write_text("background b, retitled")
            wait_for(lambda: "b, retitled" in ipc("window_labels"), "the second new title in the shell's state")
            time.sleep(max(0, tick - 0.5 - time.time()))
            sock.focus(tiles[3])
            sock.focus(tiles[2])
            time.sleep(max(0, tick + SAVE_EVERY + 3 - time.time()))
            around = saves(tick - 1, tick + SAVE_EVERY + 3)
            check("a save half a second after a Niri event: saved again at the next tick", len(around), "2",
                  len(around) == 2)

            # A space last snapshotted over a minute ago (tiles[3], in the first walk) is taken again.
            taken = settles(tiles[3])
            check("a space snapshotted over a minute ago: snapshots when it settles", taken, "1", taken == 1)

            # A change only to the spaces (the window to the right joins the focused one's column; focus stays).
            niri("action", "consume-window-into-column")
            took = saved_within(lambda: saved_columns().get(tiles[4]) == saved_columns().get(tiles[3]),
                                "the save of a column change")
            check("a window joining another's column, focus unchanged: saved by the next automatic save", took,
                  f"< {SAVE_EVERY + 3}", took is not None and took < SAVE_EVERY + 3, "s")
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
