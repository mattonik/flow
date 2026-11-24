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
        self.niri.logical = OUTPUTS["Virtual-1"]["logical"]
        self.session = "session-1"
        patcher = patch.object(spaces.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def invoke(self, *args, expect_error=None):
        ws, _ = self.niri.snapshot()
        with patch.object(sys, "argv", ["flow-spaces", *args]), \
             patch.object(spaces, "snapshot", side_effect=self.niri.snapshot), \
             patch.object(spaces, "display_state", side_effect=lambda: (OUTPUTS, [self.niri.snapshot()[0]])), \
             patch.object(spaces, "full_state", side_effect=lambda: (*spaces.snapshot(), *spaces.display_state())), \
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
        self.assertIn("choose the ones that belong", result["incomplete_groups"][0]["status"])
        self.invoke("group-layout", self.groups()[0]["id"], expect_error="unavailable or ambiguous")
        # Neither terminal can be told apart from the Group's, so neither is floated out as a Layer.
        self.niri("action", "focus-window", "--id", 5)
        self.invoke("make-layer", expect_error="Remove this app from its Group")
        # Once only one window of each app is left, the Group is recognized and bound again.
        self.niri("action", "close-window", "--id", 5)
        self.niri("action", "close-window", "--id", 6)
        result = self.invoke("refresh")
        self.assertEqual(len(self.group_space(result)), 1)
        self.assertEqual(self.groups()[0]["bound"]["session"], "session-2")

    def test_a_closed_member_is_not_replaced_from_another_workspace(self):
        self.invoke("create-group")
        elsewhere = {"id": 40, "app_id": "editor", "title": "editor", "workspace_id": 2, "is_floating": False,
                     "layout": {"pos_in_scrolling_layout": [1, 1], "tile_size": [500, 700]}}
        snapshot = self.niri.snapshot
        self.niri.snapshot = lambda: (snapshot()[0], snapshot()[1] + [elsewhere])
        self.niri("action", "close-window", "--id", 2)
        result = self.invoke("refresh")
        (incomplete,) = result["incomplete_groups"]
        self.assertEqual((incomplete["can_open"], incomplete["missing_app"]), (True, "editor"))
        self.assertEqual(self.groups()[0]["bound"]["windows"], {"terminal": 1, "editor": 2})

    def test_other_windows_opening_do_not_rewrite_the_registry(self):
        self.invoke("create-group")
        before = (self.state / "groups.json").read_bytes()
        self.niri.open_window(50, "browser")
        self.invoke("refresh")
        self.assertEqual((self.state / "groups.json").read_bytes(), before)

    def test_another_window_of_a_member_app_is_its_own_space_for_every_action(self):
        self.invoke("create-group")
        self.niri("action", "focus-window", "--id", 5)
        self.invoke("make-layer")
        self.assertEqual(self.niri.floating, [5])
        self.invoke("member-left", "6", expect_error="not a Group member")
        self.niri("action", "focus-window", "--id", 6)
        self.invoke("pin")

    def test_after_a_bound_member_closes_other_windows_of_its_app_are_free(self):
        self.invoke("create-group")
        self.niri("action", "close-window", "--id", 2)
        self.niri("action", "focus-window", "--id", 6)
        self.invoke("make-layer")
        self.assertEqual(self.niri.floating, [6])

    def test_a_window_outside_the_live_group_is_refused_cleanly(self):
        group = {"id": "g", "label": "Pair", "members": ["terminal", "editor"]}
        ws, windows = self.niri.snapshot()
        live = [w for w in windows if w["id"] in (1, 2)]
        with patch.object(spaces, "group_of", return_value=group), \
             patch.object(spaces, "live_group", return_value=(group, live)), \
             self.assertRaisesRegex(ValueError, "not a Group member"):
            spaces.group_of_window([group], 5, ws, windows, "s")

    def test_after_a_restart_the_user_chooses_which_window_belongs_to_the_group(self):
        self.invoke("create-group")
        group = self.groups()[0]["id"]
        self.session = "session-2"
        (incomplete,) = self.invoke("refresh")["incomplete_groups"]
        self.assertEqual([(c["app"], [w["id"] for w in c["windows"]]) for c in incomplete["choices"]],
                         [("terminal", [1, 5]), ("editor", [2, 6])])
        # Choosing the terminal also settles the editor stacked with it; nothing else is guessed.
        result = self.invoke("choose-member", f"{group}:1")
        self.assertEqual([[m["id"] for m in s["members"]] for s in self.group_space(result)], [[1, 2]])
        self.assertEqual(self.groups()[0]["bound"], {"session": "session-2", "windows": {"terminal": 1, "editor": 2}})
        self.assertEqual(self.niri.columns, [[1, 2], [5], [6]])

    def test_a_side_by_side_group_needs_a_choice_for_each_ambiguous_app(self):
        self.niri.columns = [[1], [2], [5], [6]]
        self.niri("action", "focus-window", "--id", 1)
        self.invoke("stack")
        group = self.invoke("create-group") and self.groups()[0]["id"]
        self.invoke("group-layout", group)
        self.session = "session-2"
        self.invoke("choose-member", f"{group}:1")
        (incomplete,) = self.invoke("refresh")["incomplete_groups"]
        self.assertEqual([c["app"] for c in incomplete["choices"]], ["editor"])
        result = self.invoke("choose-member", f"{group}:2")
        self.assertEqual([len(s["members"]) for s in self.group_space(result)], [2])

    def test_only_an_ambiguous_member_app_on_this_workspace_can_be_chosen(self):
        self.invoke("create-group")
        group = self.groups()[0]["id"]
        self.invoke("choose-member", f"{group}:5", expect_error="not ambiguous")
        self.session = "session-2"
        self.niri.apps[9] = "viewer"
        self.niri.columns.append([9])
        self.invoke("choose-member", f"{group}:9", expect_error="not one of this Group")
        self.invoke("choose-member", f"{group}:77", expect_error="not one of this Group")
        self.assertNotEqual(self.groups()[0]["bound"]["session"], "session-2")

    def elsewhere(self, *extra):
        snapshot = self.niri.snapshot
        self.niri.snapshot = lambda: (snapshot()[0], snapshot()[1] + list(extra))

    def window_on(self, ident, app, workspace=2, floating=False):
        return {"id": ident, "app_id": app, "title": f"{app} {ident}", "workspace_id": workspace, "is_floating": floating,
                "layout": {"pos_in_scrolling_layout": None if floating else [1, 1], "tile_size": [500, 700]}}

    def test_observing_never_writes_the_registry_even_when_a_binding_would_change(self):
        self.invoke("create-group")
        self.session = "session-2"
        self.niri("action", "close-window", "--id", 5)
        self.niri("action", "close-window", "--id", 6)
        before = (self.state / "groups.json").read_bytes()
        self.invoke("observe")
        self.assertEqual((self.state / "groups.json").read_bytes(), before)

    def test_choices_offer_only_tiled_windows_here_and_explain_other_workspaces(self):
        self.invoke("create-group")
        group = self.groups()[0]["id"]
        self.session = "session-2"
        self.elsewhere(self.window_on(40, "terminal"), self.window_on(41, "terminal", workspace=1, floating=True))
        (incomplete,) = self.invoke("refresh")["incomplete_groups"]
        terminals = next(c for c in incomplete["choices"] if c["app"] == "terminal")
        self.assertEqual([w["id"] for w in terminals["windows"]], [1, 5])
        self.invoke("choose-member", f"{group}:40", expect_error="on this workspace")
        self.invoke("choose-member", f"{group}:41", expect_error="Make this Layer a space")
        # With every duplicate on another workspace there is nothing to choose here, and the card says why.
        self.niri.columns = [[2]]
        self.niri.snapshot = FakeNiri.snapshot.__get__(self.niri)
        self.elsewhere(self.window_on(40, "terminal"), self.window_on(42, "terminal"))
        (incomplete,) = self.invoke("refresh")["incomplete_groups"]
        self.assertIn("on another workspace; choose them there", incomplete["status"])

    def test_choosing_needs_a_known_niri_session(self):
        group = {"id": "g", "label": "Pair", "members": ["terminal", "editor"]}
        ws, windows = self.niri.snapshot()
        with self.assertRaisesRegex(ValueError, "Cannot identify this Niri session"):
            spaces.run([], "choose-member", "g:1", ws, windows, "s", [group])

    def test_a_wrong_choice_can_be_undone(self):
        self.niri.columns = [[1], [2], [5], [6]]
        self.niri("action", "focus-window", "--id", 1)
        self.invoke("stack")
        self.invoke("create-group")
        group = self.groups()[0]["id"]
        self.invoke("group-layout", group)
        self.session = "session-2"
        self.invoke("choose-member", f"{group}:5")
        (incomplete,) = self.invoke("refresh")["incomplete_groups"]
        self.assertTrue(incomplete["can_forget_choice"])
        self.invoke("forget-choice", group)
        self.assertNotIn("bound", self.groups()[0])
        self.invoke("forget-choice", group, expect_error="No window was chosen")
        self.invoke("choose-member", f"{group}:1")
        result = self.invoke("choose-member", f"{group}:2")
        self.assertEqual([len(s["members"]) for s in self.group_space(result)], [2])
        self.invoke("forget-choice", group, expect_error="Group is working")

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
