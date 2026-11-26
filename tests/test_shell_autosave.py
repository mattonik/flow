"""The shell's side of automatic saving, which the controller runs (flow_controller.py): when it asks for it.

There is no QML harness: the shell's handlers are read from the source, so these checks break on a reflow and
pass a behaviour change kept in the same text. The guest smokes (niri_background_smoke.py, the reboot smoke) run
the behaviour.

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
                         ["!autoRecoveryBlocked", "!recoveryWorking", "recoveryPreferences.sessionRecovery"])
        # A save during Restore session would replace the session it restores; Save and rename count too.
        self.assertEqual(definition("recoveryWorking"),
                         'recoveryProcess.running && !String(recoveryProcess.command[1]).startsWith("auto-")')

    def test_a_result_after_the_setting_was_switched_off_changes_nothing(self):
        body = SHELL[SHELL.index("    function autoSaved(result) {"):]
        body = body[:body.index("\n    }\n")]
        self.assertLess(body.index("if (!recoveryPreferences.sessionRecovery) return"), body.index("recoveryMessage ="))
        self.assertLess(body.index("if (!result.error) return"), body.index("autoRecoveryBlocked = true"))

    def test_a_paused_automatic_save_is_told_in_a_toast_and_leaves_the_desk_closed(self):
        # With recovery on by default, two windows of one app pause it while the user works.
        body = SHELL[SHELL.index("    function autoSaved(result) {"):]
        body = body[:body.index("\n    }\n")]
        self.assertIn("showToast(", body)
        for opens in ("deskOpen", "settingsOpen", "reveal("):
            self.assertNotIn(opens, body)

    def test_every_shell_start_marks_the_niri_instance_whatever_the_setting(self):
        # restore.automatic reopens only at an instance's first shell start; a start with recovery off counts.
        timer = SHELL[SHELL.index("id: autoStartTimer"):]
        timer = timer[:timer.index("\n    }\n")]
        self.assertIn("Component.onCompleted: start()", timer)
        self.assertIn('recoveryPreferences.sessionRecovery ? "auto-start" : "auto-seen"', timer)
        self.assertNotIn("if (!recoveryPreferences.sessionRecovery) return", timer)

    def test_a_pause_from_earlier_in_the_session_is_told_in_a_toast_not_on_the_desk(self):
        handler = SHELL[SHELL.index('if (recoveryProcess.command[1] === "auto-start" || recoveryProcess.command[1] === "auto-ready") {'):]
        handler = handler[:handler.index("\n                    }\n")]
        paused = re.search(r"if \(result\.error && result\.paused\) (.+)\n", handler).group(1)
        self.assertIn("showToast(", paused)
        self.assertNotIn("deskOpen", paused)
        self.assertRegex(handler, r"else if \(result\.error\) \{ root\.deskOpen = true")

    def test_a_saved_session_set_aside_is_told_in_a_toast(self):
        handler = SHELL[SHELL.index('if (recoveryProcess.command[1] === "auto-start" || recoveryProcess.command[1] === "auto-ready") {'):]
        handler = handler[:handler.index("\n                    }\n")]
        self.assertRegex(handler, r"if \(result\.set_aside\) \{\s*root\.recoverySetAside = result\.set_aside\s*"
                                  r"root\.recoverySetAsideLeft = \"\"\s*root\.showToast\(")
        # Kept in Settings for the shell's life, as the first automatic save soon replaces recoveryMessage.
        self.assertRegex(SHELL, r'Text \{ id: recoveryAside; visible: root\.recoverySetAside !== ""; text: [^;]*root\.recoverySetAside')

    def test_a_set_aside_session_is_reopened_from_its_own_file(self):
        # Not by renaming it to session.json, which the next automatic save would replace before Restore read it.
        # Whole lines and the whole function, as text: an early return, a guard dropped or the button disabled
        # fails here. What it does is run by tests/niri_set_aside_reopen_smoke.py in the guest; CI cannot.
        lines = [line.strip() for line in SHELL.splitlines()]
        self.assertIn('FlowButton { id: reopenAside; visible: root.recoverySetAside !== "" && root.recoverySetAsideLeft === ""; implicitHeight: 44; variant: "quiet"; text: "Reopen it"; enabled: !root.recoveryWorking && !root.spacesWorking; onClicked: root.reopenSetAside() }', lines)
        self.assertIn('Text { id: recoveryAside; visible: root.recoverySetAside !== ""; text: root.recoverySetAsideLeft ? "Reopened in part from " + root.recoverySetAside + ": " + root.recoverySetAsideLeft + " Open the rest yourself; reopening it again would open the others twice." : "Not reopened when the shell started: the saved session is kept as " + root.recoverySetAside + "."; textFormat: Text.PlainText; color: floeTheme.textMuted; width: parent.width; wrapMode: Text.Wrap; font.pixelSize: 14; font.family: floeTheme.interfaceFont }', lines)
        self.assertIn('    function reopenSetAside() {\n        const busy = recoveryProcess.running ? "Wait for session recovery to finish" : spacesWorking ? "Wait for the current action" : ""\n        if (busy) { recoveryMessage = busy; return }\n        if (!recoverySetAside || recoverySetAsideLeft) return\n        recoveryMessage = "Reopening applications…"\n        recoveryProcess.command = ["flow-recovery", "restore", recoverySetAside]\n        recoveryProcess.running = true\n    }', SHELL)
        # Withdrawn once every window is back; once anything opened, Reopen it is not offered again (a second
        # run would open those twice) and the line says what was left out. Cleared nowhere else.
        handler = SHELL[SHELL.index("        id: recoveryProcess"):]
        handler = handler[:handler.index("\n    }\n")]
        self.assertIn('if (recoveryProcess.command[1] === "restore" && recoveryProcess.command.length > 2) {\n                        if (result.complete) root.recoverySetAside = ""\n                        else if (result.launched) root.recoverySetAsideLeft = result.error || result.message\n                    }', handler)
        self.assertEqual(SHELL.count('recoverySetAside = ""'), 1)
        self.assertEqual(len(re.findall(r"recoverySetAsideLeft = (?!\"\")", SHELL)), 1)
        self.assertNotIn("session.json", next(line for line in lines if line.startswith("Text { id: recoveryAside;")))
        # Restore with a file argument is not an automatic command, so automatic saving waits while it runs.
        self.assertIn('recoveryWorking: recoveryProcess.running && !String(recoveryProcess.command[1]).startsWith("auto-")', SHELL)
        # The smoke's way in: the same function, and the path the shell holds.
        self.assertIn('function reopen_set_aside(): void { root.reopenSetAside() }', lines)
        self.assertRegex(SHELL, r"set_aside: root\.recoverySetAside, connected: controller\.connected \}\)")

    def test_the_controller_is_told_on_connecting_and_on_every_change(self):
        self.assertRegex(SHELL, r"if \(connected\) \{ write\('\{\"watch\": true, \"auto_save\": ' \+ root\.autoSaveWanted \+ '\}\\n'\)")
        handler = SHELL[SHELL.index("onAutoSaveWantedChanged:"):]
        handler = handler[:handler.index("\n    }\n")]
        self.assertIn("controller.write(JSON.stringify({ auto_save: autoSaveWanted }) + ", handler)

if __name__ == "__main__":
    unittest.main()
