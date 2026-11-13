import importlib.util
from pathlib import Path
import unittest
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location("flow_search", Path(__file__).parents[1] / "session/search.py")
search = importlib.util.module_from_spec(spec)
spec.loader.exec_module(search)


class SearchTest(unittest.TestCase):
    def test_local_results_include_available_pins_windows_and_apps(self):
        app = Mock()
        app.get_display_name.return_value = "Browser"
        app.should_show.return_value = True
        hidden = Mock()
        hidden.get_display_name.return_value = "Hidden"
        hidden.should_show.return_value = False
        data = {"pins": [{"label": "Work", "id": "1", "available": True}, {"label": "Missing", "id": "2", "available": False}],
                "windows": [{"label": "My\nDocument", "id": 4, "app_id": "editor"}]}
        rows = search.entries(data, [app, hidden])
        self.assertEqual([r[1][0] for r in rows], ["settings", "settings", "pin", "window", "app"])
        self.assertEqual(rows[3][0], "My Document · Open window · editor")
        self.assertEqual(rows[3][1], ("window", "4"))


    def test_settings_results_invoke_matching_shell_action(self):
        gio = SimpleNamespace(AppInfo=SimpleNamespace(get_all=lambda: []))
        repository = SimpleNamespace(Gio=gio)
        for index, action in enumerate(("settings", "quick_open")):
            with self.subTest(action=action), \
                 patch.dict(sys.modules, {"gi": SimpleNamespace(repository=repository), "gi.repository": repository}), \
                 patch.object(search.subprocess, "check_output", return_value="{}"), \
                 patch.object(search.subprocess, "run", side_effect=[SimpleNamespace(returncode=0, stdout=str(index)), None]) as run:
                search.main()
                self.assertEqual(run.call_args.args[0], ["quickshell", "ipc", "-p", "/etc/flow/shell.qml", "call", "desk", action])
                self.assertTrue(run.call_args.kwargs["check"])


if __name__ == "__main__":
    unittest.main()
