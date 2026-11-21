import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
import sys
import tempfile
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / "session"))  # search.py imports launching
spec = importlib.util.spec_from_file_location("flow_search", Path(__file__).parents[1] / "session/search.py")
search = importlib.util.module_from_spec(spec)
spec.loader.exec_module(search)


class SearchTest(unittest.TestCase):
    def test_local_results_include_available_pins_windows_and_apps(self):
        app = Mock()
        app.get_display_name.return_value = "Browser"
        app.get_id.return_value = "org.example.Browser.desktop"
        app.should_show.return_value = True
        hidden = Mock()
        hidden.get_display_name.return_value = "Hidden"
        hidden.should_show.return_value = False
        data = {"pins": [{"label": "Work", "id": "1", "available": True}, {"label": "Missing", "id": "2", "available": False}],
                "windows": [{"label": "My\nDocument", "id": 4, "app_id": "editor"}]}
        app.get_icon.return_value = None
        rows = search.results(data, [app, hidden])
        self.assertEqual([r[2] for r in rows], ["settings", "settings", "pin", "pin", "window", "app", "layer"])
        # Each shown app can also open as a Layer over the current space; hidden apps cannot.
        self.assertEqual(rows[6], ("Browser", "Open as Layer", "layer", "org.example.Browser.desktop", ""))
        self.assertEqual(rows[4], ("My Document", "Open window · editor", "window", "4", ""))
        self.assertEqual(rows[3], ("Missing", "Pinned space · Open", "pin", "2", ""))

    def test_unavailable_group_does_not_offer_to_reopen(self):
        rows = search.results({"pins": [{"label": "Work", "id": "g", "kind": "group", "available": False}]}, [])
        self.assertEqual(rows[2], ("Work", "Pinned Group · Unavailable", "pin", "g", ""))

    def test_other_display_pins_are_labeled_and_closed_disconnected_pins_hidden(self):
        rows = search.results({"other_pins": [
            {"label": "Terminal", "id": "t", "display_state": "connected", "display_label": "Dell U2723QE", "available": True},
            {"label": "Notes", "id": "n", "display_state": "disconnected", "display_label": "Studio", "available": True},
            {"label": "Gone", "id": "g", "display_state": "disconnected", "display_label": "Studio", "available": False}]}, [])
        self.assertEqual([row for row in rows if row[2] == "pin"],
                         [("Terminal", "Pinned on Dell U2723QE", "pin", "t", ""), ("Notes", "Pinned on Studio", "pin", "n", "")])

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
            self.assertEqual(search.results({}, [], [good])[2][2:4], ("file", good.as_uri()))
            history.write_text("broken")
            self.assertEqual(search.recent_files(history, home), [])

    def test_json_lists_the_results_and_names_apps_by_their_desktop_file(self):
        app = Mock()
        app.get_display_name.return_value = "Browser"
        app.get_id.return_value = "browser.desktop"
        app.get_filename.return_value = "/usr/share/applications/browser.desktop"
        app.should_show.return_value = True
        app.get_icon.return_value.to_string.return_value = "browser"
        gio = SimpleNamespace(AppInfo=SimpleNamespace(get_all=lambda: [app]))
        repository = SimpleNamespace(Gio=gio)
        out = io.StringIO()
        with patch.dict(sys.modules, {"gi": SimpleNamespace(repository=repository), "gi.repository": repository}), \
             patch.object(search, "recent_files", return_value=[]), \
             patch.object(search.subprocess, "check_output", return_value='{"pins":[{"label":"Work","id":"p","available":true}]}'), \
             contextlib.redirect_stdout(out):
            self.assertEqual(search.main(["--json"]), 0)
        rows = json.loads(out.getvalue())
        self.assertEqual([(r["kind"], r["value"]) for r in rows],
                         [("settings", "settings"), ("settings", "quick_open"), ("pin", "p"),
                          ("app", "/usr/share/applications/browser.desktop"), ("layer", "browser.desktop")])
        self.assertEqual(rows[3]["icon"], "browser")

    def test_an_icon_with_fallbacks_is_given_to_the_shell_by_its_name(self):
        app = Mock()
        app.get_display_name.return_value = "Editor"
        app.should_show.return_value = True
        app.get_icon.return_value.to_string.return_value = ". GThemedIcon org.example.Editor org.example.Editor-symbolic"
        self.assertEqual(search.results({}, [app])[2][4], "org.example.Editor")
        app.get_icon.return_value.to_string.return_value = "/opt/editor/icon.png"
        self.assertEqual(search.results({}, [app])[2][4], "/opt/editor/icon.png")

    def test_open_launches_apps_and_files_through_gio_in_an_app_scope(self):
        for kind, value, command in (("app", "/a/browser.desktop", ["gio", "launch", "/a/browser.desktop"]),
                                     ("file", "file:///home/u/notes.txt", ["gio", "open", "file:///home/u/notes.txt"])):
            with self.subTest(kind=kind), patch.dict(search.os.environ, {"FLOW_APP_SCOPES": "1"}), \
                 patch.object(search.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
                self.assertEqual(search.main(["--open", kind, value]), 0)
                self.assertEqual(run.call_args.args[0], ["systemd-run", "--user", "--scope", "--collect", "--quiet", "--", *command])
                # None of the app's streams may be the shell's pipe.
                for stream in ("stdin", "stdout", "stderr"):
                    self.assertIs(run.call_args.kwargs[stream], search.subprocess.DEVNULL)

    def test_open_reports_failure_and_refuses_other_kinds(self):
        with patch.object(search.subprocess, "run", return_value=SimpleNamespace(returncode=4)):
            self.assertEqual(search.main(["--open", "app", "/a/gone.desktop"]), 4)
        with patch.object(search.subprocess, "run", side_effect=FileNotFoundError("gio")), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(search.main(["--open", "file", "file:///x"]), 127)
        with patch.object(search.subprocess, "run") as run, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(search.main(["--open", "pin", "1"]), 2)
            self.assertEqual(search.main([]), 2)   # no fuzzel fallback: the shell is the only caller
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
