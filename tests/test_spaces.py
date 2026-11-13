import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
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

    def test_saved_group_label_requires_unambiguous_matching_members(self):
        self.windows[1]["layout"]["pos_in_scrolling_layout"] = [1, 2]
        groups = [{"id": "group-1", "label": "Work", "members": ["terminal", "files"]}]
        shown = spaces.display_spaces(self.ws, self.windows, groups)[0]
        self.assertEqual((shown["group_id"], shown["label"]), ("group-1", "Work"))
        self.windows[0]["layout"]["pos_in_scrolling_layout"] = [1, 2]
        self.windows[1]["layout"]["pos_in_scrolling_layout"] = [1, 1]
        shown = spaces.display_spaces(self.ws, self.windows, groups)[0]
        self.assertEqual((shown["group_id"], shown["label"]), ("group-1", "Work"))
        self.windows.append(window(3, "terminal", 2))
        shown = spaces.display_spaces(self.ws, self.windows, groups)[0]
        self.assertIsNone(shown["group_id"])
        self.assertNotEqual(shown["label"], "Work")

    def test_duplicate_saved_groups_make_membership_ambiguous(self):
        self.windows[1]["layout"]["pos_in_scrolling_layout"] = [1, 2]
        groups = [
            {"id": "group-1", "label": "Work", "members": ["terminal", "files"]},
            {"id": "group-2", "label": "Other", "members": ["files", "terminal"]},
        ]
        shown = spaces.display_spaces(self.ws, self.windows, groups)[0]
        self.assertIsNone(shown["group_id"])
        self.assertNotIn(shown["label"], {"Work", "Other"})

    @patch.object(spaces, "command")
    def test_pin_group_records_members_and_orders_its_stack_as_one_destination(self, command):
        self.windows[0]["layout"]["pos_in_scrolling_layout"] = [2, 1]
        self.windows[1]["layout"]["pos_in_scrolling_layout"] = [2, 2]
        groups = [{"id": "group-1", "label": "Work", "members": ["terminal", "files"]}]
        result = spaces.run(self.pins, "pin", None, self.ws, self.windows, "session", groups)
        pin = self.pins[0]
        self.assertEqual((pin["group_id"], pin["members"], pin["window_ids"]),
                         ("group-1", ["terminal", "files"], [1, 2]))
        self.assertEqual(result[0]["kind"], "group")
        self.assertEqual(result[0]["group_id"], "group-1")
        self.assertTrue(result[0]["available"])
        command.assert_any_call("action", "move-column-to-index", 1)
        self.assertEqual([w["id"] for w in spaces.group_windows(pin, self.ws, self.windows, "session", groups)], [1, 2])

    @patch.object(spaces, "command")
    def test_pin_active_group_uses_saved_identity_with_reversed_member_order(self, command):
        self.windows[0]["layout"]["pos_in_scrolling_layout"] = [2, 2]
        self.windows[1]["layout"]["pos_in_scrolling_layout"] = [2, 1]
        self.ws["active_window_id"] = 1
        groups = [{"id": "group-1", "label": "Work", "members": ["files", "terminal"]}]
        spaces.run(self.pins, "pin", None, self.ws, self.windows, "session", groups)
        self.assertEqual(self.pins[0]["members"], ["files", "terminal"])
        self.assertEqual(self.pins[0]["window_ids"], [2, 1])
        command.assert_any_call("action", "move-column-to-index", 1)

    def test_group_pin_rejects_missing_ambiguous_or_modified_groups(self):
        self.windows[0]["layout"]["pos_in_scrolling_layout"] = [1, 1]
        self.windows[1]["layout"]["pos_in_scrolling_layout"] = [1, 2]
        groups = [{"id": "group-1", "label": "Work", "members": ["terminal", "files"]}]
        for current_groups in ([], [{**groups[0], "members": ["terminal", "other"]}]):
            with self.subTest(groups=current_groups), patch.object(spaces, "command") as command:
                with self.assertRaises(ValueError):
                    spaces.run([], "pin-group", "group-1", self.ws, self.windows, "session", current_groups)
                command.assert_not_called()
        duplicate = self.windows + [window(3, "terminal", 2)]
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            spaces.run([], "pin-group", "group-1", self.ws, duplicate, "session", groups)

    def test_pinned_group_becomes_unavailable_if_the_stack_changes(self):
        pin = {"id": "group-pin", "group_id": "group-1", "label": "Work", "app_id": "terminal",
               "members": ["terminal", "files"], "window_id": 1, "window_ids": [1, 2], "session": "session"}
        groups = [{"id": "group-1", "label": "Work", "members": ["files", "terminal"]}]
        self.windows[1]["layout"]["pos_in_scrolling_layout"] = [3, 1]
        self.assertIsNone(spaces.pin_window(pin, self.ws, self.windows, "session", groups))
        self.windows[1]["layout"]["pos_in_scrolling_layout"] = [1, 2]
        self.assertIsNotNone(spaces.pin_window(pin, self.ws, self.windows, "new-session", groups))
        self.windows[0]["workspace_id"] = 2
        self.assertIsNone(spaces.pin_window(pin, self.ws, self.windows, "new-session", groups))
        self.assertIsNone(spaces.pin_window(pin, self.ws, self.windows, "session", [{"id": "group-1", "label": "Work",
                                                                                      "members": ["terminal", "other"]}]))

    def test_reopened_group_member_rebinds_only_when_unambiguous(self):
        pin = {"id": "group-pin", "group_id": "group-1", "label": "Work", "app_id": "terminal",
               "members": ["terminal", "files"], "window_id": 1, "window_ids": [1, 2], "session": "session"}
        groups = [{"id": "group-1", "label": "Work", "members": ["terminal", "files"]}]
        windows = [window(1, "terminal", 1), window(9, "files", 1)]
        windows[1]["layout"]["pos_in_scrolling_layout"] = [1, 2]
        self.assertEqual([w["id"] for w in spaces.group_windows(pin, self.ws, windows, "session", groups)], [1, 9])
        windows.append(window(10, "files", 2))
        self.assertIsNone(spaces.group_windows(pin, self.ws, windows, "session", groups))

    @patch.object(spaces, "command")
    def test_group_pin_returns_to_last_focused_member_across_sessions(self, command):
        self.windows[1]["layout"]["pos_in_scrolling_layout"] = [1, 2]
        groups = [{"id": "group-1", "label": "Work", "members": ["terminal", "files"]}]
        spaces.run(self.pins, "pin", None, self.ws, self.windows, "session", groups)
        self.assertEqual(self.pins[0]["last_focused_app"], "files")
        self.ws["active_window_id"] = 1
        spaces.run(self.pins, "refresh", None, self.ws, self.windows, "session", groups)
        self.assertEqual(self.pins[0]["last_focused_app"], "terminal")
        windows = [window(10, "terminal", 1), window(20, "files", 1)]
        windows[1]["layout"]["pos_in_scrolling_layout"] = [1, 2]
        self.ws["active_window_id"] = None
        command.reset_mock()
        spaces.run(self.pins, "focus", self.pins[0]["id"], self.ws, windows, "new-session", groups)
        command.assert_any_call("action", "focus-window", "--id", 10)

    @patch.object(spaces, "command")
    def test_unpin_removes_group_metadata_without_touching_windows(self, command):
        pin = {"id": "group-pin", "group_id": "group-1", "label": "Work", "app_id": "terminal",
               "members": ["terminal", "files"], "window_id": 1, "window_ids": [1, 2], "session": "session"}
        pins = [pin]
        spaces.run(pins, "unpin", "group-pin", self.ws, self.windows, "session")
        self.assertEqual(pins, [])
        command.assert_not_called()

    @patch.object(spaces, "command")
    @patch.object(spaces.subprocess, "run")
    def test_focus_unavailable_group_never_launches_or_moves_members(self, launch, command):
        pin = {"id": "group-pin", "group_id": "group-1", "label": "Work", "app_id": "terminal",
               "members": ["terminal", "files"], "window_id": 1, "window_ids": [1, 2], "session": "session"}
        groups = [{"id": "group-1", "label": "Work", "members": ["terminal", "files"]}]
        with self.assertRaisesRegex(ValueError, "no longer one stack"):
            spaces.run([pin], "focus", "group-pin", self.ws, self.windows, "session", groups)
        command.assert_not_called()
        launch.assert_not_called()

    @patch.object(spaces, "command")
    def test_group_pin_protects_both_members_from_unstacking(self, command):
        self.windows[1]["layout"]["pos_in_scrolling_layout"] = [1, 2]
        pins = [{"id": "group-pin", "app_id": "terminal", "members": ["terminal", "files"]}]
        self.ws["active_window_id"] = 1
        with self.assertRaisesRegex(ValueError, "Unpin these windows"):
            spaces.run(pins, "unstack", None, self.ws, self.windows, "session")
        command.assert_not_called()

    @patch.object(spaces, "command")
    def test_group_view_toggles_display_without_moving_or_reidentifying_pinned_group(self, command):
        self.windows[0]["layout"]["pos_in_scrolling_layout"] = [1, 1]
        self.windows[1]["layout"]["pos_in_scrolling_layout"] = [1, 2]
        groups = [{"id": "group-1", "label": "Work", "members": ["terminal", "files"]}]
        pin = {"id": "group-pin", "group_id": "group-1", "label": "Work", "app_id": "terminal",
               "members": ["terminal", "files"], "window_id": 1, "window_ids": [1, 2], "session": "session"}
        pins = [pin]

        result = spaces.run(pins, "group-view", None, self.ws, self.windows, "session", groups)

        command.assert_called_once_with("action", "toggle-column-tabbed-display")
        self.assertEqual(pins, [pin])
        self.assertTrue(result[0]["available"])

    @patch.object(spaces, "command")
    def test_group_view_rejects_singleton_unsaved_and_ambiguous_groups(self, command):
        self.windows[0]["layout"]["pos_in_scrolling_layout"] = [1, 1]
        self.windows[1]["layout"]["pos_in_scrolling_layout"] = [1, 2]
        group = {"id": "group-1", "label": "Work", "members": ["terminal", "files"]}
        cases = [
            (self.windows[:1], [group]),
            (self.windows, []),
            (self.windows, [group, {**group, "id": "group-2", "label": "Duplicate"}]),
            (self.windows + [window(3, "terminal", 2)], [group]),
        ]
        for windows, groups in cases:
            with self.subTest(groups=groups, window_count=len(windows)), self.assertRaisesRegex(ValueError, "saved two-app Group"):
                spaces.run([], "group-view", None, self.ws, windows, "session", groups)
        command.assert_not_called()

    @patch.object(spaces, "command")
    def test_renamed_saved_group_updates_its_pinned_label(self, command):
        group = {"id": "group-1", "label": "Studio", "members": ["terminal", "files"]}
        pin = {"id": "group-pin", "group_id": "group-1", "label": "Work", "app_id": "terminal",
               "members": ["terminal", "files"], "window_id": 1, "window_ids": [1, 2], "session": "session"}

        result = spaces.run([pin], "refresh", None, self.ws, self.windows, "session", [group])

        self.assertEqual(pin["label"], "Studio")
        self.assertEqual(result[0]["label"], "Studio")
        spaces.run([pin], "refresh", None, self.ws, self.windows, "session",
                   [{**group, "label": "Other", "members": ["terminal", "other"]}])
        self.assertEqual(pin["label"], "Studio")

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

    def test_session_id_changes_when_socket_inode_is_reused(self):
        with patch.dict(spaces.os.environ, {"NIRI_SOCKET": "/tmp/niri.sock"}), \
             patch.object(Path, "read_text", return_value="boot-id\n"), \
             patch.object(spaces.os, "stat", side_effect=[SimpleNamespace(st_ino=7, st_ctime_ns=10),
                                                           SimpleNamespace(st_ino=7, st_ctime_ns=11)]):
            self.assertNotEqual(spaces.session_id(), spaces.session_id())

    @patch.object(spaces, "command")
    @patch.object(spaces.time, "sleep")
    @patch.object(spaces.subprocess, "run")
    def test_missing_pin_launches_exact_desktop_entry_and_focuses(self, launch, sleep, command):
        pin = {"id": "pin", "app_id": "files", "window_id": 9, "session": "old", "label": "Files"}
        with tempfile.TemporaryDirectory() as directory:
            apps = Path(directory) / "applications"
            apps.mkdir()
            (apps / "files.desktop").write_text("[Desktop Entry]\n")
            with patch.dict(spaces.os.environ, {"XDG_DATA_HOME": directory, "XDG_DATA_DIRS": directory}), \
                 patch.object(spaces, "snapshot", return_value=(self.ws, self.windows)):
                result = spaces.run([pin], "focus", "pin", self.ws, [self.windows[0]], "session")
        self.assertTrue(result[0]["available"])
        self.assertEqual(pin["window_id"], 2)
        self.assertEqual(launch.call_args.args[0], ["gio", "launch", str(apps / "files.desktop")])
        command.assert_any_call("action", "focus-window", "--id", 2)

    @patch.object(spaces.subprocess, "run")
    def test_ambiguous_pin_does_not_launch_another_window(self, launch):
        pin = {"id": "pin", "app_id": "terminal", "window_id": 9, "session": "old", "label": "Terminal"}
        windows = self.windows + [window(3, "terminal", 3)]
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            spaces.run([pin], "focus", "pin", self.ws, windows, "session")
        launch.assert_not_called()

    @patch.object(spaces.subprocess, "run")
    def test_missing_desktop_entry_is_reported_without_launch(self, launch):
        pin = {"id": "pin", "app_id": "missing", "window_id": 9, "session": "old", "label": "Missing"}
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(spaces.os.environ, {"XDG_DATA_HOME": directory, "XDG_DATA_DIRS": directory}):
            with self.assertRaisesRegex(ValueError, "No matching installed application"):
                spaces.run([pin], "focus", "pin", self.ws, self.windows, "session")
        launch.assert_not_called()

    @patch.object(spaces, "command")
    @patch.object(spaces.time, "sleep")
    @patch.object(spaces.subprocess, "run")
    def test_changed_workspace_does_not_steal_focus(self, launch, sleep, command):
        pin = {"id": "pin", "app_id": "files", "window_id": 9, "session": "old", "label": "Files"}
        with tempfile.TemporaryDirectory() as directory:
            apps = Path(directory) / "applications"
            apps.mkdir()
            (apps / "files.desktop").touch()
            with patch.dict(spaces.os.environ, {"XDG_DATA_HOME": directory, "XDG_DATA_DIRS": directory}), \
                 patch.object(spaces, "snapshot", return_value=({"id": 2}, self.windows)):
                with self.assertRaisesRegex(ValueError, "Workspace changed"):
                    spaces.run([pin], "focus", "pin", self.ws, [self.windows[0]], "session")
        command.assert_not_called()

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

    @patch.object(spaces, "command")
    def test_unavailable_pin_does_not_block_reconciling_live_pins(self, command):
        other_workspace = {**self.windows[0], "workspace_id": 2}
        pins = [
            {"app_id": "terminal", "window_id": 1, "session": "session"},
            {"app_id": "files", "window_id": 2, "session": "session"},
        ]

        spaces.reconcile(pins, self.ws, [other_workspace, self.windows[1]], "session")

        self.assertEqual(command.call_args_list, [
            unittest.mock.call("action", "focus-window", "--id", 2),
            unittest.mock.call("action", "move-column-to-index", 1),
            unittest.mock.call("action", "focus-window", "--id", 2),
        ])

    @patch.object(spaces, "command")
    def test_unavailable_pins_alone_do_not_issue_compositor_actions(self, command):
        pin = {"app_id": "terminal", "window_id": 1, "session": "session"}
        window = {**self.windows[0], "workspace_id": 2}

        spaces.reconcile([pin], self.ws, [window], "session")

        command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
