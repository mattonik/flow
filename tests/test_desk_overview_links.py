"""tests/niri_desk_overview_smoke.py puts the user's Niri include links back as they were.

The smoke runs the installed shell file, which points ~/.config/flow/niri-appearance.kdl and niri-motion.kdl from its
own settings (Reduce motion has to reach Niri to be tested). Here, in a temporary HOME: links changed, added and
removed are all put back, each by a rename.
"""
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parent))
import niri_desk_overview_smoke as smoke  # noqa: E402


class PutBackLinksTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="flow-links-")
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)
        (self.home / ".config/flow").mkdir(parents=True)
        self.dir = self.home / ".config/flow"

    def link(self, name, target):
        path = self.dir / name
        if path.is_symlink():
            path.unlink()
        path.symlink_to(target)

    def test_changed_added_and_removed_links_are_put_back(self):
        self.link("niri-appearance.kdl", "/etc/flow/niri-dark.kdl")
        self.link("niri-motion.kdl", "/etc/flow/niri-moving.kdl")
        before = smoke.niri_links(self.home)
        self.link("niri-motion.kdl", "/etc/flow/niri-still.kdl")        # the run's Reduce motion
        (self.dir / "niri-appearance.kdl").unlink()                       # gone
        self.link("niri-extra.kdl", "/etc/flow/niri-extra.kdl")          # added
        smoke.put_back_links(self.home, before)
        self.assertEqual(smoke.niri_links(self.home), before)
        self.assertEqual(os.readlink(self.dir / "niri-motion.kdl"), "/etc/flow/niri-moving.kdl")
        self.assertFalse((self.dir / "niri-motion.kdl.f007").exists())

    def test_a_link_that_was_not_there_is_removed(self):
        self.link("niri-motion.kdl", "/etc/flow/niri-still.kdl")
        smoke.put_back_links(self.home, {})
        self.assertEqual(smoke.niri_links(self.home), {})


if __name__ == "__main__":
    unittest.main()
