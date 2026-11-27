"""Overview's row is walkable with the keyboard, the wheel, a pointer or a finger.

These tests check the configuration and shell sources (no compositor runs here):
- every wheel direction is bound, enabled (not commented out with KDL's ``/-``), goes through the
  running shell's ``navigate`` IPC call, and keeps a short cooldown;
- that call drops a notch while a step is running or queued, so a spin never queues moves;
- the work indicator, with both arrows, stays visible in Overview, also over a Layer.
Flow's navigation itself is covered by test_spaces (op ``navigate``).
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).parents[1]
NIRI = (ROOT / "session/niri.kdl").read_text()
SHELL = (ROOT / "session/shell.qml").read_text()


def active_binds():
    """The binds block's lines that Niri reads: no comments, no ``/-`` (disabled) nodes."""
    block = NIRI[NIRI.index("binds {"):]
    lines = []
    for line in block.splitlines():
        line = re.sub(r"//.*", "", line).strip()
        if line and not line.startswith("/-"):
            lines.append(line)
    return lines


def block_after(marker, source=SHELL):
    """The QML object that starts on the line containing ``marker``, by brace matching."""
    start = source.rfind("\n", 0, source.index(marker)) + 1
    depth = 0
    for i in range(source.index("{", start), len(source)):
        depth += {"{": 1, "}": -1}.get(source[i], 0)
        if depth == 0:
            return source[start:i + 1]
    raise AssertionError(marker)


class WheelTest(unittest.TestCase):
    def test_every_wheel_direction_steps_through_the_shell(self):
        found = {}
        for line in active_binds():
            m = re.fullmatch(r'Super\+(WheelScroll\w+) cooldown-ms=(\d+) \{ spawn "@shell@" "ipc" "-p" "@shellConfig@" '
                             r'"call" "desk" "navigate" "(left|right)"; \}', line)
            if m:
                found[m.group(1)] = (m.group(3), int(m.group(2)))
        self.assertEqual({k: v[0] for k, v in found.items()},
                         {"WheelScrollDown": "right", "WheelScrollUp": "left",
                          "WheelScrollRight": "right", "WheelScrollLeft": "left"})
        for direction, cooldown in found.values():
            self.assertLessEqual(cooldown, 300)  # a step per notch, not one every few seconds

    def test_a_notch_is_dropped_while_a_step_is_running_or_queued(self):
        handler = block_after("function navigate(direction: string): void {")
        self.assertIn('spacesProcess.running && spacesProcess.command[1] === "navigate"', handler)
        self.assertIn('root.pendingSpaceActions.some(a => a.action === "navigate")', handler)
        self.assertLess(handler.index("return"), handler.index('root.spaceAction("navigate", direction)'))

    def test_the_keyboard_keeps_flows_navigation(self):
        lines = active_binds()
        self.assertIn('Super+Left { spawn "@spaces@" "navigate" "left"; }', lines)
        self.assertIn('Super+Right { spawn "@spaces@" "navigate" "right"; }', lines)


class IndicatorTest(unittest.TestCase):
    def test_the_work_indicator_shows_only_while_moving_and_has_no_buttons(self):
        # ADR-0014: a moment after a move between spaces, taking no clicks, never in Niri's overview; the opened Island
        # has the arrows instead.
        marker = SHELL.index("// Work indicator: the space's place in this display's Flow")
        window = block_after("PanelWindow {", SHELL[SHELL.rindex("PanelWindow {", 0, marker):])
        visibility = window[window.index("visible: "):window.index("mask:")]
        self.assertIn("root.spacesMoving", visibility)
        self.assertIn("!root.overviewOpen", visibility)
        self.assertIn("mask: Region {}", window)
        self.assertNotRegex(window, r"\b(IslandButton|FlowButton|Button|MouseArea|TapHandler)\s*\{")
        island = block_after("PanelWindow {", SHELL[SHELL.rindex("PanelWindow {", 0, SHELL.index("        id: workIsland\n")):])
        for direction, label in (("left", "Previous space"), ("right", "Next space")):
            self.assertIn(f'Accessible.name: "{label}"', island)
            self.assertIn(f'onClicked: root.spaceAction("navigate", "{direction}")', island)


# QML with its // comments removed: a commented-out line must not satisfy a check. A comment starts a line or follows
# whitespace, so "file://" in a string stays.
CODE = re.sub(r"(?m)(^|(?<=\s))//.*$", "", SHELL)


def window(ident):
    """The top-level lines of the PanelWindow whose id is ``ident`` (its own properties, not its children's)."""
    at = CODE.index(f"id: {ident}\n")
    start = CODE.rindex("PanelWindow {", 0, at)
    depth, lines, line = 0, [], ""
    for ch in CODE[start:]:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                break
        if ch == "\n":
            if depth == 1:
                lines.append(line.strip())
            line = ""
        elif depth == 1 and ch not in "{}":
            line += ch
    return [l for l in lines if l]


