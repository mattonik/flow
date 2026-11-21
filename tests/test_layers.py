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
        spaces.app_name.cache_clear()
        self.addCleanup(spaces.app_name.cache_clear)
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

    def test_on_a_portrait_output_a_layer_is_a_bottom_sheet(self):
        portrait = {"Virtual-1": {"logical": {"x": 0, "y": 0, "width": 800, "height": 1280}}}
        self.niri("action", "focus-window", "--id", 9)
        with patch.object(spaces, "display_state", return_value=(portrait, [])):
            self.run_op("make-layer")
        self.assertIn(("action", "set-window-width", "--id", 9, "100%"), self.niri.log)
        self.assertIn(("action", "set-window-height", "--id", 9, "50%"), self.niri.log)
        self.assertIn(("action", "move-floating-window", "--id", 9, "-x", "0", "-y", "640"), self.niri.log)
        self.assertNotIn(("action", "center-window", "--id", 9), self.niri.log)

    def test_a_landscape_layer_is_centered_once_its_new_size_is_committed(self):
        landscape = {"Virtual-1": {"logical": {"x": 0, "y": 0, "width": 1280, "height": 800}}}
        heights = iter([634, 634, 480])
        snapshot = self.niri.snapshot

        def settling():
            ws, windows = snapshot()
            for w in windows:
                if w["is_floating"]:
                    w["layout"]["tile_size"] = [576, next(heights, 480)]
            return ws, windows

        self.niri("action", "focus-window", "--id", 9)
        with patch.object(spaces, "display_state", return_value=(landscape, [])), \
             patch.object(spaces, "snapshot", side_effect=settling):
            self.run_op("make-layer")
        self.assertEqual(next(heights, "done"), "done")
        self.assertIn(("action", "center-window", "--id", 9), self.niri.log)

    def test_a_committed_height_ends_the_wait_even_off_the_expected_size(self):
        landscape = {"Virtual-1": {"logical": {"x": 0, "y": 0, "width": 1280, "height": 800}}}
        heights = iter([634, 450, 999])
        snapshot = self.niri.snapshot

        def settling():
            ws, windows = snapshot()
            for w in windows:
                if w["is_floating"]:
                    w["layout"]["tile_size"] = [576, next(heights, 999)]
            return ws, windows

        self.niri("action", "focus-window", "--id", 9)
        with patch.object(spaces, "display_state", return_value=(landscape, [])), \
             patch.object(spaces, "snapshot", side_effect=settling):
            self.run_op("make-layer")
        # A working area smaller than the output gives 450, not 480; the wait still ends at once.
        self.assertEqual(next(heights, None), 999)

    def test_navigating_past_the_last_space_from_a_layer_keeps_the_layer(self):
        with self.launch("notes"):
            self.run_op("open-layer", "org.example.Notes")
        self.niri("action", "focus-window", "--id", 9)
        self.niri("action", "focus-window", "--id", 50)
        self.run_op("navigate", "right")
        self.assertEqual(self.niri.active, 50)

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

    def test_navigating_from_a_layer_moves_from_the_space_beneath_it(self):
        with self.launch("notes"):
            self.run_op("open-layer", "org.example.Notes")
        self.run_op("navigate", "left")
        self.assertEqual(self.niri.active, 7)
        self.assertEqual(self.niri.floating, [50])

    def test_a_layer_joins_the_side_by_side_group_beneath_it_with_an_equal_share(self):
        with self.launch("notes"):
            self.run_op("open-layer", "org.example.Notes")
        self.run_op("layer-to-group", "50")
        self.assertEqual((self.niri.layout(), self.niri.floating, self.niri.active),
                         ([["mail"], ["a"], ["b"], ["notes"], ["other"]], [], 50))
        self.assertEqual(self.group["members"], ["a", "b", "notes"])
        self.assertEqual([self.niri.widths[i] for i in (1, 2, 50)], [40.0, 26.67, 33.33])
        self.assertEqual(self.pins[1]["members"], ["a", "b", "notes"])

    def test_a_layer_joins_a_stacked_group_at_the_bottom(self):
        self.niri.columns = [[7], [1, 2], [9]]
        self.group = {"id": "g", "label": "Pair", "members": ["a", "b"], "layout": "stack"}
        with self.launch("notes"):
            self.run_op("open-layer", "org.example.Notes")
        self.run_op("layer-to-group", "50")
        self.assertEqual(self.niri.layout(), [["mail"], ["a", "b", "notes"], ["other"]])

    def test_a_layer_over_another_space_or_a_full_group_stays_a_layer(self):
        self.niri("action", "focus-window", "--id", 7)
        with self.launch("notes"):
            self.run_op("open-layer", "org.example.Notes")
        with self.assertRaisesRegex(ValueError, "not a Group"):
            self.run_op("layer-to-group", "50")
        self.assertEqual((self.niri.floating, self.niri.active), ([50], 50))
        self.niri.apps.update({3: "c", 4: "d"})
        self.niri.columns = [[7], [1], [2], [3], [4], [9]]
        self.group.update(members=["a", "b", "c", "d"], side_ratios=[0.25] * 4)
        self.group.pop("side_ratio")
        self.niri("action", "focus-window", "--id", 1)
        self.niri("action", "focus-window", "--id", 50)
        with self.assertRaisesRegex(ValueError, "up to 4"):
            self.run_op("layer-to-group", "50")
        self.assertEqual((self.niri.floating, self.niri.active), ([50], 50))

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


