"""The notification banner, read from the shell's source.

When it shows, for how long, and what closes it are tested on a running shell by
tests/niri_notification_banner_smoke.py in the guest. These checks cover what that smoke cannot see:
- The banner never takes the keyboard, so a notification cannot pull typing away from an app.
- Its dismiss button is a full 44 px target, and it is announced to assistive technology.
- It sits where the panels sit, so a panel opening hides it instead of overlapping it.
"""
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent))
from test_system_panels import SHELL, block_at, component, own  # noqa: E402


def banner():
    return block_at(SHELL.rindex("    PanelWindow {", 0, SHELL.index("        id: notificationBanner")))


class NotificationBannerTest(unittest.TestCase):
    def test_it_never_takes_the_keyboard(self):
        window = own(banner())
        self.assertIn("WlrLayershell.keyboardFocus: WlrKeyboardFocus.None", window)
        dismiss = block_at(SHELL.rindex("FlowButton {", 0, SHELL.index("id: bannerDismiss")))
        self.assertIn("focusPolicy: Qt.NoFocus", own(dismiss))

    def test_its_dismiss_is_a_full_target_and_dismisses(self):
        dismiss = own(block_at(SHELL.rindex("FlowButton {", 0, SHELL.index("id: bannerDismiss"))))
        self.assertIn("width: 44; implicitHeight: 44", dismiss)
        self.assertIn('Accessible.name: "Dismiss notification"', dismiss)
        self.assertIn("if (notification) notification.dismiss()", banner())

    def test_it_is_announced(self):
        self.assertIn("Accessible.role: Accessible.AlertMessage", banner())

    def test_it_sits_where_the_panels_sit_and_gives_way_to_them(self):
        top = lambda block: int(re.search(r"margins\.top: (\d+)", own(block)).group(1))
        self.assertEqual(top(banner()), top(component("SystemPanel")))
        self.assertIn("&& !root.notificationsOpen && !root.quickSettingsOpen && !root.searchOpen", own(banner()))

    def test_its_text_is_plain_and_bounded(self):
        texts = re.findall(r"text: notificationBanner\.notification \? notificationBanner\.notification\.(summary|body) "
                           r": \"\"; textFormat: Text\.PlainText", banner())
        self.assertEqual(texts, ["summary", "body"])
        self.assertEqual(banner().count("maximumLineCount: 2"), 2)


if __name__ == "__main__":
    unittest.main()
