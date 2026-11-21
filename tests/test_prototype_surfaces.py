"""Flow's own Overview, home indicator and search panel (the prototype's), and the data behind them.

- search.py --json: the rows the panel shows, with an app named by its desktop file and its icon.
- The shell's wiring, checked in its source (no compositor here): which keys, IPC calls and buttons
  open what, that snapshots are taken only of the plain work view, and that the chosen row and card
  are marked by more than tint. The surfaces themselves were checked in the guest (see the status doc).
"""
import importlib.util
import json
from pathlib import Path
import re
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


def function(name):
    start = SHELL.index(f"function {name}(")
    depth = 0
    for i in range(SHELL.index("{", start), len(SHELL)):
        depth += {"{": 1, "}": -1}.get(SHELL[i], 0)
        if depth == 0:
            return SHELL[start:i + 1]
    raise AssertionError(name)


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

    def test_fuzzel_rows_are_unchanged(self):
        rows = search.entries({}, [app("Files", "/a/files.desktop")])
        self.assertEqual(rows[2], ("Files · Application", ("app", rows[2][1][1])))


class ShellWiringTest(unittest.TestCase):
    def test_super_o_and_the_home_indicator_open_flows_overview_then_desk(self):
        self.assertIn('Super+O { spawn "@shell@" "ipc" "-p" "@shellConfig@" "call" "desk" "overview"; }', NIRI)
        self.assertIn("function overview(): void { root.toggleSpacesOverview() }", SHELL)
        toggle = function("toggleSpacesOverview")
        self.assertRegex(toggle, r"if \(spacesOverviewOpen\) revealDeskFromOverview\(\)")
        self.assertRegex(toggle, r"else openSpacesOverview\(\)")
        indicator = SHELL[SHELL.index("component HomeIndicator: MouseArea {"):]
        self.assertIn("onClicked: if (!dragged) root.toggleSpacesOverview()", indicator[:1500])
        self.assertEqual(SHELL.count("HomeIndicator {"), 3)  # work view, Desk and Overview

    def test_the_old_bottom_buttons_are_gone(self):
        self.assertNotIn("navigationControls", SHELL)

    def test_snapshots_only_show_the_plain_work_view(self):
        snapshot = function("snapshotActiveSpace")
        self.assertIn("if (!space || !workViewOnly || !screen || snapshotProcess.running)", snapshot)
        self.assertIn('grim -o "$2" -s "$3" -t jpeg -q 72 "$1/$4.tmp" && mv -f "$1/$4.tmp" "$1/$4.jpg"', snapshot)
        self.assertNotIn(".grabToImage(", SHELL)  # crashed Quickshell 0.3.2 on Niri
        self.assertIn("grim", (ROOT / "modules/flow.nix").read_text().split("environment.systemPackages")[1].split("]")[0])
        opening = function("openSpacesOverview")
        self.assertLess(opening.index("const plain = workViewOnly"), opening.index("deskOpen = false"))
        self.assertIn("if (plain) snapshotActiveSpace(show)", opening)
        for surface in ("deskOpen", "switcherOpen", "spacesOverviewOpen", "overviewOpen", "launcherActive", "searchOpen",
                        "appMenuOpen", "quickSettingsOpen", "notificationsOpen"):
            self.assertIn(f"!{surface}", SHELL[SHELL.index("readonly property bool workViewOnly"):][:300], surface)

    def test_search_and_apps_open_the_panel(self):
        self.assertIn("openSearchPanel(false)", function("openSearch"))
        self.assertIn("openSearchPanel(true)", function("openApps"))
        self.assertIn('command: ["flow-search", "--json"]', SHELL)
        run = function("runSearchResult")
        for kind, call in (("app", 'root.launchApp(["gio", "launch", result.value])'), ("file", 'root.launchApp(["gio", "open", result.value])'),
                           ("pin", 'spaceAction("focus", result.value)'), ("window", 'spaceAction("focus-window", result.value)'),
                           ("layer", 'spaceAction("open-layer", result.value)')):
            self.assertRegex(run, rf'result\.kind === "{kind}"\) {re.escape(call)}', kind)

    def test_choices_are_marked_by_more_than_tint(self):
        overview = SHELL[SHELL.index("id: spacesOverview"):SHELL.index("id: searchPanel")]
        self.assertIn("visible: spaceCard.selected", overview)        # the 2 px ring
        self.assertIn("border.width: 2; border.color: floeTheme.focus", overview)
        panel = SHELL[SHELL.index("id: searchPanel"):]
        self.assertIn("border.width: chosen ? 2 : 0; border.color: floeTheme.focus", panel)


if __name__ == "__main__":
    unittest.main()
