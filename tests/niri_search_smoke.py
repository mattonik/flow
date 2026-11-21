#!/usr/bin/env python3
"""The search panel of a development shell: it shows results, opens the chosen one and says why when it can't.

Run inside a Niri session from the Flow checkout, with ``FLOW_SHELL`` set to the development ``shell.qml``,
``wtype`` on PATH and the installed shell stopped (``systemctl --user stop flow-shell``: its keyboard
surfaces would take the keys). The shell gets its own runtime directory, state and configuration, and a
stand-in ``flow-search`` first on PATH that lists fixed results and records what it is asked to open, so
no app starts. Keys are typed with wtype into the panel, which has the keyboard while open.

Checked, through the shell's ``search_view`` and ``toast`` IPC calls and the stand-in's log:
- the panel lists what ``flow-search --json`` answers;
- typing ranks an exact title first and keeps ties in flow-search's order, so Return on "Notes" opens
  the Notes app, not "Notes Server" or Notes as a Layer, through ``flow-search --open app <desktop file>``;
- a query that matches nothing says so, in its own space above the footer;
- a result that fails to open shows a toast naming it, also when ``flow-search`` cannot even start;
- a flow-search that does not answer with results, or cannot start, says search failed, not "Nothing matches".

That ``flow-search`` itself answers ``--json`` and finds gio is checked by ``checks.module`` (flake.nix),
which runs the installed wrapper.
"""

import hashlib
import itertools
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

# Enough ties that an unstable sort shows (the "Foot" query in the guest had six): every app twice.
APPS = ["Notes Client", "Notes Server", "Notes", "Notes Viewer"]
ROWS = ([{"title": "Flow Settings", "detail": "Settings", "kind": "settings", "value": "settings", "icon": ""}]
        + [{"title": a, "detail": "Application", "kind": "app", "value": f"/apps/{a}.desktop", "icon": ""} for a in APPS]
        + [{"title": a, "detail": "Open as Layer", "kind": "layer", "value": f"{a}.desktop", "icon": ""} for a in APPS])
