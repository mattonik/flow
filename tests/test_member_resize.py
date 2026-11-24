"""Narrower and Wider for apps in a side-by-side Group."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "session"))
sys.path.insert(0, str(Path(__file__).parent))
import groups as group_store
import spaces
from fake_niri import FakeNiri

APPS = {1: "a", 2: "b", 3: "c", 9: "other"}


class StubbornNiri(FakeNiri):
    """An app with a minimum width: Niri asks, the client keeps at least ``minimum`` percent."""

    def __init__(self, *args, minimum=None, **kw):
        super().__init__(*args, **kw)
        self.minimum = minimum or {}

    def __call__(self, *args):
        result = super().__call__(*args)
        if args[:2] == ("action", "set-window-width"):
            ident = int(args[args.index("--id") + 1])
            self.widths[ident] = max(self.widths[ident], self.minimum.get(ident, 0))
        return result


class MemberResizeTest(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(spaces.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def niri(self, **kw):
        niri = StubbornNiri([[9], [1], [2], [3]], APPS, 1, **kw)
        niri.widths = {9: 100.0, 1: 50.0, 2: 25.0, 3: 25.0}
        for item in (patch.object(spaces, "snapshot", side_effect=niri.snapshot),
                     patch.object(spaces, "display_state", side_effect=niri.display_state),
                     patch.object(spaces, "command", side_effect=niri)):
            item.start()
            self.addCleanup(item.stop)
        return niri

    def trio(self, layout="side-by-side"):
        return {"id": "g", "label": "Trio", "members": ["a", "b", "c"], "layout": layout,
                "side_ratios": [0.5, 0.25, 0.25]}

    def run_op(self, niri, op, arg, registry):
        ws, windows = niri.snapshot()
        return spaces.run([], op, arg, ws, windows, "s", registry)

    def test_wider_takes_width_from_the_next_app_and_is_saved(self):
        niri, registry = self.niri(), [self.trio()]
        self.run_op(niri, "member-wider", "1", registry)
        self.assertEqual((niri.widths[1], niri.widths[2], niri.widths[3]), (60.0, 15.0, 25.0))
        self.assertEqual(group_store.ratios(registry[0]), [0.6, 0.15, 0.25])
        self.assertEqual(niri.layout(), [["other"], ["a"], ["b"], ["c"]])
        # The last app trades with the one before it.
        self.run_op(niri, "member-narrower", "3", registry)
        self.assertEqual((niri.widths[2], niri.widths[3]), (25.0, 15.0))
        self.assertEqual(group_store.ratios(registry[0]), [0.6, 0.25, 0.15])
        # A middle app trades with the next one, leaving the first app's width alone.
        self.run_op(niri, "member-narrower", "2", registry)
        self.assertEqual((niri.widths[1], niri.widths[2], niri.widths[3]), (60.0, 15.0, 25.0))

    def test_steps_stop_at_the_minimum_share_without_changing_anything(self):
        niri, registry = self.niri(), [self.trio()]
        self.run_op(niri, "member-wider", "1", registry)
        before = dict(niri.widths)
        with self.assertRaisesRegex(ValueError, "already as wide"):
            self.run_op(niri, "member-wider", "1", registry)
        with self.assertRaisesRegex(ValueError, "already as narrow"):
            self.run_op(niri, "member-narrower", "2", registry)
        self.assertEqual(niri.widths, before)
        ws, windows = niri.snapshot()
        space = next(s for s in spaces.display_spaces(ws, windows, registry) if s["group_id"])
        self.assertEqual(space["shares"], [60, 15, 25])
        self.assertEqual((space["can_widen"], space["can_narrow"]), ([False, True, False], [True, False, True]))

    def test_size_limits_keep_the_closest_size_and_say_so(self):
        niri, registry = self.niri(minimum={2: 22.0}), [self.trio()]
        with self.assertRaisesRegex(ValueError, "size limits"):
            self.run_op(niri, "member-wider", "1", registry)
        self.assertEqual(group_store.ratios(registry[0]), [round(60 / 107, 4), round(22 / 107, 4), round(25 / 107, 4)])

    def test_only_side_by_side_groups_are_resized(self):
        niri = self.niri()
        niri.columns = [[9], [1, 2, 3]]
        with self.assertRaisesRegex(ValueError, "side by side"):
            self.run_op(niri, "member-wider", "1", [self.trio("stack")])
        self.assertFalse([a for a in niri.log if a[:2] == ("action", "set-window-width")])


if __name__ == "__main__":
    unittest.main()
