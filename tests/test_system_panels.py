"""The Island's system panels (quick settings and notifications), read from the shell's source.

This harness cannot render QML, so these are source checks, scoped to the component or panel each
claim is about:
- Both panels are SystemPanels, which hang below the Island instead of over it, and both close on
  Escape and on their close button, each clearing its own flag (not the other panel's).
- Quick settings' rows open what they name, and the volume slider sets the volume without losing
  changes made elsewhere.
- Every control in the panels is at least 44 px, the brand's touch minimum.
How the panels look is checked by screenshots in the guest (docs/IMPLEMENTATION_STATUS.md).
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


def component(name):
    return block_at(SHELL.index(f"    component {name}: "))


def panel(flag):
    return block_at(SHELL.index(f"    SystemPanel {{\n        shown: root.{flag};"))


def objects(block, type_name):
    """Every ``type_name`` object inside ``block``."""
    base = SHELL.index(block)
    return [block_at(base + m.start()) for m in re.finditer(rf"\b{type_name} \{{", block)]


class SystemPanelTest(unittest.TestCase):
    def test_a_panel_hangs_below_the_island(self):
        island = component("SystemIsland")
        island_height = int(re.search(r"implicitHeight: (\d+)", island).group(1))
        top = int(re.search(r"margins\.top: (\d+)", component("SystemPanel")).group(1))
        self.assertGreaterEqual(top, island_height + 8)

    def test_escape_and_the_close_button_ask_to_close(self):
        system_panel = component("SystemPanel")
        self.assertIn("Keys.onEscapePressed: systemPanel.closeRequested()", system_panel)
        at = SHELL.index(system_panel) + system_panel.index("id: panelClose")
        close = block_at(SHELL.rfind("FlowButton {", 0, at))
        self.assertIn("onClicked: systemPanel.closeRequested()", close)
        self.assertIn("visible: shown", system_panel)

    def test_each_panel_closes_itself(self):
        for flag in ("quickSettingsOpen", "notificationsOpen"):
            self.assertIn(f"onCloseRequested: root.{flag} = false", panel(flag))


class QuickSettingsTest(unittest.TestCase):
    def test_rows_open_what_they_name(self):
        rows = {re.search(r'text: "([^"]+)"', row).group(1): row for row in objects(panel("quickSettingsOpen"), "PanelRow")}
        self.assertEqual(list(rows), ["Network", "Edit connections", "On-screen keyboard", "Sound"])
        self.assertIn('root.launchApp(["flow-wifi-settings"])', rows["Network"])
        self.assertIn('root.launchApp(["nm-connection-editor"])', rows["Edit connections"])
        self.assertIn('root.launchApp(["flow-keyboard"])', rows["On-screen keyboard"])
        self.assertIn("interactive: false", rows["Sound"])
        self.assertNotIn("onClicked", rows["Sound"])

    def test_the_volume_slider_sets_the_volume_and_follows_changes_made_elsewhere(self):
        slider = component("VolumeSlider")
        self.assertIn("onMoved: root.setVolume(value)", slider)
        # A plain `value:` binding would end on the first move; the Binding element survives it.
        self.assertIn("Binding on value { value: root.audioAvailable ? root.audioSink.audio.volume : 0; when: !volumeSlider.pressed }", slider)
        self.assertNotRegex(slider, r"\n {8}value:")
        self.assertIn("VolumeSlider {", panel("quickSettingsOpen"))
        self.assertIn("onClicked: root.toggleMute()", panel("quickSettingsOpen"))


class TouchTargetTest(unittest.TestCase):
    def test_every_panel_control_is_a_full_touch_target(self):
        self.assertGreaterEqual(int(re.search(r"implicitHeight: (\d+)", component("PanelRow")).group(1)), 44)
        self.assertGreaterEqual(int(re.search(r"implicitHeight: (\d+)", component("VolumeSlider")).group(1)), 44)
        blocks = [component("SystemPanel"), panel("quickSettingsOpen"), panel("notificationsOpen")]
        buttons = [b for block in blocks for b in objects(block, "FlowButton")]
        self.assertGreaterEqual(len(buttons), 5)  # close, mute, play, clear all, dismiss, actions
        for button in buttons:
            own = button.split("\n", 4)[:4]
            heights = [int(h) for h in re.findall(r"implicitHeight: (\d+)", "\n".join(own))]
            self.assertTrue(heights, own[0] + own[1])
            self.assertTrue(all(h >= 44 for h in heights), own[1])
            for width in re.findall(r"\bwidth: (\d+);", "\n".join(own)):
                self.assertGreaterEqual(int(width), 44, own[1])


if __name__ == "__main__":
    unittest.main()
