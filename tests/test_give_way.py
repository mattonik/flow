"""The work view's bands get out of an app's way (ADR-0013): source checks only, as CI has no QML runner.

What the bands do with a real pointer (an app's edge controls take a rest-then-click, Open and Save in Nautilus's
chooser too) rests on ``edges`` and ``chooser-pointer`` in tests/niri_portals_smoke.py in the guest; that a quick
click, a tap and a swipe still reach the home indicator rests on tests/niri_prototype_surfaces_smoke.py and
tests/niri_desk_overview_smoke.py. These check that each band is wired to the mechanism.
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
# Each band, its GiveWay's id, and the screen edge it hangs from.
BANDS = {"workIsland": ("islandWay", '"top"'), "homeIndicator": ("homeWay", '"bottom"'),
         "workIndicatorWindow": ("indicatorWay", None), "appMenuTrigger": ("appMenuWay", 'appMenuTrigger.narrow ? "" : "top"')}


class GiveWayTest(unittest.TestCase):
    def test_every_band_takes_its_input_region_from_its_give_way(self):
        for ident, (way, edge) in BANDS.items():
            with self.subTest(band=ident):
                surface = window(ident)
                self.assertIn(f"mask: {way}.mask", surface)
                inner = block_after(f"id: {way}", surface)
                self.assertIn(f"active: {ident}.visible", inner)
                if edge:
                    self.assertIn(f"edge: {edge}", inner)
                else:
                    self.assertNotIn("edge:", inner)

    def test_a_faded_band_keeps_only_its_edge_rows_and_a_probe_takes_all(self):
        self.assertIn("readonly property var mask: yielded && !probing ? passRegion : null", GIVE_WAY)
        region = block_after("property Region passRegion: Region {", GIVE_WAY)
        self.assertIn('width: giveWay.edge ? giveWay.width : 0; height: giveWay.edge ? giveWay.edgeRows : 0', region)
        self.assertIn('y: giveWay.edge === "bottom" ? giveWay.height - giveWay.edgeRows : 0', region)

    def test_a_faded_band_is_unseen_and_takes_nothing(self):
        band = block_after("id: band", GIVE_WAY)
        self.assertIn("enabled: !giveWay.yielded", band)
        self.assertIn("opacity: giveWay.yielded ? 0 : 1", band)
        # The handlers stay outside the disabled part, or a faded band could never see the pointer again.
        self.assertNotIn("HoverHandler", band)
        self.assertIn("default property alias content: band.data", GIVE_WAY)

    def test_it_fades_only_after_a_rest_over_an_app_away_from_the_edge(self):
        rest = block_after("id: rest; interval: giveWay.restMs", GIVE_WAY)
        self.assertIn("if (pointer.hovered && !giveWay.pressed && giveWay.overApp && !giveWay.fromEdge && !giveWay.yielded) {", rest)
        self.assertEqual(re.search(r"readonly property int restMs: (\d+)", GIVE_WAY).group(1), "600")
        over = re.search(r"readonly property bool overApp: (.*\n.*)", GIVE_WAY).group(1)
        for part in ("active", "root.workViewOnly", "root.windows.length > 0", "!root.searchOpen", "!root.quickSettingsOpen",
                     "!root.notificationsOpen", "!root.appMenuOpen"):
            self.assertIn(part, over)
        self.assertIn("onOverAppChanged: if (!overApp) { yielded = false; probing = false }", GIVE_WAY)
        moved = block_after("function moved(): void {", GIVE_WAY)
        # Only a motion starts the rest again: the same position handed over again (an animation under a still
        # pointer, the home indicator's line widening) does not, or the home indicator faded 0.2 s late.
        self.assertIn("if (at.x === lastAt.x && at.y === lastAt.y) return", moved)
        self.assertLess(moved.index("return"), moved.index("rest.restart()"))
        self.assertIn("onPointChanged: if (hovered) giveWay.moved()", GIVE_WAY)

    def test_a_press_on_the_band_keeps_it(self):
        # Seen above the band and passed on, so the band's own controls still take it.
        area = block_after("MouseArea {", GIVE_WAY)
        self.assertIn("onPressed: mouse => { giveWay.pressed = true; giveWay.presses += 1; rest.stop(); mouse.accepted = false }", area)
        self.assertNotIn("hoverEnabled", area)
        self.assertLess(GIVE_WAY.index("id: band"), GIVE_WAY.index("MouseArea {"))

    def test_it_comes_back_at_the_edge_or_when_a_probe_finds_no_pointer(self):
        seen = block_after("function moved(): void {", GIVE_WAY)
        self.assertIn("if (yielded && atTheEdge) { yielded = false; probing = false }", seen)
        self.assertIn("else if (probing) probing = false", seen)
        self.assertIn("running: giveWay.yielded && !giveWay.probing; onTriggered: giveWay.probing = true", GIVE_WAY)
        self.assertIn("running: giveWay.probing; onTriggered: { giveWay.probing = false; giveWay.yielded = false }", GIVE_WAY)

    def test_the_toast_never_takes_a_click(self):
        toast = block_after("PanelWindow {", SHELL[SHELL.index("// Temporary feedback for shortcuts") - 200:])
        self.assertIn("mask: Region {}", toast)
        self.assertNotIn("MouseArea", toast)


if __name__ == "__main__":
    unittest.main()
