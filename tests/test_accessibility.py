from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1]


class AccessibilityBusTest(unittest.TestCase):
    """CI evaluates no Nix, so this guards the line screen readers depend on as text: it does not evaluate
    the setting (another module could still force it off). The guest smoke tests/niri_focus_walk_smoke.py
    checks at run time that the session bus can start the accessibility bus."""

    def test_module_enables_the_accessibility_bus(self):
        module = (ROOT / "modules/flow.nix").read_text()
        self.assertRegex(module, r"(?m)^\s*services\.gnome\.at-spi2-core\.enable = true;")


if __name__ == "__main__":
    unittest.main()
