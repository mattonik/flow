"""The shell keeps each persisted setting in a Core.Settings of its own.

Qt 6.12's Settings, loading a file that lacks one of its keys, writes back the values its later properties had
before they were loaded: their defaults, over what the file held. With one property per object, a missing key is
only ever written with its own default. tests/niri_shell_restart_controller_smoke.py starts the shell from a file
without the first key and checks that automatic recovery stays on across restarts.
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
        self.assertGreaterEqual(len(blocks), 2)
        for block in blocks:
            with self.subTest(block=block.strip()):
                self.assertEqual(len(re.findall(r"\bproperty\s+\w+\s+\w+", block)), 1)

    def test_settings_objects_share_the_two_files(self):
        locations = {re.search(r"location:\s*(\S+)", block).group(1) for block in settings_blocks(SHELL)}
        self.assertEqual(locations, {"root.settingsFile", "root.appearanceFile"})


if __name__ == "__main__":
    unittest.main()