STAND_IN = """#!/bin/sh
# Flow search, standing in: --json answers with the rows in $DIR/rows (or fails when $DIR/fail-list
# exists); --open records its arguments and exits with the code in $DIR/open-exit.
DIR={dir}
case "$1" in
  --json) [ -e "$DIR/fail-list" ] && {{ echo "usage: flow-search" >&2; exit 2; }}; cat "$DIR/rows" ;;
  --open) echo "$2 $3" >> "$DIR/opened"; exit "$(cat "$DIR/open-exit" 2>/dev/null || echo 0)" ;;
  *) exit 2 ;;
esac
"""


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def wait_for(test, label, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = test()
        if value:
            return value
        time.sleep(0.2)
    raise RuntimeError(f"Timed out waiting for {label() if callable(label) else label}")


def main():
    shell = os.environ.get("FLOW_SHELL")
    if not shell or not shutil.which("wtype"):
        raise RuntimeError("Run with FLOW_SHELL set and wtype on PATH")
    if subprocess.run(["systemctl", "--user", "is-active", "--quiet", "flow-shell"]).returncode == 0:
        raise RuntimeError("Stop the installed shell first: its surfaces would take the keys")
    real_runtime = Path(os.environ["XDG_RUNTIME_DIR"])
    wayland = os.environ.get("WAYLAND_DISPLAY", "wayland-1")
    real_state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "flow"
    real_files = [real_state / name for name in ("spaces.json", "groups.json", "session.json")]
    real_digests = [digest(path) for path in real_files]
    steps, shell_process = [], None

    with tempfile.TemporaryDirectory(prefix="flow-search-") as tmp:
        tmp = Path(tmp)
        runtime, stand_in = tmp / "runtime", tmp / "bin"
        runtime.mkdir(mode=0o700)
        stand_in.mkdir()
        (tmp / "rows").write_text(json.dumps(ROWS))
        (stand_in / "flow-search").write_text(STAND_IN.format(dir=tmp))
        (stand_in / "flow-search").chmod(0o755)
        opened = tmp / "opened"
        env = dict(os.environ, XDG_STATE_HOME=str(tmp), XDG_CONFIG_HOME=str(tmp / "config"), XDG_RUNTIME_DIR=str(runtime),
                   WAYLAND_DISPLAY=wayland if wayland.startswith("/") else str(real_runtime / wayland),
                   PATH=f"{stand_in}:{os.environ['PATH']}")
        typing_env = dict(os.environ, WAYLAND_DISPLAY=env["WAYLAND_DISPLAY"])

        def ipc(*call):
            try:
                return subprocess.run(["quickshell", "ipc", "-p", shell, "call", "desk", *call], env=env, text=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10).stdout.strip()
            except subprocess.SubprocessError:
                return None

        def view():
            try:
                return json.loads(ipc("search_view") or "")
            except ValueError:
                return None

        primer = itertools.cycle(["Shift_L", "Control_L"])

        def type_keys(*args):
            """Niri does not resend a virtual keymap equal to the last one, so a shell started since would read
            wtype's keys with the physical keymap ("Notes" as "12345"). A lone modifier, Shift and Control in
            turn, makes each call's keymap differ from the one before (as niri_focus_walk_smoke.py does)."""
            subprocess.run(["wtype", "-k", next(primer), *args], env=typing_env, check=True, timeout=10)
            time.sleep(0.3)

        def opened_lines():
            return opened.read_text().splitlines() if opened.exists() else []

        def open_search():
            ipc("search")
            wait_for(lambda: ipc("search_state") == "open", "search to open")
            time.sleep(0.4)  # the panel takes the keyboard when it maps

        try:
            shell_process = subprocess.Popen(["quickshell", "-p", shell], env=env, stdin=subprocess.DEVNULL,
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            wait_for(lambda: ipc("search_state") == "closed", "the development shell", timeout=20)
            # A keymap no call below sends: the first call's keymap then differs from it, even when an earlier
            # run stopped right after sending that same first call.
            subprocess.run(["wtype", "-k", "Hyper_L"], env=typing_env, check=True, timeout=10)

            open_search()
            shown = wait_for(lambda: (v := view()) and len(v["rows"]) == len(ROWS) and v, lambda: f"the results ({view()})")
            if [r["title"] for r in shown["rows"]] != [r["title"] for r in ROWS] or shown["message"]:
                raise RuntimeError(f"The panel did not list flow-search's results in order: {shown}")
            steps.append("the panel listed flow-search's results in their order")

            type_keys("Notes")
            ranked = wait_for(lambda: (v := view()) and len(v["rows"]) == 8 and v, lambda: f"Notes results ({view()})")
            expected = ([("app", "Notes"), ("layer", "Notes")] + [("app", a) for a in APPS if a != "Notes"]
                        + [("layer", a) for a in APPS if a != "Notes"])
            if [(r["kind"], r["title"]) for r in ranked["rows"]] != expected:
                raise RuntimeError(f"Search ranked {[(r['kind'], r['title']) for r in ranked['rows']]}, expected {expected}")
            type_keys("-k", "Return")
            wait_for(lambda: opened_lines(), "the chosen result to open")
            wait_for(lambda: ipc("search_state") == "closed", "search to close after opening")
            if opened_lines() != ["app /apps/Notes.desktop"]:
                raise RuntimeError(f"Return opened {opened_lines()}, expected the Notes app")
            steps.append("an exact title ranked first and ties kept flow-search's order; Return opened the Notes app "
                         "through flow-search --open")

            open_search()
            wait_for(lambda: (v := view()) and v["rows"], "the results again")
            type_keys("zzqx")
            wait_for(lambda: (v := view()) and v["message"] == "Nothing matches “zzqx”" and not v["rows"],
                     lambda: f"the no-match message ({view()})")
            if not view()["message_clear"]:
                raise RuntimeError("The no-match message runs into the panel's footer")
            type_keys("-k", "Escape")
            wait_for(lambda: ipc("search_state") == "closed", "Escape to close search")
            steps.append("a query that matched nothing said so, above the footer")

            (tmp / "open-exit").write_text("3")
            open_search()
            wait_for(lambda: (v := view()) and v["rows"], "the results again")
            type_keys("Notes")
            wait_for(lambda: (v := view()) and v["rows"] and v["rows"][0]["title"] == "Notes", "Notes first")
            type_keys("-k", "Return")
            wait_for(lambda: ipc("toast") == "Couldn’t open Notes", lambda: f"the failure toast ({ipc('toast')!r})")
            steps.append("a result that failed to open showed a toast naming it")

            (tmp / "fail-list").touch()
            open_search()
            wait_for(lambda: (v := view()) and v["message"].startswith("Search couldn’t list anything"),
                     lambda: f"the failure message ({view()})")
            type_keys("-k", "Escape")
            wait_for(lambda: ipc("search_state") == "closed", "Escape to close search")
            steps.append("a flow-search that did not answer with results showed that search failed")

            # flow-search that cannot start, as gio could not: a process that never starts must not be silent either.
            # Its interpreter is missing, so the exec fails; deleting the stand-in would only reach another
            # flow-search further down PATH.
            (tmp / "fail-list").unlink()
            (tmp / "open-exit").write_text("0")
            open_search()
            wait_for(lambda: (v := view()) and v["rows"], "the results again")
            type_keys("Notes Viewer")
            wait_for(lambda: (v := view()) and v["rows"] and v["rows"][0]["title"] == "Notes Viewer", "Notes Viewer first")
            (stand_in / "flow-search").write_text("#!/nonexistent/sh\n")
            type_keys("-k", "Return")
            wait_for(lambda: ipc("toast") == "Couldn’t open Notes Viewer", lambda: f"the toast for a missing flow-search ({ipc('toast')!r})")
            open_search()
            wait_for(lambda: (v := view()) and v["message"].startswith("Search couldn’t list anything"),
                     lambda: f"the failure message for a missing flow-search ({view()})")
            type_keys("-k", "Escape")
            steps.append("with a flow-search that cannot start, opening a result showed a toast and listing said search failed")
            result = {"result": "pass", "steps": steps}
        finally:
            if shell_process and shell_process.poll() is None:
                shell_process.terminate()
                try:
                    shell_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    shell_process.kill()
    if [digest(path) for path in real_files] != real_digests:
        raise RuntimeError("A real Flow Group, pin, or session file changed")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, KeyError, subprocess.SubprocessError, ValueError) as exc:
        print(f"Search test failed: {exc}", file=sys.stderr)
        sys.exit(1)
