"""The shell's side of automatic saving, which the controller runs (flow_controller.py): when it asks for it.

The guest smoke (niri_background_smoke.py) drives the rest: asking once recovery is ready, and pausing on an
error. A Save session or Restore session running is not driven there, so it is read from the source here.
"""
from pathlib import Path
import re
import unittest

SHELL = (Path(__file__).parents[1] / "session/shell.qml").read_text()


def definition(name):
    return re.search(rf"readonly property bool {name}: (.+)\n", SHELL).group(1)


class ShellAutoSaveTest(unittest.TestCase):
    def test_saving_is_asked_for_only_with_the_setting_on_recovery_ready_and_no_recovery_command_running(self):
        self.assertEqual(sorted(term.strip() for term in definition("autoSaveWanted").split("&&")),
                         ["!autoRecoveryBlocked", "!recoveryWorking", "menuPreferences.autoRecovery"])
        # A save during Restore session would replace the session it restores; Save and rename count too.
        self.assertEqual(definition("recoveryWorking"),
                         'recoveryProcess.running && !String(recoveryProcess.command[1]).startsWith("auto-")')

    def test_a_result_after_the_setting_was_switched_off_changes_nothing(self):
        body = SHELL[SHELL.index("    function autoSaved(result) {"):]
        body = body[:body.index("\n    }\n")]
        self.assertEqual(body.splitlines()[1].strip(), "if (!menuPreferences.autoRecovery) return  // switched off since")


if __name__ == "__main__":
    unittest.main()