class ReadOnlyTest(unittest.TestCase):
    """QML refuses an assignment to a read-only property at run time (a TypeError in the log), so a handler that
    makes one does nothing: Overview's Home and End, and a click on a card that is not selected, did nothing that way.
    The selection is a space (``overviewSpaceId``), set through ``selectOverview``. Source checks only: CI has no QML
    runner; tests/niri_desk_overview_smoke.py drives the keys and the click in the guest."""

    def test_no_handler_assigns_to_a_read_only_property_of_the_shell(self):
        names = re.findall(r"readonly property [\w<>.]+ (\w+)\s*:", CODE)
        self.assertIn("overviewIndex", names)
        for name in names:
            with self.subTest(name=name):
                # =, +=, -= and the like, and ++ or -- either side: QML refuses each on a read-only property.
                change = r"\s*(?:[-+*/%]?=(?![=>])|\+\+|--)"
                self.assertIsNone(re.search(rf"\broot\.{name}{change}|(?:\+\+|--)root\.{name}\b", CODE),
                                  f"root.{name} is assigned")
                # Unqualified, as from a handler in the property's own scope; a local ``const``/``let``/``var`` of the
                # same name is a declaration, not an assignment.
                assigned = [m for m in re.finditer(rf"(?<![\w.]){name}{change}|(?:\+\+|--){name}\b", CODE)
                            if not re.search(r"\b(const|let|var)\s+$", CODE[:m.start()][-12:])]
                self.assertEqual(assigned, [], f"{name} is assigned")

    def test_home_end_and_a_card_click_choose_through_select_overview(self):
        keys = block_after("Keys.onPressed: event => {", CODE[CODE.index("id: spacesOverview"):])
        self.assertIn("root.selectOverview(0)", keys)
        self.assertIn("root.selectOverview(root.displaySpaces.length - 1)", keys)
        self.assertIn(": root.selectOverview(spaceCard.index)", CODE)

    def test_super_o_again_while_overview_opens_goes_on_to_desk(self):
        toggle = block_after("function toggleSpacesOverview() {", CODE)
        pending = next(line for line in toggle.splitlines() if "if (overviewPending)" in line)
        self.assertIn("revealDeskFromOverview()", pending)
        self.assertLess(pending.index("revealDeskFromOverview()"), pending.index("return"))


class ScreenTest(unittest.TestCase):
    """Where the shell's surfaces map, and what shows between them. Source checks only: CI has no QML runner, so the
    timing (the holds, their 0.6 s limit, a drawn frame) rests on tests/niri_desk_overview_smoke.py in the guest, which
    checks it frame by frame and on two displays. These check that each piece is where it acts."""

    def test_the_work_views_bands_are_on_the_focused_displays_screen(self):
        self.assertIn("readonly property var workScreen: Quickshell.screens.find(screen => screen.name === displayOutput) || null", CODE)
        self.assertIn('displayOutput = result.display ? result.display.output || "" : ""', CODE)
        for ident in ("homeIndicator", "workIndicatorWindow"):
            with self.subTest(window=ident):
                self.assertIn("screen: root.workScreen", window(ident))
        # The opened Island goes where it was opened (the screen whose edge or pill was used), else the focused one;
        # the App button goes with it.
        self.assertIn("screen: root.islandScreen || root.workScreen", window("workIsland"))
        self.assertIn("screen: root.islandOpen && root.islandScreen ? root.islandScreen : root.workScreen", window("appMenuTrigger"))

    def test_desk_and_overview_map_where_focus_is(self):
        # A screen from the refresh can be late: Desk would open on the display the user just left.
        for ident in ("deskWindow", "spacesOverview"):
            with self.subTest(window=ident):
                self.assertFalse([line for line in window(ident) if line.startswith("screen:")], window(ident))

    def test_the_work_view_stays_until_desk_or_overview_has_drawn(self):
        covered = re.search(r"readonly property bool workCovered: (.*)", CODE).group(1).strip()
        self.assertEqual(covered, "(deskOpen && deskDrawn) || (spacesOverviewOpen && overviewDrawn) || deskHeld || overviewHeld")
        for ident in ("homeIndicator", "workIndicatorWindow", "workIsland", "appMenuTrigger"):
            with self.subTest(window=ident):
                visible = next(line for line in window(ident) if line.startswith("visible:"))
                self.assertIn("!root.workCovered", visible)
                self.assertNotIn("root.deskOpen", visible)
                self.assertNotIn("root.spacesOverviewOpen", visible)
        self.assertIn("function onFrameSwapped() { if (deskWindow.visible && !root.deskDrawn) root.deskDrawn = true }", CODE)
        overview_swap = block_after("function onFrameSwapped() {\n                    if (spacesOverview.visible", CODE)
        self.assertEqual(overview_swap.split("\n")[1].strip(), "if (spacesOverview.visible && !root.overviewDrawn) root.overviewDrawn = true")
        self.assertFalse([line for line in window("deskWindow") if "deskDrawn = true" in line])

    def test_between_overview_and_desk_the_one_going_stays_until_the_one_coming_has_drawn(self):
        self.assertIn("visible: root.deskOpen || root.deskHeld", window("deskWindow"))
        self.assertIn("visible: root.spacesOverviewOpen || root.overviewHeld", window("spacesOverview"))
        self.assertIn("onDeskDrawnChanged: if (deskDrawn) overviewHeld = false", CODE)
        self.assertIn("onOverviewDrawnChanged: if (overviewDrawn) deskHeld = false", CODE)
        # Held where the change is made, so every route between them holds: Super+H from Overview, Super+O again, the
        # home indicator, Desk's Overview, search's Overview, the IPC.
        toggle = block_after("function toggleDesk() {", CODE)
        self.assertLess(toggle.index("if (!deskOpen && spacesOverviewOpen) holdOverview()"), toggle.index("spacesOverviewOpen = false"))
        opening = block_after("function openSpacesOverview() {", CODE)
        self.assertLess(opening.index("if (deskOpen) holdDesk()"), opening.index("deskOpen = false"))
        self.assertIn("else toggleDesk()", block_after("function revealDeskFromOverview() {", CODE))
        self.assertNotIn("Held = true", CODE.replace("function holdDesk() { deskHeld = true", "").replace(
            "function holdOverview() { overviewHeld = true", ""))
        search = block_after("function runSearchResult(result) {", CODE)
        self.assertIn('result.value === "overview"))) deskOpen = false', search)


if __name__ == "__main__":
    unittest.main()