class LayerRotationTest(unittest.TestCase):
    """An open Layer takes the new shape when its output rotates, and only then."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name) / "flow"
        self.state.mkdir()
        (self.state / "groups.initialized").touch()
        (self.state / "groups.json").write_text('{"version": 1, "groups": []}')
        self.niri = FakeNiri([[7], [9]], {7: "mail", 9: "notes"}, 9)
        self.size = (1280, 800)
        for item in (patch.dict(os.environ, {"XDG_STATE_HOME": directory.name}),
                     patch.object(spaces.time, "sleep")):
            item.start()
            self.addCleanup(item.stop)

    def invoke(self, *args):
        import contextlib
        import io
        outputs = {"Virtual-1": {"make": "QEMU", "model": "Monitor", "serial": None,
                                 "logical": {"x": 0, "y": 0, "width": self.size[0], "height": self.size[1]}}}
        with patch.object(sys, "argv", ["flow-spaces", *args]), \
             patch.object(spaces, "snapshot", side_effect=self.niri.snapshot), \
             patch.object(spaces, "display_state", side_effect=lambda: (outputs, [self.niri.snapshot()[0]])), \
             patch.object(spaces, "session_id", return_value="s"), \
             patch.object(spaces, "command", side_effect=self.niri), \
             contextlib.redirect_stdout(io.StringIO()):
            spaces.run_locked(self.state)

    def sheet_moves(self):
        return [a for a in self.niri.log if a[:2] == ("action", "move-floating-window")]

    def test_rotating_reshapes_the_layer_and_leaves_focus_alone(self):
        self.invoke("make-layer")
        self.niri("action", "focus-window", "--id", 7)
        self.invoke("auto-layout")
        self.assertEqual(self.sheet_moves(), [])
        self.size = (800, 1280)
        self.invoke("auto-layout")
        self.assertEqual(self.sheet_moves(), [("action", "move-floating-window", "--id", 9, "-x", "0", "-y", "640")])
        self.assertEqual(self.niri.active, 7)
        # Nothing changes while the orientation stays the same, so a resized Layer is left alone.
        self.invoke("auto-layout")
        self.assertEqual(len(self.sheet_moves()), 1)
        self.size = (1280, 800)
        start = len(self.niri.log)
        self.invoke("auto-layout")
        actions = [a for a in self.niri.log[start:] if a[0] == "action"]
        self.assertEqual(actions[-2:], [("action", "center-window", "--id", 9), ("action", "focus-window", "--id", 7)])

    def test_a_rotation_right_after_opening_a_layer_is_noticed(self):
        self.invoke("make-layer")
        self.size = (800, 1280)
        self.invoke("auto-layout")
        self.assertEqual(len(self.sheet_moves()), 1)
