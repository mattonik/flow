#!/usr/bin/env python3
"""The opt-in app menu in a development shell, driven with the pointer: the App button shows only for a window
whose menu the resident reader (``flow-appmenu serve``) can read, and its commands reach the right window.

Run it with ``tests/run_appmenu_smoke.sh`` (see there) inside a Niri session where Flow is installed, with the
installed shell stopped. The shell runs with its own runtime directory, state and configuration, the app menu on,
on a private session bus whose accessibility bus and switch are the run's own. Kate and DB Browser for SQLite come
from the guest's pinned nixpkgs (``FLOW_NIX``, ``FLOW_NIX_ARGS``; ``FLOW_NIXPKGS`` names another flake), and run
with a home and XDG folders of their own. Their ``share`` and ``bin`` folders are on the shell's
``XDG_DATA_DIRS`` and ``PATH``, as an installed app's would be. Everything happens on the focused display's empty
workspace; the run's windows are closed and its processes stopped, also when it fails.

Checked:
- the reader switches the run's accessibility on and records that Flow did;
- Kate focused: the App button shows, and covers none of Kate's own menu bar (its left edge is right of every
  top-level item's right edge, read over AT-SPI; Kate's window spans the output); foot focused: it is hidden;
- Help > About Kate, chosen from the button's menu, opens Kate's About dialog;
- two Kate windows of one process with one title: File > New chosen for the focused one changes only that one,
  each way round (the frame is chosen when focus settles, before the menu's surface takes the keyboard);
- DB Browser's Help > About (a modal dialog, which answers only when it closes): the menu closes within 2 s,
  with no error, and the dialog opens;
- Super+M (the ``menu`` IPC call it runs) on foot: a "No app menu" toast, and no menu;
- one reader process the whole time;
- turning the setting off stops the reader and switches accessibility off again, since Flow switched it on; with
  accessibility already on before the setting, turning it off leaves it on.

``--shots DIR`` saves screenshots of the button shown (Kate) and hidden (foot), and of Kate's menu.
Prints one JSON line per check and a final ``{"result": ...}``; exits 1 if any check failed.
"""

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from isolated_state import IsolatedState, started_with  # noqa: E402
from pointer import Pointer  # noqa: E402

A11Y = ["org.a11y.Bus", "/org/a11y/bus", "org.a11y.Status", "IsEnabled"]
MARK = "appmenu-switched-accessibility-on"  # session/appmenu.py's marker()


def niri(*args):
    return subprocess.check_output(["niri", "msg", *map(str, args)], text=True, timeout=10)


def read(kind):
    return json.loads(niri("-j", kind))


def windows():
    return {w["id"]: w for w in read("windows")}


def wait_for(test, label, timeout=15, step=0.1):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = test()
        if value:
            return value
        time.sleep(step)
    raise RuntimeError(f"Timed out waiting for {label}")


def store_path(attr):
    nix = shlex.split(os.environ.get("FLOW_NIX", "nix"))
    extra = shlex.split(os.environ.get("FLOW_NIX_ARGS", ""))
    ref = f"{os.environ.get('FLOW_NIXPKGS', 'nixpkgs')}#{attr}^out"
    return subprocess.check_output([*nix, "build", "--no-link", "--print-out-paths", *extra, ref], text=True).split()[0]


def a11y():
    """The run's accessibility switch: the private bus's, never the session's."""
    out = subprocess.run(["busctl", "--user", "get-property", *A11Y], text=True, capture_output=True, timeout=10)
    return out.stdout.strip()


def set_a11y(on):
    subprocess.run(["busctl", "--user", "set-property", *A11Y, "b", "true" if on else "false"], check=True, timeout=10)


