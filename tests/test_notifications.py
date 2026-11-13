from pathlib import Path
import unittest


class NotificationShellTest(unittest.TestCase):
    def test_notification_surface_is_bounded_and_plain_text(self):
        qml = (Path(__file__).parents[1] / "session/shell.qml").read_text()
        self.assertEqual(qml.count("NotificationServer {"), 1)
        self.assertIn("Qt.callLater(root.trimTrackedNotifications)", qml)
        self.assertLess(qml.index("notification.tracked = true"), qml.index("Qt.callLater(root.trimTrackedNotifications)"))
        self.assertIn("const tracked = [...notificationServer.trackedNotifications.values]", qml)
        self.assertIn("for (let i = 0; i < tracked.length - 20; i++) tracked[i].dismiss()", qml)
        self.assertNotIn("trackedNotifications.count", qml)
        self.assertNotIn("trackedNotifications.get(", qml)
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

    def test_empty_state_has_sized_scroll_content_and_mobile_targets(self):
        qml = (Path(__file__).parents[1] / "session/shell.qml").read_text()
        self.assertIn("contentWidth: availableWidth; contentHeight: notificationColumn.implicitHeight", qml)
        self.assertIn('visible: notificationServer.trackedNotifications.values.length === 0', qml)
        self.assertIn("index === notificationServer.trackedNotifications.values.length - 1", qml)
        self.assertIn("width: parent.width; height: 88; verticalAlignment: Text.AlignVCenter", qml)
        self.assertIn("FlowButton { width: 80; implicitHeight: 44; text: \"Close\"", qml)
        self.assertIn("screen.height - 80", qml)


if __name__ == "__main__":
    unittest.main()
