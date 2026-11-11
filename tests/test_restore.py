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
            restore.save(path, {"id": 1, "active_window_id": 2}, [window(2, "two", 2), window(1, "one", 1)])
            saved = json.loads(path.read_text())
            self.assertEqual([e["app_id"] for e in saved["windows"]], ["one", "two"])
            self.assertTrue(saved["windows"][1]["focused"])

    def test_existing_window_is_focused_without_relaunch(self):
        launch, action = Mock(), Mock()
        ws = {"id": 1, "active_window_id": 1}
        report, count = restore.restore({"version": 1, "windows": [{"app_id": "one", "focused": True}]}, ws,
                                        [window(1, "one", 1)], [], launch, Mock(), action)
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

    def test_launch_only_exact_desktop_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            apps = Path(directory) / "applications"
            apps.mkdir()
            (apps / "one.desktop").write_text("[Desktop Entry]\n")
            self.assertEqual(restore.desktop_file("one", [directory]), apps / "one.desktop")
            self.assertIsNone(restore.desktop_file("../one", [directory]))
            launch = Mock()
            ws = {"id": 1, "active_window_id": None}
            after = [window(7, "one", 1)]
            report, count = restore.restore({"version": 1, "windows": [{"app_id": "one"}]}, ws, [], [directory],
                                            launch, lambda: (ws, after), Mock(), lambda _: None)
            self.assertEqual((report, count), ([], 1))
            launch.assert_called_once_with(apps / "one.desktop")


if __name__ == "__main__":
    unittest.main()
