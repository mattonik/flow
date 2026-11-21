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
    def test_the_work_indicator_and_both_arrows_stay_in_overview(self):
        marker = SHELL.index("// Work indicator: the focused app of an active Group")
        window = block_after("PanelWindow {", SHELL[SHELL.rindex("PanelWindow {", 0, marker):])
        visibility = re.findall(r"visible: ([^\n]+)", window)
        self.assertNotIn("overviewOpen", visibility[0])  # the window itself
        self.assertNotRegex(window, r"visible: !root\.overviewOpen\b")
        for direction, label in (("left", "Previous space"), ("right", "Next space")):
            button = window[window.rindex("IslandButton {", 0, window.index(f'Accessible.name: "{label}"')):]
            button = button[:button.index(f'root.spaceAction("navigate", "{direction}")') + 40]
            self.assertIn("visible: !root.activeLayer || root.overviewOpen", button, direction)
            self.assertIn(f'onClicked: root.spaceAction("navigate", "{direction}")', button, direction)


if __name__ == "__main__":
    unittest.main()
