"""User-stable display numbers and the Super / Super+Alt pinned-space shortcuts."""
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


def records(*connectors):
    return {f"k-{c}": {"connector": c, "label": c} for c in connectors}


class NumberingTest(unittest.TestCase):
    def test_built_in_panel_is_main_and_externals_count_in_screen_order(self):
        displays = records("DP-2", "eDP-1", "HDMI-A-1")
        pin_store.assign_numbers(displays, ["k-HDMI-A-1", "k-eDP-1", "k-DP-2"])
        self.assertEqual({k: r["number"] for k, r in displays.items()},
                         {"k-eDP-1": 0, "k-HDMI-A-1": 1, "k-DP-2": 2})
        self.assertEqual([pin_store.display_name(displays[k]) for k in ("k-eDP-1", "k-DP-2")], ["Main", "External 2"])

    def test_numbers_stay_with_disconnected_displays_and_new_ones_take_the_lowest_free(self):
        displays = records("eDP-1", "DP-1", "DP-2")
        pin_store.assign_numbers(displays, ["k-eDP-1", "k-DP-1", "k-DP-2"])
        # External 1 is unplugged; a new monitor arrives while External 2 stays.
        displays.update(records("HDMI-A-1"))
        pin_store.assign_numbers(displays, ["k-eDP-1", "k-HDMI-A-1", "k-DP-2"])
        self.assertEqual({k: r["number"] for k, r in displays.items()},
                         {"k-eDP-1": 0, "k-DP-1": 1, "k-DP-2": 2, "k-HDMI-A-1": 3})
        # Without a built-in panel the first display in screen order is Main.
        desk = records("DP-3", "DP-4")
        pin_store.assign_numbers(desk, ["k-DP-4", "k-DP-3"])
        self.assertEqual((desk["k-DP-4"]["number"], desk["k-DP-3"]["number"]), (0, 1))

    def test_invalid_or_duplicated_numbers_are_replaced_and_main_can_be_chosen(self):
        displays = records("eDP-1", "DP-1", "DP-2")
        displays["k-DP-1"]["number"], displays["k-DP-2"]["number"], displays["k-eDP-1"]["number"] = 1, 1, True
        pin_store.assign_numbers(displays, ["k-eDP-1", "k-DP-1", "k-DP-2"])
        self.assertEqual(sorted(r["number"] for r in displays.values()), [0, 1, 2])
        pin_store.make_main(displays, "k-DP-2")
        self.assertEqual((displays["k-DP-2"]["number"], displays["k-eDP-1"]["number"]), (0, 2))
        with self.assertRaisesRegex(ValueError, "already the main"):
            pin_store.make_main(displays, "k-DP-2")


LAPTOP = {"make": "BOE", "model": "Panel", "serial": None, "logical": {"x": 0, "y": 0, "width": 1280, "height": 800}}
MONITOR = {"make": "Dell", "model": "U2723QE", "serial": "SN1",
           "logical": {"x": 1280, "y": 0, "width": 1920, "height": 1080}}
LAPTOP_KEY = "port:eDP-1|BOE|Panel"
MONITOR_KEY = "edid:Dell|U2723QE|SN1"


class ShortcutTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)
        (self.state / "groups.initialized").touch()
        (self.state / "groups.json").write_text('{"version": 1, "groups": []}')
        displays = {LAPTOP_KEY: {"connector": "eDP-1", "label": "BOE Panel"},
                    MONITOR_KEY: {"connector": "DP-1", "label": "Dell U2723QE"}}
        pins = [{"id": "m1", "label": "Mail", "app_id": "mail", "display": LAPTOP_KEY},
                {"id": "x1", "label": "Terminal", "app_id": "terminal", "display": MONITOR_KEY},
                {"id": "x2", "label": "Files", "app_id": "files", "display": MONITOR_KEY}]
        (self.state / "spaces.json").write_text(json.dumps({"version": 2, "displays": displays, "pins": pins}))
        self.niri = MultiNiri({"eDP-1": [[1]], "DP-1": [[2], [3]]}, {1: "mail", 2: "terminal", 3: "files"}, 1,
                              {"eDP-1": LAPTOP, "DP-1": MONITOR})

    def invoke(self, *args, outputs=None):
        state = lambda: (outputs or self.niri.outputs, self.niri.workspaces())  # noqa: E731
        with patch.object(sys, "argv", ["flow-spaces", *args]), \
             patch.object(spaces, "snapshot", side_effect=self.niri.snapshot), \
             patch.object(spaces, "display_state", side_effect=state), \
             patch.object(spaces, "session_id", return_value="session"), \
             patch.object(spaces, "command", side_effect=self.niri), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            spaces.run_locked(self.state)
        return json.loads(output.getvalue())

    def test_external_display_digit_then_space_digit_focuses_that_pin_directly(self):
        result = self.invoke("focus-number", "1:2")
        # The external display is focused first; Main is never an intermediate stop.
        self.assertEqual(self.niri.log[0], ("action", "focus-monitor", "DP-1"))
        self.assertEqual((self.niri.focused_output, self.niri.active["DP-1"]), ("DP-1", 3))
        self.assertEqual((result["display"]["name"], result["display"]["number"]), ("External 1", 1))

    def test_super_number_means_the_main_display_from_anywhere(self):
        self.niri("action", "focus-monitor", "DP-1")
        self.invoke("focus-number", "0:1")
        self.assertEqual((self.niri.focused_output, self.niri.active["eDP-1"]), ("eDP-1", 1))

    def test_unknown_or_unavailable_targets_are_reported_without_moving_focus(self):
        self.invoke("refresh")
        for arg, message in (("1:3", "External 1 has no pinned space 3"), ("2:1", "External 2 has not been connected"),
                             ("x", "Choose a display")):
            with self.subTest(arg=arg), self.assertRaisesRegex(ValueError, message):
                self.invoke("focus-number", arg)
        with self.assertRaisesRegex(ValueError, "External 1 is not connected"):
            self.invoke("focus-number", "1:1", outputs={"eDP-1": LAPTOP})
        self.assertFalse([a for a in self.niri.log if a[:2] in (("action", "focus-monitor"), ("action", "focus-window"))])

    def test_super_number_uses_the_lowest_numbered_display_while_main_is_away(self):
        self.invoke("refresh")
        self.niri("action", "focus-monitor", "DP-1")
        result = self.invoke("focus-number", "0:2", outputs={"DP-1": MONITOR})
        self.assertEqual(self.niri.active["DP-1"], 3)
        self.assertEqual(result["notice"], "The main display is not connected; using External 1")

    def test_main_can_be_moved_to_another_display_and_numbers_persist(self):
        self.invoke("make-main", MONITOR_KEY)
        displays = pin_store.read(self.state / "spaces.json")[1]
        self.assertEqual((displays[MONITOR_KEY]["number"], displays[LAPTOP_KEY]["number"]), (0, 1))
        self.invoke("focus-number", "0:1")
        self.assertEqual(self.niri.active["DP-1"], 2)
        self.niri("action", "focus-monitor", "eDP-1")
        result = self.invoke("refresh", outputs={"eDP-1": LAPTOP})
        self.assertEqual([p["display_name"] for p in result["other_pins"]], ["Main", "Main"])


if __name__ == "__main__":
    unittest.main()
