import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location("flow_search", Path(__file__).parents[1] / "session/search.py")
search = importlib.util.module_from_spec(spec)
spec.loader.exec_module(search)


class SearchTest(unittest.TestCase):
    def test_local_results_include_available_pins_windows_and_apps(self):
        app = Mock()
        app.get_display_name.return_value = "Browser"
        app.should_show.return_value = True
        hidden = Mock()
        hidden.get_display_name.return_value = "Hidden"
        hidden.should_show.return_value = False
        data = {"pins": [{"label": "Work", "id": "1", "available": True}, {"label": "Missing", "id": "2", "available": False}],
                "windows": [{"label": "My\nDocument", "id": 4, "app_id": "editor"}]}
        rows = search.entries(data, [app, hidden])
        self.assertEqual([r[1][0] for r in rows], ["pin", "window", "app"])
        self.assertEqual(rows[1][0], "My Document · Open window · editor")
        self.assertEqual(rows[1][1], ("window", "4"))


if __name__ == "__main__":
    unittest.main()
