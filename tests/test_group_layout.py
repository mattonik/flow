import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "session"))
import groups
import spaces


def window(ident, app, column, row=1, workspace=1):
    return {"id": ident, "app_id": app, "title": app, "workspace_id": workspace,
            "is_floating": False, "layout": {"pos_in_scrolling_layout": [column, row]}}


class GroupLayoutTest(unittest.TestCase):
    def setUp(self):
        self.ws = {"id": 1, "active_window_id": 11}
        self.windows = [window(11, "a", 1, 1), window(12, "b", 2, 1), window(13, "other", 3)]
        self.group = {"id": "g", "label": "Work", "members": ["a", "b"], "layout": "side-by-side"}

    def test_side_by_side_projects_once_in_member_order_and_keeps_other_windows(self):
        self.ws["active_window_id"] = 12
        result = spaces.display_spaces(self.ws, self.windows, [self.group])
        self.assertEqual([s.get("group_id") for s in result], ["g", None])
        self.assertEqual([w["app_id"] for w in result[0]["members"]], ["a", "b"])
        self.assertTrue(result[0]["active"])
        self.assertEqual(result[0]["id"], 12)
        self.assertEqual([w["app_id"] for w in result[1]["members"]], ["other"])

    def test_side_by_side_projection_rejects_ambiguous_or_incomplete_members(self):
        cases = {
            "duplicate": self.windows + [window(14, "a", 4)],
            "missing": [self.windows[0], self.windows[2]],
            "other workspace": [self.windows[0], window(12, "b", 2, workspace=2), self.windows[2]],
            "changed position": [self.windows[0], window(12, "b", 3), self.windows[2]],
        }
        for name, live in cases.items():
            with self.subTest(name=name):
                result = spaces.display_spaces(self.ws, live, [self.group])
                self.assertFalse(any(s.get("group_id") == "g" for s in result))

    def test_pending_layout_is_not_projected_as_a_complete_group(self):
        group = {**self.group, "pending_layout": "stack"}
        result = spaces.display_spaces(self.ws, self.windows, [group])
        self.assertFalse(any(s.get("group_id") == "g" for s in result))

    def test_change_layout_persists_pending_before_action_and_completes_both_directions(self):
        for source, positions in (("stack", ((1, 1), (1, 2))),
                                  ("side-by-side", ((1, 1), (2, 1)))):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                state = Path(directory)
                self.windows[0]["layout"]["pos_in_scrolling_layout"] = list(positions[0])
                self.windows[1]["layout"]["pos_in_scrolling_layout"] = list(positions[1])
                group = {"id": "g", "label": "Work", "members": ["a", "b"], "layout": source}
                registry = [group]
                pin = {"id": "p", "group_id": "g", "members": ["a", "b"],
                       "window_ids": [11, 12], "window_id": 12, "session": "s"}
                pins = [pin]
                destination = "side-by-side" if source == "stack" else "stack"
                actions = []

                def action(*args):
                    self.assertEqual(json.loads((state / "groups.json").read_text())["groups"][0]["pending_layout"], destination)
                    actions.append(args)
                    if args[1].startswith("consume-or-expel"):
                        self.windows[0]["layout"]["pos_in_scrolling_layout"] = [1, 1]
                        self.windows[1]["layout"]["pos_in_scrolling_layout"] = [2, 1] if destination == "side-by-side" else [1, 2]

                groups.write(state / "groups.json", registry)
                with patch.object(spaces, "snapshot", side_effect=lambda: (self.ws, self.windows)), \
                     patch.object(spaces, "command", side_effect=action):
                    spaces.change_group_layout(state, registry, pins, "g", self.ws, self.windows, "s")

                self.assertEqual(group["layout"], destination)
                self.assertNotIn("pending_layout", group)
                self.assertEqual([args[1] for args in actions if args[1].startswith("consume-or-expel")],
                                 ["consume-or-expel-window-right" if destination == "side-by-side" else "consume-or-expel-window-left"])
                self.assertEqual(pins[0]["id"], "p")
                self.assertEqual(pins[0]["window_ids"], [11, 12])
                self.assertEqual(pins[0]["window_id"], 12)

    def test_action_failure_keeps_pending_registry_for_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            self.windows[1]["layout"]["pos_in_scrolling_layout"] = [1, 2]
            group = {"id": "g", "label": "Work", "members": ["a", "b"], "layout": "stack"}
            registry = [group]
            groups.write(state / "groups.json", registry)
            with patch.object(spaces, "snapshot", return_value=(self.ws, self.windows)), \
                 patch.object(spaces, "command", side_effect=OSError("Niri stopped")), \
                 self.assertRaisesRegex(ValueError, "interrupted"):
                spaces.change_group_layout(state, registry, [], "g", self.ws, self.windows, "s")
            saved = groups.load(state)[0]
            self.assertEqual(saved, {**group, "pending_layout": "side-by-side"})
            self.assertEqual(saved["layout"], "stack")

    def test_resume_finishes_transitions_already_applied_and_both_directions(self):
        for source, pending, positions in (
                ("stack", "side-by-side", ((1, 1), (2, 1))),
                ("side-by-side", "stack", ((1, 1), (1, 2)))):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                state = Path(directory)
                self.windows[0]["layout"]["pos_in_scrolling_layout"] = list(positions[0])
                self.windows[1]["layout"]["pos_in_scrolling_layout"] = list(positions[1])
                group = {"id": "g", "label": "Work", "members": ["a", "b"],
                         "layout": source, "pending_layout": pending}
                registry = [group]
                groups.write(state / "groups.json", registry)
                actions = []
                with patch.object(spaces, "snapshot", side_effect=lambda: (self.ws, self.windows)), \
                     patch.object(spaces, "command", side_effect=lambda *args: actions.append(args)):
                    spaces.change_group_layout(state, registry, [], "g", self.ws, self.windows, "s", resume=True)
                self.assertEqual(group["layout"], pending)
                self.assertNotIn("pending_layout", group)
                self.assertFalse(any(args[1].startswith("consume-or-expel") for args in actions))
                self.assertEqual(groups.load(state)[0], group)

    def test_resume_applies_transition_when_compositor_is_still_in_source_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            self.windows[0]["layout"]["pos_in_scrolling_layout"] = [1, 1]
            self.windows[1]["layout"]["pos_in_scrolling_layout"] = [1, 2]
            group = {"id": "g", "label": "Work", "members": ["a", "b"],
                     "layout": "stack", "pending_layout": "side-by-side"}
            registry = [group]
            groups.write(state / "groups.json", registry)
            actions = []

            def action(*args):
                actions.append(args)
                if args[1] == "consume-or-expel-window-right":
                    self.windows[0]["layout"]["pos_in_scrolling_layout"] = [1, 1]
                    self.windows[1]["layout"]["pos_in_scrolling_layout"] = [2, 1]

            with patch.object(spaces, "snapshot", side_effect=lambda: (self.ws, self.windows)), \
                 patch.object(spaces, "command", side_effect=action):
                spaces.change_group_layout(state, registry, [], "g", self.ws, self.windows, "s", resume=True)

            self.assertTrue(any(args[1] == "consume-or-expel-window-right" for args in actions))
            self.assertEqual(groups.load(state)[0], {k: v for k, v in group.items() if k != "pending_layout"})
            self.assertEqual(groups.load(state)[0]["layout"], "side-by-side")

    def test_final_registry_write_failure_retains_pending_marker_after_layout_change(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            self.windows[0]["layout"]["pos_in_scrolling_layout"] = [1, 1]
            self.windows[1]["layout"]["pos_in_scrolling_layout"] = [1, 2]
            group = {"id": "g", "label": "Work", "members": ["a", "b"], "layout": "stack"}
            registry = [group]
            groups.write(state / "groups.json", registry)
            original_write = groups.write
            write_count = 0

            def write_then_fail(path, data):
                nonlocal write_count
                write_count += 1
                if write_count == 1:
                    return original_write(path, data)
                raise OSError("disk full")

            def action(*args):
                if args[1] == "consume-or-expel-window-right":
                    self.windows[0]["layout"]["pos_in_scrolling_layout"] = [1, 1]
                    self.windows[1]["layout"]["pos_in_scrolling_layout"] = [2, 1]

            with patch.object(spaces, "snapshot", side_effect=lambda: (self.ws, self.windows)), \
                 patch.object(spaces, "command", side_effect=action) as command, \
                 patch.object(spaces.group_store, "write", side_effect=write_then_fail), \
                 self.assertRaisesRegex(ValueError, "interrupted"):
                spaces.change_group_layout(state, registry, [], "g", self.ws, self.windows, "s")

            saved = json.loads((state / "groups.json").read_text())["groups"][0]
            self.assertEqual(saved["layout"], "stack")
            self.assertEqual(saved["pending_layout"], "side-by-side")
            self.assertEqual(self.windows[1]["layout"]["pos_in_scrolling_layout"], [2, 1])
            command.assert_any_call("action", "consume-or-expel-window-right", "--id", 12)

    def test_change_layout_rejects_wrong_focus_or_workspace_before_writing(self):
        stack = [window(11, "a", 1, 1), window(12, "b", 1, 2)]
        cases = (({**self.ws, "active_window_id": 99}, self.ws, stack, "Select the Group", "focus"),
                 (self.ws, {**self.ws, "id": 2},
                  [window(11, "a", 1, 1, workspace=2), window(12, "b", 1, 2, workspace=2)],
                  "Group or focus changed", "workspace"))
        for workspace, current, current_windows, message, label in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                state = Path(directory)
                group = {"id": "g", "label": "Work", "members": ["a", "b"], "layout": "stack"}
                registry = [group]
                groups.write(state / "groups.json", registry)
                before = (state / "groups.json").read_bytes()
                with patch.object(spaces, "snapshot", return_value=(current, current_windows)), \
                     patch.object(spaces, "command") as command, \
                     self.assertRaisesRegex(ValueError, message):
                    spaces.change_group_layout(state, registry, [], "g", workspace, stack, "s")
                command.assert_not_called()
                self.assertEqual((state / "groups.json").read_bytes(), before)

    def test_explicit_repair_moves_only_the_saved_order_member_and_restores_focus(self):
        live = [window(11, "a", 1), window(13, "other", 2), window(12, "b", 3)]
        self.ws["active_window_id"] = 11
        actions = []

        def action(*args):
            actions.append(args)
            if args[1] == "focus-window":
                self.ws["active_window_id"] = int(args[-1])
            elif args[1] == "move-column-to-index":
                live[1]["layout"]["pos_in_scrolling_layout"] = [3, 1]
                live[2]["layout"]["pos_in_scrolling_layout"] = [2, 1]

        with patch.object(spaces, "snapshot", side_effect=lambda: (self.ws, live)), \
             patch.object(spaces, "command", side_effect=action):
            spaces.repair_side_group("g", self.ws, live, [self.group], [])

        self.assertEqual([args[1] for args in actions],
                         ["focus-window", "move-column-to-index", "focus-window"])
        self.assertEqual(actions[1], ("action", "move-column-to-index", 2))
        self.assertEqual([w["app_id"] for w in sorted(live, key=lambda w: w["layout"]["pos_in_scrolling_layout"])],
                         ["a", "b", "other"])
        self.assertEqual(self.ws["active_window_id"], 11)

    def test_incomplete_group_reports_safe_repair_and_rejects_unsafe_interveners(self):
        live = [window(11, "a", 1), window(13, "other", 2), window(12, "b", 3)]
        entry = spaces.incomplete_groups(self.ws, live, [self.group], [])[0]
        self.assertTrue(entry["can_repair"])
        self.assertIn("repair", entry["status"])
        pin = {"id": "p", "app_id": "other", "label": "Other"}
        entry = spaces.incomplete_groups(self.ws, live, [self.group], [pin])[0]
        self.assertFalse(entry["can_repair"])
        self.assertIn("pin", entry["status"])

    def test_repair_rejects_missing_duplicate_focus_and_workspace_changes(self):
        cases = [
            ([window(11, "a", 1), window(13, "other", 2)], self.ws, "missing or ambiguous"),
            ([window(11, "a", 1), window(13, "other", 2), window(12, "b", 3), window(14, "a", 4)], self.ws,
             "missing or ambiguous"),
            ([window(11, "a", 1), window(13, "other", 2), window(12, "b", 3)],
             {**self.ws, "active_window_id": 99}, "changed during repair"),
            ([window(11, "a", 1, workspace=2), window(13, "other", 2, workspace=2),
              window(12, "b", 3, workspace=2)], {**self.ws, "id": 2}, "another workspace"),
        ]
        for live, ws, error in cases:
            with self.subTest(error=error), patch.object(spaces, "snapshot", return_value=(ws, live)), \
                 patch.object(spaces, "command") as action:
                with self.assertRaisesRegex(ValueError, error):
                    spaces.repair_side_group("g", self.ws, live, [self.group], [])
                action.assert_not_called()

    def test_repair_keeps_an_interrupted_command_visible(self):
        live = [window(11, "a", 1), window(13, "other", 2), window(12, "b", 3)]
        with patch.object(spaces, "snapshot", return_value=(self.ws, live)), \
             patch.object(spaces, "command", side_effect=OSError("Niri stopped")), \
             self.assertRaisesRegex(ValueError, "interrupted"):
            spaces.repair_side_group("g", self.ws, live, [self.group], [])

    def test_repair_rejects_stacked_member_columns_and_pinned_group_neighbor(self):
        stacked = [window(11, "a", 1), window(15, "extra", 1, 2), window(13, "other", 2), window(12, "b", 3)]
        with self.assertRaisesRegex(ValueError, "only windows"):
            spaces.repair_side_group("g", self.ws, stacked, [self.group], [])
        live = [window(11, "a", 1), window(13, "other", 2), window(12, "b", 3)]
        other_group = {"id": "h", "label": "Other Group", "members": ["other", "x"]}
        with self.assertRaisesRegex(ValueError, "another Group or pin"):
            spaces.repair_side_group("g", self.ws, live, [self.group, other_group], [])
        group_pin = {"id": "p", "group_id": "h", "members": ["other", "x"]}
        with self.assertRaisesRegex(ValueError, "another Group or pin"):
            spaces.repair_side_group("g", self.ws, live, [self.group], [group_pin])

    def test_repair_does_not_restore_focus_after_an_unrelated_focus_change(self):
        live = [window(11, "a", 1), window(13, "other", 2), window(12, "b", 3)]
        self.ws["active_window_id"] = 11
        calls = []
        actions = []

        def current_state():
            calls.append(None)
            if len(calls) == 4:
                self.ws["active_window_id"] = 13
            return self.ws, live

        def action(*args):
            actions.append(args)
            if args[1] == "focus-window":
                self.ws["active_window_id"] = int(args[-1])
            elif args[1] == "move-column-to-index":
                live[1]["layout"]["pos_in_scrolling_layout"] = [3, 1]
                live[2]["layout"]["pos_in_scrolling_layout"] = [2, 1]

        with patch.object(spaces, "snapshot", side_effect=current_state), \
             patch.object(spaces, "command", side_effect=action), \
             self.assertRaisesRegex(ValueError, "interrupted"):
            spaces.repair_side_group("g", self.ws, live, [self.group], [])
        self.assertEqual([args[1] for args in actions], ["focus-window", "move-column-to-index"])


if __name__ == "__main__":
    unittest.main()
