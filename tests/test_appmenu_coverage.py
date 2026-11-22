import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("appmenu_coverage", Path(__file__).parent / "appmenu_coverage.py")
coverage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coverage)

MENUS = [
    {"label": "File", "children": [{"label": "New", "path": [0, 0, 0], "enabled": True},
                                   {"label": "Recent", "children": [{"label": "a.txt", "path": [0, 0, 1, 0], "enabled": True}]}]},
    {"label": "Help", "children": [{"label": "About Kate", "path": [0, 1, 0], "enabled": False},
                                   {"label": "About KDE", "path": [0, 1, 1], "enabled": True}]},
]


class CoverageHelpersTest(unittest.TestCase):
    def test_leaves_carry_their_menu_path(self):
        self.assertEqual([labels for labels, _ in coverage.leaves(MENUS)],
                         [("File", "New"), ("File", "Recent", "a.txt"), ("Help", "About Kate"), ("Help", "About KDE")])

    def test_command_is_the_first_enabled_match(self):
        labels, leaf = coverage.find_command(MENUS, ("^Help$", "^About"))
        self.assertEqual(labels, ("Help", "About KDE"))
        self.assertEqual(leaf["path"], [0, 1, 1])
        self.assertEqual(coverage.find_command(MENUS, ("^View$", ".")), (None, None))

    def test_accelerator_from_gtk_qt_and_bare_bindings(self):
        self.assertEqual(coverage.accelerator("n;<Alt>f:n;<Control>n"), "<Control>n")
        self.assertEqual(coverage.accelerator("Ctrl+N"), "Ctrl+N")
        self.assertEqual(coverage.accelerator("n;;"), "")
        self.assertEqual(coverage.accelerator("Alt+N"), "")  # Qt: the mnemonic of an item without a shortcut
        self.assertEqual(coverage.accelerator("Alt+F4"), "Alt+F4")
        self.assertEqual(coverage.accelerator(""), "")

    def test_narrowing_needs_title_and_active_to_single_out_a_frame(self):
        frames = [{"name": "Home", "active": False}, {"name": "Home", "active": True}, {"name": "tmp", "active": False}]
        self.assertEqual(coverage.narrowing(frames, "Home"),
                         {"by_title": [0, 1], "by_active": [1], "both": [1], "resolved": True})
        self.assertFalse(coverage.narrowing(frames, "tmp")["resolved"])  # named, but not the active frame
        self.assertFalse(coverage.narrowing([{"name": "Home", "active": True}] * 2, "Home")["resolved"])


if __name__ == "__main__":
    unittest.main()