def kate_menu_ends(pid):
    """The right edges of the top-level items in Kate's own menu bar, in its window (x 0 is the window's left)."""
    import gi
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi
    import pyatspi
    desktop = pyatspi.Registry.getDesktop(0)
    for app in (desktop.getChildAtIndex(i) for i in range(desktop.childCount)):
        if app is None or app.get_process_id() != pid:
            continue
        queue = [app]
        while queue:
            node = queue.pop(0)
            for child in (node.getChildAtIndex(i) for i in range(node.childCount)):
                if child is None:
                    continue
                if child.getRole() == pyatspi.ROLE_MENU_BAR and child.getState().contains(pyatspi.STATE_SHOWING):
                    ends = []
                    for item in (child.getChildAtIndex(i) for i in range(child.childCount)):
                        # Asked directly: pyatspi's queryComponent() refuses Qt's menu items (NotImplementedError).
                        box = Atspi.Component.get_extents(item, Atspi.CoordType.WINDOW) if item is not None else None
                        if box is not None and box.width > 0:
                            ends.append(box.x + box.width)
                    return ends
                if len(queue) < 200:
                    queue.append(child)
    return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", type=Path)
    args = parser.parse_args()
    shell = os.environ.get("FLOW_SHELL")
    if not shell or not os.environ.get("FLOW_APPMENU_SMOKE_BUS"):
        raise RuntimeError("Run with tests/run_appmenu_smoke.sh: it gives the run a private session bus")
    session_bus = subprocess.run(["systemctl", "--user", "show-environment"], text=True, capture_output=True).stdout
    if f"DBUS_SESSION_BUS_ADDRESS={os.environ.get('DBUS_SESSION_BUS_ADDRESS')}" in session_bus.splitlines():
        raise RuntimeError("This is the session's own bus: the reader would switch its accessibility on")
    if subprocess.run(["systemctl", "--user", "is-active", "--quiet", "flow-shell"]).returncode == 0:
        raise RuntimeError("Stop the installed shell first: the pointer would reach its surfaces")
    roots = {name: store_path(attr) for name, attr in (("kate", "kdePackages.kate"), ("dbb", "sqlitebrowser"))}
    start_workspace = next(w for w in read("workspaces") if w["is_focused"])
    output = start_workspace["output"]
    empty = max((w for w in read("workspaces") if w["output"] == output), key=lambda w: w["idx"])
    if any(w["workspace_id"] == empty["id"] for w in read("windows")):
        raise RuntimeError("The focused display has no empty workspace")
    run = uuid.uuid4().hex
    results, shell_process, own_windows = [], None, set()

    def check(name, ok, **detail):
        results.append(bool(ok))
        print(json.dumps({"check": name, "ok": bool(ok), **detail}), flush=True)

    with IsolatedState("flow-appmenu-smoke-") as tmp:
        tmp = Path(tmp)
        for sub in ("config", "home", "app-config", "data", "cache", "shots"):
            (tmp / sub).mkdir()
        (tmp / "config" / "flow-settings.ini").write_text("[General]\nappMenuEnabled=true\nsessionRecovery=false\n")  # automatic recovery is on by default; not this smoke's subject
        apps_bin = ":".join(f"{root}/bin" for root in roots.values())
        apps_share = ":".join(f"{root}/share" for root in roots.values())
        env = dict(os.environ, XDG_STATE_HOME=str(tmp), XDG_CONFIG_HOME=str(tmp / "config"),
                   PATH=f"{os.environ['PATH']}:{apps_bin}", XDG_DATA_DIRS=f"{os.environ.get('XDG_DATA_DIRS', '')}:{apps_share}",
                   FLOW_APPMENU_SMOKE_RUN=run)
        app_env = {k: v for k, v in env.items() if k not in ("GI_TYPELIB_PATH", "PYTHONPATH")}
        app_env.update(HOME=str(tmp / "home"), XDG_CONFIG_HOME=str(tmp / "app-config"), XDG_DATA_HOME=str(tmp / "data"),
                       XDG_CACHE_HOME=str(tmp / "cache"))

        def ipc(*call):
            return subprocess.run(["quickshell", "ipc", "-p", shell, "call", "desk", *map(str, call)], env=env, text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10).stdout.strip()

        def state():
            try:
                return json.loads(ipc("app_menu"))
            except ValueError:
                return {}

        def target(name):
            try:
                return json.loads(ipc("pointer_target", name) or "null")
            except ValueError:
                return None

        def launch(argv):
            known = set(windows())
            process = subprocess.Popen(argv, env=app_env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, start_new_session=True)
            ident = wait_for(lambda: next((i for i, w in windows().items() if i not in known and w.get("pid") == process.pid), None),
                             f"a window of {argv[0]}", timeout=90)
            own_windows.add(ident)
            return process, ident

        def new_window(pid, known, label, timeout=10):
            ident = wait_for(lambda: next((i for i, w in windows().items() if i not in known and w.get("pid") == pid), None),
                             label, timeout=timeout)
            own_windows.add(ident)
            return ident

        def focus(ident):
            niri("action", "focus-window", "--id", ident)
            wait_for(lambda: state().get("window") == ident and state().get("probe") is not None, f"a probe of window {ident}")

        def shot(name):
            if args.shots:
                time.sleep(0.4)
                subprocess.run(["grim", str(tmp / "shots" / name)], env=env, check=True, timeout=20)

        def choose(pattern, label):
            """In the open menu, the first row whose label matches, by the pointer."""
            labels = wait_for(lambda: (s := state()).get("open") and any(re.search(pattern, l) for l in s["labels"]) and s["labels"],
                              f"{label} in the menu")
            index = next(i for i, l in enumerate(labels) if re.search(pattern, l))
            pointer.click(pointer.hover(lambda: target(f"app_item:{index}"), label))

        def open_menu():
            pointer.click(pointer.hover(lambda: target("app_button"), "the App button"))
            wait_for(lambda: state().get("open"), "the menu to open")

        def run_command(*path):
            open_menu()
            for pattern in path:
                choose(pattern, pattern)

        try:
            niri("action", "focus-workspace", empty["idx"])
            before = a11y()
            check("the run's accessibility starts off", before == "b false", value=before)
            shell_log = open(tmp / "shell.log", "w")
            shell_process = subprocess.Popen(["quickshell", "-p", shell], env=env, stdin=subprocess.DEVNULL,
                                             stdout=shell_log, stderr=subprocess.STDOUT)
            wait_for(lambda: ipc("desk_state") == "closed", "the development shell", timeout=30)
            pointer = Pointer(env)
            wait_for(lambda: state().get("reader"), "the reader to start")
            readers = wait_for(lambda: started_with(f"FLOW_APPMENU_SMOKE_RUN={run}") and
                               [p for p in started_with(f"FLOW_APPMENU_SMOKE_RUN={run}") if b"appmenu.py" in Path(f"/proc/{p}/cmdline").read_bytes()],
                               "the reader process")
            switched = wait_for(lambda: a11y() == "b true", "accessibility to come on", timeout=10)
            check("the reader switches accessibility on and records it", switched and (tmp / "flow" / MARK).exists(),
                  value=a11y(), marked=(tmp / "flow" / MARK).exists())

            # Kate and foot.
            kate, kate_a = launch([f"{roots['kate']}/bin/kate", "-n"])
            focus(kate_a)
            shown = wait_for(lambda: (s := state()).get("button") and s, "the App button for Kate", timeout=15)
            check("Kate focused: the App button shows", shown["button"], probe=shown["probe"])
            shot("1-button-shown-kate.png")
            button, ends, width = target("app_button"), kate_menu_ends(kate.pid), windows()[kate_a]["layout"]["window_size"][0]
            left = button["screen"][0] + button["place"]["margins"][2] if button else None
            check("the App button covers none of Kate's own menus", button and ends and width == button["screen"][2]
                  and left > max(ends), button_left=left, menu_item_right_edges=ends, window_width=width)
            foot, foot_window = launch(["foot", "-T", "app menu smoke"])
            focus(foot_window)
            time.sleep(1)
            hidden = state()
            check("foot focused: no App button", hidden.get("probe") and not hidden["button"], probe=hidden.get("probe"))
            shot("2-button-hidden-foot.png")
            ipc("menu")  # what Super+M runs
            toast = wait_for(lambda: ipc("toast") or None, "a toast", timeout=3)
            check("Super+M on foot: a 'No app menu' toast and no menu", toast == "No app menu" and not state().get("open"),
                  toast=toast)

            # Help > About Kate, from the button.
            focus(kate_a)
            wait_for(lambda: state().get("button"), "the App button for Kate")
            known = set(windows())
            wait_for(lambda: not ipc("toast"), "the toast to go", timeout=5)  # out of the screenshot
            open_menu()
            shot("3-menu-kate.png")
            choose("^Help$", "Help")
            choose("^About Kate", "About Kate")
            closed = wait_for(lambda: not state().get("open"), "the menu to close", timeout=5)
            about = new_window(kate.pid, known, "Kate's About dialog")
            check("Help > About Kate opens Kate's About dialog", closed and "About" in windows()[about]["title"],
                  title=windows()[about]["title"])
            niri("action", "close-window", "--id", about)
            wait_for(lambda: about not in windows(), "the About dialog to close")

            # Two Kate windows of one process, with one title.
            focus(kate_a)
            known = set(windows())
            run_command("^File$", "^New Window")
            kate_b = new_window(kate.pid, known, "Kate's second window")
            wait_for(lambda: windows()[kate_b]["title"] == windows()[kate_a]["title"], "both Kate windows to share a title")
            focus(kate_b)
            for chosen, other in ((kate_a, kate_b), (kate_b, kate_a)):
                focus(chosen)
                wait_for(lambda: state().get("button"), "the App button for Kate")
                titles = {i: windows()[i]["title"] for i in (kate_a, kate_b)}
                run_command("^File$", "^New$")
                try:
                    wait_for(lambda: [i for i in titles if windows()[i]["title"] != titles[i]], "a Kate title to change", timeout=5)
                except RuntimeError:  # the command went nowhere: the check below fails with what the menu says
                    pass
                time.sleep(1)
                changed = [i for i in titles if windows()[i]["title"] != titles[i]]
                error = state().get("error")
                if state().get("open"):  # still open with an error: close it for the next step
                    ipc("menu")
                check(f"File > New in Kate window {'A' if chosen == kate_a else 'B'} changes only that window",
                      changed == [chosen], changed=changed, chosen=chosen, error=error, before=titles,
                      after={i: windows()[i]["title"] for i in titles})

            # DB Browser's modal About.
            dbb, dbb_window = launch([f"{roots['dbb']}/bin/sqlitebrowser"])
            focus(dbb_window)
            wait_for(lambda: state().get("button"), "the App button for DB Browser", timeout=15)
            known = set(windows())
            open_menu()
            choose("^Help$", "Help")
            labels = wait_for(lambda: state().get("labels"), "Help's items")
            index = next(i for i, l in enumerate(labels) if re.search("^&?About", l))
            at = pointer.hover(lambda: target(f"app_item:{index}"), "About")
            ipc("toast")
            clicked = time.monotonic()
            pointer.click(at)
            wait_for(lambda: not state().get("open"), "the menu to close", timeout=10, step=0.05)
            took = time.monotonic() - clicked
            dialog = new_window(dbb.pid, known, "DB Browser's About dialog")
            after = state()
            check("DB Browser's modal About: the menu closes within 2 s, with no error", took < 2 and not after.get("error")
                  and not ipc("toast"), seconds=round(took, 2), error=after.get("error"), dialog=windows()[dialog]["title"])
            niri("action", "close-window", "--id", dialog)
            wait_for(lambda: dialog not in windows(), "the About dialog to close")

            now = [p for p in started_with(f"FLOW_APPMENU_SMOKE_RUN={run}") if b"appmenu.py" in Path(f"/proc/{p}/cmdline").read_bytes()]
            check("one reader process the whole time", now == readers, first=readers, now=now)

            # The setting off: the reader stops and accessibility goes off again, as Flow switched it on.
            ipc("app_menu_enabled", "false")
            wait_for(lambda: not state().get("reader"), "the reader to stop")
            off = wait_for(lambda: a11y() == "b false" and a11y(), "accessibility to go off", timeout=10)
            check("setting off: accessibility off again, since Flow switched it on",
                  off == "b false" and not (tmp / "flow" / MARK).exists(), value=off)
            set_a11y(True)  # now the user's own
            ipc("app_menu_enabled", "true")
            wait_for(lambda: state().get("reader"), "the reader to start again")
            time.sleep(2)
            ipc("app_menu_enabled", "false")
            wait_for(lambda: not state().get("reader"), "the reader to stop again")
            time.sleep(2)
            check("setting off: accessibility switched on by the user stays on", a11y() == "b true", value=a11y())
        except Exception:
            print(json.dumps({"state": state(), "targets": {name: target(name) for name in ["app_button"] + [f"app_item:{i}" for i in range(12)]},
                              "outputs": {k: v.get("logical") for k, v in json.loads(niri("-j", "outputs")).items()},
                              "shell_log": (tmp / "shell.log").read_text()[-4000:]}), flush=True)
            raise
        finally:
            for ident in own_windows:
                subprocess.run(["niri", "msg", "action", "close-window", "--id", str(ident)], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=10)
            if shell_process:
                shell_process.terminate()
                try:
                    shell_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    shell_process.kill()
            time.sleep(1)
            for sig in (signal.SIGTERM, signal.SIGKILL):
                for pid in started_with(f"FLOW_APPMENU_SMOKE_RUN={run}"):
                    try:
                        os.kill(pid, sig)
                    except OSError:
                        pass
                time.sleep(1)
            niri("action", "focus-workspace", start_workspace["idx"])
            if args.shots:
                args.shots.mkdir(parents=True, exist_ok=True)
                for path in (tmp / "shots").iterdir():
                    shutil.copy(path, args.shots / path.name)
    passed = all(results)
    print(json.dumps({"result": "pass" if passed else "fail"}))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
