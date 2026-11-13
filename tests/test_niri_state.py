import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("niri_state", Path(__file__).parents[1] / "session/niri_state.py")
niri_state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(niri_state)


def window(ident, focused=False):
    return {"id": ident, "title": f"Window {ident}", "workspace_id": 10, "is_focused": focused}


class NiriStateTest(unittest.TestCase):
    def setUp(self):
        self.state = niri_state.NiriState()

    def load_snapshot(self):
        self.assertTrue(self.state.apply({"WorkspacesChanged": {"workspaces": [
            {"id": 10, "output": "DP-1", "is_active": True, "is_focused": True},
            {"id": 11, "output": "DP-1", "is_active": False, "is_focused": False},
        ]}}))
        self.assertTrue(self.state.apply({"WindowsChanged": {"windows": [window(1, True), window(2)]}}))

    def test_initial_snapshot_replaces_state_and_sets_focus(self):
        self.load_snapshot()
        self.assertTrue(self.state.ready)
        self.assertEqual(set(self.state.windows), {1, 2})
        self.assertEqual(self.state.focused_window_id, 1)

    def test_window_change_and_close_project_incrementally(self):
        self.load_snapshot()
        self.assertTrue(self.state.apply({"WindowOpenedOrChanged": {"window": window(3)}}))
        self.assertIn(3, self.state.windows)
        self.assertTrue(self.state.apply({"WindowClosed": {"id": 2}}))
        self.assertNotIn(2, self.state.windows)

    def test_unfocused_window_update_clears_its_stale_focus(self):
        self.load_snapshot()
        self.assertTrue(self.state.apply({"WindowOpenedOrChanged": {"window": window(1)}}))
        self.assertIsNone(self.state.focused_window_id)
        self.assertFalse(self.state.windows[1]["is_focused"])
        self.assertFalse(self.state.windows[2]["is_focused"])

    def test_focus_event_clears_previous_focus_and_updates_workspace(self):
        self.load_snapshot()
        self.assertTrue(self.state.apply({"WindowFocusChanged": {"id": 2}}))
        self.assertFalse(self.state.windows[1]["is_focused"])
        self.assertTrue(self.state.windows[2]["is_focused"])
        self.assertTrue(self.state.apply({"WorkspaceActiveWindowChanged": {
            "workspace_id": 10, "active_window_id": 2}}))
        self.assertEqual(self.state.workspaces[10]["active_window_id"], 2)

    def test_window_layouts_changed_uses_niri_26_04_tuple_array_shape(self):
        self.load_snapshot()
        layout = {"pos_in_scrolling_layout": [3, 1], "window_size": [800, 600]}
        self.assertTrue(self.state.apply({"WindowLayoutsChanged": {"changes": [[2, layout]]}}))
        self.assertEqual(self.state.windows[2]["layout"], layout)
        self.assertNotIn("layout", self.state.windows[1])

    def test_reconnect_reset_waits_for_both_fresh_snapshots(self):
        self.load_snapshot()
        self.state.reset()
        self.assertFalse(self.state.ready)
        self.assertEqual(self.state.windows, {})
        self.assertIsNone(self.state.focused_window_id)
        self.state.apply({"WindowsChanged": {"windows": [window(4, True)]}})
        self.assertFalse(self.state.ready)
        self.state.apply({"WorkspacesChanged": {"workspaces": []}})
        self.assertTrue(self.state.ready)
        self.assertEqual(set(self.state.windows), {4})

    def test_malformed_event_does_not_mutate_projection(self):
        self.load_snapshot()
        before = (dict(self.state.workspaces), dict(self.state.windows))
        self.assertFalse(self.state.apply({"WindowClosed": {}}))
        self.assertFalse(self.state.apply({"WindowClosed": {"id": []}}))
        self.assertFalse(self.state.apply({"WindowFocusChanged": {"id": {"bad": 1}}}))
        self.assertFalse(self.state.apply({"WorkspacesChanged": {"workspaces": [{"id": []}]}}))
        self.assertFalse(self.state.apply({"WindowLayoutsChanged": {"changes": [[{}, {}]]}}))
        self.assertEqual((self.state.workspaces, self.state.windows), before)

    def test_duplicate_snapshot_ids_do_not_silently_drop_rows(self):
        self.load_snapshot()
        before = (dict(self.state.workspaces), dict(self.state.windows))
        self.assertFalse(self.state.apply({"WorkspacesChanged": {"workspaces": [
            {"id": 10, "output": "DP-1"}, {"id": 10, "output": "DP-2"},
        ]}}))
        self.assertFalse(self.state.apply({"WindowsChanged": {"windows": [
            window(1), window(1, True),
        ]}}))
        self.assertEqual((self.state.workspaces, self.state.windows), before)


if __name__ == "__main__":
    unittest.main()
