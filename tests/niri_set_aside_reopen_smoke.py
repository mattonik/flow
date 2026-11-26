#!/usr/bin/env python3
"""Settings' Reopen it, for a saved session automatic recovery set aside, in a development Flow shell.

Run inside a Niri session from the Flow checkout with the installed shell stopped (``systemctl --user stop
flow-shell``), ``FLOW_SHELL`` naming the development ``shell.qml``, and ``foot`` and ``flow-recovery`` on PATH.
The shell gets its own runtime directory, state and configuration, with automatic session recovery on. The
focused display's empty workspace must be free; the smoke works there and closes every window it opened. With
``FLOW_SCREENSHOT`` set (and ``grim`` on PATH), Settings with the set-aside line is captured there.

The saved session is two terminals, ``flow-sa-one`` and ``flow-sa-work``, saved; ``flow-sa-one`` is closed and
``flow-sa-work`` stays open as the work, so the session lists an app the work has open. The shell's runtime has
no marker and Niri started over ten minutes ago, so its first start takes the open window for work under way
(``restore.under_way_long_enough``) and sets the session aside rather than reopen it into that work.

Checked:
- the shell sets the saved session aside: the file holds what was saved, the shell names it, a toast says so;
- automatic saving then writes session.json with the work window (what replaced a set-aside renamed back to
  session.json, as Settings once advised);
- Reopen it reopens both saved terminals on the display's empty workspace from the set-aside file, leaves the
  work window where it was, keeps the file, and the shell no longer offers it.

Prints one JSON line per check and a final ``{"result": ...}``; exits 1 if any check failed.
"""

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

import importlib.util

from isolated_state import IsolatedState
from niri_performance_smoke import niri, read, wait_for

spec = importlib.util.spec_from_file_location("restore", Path(__file__).parents[1] / "session/restore.py")
restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore)

SAVE_EVERY = 30  # s at most between automatic saves (the controller's AUTO_SAVE_EVERY)


