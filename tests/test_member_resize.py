"""Narrower and Wider for apps in a side-by-side Group."""
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

    def test_a_refused_step_puts_both_apps_back(self):
        """b keeps at least 22%, so a 60/15/25 step leaves a row of 107%; both apps go back to 50/25."""
        niri, registry = self.niri(minimum={2: 22.0}), [self.trio()]
        with self.assertRaisesRegex(ValueError, "size limits prevent that width; the Group keeps its widths"):
            self.run_op(niri, "member-wider", "1", registry)
        self.assertEqual((niri.widths[1], niri.widths[2], niri.widths[3]), (50.0, 25.0, 25.0))
        self.assertEqual(group_store.ratios(registry[0]), [0.5, 0.25, 0.25])

    def test_the_buttons_and_the_steps_agree_on_rows_that_do_not_fill_the_display(self):
        """The switcher's shares and a step are both of the display. The app moves by exactly one step and its
        neighbor takes the rest, so the row fills the display; a disabled step is the one that is refused."""
        # Rows that are not whole percentages, as real displays and apps give, are judged on the same exact
        # shares by the buttons and the step: 25% of 1366 is 341.5 px, and an app at 24.6% shows as 25.
        rows = [(row, 1000) for row in ((50.0, 25.0, 25.0), (40.0, 20.0, 20.0), (60.0, 30.0, 30.0),
                                        (50.0, 15.0, 25.0), (35.0, 30.0, 25.0), (44.4, 24.9, 30.7),
                                        (45.5, 25.0, 29.5), (50.4, 25.0, 24.6))] + [((50.0, 25.0, 24.96), 1366)]
        for row, width in rows:
            for index in range(3):
                for op, flag in (("member-wider", "can_widen"), ("member-narrower", "can_narrow")):
                    with self.subTest(row=row, width=width, app=index + 1, op=op):
                        niri, registry = self.niri(), [self.trio()]
                        niri.logical = {"width": width, "height": 768}
                        niri.widths.update(zip((1, 2, 3), row))
                        ws, windows = niri.snapshot()
                        space = next(s for s in spaces.display_spaces(ws, windows, registry, (), width) if s["group_id"])
                        self.assertEqual(space["shares"], [round(w) for w in row])
                        if space[flag][index]:
                            self.run_op(niri, op, str(index + 1), registry)
                            widths = [niri.widths[i] for i in (1, 2, 3)]
                            step = 10.0 if op == "member-wider" else -10.0
                            self.assertAlmostEqual(widths[index], row[index] + step, places=1)
                            self.assertAlmostEqual(sum(widths), 100.0, places=1)
                        else:
                            with self.assertRaisesRegex(ValueError, "already as"):
                                self.run_op(niri, op, str(index + 1), registry)
                            self.assertEqual(tuple(niri.widths[i] for i in (1, 2, 3)), row)

    def test_a_refused_step_on_a_row_wider_than_the_display_puts_back_its_widths(self):
        niri, registry = self.niri(minimum={2: 32.0}), [self.trio()]
        niri.widths.update({1: 55.0, 2: 35.0, 3: 25.0})  # 115% of the display
        # Narrower gives 45/30/25, which fills the display, but b keeps 32%: a row of 102%.
        with self.assertRaisesRegex(ValueError, "the Group keeps its widths"):
            self.run_op(niri, "member-narrower", "1", registry)
        self.assertEqual((niri.widths[1], niri.widths[2], niri.widths[3]), (55.0, 35.0, 25.0))

    def test_removing_an_app_side_by_side_fills_the_row(self):
        niri, registry = self.niri(), [self.trio()]
        self.run_op(niri, "remove-from-group", "2", registry)
        self.assertEqual(registry[0]["members"], ["a", "c"])
        self.assertEqual((niri.widths[1], niri.widths[3]), (66.67, 33.33))
        self.assertEqual([round(r, 4) for r in group_store.ratios(registry[0])], [0.6667, 0.3333])

    def test_removing_an_app_is_saved_even_if_filling_the_row_fails(self):
        """The app has left the row already, so the fill is best effort."""
        niri, registry = self.niri(), [self.trio()]
        niri.fail_on = lambda args: args[1] == "set-window-width"
        self.run_op(niri, "remove-from-group", "2", registry)
        self.assertEqual(registry[0]["members"], ["a", "c"])

    def test_adding_checks_the_windows_again_before_resizing_them(self):
        niri, group = self.pair_and_neighbor()
        ws, windows = niri.snapshot()
        niri.columns = [[9], [3], [1], [2]]  # the neighbor moved before the action ran
        with self.assertRaisesRegex(ValueError, "Group or focus changed"):
            spaces.run([], "add-to-group", "g", ws, windows, "s", [group])
        self.assertFalse([a for a in niri.log if a[:2] == ("action", "set-window-width")])
        self.assertEqual(group["members"], ["a", "b"])

    def pair_and_neighbor(self, **kw):
        niri = self.niri(**kw)
        niri.widths.update({1: 60.0, 2: 40.0, 3: 100.0})
        return niri, {"id": "g", "label": "Pair", "members": ["a", "b"], "layout": "side-by-side", "side_ratio": 0.6}

    def test_an_app_added_side_by_side_takes_an_equal_share(self):
        """The others keep their proportions to each other; the row fits the display."""
        niri, group = self.pair_and_neighbor()
        self.run_op(niri, "add-to-group", "g", [group])
        self.assertEqual(group["members"], ["a", "b", "c"])
        self.assertEqual((niri.widths[1], niri.widths[2], niri.widths[3]), (40.0, 26.67, 33.33))
        self.assertEqual(group_store.ratios(group), [0.4, 0.2667, 0.3333])

    def test_an_app_that_refuses_its_share_is_not_added(self):
        """The Group's apps go back to the widths they had, and the app to a full-width space."""
        niri, group = self.pair_and_neighbor(minimum={3: 50.0})
        niri.widths.update({1: 55.0, 2: 40.0, 3: 70.0})
        with self.assertRaisesRegex(ValueError, "size limits prevent an equal share; the app was not added"):
            self.run_op(niri, "add-to-group", "g", [group])
        self.assertEqual(group["members"], ["a", "b"])
        self.assertEqual(group_store.ratios(group), [0.6, 0.4])
        self.assertEqual((niri.widths[1], niri.widths[2], niri.widths[3]), (55.0, 40.0, 100.0))

    def test_the_switcher_shows_each_share_of_the_display(self):
        """A row wider than the display shows as such, not as shares of its own total."""
        niri, registry = self.niri(), [self.trio()]
        niri.widths.update({1: 60.0, 2: 30.0, 3: 30.0})
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            group_store.write(state / "groups.json", registry)
            (state / "groups.initialized").touch()
            with patch.object(sys, "argv", ["flow-spaces", "refresh"]), \
                 patch.object(spaces, "full_state", side_effect=lambda: (*niri.snapshot(), *niri.display_state())), \
                 patch.object(spaces, "session_id", return_value="s"), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                spaces.run_locked(state)
        space = next(s for s in json.loads(output.getvalue())["spaces"] if s.get("group_id"))
        self.assertEqual(space["shares"], [60, 30, 30])

    def test_only_side_by_side_groups_are_resized(self):
        niri = self.niri()
        niri.columns = [[9], [1, 2, 3]]
        with self.assertRaisesRegex(ValueError, "side by side"):
            self.run_op(niri, "member-wider", "1", [self.trio("stack")])
        self.assertFalse([a for a in niri.log if a[:2] == ("action", "set-window-width")])


if __name__ == "__main__":
    unittest.main()
