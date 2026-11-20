"""Group members are labelled by their installed app's name."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "session"))
import spaces


class AppNameTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)
        (self.data / "applications").mkdir()
        environment = patch.dict(os.environ, {"XDG_DATA_HOME": str(self.data), "XDG_DATA_DIRS": str(self.data / "none")})
        environment.start()
        self.addCleanup(environment.stop)
        spaces.app_name.cache_clear()
        self.addCleanup(spaces.app_name.cache_clear)

    def test_name_comes_from_the_desktop_entry_section_only(self):
        (self.data / "applications/org.kde.kate.desktop").write_text(
            "[Desktop Action new]\nName=New Window\n[Desktop Entry]\nType=Application\nName=Kate\nName[de]=Kate DE\n")
        self.assertEqual(spaces.app_name("org.kde.kate"), "Kate")
        self.assertIsNone(spaces.app_name("missing"))
        self.assertIsNone(spaces.app_name("../etc/passwd"))
        ws = {"id": 1, "active_window_id": 1}
        windows = [{"id": 1, "app_id": "org.kde.kate", "title": "notes.txt — Kate", "workspace_id": 1,
                    "is_floating": False, "layout": {"pos_in_scrolling_layout": [1, 1]}}]
        member = spaces.display_spaces(ws, windows)[0]["members"][0]
        self.assertEqual((member["label"], member["app_name"]), ("notes.txt — Kate", "Kate"))


if __name__ == "__main__":
    unittest.main()
