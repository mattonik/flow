import contextlib
import importlib.util
import io
import json
import os
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
        self.assertEqual([r[2] for r in rows], ["settings", "settings", "pin", "pin", "window", "app",
                                                *["action"] * 5, *["command"] * 5, "layer"])
        # Each shown app can also open as a Layer over the current space; hidden apps cannot.
        self.assertEqual(rows[-1], ("Browser", "Open as Layer", "layer", "org.example.Browser.desktop", ""))
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

    def test_user_folders_are_the_xdg_ones_wherever_they_are(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            home, config, elsewhere = base / "home", base / "config", base / "elsewhere"
            for folder in (home / "Documents", home / "Pictures", home / "Work/Papers", home / "Music",
                           config, elsewhere):
                folder.mkdir(parents=True)
            (home / "Desktop").write_text("not a folder")
            (home / "Videos").symlink_to(elsewhere)  # a user folder kept on another disk is walked there
            self.assertEqual(search.user_folders(home, config),
                             [(home / name).resolve() for name in ("Documents", "Pictures", "Music")] + [elsewhere.resolve()])
            (config / "user-dirs.dirs").write_text(
                '# xdg-user-dirs\n'
                'XDG_DESKTOP_DIR="$HOME"\n'                       # turned off
                'XDG_DOWNLOAD_DIR="/"\n'                          # holds the home folder: never walked
                'XDG_DOCUMENTS_DIR="$HOME/Work/Papers"\n'
                'XDG_PICTURES_DIR="$HOME/Work"\n'                 # holds Documents, which it then replaces
                f'XDG_MUSIC_DIR="{elsewhere}"\n'                  # the same folder as Videos: walked once
                'XDG_TEMPLATES_DIR="$HOME/Templates"\n')          # not a folder search walks
            self.assertEqual(search.user_folders(home, config), [(home / "Work").resolve(), elsewhere.resolve()])

    def test_find_files_matches_names_level_by_level_with_hidden_files_and_no_links(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            docs, outside = base / "Documents", base / "outside"
            (docs / "deep/deeper").mkdir(parents=True)
            (docs / ".notes").mkdir()
            (docs / "mounted").mkdir()
            outside.mkdir()
            for path in (docs / "deep/deeper/Report draft.txt", docs / "report final.odt", docs / ".notes/report.md",
                         docs / "annual report.pdf", docs / "Budget report.ods", docs / "summary.txt", docs / "mounted/report on a share.txt",
                         outside / "report secret.txt", docs / "report locked.txt"):
                path.write_text("x")
            (docs / "report link.txt").symlink_to(outside / "report secret.txt")
            (docs / "linked folder").symlink_to(outside)
            if os.geteuid() != 0:  # root reads anything
                (docs / "report locked.txt").chmod(0)
            mounts = frozenset({str(docs / "mounted")})
            found = search.find_files("REPORT", [docs], mounts=mounts)
            readable = [docs / "report locked.txt"] if os.geteuid() == 0 else []
            self.assertEqual(found, [docs / "annual report.pdf", docs / "Budget report.ods", docs / "report final.odt", *readable,
                                     docs / ".notes/report.md", docs / "deep/deeper/Report draft.txt"])
            self.assertIn(docs / "mounted/report on a share.txt", search.find_files("report", [docs]))
            self.assertEqual(search.find_files("draft report", [docs]), [docs / "deep/deeper/Report draft.txt"])
            self.assertEqual(search.find_files("deep", [docs]), [docs / "deep", docs / "deep/deeper"])
            self.assertEqual(search.find_files("re", [docs]), [])  # too short to walk for
            rows = json.loads(search.file_rows([docs / "deep", docs / "summary.txt"]))
            self.assertEqual([(r["title"], r["detail"], r["kind"]) for r in rows],
                             [("deep", f"Folder · {docs}", "file"), ("summary.txt", f"File · {docs}", "file")])
            self.assertEqual(rows[1]["value"], (docs / "summary.txt").as_uri())

    def test_find_files_stops_at_its_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            docs = Path(directory)
            for index in range(5):
                (docs / f"note {index}.txt").write_text("x")
            nested = docs / "a/b/c"
            nested.mkdir(parents=True)
            (nested / "note deep.txt").write_text("x")
            bounds = dict(search.WALK)
            self.assertEqual(len(search.find_files("note", [docs], dict(bounds, results=3))), 3)
            self.assertNotIn(nested / "note deep.txt", search.find_files("note", [docs], dict(bounds, depth=3)))
            self.assertIn(nested / "note deep.txt", search.find_files("note", [docs], dict(bounds, depth=4)))
            ticks = iter(range(100))
            self.assertEqual(search.find_files("note", [docs], dict(bounds, seconds=2.5), clock=lambda: next(ticks)),
                             [docs / "note 0.txt"])

    def test_find_files_checks_the_clock_while_it_reads_a_folder(self):
        pulled = []

        class Huge:
            def __enter__(self):
                def entries():
                    for index in range(100000):
                        pulled.append(index)
                        yield SimpleNamespace(name=f"x{index}", path=f"/huge/x{index}", is_dir=lambda **_: False,
                                              is_file=lambda **_: True)
                return entries()

            def __exit__(self, *_):
                return False

        times = iter([0] + [10] * 100)  # the walk starts at 0 s; every later look at the clock says 10 s
        with patch.object(search.os, "scandir", return_value=Huge()):
            self.assertEqual(search.find_files("report", [Path("/huge")], clock=lambda: next(times)), [])
        self.assertEqual(len(pulled), 256)

    def test_files_mode_walks_the_users_folders(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "Documents").mkdir()
            (home / "Documents/Quarterly report.txt").write_text("x")
            (home / "report outside the user folders.txt").write_text("x")
            out = io.StringIO()
            with patch.dict("os.environ", {"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config")}), \
                    patch.object(search.sys, "stdin", io.StringIO("report\n")), contextlib.redirect_stdout(out):
                self.assertEqual(search.main(["--files"]), 0)
            self.assertEqual([r["title"] for r in json.loads(out.getvalue())], ["Quarterly report.txt"])

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
                          ("app", "/usr/share/applications/browser.desktop"),
                          *[(kind, value) for _, _, kind, value in search.COMMANDS], ("layer", "browser.desktop")])
        self.assertEqual(rows[3]["icon"], "browser")
        # Ending the session or stopping the computer asks first; nothing else does.
        self.assertEqual({r["value"] for r in rows if r.get("confirm")}, {"logout", "restart", "poweroff"})

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

    def test_open_runs_system_commands_outside_any_app_scope(self):
        expected = {"lock": ["loginctl", "lock-session"], "suspend": ["systemctl", "suspend"],
                    "logout": ["niri", "msg", "action", "quit", "--skip-confirmation"],
                    "restart": ["systemctl", "reboot"], "poweroff": ["systemctl", "poweroff"]}
        self.assertEqual({value for _, _, kind, value in search.COMMANDS if kind == "command"}, set(expected))
        for value, command in expected.items():
            with self.subTest(command=value), patch.dict(search.os.environ, {"FLOW_APP_SCOPES": "1"}), \
                 patch.object(search.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
                self.assertEqual(search.main(["--open", "command", value]), 0)
                self.assertEqual(run.call_args.args[0], command)
                for stream in ("stdin", "stdout", "stderr"):
                    self.assertIs(run.call_args.kwargs[stream], search.subprocess.DEVNULL)
        with patch.object(search.subprocess, "run") as run, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(search.main(["--open", "command", "rm -rf"]), 2)
            run.assert_not_called()

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
