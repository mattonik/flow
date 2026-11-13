from pathlib import Path
import unittest


class NotificationShellTest(unittest.TestCase):
    def test_notification_surface_is_bounded_and_plain_text(self):
        qml = (Path(__file__).parents[1] / "session/shell.qml").read_text()
        self.assertEqual(qml.count("NotificationServer {"), 1)
        self.assertIn("if (trackedNotifications.count > 20)", qml)
        self.assertNotIn("doNotDisturb", qml)
        self.assertIn("visible: root.notificationsOpen", qml)
        self.assertIn("text: modelData.body; textFormat: Text.PlainText", qml)
        self.assertIn("Keys.onEscapePressed: root.notificationsOpen = false", qml)
        self.assertIn('else if (modelData === "Notifications") root.toggleNotifications()', qml)
        self.assertIn("required property int index", qml)

    def test_actions_are_explicit_and_touch_sized(self):
        qml = (Path(__file__).parents[1] / "session/shell.qml").read_text()
        self.assertIn("implicitHeight: 44", qml)
        self.assertIn("onClicked: modelData.invoke()", qml)


if __name__ == "__main__":
    unittest.main()
