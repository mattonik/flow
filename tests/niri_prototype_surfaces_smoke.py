#!/usr/bin/env python3
"""Flow's Overview, home indicator and search panel behave, on a running development shell.

Run inside a Niri session from the Flow checkout, with ``FLOW_SHELL`` set to the development
``shell.qml``, the focused output's active workspace empty, ``flow-spaces`` and ``flow-pointer`` on PATH, and
the installed shell stopped (``systemctl --user stop flow-shell``): its home indicator sits where the
development shell's does. ``FLOW_GRIM``
may name a grim binary; without one the snapshot check is skipped. The shell gets its own runtime
directory, state and configuration; Wayland and Niri are reached by absolute paths. Every shell
process the test started is found by that runtime directory and stopped at the end (Quickshell
rewrites its process title, so its command line cannot find it).

What is checked, through the shell's IPC calls:
- Overview opens on the focused space; Overview again goes on to Desk; never two surfaces at once.
- The selection stays on the chosen space when the list of spaces changes underneath it.
- A Desk or search request while Overview is still opening wins; Overview never lands on top.
- Pinning from Overview keeps Overview, and does not open Desk under it.
- Search opens over Desk, and the Platform API's busy rules count a pending Overview.
- A snapshot of a space is taken once it has settled in the work view, and none of Desk.

With the pointer (tests/pointer.py, so ``flow-pointer`` on PATH and the installed shell stopped):
- with a touchscreen plugged in (uinput: the home line shows only with a touch device, ADR-0014), the home
  indicator lights under the pointer; a tap opens Overview, a tap on Overview's goes on to Desk,
  and a tap on Desk's returns to work;
- one wheel notch over Overview's row moves the selection one space, down to the next and up to the
  previous;
- a swipe up from the home indicator opens Overview, and only Overview is open after the release. (The
  indicator's window hides under the pointer once Overview opens, so the release never reaches it; that
  ``onClicked`` ignores a release after a swipe cannot be shown here.)
"""

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

from isolated_state import IsolatedState
from pointer import Pointer, center
from uinput import Devices


def niri(*args):
    return subprocess.check_output(["niri", "msg", *map(str, args)], text=True, timeout=10)