def main():
    shell = os.environ.get("FLOW_SHELL")
    if not shell or not shutil.which("foot") or not shutil.which("flow-recovery"):
        raise RuntimeError("Run with FLOW_SHELL set and foot and flow-recovery on PATH")
    if subprocess.run(["systemctl", "--user", "is-active", "--quiet", "flow-shell"]).returncode == 0:
        raise RuntimeError("Stop the installed shell first: it reacts to the same events")
    start_workspace = next(w for w in read("workspaces") if w["is_focused"])
    output = start_workspace["output"]
    empty = max((w for w in read("workspaces") if w["output"] == output), key=lambda w: w["idx"])
    if any(w["workspace_id"] == empty["id"] for w in read("windows")):
        raise RuntimeError("The focused display has no empty workspace")
    if restore.niri_age() < restore.UNDER_WAY_AFTER + 60:
        raise RuntimeError("Niri started under eleven minutes ago: open work would not yet count as under way")
    results, terminals, shell_process = [], [], None

    def check(name, value, ok):
        results.append(ok)
        print(json.dumps({"check": name, "value": value, "ok": ok}), flush=True)

    with IsolatedState("flow-set-aside-") as tmp:
        tmp = Path(tmp)
        runtime, config, data = tmp / "runtime", tmp / "config", tmp / "data"
        runtime.mkdir(mode=0o700)
        for folder in (config, data / "applications"):
            folder.mkdir(parents=True)
        (config / "flow-settings.ini").write_text("[General]\nsessionRecovery=true\n")
        for name in ("one", "work"):
            (data / "applications" / f"flow-sa-{name}.desktop").write_text(
                f"[Desktop Entry]\nType=Application\nName=Set aside {name}\nExec=foot --app-id flow-sa-{name}\n")
        wayland = os.environ.get("WAYLAND_DISPLAY", "wayland-1")
        env = dict(os.environ, XDG_STATE_HOME=str(tmp), XDG_RUNTIME_DIR=str(runtime), XDG_CONFIG_HOME=str(config),
                   XDG_DATA_HOME=str(data),
                   WAYLAND_DISPLAY=wayland if wayland.startswith("/") else str(Path(os.environ["XDG_RUNTIME_DIR"]) / wayland))
        session = tmp / "flow" / "session.json"

        def ipc(*call):
            return subprocess.run(["quickshell", "ipc", "-p", shell, "call", "desk", *call], env=env, text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10).stdout.strip()

        def recovery(*args):
            result = subprocess.run(["flow-recovery", *args], env=env, text=True, capture_output=True, timeout=60)
            return json.loads(result.stdout or "{}")

        def state():
            try:
                return json.loads(ipc("recovery_state") or "{}")
            except ValueError:
                return {}

        def terminal(name):
            process = subprocess.Popen(["foot", "--app-id", f"flow-sa-{name}"], stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            terminals.append(process)
            return wait_for(lambda: next((w["id"] for w in read("windows") if w.get("pid") == process.pid), None),
                            f"terminal {name}")

        def windows_of(app, workspace=None):
            return [w for w in read("windows") if w.get("app_id") == app
                    and (workspace is None or w["workspace_id"] == workspace)]

        try:
            niri("action", "focus-workspace", empty["idx"])
            terminal("one")
            work_id = terminal("work")
            saved = recovery("save")
            if not session.exists() or len(json.loads(session.read_text())["windows"]) != 2:
                raise RuntimeError(f"Save session failed: {saved}")
            saved_bytes = session.read_bytes()
            first = terminals.pop(0)
            first.terminate()
            first.wait(timeout=10)
            wait_for(lambda: not windows_of("flow-sa-one") or None, "the saved terminal to close")
            work_at = next(w for w in read("windows") if w["id"] == work_id)["layout"]["pos_in_scrolling_layout"]

            shell_process = subprocess.Popen(["quickshell", "-p", shell], env=env, stdin=subprocess.DEVNULL,
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # The toast lasts 1.8 s and comes with the set-aside, so it is watched alone, then the path read.
            try:
                toast = wait_for(lambda: (t := ipc("toast")).startswith("Nothing was reopened") and t,
                                 "the set-aside toast", timeout=30)
            except RuntimeError:
                toast = ""
            try:
                aside = Path(wait_for(lambda: state().get("set_aside"), "the shell to name the set-aside", timeout=10))
            except RuntimeError:
                aside = None
            kept = bool(aside) and aside.parent == session.parent and aside.exists() and aside.read_bytes() == saved_bytes
            check("first start, no marker, a window open past ten minutes: the session set aside, named, a toast",
                  f"{aside}, toast {toast!r}", kept and bool(toast) and not windows_of("flow-sa-one"))
            if not kept:
                return 1

            def saved_work():
                try:
                    apps = [w["app_id"] for w in json.loads(session.read_text())["windows"]]
                except (OSError, ValueError, KeyError):
                    return None
                return apps if "flow-sa-work" in apps else None
            try:
                apps = wait_for(saved_work, "an automatic save of the work", timeout=SAVE_EVERY + 15)
            except RuntimeError:
                apps = None
            check("automatic saving writes session.json with the work, not the set-aside session", apps,
                  apps == ["flow-sa-work"])

            if os.environ.get("FLOW_SCREENSHOT") and shutil.which("grim"):
                ipc("show_set_aside")
                time.sleep(1.5)
                ipc("show_set_aside")  # again, laid out now: the first scroll ran as the Desk was still opening
                time.sleep(1)
                subprocess.run(["grim", "-o", output, os.environ["FLOW_SCREENSHOT"]], check=False)
                ipc("leave")
                time.sleep(1)

            ipc("reopen_set_aside")
            try:
                wait_for(lambda: (s := state()) and not s.get("working") and s.get("message", "").startswith("Reopened")
                         or None, "Reopen to finish", timeout=60)
            except RuntimeError:
                pass
            after = state()
            work = next((w for w in read("windows") if w["id"] == work_id), {})
            elsewhere = sorted(w["app_id"] for w in read("windows") if w.get("app_id", "").startswith("flow-sa-")
                               and w["workspace_id"] != empty["id"])
            check("Reopen it: both saved apps on the empty workspace, the work window left where it was, the file "
                  "kept, no longer offered",
                  f"reopened {elsewhere}, work {work.get('workspace_id')}:{work.get('layout', {}).get('pos_in_scrolling_layout')}"
                  f", message {after.get('message')!r}, offered {after.get('set_aside')!r}",
                  elsewhere == ["flow-sa-one", "flow-sa-work"] and work.get("workspace_id") == empty["id"]
                  and work["layout"]["pos_in_scrolling_layout"] == work_at and aside.read_bytes() == saved_bytes
                  and after.get("set_aside") == "" and after.get("message", "").startswith("Reopened 2 of 2"))
        finally:
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
            for window in windows_of("flow-sa-one") + windows_of("flow-sa-work"):  # some opened by Reopen, not here
                if window.get("pid"):
                    try:
                        os.kill(window["pid"], signal.SIGTERM)
                    except ProcessLookupError:
                        pass
            niri("action", "focus-workspace", start_workspace["idx"])
    ok = all(results) and len(results) == 3
    print(json.dumps({"result": "pass" if ok else "fail"}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
