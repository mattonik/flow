"""Desk's routes and bands, and the Island's touch targets, read from the shell's source.

This harness cannot render QML, so these are source checks, scoped to the one window or component
each claim is about (not to the whole file, where another surface could satisfy them):
- Desk's window carries the Island, its four tools and its four foot entries, and each runs its
  own handler (a tool or entry routed to the wrong place is a wrong button, not a style change).
- Desk's scrolling content keeps clear of the Island's band above and the home indicator's band
  below, so neither one's input can take the content's clicks.
- Every control in the Island is at least 44 px, the brand's touch minimum.
How Desk looks is checked by screenshots in the guest (docs/IMPLEMENTATION_STATUS.md).
"""
from pathlib import Path
import re
import unittest

SHELL = (Path(__file__).parents[1] / "session/shell.qml").read_text()


def block_at(start):
    """The QML object whose opening brace is the first one at or after ``start``."""
    depth = 0
    for i in range(SHELL.index("{", start), len(SHELL)):
        depth += {"{": 1, "}": -1}.get(SHELL[i], 0)
        if depth == 0:
            return SHELL[start:i + 1]
    raise AssertionError("unbalanced braces")


def desk_window():
    return block_at(SHELL.index("PanelWindow {\n        visible: root.deskOpen || root.deskHeld\n"))


def child(block, marker):
    """The object inside ``block`` that ``marker`` names: the one opening on its line (``Flickable {``),
    or else the one whose opening line comes just before it (an ``id:`` is an object's first line)."""
    at = SHELL.index(block) + block.index(marker)
    line_end = SHELL.index("\n", at)
    opens_here = SHELL[at:line_end].rstrip().endswith("{")
    opening = at if opens_here else SHELL.rfind("{\n", 0, at)
    return block_at(SHELL.rfind("\n", 0, opening) + 1)


class DeskRoutesTest(unittest.TestCase):
    def test_desk_carries_the_island(self):
        self.assertIn("SystemIsland {", desk_window())

    def test_the_four_tools_each_run_their_own_handler(self):
        tools = child(desk_window(), "id: toolGrid")
        self.assertEqual(re.findall(r'label: "(\w+)"', tools), ["Search", "Apps", "Files", "Settings"])
        handler = tools[tools.index("onClicked: {"):]
        self.assertIn('if (tool === "Settings") {\n', handler)
        self.assertIn("root.settingsOpen = !root.settingsOpen", handler)
        self.assertIn('else if (tool === "Search") root.openSearch()', handler)
        self.assertIn('else if (tool === "Apps") root.openApps()', handler)
        self.assertIn('else { root.deskOpen = false; root.launchApp(["nautilus"]) }', handler)

    def test_the_foot_offers_every_way_on(self):
        foot = child(desk_window(), "id: deskFoot")
        self.assertIn('model: ["Overview", "Spaces", "Shortcuts", "Return to work"]', foot)
        handler = foot[foot.index("onClicked: {"):]
        self.assertIn('if (modelData === "Overview") root.openSpacesOverview()', handler)
        self.assertIn('else if (modelData === "Spaces") root.toggleSwitcher()', handler)
        shortcuts = handler[handler.index('else if (modelData === "Shortcuts") {'):]
        self.assertLess(shortcuts.index("root.deskOpen = false"), shortcuts.index("} else root.deskOpen = false"))
        self.assertIn('Quickshell.execDetached(["niri", "msg", "action", "show-hotkey-overlay"])',
                      shortcuts[:shortcuts.index("} else")])


class DeskBandsTest(unittest.TestCase):
    def island_height(self):
        island = block_at(SHELL.index("component SystemIsland: Rectangle {"))
        return int(re.search(r"implicitWidth: [^;\n]+; implicitHeight: (\d+)", island).group(1))

    def test_content_keeps_clear_of_the_island_and_the_home_indicator(self):
        desk = desk_window()
        scroller = child(desk, "id: deskScroller")
        margins = re.search(r"anchors\.topMargin: ([\d +]+); anchors\.bottomMargin: (\d+)", scroller)
        self.assertIsNotNone(margins, "Desk's content has no top and bottom margins")
        indicator = child(desk, "HomeIndicator {")
        indicator_height = int(re.search(r"height: (\d+)", indicator).group(1))
        self.assertGreaterEqual(sum(int(n) for n in margins.group(1).split("+")), self.island_height())
        self.assertGreaterEqual(int(margins.group(2)), indicator_height)
        # Nothing but the Island, the home indicator and the content may float over Desk's screen.
        scope = child(desk, "FocusScope {")
        floating = re.findall(r"\n {12}(\w+) \{", scope)
        self.assertEqual(floating, ["SystemIsland", "HomeIndicator", "Flickable"])

    def test_every_island_button_is_a_full_touch_target(self):
        self.assertGreaterEqual(self.island_height(), 44)
        start = SHELL.index("component SystemIsland: Rectangle {")
        island = block_at(start)
        buttons = [block_at(start + m.start()) for m in re.finditer(r"IslandButton \{", island)]
        self.assertEqual(len(buttons), 4)  # floe., Apps, notifications, quick settings
        for button in buttons:
            own = button.split("\n", 3)[:3]  # its own lines, before any child object
            sizes = re.findall(r"\b(width|implicitHeight): (\d+)", "\n".join(own))
            self.assertIn("implicitHeight", [name for name, _ in sizes], own[0])
            for name, size in sizes:
                self.assertGreaterEqual(int(size), 44, f"{name} in {own[0].strip()}")


if __name__ == "__main__":
    unittest.main()