def read(kind):
    return json.loads(niri("-j", kind))


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
    shell = os.environ.get("FLOW_SHELL")
    if not shell:
        raise RuntimeError("Set FLOW_SHELL to the development shell.qml")
    real_runtime = Path(os.environ["XDG_RUNTIME_DIR"])
    wayland = os.environ.get("WAYLAND_DISPLAY", "wayland-1")
    workspace = next(w for w in read("workspaces") if w["is_focused"])
    if any(w.get("workspace_id") == workspace["id"] for w in read("windows")):
        raise RuntimeError("The focused output's active workspace must be empty")
    real_state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "flow"
    real_files = [real_state / name for name in ("spaces.json", "groups.json", "session.json")]
    real_digests = [digest(path) for path in real_files]
    token = uuid.uuid4().hex[:8]
    steps, apps = [], []

    with IsolatedState("flow-surfaces-") as tmp:
        root = Path(tmp)
        runtime = root / "runtime"
        runtime.mkdir(mode=0o700)
        (root / "config").mkdir()
        (root / "config" / "flow-settings.ini").write_text("[General]\nsessionRecovery=false\n")  # automatic recovery is on by default; not this smoke's subject
        bin_dir = root / "bin"
        bin_dir.mkdir()
        grim = os.environ.get("FLOW_GRIM")
        if grim:
            (bin_dir / "grim").symlink_to(grim)
        env = dict(os.environ, XDG_RUNTIME_DIR=str(runtime), XDG_STATE_HOME=str(root), XDG_CONFIG_HOME=str(root / "config"),
                   WAYLAND_DISPLAY=wayland if wayland.startswith("/") else str(real_runtime / wayland),
                   PATH=f"{bin_dir}:{os.environ['PATH']}")

        def ipc(*args):
            out = subprocess.run(["quickshell", "ipc", "-p", shell, "call", "desk", *map(str, args)], env=env, text=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10).stdout.strip()
            return out

        def surfaces():
            try:
                return json.loads(ipc("surfaces") or "{}")
            except ValueError:
                return {}

        def open_surfaces():
            state = surfaces()
            return sorted(name for name, is_open in state.items() if is_open and name != "pending")

        def target(name):
            try:
                return json.loads(ipc("pointer_target", name) or "null")
            except ValueError:
                return None

        def never_two(label):
            shown = open_surfaces()
            if len(shown) > 1:
                raise RuntimeError(f"{label}: two surfaces open at once: {shown}")

        def mine():
            found = []
            for line in subprocess.run(["pgrep", "-f", "quickshell"], text=True, stdout=subprocess.PIPE).stdout.split():
                try:
                    environ = Path(f"/proc/{line}/environ").read_bytes().split(b"\0")
                except OSError:
                    continue
                if f"XDG_RUNTIME_DIR={runtime}".encode() in environ:
                    found.append(int(line))
            return found

        def open_app(suffix):
            app = f"flow-surfaces-{token}-{suffix}"
            apps.append(subprocess.Popen(["foot", "--app-id", app], stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            return wait_for(lambda: next((w for w in read("windows") if w["app_id"] == app), None), app)

        pointer = Pointer()
        # The home line shows only with a touch device (ADR-0014): a touchscreen, plugged in before the shell starts.
        touch = Devices(touchscreen=True)
        try:
            first, second, third = (open_app(s) for s in "abc")
            subprocess.Popen(["quickshell", "-p", shell], env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            wait_for(lambda: ipc("desk_state") == "closed", "the development shell", timeout=20)
            niri("action", "focus-window", "--id", second["id"])
            time.sleep(1.5)  # the focused space settles; a snapshot is taken

            snapshots = runtime / "flow-snapshots"
            if grim:
                wait_for(lambda: (snapshots / f"{second['id']}.jpg").exists(), "a snapshot of the settled space")
                steps.append("a space that settled in the work view got a snapshot")

            ipc("overview")
            wait_for(lambda: ipc("overview_state") == "open", "Overview")
            if ipc("overview_selected") != str(second["id"]):
                raise RuntimeError(f"Overview did not open on the focused space: {ipc('overview_selected')}")
            never_two("Overview")
            # Not the first card: a list that resets to its start would land there by accident.
            ipc("overview_move", 1)
            chosen = ipc("overview_selected")
            if chosen != str(third["id"]):
                raise RuntimeError(f"Moving right did not select the third space: {chosen}")
            open_app("d")  # the list of spaces changes underneath the open Overview
            time.sleep(2)
            if ipc("overview_selected") != chosen:
                raise RuntimeError(f"The selection jumped when the spaces changed: {ipc('overview_selected')}")
            steps.append("Overview opened on the focused space and kept the chosen space when a window opened")

            ipc("pin")  # pins the focused space, as the tool bar's Pin space does
            time.sleep(2)
            if open_surfaces() != ["overview"]:
                raise RuntimeError(f"Pinning from Overview changed the surfaces: {open_surfaces()}")
            steps.append("pinning from Overview kept Overview and did not open Desk")

            ipc("overview")
            wait_for(lambda: ipc("desk_state") == "open", "Desk after Overview again")
            never_two("Overview again")
            steps.append("Overview again went on to Desk, with Overview closed")

            if grim:
                before = {p.name: p.stat().st_mtime for p in snapshots.glob("*.jpg")}
                time.sleep(1.5)
                after = {p.name: p.stat().st_mtime for p in snapshots.glob("*.jpg")}
                if before != after:
                    raise RuntimeError("A snapshot was taken while Desk was open")
            ipc("search")
            wait_for(lambda: ipc("search_state") == "open", "search over Desk")
            steps.append("search opened over Desk" + (", and no snapshot was taken while Desk was open" if grim else ""))
            ipc("search")
            ipc("leave")
            wait_for(lambda: open_surfaces() == [], "everything closed")

            # A Desk request while Overview is still opening (waiting for its snapshot) wins.
            ipc("overview")
            ipc("toggle")
            time.sleep(2)
            if open_surfaces() != ["desk"]:
                raise RuntimeError(f"Desk during Overview's opening left: {open_surfaces()}")
            ipc("leave")
            ipc("overview")
            busy = ipc("desk_open")
            time.sleep(2)
            if busy != "busy" or open_surfaces() != ["overview"]:
                raise RuntimeError(f"ShowDesk while Overview opened answered {busy!r} and left {open_surfaces()}")
            # Super+H over an open Overview: Desk replaces it.
            ipc("toggle")
            time.sleep(1)
            if open_surfaces() != ["desk"]:
                raise RuntimeError(f"Desk over an open Overview left: {open_surfaces()}")
            ipc("leave")
            wait_for(lambda: open_surfaces() == [], "Desk closed")
            steps.append("Desk while Overview was opening won, ShowDesk then was busy, and Desk over an open "
                         "Overview replaced it: never two at once")

            # The pointer.
            pointer.park()
            pointer.click(pointer.hover(lambda: target("home"), "the home indicator"))
            wait_for(lambda: open_surfaces() == ["overview"], lambda: f"a tap on the home indicator to open Overview ({open_surfaces()})")
            steps.append("the home indicator lit under the pointer, and a tap opened Overview")

            # From the row's start, whose next space overview_move names: a notch that moved two would land past it.
            row = wait_for(lambda: target("overview_row"), "Overview's row")

            def step(move):
                before = ipc("overview_selected")
                ipc("overview_move", move)
                time.sleep(0.4)
                return ipc("overview_selected") != before

            while step(-1):
                pass
            start, next_space = ipc("overview_selected"), (step(1), ipc("overview_selected"))[1]
            third_space = (step(1), ipc("overview_selected"))[1]
            step(-1)
            step(-1)
            if ipc("overview_selected") != start or len({start, next_space, third_space}) != 3:
                raise RuntimeError(f"Overview's row has fewer than three spaces, or moving did not return "
                                   f"({start}, {next_space}, {third_space})")
            pointer.scroll(center(row), 1)
            wait_for(lambda: ipc("overview_selected") == next_space,
                     lambda: f"a notch down to select the next space {next_space} ({ipc('overview_selected')})")
            time.sleep(0.5)
            if ipc("overview_selected") != next_space:
                raise RuntimeError(f"One notch down went past the next space: {ipc('overview_selected')}")
            pointer.scroll(center(row), -1)
            wait_for(lambda: ipc("overview_selected") == start,
                     lambda: f"a notch up to select the first space {start} again ({ipc('overview_selected')})")
            steps.append("one wheel notch over Overview's row moved the selection one space down, and one back up")

            pointer.click(pointer.hover(lambda: target("overview_home"), "Overview's home indicator"))
            wait_for(lambda: open_surfaces() == ["desk"], lambda: f"a tap on Overview's home indicator to open Desk ({open_surfaces()})")
            pointer.click(pointer.hover(lambda: target("desk_home"), "Desk's home indicator"))
            wait_for(lambda: open_surfaces() == [], lambda: f"a tap on Desk's home indicator to return to work ({open_surfaces()})")
            steps.append("a tap on Overview's home indicator went on to Desk, and one on Desk's returned to work")

            pointer.park()
            start = pointer.hover(lambda: target("home"), "the home indicator again")
            pointer.drag(start, (start[0], start[1] - 60))
            wait_for(lambda: open_surfaces() == ["overview"], lambda: f"a swipe up to open Overview ({open_surfaces()})")
            time.sleep(1)
            if open_surfaces() != ["overview"]:
                raise RuntimeError(f"After a swipe up and release, {open_surfaces()} were open, not Overview alone")
            ipc("overview_close")
            wait_for(lambda: open_surfaces() == [], "Overview to close")
            steps.append("a swipe up from the home indicator opened Overview, and only Overview after the release")
            pointer.park()
            result = {"result": "pass", "steps": steps}
        finally:
            touch.close()
            for pid in mine():
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            time.sleep(1)
            for pid in mine():
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            for app in apps:
                app.terminate()
                try:
                    app.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    app.kill()
        if mine():
            raise RuntimeError("A development shell outlived the test")
    if [digest(path) for path in real_files] != real_digests:
        raise RuntimeError("A real Flow Group, pin, or session file changed")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, KeyError, StopIteration, subprocess.SubprocessError, ValueError) as exc:
        print(f"Prototype surfaces test failed: {exc}", file=sys.stderr)
        sys.exit(1)
