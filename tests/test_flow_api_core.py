"""Policy of the Flow Platform API: who may see and focus which spaces (ADR-0009)."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "session"))
sys.path.insert(0, str(Path(__file__).parent))
import flow_api_core as core
import spaces
from fake_niri import MultiNiri

LAPTOP = {"make": "BOE", "model": "Panel", "serial": None, "logical": {"x": 0, "y": 0, "width": 1280, "height": 800}}
MONITOR = {"make": "Dell", "model": "U2723QE", "serial": "SN1",
           "logical": {"x": 1280, "y": 0, "width": 1920, "height": 1080}}
LAPTOP_KEY = "port:eDP-1|BOE|Panel"
MONITOR_KEY = "edid:Dell|U2723QE|SN1"
APPS = {1: "org.example.Notes", 2: "org.example.Paint", 3: "org.example.Mail", 4: "org.example.Term",
        8: "org.example.Chat"}
PIDS = {1: 101, 2: 102, 3: 103, 4: 104, 8: 108}


class Proc:
    """A fake /proc: processes with parents, some inside a Flatpak sandbox."""

    def __init__(self):
        directory = tempfile.TemporaryDirectory()
        self.cleanup = directory.cleanup
        self.root = Path(directory.name)

    def process(self, pid, parent, comm="app", flatpak=None):
        base = self.root / str(pid)
        (base / "root").mkdir(parents=True)
        (base / "stat").write_text(f"{pid} ({comm}) S {parent} 1 1 0 -1\n")
        if flatpak is not None:
            (base / "root" / ".flatpak-info").write_text(flatpak)


class CoreTest(unittest.TestCase):
    def setUp(self):
        self.proc = Proc()
        self.addCleanup(self.proc.cleanup)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)
        (self.state / "groups.initialized").touch()
        group = {"id": "g", "label": "Studio", "members": ["org.example.Notes", "org.example.Paint"],
                 "layout": "stack"}
        (self.state / "groups.json").write_text(json.dumps({"version": 1, "groups": [group]}))
        displays = {LAPTOP_KEY: {"connector": "eDP-1", "label": "BOE Panel", "number": 0},
                    MONITOR_KEY: {"connector": "DP-1", "label": "Dell U2723QE", "number": 1}}
        pins = [{"id": "m", "label": "Mail", "app_id": "org.example.Mail", "display": LAPTOP_KEY},
                {"id": "c", "label": "Chat", "app_id": "org.example.Chat", "display": MONITOR_KEY}]
        (self.state / "spaces.json").write_text(json.dumps({"version": 2, "displays": displays, "pins": pins}))
        # Laptop: pinned Mail, the Studio Group (Notes + Paint stacked), a terminal. Monitor: pinned Chat.
        self.niri = MultiNiri({"eDP-1": [[3], [1, 2], [4]], "DP-1": [[8]]}, APPS, 1,
                              {"eDP-1": LAPTOP, "DP-1": MONITOR})
        self.spaces, self.windows = core.project(self.observe(), "session")

    def observe(self):
        snapshot = self.niri.snapshot

        def with_pids():
            ws, windows = snapshot()
            for w in windows:
                w["pid"] = PIDS[w["id"]]
            return ws, windows

        with patch.object(sys, "argv", ["flow-spaces", "observe"]), \
             patch.object(spaces, "snapshot", side_effect=with_pids), \
             patch.object(spaces, "display_state", side_effect=self.niri.display_state), \
             patch.object(spaces, "full_state", side_effect=lambda: (*spaces.snapshot(), *spaces.display_state())), \
             patch.object(spaces, "session_id", return_value="session"), \
             patch.object(spaces, "command", side_effect=self.niri), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            spaces.run_locked(self.state)
        return json.loads(output.getvalue())

    def by_label(self, shown):
        return [(s["label"], s["id"].split(":")[0], s["position"], s["display"]) for s in shown]

    def test_spaces_are_projected_with_stable_ids_and_no_raw_niri_ids(self):
        self.assertEqual(self.by_label(self.spaces), [
            ("Mail", "pin", 1, "Main"), ("Studio", "group", 2, "Main"),
            ("org.example.Term", "window", 3, "Main"), ("Chat", "pin", 1, "External 1")])
        terminal = self.spaces[2]
        self.assertEqual(terminal["id"], core.opaque("session", 4))
        self.assertNotEqual(terminal["id"], "window:4")
        self.assertEqual(self.spaces[1]["apps"], ["org.example.Notes", "org.example.Paint"])
        self.assertEqual([s["active"] for s in self.spaces], [False, True, False, False])
        self.assertEqual(set(core.public(self.spaces[0])), {"id", "kind", "pinned", "position", "display", "active",
                                                            "available", "label", "apps"})

    def paint(self):
        """Paint as a Flatpak: the caller (pid 500) and the process behind its window (pid 102) share its sandbox."""
        self.proc.process(500, 1, flatpak="[Application]\nname=org.example.Paint\n")
        self.proc.process(PIDS[2], 1, flatpak="[Application]\nname=org.example.Paint\n")
        return core.caller(500, self.proc.root)

    def test_a_flatpak_app_sees_only_its_own_part_of_a_shared_space(self):
        self.paint()
        paint = core.caller(500, self.proc.root)
        self.assertEqual((paint["sandboxed"], paint["app_id"]), (True, "org.example.Paint"))
        # Notes shares the Studio column with Paint and has a private title; Paint never sees it.
        self.windows[1]["title"] = "Statement - MyBank - Notes"
        shown = core.visible(paint, self.spaces, self.windows, control=False)
        self.assertEqual([(s["id"], s["label"], s["apps"]) for s in shown],
                         [("group:g", "org.example.Paint", ["org.example.Paint"])])
        self.assertNotIn("MyBank", json.dumps([core.public(s) for s in shown]))
        self.assertEqual(core.capabilities(paint), (1, 2, ["spaces.list", "spaces.focus", "spaces.changed", "system.ui",
                                                         "activities"]))

    def test_an_app_moves_focus_only_while_it_has_focus(self):
        paint = self.paint()
        # Notes (window 1) has focus, so Paint may not raise its own Group space over it.
        with self.assertRaises(core.ApiError) as raised:
            core.focus_target(paint, "group:g", self.spaces, self.windows, False)
        self.assertEqual(raised.exception.kind, "NotAuthorized")
        self.niri("action", "focus-window", "--id", 2)
        self.spaces, self.windows = core.project(self.observe(), "session")
        # Notes is first in the shared Group, but Paint raises its own window, never hands focus to Notes.
        self.assertEqual(core.focus_target(paint, "group:g", self.spaces, self.windows, False), ("focus-window", "2"))
        for space_id, kind in (("pin:m", "NotAuthorized"), (core.opaque("session", 4), "NotAuthorized"),
                               ("pin:missing", "NotFound")):
            with self.subTest(space_id=space_id), self.assertRaises(core.ApiError) as raised:
                core.focus_target(paint, space_id, self.spaces, self.windows, False)
            self.assertEqual(raised.exception.kind, kind)
        with self.assertRaises(core.ApiError) as raised:
            core.visible(paint, self.spaces, self.windows, control=True)
        self.assertEqual(raised.exception.kind, "NotAuthorized")

    def test_a_window_is_a_flatpak_apps_only_if_drawn_from_its_sandbox(self):
        paint = self.paint()
        # Notes is a native app that sets Paint's app ID on its window, and it has focus.
        self.windows[1].update(app_id="org.example.Paint", title="root@prod: ~ - ssh")
        self.proc.process(PIDS[1], 1)
        shown = core.visible(paint, self.spaces, self.windows, control=False)
        self.assertEqual([(s["id"], s["label"]) for s in shown], [("group:g", "org.example.Paint")])
        self.assertNotIn("prod", json.dumps([core.public(s) for s in shown]))
        for request in (lambda: core.ui_request(paint, "desk", self.windows),
                        lambda: core.focus_target(paint, "group:g", self.spaces, self.windows, False)):
            with self.assertRaises(core.ApiError) as raised:
                request()
            self.assertEqual(raised.exception.kind, "NotAuthorized")
        # Another Flatpak's window, and a window whose process is gone, are not Paint's either.
        self.proc.process(PIDS[4], 1, flatpak="[Application]\nname=org.example.Term\n")
        self.windows[4]["app_id"] = "org.example.Paint"
        self.windows[3].update(app_id="org.example.Paint", pid=99999)
        self.assertEqual([s["id"] for s in core.visible(paint, self.spaces, self.windows, control=False)], ["group:g"])
        # Paint's own window counts whatever app ID it sets.
        self.windows[2]["app_id"] = "paint"
        self.assertEqual([s["apps"] for s in core.visible(paint, self.spaces, self.windows, control=False)], [["paint"]])

    def test_an_unsandboxed_command_acts_as_the_app_it_runs_in(self):
        # A command started from the terminal (pid 104) through a shell.
        self.proc.process(104, 1, comm="term")
        self.proc.process(600, 104, comm="sh (login)")
        self.proc.process(601, 600, comm="flow")
        command = core.caller(601, self.proc.root)
        self.assertEqual(command["lineage"], {601, 600, 104})
        shown = core.visible(command, self.spaces, self.windows, control=False)
        self.assertEqual([s["label"] for s in shown], ["org.example.Term"])
        self.assertEqual(len(core.visible(command, self.spaces, self.windows, control=True)), 4)
        # Another display's pin is owned through its live window too.
        self.proc.process(108, 1, comm="chat")
        chat = core.caller(108, self.proc.root)
        self.assertEqual([s["id"] for s in core.visible(chat, self.spaces, self.windows, control=False)], ["pin:c"])
        self.assertEqual(core.focus_target(command, "pin:c", self.spaces, self.windows, True), ("focus", "c"))

    def test_in_a_shared_space_spaces_focuses_the_callers_window_and_control_the_first(self):
        # A command run from Paint (window 2, pid 102) while Paint has focus; Notes is first in the Studio Group.
        self.proc.process(PIDS[2], 1, comm="paint")
        self.proc.process(610, PIDS[2], comm="flow")
        command = core.caller(610, self.proc.root)
        self.niri("action", "focus-window", "--id", 2)
        self.spaces, self.windows = core.project(self.observe(), "session")
        self.assertEqual(core.focus_target(command, "group:g", self.spaces, self.windows, False), ("focus-window", "2"))
        # Control acts for the user, not for an app: any listed space, its first window, even another app's.
        self.assertEqual(core.focus_target(command, "group:g", self.spaces, self.windows, True), ("focus-window", "1"))
        self.assertEqual(core.focus_target(command, core.opaque("session", 4), self.spaces, self.windows, True),
                         ("focus-window", "4"))

    def test_unidentifiable_or_nameless_sandboxed_callers_are_refused(self):
        self.proc.process(700, 1, flatpak="[Application]\n")
        # A sandbox root that cannot be read counts as sandboxed, never as a trusted caller.
        self.proc.process(701, 1)
        (self.proc.root / "701" / "root").chmod(0)
        self.addCleanup((self.proc.root / "701" / "root").chmod, 0o755)
        for pid in (0, 700, "x") + ((701,) if os.geteuid() else ()):  # root reads any directory
            with self.subTest(pid=pid), self.assertRaises(core.ApiError) as raised:
                core.caller(pid, self.proc.root)
            self.assertEqual(raised.exception.kind, "NotAuthorized")

    def run_steps(self, steps):
        for action, target in steps:
            with patch.object(sys, "argv", ["flow-spaces", action] + ([target] if target is not None else [])), \
                 patch.object(spaces, "snapshot", side_effect=self.niri.snapshot), \
                 patch.object(spaces, "display_state", side_effect=self.niri.display_state), \
                 patch.object(spaces, "full_state", side_effect=lambda: (*spaces.snapshot(), *spaces.display_state())), \
                 patch.object(spaces, "session_id", return_value="session"), \
                 patch.object(spaces, "command", side_effect=self.niri), \
                 contextlib.redirect_stdout(io.StringIO()):
                spaces.run_locked(self.state)

    def test_control_pins_a_space_and_gives_focus_back(self):
        self.proc.process(900, 1)
        tool = core.caller(900, self.proc.root)
        terminal = core.opaque("session", 4)
        steps = core.pin_steps(tool, terminal, self.spaces, self.windows)
        self.assertEqual(steps, [("pin", "4")])
        # Focus moving to another space meanwhile cannot change what gets pinned: the window is named.
        self.niri("action", "focus-window", "--id", 2)
        self.run_steps(steps)
        self.spaces, self.windows = core.project(self.observe(), "session")
        self.assertEqual([s["label"] for s in self.spaces if s["pinned"] and s["display"] == "Main"],
                         ["Mail", "org.example.Term"])
        self.assertEqual(self.niri.active["eDP-1"], 2)
        pinned = next(s["id"] for s in self.spaces if s["label"] == "org.example.Term")
        self.assertEqual(core.move_steps(tool, pinned, "left", self.spaces), [("left", pinned[4:])])
        for space_id, direction, message in ((pinned, "right", "already at that end"), ("pin:m", "left", "already at that end"),
                                             ("pin:c", "left", "another display")):
            with self.subTest(space_id=space_id, direction=direction), self.assertRaises(core.ApiError) as raised:
                core.move_steps(tool, space_id, direction, self.spaces)
            self.assertIn(message, str(raised.exception))
        self.assertEqual(core.unpin_steps(tool, pinned, self.spaces), [("unpin", pinned[4:])])
        self.assertEqual(core.pin_steps(tool, "group:g", self.spaces, self.windows), [("pin-group", "g")])
        for call, kind in ((lambda: core.pin_steps(tool, "pin:m", self.spaces, self.windows), "InvalidState"),
                           (lambda: core.unpin_steps(tool, "group:g", self.spaces), "InvalidState"),
                           (lambda: core.move_steps(tool, "pin:m", "up", self.spaces), "InvalidArgs"),
                           (lambda: core.move_steps(tool, "pin:nope", "left", self.spaces), "NotFound")):
            with self.subTest(kind=kind), self.assertRaises(core.ApiError) as raised:
                call()
            self.assertEqual(raised.exception.kind, kind)

    def test_sandboxed_apps_cannot_pin_and_ask_for_system_ui_only_with_focus(self):
        paint = self.paint()
        with self.assertRaises(core.ApiError) as raised:
            core.pin_steps(paint, "group:g", self.spaces, self.windows)
        self.assertEqual(raised.exception.kind, "NotAuthorized")
        with self.assertRaises(core.ApiError) as raised:
            core.ui_request(paint, "overview", self.windows)
        self.assertEqual(raised.exception.kind, "NotAuthorized")
        self.niri("action", "focus-window", "--id", 2)
        self.spaces, self.windows = core.project(self.observe(), "session")
        self.assertEqual(core.ui_request(paint, "overview", self.windows), "overview_open")
        with self.assertRaises(core.ApiError) as raised:
            core.ui_request(paint, "launcher", self.windows)
        self.assertEqual(raised.exception.kind, "NotAuthorized")
        # Behind Desk or a launcher the app keeps Niri's active window but not the keyboard: no UI requests.
        self.niri.shell_has_keyboard = True
        self.spaces, self.windows = core.project(self.observe(), "session")
        with self.assertRaises(core.ApiError) as raised:
            core.ui_request(paint, "overview", self.windows)
        self.assertEqual(raised.exception.kind, "NotAuthorized")
        with self.assertRaises(core.ApiError) as raised:
            core.focus_target(paint, "group:g", self.spaces, self.windows, False)
        self.assertEqual(raised.exception.kind, "NotAuthorized")
        self.niri.shell_has_keyboard = False
        self.proc.process(900, 1)
        self.assertEqual([core.ui_request(core.caller(900, self.proc.root), what, {}) for what in ("desk", "overview", "launcher")],
                         ["desk_open", "overview_open", "apps_open"])
        for reply, kind in (("busy\n", "InvalidState"), ("", "Failed"), ("toggled", "Failed")):
            with self.subTest(reply=reply), self.assertRaises(core.ApiError) as raised:
                core.ui_answer(reply)
            self.assertEqual(raised.exception.kind, kind)
        core.ui_answer("opened\n")
        core.ui_answer("open")
        with self.assertRaises(core.ApiError) as raised:
            core.ui_request(paint, "settings", self.windows)
        self.assertEqual(raised.exception.kind, "InvalidArgs")

    def test_flow_spaces_errors_map_to_structured_kinds(self):
        cases = {"External 1 is not connected": "DisplayUnavailable", "Pinned space no longer exists": "NotFound",
                 "This pinned app is unavailable or ambiguous": "AppNotRunning", "Workspace changed": "Failed",
                 "Move this pin to this display before reordering it": "InvalidState",
                 "This app is already pinned": "InvalidState",
                 "Return side-by-side Groups to a stack before changing pins or Groups": "InvalidState"}
        self.assertEqual({m: core.error_from(m).kind for m in cases}, cases)

    def test_a_flatpak_app_is_rate_limited_across_its_connections(self):
        self.proc.process(500, 1, flatpak="[Application]\nname=org.example.Paint\n")
        self.proc.process(501, 1)
        paint, other = core.caller(500, self.proc.root), core.caller(501, self.proc.root)
        self.assertEqual({core.limit_key(paint, ":1.7"), core.limit_key(paint, ":1.8")}, {"app:org.example.Paint"})
        self.assertNotEqual(core.limit_key(other, ":1.7"), core.limit_key(other, ":1.8"))

    def test_each_caller_is_rate_limited_on_its_own(self):
        now = [0.0]
        limit = core.RateLimit((3, 10.0), clock=lambda: now[0])
        for _ in range(3):
            limit.check(":1.5")
        with self.assertRaises(core.ApiError) as raised:
            limit.check(":1.5")
        self.assertEqual(raised.exception.kind, "RateLimited")
        limit.check(":1.6")
        now[0] = 10.5
        limit.check(":1.5")
        self.assertEqual(set(limit.calls), {":1.5"})


if __name__ == "__main__":
    unittest.main()
