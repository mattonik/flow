from pathlib import Path
import unittest


class ShellTextTest(unittest.TestCase):
    def test_app_supplied_labels_and_errors_are_plain_text(self):
        qml = (Path(__file__).parents[1] / "session/shell.qml").read_text()
        # These shared and direct renderers receive application or process data.
        for expression in ("control.text", "root.spacesError", "root.recoveryMessage",
                           'root.appMenu ? root.appMenu.name : "Application"', "root.appMenuError"):
            with self.subTest(expression=expression):
                renderers = [line for line in qml.splitlines() if "Text {" in line and f"text: {expression}" in line]
                if expression == "control.text":
                    renderers = [line for line in qml.splitlines() if f"text: {expression}" in line]
                self.assertTrue(renderers)
                for renderer in renderers:
                    self.assertIn("textFormat: Text.PlainText", renderer)

    def test_desk_heading_keeps_intentional_rich_text(self):
        qml = (Path(__file__).parents[1] / "session/shell.qml").read_text()
        self.assertRegex(qml, r'(?s)text: "Everything<br>.*?textFormat: Text\.RichText')

    def test_spaces_errors_have_accessible_dismiss_controls_in_desk_and_switcher(self):
        qml = (Path(__file__).parents[1] / "session/shell.qml").read_text()
        self.assertEqual(qml.count('Accessible.name: "Dismiss spaces error"'), 2)
        self.assertEqual(qml.count('onClicked: root.spacesError = ""'), 2)
        self.assertGreaterEqual(qml.count('implicitHeight: 44; text: "Dismiss"'), 2)
        self.assertIn('switcherSpacesError.visible ? switcherSpacesError.implicitHeight + 8 : 0', qml)


if __name__ == "__main__":
    unittest.main()
