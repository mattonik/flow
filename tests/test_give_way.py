"""The work view's bands get out of an app's way (ADR-0013): source checks only, as CI has no QML runner.

What the bands do with a real pointer (an app's edge controls take a click, Open and Save in Nautilus's chooser
too; each band reached by its edge and by Super+B's IPC) rests on ``edges`` and ``chooser-pointer`` in
tests/niri_portals_smoke.py in the guest. These check that each band is wired to the mechanism.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).parents[1]
SHELL = (ROOT / "session/shell.qml").read_text()


def block_after(marker, source=SHELL):
    """The QML object that starts on the line containing ``marker``, by brace matching."""
    start = source.rfind("\n", 0, source.index(marker)) + 1
    depth = 0
    for i in range(source.index("{", start), len(source)):
        depth += {"{": 1, "}": -1}.get(source[i], 0)
        if depth == 0:
            return source[start:i + 1]
    raise AssertionError(marker)


def window(ident):
    """The PanelWindow whose id is ``ident``."""
    at = SHELL.index(f"id: {ident}\n")
    return block_after("PanelWindow {", SHELL[SHELL.rfind("PanelWindow {", 0, at):])


GIVE_WAY = block_after("component GiveWay: Item {")
NIRI = (ROOT / "session/niri.kdl").read_text()
# Each band, its GiveWay's id, and the screen edges its surface touches.
BANDS = {"workIsland": ("islandWay", ["edgeTop: true"]), "homeIndicator": ("homeWay", ["edgeBottom: true"]),
         "workIndicatorWindow": ("indicatorWay", ["edgeBottom: true", "edgeRight: true"]),
         "appMenuTrigger": ("appMenuWay", ["edgeTop: !appMenuTrigger.narrow"])}


class GiveWayTest(unittest.TestCase):
    def test_every_band_takes_its_input_region_from_its_give_way(self):
        for ident, (way, edges) in BANDS.items():
            with self.subTest(band=ident):
                surface = window(ident)
                self.assertIn(f"mask: {way}.mask", surface)
                inner = block_after(f"id: {way}", surface)
                self.assertIn(f"active: {ident}.visible", inner)
                for edge in edges:
                    self.assertIn(edge, inner)

    def test_a_faded_band_keeps_only_its_edge_rows_and_a_probe_takes_all(self):
        self.assertIn("readonly property var mask: yielded && !probing ? passRegion : shownMask", GIVE_WAY)
        region = block_after("property Region passRegion: Region {", GIVE_WAY)
        self.assertIn("width: giveWay.edgeTop ? giveWay.width : 0; height: giveWay.edgeTop ? giveWay.edgeRows : 0", region)
        self.assertIn("y: giveWay.height - giveWay.edgeRows", region)
        self.assertIn("width: giveWay.edgeRight ? giveWay.edgeRows : 0; height: giveWay.edgeRight ? giveWay.height : 0", region)

    def test_the_work_indicator_reaches_its_corner_but_takes_only_its_capsule_and_edges(self):
        surface = window("workIndicatorWindow")
        self.assertNotIn("margins", surface.split("GiveWay {")[0])
        self.assertIn("implicitWidth: workIndicator.implicitWidth + 8 + 12; implicitHeight: 52 + lift", surface)
        shown = block_after("shownMask: Region {", surface)
        self.assertIn("item: indicatorCapsule", shown)
        self.assertEqual(shown.count("Region {"), 3)

    def test_a_faded_band_is_unseen_and_takes_nothing(self):
        band = block_after("id: band", GIVE_WAY)
        self.assertIn("enabled: !giveWay.yielded", band)
        self.assertIn("opacity: giveWay.yielded ? giveWay.fadedOpacity : 1", band)
        # The handlers stay outside the disabled part, or a faded band could never see the pointer again.
        self.assertNotIn("HoverHandler", band)
        self.assertIn("default property alias content: band.data", GIVE_WAY)

    def test_it_fades_the_moment_the_pointer_comes_from_the_app(self):
        moved = block_after("function moved(): void {", GIVE_WAY)
        self.assertIn("if (atEdge(at)) { held = true; yielded = false; probing = false; return }", moved)
        self.assertIn("if (held) return", moved)
        self.assertIn("if (overApp) fadeOut()", moved)
        self.assertNotIn("Timer", moved)
        self.assertNotIn("restMs", GIVE_WAY)
        # Only a motion counts: the same position handed over again (an animation under a still pointer) does not.
        self.assertIn("if (at.x === lastAt.x && at.y === lastAt.y) return", moved)
        self.assertIn("onPointChanged: if (hovered) giveWay.moved()", GIVE_WAY)
        over = re.search(r"readonly property bool overApp: (.*\n.*)", GIVE_WAY).group(1)
        for part in ("active", "root.workViewOnly", "root.windows.length > 0", "!root.searchOpen", "!root.quickSettingsOpen",
                     "!root.notificationsOpen", "!root.appMenuOpen"):
            self.assertIn(part, over)
        self.assertIn("onOverAppChanged: if (!overApp) { yielded = false; probing = false }", GIVE_WAY)

    def test_held_until_the_pointer_leaves(self):
        hover = block_after("HoverHandler {", GIVE_WAY)
        self.assertIn("giveWay.held = false", hover)
        self.assertIn("onTriggered: if (!pointer.hovered) giveWay.held = false", GIVE_WAY)

    def test_super_b_summons_every_band(self):
        self.assertIn('Super+B { spawn "@shell@" "ipc" "-p" "@shellConfig@" "call" "desk" "bands"; }', NIRI)
        bands = block_after("function bands(): void {", SHELL)
        self.assertIn("for (const way of [islandWay, homeWay, indicatorWay, appMenuWay]) way.summon()", bands)
        summon = block_after("function summon(): void {", GIVE_WAY)
        self.assertIn("yielded = false; probing = false; held = true", summon)

    def test_it_comes_back_when_a_probe_finds_no_pointer(self):
        self.assertIn("running: giveWay.yielded && !giveWay.probing; onTriggered: giveWay.probing = true", GIVE_WAY)
        self.assertIn("running: giveWay.probing; onTriggered: { giveWay.probing = false; giveWay.yielded = false }", GIVE_WAY)

    def test_something_urgent_stays_visible_on_a_faded_island(self):
        self.assertIn("fadedOpacity: root.islandUrgent ? 0.4 : 0", block_after("id: islandWay", window("workIsland")))
        urgent = re.search(r"readonly property bool islandUrgent: (.*\n.*)", SHELL).group(1)
        self.assertIn("activities.length > 0", urgent)
        self.assertIn("NotificationUrgency.Critical", urgent)

    def test_the_toast_never_takes_a_click(self):
        toast = block_after("PanelWindow {", SHELL[SHELL.index("// Temporary feedback for shortcuts") - 200:])
        self.assertIn("mask: Region {}", toast)
        self.assertNotIn("MouseArea", toast)


if __name__ == "__main__":
    unittest.main()
