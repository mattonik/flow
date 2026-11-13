import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parents[1] / "session"))
spec = importlib.util.spec_from_file_location("restore", Path(__file__).parents[1] / "session/restore.py")
restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore)


def window(ident, app, column):
    return {"id": ident, "app_id": app, "workspace_id": 1, "title": app,
            "layout": {"pos_in_scrolling_layout": [column, 1]}}


class NiriActions:
    def __init__(self, workspace, windows):
        self.workspace = workspace
        self.windows = windows
        self.focused = workspace.get("active_window_id")

    def __call__(self, _kind, action, *args):
        if action == "focus-window":
            self.focused = args[1]
            self.workspace["active_window_id"] = self.focused
            return
        columns = {}
        for item in self.windows:
            column = item["layout"]["pos_in_scrolling_layout"][0]
            columns.setdefault(column, []).append(item)
        ordered = [columns[key] for key in sorted(columns)]
        if action == "move-column-to-index":
            item = next(w for w in self.windows if w["id"] == self.focused)
            source = next(i for i, members in enumerate(ordered) if item in members)
            moving = ordered.pop(source)
            ordered.insert(args[0] - 1, moving)
        elif action == "consume-window-into-column":
            item = next(w for w in self.windows if w["id"] == self.focused)
            target = next(i for i, members in enumerate(ordered) if item in members)
            if target + 1 < len(ordered):
                ordered[target] += ordered.pop(target + 1)
        elif action == "consume-or-expel-window-left":
            item = next(w for w in self.windows if w["id"] == args[1])
            target = next(i for i, members in enumerate(ordered) if item in members)
            if target > 0:
                ordered[target - 1] += ordered.pop(target)
        for column, members in enumerate(ordered, 1):
            for row, item in enumerate(members, 1):
                item["layout"]["pos_in_scrolling_layout"] = [column, row]


