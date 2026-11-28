"""The notification banner, read from the shell's source.

When it shows, for how long, what replaces it and what closes it are tested on a running shell by
tests/niri_notification_banner_smoke.py in the guest. These checks of the source text cover what that
smoke cannot see or reach:
- The banner never takes the keyboard or a click (ADR-0014: it sits over an app), and Super+N opens the panel.
- Exactly which surfaces hide it (the visibility terms, in any order), the opened Island among them.
- The card carries the alert role.
- New and replaced notifications both reach showBanner.
"""
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent))
from test_system_panels import SHELL, block_at, component, own  # noqa: E402

NIRI = (Path(__file__).parents[1] / "session/niri.kdl").read_text()


def banner():
    return block_at(SHELL.rindex("    PanelWindow {", 0, SHELL.index("        id: notificationBanner")))


def object_with(marker):
    """The object whose ``id:`` line is ``marker``."""
    at = SHELL.index(marker)
    return block_at(SHELL.rindex("\n", 0, SHELL.rindex("{\n", 0, at)) + 1)


def visible_terms():
    expression = re.search(r"\n {8}visible: (.+?)\n {8}anchors", own(banner()), re.S).group(1)
    return sorted(term.strip() for term in expression.replace("\n", " ").split("&&"))


class NotificationBannerTest(unittest.TestCase):
    def test_it_never_takes_the_keyboard_and_super_n_opens_the_panel(self):
        self.assertIn("WlrLayershell.keyboardFocus: WlrKeyboardFocus.None", own(banner()))
        self.assertIn('Super+N { spawn "@shell@" "ipc" "-p" "@shellConfig@" "call" "desk" "notifications"; }', NIRI)

    def test_it_takes_no_clicks(self):
        # An empty input region: a click on the banner reaches the app beneath it.
        self.assertIn("mask: Region {}", banner())
        self.assertNotRegex(banner(), r"\b(MouseArea|FlowButton|Button|TapHandler)\s*\{")

    def test_exactly_the_plain_work_view_without_a_panel_shows_it(self):
        self.assertEqual(visible_terms(), ["!!notification", "root.workViewOnly"])
        terms = lambda name: sorted(term.strip() for term in re.search(
            rf"readonly property bool {name}: (.+?)\n {{4}}\S", SHELL, re.S).group(1).replace("\n", " ").split("&&"))
        self.assertEqual(terms("workViewOnly"), ["!islandOpen", "restingView"])
        self.assertEqual(terms("restingView"), sorted([
            "!deskOpen", "!switcherOpen", "!spacesOverviewOpen", "!overviewOpen", "!searchOpen", "!appMenuOpen",
            "!quickSettingsOpen", "!notificationsOpen"]))

    def test_it_sits_where_the_panels_sit(self):
        # Near the top edge, or below a notched screen's band, as a panel opened without the Island.
        self.assertIn("margins.top: root.topInset(screen)", own(banner()))
        self.assertIn("margins.top: root.panelTop", own(component("SystemPanel")))
        self.assertIn(": topInset(panelScreen)", SHELL[SHELL.index("readonly property real panelTop:"):][:200])

    def test_the_card_has_the_alert_role(self):
        card = own(object_with("id: bannerCard"))
        self.assertIn("Accessible.role: Accessible.AlertMessage", card)
        self.assertNotRegex(card, r"\b(scale|opacity|visible):")

    def test_new_and_replaced_notifications_reach_show_banner(self):
        handler = SHELL[SHELL.index("onNotification: notification => {"):]
        handler = handler[:handler.index("\n        }\n")]
        self.assertRegex(handler, r"\n {12}root\.showBanner\(notification\)\n")  # its own call, not only the lambda's
        self.assertIn("notification.summaryChanged.connect(changed)", handler)
        self.assertIn("notification.bodyChanged.connect(changed)", handler)

    def test_its_text_is_plain_and_bounded(self):
        texts = re.findall(r"text: notificationBanner\.notification \? notificationBanner\.notification\.(summary|body) "
                           r": \"\"; textFormat: Text\.PlainText", banner())
        self.assertEqual(texts, ["summary", "body"])
        self.assertEqual(banner().count("maximumLineCount: 2"), 2)


if __name__ == "__main__":
    unittest.main()
