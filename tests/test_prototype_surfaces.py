"""The data and configuration behind Flow's own Overview, home indicator and search panel.

- search.py --json: the rows the panel shows, with an app named by its desktop file and its icon.
- Configuration text: Super+O, the hot corner, the grim package, and that Qt's grabToImage (which
  crashed Quickshell 0.3.2 on Niri) is not called.

The surfaces' behaviour (which call opens what, that two never stack, the selection surviving a
refresh, snapshots) is tested on a running shell by tests/niri_prototype_surfaces_smoke.py in the guest.
"""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "session"))
spec = importlib.util.spec_from_file_location("flow_search_json", ROOT / "session/search.py")
search = importlib.util.module_from_spec(spec)
spec.loader.exec_module(search)
SHELL = (ROOT / "session/shell.qml").read_text()
NIRI = (ROOT / "session/niri.kdl").read_text()


def app(name, desktop, icon="org.example.Icon", ident=None):
    return SimpleNamespace(get_display_name=lambda: name, should_show=lambda: True, get_filename=lambda: desktop,
                           get_id=lambda: ident or Path(desktop or "x").name,
                           get_icon=lambda: SimpleNamespace(to_string=lambda: icon) if icon else None)


class SearchJsonTest(unittest.TestCase):
    def rows(self, apps=(), snapshot=None, files=()):
        return json.loads(search.as_json(search.results(snapshot or {}, list(apps), files)))

    def test_rows_carry_title_detail_kind_value_and_icon(self):
        rows = self.rows([app("Files", "/usr/share/applications/org.gnome.Nautilus.desktop", "org.gnome.Nautilus")],
                         {"pins": [{"id": "p1", "label": "Mail", "available": True}],
                          "windows": [{"id": 7, "label": "notes.txt — Kate", "app_id": "org.kde.kate"}]},
                         [Path("/home/u/plan.txt")])
        self.assertEqual(rows[0], {"title": "Flow Settings", "detail": "Settings", "kind": "settings", "value": "settings", "icon": ""})
        self.assertIn({"title": "Mail", "detail": "Pinned space", "kind": "pin", "value": "p1", "icon": ""}, rows)
        self.assertIn({"title": "notes.txt — Kate", "detail": "Open window · org.kde.kate", "kind": "window", "value": "7", "icon": ""}, rows)
        self.assertIn({"title": "plan.txt", "detail": "Recent file · /home/u", "kind": "file", "value": "file:///home/u/plan.txt", "icon": ""}, rows)
        self.assertIn({"title": "Files", "detail": "Application", "kind": "app",
                       "value": "/usr/share/applications/org.gnome.Nautilus.desktop", "icon": "org.gnome.Nautilus"}, rows)
        self.assertIn({"title": "Files", "detail": "Open as Layer", "kind": "layer",
                       "value": "org.gnome.Nautilus.desktop", "icon": "org.gnome.Nautilus"}, rows)

    def test_an_app_without_a_desktop_file_cannot_be_opened_so_is_not_listed(self):
        rows = self.rows([app("Ghost", None, ident="ghost.desktop")])
        self.assertNotIn("app", [r["kind"] for r in rows])


class ConfigurationTest(unittest.TestCase):
    def test_super_o_opens_flows_overview_and_the_hot_corner_is_off(self):
        self.assertIn('Super+O { spawn "@shell@" "ipc" "-p" "@shellConfig@" "call" "desk" "overview"; }', NIRI)
        self.assertIn("gestures { hot-corners { off; }; }", NIRI)
        self.assertNotIn("toggle-overview", NIRI)

    def test_snapshots_come_from_grim_never_from_qt(self):
        self.assertNotIn(".grabToImage(", SHELL)
        packages = (ROOT / "modules/flow.nix").read_text().split("environment.systemPackages")[1].split("]")[0]
        self.assertIn("grim", packages.split())
        self.assertNotIn('"/tmp"', SHELL[SHELL.index("readonly property string snapshotDir"):][:200])


if __name__ == "__main__":
    unittest.main()
