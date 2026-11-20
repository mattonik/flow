"""Layers: temporary apps floating over a space, opened and dismissed without changing it."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "session"))
sys.path.insert(0, str(Path(__file__).parent))
import spaces
from fake_niri import FakeNiri

APPS = {1: "a", 2: "b", 7: "mail", 9: "other"}


class LayerTest(unittest.TestCase):
    def setUp(self):
        self.niri = FakeNiri([[7], [1], [2], [9]], APPS, 1)
        self.niri.widths = {7: 100.0, 1: 60.0, 2: 40.0, 9: 100.0}
        self.group = {"id": "g", "label": "Pair", "members": ["a", "b"], "layout": "side-by-side", "side_ratio": 0.6}
        self.pins = [{"id": "m", "label": "Mail", "app_id": "mail"},
                     {"id": "gp", "label": "Pair", "app_id": "a", "members": ["a", "b"], "group_id": "g"}]
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)
        (self.data / "applications").mkdir()
        (self.data / "applications/org.example.Notes.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Notes\nExec=notes\nStartupWMClass=notes\n")
        for item in (patch.object(spaces, "snapshot", side_effect=self.niri.snapshot),
                     patch.object(spaces, "command", side_effect=self.niri),
                     patch.object(spaces.time, "sleep"),
                     patch.dict(os.environ, {"XDG_DATA_HOME": str(self.data), "XDG_DATA_DIRS": str(self.data / "x")})):
            item.start()
            self.addCleanup(item.stop)

    def run_op(self, op, arg=None):
        ws, windows = self.niri.snapshot()
        return spaces.run(self.pins, op, arg, ws, windows, "s", [self.group])

    def tiles(self):
        ws, windows = self.niri.snapshot()
        return {w["id"]: (tuple(w["layout"]["pos_in_scrolling_layout"]), w["layout"]["tile_size"][0])
                for w in windows if not w["is_floating"]}

    def launch(self, app, ident=50):
        def run(args, **kw):
            self.assertEqual(args[:2], ["gio", "launch"])
            self.niri.open_window(ident, app)
        return patch.object(spaces.subprocess, "run", side_effect=run)

    def test_opening_a_layer_over_a_group_leaves_the_group_and_pins_as_they_were(self):
        before = self.tiles()
        with self.launch("org.example.Notes"):
            self.run_op("open-layer", "org.example.Notes")
        # Niri opened Notes between the Group's apps; as a Layer it floats and the strip closes up.
        self.assertEqual(self.tiles(), before)
        self.assertEqual((self.niri.floating, self.niri.active), ([50], 50))
        ws, windows = self.niri.snapshot()
        self.assertEqual([s["group_id"] for s in spaces.display_spaces(ws, windows, [self.group])], [None, "g", None])
        self.assertEqual([(layer["id"], layer["app_name"], layer["active"]) for layer in spaces.layers(ws, windows)],
                         [(50, "Notes", True)])
        self.assertIn(("action", "set-window-width", "--id", 50, "45%"), self.niri.log)
        self.assertFalse([a for a in self.niri.log if a[:2] == ("action", "move-column-to-index")])

    def test_closing_a_layer_returns_focus_to_the_group_without_moving_anything(self):
        before = self.tiles()
        with self.launch("notes"):
            self.run_op("open-layer", "org.example.Notes")
        self.run_op("close-layer", "50")
        self.assertEqual((self.tiles(), self.niri.floating, self.niri.active), (before, [], 1))
        with self.assertRaisesRegex(ValueError, "no longer open"):
            self.run_op("close-layer", "50")
        with self.assertRaisesRegex(ValueError, "no longer open"):
            self.run_op("close-layer", "9")

    def test_a_layer_can_become_its_own_space_after_the_current_one(self):
        self.niri("action", "focus-window", "--id", 9)
        self.run_op("make-layer")
        self.assertEqual((self.niri.layout(), self.niri.floating), ([["mail"], ["a"], ["b"]], [9]))
        self.niri("action", "focus-window", "--id", 7)
        self.niri("action", "focus-window", "--id", 9)
        self.niri.widths[9] = 45.0
        self.run_op("layer-to-space", "9")
        self.assertEqual((self.niri.layout(), self.niri.active), ([["mail"], ["other"], ["a"], ["b"]], 9))
        self.assertEqual(self.niri.widths[9], 100.0)

    def test_only_a_standalone_unpinned_tiled_app_becomes_a_layer(self):
        for active, message in ((1, "Remove this app from its Group"), (7, "Unpin this app")):
            self.niri("action", "focus-window", "--id", active)
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.run_op("make-layer")
        self.niri.columns = [[7], [1], [2], [9, 3]]
        self.niri.apps[3] = "c"
        self.niri("action", "focus-window", "--id", 9)
        with self.assertRaisesRegex(ValueError, "Separate this app"):
            self.run_op("make-layer")
        self.assertEqual(self.niri.floating, [])

    def test_a_layer_is_only_taken_from_a_new_window_of_that_app(self):
        with patch.object(spaces.subprocess, "run"), self.assertRaisesRegex(ValueError, "did not open a new window"):
            self.run_op("open-layer", "org.example.Notes")
        with self.launch("something-else"), self.assertRaisesRegex(ValueError, "did not open a new window"):
            self.run_op("open-layer", "org.example.Notes")
        self.assertEqual(self.niri.floating, [])
        with self.assertRaisesRegex(ValueError, "No matching installed"):
            self.run_op("open-layer", "org.example.Missing")


if __name__ == "__main__":
    unittest.main()
