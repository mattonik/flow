"""Resting chrome by device (ADR-0014): pins on the shell's source, and the parts that run outside it.

The behaviour (the strip's rows, the rest before the Island opens, Super+B and the click that closes it, the clock pill
on one or two displays, the moving indicator, the home line with a touchscreen, the banner's clicks, the band) is
tested by tests/niri_device_island_smoke.py in the guest, and each behaviour neutralised there fails it. CI has no
QML runner: these only pin the values and conditions in the source, so that one changed by accident shows in review.
A pin is a substring: it does not prove the behaviour, and a reflowed line breaks it. What runs here for real is the
band arithmetic (Asahi's 16:10 crop) and the controller's physical mode (``spaces.physical_mode``, and its wiring in
tests/test_move_display.py).
"""
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "session"))
import spaces  # noqa: E402

ROOT = Path(__file__).parents[1]
SHELL = (ROOT / "session/shell.qml").read_text()
NIRI = (ROOT / "session/niri.kdl").read_text()
# QML without its // comments, so a commented-out line never satisfies a check.
CODE = re.sub(r"(?m)(^|(?<=\s))//.*$", "", SHELL)


def window(ident):
    """The text of the PanelWindow whose id is ``ident``, by brace depth."""
    start = CODE.rindex("PanelWindow {", 0, CODE.index(f"id: {ident}\n"))
    depth, index = 0, start
    while True:
        depth += {"{": 1, "}": -1}.get(CODE[index], 0)
        index += 1
        if depth == 0 and CODE[index - 1] == "}":
            return CODE[start:index]


def function(name):
    start = CODE.index(f"function {name}(")
    depth, index = 0, CODE.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(CODE[index], 0)
        index += 1
        if depth == 0:
            return CODE[start:index]


class PlainScreenTest(unittest.TestCase):
    def test_the_edge_strip_is_one_row_that_opens_after_the_rest_or_at_a_press(self):
        strip = window("edgeStrip")
        self.assertIn("implicitHeight: 1\n", strip)
        self.assertIn("anchors { top: true; left: true; right: true }", strip)
        self.assertIn('WlrLayershell.namespace: "flow-edge"', strip)
        self.assertIn("readonly property int islandDwell: 150", CODE)
        self.assertIn("interval: root.islandDwell", strip)
        self.assertIn('root.openIsland(edgeStrip.modelData.name, "edge")', strip)
        self.assertIn('root.openIsland(edgeStrip.modelData.name, "press")', strip)
        # None on a screen with a band: the band is the shell's.
        self.assertIn("visible: !root.bandFor(modelData)", strip)

    def test_nothing_else_rests_over_apps(self):
        self.assertIn("visible: root.islandOpen && !!root.islandScreen && !root.workCovered", window("workIsland"))
        self.assertIn("visible: root.touchPresent && ", window("homeIndicator"))
        indicator = window("workIndicatorWindow")
        self.assertIn("root.spacesMoving", indicator)
        self.assertIn("mask: Region {}", indicator)
        self.assertIn("mask: Region {}", window("notificationBanner"))

    def test_super_b_opens_the_island_and_a_click_elsewhere_closes_it(self):
        self.assertIn('Super+B { spawn "@shell@" "ipc" "-p" "@shellConfig@" "call" "desk" "island"; }', NIRI)
        self.assertIn("function island(): void { root.toggleIsland() }", CODE)
        self.assertIn('openIsland(name, "shortcut")', function("toggleIsland"))
        island = window("workIsland")
        # Opened by the shortcut it holds the keyboard, so it covers the screen to close on a click anywhere else.
        self.assertIn('readonly property bool modal: root.islandOpenedBy === "shortcut"', island)
        self.assertIn("anchors { top: true; left: modal; right: modal; bottom: modal }", island)
        self.assertIn("WlrKeyboardFocus.Exclusive", island)
        catcher = island[island.index("enabled: workIsland.modal"):][:300]
        self.assertIn("root.closeIsland()", catcher)
        self.assertIn("Keys.onEscapePressed: root.closeIsland()", island)

    def test_the_island_closes_once_the_pointer_has_left(self):
        away = CODE[CODE.index("id: islandAway"):][:700]
        self.assertIn("running: root.islandOpen && !root.islandHeld", away)
        self.assertIn("if (++away >= 5) root.closeIsland()", away)
        self.assertIn("onWorkCoveredChanged: if (workCovered) closeIsland()", CODE)
        self.assertIn("onIslandScreenChanged: if (!islandScreen) closeIsland()", CODE)

    def test_the_clock_pill_is_off_by_default_and_follows_main(self):
        self.assertIn('property string clockPill: "off"', CODE)
        pill_on = function("pillOn")
        self.assertIn('pillPreferences.clockPill === "all"', pill_on)
        self.assertIn('pillPreferences.clockPill === "main" && screen.name === mainDisplayName', pill_on)
        # Main as Super+number has it: number 0, or the lowest-numbered display connected.
        main = CODE[CODE.index("readonly property string mainDisplayName:"):][:300]
        self.assertIn("(a.number ?? 99) - (b.number ?? 99)", main)
        self.assertIn("ranked[0].output", main)
        # One per screen, made and dropped as screens come and go.
        self.assertIn("id: clockPills\n        model: Quickshell.screens", CODE)


