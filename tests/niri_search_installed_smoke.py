#!/usr/bin/env python3
"""Search through the INSTALLED shell and the installed ``flow-search``, with no stand-ins (F-008).

Run inside a Niri session on the installed system, from a copy of this checkout (``tests/run_search_installed_smoke.sh``
takes the guest's screen lock and does the rest). The installed shell (``/etc/flow/shell.qml``, or the shell named
by ``FLOW_SHELL``) must be running; its IPC reads what is on screen and ``flow-pointer`` clicks. Keys are typed
with wtype into the search panel, which has the keyboard while open. ``--only NAME`` runs one check (names below),
which the mutation runner uses.

Each check opens search, types, and presses Return or clicks; what it asserts is the effect on the system:

- ``apps``: "Foot" then Return opens a real Foot window, in an app scope of its own, not in the shell's unit;
- ``click``: with the second row chosen (Down), a click on the first row (pointer, ``pointer_target``) opens that
  row, a tiled Foot window, and not the chosen Layer;
- ``spaces``: a pinned app and an unpinned one, each searched by name and chosen with Return, are focused, and the
  windows' columns, sizes and workspace stay as they were; ``closed-pin``: the pinned app, closed, is launched
  again by Return on its row, in an app scope, and the other windows keep their order and sizes;
- ``settings``: Flow Settings opens Desk with its Settings section expanded; ``quick``: Quick Settings opens
  that panel. Both are closed again, and Settings is left collapsed as it was found;
- ``recent``: Mousepad opens a disposable file outside the user folders, so Mousepad itself records it in
  ``recently-used.xbel``, with no hand-written history. Search then finds it by name, as a recent file and not as
  a found one, and Return opens it, in an app scope, in Mousepad.

State it touches and restores: the test's windows; one pin (unpinned at the end); ``recently-used.xbel``,
``~/.config/mimeapps.list`` and Mousepad's dconf keys; temporary desktop entries and a folder under ``$HOME``;
the focused workspace. The pins, recent-file list and mime defaults are compared with how they were found.
"""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pointer import Pointer, center  # noqa: E402

CHECKS = ["apps", "click", "spaces", "closed-pin", "settings", "quick", "recent"]
INSTALLED_SHELL = "/etc/flow/shell.qml"
NO_SCOPE = re.compile(r"flow-shell\.service|session-\d+\.scope|init\.scope")


