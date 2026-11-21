from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1]


class AccessibilityBusTest(unittest.TestCase):
    """CI evaluates no Nix, so this guards the one line screen readers depend on.
    The guest smoke tests/niri_focus_walk_smoke.py checks what it does at run time."""

    def test_module_enables_the_accessibility_bus(self):
        module = (ROOT / "modules/flow.nix").read_text()
        self.assertRegex(module, r"(?m)^\s*services\.gnome\.at-spi2-core\.enable = true;")


if __name__ == "__main__":
    unittest.main()
