import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("spaces", Path(__file__).parents[1] / "session/spaces.py")
spaces = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spaces)


def window(ident, app, column):
    return {"id": ident, "app_id": app, "title": app, "workspace_id": 1,
            "is_floating": False, "layout": {"pos_in_scrolling_layout": [column, 1]}}


class SpacesTest(unittest.TestCase):
    def setUp(self):
        self.ws = {"id": 1, "active_window_id": 2}
        self.windows = [window(1, "terminal", 1), window(2, "files", 2)]
        self.pins = []

    @patch.object(spaces, "command")
    def test_pin_reorders_and_unpin_preserves_window(self, command):
        result = spaces.run(self.pins, "pin", None, self.ws, self.windows, "session")
        self.assertEqual(result[0]["label"], "files")
        self.assertEqual(command.call_args_list[0].args, ("action", "focus-window", "--id", 2))
        self.assertEqual(command.call_args_list[1].args, ("action", "move-column-to-index", 1))
        spaces.run(self.pins, "unpin", self.pins[0]["id"], self.ws, self.windows, "session")
        self.assertEqual(self.pins, [])
        self.assertNotIn("close-window", str(command.call_args_list))

    @patch.object(spaces, "command")
    def test_switcher_only_focuses_current_workspace_windows(self, command):
        spaces.run([], "focus-window", "2", self.ws, self.windows, "session")
        command.assert_called_once_with("action", "focus-window", "--id", 2)
        with self.assertRaises(ValueError):
            spaces.run([], "focus-window", "999", self.ws, self.windows, "session")

    def test_stacked_windows_are_one_space_with_individual_focus_targets(self):
        self.windows[1]["layout"]["pos_in_scrolling_layout"] = [1, 2]
        result = spaces.display_spaces(self.ws, self.windows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], 2)
        self.assertEqual([member["id"] for member in result[0]["members"]], [1, 2])
        self.windows[1]["is_floating"] = True
        self.assertEqual(len(spaces.display_spaces(self.ws, self.windows)), 2)

    @patch.object(spaces, "command")
    def test_stack_and_separate_two_unpinned_windows(self, command):
        self.ws["active_window_id"] = 1
        spaces.run([], "stack", None, self.ws, self.windows, "session")
        command.assert_called_once_with("action", "consume-window-into-column")
        command.reset_mock()
        self.windows[1]["layout"]["pos_in_scrolling_layout"] = [1, 2]
        spaces.run([], "unstack", None, self.ws, self.windows, "session")
        command.assert_called_once_with("action", "expel-window-from-column")

    @patch.object(spaces, "command")
    def test_stack_rejects_pinned_or_missing_neighbor(self, command):
        self.ws["active_window_id"] = 1
        with self.assertRaises(ValueError):
            spaces.run([{"app_id": "files"}], "stack", None, self.ws, self.windows, "session")
        self.ws["active_window_id"] = 2
        with self.assertRaises(ValueError):
            spaces.run([], "stack", None, self.ws, self.windows, "session")
        command.assert_not_called()

    def test_restart_never_guesses_between_duplicate_windows(self):
        pin = {"app_id": "terminal", "window_id": 1, "session": "old"}
        self.assertEqual(spaces.resolve(pin, self.windows, "new")["id"], 1)
        self.windows.append(window(3, "terminal", 3))
        self.assertIsNone(spaces.resolve(pin, self.windows, "new"))
        self.assertEqual(spaces.resolve(pin, self.windows, "old")["id"], 1)

    def test_reopened_pin_resolves_only_an_unambiguous_window(self):
        pin = {"app_id": "terminal", "window_id": 9, "session": "session"}
        self.assertEqual(spaces.resolve(pin, self.windows, "session")["id"], 1)
        self.windows.append(window(3, "terminal", 3))
        self.assertIsNone(spaces.resolve(pin, self.windows, "session"))

    def test_refuses_stacked_or_floating_windows(self):
        self.windows.append(window(3, "notes", 2))
        self.assertFalse(spaces.eligible(self.windows[1], self.ws, self.windows))
        self.windows.pop()
        self.windows[1]["is_floating"] = True
        with self.assertRaises(ValueError):
            spaces.run(self.pins, "pin", None, self.ws, self.windows, "session")
        self.assertFalse(self.pins)

    @patch.object(spaces, "command")
    def test_no_movement_when_order_is_correct(self, command):
        pins = [{"app_id": "terminal", "window_id": 1, "session": "session"}]
        spaces.reconcile(pins, self.ws, self.windows, "session")
        command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
