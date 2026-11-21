"""In Overview a display's row can be walked with the keyboard, the wheel, a pointer or a finger.

Niri has no Overview-only binds, so plain arrow keys cannot do it. What Flow provides:
- Super+Left/Right (Flow's space navigation, which keeps Overview open: test_spaces covers navigate);
- Super+wheel in every direction, which Niri applies in Overview without Super held;
- the work indicator's arrows and position, which stay visible in Overview.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).parents[1]
NIRI = (ROOT / "session/niri.kdl").read_text()
SHELL = (ROOT / "session/shell.qml").read_text()


class OverviewNavigationTest(unittest.TestCase):
    def test_every_scroll_direction_moves_along_the_row(self):
        binds = dict(re.findall(r'Super\+(WheelScroll\w+) cooldown-ms=\d+ \{ spawn "@spaces@" "navigate" "(\w+)"; \}', NIRI))
        self.assertEqual(binds, {"WheelScrollDown": "right", "WheelScrollUp": "left",
                                 "WheelScrollRight": "right", "WheelScrollLeft": "left"})

    def test_the_keyboard_keeps_flows_navigation(self):
        self.assertIn('Super+Left { spawn "@spaces@" "navigate" "left"; }', NIRI)
        self.assertIn('Super+Right { spawn "@spaces@" "navigate" "right"; }', NIRI)

    def test_the_work_indicator_stays_in_overview(self):
        window = SHELL[SHELL.index("id: workIndicatorWindow"):]
        visible = window[window.index("visible:"):window.index("\n", window.index("visible:"))]
        self.assertNotIn("overviewOpen", visible)
        self.assertIn('root.spaceAction("navigate", "left")', window[:window.index("// shortcut: window titles")])


if __name__ == "__main__":
    unittest.main()