class IslandContentTest(unittest.TestCase):
    """The user's choices (27 November 2026): Overview and Desk buttons in the opened Island, no hot corner; a Group's
    apps and a Layer's actions there too; a critical banner stays until the Island or the panel opens."""

    def test_overview_and_desk_are_in_the_opened_island(self):
        island = window("workIsland")
        for ident, call in (("islandOverview", "root.toggleSpacesOverview()"), ("islandDesk", "root.toggleDesk()")):
            button = island[island.index(f"id: {ident}"):][:300]
            self.assertIn(f"onClicked: {call}", button)
        self.assertIn('BandButton {\n                    band: band.h; text: "Overview"', CODE)
        # No hot corner: Niri's stays off, and Overview is reached from the Island, Super+O or the home line.
        self.assertIn("gestures { hot-corners { off; }; }", NIRI)

    def test_a_groups_apps_and_a_layers_actions_are_in_the_opened_island(self):
        island = window("workIsland")
        group = island[island.index("id: islandGroup"):][:1400]
        self.assertIn("model: workIsland.visible && islandGroup.visible ? root.activeSpace.members : []", group)
        self.assertIn('root.spaceAction("focus-window", String(modelData.id))', group)
        layer = island[island.index("id: islandLayer"):][:1600]
        self.assertIn('root.spaceAction("layer-to-space", String(root.activeLayer.id))', layer)
        self.assertIn('root.spaceAction("close-layer", String(root.activeLayer.id))', layer)

    def test_a_critical_banner_stays_until_the_island_or_the_panel_opens(self):
        counting = CODE[CODE.index("readonly property bool bannerCounting:"):][:250]
        self.assertIn("bannerNotification.urgency !== NotificationUrgency.Critical", counting)
        self.assertIn("onIslandOpenChanged: if (islandOpen) bannerNotification = null", CODE)

    def test_the_clock_pill_shows_an_activity(self):
        pill = window("clockPill")
        self.assertIn("root.activities.length ? root.activities[0] : null", pill)
        self.assertIn("activity.owner", pill)


class NotchedScreenTest(unittest.TestCase):
    def test_the_band_keeps_its_rows_from_windows(self):
        band = window("band")
        self.assertIn("exclusiveZone: geometry ? geometry.height : 0", band)
        self.assertIn("WlrLayer.Top", band)

    def test_panels_are_matched_on_the_physical_mode(self):
        band_for = function("bandFor")
        self.assertIn("(displays.find(d => d.output === screen.name) || {}).mode", band_for)
        self.assertIn("p.size[0] === mode[0] && p.size[1] === mode[1]", band_for)
        self.assertIn("const k = screen.width / panel.size[0]", band_for)
        self.assertNotIn("devicePixelRatio", band_for)
        # Asahi's default is the largest 16:10 area below the notch: the band is what is left.
        for size, band in (((2560, 1664), 64), ((2880, 1864), 64), ((3024, 1964), 74), ((3456, 2234), 74)):
            with self.subTest(size=size):
                self.assertIn(f"size: [{size[0]}, {size[1]}], band: {band},", CODE)
                self.assertEqual(size[1] - size[0] * 10 // 16, band)

    def test_the_controller_reports_each_displays_physical_mode(self):
        self.assertEqual(spaces.physical_mode({"modes": [{"width": 1280, "height": 800}, {"width": 2560, "height": 1664}],
                                               "current_mode": 1}), [2560, 1664])
        self.assertIsNone(spaces.physical_mode({"modes": [], "current_mode": None}))
        self.assertIsNone(spaces.physical_mode(None))


if __name__ == "__main__":
    unittest.main()
