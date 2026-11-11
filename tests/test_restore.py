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
        action = Mock()
        report, count = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), action)
        self.assertEqual((report, count), ([], 2))
        action.assert_any_call("action", "consume-window-into-column")
        action.assert_called_with("action", "focus-window", "--id", 2)

    def test_moves_group_member_next_to_partner_in_either_direction(self):
        saved = {"version": 1, "windows": [{"app_id": "one", "column": 1}, {"app_id": "two", "column": 1}]}
        ws = {"id": 1}
        for order in (("one", "other", "two"), ("two", "other", "one")):
            windows = [window(i, app, i) for i, app in enumerate(order, 1)]
            focused = None
            def action(*args):
                nonlocal focused
                if args[1] == "focus-window":
                    focused = args[3]
                elif args[1] == "move-column-to-index":
                    ordered = sorted(windows, key=lambda w: w["layout"]["pos_in_scrolling_layout"][0])
                    member = next(w for w in ordered if w["id"] == focused)
                    ordered.remove(member)
                    ordered.insert(args[2] - 1, member)
                    for index, w in enumerate(ordered, 1):
                        w["layout"]["pos_in_scrolling_layout"][0] = index
            actions = Mock(side_effect=action)
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
        report, _ = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), Mock(), pinned={"one"})
        self.assertIn("pinned", report[0])

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


if __name__ == "__main__":
    unittest.main()
