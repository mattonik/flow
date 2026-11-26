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

BAND = 44  # the Island's tab: its clock changes, so screenshots are compared below it
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
        self.shell = subprocess.Popen(["quickshell", "-p", self.shell_file], env=dict(self.env, XDG_CONFIG_HOME=str(self.config)),
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

    def settled(self, output, timeout=4):
        """A screenshot of ``output`` once two in a row, 0.15 s apart, are the same below the Island's band."""
        last, end = self.grab(output), time.monotonic() + timeout
        while time.monotonic() < end:
            time.sleep(0.15)
            now = self.grab(output)
            if differ(last, now) == 0:
                return now
            last = now
        raise Failure(f"{output} did not stop changing within {timeout} s")

    def shots(self):
        return {output: self.settled(output) for output in self.outputs()}

    def capture(self):
        wait_for(lambda: self.ipc("toast") == "", "the shell's toast to go", timeout=8)
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
        expected = json.loads(json.dumps(before["work"]))
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
        if not focus and after["view"] != before["view"]:
            problems.append("the shell's spaces_view changed")
        if same_screen:
            for output in before["shots"]:
                count = differ(before["shots"][output], after["shots"].get(output, b""))
                if count:
                    self.save(f"{slug(label)}-before", {output: before["shots"][output]})
                    self.save(f"{slug(label)}-after", {output: after["shots"][output]})
                    problems.append(f"{output}'s screen differs from before in {count} pixels below the Island")
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

    def pins_reach(self, label):
        """Super+1 and Super+2, pressed on the keyboard, reach pin 1 (a) and pin 2 (the Group's last focused member)."""
        self.keys("super", "1")
        wait_for(lambda: self.work()["focused"] == "a", lambda: f"Super+1 to reach a after {label} ({self.work()['focused']})")
        self.keys("super", "2")
        wait_for(lambda: self.work()["focused"] in ("b", "c"), lambda: f"Super+2 to reach the Group after {label} ({self.work()['focused']})")

    def case(self, label, reveal, shown, back, focus=None, home="e"):
        """One reveal and return from a settled baseline with focus on ``home``."""
        self.focus_window(home)
        time.sleep(0.3)
        before = self.capture()
        reveal()
        wait_for(lambda: shown(), lambda: f"{label}: the surface to show ({sorted(self.surfaces())})")
        while_shown = self.work()
        if {k: v for k, v in while_shown["windows"].items()} != before["work"]["windows"]:
            raise Failure(f"{label}: windows changed while it was shown: {diff_dicts(before['work']['windows'], while_shown['windows'])}")
        shown_shots = self.shots()
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
                  "state files, layers and screen; Super+1 and Super+2 reached their pins")
        return shown_shots

    def reference(self, home, focus):
        self.focus_window(home)
        time.sleep(0.3)
        self.shots()
        self.focus_window(focus)
        return self.shots()

    def desk_open(self):
        return "desk" in self.surfaces() and not self.json("desk_motion").get("entering")

    def overview_open(self):
        return self.surfaces() == {"overview"}

    def niri_open(self):
        return read("overview-state")["is_open"]

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
            ("Desk: Super+H, then Super+2", lambda: k("super", "h"), lambda: k("super", "2"), "c"),
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
             lambda: ui.click(ui.control("overview", "Open selected")), None),
            ("Overview: the Platform API's ShowOverview, then Escape", lambda: ui.ipc("overview_open"), lambda: k("esc"), None),
            ("Overview: Super+O, then Right and Enter", lambda: k("super", "o"), lambda: (k("right"), k("enter")), "f"),
            ("Overview: Super+O, then Home and Enter", lambda: k("super", "o"), lambda: (k("home"), k("enter")), "a"),
            ("Overview: Super+O, then End and Enter", lambda: k("super", "o"), lambda: (k("end"), k("enter")), "f"),
            ("Overview: Super+O, then a click on the first card and a click to open it", lambda: k("super", "o"),
             lambda: ui.click_card(-2), "a"),
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
            ("Desk, its Overview, then Escape", lambda: (k("super", "h"), wait_for(self.desk_open, "Desk"),
                                                         ui.click(ui.control("desk", "Overview"))), lambda: k("esc")),
        ]
        for label, reveal, back in chains:
            self.case(label, reveal, lambda: bool(self.surfaces()), back)

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
            self.case(label, reveal, lambda: self.niri_open() and not self.surfaces(), back, focus=focus)


def differ(a, b):
    """Pixels that differ below the Island's band between two PPM screenshots (any size difference counts as all)."""
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
    start = wa * 3 * BAND
    if pa[start:] == pb[start:]:
        return 0
    return sum(1 for i in range(start, len(pa), 3) if pa[i:i + 3] != pb[i:i + 3])


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
    real_state = Path.home() / ".local/state/flow"
    real = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in real_state.glob("*") if p.is_file()}
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
        if failure:
            raise failure
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in real_state.glob("*") if p.is_file()}
    if after != real:
        raise Failure("The real Flow state changed")
    print(json.dumps({"result": "pass", "checks": len(smoke.steps), "steps": smoke.steps}, indent=1))


if __name__ == "__main__":
    try:
        main()
    except (Failure, OSError, KeyError, StopIteration, subprocess.SubprocessError, ValueError) as exc:
        print(f"F-007 Desk/Overview test failed: {exc}", file=sys.stderr)
        sys.exit(1)
