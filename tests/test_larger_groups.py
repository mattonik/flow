from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "session"))
sys.path.insert(0, str(Path(__file__).parent))
import groups
import restore
import spaces
from fake_niri import FakeNiri

APPS = {1: "a", 2: "b", 3: "c", 4: "d", 9: "other"}


def three(layout="stack", **extra):
    return {"id": "g", "label": "Trio", "members": ["a", "b", "c"], "layout": layout, **extra}


class LargerGroupTest(unittest.TestCase):
    def niri(self, columns, active=1):
        niri = FakeNiri(columns, APPS, active)
        patches = (patch.object(spaces, "snapshot", side_effect=niri.snapshot),
                   patch.object(spaces, "command", side_effect=niri))
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return niri

    def run_op(self, niri, op, arg, registry, pins=()):
        ws, windows = niri.snapshot()
        pins = list(pins)
        return spaces.run(pins, op, arg, ws, windows, "s", registry), pins

    def test_registry_accepts_two_to_four_distinct_members_with_matching_ratios(self):
        good = [{"id": "g", "label": "G", "members": ["a", "b", "c", "d"], "side_ratios": [0.4, 0.2, 0.2, 0.2]}]
        self.assertEqual(groups.validate({"version": 1, "groups": good}), good)
        for bad in ({"members": ["a"]}, {"members": ["a", "b", "c", "d", "e"]}, {"members": ["a", "b", "a"]},
                    {"members": ["a", "b", "c"], "side_ratio": 0.5},
                    {"members": ["a", "b", "c"], "side_ratios": [0.5, 0.5]},
                    {"members": ["a", "b", "c"], "side_ratios": [0.5, 0.4, 0.4]}):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "Invalid saved Groups"):
                groups.validate({"version": 1, "groups": [{"id": "g", "label": "G", **bad}]})

    def test_three_app_stack_and_side_by_side_are_one_destination(self):
        niri = self.niri([[1, 2, 3], [9]])
        ws, windows = niri.snapshot()
        spaces_ = spaces.display_spaces(ws, windows, [three()])
        self.assertEqual([s["group_id"] for s in spaces_], ["g", None])
        self.assertEqual([m["app_id"] for m in spaces_[0]["members"]], ["a", "b", "c"])
        niri.columns = [[9], [1], [2], [3]]
        ws, windows = niri.snapshot()
        spaces_ = spaces.display_spaces(ws, windows, [three("side-by-side")])
        self.assertEqual([s["group_id"] for s in spaces_], [None, "g"])
        self.assertEqual(len(spaces_[1]["members"]), 3)
        # The wrong order is not silently accepted as the Group.
        niri.columns = [[9], [1], [3], [2]]
        ws, windows = niri.snapshot()
        self.assertFalse(any(s["group_id"] for s in spaces.display_spaces(ws, windows, [three("side-by-side")])))
        entry = spaces.incomplete_groups(ws, windows, [three("side-by-side")], [])[0]
        self.assertTrue(entry["can_adopt_order"])

    def test_layout_change_round_trip_keeps_order_and_saved_proportions(self):
        niri = self.niri([[1, 2, 3], [9]])
        group = three(side_ratios=[0.5, 0.3, 0.2])
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            groups.write(state / "groups.json", [group])
            ws, windows = niri.snapshot()
            spaces.change_group_layout(state, [group], [], "g", ws, windows, "s")
            self.assertEqual(niri.layout(), [["a"], ["b"], ["c"], ["other"]])
            self.assertEqual([niri.widths[i] for i in (1, 2, 3)], [50.0, 30.0, 20.0])
            self.assertEqual(group["layout"], "side-by-side")
            niri.widths.update({1: 40.0, 2: 40.0, 3: 20.0})
            ws, windows = niri.snapshot()
            spaces.change_group_layout(state, [group], [], "g", ws, windows, "s")
            self.assertEqual(niri.layout(), [["a", "b", "c"], ["other"]])
            self.assertEqual(groups.load(state)[0]["side_ratios"], [0.4, 0.4, 0.2])
            self.assertNotIn("pending_layout", groups.load(state)[0])

    def test_interrupted_layout_change_resumes_from_a_partial_arrangement(self):
        niri = self.niri([[1, 2, 3], [9]])
        group = three()
        niri.fail_on = lambda args: args[1] == "consume-or-expel-window-right" and args[-1] == 2
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            groups.write(state / "groups.json", [group])
            ws, windows = niri.snapshot()
            with self.assertRaisesRegex(ValueError, "interrupted"):
                spaces.change_group_layout(state, [group], [], "g", ws, windows, "s")
            self.assertEqual(niri.layout(), [["a", "b"], ["c"], ["other"]])
            self.assertEqual(groups.load(state)[0]["pending_layout"], "side-by-side")
            entry = spaces.incomplete_groups(ws, niri.snapshot()[1], [group], [])[0]
            self.assertTrue(entry["can_resume_layout"])
            niri.fail_on = None
            ws, windows = niri.snapshot()
            spaces.change_group_layout(state, [group], [], "g", ws, windows, "s", resume=True)
            self.assertEqual(niri.layout(), [["a"], ["b"], ["c"], ["other"]])
            self.assertEqual(groups.load(state)[0]["layout"], "side-by-side")

    def test_add_and_remove_members_in_a_stack(self):
        group = {"id": "g", "label": "Pair", "members": ["a", "b"], "side_ratio": 0.6}
        pin = {"id": "p", "label": "Pair", "app_id": "a", "members": ["a", "b"], "group_id": "g",
               "window_ids": [1, 2], "window_id": 1, "session": "s"}
        niri = self.niri([[1, 2], [3], [9]])
        ws, windows = niri.snapshot()
        self.assertTrue(spaces.display_spaces(ws, windows, [group], [pin])[0]["can_add"])
        _, pins = self.run_op(niri, "add-to-group", "g", [group], [pin])
        self.assertEqual(niri.layout(), [["a", "b", "c"], ["other"]])
        self.assertEqual(group["members"], ["a", "b", "c"])
        self.assertNotIn("side_ratio", group)
        self.assertEqual(sum(group["side_ratios"]), 1)
        self.assertEqual((pins[0]["members"], pins[0]["window_ids"]), (["a", "b", "c"], [1, 2, 3]))
        _, pins = self.run_op(niri, "remove-from-group", "2", [group], pins)
        self.assertEqual(niri.layout(), [["a", "c"], ["b"], ["other"]])
        self.assertEqual(group["members"], ["a", "c"])
        self.assertEqual(pins[0]["window_ids"], [1, 3])
        with self.assertRaisesRegex(ValueError, "at least two"):
            self.run_op(niri, "remove-from-group", "3", [group], pins)

    def test_add_refuses_pinned_grouped_duplicate_or_full_neighbors(self):
        group = {"id": "g", "label": "Pair", "members": ["a", "b"]}
        niri = self.niri([[1, 2], [3]])
        with self.assertRaisesRegex(ValueError, "another Group or pin"):
            self.run_op(niri, "add-to-group", "g", [group], [{"id": "q", "label": "C", "app_id": "c"}])
        niri.columns = [[1, 2], [3], [4]]
        niri.apps = {**APPS, 4: "c"}
        with self.assertRaisesRegex(ValueError, "several windows"):
            self.run_op(niri, "add-to-group", "g", [group])
        full = {"id": "g", "label": "Full", "members": ["a", "b", "c", "d"]}
        niri.apps, niri.columns = APPS, [[1, 2, 3, 4], [9]]
        with self.assertRaisesRegex(ValueError, "up to 4"):
            self.run_op(niri, "add-to-group", "g", [full])
        self.assertFalse(any(args[1].startswith("consume") for args in niri.log if len(args) > 1))

    def test_side_by_side_member_changes_keep_the_group_contiguous(self):
        group = three("side-by-side")
        niri = self.niri([[1], [2], [3], [4], [9]], active=2)
        self.run_op(niri, "add-to-group", "g", [group])
        self.assertEqual(group["members"], ["a", "b", "c", "d"])
        self.run_op(niri, "member-left", "3", [group])
        self.assertEqual(niri.layout(), [["a"], ["c"], ["b"], ["d"], ["other"]])
        self.assertEqual(group["members"], ["a", "c", "b", "d"])
        self.run_op(niri, "remove-from-group", "3", [group])
        self.assertEqual(niri.layout(), [["a"], ["b"], ["d"], ["c"], ["other"]])
        self.assertEqual(group["members"], ["a", "b", "d"])
        ws, windows = niri.snapshot()
        self.assertEqual(spaces.display_spaces(ws, windows, [group])[0]["group_id"], "g")

    def test_stack_reorder_and_adopting_a_manual_order(self):
        group = three()
        niri = self.niri([[1, 2, 3]])
        self.run_op(niri, "member-right", "1", [group])
        self.assertEqual((niri.layout(), group["members"]), ([["b", "a", "c"]], ["b", "a", "c"]))
        niri.columns = [[3, 1, 2]]  # Moved with Niri's own keys.
        ws, windows = niri.snapshot()
        space = spaces.display_spaces(ws, windows, [group])[0]
        self.assertTrue(space["order_changed"])
        with self.assertRaisesRegex(ValueError, "order"):
            self.run_op(niri, "member-left", "1", [group])
        self.run_op(niri, "adopt-group-order", "g", [group])
        self.assertEqual(group["members"], ["c", "a", "b"])

    def test_interrupted_member_move_leaves_registry_and_reports_order_change(self):
        group = three("side-by-side")
        niri = self.niri([[1], [2], [3]])
        original = niri.__call__

        def drop_move(*args):
            if args[1] == "move-column-right":
                niri.columns = [[2], [1], [3]]
                niri.active = 1
                niri.log.append(args)
                return ""
            return original(*args)

        with patch.object(spaces, "command", side_effect=drop_move), \
             self.assertRaisesRegex(ValueError, "interrupted"):
            self.run_op(niri, "member-right", "2", [group])
        self.assertEqual(group["members"], ["a", "b", "c"])
        ws, windows = niri.snapshot()
        self.assertTrue(spaces.incomplete_groups(ws, windows, [group], [])[0]["can_adopt_order"])

    def test_dissolve_and_rejoin_three_apps_keep_saved_order(self):
        group = three()
        niri = self.niri([[1, 2, 3], [9]])
        registry = [group]
        self.run_op(niri, "dissolve-group", "g", registry)
        self.assertEqual((niri.layout(), registry), ([["a"], ["b"], ["c"], ["other"]], []))
        registry = [three()]
        niri.columns = [[3], [2], [1], [9]]
        ws, windows = niri.snapshot()
        self.assertTrue(spaces.incomplete_groups(ws, windows, registry, [])[0]["can_rejoin"])
        self.run_op(niri, "rejoin-group", "g", registry)
        self.assertEqual(niri.layout()[0], ["a", "b", "c"])
        niri.columns = [[1], [3], [2], [9]]
        ws, windows = niri.snapshot()
        entry = spaces.incomplete_groups(ws, windows, [three()], [])[0]
        self.assertFalse(entry["can_rejoin"])
        self.assertIn("saved order", entry["status"])

    def test_rejoin_stacks_a_pinned_three_app_group_with_columns_after_it(self):
        # Each step of the rejoin is checked with the columns after the Group closing up behind it.
        registry = [three()]
        pins = [{"id": "pin", "group_id": "g", "members": ["a", "b", "c"], "app_id": "a"}]
        niri = self.niri([[1], [2], [3], [9], [4]])
        ws, windows = niri.snapshot()
        self.assertTrue(spaces.incomplete_groups(ws, windows, registry, pins)[0]["can_rejoin"])
        _, pins = self.run_op(niri, "rejoin-group", "g", registry, pins)
        self.assertEqual(niri.layout(), [["a", "b", "c"], ["other"], ["d"]])
        self.assertEqual(pins[0]["window_ids"], [1, 2, 3])

    def test_pin_ordered_is_what_reconcile_leaves(self):
        pin = {"id": "pin", "group_id": "g", "members": ["a", "b"], "app_id": "a"}
        cases = {
            "a pinned app moved ahead": ({"id": "g", "label": "Pair", "members": ["a", "b"]}, [[9], [1, 2]]),
            # Bound in this session to windows that have closed: a refresh does not see the Group, nor may the model.
            "a Group bound to closed windows": ({"id": "g", "label": "Pair", "members": ["a", "b"],
                                                 "bound": {"session": "s", "windows": {"a": 101, "b": 102}}}, [[9], [1, 2]]),
            "a side-by-side Group pauses it": ({"id": "g", "label": "Pair", "members": ["a", "b"], "layout": "side-by-side"},
                                               [[9], [1], [2]]),
        }
        for label, (group, columns) in cases.items():
            with self.subTest(label):
                pins = [dict(pin), {"id": "other", "app_id": "other"}]
                niri = self.niri(columns, active=9)
                ws, windows = niri.snapshot()
                modelled = {w["id"]: w["layout"]["pos_in_scrolling_layout"]
                            for w in spaces.pin_ordered(ws, windows, pins, "s", [group])}
                self.assertIsNone(spaces.SESSION)  # the model leaves no binding behind
                spaces.SESSION = "s"  # as a refresh runs reconcile
                try:
                    spaces.reconcile(pins, ws, windows, "s", [group])
                finally:
                    spaces.SESSION = None
                _, after = niri.snapshot()
                self.assertEqual(modelled, {w["id"]: w["layout"]["pos_in_scrolling_layout"] for w in after})
                niri.columns = []  # the next case starts its own Niri

    def test_rejoin_checks_each_step_of_the_join(self):
        registry = [three()]
        niri = self.niri([[1], [2], [3]])
        skipped = []

        def misroute(kind, action, *args):
            if args[-1:] == (2,):
                skipped.append(args)  # Niri put b elsewhere
                return None
            return niri(kind, action, *args)

        with patch.object(spaces, "command", side_effect=misroute) as command:
            with self.assertRaisesRegex(ValueError, "Could not confirm Group rejoin"):
                self.run_op(niri, "rejoin-group", "g", registry)
        self.assertTrue(skipped)
        self.assertNotIn(("action", "consume-or-expel-window-left", "--id", 3), [c.args for c in command.call_args_list])

    def test_rejoin_stops_when_the_workspace_changes_during_the_join(self):
        registry = [three()]
        niri = self.niri([[1], [2], [3]])

        def switch(kind, action, *args):
            niri(kind, action, *args)
            niri.workspace = 2  # the user switched workspaces right after the first step

        with patch.object(spaces, "command", side_effect=switch) as command:
            with self.assertRaisesRegex(ValueError, "Could not confirm Group rejoin"):
                self.run_op(niri, "rejoin-group", "g", registry)
        self.assertNotIn(("action", "consume-or-expel-window-left", "--id", 3), [c.args for c in command.call_args_list])

    def test_three_app_side_repair_moves_each_member_into_place(self):
        group = three("side-by-side")
        niri = self.niri([[1], [9], [2], [3]], active=9)
        ws, windows = niri.snapshot()
        spaces.repair_side_group("g", ws, windows, [group], [])
        self.assertEqual(niri.layout(), [["a"], ["b"], ["c"], ["other"]])
        self.assertEqual(niri.active, 9)

    def test_pinned_three_app_group_survives_save_and_recovery(self):
        group = three()
        niri = FakeNiri([[9], [1, 2, 3]], APPS, 1)
        ws, windows = niri.snapshot()
        pin = {"id": "p", "label": "Trio", "app_id": "a", "members": ["a", "b", "c"], "group_id": "g",
               "window_ids": [1, 2, 3], "window_id": 1, "session": "s"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            restore.save(path, ws, windows, "s", [pin], [group], "edid:A|B|1")
            import json
            data = json.loads(path.read_text())
            self.assertEqual(data["display"], "edid:A|B|1")
            self.assertEqual(data["groups"][0]["members"], ["a", "b", "c"])
        # After a restart the apps reopen as separate columns; recovery stacks them in saved order.
        niri.columns = [[9], [3], [2], [1]]
        ws, windows = niri.snapshot()
        report, count = restore.restore(data, ws, windows, (), lambda d: None, niri.snapshot, niri,
                                        wait=lambda s: None, pinned={"a", "b", "c"}, session="new",
                                        pinned_groups=[pin], registered=[group])
        self.assertEqual(niri.layout(), [["other"], ["a", "b", "c"]])
        self.assertEqual(count, 4)
        self.assertFalse([r for r in report if "Group" in r], report)

    def test_unpinned_three_app_side_group_recovers_proportions(self):
        group = three("side-by-side", side_ratios=[0.5, 0.25, 0.25])
        data = {"version": 1, "session": "old", "groups": [group],
                "windows": [{"app_id": a, "column": c, "focused": c == 1} for a, c in (("a", 1), ("b", 2), ("c", 3))]}
        niri = FakeNiri([[1], [2], [3]], APPS, 1)
        ws, windows = niri.snapshot()
        report, _ = restore.restore(data, ws, windows, (), lambda d: None, niri.snapshot, niri,
                                    wait=lambda s: None, session="new", registered=[group])
        self.assertEqual([niri.widths[i] for i in (1, 2, 3)], [50.0, 25.0, 25.0])
        self.assertEqual(report, [])


if __name__ == "__main__":
    unittest.main()
