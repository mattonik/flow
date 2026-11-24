"""The shell keeps each persisted setting in a Core.Settings of its own (F-018).

Qt 6.12's Settings, loading a file that lacks one of its keys, writes back the values its later properties had
before they were loaded: their defaults. In the guest, a flow-settings.ini without appMenuEnabled came back
from the next shell start with autoRecovery=false (tests/niri_shell_restart_controller_smoke.py). With one
property per object, a missing key is written only with its own default.
"""

from pathlib import Path
import re
import unittest

SHELL = (Path(__file__).parents[1] / "session/shell.qml").read_text()


def settings_blocks(qml):
    """The body of each ``Core.Settings { ... }``, by brace depth."""
    blocks = []
    for match in re.finditer(r"Core\.Settings\s*\{", qml):
        depth, index = 1, match.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(qml[index], 0)
            index += 1
        blocks.append(qml[match.end():index - 1])
    return blocks


class SettingsTest(unittest.TestCase):
    def test_each_settings_object_persists_one_property(self):
        blocks = settings_blocks(SHELL)
        self.assertGreaterEqual(len(blocks), 4)
        for block in blocks:
            with self.subTest(block=block.strip()):
                self.assertEqual(len(re.findall(r"^\s*property\s", block, re.M)), 1)

    def test_every_setting_is_still_persisted_where_it_was(self):
        found = {}
        for block in settings_blocks(SHELL):
            location = re.search(r"location:\s*root\.(\w+)", block).group(1)
            found[re.search(r"property\s+\w+\s+(\w+)", block).group(1)] = location
        self.assertEqual(found, {"appMenuEnabled": "settingsFile", "autoRecovery": "settingsFile",
                                 "lightMode": "appearanceFile", "reduceMotion": "appearanceFile"})
        self.assertIn('"/flow-settings.ini"', SHELL)
        self.assertIn('"/floe-appearance.ini"', SHELL)


if __name__ == "__main__":
    unittest.main()
