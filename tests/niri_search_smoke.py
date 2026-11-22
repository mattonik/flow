#!/usr/bin/env python3
"""The search panel of a development shell: it shows results, opens the chosen one and says why when it can't.

Run inside a Niri session from the Flow checkout, with ``FLOW_SHELL`` set to the development ``shell.qml``,
``wtype`` and ``flow-pointer`` on PATH and the installed shell stopped (``systemctl --user stop flow-shell``: its keyboard
surfaces would take the keys). The shell gets its own runtime directory, state and configuration, and a
stand-in ``flow-search`` first on PATH that lists fixed results and records what it is asked to open, so
no app starts. Keys are typed with wtype into the panel, which has the keyboard while open.

Checked, through the shell's ``search_view`` and ``toast`` IPC calls and the stand-in's log:
- the panel lists what ``flow-search --json`` answers;
- search opened again while a slow listing still runs says it is looking, then shows the new listing,
  never the old one's answer (what was there before: windows since closed), and the old listing is stopped;
  one that ignores being stopped and answers after the new one is dropped too;
- typing ranks an exact title first and keeps ties in flow-search's order, so Return on "Notes" opens
  the Notes app, not "Notes Server" or Notes as a Layer, through ``flow-search --open app <desktop file>``;
- with the pointer (tests/pointer.py), the row under it is tinted, a click opens that row rather than
  the chosen one, and a click on the shade closes search;
- a query that matches nothing says so, in its own space above the footer;
- typing three letters or more walks the user folders for files by name (``flow-search --files``); a found file
  is listed and opens; a slow walk for an older query is dropped; the row chosen with the arrow keys stays
  chosen when a walk's answer arrives;
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

from pointer import Pointer

# Enough ties that an unstable sort shows (the "Foot" query in the guest had six): every app twice.
APPS = ["Notes Client", "Notes Server", "Notes", "Notes Viewer"]
ROWS = ([{"title": "Flow Settings", "detail": "Settings", "kind": "settings", "value": "settings", "icon": ""}]
        + [{"title": a, "detail": "Application", "kind": "app", "value": f"/apps/{a}.desktop", "icon": ""} for a in APPS]
        + [{"title": a, "detail": "Open as Layer", "kind": "layer", "value": f"{a}.desktop", "icon": ""} for a in APPS])
# What a slow listing saw before search was opened again: a window since closed.
STALE_ROWS = [{"title": "Closed Window", "detail": "Open window · foot", "kind": "window", "value": "7", "icon": ""}]
# Files a walk finds by name (flow-search --files): one, and an older walk's answer, since deleted.
def file_row(name):
    return {"title": name, "detail": "File · /home/floe/Documents", "kind": "file",
            "value": "file:///home/floe/Documents/" + name.replace(" ", "%20"), "icon": ""}


STAND_IN = """#!/bin/sh
# Flow search, standing in: --json answers with the rows in $DIR/rows as they were when it started, after
# the seconds in $DIR/slow if that exists, and counts the listings it finished in $DIR/listed. It ignores
# SIGTERM when $DIR/ignore-term exists, and fails when $DIR/fail-list does. --open records its arguments
# and exits with the code in $DIR/open-exit.
DIR={dir}
case "$1" in
  --json) [ -e "$DIR/fail-list" ] && {{ echo "usage: flow-search" >&2; exit 2; }}
          [ -e "$DIR/ignore-term" ] && trap '' TERM
          rows=$(cat "$DIR/rows"); [ -e "$DIR/slow" ] && sleep "$(cat "$DIR/slow")"; printf '%s\n' "$rows"
          echo listed >> "$DIR/listed" ;;
  --files) echo "$2" >> "$DIR/walked"; rows=$(cat "$DIR/files" 2>/dev/null || echo '[]')
           [ -e "$DIR/walk-slow" ] && sleep "$(cat "$DIR/walk-slow")"; printf '%s\n' "$rows" ;;
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

        def target(name):
            try:
                return json.loads(ipc("pointer_target", name) or "null")
            except ValueError:
                return None

        def opened_lines():
            return opened.read_text().splitlines() if opened.exists() else []

        def open_search():
            ipc("search")
            wait_for(lambda: ipc("search_state") == "open", "search to open")
            time.sleep(0.4)  # the panel takes the keyboard when it maps

        pointer = Pointer()
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

            # A listing still running when search opens again answers with what was there when it began.
            def close_search():
                ipc("search")
                wait_for(lambda: ipc("search_state") == "closed", "search to close")

            def reopen_during_slow_listing(outlives_stop):
                close_search()
                (tmp / "listed").unlink(missing_ok=True)
                (tmp / "rows").write_text(json.dumps(STALE_ROWS))
                (tmp / "slow").write_text("4")
                if outlives_stop:
                    (tmp / "ignore-term").touch()
                open_search()
                wait_for(lambda: (v := view()) and v["message"] == "Looking…", lambda: f"the slow listing ({view()})")
                close_search()
                (tmp / "ignore-term").unlink(missing_ok=True)
                (tmp / "rows").write_text(json.dumps(ROWS))
                (tmp / "slow").write_text("2")  # the new listing is slow too: until it answers, search is looking
                open_search()
                (tmp / "slow").unlink()
                seen, end = [], time.monotonic() + 6  # past the end of the first listing
                while time.monotonic() < end:
                    seen.append(view())
                    time.sleep(0.2)
                if not seen[0] or seen[0]["message"] != "Looking…":
                    raise RuntimeError(f"Search opened again during a slow listing did not say it was looking: {seen[0]}")
                for shown in seen:
                    if not shown or shown["message"] not in ("Looking…", "") or (
                            shown["rows"] and [r["title"] for r in shown["rows"]] != [r["title"] for r in ROWS]):
                        raise RuntimeError(f"Search opened again showed something other than looking or the new listing: {shown}")
                if not seen[-1]["rows"]:
                    raise RuntimeError(f"The new listing never showed: {seen[-1]}")
                # The new one's, and the old one's only when it ignored being stopped.
                listed = (tmp / "listed").read_text().split()
                if len(listed) != (2 if outlives_stop else 1):
                    raise RuntimeError(f"{len(listed)} listings finished: the slow one was {'not ' * (not outlives_stop)}stopped")

            reopen_during_slow_listing(False)
            steps.append("search opened again during a slow listing said it was looking, then showed the new listing, "
                         "not the old one's answer, and the old listing was stopped")
            # A listing that ignores SIGTERM answers after the new one: its answer is dropped too.
            reopen_during_slow_listing(True)
            steps.append("a slow listing that ignored being stopped answered after the new one, and its answer was dropped")

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

            # The pointer: the third row, not the chosen first one, so a click is told apart from Return.
            pointer.park()
            open_search()
            wait_for(lambda: (v := view()) and v["rows"], "the results again")
            type_keys("Notes")
            wait_for(lambda: (v := view()) and len(v["rows"]) == 8, lambda: f"Notes results ({view()})")
            row = pointer.hover(lambda: target("search_row:2"), "the third row")
            if any((target(f"search_row:{i}") or {}).get("hovered") for i in (1, 3)):
                raise RuntimeError("A row the pointer is not on was tinted")
            pointer.click(row)
            wait_for(lambda: len(opened_lines()) == 2, lambda: f"the clicked row to open ({opened_lines()})")
            wait_for(lambda: ipc("search_state") == "closed", "search to close after the click")
            if opened_lines()[-1] != "app /apps/Notes Client.desktop":
                raise RuntimeError(f"Clicking the third row opened {opened_lines()[-1]!r}, expected Notes Client")
            steps.append("with the pointer, only the row under it was tinted, and clicking the third row opened that "
                         "row (Notes Client), not the chosen first one")

            pointer.park()
            open_search()
            shade = wait_for(lambda: target("search_shade"), "the shade's place")
            # Above the palette (at least 64 px down) and at the screen's left edge, clear of it at any width.
            pointer.click((shade["screen"][0] + 4, shade["screen"][1] + 32))
            wait_for(lambda: ipc("search_state") == "closed", "a click on the shade to close search")
            if len(opened_lines()) != 2:
                raise RuntimeError(f"A click on the shade opened {opened_lines()[2:]}")
            steps.append("a click on the shade, outside the palette, closed search and opened nothing")
            pointer.park()

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

            def walked():
                return (tmp / "walked").read_text().splitlines() if (tmp / "walked").exists() else []

            # Files found by name while typing join the results, and Return opens the chosen one.
            (tmp / "files").write_text(json.dumps([file_row("Quarterly report.odt")]))
            open_search()
            wait_for(lambda: (v := view()) and v["rows"], "the results again")
            type_keys("quarterly")
            wait_for(lambda: (v := view()) and [r["title"] for r in v["rows"]] == ["Quarterly report.odt"],
                     lambda: f"the file found by name ({view()})")
            if not walked() or walked()[-1] != "quarterly" or any(len(q) < 3 for q in walked()):
                raise RuntimeError(f"Search walked for {walked()}, expected the typed query only")
            type_keys("-k", "Return")
            wait_for(lambda: opened_lines()[-1:] == ["file file:///home/floe/Documents/Quarterly%20report.odt"],
                     lambda: f"the found file to open ({opened_lines()[-1:]})")
            steps.append("typing walked the user folders for the query (never fewer than three letters), the file "
                         "found by name was listed, and Return opened it")

            # A slow walk for an older query is dropped: its file has since been deleted.
            (tmp / "files").write_text(json.dumps([file_row("Report notes (deleted).md")]))
            (tmp / "walk-slow").write_text("3")
            open_search()
            wait_for(lambda: (v := view()) and v["rows"], "the results again")
            type_keys("report")
            wait_for(lambda: walked()[-1:] == ["report"], "the walk for report")
            (tmp / "files").write_text(json.dumps([file_row("Report notes.md")]))
            (tmp / "walk-slow").unlink()
            type_keys(" notes")
            seen, end = [], time.monotonic() + 5  # past the end of the slow walk
            while time.monotonic() < end:
                seen.append([r["title"] for r in (view() or {"rows": []})["rows"]])
                time.sleep(0.2)
            if any("Report notes (deleted).md" in titles for titles in seen) or "Report notes.md" not in seen[-1]:
                raise RuntimeError(f"Search showed an older walk's file, or not the new one: {seen[-1]}")
            type_keys("-k", "Escape")
            wait_for(lambda: ipc("search_state") == "closed", "Escape to close search")
            steps.append("a slow walk for an older query was dropped: only the new query's file was shown")

            # The chosen row stays chosen when a walk's answer arrives and ranks above it.
            (tmp / "files").write_text(json.dumps([file_row("Notes")]))
            (tmp / "walk-slow").write_text("2")
            open_search()
            wait_for(lambda: (v := view()) and v["rows"], "the results again")
            type_keys("Notes")
            wait_for(lambda: (v := view()) and len(v["rows"]) == 8, lambda: f"Notes results ({view()})")
            type_keys("-k", "Down", "-k", "Down")  # Notes Client, the third row
            wait_for(lambda: (v := view()) and any(r["kind"] == "file" for r in v["rows"]), "the walk's file")
            (tmp / "walk-slow").unlink()
            type_keys("-k", "Return")
            wait_for(lambda: opened_lines()[-1:] == ["app /apps/Notes Client.desktop"],
                     lambda: f"the chosen row to open ({opened_lines()[-1:]})")
            steps.append("the row chosen with the arrow keys stayed chosen when a file found later ranked above it, "
                         "and Return opened it")

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
