"""Apps Flow opens get their own systemd scope when the installed shell asks for it (FLOW_APP_SCOPES=1).

Without it (a development shell, tests) the commands are unchanged; the other test files rely on that.
These tests go through the real call sites: opening a missing Group member. Search's --open is covered in test_search.py.
"""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "session"))
import launching

spec = importlib.util.spec_from_file_location("spaces_for_launching", Path(__file__).parents[1] / "session/spaces.py")
spaces = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spaces)

SCOPE = ["systemd-run", "--user", "--scope", "--collect", "--quiet", "--"]


def window(ident, app, column):
    return {"id": ident, "app_id": app, "title": app, "workspace_id": 1,
            "is_floating": False, "layout": {"pos_in_scrolling_layout": [column, 1]}}


class LaunchingTest(unittest.TestCase):
    def test_only_the_installed_shell_asks_for_scopes(self):
        with patch.dict(launching.os.environ, {"FLOW_APP_SCOPES": "1"}):
            self.assertEqual(launching.app_command(["gio", "launch", "a.desktop"]), [*SCOPE, "gio", "launch", "a.desktop"])
        for value in ("", "0", "yes"):
            with patch.dict(launching.os.environ, {"FLOW_APP_SCOPES": value}):
                self.assertEqual(launching.app_command(["gio", "launch", "a.desktop"]), ["gio", "launch", "a.desktop"])

    def open_missing_member(self, env):
        group = {"id": "g", "label": "Work", "members": ["terminal", "files"]}
        ws = {"id": 1, "active_window_id": 1}
        windows = [window(1, "terminal", 1)]
        with tempfile.TemporaryDirectory() as directory:
            apps = Path(directory) / "applications"
            apps.mkdir()
            desktop = apps / "files.desktop"
            desktop.touch()
            with patch.dict(spaces.os.environ, {"XDG_DATA_HOME": directory, "XDG_DATA_DIRS": directory, **env}), \
                 patch.object(spaces, "command"), patch.object(spaces.time, "sleep"), \
                 patch.object(spaces.subprocess, "run") as run, \
                 patch.object(spaces, "snapshot", side_effect=[(ws, windows), (ws, windows + [window(2, "files", 2)])]):
                spaces.run([], "open-group-member", "g", ws, windows, "session", [group])
            return run.call_args.args[0], str(desktop)

    def test_a_group_member_opened_from_the_installed_shell_gets_its_own_scope(self):
        args, desktop = self.open_missing_member({"FLOW_APP_SCOPES": "1"})
        self.assertEqual(args, [*SCOPE, "gio", "launch", desktop])
        args, desktop = self.open_missing_member({"FLOW_APP_SCOPES": ""})
        self.assertEqual(args, ["gio", "launch", desktop])


if __name__ == "__main__":
    unittest.main()
