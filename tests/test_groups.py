import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "session"))
import groups
import restore
import spaces


def window(ident, app, column, row=1):
    return {"id": ident, "app_id": app, "title": app, "workspace_id": 1,
            "is_floating": False, "layout": {"pos_in_scrolling_layout": [column, row]}}


class GroupRegistryTest(unittest.TestCase):
    def test_optional_layout_fields_preserve_legacy_and_unknown_fields(self):
        legacy = {"id": "legacy", "label": "Old", "members": ["a", "b"], "extra": 4}
        laid_out = {"id": "new", "label": "New", "members": ["c", "d"],
                    "layout": "side-by-side", "pending_layout": "stack", "extra": 5}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "groups.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            groups.write(path, [legacy, laid_out])
            self.assertEqual(groups.load(Path(directory)), [legacy, laid_out])
            self.assertNotIn("layout", groups.load(Path(directory))[0])

    def test_invalid_optional_layout_fields_leave_registry_unchanged(self):
        for field in ("layout", "pending_layout"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                state = Path(directory)
                path = state / "groups.json"
                path.write_text(json.dumps({"version": 1, "groups": [
                    {"id": "g", "label": "Work", "members": ["a", "b"], field: "diagonal"}]}))
                before = path.read_bytes()
                with self.assertRaisesRegex(ValueError, "Invalid saved Groups"):
                    groups.load(state)
                self.assertEqual(path.read_bytes(), before)

    def test_invalid_optional_layout_field_prevents_snapshot_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            path = state / "session.json"
            path.write_text(json.dumps({"version": 1, "windows": [], "groups": [
                {"id": "g", "label": "Work", "members": ["a", "b"], "pending_layout": None}]}))
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "Invalid saved Groups"):
                groups.load(state)
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse((state / "groups.json").exists())

    def test_pending_layout_must_differ_from_current_layout(self):
        for group in ({"pending_layout": "stack"}, {"layout": "side-by-side", "pending_layout": "side-by-side"}):
            with self.subTest(group=group), self.assertRaisesRegex(ValueError, "Invalid saved Groups"):
                groups.validate({"version": 1, "groups": [{"id": "g", "label": "Work", "members": ["a", "b"], **group}]})

    def test_migration_preserves_snapshot_pins_and_group_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            legacy = {"version": 1, "windows": [], "groups": [{"id": "g", "label": "Writing", "members": ["a", "b"], "extra": 4}]}
            snapshot = state / "session.json"
            pins = state / "spaces.json"
            snapshot.write_text(json.dumps(legacy))
            pins.write_text('[{"id":"pin","group_id":"g","app_id":"a","members":["a","b"]}]')
            before = snapshot.read_bytes(), pins.read_bytes()
            self.assertEqual(groups.load(state), legacy["groups"])
            self.assertEqual((snapshot.read_bytes(), pins.read_bytes()), before)
            self.assertEqual(groups.load(state), legacy["groups"])
            self.assertEqual((snapshot.read_bytes(), pins.read_bytes()), before)

    def test_invalid_legacy_snapshot_is_not_replaced_or_migrated(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            path = state / "session.json"
            path.write_text('{"version":1,"groups":[{"id":"g"}]}')
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                groups.load(state)
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse((state / "groups.json").exists())

    def test_ambiguous_legacy_groups_are_not_migrated(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            data = {"version": 1, "windows": [], "groups": [
                {"id": "one", "label": "One", "members": ["a", "b"]},
                {"id": "two", "label": "Two", "members": ["b", "c"]}]}
            snapshot = state / "session.json"
            snapshot.write_text(json.dumps(data))
            before = snapshot.read_bytes()
            with self.assertRaisesRegex(ValueError, "Ambiguous"):
                groups.load(state)
            self.assertEqual(snapshot.read_bytes(), before)
            self.assertFalse((state / "groups.json").exists())

    def test_missing_registry_after_migration_never_resurrects_old_groups(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "session.json").write_text('{"version":1,"windows":[],"groups":[{"id":"g","label":"Work","members":["a","b"]}]}')
            self.assertEqual(groups.load(state)[0]["id"], "g")
            groups.write(state / "groups.json", [])
            (state / "groups.json").unlink()
            with self.assertRaisesRegex(ValueError, "registry is missing"):
                groups.load(state)
            self.assertFalse((state / "groups.json").exists())

    def test_create_rename_dissolve_without_replacing_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "flow"
            state.mkdir()
            snapshot = state / "session.json"
            snapshot.write_text('{"version":1,"windows":[],"groups":[]}')
            original = snapshot.read_bytes()
            ws = {"id": 1, "active_window_id": 1}
            windows = [window(1, "a", 1), window(2, "b", 1, 2)]
            def action(kind, name, *args):
                self.assertEqual((kind, name, args), ("action", "consume-or-expel-window-right", ("--id", 1)))
                windows[0]["layout"]["pos_in_scrolling_layout"] = [2, 1]
                windows[1]["layout"]["pos_in_scrolling_layout"] = [1, 1]
            def invoke(op, *args):
                with patch.dict(os.environ, {"XDG_STATE_HOME": directory}), patch.object(sys, "argv", ["flow-spaces", op, *args]), \
                     patch.object(spaces, "snapshot", side_effect=lambda: (ws, windows)), \
                     patch.object(spaces, "session_id", return_value="session"), patch.object(spaces, "command", side_effect=action), \
                     contextlib.redirect_stdout(io.StringIO()) as output:
                    spaces.main()
                return json.loads(output.getvalue())
            created = invoke("create-group")
            ident = created["spaces"][0]["group_id"]
            self.assertTrue(ident)
            self.assertEqual(snapshot.read_bytes(), original)
            self.assertEqual(groups.load(state)[0]["id"], ident)
            with patch.dict(os.environ, {"XDG_STATE_HOME": directory}), \
                 patch.object(sys, "argv", ["flow-recovery", "rename-group", ident, "Writing"]), \
                 contextlib.redirect_stdout(io.StringIO()):
                restore.main()
            self.assertEqual(invoke("refresh")["spaces"][0]["label"], "Writing")
            pin = invoke("pin-group", ident)["pins"][0]
            self.assertEqual((pin["label"], pin["group_id"]), ("Writing", ident))
            invoke("unpin", pin["id"])
            separated = invoke("dissolve-group", ident)
            self.assertEqual(len(separated["spaces"]), 2)
            self.assertEqual(groups.load(state), [])
            self.assertEqual(snapshot.read_bytes(), original)

    def test_restore_does_not_recreate_dissolved_group(self):
        pair = [window(1, "a", 1), window(2, "b", 2)]
        ws = {"id": 1, "active_window_id": 1}
        data = {"version": 1, "groups": [{"id": "g", "label": "Work", "members": ["a", "b"]}],
                "windows": [{"app_id": "a", "column": 1}, {"app_id": "b", "column": 1}]}
        actions = []
        report, count = restore.restore(data, ws, pair, [], lambda _: None, lambda: (ws, pair),
                                        lambda *a: actions.append(a), registered=[])
        self.assertEqual(count, 2)
        self.assertTrue(any("dissolved" in item for item in report))
        self.assertFalse(any("consume" in str(action) for action in actions))

    def test_group_actions_reject_stale_focus_and_pinned_members(self):
        ws = {"id": 1, "active_window_id": 1}
        pair = [window(1, "a", 1), window(2, "b", 1, 2)]
        with patch.object(spaces, "snapshot", return_value=({**ws, "active_window_id": 2}, pair)), \
             patch.object(spaces, "command") as command, self.assertRaisesRegex(ValueError, "focus changed"):
            spaces.run([], "create-group", None, ws, pair, "session", [])
        command.assert_not_called()
        pin = {"id": "p", "app_id": "a", "window_id": 1}
        with patch.object(spaces, "command") as command, self.assertRaisesRegex(ValueError, "Unpin"):
            spaces.run([pin], "create-group", None, ws, pair, "session", [])
        command.assert_not_called()

    def test_failed_separation_keeps_group_identity(self):
        ws = {"id": 1, "active_window_id": 1}
        pair = [window(1, "a", 1), window(2, "b", 1, 2)]
        registered = [{"id": "g", "label": "Work", "members": ["a", "b"]}]
        with patch.object(spaces, "snapshot", return_value=(ws, pair)), patch.object(spaces, "command"), \
             self.assertRaisesRegex(ValueError, "Group remains saved"):
            spaces.run([], "dissolve-group", "g", ws, pair, "session", registered)
        self.assertEqual([g["id"] for g in registered], ["g"])

    def test_named_group_is_not_removed_by_generic_unstack(self):
        ws = {"id": 1, "active_window_id": 1}
        pair = [window(1, "a", 1), window(2, "b", 1, 2)]
        registered = [{"id": "g", "label": "Work", "members": ["a", "b"]}]
        with patch.object(spaces, "command") as command, self.assertRaisesRegex(ValueError, "Dissolve Group"):
            spaces.run([], "unstack", None, ws, pair, "session", registered)
        command.assert_not_called()

    def test_dissolve_does_not_shift_an_unrelated_pin_to_the_right(self):
        ws = {"id": 1, "active_window_id": 1}
        windows = [window(1, "a", 1), window(2, "b", 1, 2), window(3, "pinned", 2)]
        registered = [{"id": "g", "label": "Work", "members": ["a", "b"]}]
        pins = [{"id": "p", "label": "Pinned", "app_id": "pinned", "window_id": 3}]
        with patch.object(spaces, "snapshot", return_value=(ws, windows)), patch.object(spaces, "command") as command, \
             self.assertRaisesRegex(ValueError, "pinned spaces left"):
            spaces.run(pins, "dissolve-group", "g", ws, windows, "session", registered)
        command.assert_not_called()
        self.assertEqual(registered[0]["id"], "g")

    def test_dissolve_allows_a_pin_to_the_left(self):
        ws = {"id": 1, "active_window_id": 1}
        windows = [window(3, "pinned", 1), window(1, "a", 2), window(2, "b", 2, 2)]
        registered = [{"id": "g", "label": "Work", "members": ["a", "b"]}]
        pins = [{"id": "p", "label": "Pinned", "app_id": "pinned", "window_id": 3}]
        def separate(*args):
            windows[1]["layout"]["pos_in_scrolling_layout"] = [3, 1]
            windows[2]["layout"]["pos_in_scrolling_layout"] = [2, 1]
        with patch.object(spaces, "snapshot", return_value=(ws, windows)), patch.object(spaces, "command", side_effect=separate) as command:
            spaces.run(pins, "dissolve-group", "g", ws, windows, "session", registered)
        command.assert_called_once_with("action", "consume-or-expel-window-right", "--id", 1)
        self.assertEqual(windows[0]["layout"]["pos_in_scrolling_layout"], [1, 1])
        self.assertEqual(registered, [])

    def test_save_uses_registry_without_inventing_new_groups(self):
        ws = {"id": 1, "active_window_id": 1}
        pair = [window(1, "a", 1), window(2, "b", 1, 2)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            restore.save(path, ws, pair, registered=[])
            self.assertEqual(json.loads(path.read_text())["groups"], [])
            registry = [{"id": "g", "label": "Writing", "members": ["a", "b"]}]
            restore.save(path, ws, pair, registered=registry)
            self.assertEqual(json.loads(path.read_text())["groups"], registry)
            self.assertEqual(registry[0]["id"], "g")

    def test_save_preserves_snapshot_when_a_registered_group_is_separated(self):
        ws = {"id": 1, "active_window_id": 1}
        pair = [window(1, "a", 1), window(2, "b", 2)]
        registry = [{"id": "g", "label": "Writing", "members": ["a", "b"]}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            path.write_text('{"version":1,"windows":[],"groups":[]}')
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "separated Group"):
                restore.save(path, ws, pair, registered=registry)
            self.assertEqual(path.read_bytes(), before)

    def test_failed_registry_write_retains_old_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "groups.json"
            old = [{"id": "g", "label": "Writing", "members": ["a", "b"]}]
            groups.write(path, old)
            before = path.read_bytes()
            with patch.object(groups.os, "replace", side_effect=OSError("disk full")), self.assertRaises(OSError):
                groups.write(path, [])
            self.assertEqual(path.read_bytes(), before)

    def test_restore_releases_locks_before_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "flow"
            state.mkdir()
            (state / "session.json").write_text('{"version":1,"windows":[{"app_id":"a","column":1}],"groups":[]}')
            ws = {"id": 1, "active_window_id": 1}
            windows = []
            def launch(*args, **kwargs):
                for name in ("session.lock", "spaces.lock"):
                    with (state / name).open("a") as lock:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                windows.append(window(1, "a", 1))
            with patch.dict(os.environ, {"XDG_STATE_HOME": directory}), patch.object(sys, "argv", ["flow-recovery", "restore"]), \
                 patch.object(spaces, "snapshot", side_effect=lambda: (ws, windows)), \
                 patch.object(spaces, "desktop_file", return_value=Path("/tmp/a.desktop")), \
                 patch.object(spaces, "session_id", return_value="session"), patch.object(spaces, "command"), \
                 patch.object(restore.subprocess, "run", side_effect=launch), patch.object(restore.time, "sleep"), \
                 contextlib.redirect_stdout(io.StringIO()):
                restore.main()
            self.assertEqual(len(windows), 1)

    def test_restore_stops_when_snapshot_changes_during_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "flow"
            state.mkdir()
            path = state / "session.json"
            path.write_text('{"version":1,"windows":[{"app_id":"a","column":1}],"groups":[]}')
            ws = {"id": 1, "active_window_id": 1}
            windows = []
            def launch(*args, **kwargs):
                path.write_text('{"version":1,"windows":[],"groups":[]}')
                windows.append(window(1, "a", 1))
            with patch.dict(os.environ, {"XDG_STATE_HOME": directory}), patch.object(sys, "argv", ["flow-recovery", "restore"]), \
                 patch.object(spaces, "snapshot", side_effect=lambda: (ws, windows)), \
                 patch.object(spaces, "desktop_file", return_value=Path("/tmp/a.desktop")), \
                 patch.object(spaces, "session_id", return_value="session"), patch.object(spaces, "command") as command, \
                 patch.object(restore.subprocess, "run", side_effect=launch), contextlib.redirect_stdout(io.StringIO()), \
                 self.assertRaisesRegex(ValueError, "session recovery stopped"):
                restore.main()
            command.assert_not_called()

    def test_failed_dissolve_registry_write_keeps_saved_group(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "flow"
            state.mkdir()
            original = [{"id": "g", "label": "Work", "members": ["a", "b"]}]
            groups.write(state / "groups.json", original)
            groups.load(state)
            ws = {"id": 1, "active_window_id": 1}
            pair = [window(1, "a", 1), window(2, "b", 1, 2)]
            def separate(*args):
                pair[0]["layout"]["pos_in_scrolling_layout"] = [2, 1]
                pair[1]["layout"]["pos_in_scrolling_layout"] = [1, 1]
            with patch.dict(os.environ, {"XDG_STATE_HOME": directory}), \
                 patch.object(sys, "argv", ["flow-spaces", "dissolve-group", "g"]), \
                 patch.object(spaces, "snapshot", side_effect=lambda: (ws, pair)), \
                 patch.object(spaces, "session_id", return_value="session"), patch.object(spaces, "command", side_effect=separate), \
                 patch.object(groups.os, "replace", side_effect=OSError("disk full")), \
                 self.assertRaisesRegex(ValueError, "stack its apps and retry"):
                spaces.main()
            self.assertEqual(groups.load(state), original)


if __name__ == "__main__":
    unittest.main()
