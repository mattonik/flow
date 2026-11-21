"""The Island's system panels (quick settings and notifications), read from the shell's source.

This harness cannot render QML or send input, so these are checks of the source text, scoped to
the component or panel each claim is about. They pin the wiring a refactor could silently drop:
- Both panels are SystemPanels: 12 px below the Island, capped to the screen and scrolling beyond
  it, holding the keyboard while shown, and closing on Escape and on their close button, each
  clearing its own flag (not the other panel's).
- Quick settings' rows open what they name and take focus; the Sound row is not a button; the
  volume slider steps 5%, sets the volume, and follows changes made elsewhere without restoring a
  stale value when pressed (Qt 6's default restore mode would).
- Every control in the panels is at least 44 px, the brand's touch minimum.
How the panels look and respond is checked by screenshots in the guest (docs/IMPLEMENTATION_STATUS.md).
"""
from pathlib import Path
import re
import unittest

SHELL = (Path(__file__).parents[1] / "session/shell.qml").read_text()


def block_at(start):
    """The QML object whose opening brace is the first one at or after ``start``."""
    depth = 0
    for i in range(SHELL.index("{", start), len(SHELL)):
        depth += {"{": 1, "}": -1}.get(SHELL[i], 0)
        if depth == 0:
            return SHELL[start:i + 1]
    raise AssertionError("unbalanced braces")


def own(block):
    """The object's own text: its lines without any child object's (or JS block's) body."""
    out, depth = [], 0
    for ch in block[block.index("{") + 1:-1]:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def component(name):
    return block_at(SHELL.index(f"    component {name}: "))


def panel(flag):
    return block_at(SHELL.rindex("    SystemPanel {", 0, SHELL.index(f"        shown: root.{flag};")))


def objects(block, type_name):
    """Every ``type_name`` object inside ``block``."""
    base = SHELL.index(block)
    return [block_at(base + m.start()) for m in re.finditer(rf"\b{type_name} \{{", block)]


def function_body(name):
    return block_at(SHELL.index(f"    function {name}("))


class SystemPanelTest(unittest.TestCase):
    def test_a_panel_hangs_12_px_below_the_island_and_fits_the_screen(self):
        island_height = int(re.search(r"implicitHeight: (\d+)", own(component("SystemIsland"))).group(1))
        system_panel = own(component("SystemPanel"))
        self.assertEqual(int(re.search(r"margins\.top: (\d+)", system_panel).group(1)), island_height + 12)
        self.assertIn("implicitHeight: Math.min(screen.height - 80, panelBody.implicitHeight", system_panel)
        scroller = block_at(SHELL.rindex("ScrollView {", 0, SHELL.index("id: panelScroller")))
        self.assertIn("contentHeight: panelBody.implicitHeight", own(scroller))

    def test_a_shown_panel_holds_the_keyboard_and_escape_or_close_ask_to_close(self):
        system_panel = component("SystemPanel")
        self.assertIn("visible: shown", own(system_panel))
        self.assertIn("WlrLayershell.keyboardFocus: shown ? WlrKeyboardFocus.Exclusive", own(system_panel))
        scope = own(block_at(SHELL.index("FocusScope {", SHELL.index(system_panel))))
        self.assertIn("focus: systemPanel.shown", scope)
        self.assertIn("Keys.onEscapePressed: systemPanel.closeRequested()", scope)
        close = block_at(SHELL.rindex("FlowButton {", 0, SHELL.index("id: panelClose")))
        self.assertIn("onClicked: systemPanel.closeRequested()", own(close))
        # Desk, under a panel, lets it have the keys.
        self.assertIn("root.notificationsOpen || root.quickSettingsOpen || root.searchOpen ? WlrKeyboardFocus.None", SHELL)

    def test_each_panel_closes_itself(self):
        for flag in ("quickSettingsOpen", "notificationsOpen"):
            self.assertIn(f"onCloseRequested: root.{flag} = false", own(panel(flag)))


class QuickSettingsTest(unittest.TestCase):
    def test_rows_open_what_they_name_and_take_focus(self):
        quick = panel("quickSettingsOpen")
        rows = {re.search(r'text: "([^"]+)"', row).group(1): row for row in objects(quick, "PanelRow")}
        self.assertEqual(list(rows), ["Network", "Edit connections", "On-screen keyboard"])
        self.assertIn('root.quickSettingsOpen = false; root.launchApp(["flow-wifi-settings"])', rows["Network"])
        self.assertIn('root.quickSettingsOpen = false; root.launchApp(["nm-connection-editor"])', rows["Edit connections"])
        self.assertIn('root.launchApp(["flow-keyboard"]); root.quickSettingsOpen = false', rows["On-screen keyboard"])
        row = own(component("PanelRow"))
        self.assertIn("focusPolicy: Qt.StrongFocus", row)
        self.assertIn("Keys.onReturnPressed: panelRow.clicked()", row)
        self.assertIn('PanelValue { width: parent.width; text: "Sound"', quick)
        self.assertTrue(component("PanelValue").startswith("    component PanelValue: Item {"))

    def test_the_volume_slider_steps_sets_and_follows_the_volume(self):
        slider = own(component("VolumeSlider"))
        self.assertIn("from: 0; to: 1; stepSize: 0.05", slider)
        self.assertIn("focusPolicy: Qt.StrongFocus", slider)
        self.assertIn("onMoved: root.setVolume(value)", slider)
        binding = re.search(r"Binding on value \{([^}]*)\}", component("VolumeSlider")).group(1)
        self.assertIn("value: root.audioAvailable ? root.audioSink.audio.volume : 0", binding)
        self.assertIn("when: !volumeSlider.pressed", binding)
        self.assertIn("restoreMode: Binding.RestoreNone", binding)
        self.assertIn("audioSink.audio.volume = Math.max(0, Math.min(1, level))", function_body("setVolume"))
        self.assertIn("audioSink.audio.muted = !audioSink.audio.muted", function_body("toggleMute"))
        quick = panel("quickSettingsOpen")
        self.assertIn("VolumeSlider {", quick)
        self.assertIn("onClicked: root.toggleMute()", quick)


class TouchTargetTest(unittest.TestCase):
    def assert_size(self, block, name):
        props = own(block)
        heights = re.findall(r"\bimplicitHeight: ([^;\n]+)", props)
        self.assertTrue(heights, f"{name} has no height")
        for value in heights:
            self.assertTrue(value.isdigit() and int(value) >= 44, f"{name}: implicitHeight {value}")
        for value in re.findall(r"(?<![\w.])width: ([^;\n]+)", props):
            plain = value.isdigit() and int(value) >= 44
            self.assertTrue(plain or value.startswith(("Math.max(44,", "parent.width")), f"{name}: width {value}")

    def test_every_panel_control_is_a_full_touch_target(self):
        for name in ("PanelRow", "PanelValue", "VolumeSlider"):
            self.assert_size(component(name), name)
        blocks = [component("SystemPanel"), panel("quickSettingsOpen"), panel("notificationsOpen")]
        buttons = [b for block in blocks for b in objects(block, "FlowButton")]
        self.assertGreaterEqual(len(buttons), 6)  # close, mute, play, clear all, dismiss, actions
        for button in buttons:
            self.assert_size(button, button.split("\n", 2)[1].strip())


if __name__ == "__main__":
    unittest.main()
