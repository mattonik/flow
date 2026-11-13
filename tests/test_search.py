import importlib.util
from pathlib import Path
import unittest
import sys
import tempfile
import xml.etree.ElementTree as ET
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



    def test_recent_files_are_local_readable_and_within_the_users_home(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            home = base / "home"
            home.mkdir()
            good = home / "My notes.txt"
            good.write_text("notes")
            outside = base / "private.txt"
            outside.write_text("private")
            link = home / "link.txt"
            link.symlink_to(outside)
            root = ET.Element("xbel")
            for uri, stamp in [(good.as_uri(), "2026-01-02"), (good.as_uri(), "2026-01-01"),
                               (outside.as_uri(), "2026-01-03"), (link.as_uri(), "2026-01-04"),
                               ("https://example.com/file", "2026-01-05"),
                               ("file://[invalid", "2026-01-07"),
                               ((home / "missing").as_uri(), "2026-01-06")]:
                ET.SubElement(root, "bookmark", href=uri, modified=stamp)
            history = base / "recent.xbel"
            ET.ElementTree(root).write(history)
            self.assertEqual(search.recent_files(history, home), [good.resolve()])
            self.assertEqual(search.entries({}, [], [good])[2][1], ("file", good.as_uri()))
            history.write_text("broken")
            self.assertEqual(search.recent_files(history, home), [])

    def test_recent_file_result_opens_its_uri(self):
        gio = SimpleNamespace(AppInfo=SimpleNamespace(get_all=lambda: [], launch_default_for_uri=Mock()))
        repository = SimpleNamespace(Gio=gio, GLib=SimpleNamespace(Error=RuntimeError))
        with patch.dict(sys.modules, {"gi": SimpleNamespace(repository=repository), "gi.repository": repository}), \
             patch.object(search, "recent_files", return_value=[Path("/home/martin/notes.txt")]), \
             patch.object(search.subprocess, "check_output", return_value="{}"), \
             patch.object(search.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="2")):
            search.main()
        gio.AppInfo.launch_default_for_uri.assert_called_once_with("file:///home/martin/notes.txt", None)

    def test_settings_results_invoke_matching_shell_action(self):
        gio = SimpleNamespace(AppInfo=SimpleNamespace(get_all=lambda: []))
        repository = SimpleNamespace(Gio=gio, GLib=SimpleNamespace(Error=RuntimeError))
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
