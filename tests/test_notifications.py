from pathlib import Path
import unittest

QML = (Path(__file__).parents[1] / "session/shell.qml").read_text()


def notification_panel():
    start = QML.index('    SystemPanel {\n        shown: root.notificationsOpen; title: "Notifications"\n')
    return QML[start:QML.index("\n    }\n", start)]


class NotificationShellTest(unittest.TestCase):
    def test_notification_surface_is_bounded_and_plain_text(self):
        self.assertEqual(QML.count("NotificationServer {"), 1)
        self.assertIn("Qt.callLater(root.trimTrackedNotifications)", QML)
        self.assertLess(QML.index("notification.tracked = true"), QML.index("Qt.callLater(root.trimTrackedNotifications)"))
        self.assertIn("const tracked = [...notificationServer.trackedNotifications.values]", QML)
        self.assertIn("for (let i = 0; i < tracked.length - 20; i++) tracked[i].dismiss()", QML)
        self.assertNotIn("trackedNotifications.count", QML)
        self.assertNotIn("trackedNotifications.get(", QML)
        self.assertNotIn("doNotDisturb", QML)
        panel = notification_panel()
        self.assertIn("onCloseRequested: root.notificationsOpen = false", panel)
        self.assertIn("text: modelData.body; textFormat: Text.PlainText", panel)
        self.assertIn("text: modelData.summary; textFormat: Text.PlainText", panel)
        # The Island's bell opens them, in work, Overview and Desk alike (Desk has no tool of its own for them).
        self.assertIn('glyph: "bell"; open: root.notificationsOpen', QML)
        self.assertIn("onClicked: root.toggleNotifications()", QML)

    def test_newest_first_with_dismiss_clear_all_and_touch_sized_actions(self):
        panel = notification_panel()
        self.assertIn("model: [...notificationServer.trackedNotifications.values].reverse()", panel)
        self.assertIn('Accessible.name: "Dismiss notification"; onClicked: modelData.dismiss()', panel)
        self.assertIn("onClicked: [...notificationServer.trackedNotifications.values].forEach(n => n.dismiss())", panel)
        actions = panel[panel.index("model: modelData.actions"):]
        self.assertIn("implicitHeight: 44", actions)
        self.assertIn("onClicked: modelData.invoke()", actions)

    def test_empty_state(self):
        panel = notification_panel()
        self.assertIn("visible: notificationServer.trackedNotifications.values.length === 0", panel)
        self.assertIn('text: "No notifications"', panel)


if __name__ == "__main__":
    unittest.main()
