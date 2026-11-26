#!/usr/bin/env python3
"""F-007: work survives Desk, Flow's Overview and Niri's overview, by every route in and out (docs/IMPLEMENTATION_STATUS.md,
*F-007 acceptance*).

Run inside the Niri session as the user, with the installed shell stopped (``systemctl --user stop flow-shell``), from a
copy of the checkout's ``tests``. ``FLOW_SHELL`` names the shell file to run, normally the installed
``/etc/flow/shell.qml``: Niri's own bindings call that file's IPC, so the keys this test presses on a real (uinput)
keyboard reach the shell it starts. The shell gets isolated state and a configuration of its own (automatic recovery
off; Reduce motion as each part needs), and the real runtime folder, since Niri's bindings look for it there.
``tests/run_desk_overview_smoke.sh`` stops and starts the installed shell and checks the real state around it.

Each case: a baseline (the test windows, focus, Flow's state files, the shell's ``spaces_view``, Niri's layer surfaces
and a settled screenshot of every output), a reveal, checks while it is shown, a return, and then everything compared
with the baseline. A route that chooses a space must leave focus there and the rest as focusing it directly would. After
every case Super+1 and Super+2, pressed on the keyboard, must reach their pins. ``--part`` runs one part.
"""

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid

from isolated_state import IsolatedState, shell_config
from pointer import Pointer, center
from uinput import Devices

# Screenshots are compared between the shell's two 44 px bands: the Island's tab above (its clock moves) and the home and
# work indicators' band below, whose text Qt draws with grayscale antialiasing one time and subpixel the next (seen in
# the guest: the same "03 / 04", with coloured fringes after Desk closed). The shell's surfaces are checked by Niri's layer
# list and the shell's own state instead.
BAND = 44  # physical rows: the guest's outputs are at scale 1
PARTS = ("desk", "overview", "niri", "layer", "workspace", "open-close", "mid-transition", "reduced-motion", "displays")


def niri(*args):
    return subprocess.check_output(["niri", "msg", *map(str, args)], text=True, timeout=10)


def read(kind):
    return json.loads(niri("-j", kind))


def wait_for(test, label, timeout=10, step=0.05):
    end = time.monotonic() + timeout
    while True:
        value = test()
        if value:
            return value
        if time.monotonic() >= end:
            raise RuntimeError(f"Timed out waiting for {label() if callable(label) else label}")
        time.sleep(step)


class Failure(RuntimeError):
    pass