def wait_for(test, label, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = test()
        if value:
            return value
        time.sleep(0.2)
    raise RuntimeError(f"Timed out waiting for {label() if callable(label) else label}")


def niri(*args, **kwargs):
    return subprocess.run(["niri", "msg", *map(str, args)], text=True, stdout=subprocess.PIPE, timeout=10,
                          check=True, **kwargs).stdout


def read(kind):
    return json.loads(niri("-j", kind))


def windows():
    return {w["id"]: w for w in read("windows")}


def focused_workspace():
    return next(w for w in read("workspaces") if w["is_focused"])


def own_scope(pid):
    """The cgroup unit the process runs in: an app scope of its own, never the shell's unit or the login session."""
    cgroup = Path(f"/proc/{pid}/cgroup").read_text().strip().splitlines()[-1].split(":", 2)[-1]
    if NO_SCOPE.search(cgroup) or not cgroup.rsplit("/", 1)[-1].endswith(".scope"):
        raise RuntimeError(f"Process {pid} runs in {cgroup}, not an app scope of its own")
    return cgroup.rsplit("/", 1)[-1]


class Run:
    def __init__(self, shell):
        self.shell = shell
        self.env = dict(os.environ)
        self.keys = 0
        self.steps = []
        self.token = uuid.uuid4().hex[:8]
        self.opened = []  # windows this run made: closed at the end
        self.processes = []
        self.cleanups = []
        self.pointer = None

    # --- the shell ---
    def ipc(self, *call):
        try:
            return subprocess.run(["quickshell", "ipc", "-p", self.shell, "call", "desk", *map(str, call)], text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10).stdout.strip()
        except subprocess.SubprocessError:
            return None

    def json(self, *call):
        try:
            return json.loads(self.ipc(*call) or "null")
        except ValueError:
            return None

    def view(self):
        return self.json("search_view")

    def surfaces(self):
        return self.json("surfaces")

    def type_keys(self, *args):
        """Niri does not resend a virtual keymap equal to the last one: a lone modifier, Shift and Control in turn,
        makes each call's keymap differ from the one before (as niri_search_smoke.py does)."""
        self.keys += 1
        subprocess.run(["wtype", "-k", ("Shift_L", "Control_L")[self.keys % 2], *args], check=True, timeout=10)
        time.sleep(0.3)

    def open_search(self):
        wait_for(lambda: self.surfaces() and not any(self.surfaces().values()), "every shell surface closed")
        self.ipc("search")
        wait_for(lambda: self.ipc("search_state") == "open", "search to open")
        wait_for(lambda: (v := self.view()) and v["rows"], "the listing")
        time.sleep(0.4)  # the panel takes the keyboard when it maps

    def search(self, query):
        self.open_search()
        self.type_keys(query)
        return wait_for(lambda: (v := self.view()) and v["rows"] and v, lambda: f"results for {query!r}: {self.view()}")

    def close_search(self):
        if self.ipc("search_state") != "closed":
            self.type_keys("-k", "Escape")
            wait_for(lambda: self.ipc("search_state") == "closed", "search to close")

    def chosen_closed(self):
        wait_for(lambda: self.ipc("search_state") == "closed", "search to close after the choice")

    # --- windows ---
    def new_window(self, before, app_id=None, title=None, timeout=15):
        """The one window that is not in ``before`` (a set of IDs), optionally of an app ID, or whose title holds ``title``."""
        def found():
            fresh = [w for i, w in windows().items() if i not in before
                     and (app_id is None or w["app_id"] == app_id) and (title is None or title in (w["title"] or ""))]
            return fresh[0] if len(fresh) == 1 else None
        window = wait_for(found, f"a new window ({app_id or title})", timeout)
        self.opened.append(window["id"])  # closed at the end, if still open
        return window

    def spawn(self, app_id, title):
        before = set(windows())
        self.processes.append(subprocess.Popen(["foot", "--app-id", app_id, "-T", title, "sleep", "3600"],
                                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                               stderr=subprocess.DEVNULL))
        return self.new_window(before, app_id)

    def close_window(self, window_id):
        if window_id in windows():
            niri("action", "close-window", "--id", window_id)
            wait_for(lambda: window_id not in windows(), f"window {window_id} to close")

    def focus(self, window_id):
        niri("action", "focus-window", "--id", window_id)
        wait_for(lambda: focused_workspace()["active_window_id"] == window_id, f"focus on {window_id}")

    def mine(self, ids):
        """Columns, sizes and place of some windows: what a focus change must not touch."""
        found = windows()
        return {i: (found[i]["workspace_id"], tuple(found[i]["layout"]["pos_in_scrolling_layout"] or ()),
                    tuple(round(s) for s in found[i]["layout"]["tile_size"]), found[i]["is_floating"])
                for i in ids}

    def step(self, text):
        self.steps.append(text)

    # --- checks ---
    def check_apps(self):
        ws = focused_workspace()["id"]
        before = set(windows())
        rows = self.search("Foot")["rows"]
        if (rows[0]["kind"], rows[0]["title"]) != ("app", "Foot"):
            raise RuntimeError(f"Foot did not rank first: {[(r['kind'], r['title']) for r in rows[:3]]}")
        self.type_keys("-k", "Return")
        self.chosen_closed()
        window = self.new_window(before, "foot")
        if window["workspace_id"] != ws or window["is_floating"]:
            raise RuntimeError(f"Foot opened as {window}, not tiled on the focused workspace")
        scope = own_scope(window["pid"])
        self.close_window(window["id"])
        self.step(f"typing Foot, then Return, opened a Foot window (app ID foot) in its own scope {scope}")

    def check_click(self):
        before = set(windows())
        rows = self.search("Foot")["rows"]
        if (rows[0]["kind"], rows[1]["kind"], rows[1]["title"]) != ("app", "layer", "Foot"):
            raise RuntimeError(f"Foot, then Foot as a Layer, were not the first rows: {[(r['kind'], r['title']) for r in rows[:3]]}")
        self.type_keys("-k", "Down")  # the chosen row is the Layer: a click on the first row is told from Return
        pointer = self.pointer
        row = pointer.hover(lambda: self.json("pointer_target", "search_row:0"), "the first row")
        if (self.json("pointer_target", "search_row:1") or {}).get("hovered"):
            raise RuntimeError("The row the pointer is not on was tinted")
        pointer.click(row)
        self.chosen_closed()
        window = self.new_window(before, "foot")
        if window["is_floating"]:
            raise RuntimeError("The click opened Foot as a Layer, the chosen row, not the row it clicked")
        scope = own_scope(window["pid"])
        self.close_window(window["id"])
        pointer.park()
        self.step(f"with the second row chosen, a click on the first (Foot) opened a tiled Foot window in {scope}, not the Layer")

    def spaces_setup(self):
        """A pinned app, an unpinned one and a neutral one, side by side, with the neutral one focused."""
        t = self.token
        self.names = {k: f"F008 {k} {t}" for k in ("pinned", "unpinned")}
        self.ids = {k: f"flow-f008-{t}-{k}" for k in self.names}
        data = self.applications()
        entry = data / f"{self.ids['pinned']}.desktop"
        entry.write_text(f"[Desktop Entry]\nType=Application\nName={self.names['pinned']}\n"
                         f"Exec=foot --app-id {self.ids['pinned']} -T \"{self.names['pinned']}\" sleep 3600\n")
        self.cleanups.append(lambda: entry.unlink(missing_ok=True))
        self.win = {k: self.spawn(self.ids[k], self.names[k])["id"] for k in ("pinned", "unpinned")}
        self.win["other"] = self.base
        self.focus(self.win["pinned"])
        # flow-spaces pins the focused window; the pin is removed at the end.
        self.pinned_before = {p["id"] for p in self.observe()["pins"]}
        subprocess.run(["flow-spaces", "pin"], check=True, stdout=subprocess.DEVNULL, timeout=60)
        fresh = [p for p in self.observe()["pins"] if p["id"] not in self.pinned_before]
        if len(fresh) != 1:
            raise RuntimeError(f"Pinning the test app made {len(fresh)} pins")
        self.pin = fresh[0]["id"]
        self.cleanups.append(lambda: subprocess.run(["flow-spaces", "unpin", self.pin], stdout=subprocess.DEVNULL,
                                                    stderr=subprocess.DEVNULL, timeout=60))
        self.focus(self.win["other"])

    def applications(self):
        """The user's applications folder, made if it was not there and removed again if it is then empty."""
        folder = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "applications"
        if not folder.exists():
            folder.mkdir(parents=True)
            self.cleanups.append(lambda: folder.rmdir() if not any(folder.iterdir()) else None)
        return folder

    def observe(self):
        return json.loads(subprocess.check_output(["flow-spaces", "observe"], text=True, timeout=30))

    def check_spaces(self):
        if not hasattr(self, "win"):
            self.spaces_setup()
        ids = list(self.win.values())
        layout = self.mine(ids)
        for kind, label, detail in (("pin", "pinned", "Pinned space · Open"), ("window", "unpinned", "Open window")):
            self.focus(self.win["other"])
            rows = self.search(self.names[label])["rows"]
            if (rows[0]["kind"], rows[0]["title"]) != (kind, self.names[label]):
                raise RuntimeError(f"{label}: the first row was {[(r['kind'], r['title']) for r in rows[:3]]}, expected a {kind}")
            self.type_keys("-k", "Return")
            self.chosen_closed()
            wait_for(lambda: focused_workspace()["active_window_id"] == self.win[label], lambda: f"focus on the {label} window")
            if self.mine(ids) != layout:
                raise RuntimeError(f"Focusing the {label} app changed the layout: {layout} → {self.mine(ids)}")
        self.step("searching the pinned app (its pin) and the unpinned one (its window), then Return, focused each "
                  "window; every test window kept its column, size and workspace")

    def check_closed_pin(self):
        if not hasattr(self, "win"):
            self.spaces_setup()
        self.close_window(self.win["pinned"])
        self.focus(self.win["other"])
        rest = [self.win["unpinned"], self.win["other"]]
        before_ids, layout = set(windows()), self.mine(rest)
        order = lambda: sorted(rest, key=lambda i: windows()[i]["layout"]["pos_in_scrolling_layout"])
        order_before = order()
        rows = self.search(self.names["pinned"])["rows"]
        if (rows[0]["kind"], rows[0]["title"], rows[0]["armed"]) != ("pin", self.names["pinned"], False):
            raise RuntimeError(f"The closed pin was not the first row: {rows[:3]}")
        self.type_keys("-k", "Return")
        self.chosen_closed()
        window = self.new_window(before_ids, self.ids["pinned"], timeout=30)
        self.win["pinned"] = window["id"]
        wait_for(lambda: focused_workspace()["active_window_id"] == window["id"], "focus on the relaunched pinned app")
        scope = own_scope(window["pid"])
        if order() != order_before or {i: v[2:] for i, v in self.mine(rest).items()} != {i: v[2:] for i, v in layout.items()}:
            raise RuntimeError(f"Launching the pin reordered or resized the other windows: {layout} → {self.mine(rest)}")
        self.step(f"with the pinned app closed, its pin launched it on Return, in its own scope {scope}, and focused it; "
                  "the other windows kept their order, sizes and floating state")

    def desk_settings(self):
        """Whether Desk shows its Settings section: the Settings button is open (from the shell's controls)."""
        buttons = [c for c in self.json("controls") or [] if c["surface"] == "desk" and c["kind"] == "button" and c["text"] == "Settings"]
        return buttons[0] if buttons else None

    def settings_open(self):
        """True or False while Desk shows its Settings button, None while it does not."""
        button = self.desk_settings()
        return None if button is None else button["open"]

    def check_settings(self):
        pointer = self.pointer
        # Start from Desk with Settings collapsed, as the shell is when it has just started.
        self.ipc("toggle")
        wait_for(lambda: self.surfaces()["desk"] and self.settings_open() is not None, lambda: f"Desk with its Settings button ({self.surfaces()}, {self.desk_settings()})")
        if self.settings_open():
            pointer.click(self.control_center(self.desk_settings()))
            wait_for(lambda: self.settings_open() is False, "Settings to collapse")
        self.ipc("toggle")
        wait_for(lambda: not self.surfaces()["desk"], "Desk to close")
        self.search("Flow Settings")
        rows = self.view()["rows"]
        if (rows[0]["kind"], rows[0]["title"]) != ("settings", "Flow Settings"):
            raise RuntimeError(f"Flow Settings was not the first row: {rows[:3]}")
        self.type_keys("-k", "Return")
        self.chosen_closed()
        try:
            wait_for(lambda: self.surfaces()["desk"] and self.settings_open(),
                     lambda: f"Desk with Settings expanded ({self.surfaces()}, {self.desk_settings()})")
            switches = [c for c in self.json("controls") if c["surface"] == "desk" and c["kind"] == "switch"]
            if not switches:
                raise RuntimeError("Settings is open but shows no switch")
        finally:
            if self.settings_open():
                pointer.click(self.control_center(self.desk_settings()))
                wait_for(lambda: self.settings_open() is False, "Settings to collapse again")
            if self.surfaces()["desk"]:
                self.ipc("toggle")
                wait_for(lambda: not self.surfaces()["desk"], "Desk to close")
        pointer.park()
        self.step("Flow Settings, then Return, opened Desk with its Settings section expanded and its switches shown "
                  f"({', '.join(c['words'][0] if c['words'] else '?' for c in switches[:3])}…); both were closed again")

    def control_center(self, control):
        """A ``controls`` item as a pointer target (its screen is the focused output's)."""
        outputs = read("outputs")
        focused = next(w for w in read("workspaces") if w["is_focused"])["output"]
        logical = outputs[focused]["logical"]
        return center(dict(control, screen=[logical["x"], logical["y"], logical["width"], logical["height"]]))

    def check_quick(self):
        self.search("Quick Settings")
        rows = self.view()["rows"]
        if (rows[0]["kind"], rows[0]["title"]) != ("settings", "Quick Settings"):
            raise RuntimeError(f"Quick Settings was not the first row: {rows[:3]}")
        self.type_keys("-k", "Return")
        self.chosen_closed()
        try:
            wait_for(lambda: self.surfaces()["quick"], lambda: f"Quick Settings to open ({self.surfaces()})")
            shown = [c for c in self.json("controls") if c["surface"] == "quick"]
            if not shown:
                raise RuntimeError("Quick Settings is open but shows nothing")
            # A second Return does not toggle it shut: quick_open opens only.
        finally:
            if self.surfaces()["quick"]:
                self.ipc("quick")
                wait_for(lambda: not self.surfaces()["quick"], "Quick Settings to close")
        self.step(f"Quick Settings, then Return, opened the panel with {len(shown)} controls; it was closed again")

    def recent_setup(self):
        t = self.token
        editor = os.environ.get("F008_EDITOR") or next(iter(sorted(Path("/nix/store").glob("*-mousepad-*/bin/mousepad"))), None)
        if not editor or not Path(editor).exists():
            raise RuntimeError("No Mousepad: set F008_EDITOR to its binary")
        home = Path.home()
        folder = home / f"f008-{t}"  # not an XDG user folder: only the recent-file list can find a file here
        folder.mkdir()
        self.cleanups.append(lambda: shutil.rmtree(folder, ignore_errors=True))
        note = folder / f"f008-note-{t}.txt"
        note.write_text("a disposable note\n")
        data = Path(os.environ.get("XDG_DATA_HOME") or home / ".local/share")
        config = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
        self.backup(data / "recently-used.xbel")
        self.backup(config / "mimeapps.list")
        # Mousepad's own settings (window size, last search...) are written when a window closes: put them back.
        dump = subprocess.run(["dconf", "dump", "/org/xfce/mousepad/"], text=True, stdout=subprocess.PIPE).stdout \
            if shutil.which("dconf") else None
        if dump is not None:
            def restore_dconf():
                subprocess.run(["dconf", "reset", "-f", "/org/xfce/mousepad/"])
                if dump.strip():
                    subprocess.run(["dconf", "load", "/org/xfce/mousepad/"], input=dump, text=True)
            self.cleanups.append(restore_dconf)
        desktop = self.applications() / f"flow-f008-{t}-editor.desktop"
        desktop.write_text(f"[Desktop Entry]\nType=Application\nName=F008 Editor {t}\nExec={editor} %F\n"
                           "MimeType=text/plain;\n")
        self.cleanups.append(lambda: desktop.unlink(missing_ok=True))
        (config / "mimeapps.list").write_text(f"[Default Applications]\ntext/plain={desktop.name}\n")
        return editor, note, data / "recently-used.xbel"

    def backup(self, path):
        saved = path.read_bytes() if path.exists() else None
        def restore():
            if saved is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(saved)
        self.cleanups.append(restore)

    def check_recent(self):
        editor, note, history = self.recent_setup()
        before = set(windows())
        # As a user opens a file in an editor: the editor itself writes the recent-file list.
        process = subprocess.Popen([editor, str(note)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        window = self.new_window(before, "org.xfce.mousepad", timeout=30)
        wait_for(lambda: history.exists() and note.as_uri() in history.read_text(), "Mousepad to record the file", timeout=15)
        recorded = history.read_text()
        if "mousepad" not in recorded.lower():
            raise RuntimeError(f"The recent-file entry does not name Mousepad:\n{recorded}")
        self.close_window(window["id"])
        wait_for(lambda: process.poll() is not None, "Mousepad to exit")
        # The file is found by the recent-file list alone: it lies outside the folders search walks by name.
        before = set(windows())
        rows = self.search(note.name)["rows"]
        if [(r["kind"], r["title"]) for r in rows] != [("file", note.name)]:
            raise RuntimeError(f"Search for {note.name} showed {rows}")
        found = json.loads(subprocess.check_output(["flow-search", "--json"], text=True, timeout=30))
        if [r["detail"].split(" · ")[0] for r in found if r["title"] == note.name] != ["Recent file"]:
            raise RuntimeError("The file is not listed as a recent file by flow-search")
        self.type_keys("-k", "Return")
        self.chosen_closed()
        window = self.new_window(before, "org.xfce.mousepad", timeout=30)
        if note.name not in (window["title"] or ""):
            raise RuntimeError(f"Mousepad opened {window['title']!r}, not {note.name}")
        scope = own_scope(window["pid"])
        self.close_window(window["id"])
        self.step(f"Mousepad opened a disposable note and wrote it to recently-used.xbel itself; search found {note.name} "
                  f"as a recent file only, and Return opened it in Mousepad, in its own scope {scope}")

    # --- running ---
    def run(self, names):
        if subprocess.run(["systemctl", "--user", "is-active", "--quiet", "flow-shell"]).returncode and self.shell == INSTALLED_SHELL:
            raise RuntimeError("The installed shell (flow-shell) is not running")
        if not shutil.which("wtype"):
            raise RuntimeError("wtype is not on PATH")
        installed = shutil.which("flow-search")
        if not installed or not Path(installed).resolve().is_relative_to("/nix/store") or self.shell == INSTALLED_SHELL and (
                not str(installed).startswith("/run/current-system/")):
            raise RuntimeError(f"flow-search on PATH is {installed}, not the installed one")
        if self.surfaces() is None:
            raise RuntimeError(f"The shell {self.shell} does not answer on IPC")
        # The test works on an empty workspace of its own: the focused one, if empty, else the last of its output.
        start = focused_workspace()
        spaces = [w for w in read("workspaces") if w["output"] == start["output"]]
        busy = {w["workspace_id"] for w in windows().values()}
        target = next((w for w in sorted(spaces, key=lambda w: w["idx"], reverse=True) if w["id"] not in busy), None)
        if not target:
            raise RuntimeError("No empty workspace to work on")
        niri("action", "focus-workspace", target["idx"])
        wait_for(lambda: focused_workspace()["id"] == target["id"], "the empty workspace")
        pins_before = {p["id"] for p in self.observe()["pins"]}
        self.pointer = Pointer(allow_installed_shell=True)
        # An empty workspace shows Desk: a window of the test's own, kept open, keeps the shell's surfaces closed.
        self.base = self.spawn(f"flow-f008-{self.token}-base", f"F008 base {self.token}")["id"]
        if self.surfaces()["desk"]:  # the shell shows Desk on an empty workspace, and leaves it open
            self.ipc("toggle")
        wait_for(lambda: not any(self.surfaces().values()), lambda: f"every shell surface closed ({self.surfaces()})")
        subprocess.run(["wtype", "-k", "Hyper_L"], check=True, timeout=10)  # a keymap no later call sends
        failure = None
        try:
            for name in names:
                getattr(self, "check_" + name.replace("-", "_"))()
        except BaseException as exc:
            failure = exc
        finally:
            self.cleanup(start)
        if failure:
            raise failure
        if {p["id"] for p in self.observe()["pins"]} != pins_before:
            raise RuntimeError("The pins are not as they were found")
        return {"result": "pass", "steps": self.steps}

    def cleanup(self, start):
        try:
            self.close_search()
        except (RuntimeError, subprocess.SubprocessError, TypeError):
            pass
        for window_id in reversed(self.opened):
            try:
                self.close_window(window_id)
            except (RuntimeError, subprocess.SubprocessError):
                pass
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
        for cleanup in reversed(self.cleanups):
            try:
                cleanup()
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            niri("action", "focus-workspace", start["idx"])
        except subprocess.SubprocessError:
            pass
        if self.pointer:
            self.pointer.park()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", action="append", choices=CHECKS, help="run only this check (repeatable)")
    names = parser.parse_args().only or CHECKS
    names = [n for n in CHECKS if n in names]
    print(json.dumps(Run(os.environ.get("FLOW_SHELL", INSTALLED_SHELL)).run(names), indent=1))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError, ValueError) as exc:
        print(f"Installed search test failed: {exc!r}", file=sys.stderr)
        sys.exit(1)
