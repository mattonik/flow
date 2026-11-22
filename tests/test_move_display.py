"""Moving an app, stack or Group to another display as one destination."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "session"))
sys.path.insert(0, str(Path(__file__).parent))
import pin_store
import spaces
from fake_niri import MultiNiri

LAPTOP = {"make": "BOE", "model": "Panel", "serial": None, "logical": {"x": 0, "y": 0, "width": 1280, "height": 800}}
MONITOR = {"make": "Dell", "model": "U2723QE", "serial": "SN1",
           "logical": {"x": 1280, "y": 0, "width": 1920, "height": 1080}}
LAPTOP_KEY = "port:eDP-1|BOE|Panel"
MONITOR_KEY = "edid:Dell|U2723QE|SN1"
OUTPUTS = {"eDP-1": LAPTOP, "DP-1": MONITOR}
APPS = {1: "a", 2: "b", 3: "c", 7: "mail", 8: "terminal", 9: "browser"}


class MoveToDisplayTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)
        (self.state / "groups.initialized").touch()

    def setup(self, layout, focused, groups=(), pins=()):
        self.niri = MultiNiri(layout, APPS, focused, OUTPUTS)
        (self.state / "groups.json").write_text(json.dumps({"version": 1, "groups": list(groups)}))
        displays = {LAPTOP_KEY: {"connector": "eDP-1", "label": "BOE Panel"},
                    MONITOR_KEY: {"connector": "DP-1", "label": "Dell U2723QE"}}
        (self.state / "spaces.json").write_text(json.dumps({"version": 2, "displays": displays, "pins": list(pins)}))

    def invoke(self, *args):
        with patch.object(sys, "argv", ["flow-spaces", *args]), \
             patch.object(spaces, "snapshot", side_effect=self.niri.snapshot), \
             patch.object(spaces, "display_state", side_effect=self.niri.display_state), \
             patch.object(spaces, "full_state", side_effect=lambda: (*spaces.snapshot(), *spaces.display_state())), \
             patch.object(spaces, "session_id", return_value="session"), \
             patch.object(spaces, "command", side_effect=self.niri), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            spaces.run_locked(self.state)
        return json.loads(output.getvalue())

    def saved_pins(self):
        return pin_store.read(self.state / "spaces.json")[0]

    def side_trio(self):
        return {"id": "g", "label": "Trio", "members": ["a", "b", "c"], "layout": "side-by-side",
                "side_ratios": [0.5, 0.25, 0.25]}

    def test_pinned_side_by_side_group_moves_whole_and_joins_the_destination_pins(self):
        self.setup({"eDP-1": [[7], [1], [2], [3]], "DP-1": [[8], [9]]}, focused=2, groups=[self.side_trio()],
                   pins=[{"id": "m", "label": "Mail", "app_id": "mail", "display": LAPTOP_KEY},
                         {"id": "gp", "label": "Trio", "app_id": "a", "members": ["a", "b", "c"], "group_id": "g",
                          "display": LAPTOP_KEY},
                         {"id": "t", "label": "Terminal", "app_id": "terminal", "display": MONITOR_KEY}])
        self.niri.active["DP-1"] = 9
        result = self.invoke("move-to-display", MONITOR_KEY)
        # The Group arrived after the browser, then took its pinned place after Terminal, in order.
        self.assertEqual(self.niri.layout(), {"eDP-1": [["mail"]],
                                              "DP-1": [["terminal"], ["a"], ["b"], ["c"], ["browser"]]})
        self.assertEqual((self.niri.focused_output, self.niri.active["DP-1"]), ("DP-1", 2))
        self.assertEqual([p["id"] for p in result["pins"]], ["t", "gp"])
        self.assertEqual(result["display"]["key"], MONITOR_KEY)
        self.assertEqual({p["id"]: p["display"] for p in self.saved_pins()},
                         {"m": LAPTOP_KEY, "gp": MONITOR_KEY, "t": MONITOR_KEY})
        self.assertEqual([self.niri.widths[i] for i in (1, 2, 3)], [50.0, 25.0, 25.0])
        self.assertEqual([s["group_id"] for s in result["spaces"]], [None, "g", None])

    def test_stack_group_and_single_app_move_as_one_column(self):
        stack = {"id": "g", "label": "Trio", "members": ["a", "b", "c"], "layout": "stack"}
        self.setup({"eDP-1": [[1, 2, 3], [7]], "DP-1": [[8]]}, focused=3, groups=[stack],
                   pins=[{"id": "gp", "label": "Trio", "app_id": "a", "members": ["a", "b", "c"], "group_id": "g",
                          "display": LAPTOP_KEY}])
        self.invoke("move-to-display", MONITOR_KEY)
        self.assertEqual(self.niri.layout(), {"eDP-1": [["mail"]], "DP-1": [["a", "b", "c"], ["terminal"]]})
        self.assertEqual(self.niri.active["DP-1"], 3)
        self.niri("action", "focus-window", "--id", 7)
        result = self.invoke("move-to-display", MONITOR_KEY)
        # An unpinned app lands after the destination's active column; the pinned Group stays first.
        self.assertEqual(self.niri.layout()["DP-1"], [["a", "b", "c"], ["mail"], ["terminal"]])
        self.assertEqual(self.niri.layout()["eDP-1"], [])
        self.assertEqual([p["id"] for p in result["pins"]], ["gp"])

    def test_a_moved_app_never_lands_inside_a_side_by_side_group(self):
        self.setup({"eDP-1": [[7]], "DP-1": [[1], [2], [3], [8]]}, focused=7, groups=[self.side_trio()])
        self.niri.active["DP-1"] = 1
        result = self.invoke("move-to-display", MONITOR_KEY)
        self.assertEqual(self.niri.layout()["DP-1"], [["a"], ["b"], ["c"], ["mail"], ["terminal"]])
        self.assertEqual(self.niri.active["DP-1"], 7)
        self.assertEqual([s["group_id"] for s in result["spaces"]], ["g", None, None])

    def test_an_interrupted_group_move_is_finished_by_moving_again(self):
        self.setup({"eDP-1": [[1], [2], [3]], "DP-1": [[8]]}, focused=1, groups=[self.side_trio()],
                   pins=[{"id": "gp", "label": "Trio", "app_id": "a", "members": ["a", "b", "c"], "group_id": "g",
                          "display": LAPTOP_KEY}])
        moves = []
        self.niri.fail_on = lambda args: args[1] == "move-column-to-monitor" and len(moves.append(1) or moves) == 2
        with self.assertRaisesRegex(ValueError, "stopped part-way"):
            self.invoke("move-to-display", MONITOR_KEY)
        self.assertEqual(self.niri.layout(), {"eDP-1": [["b"], ["c"]], "DP-1": [["terminal"], ["a"]]})
        self.assertEqual(self.saved_pins()[0]["display"], LAPTOP_KEY)
        self.niri.fail_on = None
        self.niri("action", "focus-monitor", "DP-1")
        self.niri("action", "focus-window", "--id", 8)
        self.niri("action", "focus-window", "--id", 3)
        result = self.invoke("move-to-display", MONITOR_KEY)
        self.assertEqual(self.niri.layout(), {"eDP-1": [], "DP-1": [["a"], ["b"], ["c"], ["terminal"]]})
        self.assertEqual(self.saved_pins()[0]["display"], MONITOR_KEY)
        self.assertEqual([s["group_id"] for s in result["spaces"]], ["g", None])

    def test_another_window_of_a_group_app_moves_alone(self):
        pair = {"id": "g", "label": "Pair", "members": ["a", "b"], "layout": "stack",
                "bound": {"session": "session", "windows": {"a": 1, "b": 2}}}
        APPS[5] = "a"
        self.addCleanup(APPS.pop, 5)
        self.setup({"eDP-1": [[1, 2], [5], [7]], "DP-1": [[8]]}, focused=5, groups=[pair],
                   pins=[{"id": "gp", "label": "Pair", "app_id": "a", "members": ["a", "b"], "group_id": "g",
                          "display": LAPTOP_KEY}])
        self.invoke("move-to-display", MONITOR_KEY)
        self.assertEqual(self.niri.layout(), {"eDP-1": [["a", "b"], ["mail"]], "DP-1": [["terminal"], ["a"]]})
        self.assertEqual(self.saved_pins()[0]["display"], LAPTOP_KEY)

    def test_a_side_by_side_group_does_not_freeze_a_display_holding_another_window_of_its_app(self):
        APPS[5] = "a"
        self.addCleanup(APPS.pop, 5)
        trio = {**self.side_trio(), "bound": {"session": "session", "windows": {"a": 1, "b": 2, "c": 3}}}
        self.setup({"eDP-1": [[1], [2], [3]], "DP-1": [[5], [8]]}, focused=8, groups=[trio])
        result = self.invoke("pin")
        self.assertEqual([p["label"] for p in result["pins"]], ["terminal"])

    def test_a_pin_follows_its_windows_even_when_a_later_step_fails(self):
        self.setup({"eDP-1": [[1], [2], [3]], "DP-1": [[8]]}, focused=2, groups=[self.side_trio()],
                   pins=[{"id": "gp", "label": "Trio", "app_id": "a", "members": ["a", "b", "c"], "group_id": "g",
                          "display": LAPTOP_KEY}])
        self.niri.fail_on = lambda args: args[1] == "set-window-width"
        with self.assertRaises(OSError):
            self.invoke("move-to-display", MONITOR_KEY)
        self.assertEqual(self.niri.layout()["DP-1"], [["terminal"], ["a"], ["b"], ["c"]])
        self.assertEqual(self.saved_pins()[0]["display"], MONITOR_KEY)

    def test_a_pinned_app_takes_its_pinned_place_beside_a_side_by_side_group(self):
        self.setup({"eDP-1": [[7]], "DP-1": [[8], [1], [2], [3]]}, focused=7, groups=[self.side_trio()],
                   pins=[{"id": "t", "label": "Terminal", "app_id": "terminal", "display": MONITOR_KEY},
                         {"id": "m", "label": "Mail", "app_id": "mail", "display": LAPTOP_KEY}])
        self.niri.active["DP-1"] = 3
        self.invoke("move-to-display", MONITOR_KEY)
        self.assertEqual(self.niri.layout()["DP-1"], [["terminal"], ["mail"], ["a"], ["b"], ["c"]])
        self.assertEqual(self.niri.active["DP-1"], 7)

    def test_a_pin_follows_a_group_that_arrived_out_of_order(self):
        self.setup({"eDP-1": [[1], [2], [3]], "DP-1": [[8]]}, focused=2, groups=[self.side_trio()],
                   pins=[{"id": "gp", "label": "Trio", "app_id": "a", "members": ["a", "b", "c"], "group_id": "g",
                          "display": LAPTOP_KEY}])

        def scramble(args):
            # Another client reorders the destination just after the last column arrives.
            if args[1] == "focus-window" and len(self.niri.columns["DP-1"]) == 4:
                cols = self.niri.columns["DP-1"]
                cols[1], cols[3] = cols[3], cols[1]
            return False

        self.niri.fail_on = scramble
        with self.assertRaisesRegex(ValueError, "arrived out of order"):
            self.invoke("move-to-display", MONITOR_KEY)
        self.assertEqual(self.saved_pins()[0]["display"], MONITOR_KEY)

    def test_a_layer_is_not_moved_to_another_display(self):
        ws = {"id": 1, "active_window_id": 9, "output": "eDP-1"}
        layer = {"id": 9, "app_id": "notes", "workspace_id": 1, "is_floating": True,
                 "layout": {"pos_in_scrolling_layout": None}}
        with self.assertRaisesRegex(ValueError, "Make this Layer a space"):
            spaces.move_plan([], ws, {"id": 2}, [layer])

    def test_refuses_unsafe_moves_without_moving_anything(self):
        self.setup({"eDP-1": [[1], [7], [2], [3]], "DP-1": [[8]]}, focused=1, groups=[self.side_trio()])
        for target, message in ((LAPTOP_KEY, "already on that display"), ("port:HDMI-A-1||", "not connected"),
                                (MONITOR_KEY, "Repair the Group")):
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, message):
                self.invoke("move-to-display", target)
        self.assertEqual(self.niri.layout()["eDP-1"], [["a"], ["mail"], ["b"], ["c"]])
        self.assertFalse(any(args[1].startswith("move-") for args in self.niri.log if args[0] == "action"))

    def portrait_monitor(self):
        self.niri.outputs = {**OUTPUTS, "DP-1": {**MONITOR, "logical": {**MONITOR["logical"], "width": 1080, "height": 1920}}}

    def test_a_group_following_orientation_is_stacked_on_a_portrait_display(self):
        trio = {**self.side_trio(), "auto_layout": True}
        self.setup({"eDP-1": [[1], [2], [3]], "DP-1": [[8]]}, focused=2, groups=[trio])
        self.portrait_monitor()
        with patch.object(spaces.time, "sleep"):
            result = self.invoke("move-to-display", MONITOR_KEY)
        self.assertEqual(self.niri.layout()["DP-1"], [["terminal"], ["a", "b", "c"]])
        self.assertEqual(result["notice"], "Trio was stacked for the portrait Dell U2723QE")
        saved = json.loads((self.state / "groups.json").read_text())["groups"][0]
        self.assertEqual((saved["layout"], saved["side_ratios"]), ("stack", [0.5, 0.25, 0.25]))
        self.assertNotIn("pending_layout", saved)

    def test_a_group_whose_apps_do_not_fit_is_stacked_and_keeps_its_proportions(self):
        self.setup({"eDP-1": [[1], [2], [3]], "DP-1": [[8]]}, focused=2, groups=[self.side_trio()])
        self.niri.minimum = {3: 60.0}
        with patch.object(spaces.time, "sleep"):
            result = self.invoke("move-to-display", MONITOR_KEY)
        self.assertEqual(self.niri.layout()["DP-1"], [["terminal"], ["a", "b", "c"]])
        self.assertEqual(result["notice"], "Trio was stacked to fit Dell U2723QE")
        saved = json.loads((self.state / "groups.json").read_text())["groups"][0]
        self.assertEqual(saved["side_ratios"], [0.5, 0.25, 0.25])

    def test_a_manual_side_by_side_group_that_fits_stays_side_by_side_on_portrait(self):
        self.setup({"eDP-1": [[1], [2], [3]], "DP-1": [[8]]}, focused=2, groups=[self.side_trio()])
        self.portrait_monitor()
        with patch.object(spaces.time, "sleep"):
            result = self.invoke("move-to-display", MONITOR_KEY)
        self.assertEqual(self.niri.layout()["DP-1"], [["terminal"], ["a"], ["b"], ["c"]])
        self.assertEqual(result["notice"], "")

    def test_lists_connected_displays_in_screen_order(self):
        self.setup({"DP-1": [[8]], "eDP-1": [[7]]}, focused=7)
        result = self.invoke("refresh")
        self.assertEqual(result["displays"], [
            {"key": LAPTOP_KEY, "label": "BOE Panel", "name": "Main", "number": 0, "current": True},
            {"key": MONITOR_KEY, "label": "Dell U2723QE", "name": "External 1", "number": 1, "current": False}])


if __name__ == "__main__":
    unittest.main()
