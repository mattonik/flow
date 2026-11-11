import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

sys.modules.setdefault("pyatspi", types.ModuleType("pyatspi"))
spec = importlib.util.spec_from_file_location("appmenu", Path(__file__).parents[1] / "session/appmenu.py")
appmenu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(appmenu)


class AppMenuTest(unittest.TestCase):
    @patch.object(appmenu, "niri")
    def test_identity_does_not_need_accessibility_menu(self, niri):
        niri.side_effect = [[{"is_focused": True, "active_window_id": 7}],
                            [{"id": 7, "app_id": "editor", "pid": 10}]]
        self.assertEqual(appmenu.active_window(identity_only=True), {"id": 7, "name": "editor", "menus": []})

    def test_only_matching_enabled_command_is_allowed(self):
        menu = [{"label": "File", "enabled": True, "children": [
            {"label": "New", "path": [0, 1], "enabled": True},
            {"label": "Save", "path": [0, 2], "enabled": False},
        ]}]
        self.assertTrue(appmenu.find_action(menu, [0, 1], "New"))
        self.assertFalse(appmenu.find_action(menu, [0, 1], "Save"))
        self.assertFalse(appmenu.find_action(menu, [0, 2], "Save"))
        menu[0]["enabled"] = False
        self.assertFalse(appmenu.find_action(menu, [0, 1], "New"))


if __name__ == "__main__":
    unittest.main()