class Smoke:
    def __init__(self, tmp, shell, out):
        self.tmp, self.shell_file, self.out = Path(tmp), shell, Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.token = uuid.uuid4().hex[:6]
        self.state = self.tmp / "flow"
        self.config = Path(shell_config(tmp))
        self.env = dict(os.environ, XDG_STATE_HOME=str(self.tmp))
        # The shell's grim: the real one, half a second late while ``slow-grim`` exists, so Overview stays in its
        # opening phase (taking a snapshot) long enough for a second Super+O to land there, as on a slow large display.
        self.slow_flag = self.tmp / "slow-grim"
        wrappers = self.tmp / "bin"
        wrappers.mkdir()
        real_grim = subprocess.check_output(["sh", "-c", "command -v grim"], text=True).strip()
        (wrappers / "grim").write_text(f'#!/bin/sh\n[ -e "{self.slow_flag}" ] && sleep 0.5\nexec "{real_grim}" "$@"\n')
        (wrappers / "grim").chmod(0o755)
        self.shell_path = f"{wrappers}:{os.environ['PATH']}"
        self.shell = None
        self.apps, self.processes, self.steps = {}, [], []
        self.devices = Devices()
        self.pointer = None

    # --- the shell -------------------------------------------------------------------------------------------------
    def ipc(self, *args, check=False):
        process = subprocess.run(["quickshell", "ipc", "-p", self.shell_file, "call", "desk", *map(str, args)], text=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        if check and process.returncode:
            raise Failure(f"desk {args}: {process.stderr.strip()}")
        return process.stdout.strip()

    def json(self, *args):
        try:
            return json.loads(self.ipc(*args) or "null")
        except ValueError:
            return None

    def surfaces(self):
        return {name for name, shown in (self.json("surfaces") or {"?": True}).items() if shown}

    def start_shell(self, reduce_motion=False):
        appearance = self.config / "floe-appearance.ini"
        text = re.sub(r"(?m)^reduceMotion=.*\n?", "", appearance.read_text() if appearance.exists() else "")
        line = f"reduceMotion={'true' if reduce_motion else 'false'}\n"
        appearance.write_text(text.replace("[General]\n", "[General]\n" + line, 1) if "[General]\n" in text
                              else "[General]\n" + line + text)
        self.shell = subprocess.Popen(["quickshell", "-p", self.shell_file], env=dict(self.env, XDG_CONFIG_HOME=str(self.config), PATH=self.shell_path),
                                      stdin=subprocess.DEVNULL, stdout=open(self.out / "shell.log", "a"), stderr=subprocess.STDOUT)
        wait_for(lambda: self.shell.poll() is None and self.ipc("toast") == "" and self.json("spaces_view"),
                 "the shell to start", timeout=30, step=0.3)
        time.sleep(3)  # its startup refreshes
        self.reduce_motion = reduce_motion

    def stop_shell(self):
        if self.shell and self.shell.poll() is None:
            self.shell.terminate()
            try:
                self.shell.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.shell.kill()
                self.shell.wait()
        self.shell = None

    # --- the work --------------------------------------------------------------------------------------------------
    def spaces(self, *args):
        process = subprocess.run(["flow-spaces", *map(str, args)], env=self.env, text=True, stdout=subprocess.PIPE, timeout=60)
        value = json.loads(process.stdout or "{}")
        if process.returncode or "error" in value:
            raise Failure(f"flow-spaces {args}: {value}")
        return value

    def open_app(self, name):
        app = self.apps[name] = f"flow-f007-{self.token}-{name}"
        self.processes.append(subprocess.Popen(["foot", "--app-id", app, "--title", f"F-007 {name}", "sh", "-c",
                                                f"printf 'F-007 test window {name}\\n'; exec sleep 3600"],
                                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        return wait_for(lambda: self.window(name, missing_ok=True), f"window {name}")

    def window(self, name, missing_ok=False):
        found = [w for w in read("windows") if w.get("app_id") == self.apps.get(name)]
        if len(found) != 1:
            if missing_ok and not found:
                return None
            raise Failure(f"Window {name} is missing or open twice")
        return found[0]

    def wid(self, name):
        return self.window(name)["id"]

    def focus_window(self, name):
        ident = self.wid(name)
        niri("action", "focus-window", "--id", ident)
        wait_for(lambda: next(w for w in read("windows") if w["id"] == ident)["is_focused"], f"focus on {name}")

    def names(self):
        return {app: name for name, app in self.apps.items()}

    def work(self):
        """What a reveal and return must not change: each test window's ID, workspace, column, row, size and floating
        place; the focused output and window; each output's active workspace and each workspace's active window."""
        names, workspaces = self.names(), read("workspaces")
        output_of = {w["id"]: w["output"] for w in workspaces}
        windows = {}
        for w in read("windows"):
            if w.get("app_id") in names:
                layout = w["layout"]
                windows[names[w["app_id"]]] = {
                    "id": w["id"], "workspace": w["workspace_id"], "output": output_of.get(w["workspace_id"]),
                    "column": layout["pos_in_scrolling_layout"], "size": [round(v) for v in layout["tile_size"]],
                    "floating": w["is_floating"],
                    "at": [round(v) for v in layout["tile_pos_in_workspace_view"]] if w["is_floating"] else None}
        mine = {w["workspace"] for w in windows.values()}
        focused = next((w for w in read("windows") if w["is_focused"]), None)
        return {"windows": windows,
                "focused": names.get(focused["app_id"]) if focused else None,
                "focused_output": next(w["output"] for w in workspaces if w["is_focused"]),
                "active_workspace": {w["output"]: w["id"] for w in workspaces if w["is_active"]},
                "active_window": {w["id"]: names.get(next((x["app_id"] for x in read("windows") if x["id"] == w["active_window_id"]), None))
                                  for w in workspaces if w["id"] in mine}}

    def files(self):
        return {name: (self.state / name).read_bytes() if (self.state / name).exists() else None
                for name in ("spaces.json", "groups.json", "session.json")}

    def layers(self):
        return sorted((l["namespace"], l["output"], l["layer"], l["keyboard_interactivity"]) for l in read("layers"))

    def outputs(self):
        return sorted(name for name, o in read("outputs").items() if o.get("logical"))

    def grab(self, output):
        return subprocess.run(["grim", "-t", "ppm", "-o", output, "-"], stdout=subprocess.PIPE, check=True, timeout=10).stdout

    def settled(self, output, timeout=4, since=False):
        """A screenshot of ``output`` once two in a row, 0.15 s apart, are the same between the shell's bands. With
        ``since``, also when the first of the two was taken (``time.monotonic()``): when the screen stopped changing."""
        last, taken, end = self.grab(output), time.monotonic(), time.monotonic() + timeout
        while time.monotonic() < end:
            time.sleep(0.15)
            now, at = self.grab(output), time.monotonic()
            if differ(last, now) == 0:
                return (taken, now) if since else now
            last, taken = now, at
        raise Failure(f"{output} did not stop changing within {timeout} s")

    def shots(self):
        return {output: self.settled(output) for output in self.outputs()}

    def caught_up(self):
        """The shell shows the focused window's space as active (or the focused Layer), and its view held still for 0.6 s."""
        focused = next((w["id"] for w in read("windows") if w["is_focused"]), None)

        def shown():
            view = self.json("spaces_view") or {}
            active = [s for s in view.get("spaces", []) if s["active"]]
            return view if view and (focused is None or active or focused in view.get("layers", [])) else None
        end = time.monotonic() + 10
        while True:
            first = wait_for(shown, "the shell to show the focused space", timeout=10)
            time.sleep(0.6)
            if self.json("spaces_view") == first or time.monotonic() > end:
                return first

    def capture(self):
        wait_for(lambda: self.ipc("toast") == "", "the shell's toast to go", timeout=8)
        self.caught_up()
        return {"work": self.work(), "files": self.files(), "view": self.json("spaces_view"), "layers": self.layers(),
                "shots": self.shots(), "surfaces": self.surfaces()}

    def save(self, name, shots):
        for output, data in shots.items():
            path = self.out / f"{name}-{output}.ppm"
            path.write_bytes(data)

    # --- comparing -------------------------------------------------------------------------------------------------
    def compare(self, label, before, after, focus=None, same_screen=True):
        """``after`` must be ``before``, but for focus moved to ``focus`` (a window name), when the route chose a space."""
        problems = []
        expected = copy.deepcopy(before["work"])
        if focus:
            expected["focused"] = focus
            target = expected["windows"][focus]
            expected["active_window"][target["workspace"]] = focus
            expected["active_workspace"][target["output"]] = target["workspace"]
            expected["focused_output"] = target["output"]
        if after["work"] != expected:
            problems.append(f"the work changed: {diff_dicts(expected, after['work'])}")
        if after["files"] != before["files"]:
            changed = [n for n in after["files"] if after["files"][n] != before["files"][n]]
            problems.append(f"Flow's state files changed: {changed}")
        if after["surfaces"]:
            problems.append(f"shell surfaces still open: {sorted(after['surfaces'])}")
        if after["layers"] != before["layers"]:
            problems.append(f"Niri's layer surfaces differ: {before['layers']} → {after['layers']}")
        error = self.ipc("spaces_error")
        if error:
            problems.append(f"spaces_error says {error!r}")
        unmarked = lambda view: {**view, "spaces": [{**space, "active": None} for space in view.get("spaces", [])]} if view else view
        if (after["view"] if not focus else unmarked(after["view"])) != (before["view"] if not focus else unmarked(before["view"])):
            problems.append("the shell's spaces_view changed" + (" (which space is active aside)" if focus else ""))
        if same_screen:
            for output in before["shots"]:
                count = differ(before["shots"][output], after["shots"].get(output, b""))
                if count:
                    self.save(f"{slug(label)}-before", {output: before["shots"][output]})
                    self.save(f"{slug(label)}-after", {output: after["shots"][output]})
                    problems.append(f"{output}'s screen differs from before in {count} pixels between the shell's bands")
        if problems:
            raise Failure(f"{label}: " + "; ".join(problems))

    # --- input -----------------------------------------------------------------------------------------------------
    def keys(self, *names):
        self.devices.keys(*names)

    def control(self, surface, text):
        """The middle of the shell's visible button or switch named ``text`` on ``surface`` (controls IPC)."""
        def find():
            found = [c for c in self.json("controls") or [] if c["surface"] == surface and c["text"] == text and c.get("screen")]
            return found[0] if found else None
        return center(wait_for(find, f"{surface}'s {text!r}"))

    def click(self, at):
        self.pointer.click(at)
        self.pointer.park()

    def target(self, name):
        return self.json("pointer_target", name)

    def tap_home(self, name):
        self.pointer.click(self.pointer.hover(lambda: self.target(name), name))
        self.pointer.park()

    # --- the parts -------------------------------------------------------------------------------------------------
    def setup(self):
        """On the focused output, in its focused (empty) workspace, from the left: pin 1 (a, half the screen), pin 2 the
        stacked Group b over c (half), the side-by-side Group d | e, and f (three quarters). g is on the workspace below.
        Focus is on e, so the view starts away from the row's start."""
        start = next(w for w in read("workspaces") if w["is_focused"])
        if any(w.get("workspace_id") == start["id"] for w in read("windows")):
            raise Failure("The focused workspace must be empty")
        self.home_workspace = start["id"]
        for name in "abcdef":
            self.open_app(name)
        for index, name in enumerate("abcdef", 1):
            self.focus_window(name)
            niri("action", "move-column-to-index", index)
        self.focus_window("a")
        niri("action", "set-column-width", "50%")
        self.spaces("pin")
        self.focus_window("b")
        self.spaces("stack")
        stacked = next(s["group_id"] for s in self.spaces("create-group")["spaces"] if s.get("group_id"))
        self.spaces("pin-group", stacked)
        self.focus_window("b")
        niri("action", "set-column-width", "50%")
        self.focus_window("d")
        self.spaces("stack")
        side = next(s["group_id"] for s in self.spaces("create-group")["spaces"]
                    if s.get("group_id") and s["group_id"] != stacked)
        self.spaces("group-layout", side)
        self.focus_window("f")
        niri("action", "set-column-width", "75%")
        self.open_app("g")
        self.focus_window("g")
        niri("action", "move-window-to-workspace-down")
        wait_for(lambda: self.window("g")["workspace_id"] != self.home_workspace, "g on the workspace below")
        niri("action", "focus-workspace", start["idx"])
        self.focus_window("e")
        self.spaces("refresh")
        self.groups = {"stacked": stacked, "side": side}
        layout = {n: w["column"] for n, w in self.work()["windows"].items()}
        if not (layout["a"][0] == 1 and layout["b"][0] == layout["c"][0] == 2 and layout["d"][0] + 1 == layout["e"][0]):
            raise Failure(f"The row was not set up as intended: {layout}")
        self.step(f"set up pin 1 (a), pin 2 (the stacked Group b over c), the side-by-side Group d | e, f, and g on the "
                  f"workspace below: {layout}")

    def step(self, text):
        self.steps.append(text)
        print(f"ok: {text}", flush=True)

    pins_from = None

    def pins_reach(self, label):
        """Super+1 and Super+2, pressed on the keyboard, reach pin 1 (a) and pin 2 (the Group's last focused member);
        from ``pins_from`` when they do not from where the case ended, without Desk either (part_workspace)."""
        if self.pins_from:
            self.focus_window(self.pins_from)
        self.keys("super", "1")
        wait_for(lambda: self.work()["focused"] == "a", lambda: f"Super+1 to reach a after {label} (focus {self.work()['focused']}, "
                 f"{sorted(self.surfaces())}, {self.ipc('spaces_error')!r}, {self.ipc('toast')!r})")
        self.keys("super", "2")
        wait_for(lambda: self.work()["focused"] in ("b", "c"), lambda: f"Super+2 to reach the Group after {label} ({self.work()['focused']})")

    def case(self, label, reveal, shown, back, focus=None, home="e"):
        """One reveal and return from a settled baseline with focus on ``home``."""
        self.focus_window(home)
        time.sleep(0.3)
        before = self.capture()
        reveal()
        asked = time.monotonic()  # the key, click or call that asked for it has been sent
        wait_for(lambda: shown(), lambda: f"{label}: the surface to show ({sorted(self.surfaces())})")
        while_shown = self.work()
        if {k: v for k, v in while_shown["windows"].items()} != before["work"]["windows"]:
            raise Failure(f"{label}: windows changed while it was shown: {diff_dicts(before['work']['windows'], while_shown['windows'])}")
        grabs = [l for l in self.layers() if l[3] == "Exclusive"]
        if any(l[1] != before["work"]["focused_output"] for l in grabs):
            raise Failure(f"{label}: shown on another display than the focused {before['work']['focused_output']}: {grabs}")
        output = before["work"]["focused_output"]
        first, steady = self.settled(output, since=True)
        if first - asked > 1.0:
            raise Failure(f"{label}: the screen settled {first - asked:.2f} s after the request, not within 1 s")
        shown_shots = {o: steady if o == output else self.settled(o) for o in self.outputs()}
        self.save(f"shown-{slug(label)}", shown_shots)  # evidence, and for docs/screenshots
        back()
        wait_for(lambda: not self.surfaces() and not read("overview-state")["is_open"],
                 lambda: f"{label}: to return ({sorted(self.surfaces())})")
        after = self.capture()
        if focus:
            # The screen must be what focusing the same window directly shows, from the same start.
            reference = self.reference(home, focus)
            self.compare(label, before, after, focus=focus, same_screen=False)
            for output, shot in reference.items():
                count = differ(shot, after["shots"][output])
                if count:
                    self.save(f"{slug(label)}-after", {output: after["shots"][output]})
                    self.save(f"{slug(label)}-direct", {output: shot})
                    raise Failure(f"{label}: {output} differs from focusing {focus} directly in {count} pixels")
        else:
            self.compare(label, before, after)
        self.pins_reach(label)
        self.step(f"{label}: same windows, columns, widths, workspaces, focus{' (moved to ' + focus + ')' if focus else ''}, "
                  "state files, layers and screen; shown on the focused display; Super+1 and Super+2 reached their pins")
        return shown_shots

    def reference(self, home, focus):
        self.focus_window(home)
        time.sleep(0.3)
        self.shots()
        self.focus_window(focus)
        return self.shots()

    def desk_open(self):
        """Desk open and whole: its rise done, its content at full opacity and in place."""
        motion = self.json("desk_motion") or {}
        return ("desk" in self.surfaces() and not motion.get("entering") and motion.get("opacity") == 1
                and motion.get("shift") == 0)

    def overview_open(self):
        return self.surfaces() == {"overview"}

    def niri_open(self):
        return read("overview-state")["is_open"]

    def niri_shown(self):
        """Niri's overview open, and none of the shell's own surfaces (the shell does follow it: niri_overview)."""
        return self.niri_open() and self.surfaces() <= {"niri_overview"}

    def part_desk(self):
        k, ui = self.keys, self
        cases = [
            ("Desk: Super+H, then Super+H", lambda: k("super", "h"), lambda: k("super", "h"), None),
            ("Desk: Super+H, then Escape", lambda: k("super", "h"), lambda: k("esc"), None),
            ("Desk: the Island's floe., then floe. again", lambda: ui.click(ui.control("island", "floe.")),
             lambda: ui.click(ui.control("desk", "floe.")), None),
            ("Desk: floe., then Desk's home indicator", lambda: ui.click(ui.control("island", "floe.")),
             lambda: ui.tap_home("desk_home"), None),
            ("Desk: Super+H, then Return to work", lambda: k("super", "h"),
             lambda: ui.click(ui.control("desk", "Return to work")), None),
            ("Desk: the Platform API's ShowDesk, then Escape", lambda: ui.ipc("desk_open"), lambda: k("esc"), None),
            ("Desk: Super+H, then pin 1's card", lambda: k("super", "h"), lambda: ui.click(ui.pin_card(0)), "a"),
            ("Desk: Super+H, then Super+2", lambda: k("super", "h"), lambda: k("super", "2"), "b"),
            ("Desk: Super+H, then Spaces and f's space", lambda: k("super", "h"), lambda: ui.via_switcher("f"), "f"),
        ]
        shots = None
        for label, reveal, back, focus in cases:
            got = self.case(label, reveal, self.desk_open, back, focus=focus)
            shots = shots or got
        self.save("desk", shots)

    def pin_card(self, index):
        """Desk's card for pin ``index`` (from 0): the button whose first words are its number, 01 for the first."""
        number = f"{index + 1:02d}"
        found = wait_for(lambda: [c for c in self.json("controls") or [] if c["surface"] == "desk" and c["kind"] == "button"
                                  and c["words"][:1] == [number] and c.get("screen")], f"pin card {number}")
        return center(found[0])

    def via_switcher(self, name):
        self.click(self.control("desk", "Spaces"))
        wait_for(lambda: "switcher" in self.surfaces(), "the Spaces switcher")
        label = f"F-007 {name}"
        self.click(self.control("switcher", label))

    def part_overview(self):
        k, ui = self.keys, self
        cases = [
            ("Overview: Super+O, then Escape", lambda: k("super", "o"), lambda: k("esc"), None),
            ("Overview: the home indicator, then Enter", lambda: ui.tap_home("home"), lambda: k("enter"), None),
            ("Overview: a swipe up the home indicator, then Open selected", lambda: ui.swipe_home(),
             lambda: ui.click(ui.control("overview", "↗  Open selected")), None),
            ("Overview: the Platform API's ShowOverview, then Escape", lambda: ui.ipc("overview_open"), lambda: k("esc"), None),
            ("Overview: Super+O, then Right and Enter", lambda: k("super", "o"), lambda: (k("right"), k("enter")), "f"),
            ("Overview: Super+O, then Home and Enter", lambda: k("super", "o"), lambda: (k("home"), k("enter")), "a"),
            ("Overview: Super+O, then End and Enter", lambda: k("super", "o"), lambda: (k("end"), k("enter")), "f"),
            ("Overview: Super+O, then a click on the next card and a click to open it", lambda: k("super", "o"),
             lambda: ui.click_card(1), "f"),
            ("Overview: Super+O, a wheel notch, then Escape", lambda: k("super", "o"),
             lambda: (ui.pointer.scroll(center(ui.target("overview_row")), 1), time.sleep(0.4), k("esc")), None),
        ]
        for label, reveal, back, focus in cases:
            self.case(label, reveal, self.overview_open, back, focus=focus)
        chains = [
            ("Overview, Super+O again to Desk, then Super+H", lambda: k("super", "o"),
             lambda: (k("super", "o"), wait_for(self.desk_open, "Desk after Super+O again"), k("super", "h"))),
            ("Overview, Up to Desk, then Escape", lambda: k("super", "o"),
             lambda: (k("up"), wait_for(self.desk_open, "Desk after Up"), k("esc"))),
            ("Overview, its home indicator to Desk, then Desk's", lambda: k("super", "o"),
             lambda: (ui.tap_home("overview_home"), wait_for(self.desk_open, "Desk after Overview's home indicator"),
                      ui.tap_home("desk_home"))),
            ("Overview, Your desk, then Return to work", lambda: k("super", "o"),
             lambda: (ui.click(ui.control("overview", "↓  Your desk")), wait_for(self.desk_open, "Desk after Your desk"),
                      ui.click(ui.control("desk", "Return to work")))),
            ("Desk, its Overview, then Escape", lambda: (k("super", "h"), wait_for(self.desk_open, "Desk"),
                                                         ui.click(ui.control("desk", "Overview"))), lambda: k("esc")),
        ]
        for label, reveal, back in chains:
            self.case(label, reveal, self.overview_open, back)  # each reveal ends in Overview

    def swipe_home(self):
        start = self.pointer.hover(lambda: self.target("home"), "the home indicator")
        self.pointer.drag(start, (start[0], start[1] - 60))
        self.pointer.park()

    def click_card(self, offset):
        """Click the card ``offset`` cards from the selected one (Overview centres it), then click it again to open."""
        row = self.target("overview_row")
        x, y = center(row)
        width = max(220, min(480, row["window"][0] * 0.3)) + 32  # a card (spacesOverview.cardWidth) and the row's spacing
        at = (round(x + offset * width), y)
        before = self.ipc("overview_selected")
        self.pointer.click(at)
        wait_for(lambda: self.ipc("overview_selected") != before, "a click on another card to select it", timeout=3)
        time.sleep(0.5)  # the row moves the selected card to the middle
        self.click(center(self.target("overview_row")))

    def part_niri(self):
        k, ui = self.keys, self
        cases = [
            ("Niri's overview: four fingers up, then four fingers down", lambda: ui.devices.swipe(4, 0, -1600),
             lambda: ui.devices.swipe(4, 0, 1600), None),
            ("Niri's overview: four fingers up, then Escape", lambda: ui.devices.swipe(4, 0, -1600), lambda: k("esc"), None),
            ("Niri's overview: Spaces' Live overview, then Escape", lambda: (k("super", "tab"),
                                                                       wait_for(lambda: "switcher" in self.surfaces(), "the switcher"),
                                                                       ui.click(ui.control("switcher", "Live overview"))),
             lambda: k("esc"), None),
        ]
        for label, reveal, back, focus in cases:
            self.case(label, reveal, lambda: self.niri_shown(), back, focus=focus)

    def part_layer(self):
        """A Layer (a floating window over f's space, made one by Flow) open and focused."""
        self.focus_window("f")
        self.open_app("l")
        self.focus_window("l")
        self.spaces("make-layer")
        wait_for(lambda: self.window("l")["is_floating"] and self.window("l")["id"] in self.json("spaces_view")["layers"],
                 "l to be a Layer the shell shows")
        k = self.keys
        for label, reveal, shown, back in [
                ("Layer: Super+H, then Escape", lambda: k("super", "h"), self.desk_open, lambda: k("esc")),
                ("Layer: Super+O, then Escape", lambda: k("super", "o"), self.overview_open, lambda: k("esc")),
                ("Layer: four fingers up, then down", lambda: self.devices.swipe(4, 0, -1600),
                 lambda: self.niri_shown(), lambda: self.devices.swipe(4, 0, 1600))]:
            self.case(label, reveal, shown, back, home="l")
        self.close("l")

    def close(self, name):
        ident = self.wid(name)
        niri("action", "close-window", "--id", ident)
        wait_for(lambda: self.window(name, missing_ok=True) is None, f"{name} to close")
        del self.apps[name]

    def part_workspace(self):
        """Focus on g, on the workspace below the row's."""
        k = self.keys
        # The control: Super+1 from here with nothing shown. Flow keeps a display's spaces on one workspace, its row;
        # whether a pin is reached from another workspace is F-004's, not something Desk or Overview may change.
        self.focus_window("g")
        self.caught_up()
        k("super", "1")
        time.sleep(1.5)
        control = (self.work()["focused"], self.ipc("spaces_error"), sorted(self.surfaces()))
        if "desk" in self.surfaces():
            k("esc")
            wait_for(lambda: not self.surfaces(), "Desk to close")
        # Its error stays in the shell until an action succeeds: Super+1 from the row's workspace, so the cases below
        # start without it.
        self.focus_window("e")
        k("super", "1")
        wait_for(lambda: self.work()["focused"] == "a" and self.ipc("spaces_error") == "", "Super+1 from the row to clear it")
        self.step(f"control, nothing shown: Super+1 from the workspace below gave focus {control[0]}, error {control[1]!r}, "
                  f"surfaces {control[2]}")
        self.pins_from = "e" if control[0] != "a" else None
        for label, reveal, shown, back in [
                ("The workspace below: Super+H, then Escape", lambda: k("super", "h"), self.desk_open, lambda: k("esc")),
                ("The workspace below: Super+O, then Enter", lambda: k("super", "o"), self.overview_open, lambda: k("enter")),
                ("The workspace below: four fingers up, then Escape", lambda: self.devices.swipe(4, 0, -1600),
                 lambda: self.niri_shown(), lambda: k("esc"))]:
            self.case(label, reveal, shown, back, home="g")
        self.pins_from = None

    def without(self, captured, name):
        """``captured`` with ``name``'s window ID left out: a window opened again has a new one."""
        kept = copy.deepcopy({k: v for k, v in captured.items() if k not in ("shots", "files")})
        if name in kept["work"]["windows"]:
            kept["work"]["windows"][name]["id"] = "new"
        if kept.get("view"):
            ident = {w["id"] for w in [captured["work"]["windows"].get(name)] if w}
            for pin in kept["view"]["pins"]:
                pin["window_ids"] = ["new" if i in ident else i for i in pin["window_ids"]]
            for space in kept["view"]["spaces"]:
                if int(space["id"]) in ident:
                    space["id"] = "new"
        return kept

    def same_as(self, label, got, reference, name):
        problems = []
        if self.without(got, name) != self.without(reference, name):
            problems.append(f"the work differs from Niri's without Desk: {diff_dicts(self.without(reference, name), self.without(got, name))}")
        for output in reference["shots"]:
            count = differ(reference["shots"][output], got["shots"][output])
            if count:
                self.save(f"{slug(label)}-desk", {output: got["shots"][output]})
                self.save(f"{slug(label)}-direct", {output: reference["shots"][output]})
                problems.append(f"{output} differs from the same without Desk in {count} pixels")
        if problems:
            raise Failure(f"{label}: " + "; ".join(problems))

    def part_open_close(self):
        """A window that opens, or closes, while Desk is shown does what it does without Desk."""
        k = self.keys
        self.focus_window("e")
        time.sleep(0.3)
        start = self.capture()
        k("super", "h")
        wait_for(self.desk_open, "Desk")
        self.open_app("n")
        time.sleep(1)
        if not self.desk_open() or self.layers() == start["layers"]:
            raise Failure(f"A window opening closed Desk: {sorted(self.surfaces())}")
        k("esc")
        wait_for(lambda: not self.surfaces(), "Desk to close")
        with_desk = self.capture()
        self.close("n")
        self.focus_window("e")
        time.sleep(0.3)
        again = self.capture()
        self.compare("Closing the new window again", start, again)
        self.open_app("n")
        direct = self.capture()
        self.same_as("A window opened while Desk was shown", with_desk, direct, "n")
        self.step("a window opened while Desk was shown: Desk stayed open; after Escape the row, focus and screen were "
                  "as when it opens with Desk closed")

        before = self.capture()
        k("super", "h")
        wait_for(self.desk_open, "Desk")
        self.close("n")
        time.sleep(1)
        if not self.desk_open():
            raise Failure(f"A window closing closed Desk: {sorted(self.surfaces())}")
        k("esc")
        wait_for(lambda: not self.surfaces(), "Desk to close")
        with_desk = self.capture()
        self.focus_window("e")
        self.open_app("n")
        again = self.capture()
        self.same_as("Opening the window again", before, again, "n")
        self.close("n")
        direct = self.capture()
        self.same_as("A window closed while Desk was shown", with_desk, direct, "n")
        self.pins_reach("a window opening and closing under Desk")
        self.step("the focused window closed while Desk was shown: Desk stayed open; after Escape the row, focus and "
                  "screen were as when it closes with Desk closed; Super+1 and Super+2 reached their pins")

    def motion(self):
        return self.json("desk_motion") or {}

    def part_mid_transition(self):
        """A toggle that comes while a transition still runs is taken, every time."""
        k = self.keys
        for attempt in range(3):
            self.focus_window("e")
            time.sleep(0.3)
            before = self.capture()
            counted = self.motion()["toggled_mid_rise"]
            k("super", "h")
            wait_for(lambda: self.motion().get("entering"), "Desk's rise to start", timeout=3, step=0.005)
            k("super", "h")
            time.sleep(1)
            after = self.capture()
            if self.motion()["toggled_mid_rise"] != counted + 1:
                raise Failure(f"Super+H again did not come during Desk's rise (attempt {attempt + 1}): {self.motion()}")
            self.compare(f"Super+H again during Desk's rise ({attempt + 1})", before, after)
        self.step("Super+H again during Desk's 220 ms rise (the shell counted it there, three times) closed Desk: its layer "
                  "surface was gone and the screen was the work view again")

        before = self.capture()
        for presses in (3, 5):
            for _ in range(presses):
                k("super", "h")
                time.sleep(0.06)
            wait_for(self.desk_open, f"Desk after {presses} quick presses", timeout=3)
            time.sleep(0.5)
            if not self.desk_open():
                raise Failure(f"{presses} quick presses of Super+H left Desk closed")
            k("esc")
            wait_for(lambda: not self.surfaces(), "Desk to close")
        after = self.capture()
        self.compare("Super+H pressed quickly three and five times", before, after)
        self.step("Super+H pressed three and five times in quick succession left Desk open each time, and Escape returned "
                  "to the same work")

        before = self.capture()
        hits = []
        self.slow_flag.touch()
        for gap in (0.1, 0.2, 0.3):
            counted = self.motion()["again_while_pending"]
            k("super", "o")
            time.sleep(gap)
            k("super", "o")
            wait_for(self.desk_open, lambda: f"Desk after Super+O twice {gap} s apart ({sorted(self.surfaces())})", timeout=3)
            time.sleep(0.8)
            if self.surfaces() != {"desk"}:
                raise Failure(f"Super+O twice {gap} s apart left {sorted(self.surfaces())}")
            hits.append(self.motion()["again_while_pending"] - counted)
            k("esc")
            wait_for(lambda: not self.surfaces(), "Desk to close")
        self.slow_flag.unlink()
        if not all(hits):
            raise Failure(f"Not every second Super+O came while Overview was still opening ({hits}): the case was not reached")
        self.compare("Super+O again while Overview opened", before, self.capture())
        self.step(f"with the snapshot made 0.5 s slow, Super+O pressed again 0.1, 0.2 and 0.3 s after the first, while Overview "
                  "was still opening (the shell counted each), went on to Desk every time")

        before = self.capture()
        self.devices.swipe(4, 0, -1600, steps=8)
        opened = self.niri_open()  # the quick swipe was taken: the overview is opening
        self.devices.swipe(4, 0, 1600, steps=8)
        time.sleep(1)
        niri_after = read("overview-state")["is_open"]
        if not opened:
            raise Failure("A quick four-finger swipe up did not open Niri's overview, so the interruption was not tested")
        niri("action", "toggle-overview")
        time.sleep(0.05)
        niri("action", "toggle-overview")
        time.sleep(1)
        after = self.capture()
        if niri_after or self.niri_open():
            raise Failure("Niri's overview stayed open after an opening interrupted")
        self.compare("Niri's overview interrupted while opening", before, after)
        self.pins_reach("the interrupted transitions")
        self.step("Niri's overview, opened by four fingers and closed by four fingers straight after, and toggled twice "
                  "50 ms apart, was closed and the work the same")

    def frames(self, trigger, output, seconds=0.8):
        """Screenshots of ``output`` as fast as grim takes them, from just before ``trigger`` for ``seconds``."""
        import threading
        taken, stop = [], threading.Event()

        def run():
            while not stop.is_set():
                taken.append(self.grab(output))
        thread = threading.Thread(target=run)
        thread.start()
        time.sleep(0.1)
        trigger()
        time.sleep(seconds)
        stop.set()
        thread.join()
        return taken

    def in_between(self, frames, start, end):
        """Frames that are neither the work view before nor the surface's settled frame: motion caught midway."""
        return [f for f in frames if differ(f, start) and differ(f, end)]

    def motion_counts(self):
        output = self.work()["focused_output"]
        self.focus_window("e")
        time.sleep(0.3)
        work = self.settled(output)
        desk = self.frames(lambda: self.keys("super", "h"), output)
        desk_end = self.settled(output)
        back = self.frames(lambda: self.keys("esc"), output)
        wait_for(lambda: not self.surfaces(), "Desk to close")
        self.settled(output)
        # The return, as measured: Desk's frame (d), then the work, whole (w) or for a moment without its work indicator (?).
        print(f"{'still' if self.reduce_motion else 'moving'}: Escape from Desk "
              + "".join("d" if not differ(f, desk_end) else "w" if not differ(f, work) else "?" for f in back), flush=True)
        # Overview to Desk and back, and the returns: the work must never show between two surfaces.
        self.keys("super", "o")
        wait_for(self.overview_open, "Overview")
        overview = self.settled(output)
        to_desk = self.frames(lambda: self.keys("super", "o"), output)
        wait_for(self.desk_open, "Desk after Super+O again")
        desk_again = self.settled(output)
        to_overview = self.frames(lambda: self.click(self.control("desk", "Overview")), output)
        wait_for(self.overview_open, "Overview from Desk")
        self.keys("esc")
        wait_for(lambda: not self.surfaces(), "Overview to close")
        self.settled(output)
        between = sum(1 for f in to_desk if not differ(f, work)) + sum(1 for f in to_overview if not differ(f, work))
        between_bare = len([f for f in to_desk if differ(f, overview) and differ(f, desk_again)])
        live = self.frames(lambda: niri("action", "open-overview"), output)
        live_end = self.settled(output)
        niri("action", "close-overview")
        wait_for(lambda: not self.niri_open(), "Niri's overview to close")
        self.settled(output)
        tag = "still" if self.reduce_motion else "moving"
        for n, frame in enumerate(self.in_between(desk, work, desk_end)[:4]):
            self.save(f"midway-{tag}-desk-{n}", {output: frame})
        self.save(f"midway-{tag}-desk-end", {output: desk_end})
        self.save(f"midway-{tag}-work", {output: work})
        print(f"{tag}: frames equal to the work view {sum(1 for f in desk if not differ(f, work))}, to Desk's "
              f"{sum(1 for f in desk if not differ(f, desk_end))}, of {len(desk)}; order "
              + "".join("w" if not differ(f, work) else "d" if not differ(f, desk_end) else "?" for f in desk), flush=True)
        print(f"{tag}: Overview to Desk " + "".join("o" if not differ(f, overview) else "d" if not differ(f, desk_again)
                                                    else "w" if not differ(f, work) else "?" for f in to_desk), flush=True)
        return {"return_midway": sum(1 for f in back if differ(f, desk_end) and differ(f, work)), "return_frames": len(back),
                "between": between, "to_desk_midway": between_bare, "to_desk_frames": len(to_desk),
                "desk": len(self.in_between(desk, work, desk_end)), "desk_frames": len(desk),
                "niri": len(self.in_between(live, work, live_end)), "niri_frames": len(live)}

    def part_reduced_motion(self):
        moving = self.motion_counts()
        if not (moving["desk"] and moving["niri"]):
            raise Failure(f"With motion on, the screenshots caught no frame midway, so they could not show motion: {moving}")
        self.stop_shell()
        self.start_shell(reduce_motion=True)
        time.sleep(1.5)  # Niri reloads its configuration once the shell has pointed niri-motion.kdl at niri-still.kdl
        try:
            still = self.motion_counts()
            k = self.keys
            self.case("Reduce motion: Super+H, then Escape", lambda: k("super", "h"), self.desk_open, lambda: k("esc"))
            self.case("Reduce motion: four fingers up, then down", lambda: self.devices.swipe(4, 0, -1600),
                      lambda: self.niri_shown(), lambda: self.devices.swipe(4, 0, 1600))
        finally:
            self.stop_shell()
            self.start_shell(reduce_motion=False)
            time.sleep(1.5)
        if moving["between"] or still["between"]:
            raise Failure(f"The work showed between Overview and Desk (motion on {moving['between']}, Reduce motion "
                          f"{still['between']} frames)")
        if still["to_desk_midway"]:
            raise Failure(f"With Reduce motion, {still['to_desk_midway']} of {still['to_desk_frames']} frames from Overview to "
                          "Desk were neither")
        if still["desk"] or still["niri"]:
            raise Failure(f"With Reduce motion, frames were caught midway (Desk {still['desk']} of {still['desk_frames']}, "
                          f"Niri's overview {still['niri']} of {still['niri_frames']})")
        self.step(f"the work never showed between Overview and Desk, either way ({moving['to_desk_frames']} and "
                  f"{still['to_desk_frames']} screenshots from Overview to Desk); with Reduce motion every one was Overview's "
                  "or Desk's settled frame")
        self.step(f"returning from Desk by Escape, {moving['return_midway']} of {moving['return_frames']} screenshots (motion on) "
                  f"and {still['return_midway']} of {still['return_frames']} (Reduce motion) were the work without its whole "
                  "work indicator; the rest Desk's or the work's settled frame")
        self.step(f"Reduce motion: no frame midway for Desk ({still['desk_frames']} screenshots) or Niri's overview "
                  f"({still['niri_frames']}); with it off, {moving['desk']} of {moving['desk_frames']} and {moving['niri']} of "
                  f"{moving['niri_frames']} were caught midway")

    def display(self, on):
        subprocess.run(["sudo", "-n", "tee", "/sys/class/drm/card1-Virtual-2/status"], input=("on" if on else "off") + "\n",
                       text=True, stdout=subprocess.DEVNULL, check=True, timeout=10)
        subprocess.run(["sudo", "-n", "udevadm", "trigger", "--action=change", "/sys/class/drm/card1"], check=True, timeout=10)
        wait_for(lambda: ("Virtual-2" in self.outputs()) == on, f"Virtual-2 to turn {'on' if on else 'off'}", timeout=15)

    def part_displays(self):
        """A second display, with its own windows, pin and focus."""
        self.display(True)
        try:
            time.sleep(2)
            niri("action", "focus-monitor", "Virtual-2")
            wait_for(lambda: self.work()["focused_output"] == "Virtual-2", "focus on Virtual-2")
            self.open_app("x")
            self.focus_window("x")
            self.spaces("pin")
            self.open_app("y")
            self.focus_window("y")
            shown = self.spaces("refresh")["display"]  # the focused display's
            self.external = shown.get("number")
            if not self.external:
                raise Failure(f"Virtual-2 has no display number: {shown}")
            # The shell's pin and display lists, refreshed with the new output.
            wait_for(lambda: len((self.json("spaces_view") or {}).get("pins", [])) == 1, "the shell to show Virtual-2's pin")
            k = self.keys
            cases = [("Main, Virtual-2 on: Super+H, then Escape", "e", lambda: k("super", "h"), self.desk_open, lambda: k("esc")),
                     ("Main, Virtual-2 on: Super+O, then Escape", "e", lambda: k("super", "o"), self.overview_open, lambda: k("esc")),
                     ("Virtual-2: Super+H, then Escape", "y", lambda: k("super", "h"), self.desk_open, lambda: k("esc")),
                     ("Virtual-2: Super+H, then Super+H", "y", lambda: k("super", "h"), self.desk_open, lambda: k("super", "h")),
                     ("Virtual-2: Super+O, then Escape", "y", lambda: k("super", "o"), self.overview_open, lambda: k("esc")),
                     ("Virtual-2: Super+O, then Enter", "y", lambda: k("super", "o"), self.overview_open, lambda: k("enter")),
                     ("Two displays: four fingers up, then down", "y", lambda: self.devices.swipe(4, 0, -1600),
                      lambda: self.niri_shown(), lambda: self.devices.swipe(4, 0, 1600))]
            for label, home, reveal, shown, back in cases:
                self.case(label, reveal, shown, back, home=home)
                self.keys("super", "leftalt", str(self.external))
                self.keys("super", "leftalt", "1")
                wait_for(lambda: self.work()["focused"] == "x", f"Super+Alt+{self.external}, 1 to reach Virtual-2's pin after {label}")
            self.step(f"after each, Super+Alt+{self.external} then 1 reached Virtual-2's pin")
            # Super+H the moment focus crosses to the other display, before the shell's next refresh (0.15 to 0.6 s):
            # Desk must still open there. Each case above waited for the shell first.
            for attempt in range(3):
                for name, output in (("y", "Virtual-2"), ("e", "Virtual-1")):
                    niri("action", "focus-window", "--id", self.wid(name))
                    k("super", "h")
                    wait_for(self.desk_open, f"Desk right after focus moved to {output}", timeout=3)
                    grabs = [l for l in self.layers() if l[3] == "Exclusive"]
                    if [l[1] for l in grabs] != [output]:
                        raise Failure(f"Super+H right after focus moved to {output} (attempt {attempt + 1}) showed Desk on {grabs}")
                    k("esc")
                    wait_for(lambda: not self.surfaces(), "Desk to close")
            self.step("Super+H pressed the moment focus moved to the other display, before the shell refreshed, opened Desk "
                      "on that display, both ways, three times")
            self.close("x")
            self.close("y")
        finally:
            self.display(False)


def differ(a, b):
    """Pixels that differ between the shell's bands between two PPM screenshots (any size difference counts as all)."""
    def parse(data):
        parts = data.split(b"\n", 3)
        width, height = map(int, parts[1].split())
        return width, height, parts[3]
    if not a or not b:
        return -1
    wa, ha, pa = parse(a)
    wb, hb, pb = parse(b)
    if (wa, ha) != (wb, hb):
        return wa * ha
    start, end = wa * 3 * BAND, wa * 3 * (ha - BAND)
    if pa[start:end] == pb[start:end]:
        return 0
    return sum(1 for i in range(start, end, 3) if pa[i:i + 3] != pb[i:i + 3])


def diff_dicts(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        return {k: diff_dicts(a.get(k), b.get(k)) for k in sorted(set(a) | set(b), key=str) if a.get(k) != b.get(k)}
    return f"{a} → {b}"


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--part", action="append", choices=PARTS)
    parser.add_argument("--out", default=os.path.expanduser("~/flow-f007/out"))
    args = parser.parse_args()
    shell = os.environ.get("FLOW_SHELL", "/etc/flow/shell.qml")
    if subprocess.run(["systemctl", "--user", "is-active", "--quiet", "flow-shell"]).returncode == 0:
        raise Failure("Stop the installed shell first (systemctl --user stop flow-shell)")
    # The shell under test is /etc/flow's, which points Niri's include links in the real ~/.config/flow from its own
    # (isolated) settings: Reduce motion has to reach the user's Niri to be tested. They are put back as they were.
    links = {p: os.readlink(p) for p in (Path.home() / ".config/flow").glob("niri-*.kdl") if p.is_symlink()}
    real_state = Path.home() / ".local/state/flow"
    real = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in real_state.glob("*") if p.is_file()}
    if read("overview-state")["is_open"]:
        raise Failure("Niri's overview is open; close it first")
    start_workspace = next(w for w in read("workspaces") if w["is_focused"])
    with IsolatedState("flow-f007-") as tmp:
        smoke = Smoke(tmp, shell, args.out)
        failure = None
        try:
            smoke.setup()
            smoke.start_shell()
            smoke.pointer = Pointer()
            for part in args.part or PARTS:
                getattr(smoke, "part_" + part.replace("-", "_"))()
        except BaseException as exc:
            failure = exc
        finally:
            try:
                niri("action", "close-overview")  # a failure while it was open must not leave it open
            except subprocess.SubprocessError:
                pass
            smoke.stop_shell()
            smoke.devices.close()
            for process in smoke.processes:
                process.terminate()
            for process in smoke.processes:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
            try:
                niri("action", "focus-workspace", start_workspace["idx"])
            except subprocess.SubprocessError:
                pass
            for path in (Path.home() / ".config/flow").glob("niri-*.kdl"):
                if path.is_symlink() and path not in links:
                    path.unlink()
            for path, target in links.items():
                if not path.is_symlink() or os.readlink(path) != target:
                    temporary = path.with_name(path.name + ".f007")
                    temporary.unlink(missing_ok=True)
                    temporary.symlink_to(target)
                    temporary.replace(path)
        if failure:
            raise failure
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in real_state.glob("*") if p.is_file()}
    if after != real:
        raise Failure("The real Flow state changed")
    print(json.dumps({"result": "pass", "checks": len(smoke.steps), "steps": smoke.steps}, indent=1))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, KeyError, StopIteration, subprocess.SubprocessError, ValueError) as exc:
        print(f"F-007 Desk/Overview test failed: {exc}", file=sys.stderr)
        sys.exit(1)
