"""Groups beside other windows of their apps: bound windows in a session, never a guess."""
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
import spaces
from fake_niri import FakeNiri

OUTPUTS = {"Virtual-1": {"make": "QEMU", "model": "Monitor", "serial": None,
                         "logical": {"x": 0, "y": 0, "width": 1280, "height": 800}}}


class SameAppTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)
        (self.state / "groups.initialized").touch()
        (self.state / "groups.json").write_text('{"version": 1, "groups": []}')
        # Two terminals and two editors: the Group is terminal 1 + editor 2; 5 and 6 are other windows of those apps.
        self.niri = FakeNiri([[1, 2], [5], [6]], {1: "terminal", 2: "editor", 5: "terminal", 6: "editor"}, 1)
        self.session = "session-1"
        patcher = patch.object(spaces.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def invoke(self, *args, expect_error=None):
        ws, _ = self.niri.snapshot()
        with patch.object(sys, "argv", ["flow-spaces", *args]), \
             patch.object(spaces, "snapshot", side_effect=self.niri.snapshot), \
             patch.object(spaces, "display_state", side_effect=lambda: (OUTPUTS, [self.niri.snapshot()[0]])), \
             patch.object(spaces, "session_id", side_effect=lambda: self.session), \
             patch.object(spaces, "command", side_effect=self.niri), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            if expect_error:
                with self.assertRaisesRegex(ValueError, expect_error):
                    spaces.run_locked(self.state)
                return None
            spaces.run_locked(self.state)
        return json.loads(output.getvalue())

    def groups(self):
        return json.loads((self.state / "groups.json").read_text())["groups"]

    def group_space(self, result):
        return [s for s in result["spaces"] if s.get("group_id")]

    def test_a_group_forms_and_works_beside_other_windows_of_its_apps(self):
        created = self.invoke("create-group")
        (group,) = self.groups()
        self.assertEqual(group["bound"]["windows"], {"terminal": 1, "editor": 2})
        self.assertEqual([[m["id"] for m in s["members"]] for s in self.group_space(created)], [[1, 2]])
        self.assertEqual(len(created["spaces"]), 3)
        self.invoke("group-layout", group["id"])
        self.assertEqual(self.niri.layout(), [["terminal"], ["editor"], ["terminal"], ["editor"]])
        self.assertEqual(self.niri.columns, [[1], [2], [5], [6]])
        self.invoke("group-layout", group["id"])
        self.invoke("dissolve-group", group["id"])
        self.assertEqual((self.groups(), self.niri.columns), ([], [[1], [2], [5], [6]]))

    def test_a_closed_member_is_never_replaced_by_an_older_window_of_its_app(self):
        self.invoke("create-group")
        group = self.groups()[0]
        self.niri("action", "close-window", "--id", 2)
        result = self.invoke("refresh")
        self.assertEqual(self.group_space(result), [])
        (incomplete,) = result["incomplete_groups"]
        self.assertEqual((incomplete["can_open"], incomplete["missing_app"]), (True, "editor"))
        # Opening the missing app binds its new window, not the older editor 6.
        def launch(args, **kw):
            self.niri.open_window(20, "editor")
        with patch.object(spaces, "desktop_file", return_value=Path("/x/editor.desktop")), \
             patch.object(spaces.subprocess, "run", side_effect=launch):
            self.invoke("open-group-member", group["id"])
        self.assertEqual(self.groups()[0]["bound"]["windows"]["editor"], 20)

    def test_a_newer_window_stands_in_for_a_reopened_member(self):
        self.invoke("create-group")
        self.niri("action", "close-window", "--id", 2)
        # The app is reopened and its new window stacked back into the Group's column.
        self.niri.apps[30] = "editor"
        self.niri.columns = [[1, 30], [5], [6]]
        result = self.invoke("refresh")
        self.assertEqual([[m["id"] for m in s["members"]] for s in self.group_space(result)], [[1, 30]])
        self.assertEqual(self.groups()[0]["bound"]["windows"]["editor"], 30)

    def test_another_niri_session_does_not_trust_old_bindings(self):
        self.invoke("create-group")
        self.session = "session-2"
        result = self.invoke("refresh")
        self.assertEqual(self.group_space(result), [])
        self.assertIn("Multiple windows", result["incomplete_groups"][0]["status"])
        self.invoke("group-layout", self.groups()[0]["id"], expect_error="unavailable or ambiguous")
        # Once only one window of each app is left, the Group is recognized and bound again.
        self.niri("action", "close-window", "--id", 5)
        self.niri("action", "close-window", "--id", 6)
        result = self.invoke("refresh")
        self.assertEqual(len(self.group_space(result)), 1)
        self.assertEqual(self.groups()[0]["bound"]["session"], "session-2")

    def test_the_added_window_is_bound_even_when_its_app_has_another_window(self):
        self.niri.columns = [[1], [2], [5], [6]]
        self.niri.apps[5] = "viewer"
        self.niri.apps[7] = "viewer"
        self.niri.columns.append([7])
        self.niri("action", "focus-window", "--id", 1)
        self.invoke("stack")
        created = self.invoke("create-group")
        group = self.groups()[0]
        self.niri("action", "focus-window", "--id", 1)
        self.invoke("add-to-group", group["id"])
        self.assertEqual(self.niri.columns[0], [1, 2, 5])
        self.assertEqual(self.groups()[0]["bound"]["windows"]["viewer"], 5)
        self.assertEqual([len(s["members"]) for s in self.group_space(self.invoke("refresh"))], [3])
        del created


if __name__ == "__main__":
    unittest.main()