class RecoveryTest(unittest.TestCase):
    def test_snapshot_order_and_focus(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            restore.save(path, {"id": 1, "active_window_id": 2}, [window(2, "two", 2), window(1, "one", 1)], "session")
            saved = json.loads(path.read_text())
            self.assertEqual([e["app_id"] for e in saved["windows"]], ["one", "two"])
            self.assertTrue(saved["windows"][1]["focused"])
            self.assertEqual([e["column"] for e in saved["windows"]], [1, 2])
            self.assertEqual((saved["session"], [e["window_id"] for e in saved["windows"]]), ("session", [1, 2]))

    def test_saved_group_keeps_identity_and_label_across_snapshots(self):
        ws = {"id": 1, "active_window_id": 1}
        windows = [window(1, "one", 1), window(2, "two", 1)]
        windows[1]["layout"]["pos_in_scrolling_layout"] = [1, 2]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            restore.save(path, ws, windows, "first")
            saved = json.loads(path.read_text())
            group = saved["groups"][0]
            group["label"] = "Studio"
            path.write_text(json.dumps(saved))
            windows[0]["title"] = "Changed title"
            restore.save(path, ws, windows, "second")
            self.assertEqual(json.loads(path.read_text())["groups"], [group])
            windows[0]["layout"]["pos_in_scrolling_layout"] = [1, 2]
            windows[1]["layout"]["pos_in_scrolling_layout"] = [1, 1]
            restore.save(path, ws, windows, "third")
            self.assertEqual(json.loads(path.read_text())["groups"], [{**group, "members": ["two", "one"]}])

    def test_ambiguous_group_is_not_saved_as_a_persistent_identity(self):
        ws = {"id": 1}
        windows = [window(1, "one", 1), window(2, "two", 1), window(3, "one", 2)]
        windows[1]["layout"]["pos_in_scrolling_layout"] = [1, 2]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            restore.save(path, ws, windows)
            self.assertEqual(json.loads(path.read_text())["groups"], [])

    def test_missing_group_members_survive_later_saves(self):
        ws = {"id": 1, "active_window_id": 1}
        pair = [window(1, "one", 1), window(2, "two", 1)]
        pair[1]["layout"]["pos_in_scrolling_layout"] = [1, 2]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            restore.save(path, ws, pair, "first")
            group = json.loads(path.read_text())["groups"][0]
            self.assertEqual(restore.save(path, ws, [pair[0]], "second"), 1)
            saved = json.loads(path.read_text())
            self.assertEqual(saved["groups"], [group])
            self.assertEqual([(e["app_id"], e["column"], e["window_id"]) for e in saved["windows"]],
                             [("one", 1, 1), ("two", 1, None)])
            restore.save(path, ws, [], "third")
            saved = json.loads(path.read_text())
            self.assertEqual(saved["groups"], [group])
            self.assertEqual([e["app_id"] for e in saved["windows"]], ["one", "two"])

    def test_ambiguous_missing_group_preserves_snapshot(self):
        ws = {"id": 1}
        pair = [window(1, "one", 1), window(2, "two", 1)]
        pair[1]["layout"]["pos_in_scrolling_layout"] = [1, 2]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            restore.save(path, ws, pair)
            before = path.read_text()
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                restore.save(path, ws, [pair[0], window(3, "one", 2)])
            self.assertEqual(path.read_text(), before)
            other = window(2, "two", 1)
            other["workspace_id"] = 2
            with self.assertRaisesRegex(ValueError, "another workspace"):
                restore.save(path, ws, [pair[0], other])
            self.assertEqual(path.read_text(), before)

    def test_rename_saved_group_keeps_snapshot_and_rejects_bad_names(self):
        ws = {"id": 1}
        windows = [window(1, "one", 1), window(2, "two", 1)]
        windows[1]["layout"]["pos_in_scrolling_layout"] = [1, 2]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            restore.save(path, ws, windows)
            before = json.loads(path.read_text())
            group_id = before["groups"][0]["id"]
            restore.rename_group(path, group_id, "  Studio  ")
            after = json.loads(path.read_text())
            self.assertEqual(after["groups"][0]["label"], "Studio")
            self.assertEqual(after["windows"], before["windows"])
            with self.assertRaises(ValueError):
                restore.rename_group(path, group_id, " ")
            self.assertEqual(json.loads(path.read_text()), after)
            with self.assertRaises(ValueError):
                restore.rename_group(path, "missing", "New name")

    def test_existing_window_is_focused_without_relaunch(self):
        launch, action = Mock(), Mock()
        ws = {"id": 1, "active_window_id": 1}
        windows = [window(1, "one", 1)]
        report, count = restore.restore({"version": 1, "windows": [{"app_id": "one", "focused": True}]}, ws,
                                        windows, [], launch, lambda: (ws, windows), action)
        self.assertEqual((report, count), ([], 1))
        launch.assert_not_called()
        action.assert_any_call("action", "focus-window", "--id", 1)

    def test_duplicate_or_unknown_windows_are_not_launched(self):
        launch = Mock()
        saved = {"version": 1, "windows": [{"app_id": "one"}, {"app_id": "one"}, {"app_id": None}]}
        report, count = restore.restore(saved, {"id": 1}, [], [], launch, Mock(), Mock())
        self.assertEqual(count, 0)
        self.assertEqual(len(report), 3)
        launch.assert_not_called()

    def test_duplicate_windows_resolve_only_in_the_original_session(self):
        saved = {"version": 1, "session": "old", "windows": [
            {"app_id": "term", "window_id": 2, "focused": True},
            {"app_id": "term", "window_id": 1}]}
        windows = [window(1, "term", 1), window(2, "term", 2)]
        ws = {"id": 1, "active_window_id": 1}
        launch, action = Mock(), Mock()
        report, count = restore.restore(saved, ws, windows, [], launch, lambda: (ws, windows), action, session="old")
        self.assertEqual((report, count), ([], 2))
        action.assert_any_call("action", "focus-window", "--id", 2)
        action.assert_any_call("action", "focus-window", "--id", 1)
        action.assert_called_with("action", "focus-window", "--id", 2)
        report, count = restore.restore(saved, ws, windows, [], launch, lambda: (ws, windows), Mock(), session="new")
        self.assertEqual((len(report), count), (2, 0))
        launch.assert_not_called()

    def test_missing_duplicate_is_never_relaunched(self):
        saved = {"version": 1, "session": "old", "windows": [
            {"app_id": "term", "window_id": 1}, {"app_id": "term", "window_id": 2}]}
        launch = Mock()
        ws, windows = {"id": 1}, [window(1, "term", 1)]
        report, count = restore.restore(saved, ws, windows, [], launch, lambda: (ws, windows), Mock(), session="old")
        self.assertEqual((len(report), count), (1, 1))
        launch.assert_not_called()

    def test_reused_saved_id_does_not_match_two_entries_to_one_window(self):
        saved = {"version": 1, "session": "old", "windows": [
            {"app_id": "term", "window_id": 1}, {"app_id": "term", "window_id": 1}]}
        report, count = restore.restore(saved, {"id": 1}, [window(1, "term", 1)], [], Mock(), Mock(), Mock(), session="old")
        self.assertEqual((len(report), count), (2, 0))

    def test_restores_saved_group_for_adjacent_unique_windows(self):
        ws = {"id": 1, "active_window_id": 2}
        saved = {"version": 1, "windows": [{"app_id": "one", "column": 1},
                                           {"app_id": "two", "column": 1, "focused": True}]}
        windows = [window(1, "one", 1), window(2, "two", 2)]
        action = Mock(side_effect=NiriActions(ws, windows))
        report, count = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), action)
        self.assertEqual((report, count), ([], 2))
        action.assert_any_call("action", "consume-window-into-column")
        action.assert_called_with("action", "focus-window", "--id", 2)

    def test_restores_mixed_group_and_singleton_column_order_with_extra_window(self):
        ws = {"id": 1, "active_window_id": 4}
        saved = {"version": 1, "windows": [
            {"app_id": "one", "column": 1},
            {"app_id": "two", "column": 2},
            {"app_id": "three", "column": 2},
            {"app_id": "four", "column": 3, "focused": True},
        ]}
        windows = [window(1, "one", 3), window(2, "two", 2), window(3, "three", 4),
                   window(4, "four", 1), window(5, "extra", 5)]
        actions = Mock(side_effect=NiriActions(ws, windows))
        report, count = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions)
        columns = {}
        for item in windows:
            columns.setdefault(item["layout"]["pos_in_scrolling_layout"][0], []).append(item["app_id"])
        self.assertEqual((report, count), ([], 4))
        self.assertEqual([set(columns[i]) for i in sorted(columns)],
                         [{"one"}, {"two", "three"}, {"four"}, {"extra"}])
        actions.assert_called_with("action", "focus-window", "--id", 4)

    def test_failed_group_keeps_other_saved_columns_in_place(self):
        ws = {"id": 1, "active_window_id": 3}
        saved = {"version": 1, "windows": [
            {"app_id": "one", "column": 1},
            {"app_id": "two", "column": 2},
            {"app_id": "missing", "column": 2},
            {"app_id": "three", "column": 3, "focused": True},
        ]}
        windows = [window(1, "one", 3), window(2, "two", 1), window(3, "three", 2)]
        before = [w["layout"]["pos_in_scrolling_layout"][:] for w in windows]
        actions = Mock()
        report, _ = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions)
        self.assertTrue(any("Group members unavailable or ambiguous" in message for message in report))
        self.assertEqual([w["layout"]["pos_in_scrolling_layout"] for w in windows], before)
        self.assertNotIn("move-column-to-index", [call.args[1] for call in actions.call_args_list])

    def test_ambiguous_group_does_not_trigger_singleton_reordering(self):
        ws = {"id": 1, "active_window_id": 4}
        saved = {"version": 1, "windows": [
            {"app_id": "one", "column": 1}, {"app_id": "two", "column": 2},
            {"app_id": "three", "column": 2}, {"app_id": "four", "column": 3, "focused": True},
        ]}
        windows = [window(1, "one", 3), window(2, "two", 1), window(5, "two", 4),
                   window(3, "three", 2), window(4, "four", 5)]
        before = [w["layout"]["pos_in_scrolling_layout"][:] for w in windows]
        actions = Mock()
        report, _ = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions)
        self.assertTrue(any("ambiguous" in message for message in report))
        self.assertEqual([w["layout"]["pos_in_scrolling_layout"] for w in windows], before)
        self.assertNotIn("move-column-to-index", [call.args[1] for call in actions.call_args_list])

    def test_moves_group_member_next_to_partner_in_either_direction(self):
        saved = {"version": 1, "windows": [{"app_id": "one", "column": 1}, {"app_id": "two", "column": 1}]}
        ws = {"id": 1}
        for order in (("one", "other", "two"), ("two", "other", "one")):
            windows = [window(i, app, i) for i, app in enumerate(order, 1)]
            actions = Mock(side_effect=NiriActions(ws, windows))
            report, count = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions)
            self.assertEqual((report, count), ([], 2))
            actions.assert_any_call("action", "consume-window-into-column")

    def test_existing_group_is_preserved_and_pins_prevent_grouping(self):
        ws = {"id": 1, "active_window_id": 1}
        saved = {"version": 1, "windows": [{"app_id": "one", "column": 1}, {"app_id": "two", "column": 1}]}
        windows = [window(1, "one", 1), window(2, "two", 1)]
        windows[1]["layout"]["pos_in_scrolling_layout"] = [1, 2]
        action = Mock()
        report, count = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), action)
        self.assertEqual((report, count), ([], 2))
        self.assertNotIn("consume-window-into-column", str(action.call_args_list))
        for app in ("one", "two"):
            with self.subTest(pinned_member=app):
                pinned_action = Mock()
                report, _ = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), pinned_action, pinned={app})
                self.assertIn("pinned", report[0])
                self.assertNotIn("move-column-to-index", [call.args[1] for call in pinned_action.call_args_list])

    def test_group_pin_protects_both_members_during_session_restore(self):
        pins = [{"app_id": "one", "members": ["one", "two"]}]
        self.assertEqual(restore.pinned_apps(pins), {"one", "two"})
        self.assertEqual(restore.pinned_apps([{"app_id": "one"}]), {"one"})
        with self.assertRaisesRegex(ValueError, "Invalid pinned spaces"):
            restore.pinned_apps([{"app_id": "one", "members": "two"}])
        with self.assertRaisesRegex(ValueError, "Invalid pinned spaces"):
            restore.pinned_apps([{"app_id": "one", "members": ["one", []]}])

    def test_recovers_adjacent_members_of_the_matching_pinned_group_in_place(self):
        ws = {"id": 1, "active_window_id": 3}
        windows = [window(3, "other", 1), window(1, "one", 2), window(2, "two", 3)]
        pin = {"id": "pin-id", "app_id": "one", "members": ["one", "two"],
               "group_id": "group-id", "window_ids": [11, 12], "session": "old"}
        saved = {"version": 1, "groups": [{"id": "group-id", "label": "Studio", "members": ["one", "two"]}],
                 "windows": [{"app_id": "one", "column": 1}, {"app_id": "two", "column": 1}]}
        pins_before = json.loads(json.dumps([pin]))
        actions = Mock(side_effect=NiriActions(ws, windows))
        report, count = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions,
                                       pinned={"one", "two", "other"}, pinned_groups=[pin])
        self.assertEqual(count, 2)
        self.assertTrue(any("Pinned spaces keep their positions" in message for message in report))
        self.assertEqual(windows[1]["layout"]["pos_in_scrolling_layout"][0],
                         windows[2]["layout"]["pos_in_scrolling_layout"][0])
        self.assertEqual(windows[0]["layout"]["pos_in_scrolling_layout"], [1, 1])
        action_names = [call.args[1] for call in actions.call_args_list]
        self.assertIn("consume-or-expel-window-left", action_names)
        self.assertNotIn("focus-window", action_names[:-1])
        self.assertNotIn("move-column-to-index", action_names)
        self.assertEqual([pin], pins_before)

    def test_pinned_group_recovery_accepts_reversed_members_but_rejects_overlapping_pins(self):
        saved = {"version": 1, "groups": [{"id": "group-id", "members": ["two", "one"]}],
                 "windows": [{"app_id": "one", "column": 1}, {"app_id": "two", "column": 1}]}
        group_pin = {"app_id": "one", "members": ["two", "one"], "group_id": "group-id"}
        for pins, expected in (([group_pin], True), ([group_pin, {"app_id": "two"}], False)):
            with self.subTest(pins=pins):
                ws = {"id": 1}
                windows = [window(1, "one", 1), window(2, "two", 2)]
                actions = Mock(side_effect=NiriActions(ws, windows))
                restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions,
                                pinned={"one", "two"}, pinned_groups=pins)
                self.assertEqual(any(call.args[1] == "consume-or-expel-window-left"
                                     for call in actions.call_args_list), expected)

    def test_pinned_group_recovery_skips_nonadjacent_or_unmatched_identity(self):
        saved = {"version": 1, "groups": [{"id": "group-id", "members": ["one", "two"]}],
                 "windows": [{"app_id": "one", "column": 1}, {"app_id": "two", "column": 1}]}
        pin = {"app_id": "one", "members": ["one", "two"], "group_id": "group-id"}
        for windows, candidate in (
                ([window(1, "one", 1), window(3, "other", 2), window(2, "two", 3)], pin),
                ([window(1, "one", 1), window(2, "two", 2), window(3, "other", 3)], pin),
                ([window(1, "one", 1), window(2, "two", 2)], {**pin, "group_id": "other-id"})):
            with self.subTest(windows=windows, pin=candidate):
                ws = {"id": 1}
                before = [w["layout"]["pos_in_scrolling_layout"][:] for w in windows]
                actions = Mock()
                report, _ = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions,
                                            pinned={"one", "two"}, pinned_groups=[candidate])
                self.assertTrue(report)
                self.assertEqual([w["layout"]["pos_in_scrolling_layout"] for w in windows], before)
                self.assertNotIn("consume-or-expel-window-left", [call.args[1] for call in actions.call_args_list])
                self.assertNotIn("move-column-to-index", [call.args[1] for call in actions.call_args_list])

    def test_pinned_group_recovery_rejects_duplicate_saved_membership(self):
        saved = {"version": 1, "groups": [{"id": "first", "members": ["one", "two"]},
                                          {"id": "second", "members": ["two", "one"]}],
                 "windows": [{"app_id": "one", "column": 1}, {"app_id": "two", "column": 1}]}
        pin = {"app_id": "one", "members": ["one", "two"], "group_id": "first"}
        ws = {"id": 1}
        windows = [window(1, "one", 1), window(2, "two", 2)]
        actions = Mock()
        report, _ = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions,
                                    pinned={"one", "two"}, pinned_groups=[pin])
        self.assertTrue(report)
        self.assertNotIn("consume-or-expel-window-left", [call.args[1] for call in actions.call_args_list])

    def test_live_saved_or_unsaved_pin_blocks_the_new_ordering_pass(self):
        saved = {"version": 1, "windows": [
            {"app_id": "three", "column": 1}, {"app_id": "one", "column": 2},
            {"app_id": "two", "column": 2},
        ]}
        for pins, windows in (
            ({"one"}, [window(9, "pin", 1), window(3, "three", 3), window(1, "one", 2), window(2, "two", 2)]),
            ({"pin"}, [window(9, "pin", 1), window(3, "three", 3), window(1, "one", 2), window(2, "two", 2)]),
        ):
            with self.subTest(pins=pins):
                windows[-1]["layout"]["pos_in_scrolling_layout"] = [2, 2]
                ws = {"id": 1, "active_window_id": windows[0]["id"]}
                before = [w["layout"]["pos_in_scrolling_layout"][:] for w in windows]
                action = Mock()
                report, _ = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), action, pinned=pins)
                self.assertTrue(any("pinned" in message.lower() or "Pinned spaces keep" in message for message in report))
                self.assertEqual([w["layout"]["pos_in_scrolling_layout"] for w in windows], before)
                self.assertNotIn("move-column-to-index", [call.args[1] for call in action.call_args_list])

    def test_mixed_group_ordering_requires_valid_columns_for_every_entry(self):
        ws = {"id": 1, "active_window_id": 3}
        saved = {"version": 1, "windows": [
            {"app_id": "one", "column": 1}, {"app_id": "two", "column": 1},
            {"app_id": "three"},
        ]}
        windows = [window(1, "one", 2), window(2, "two", 2), window(3, "three", 1)]
        windows[1]["layout"]["pos_in_scrolling_layout"] = [2, 2]
        actions = Mock()
        report, _ = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions)
        self.assertIn("Saved columns are incomplete", " ".join(report))
        self.assertNotIn("move-column-to-index", [call.args[1] for call in actions.call_args_list])

    def test_launch_only_exact_desktop_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            apps = Path(directory) / "applications"
            apps.mkdir()
            (apps / "one.desktop").write_text("[Desktop Entry]\n")
            self.assertEqual(restore.spaces.desktop_file("one", [directory]), apps / "one.desktop")
            self.assertIsNone(restore.spaces.desktop_file("../one", [directory]))
            launch = Mock()
            ws = {"id": 1, "active_window_id": None}
            after = [window(7, "one", 1)]
            report, count = restore.restore({"version": 1, "windows": [{"app_id": "one"}]}, ws, [], [directory],
                                            launch, lambda: (ws, after), Mock(), lambda _: None)
            self.assertEqual((report, count), ([], 1))
            launch.assert_called_once_with(apps / "one.desktop")

    def test_workspace_change_during_launch_stops_without_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            apps = Path(directory) / "applications"
            apps.mkdir()
            (apps / "one.desktop").touch()
            action, launch = Mock(), Mock()
            with self.assertRaisesRegex(ValueError, "Workspace changed"):
                restore.restore({"version": 1, "windows": [{"app_id": "one"}]}, {"id": 1}, [], [directory],
                                launch, lambda: ({"id": 2}, [window(1, "one", 1)]), action, lambda _: None)
            launch.assert_called_once()
            action.assert_not_called()

    def test_workspace_change_during_group_recovery_does_not_steal_focus(self):
        saved = {"version": 1, "windows": [{"app_id": "one", "column": 1}, {"app_id": "two", "column": 1}]}
        ws, windows = {"id": 1, "active_window_id": 1}, [window(1, "one", 1), window(2, "two", 2)]
        action = Mock()
        with self.assertRaisesRegex(ValueError, "Workspace changed"):
            restore.restore(saved, ws, windows, [], Mock(), lambda: ({"id": 2}, windows), action)
        action.assert_not_called()

    def test_workspace_change_during_ordering_stops_before_more_moves_or_focus(self):
        ws = {"id": 1, "active_window_id": 4}
        windows = [window(1, "one", 2), window(2, "two", 1), window(3, "three", 1), window(4, "four", 3)]
        windows[2]["layout"]["pos_in_scrolling_layout"] = [1, 2]
        saved = {"version": 1, "windows": [{"app_id": "one", "column": 1},
                                           {"app_id": "two", "column": 2},
                                           {"app_id": "three", "column": 2},
                                           {"app_id": "four", "column": 3, "focused": True}]}
        snapshots = 0
        def get_snapshot():
            nonlocal snapshots
            snapshots += 1
            return (ws if snapshots < 4 else {"id": 2}, windows)
        action = Mock()
        with self.assertRaisesRegex(ValueError, "Workspace changed"):
            restore.restore(saved, ws, windows, [], Mock(), get_snapshot, action)
        self.assertEqual(action.call_args_list, [
            unittest.mock.call("action", "focus-window", "--id", 1),
        ])


if __name__ == "__main__":
    unittest.main()
