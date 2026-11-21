"""The launcher (fuzzel) in Floe's theme: legible in both appearances, and actually used.

- Every colour pair in the generated fuzzel files meets the accessibility audit's thresholds, with
  alpha blended over the panel the way fuzzel draws it.
- session/flow-fuzzel.sh runs for real under ``sh`` with a stand-in fuzzel that records its arguments.
"""
import configparser
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).parents[1]

WRAPPER = ROOT / "session/flow-fuzzel.sh"


def luminance(rgb):
    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def colour(value, under=None):
    """RRGGBBAA, blended over ``under`` when not opaque."""
    rgb = [int(value[i:i + 2], 16) for i in (0, 2, 4)]
    alpha = int(value[6:8], 16) / 255
    if alpha < 1:
        rgb = [round(c * alpha + u * (1 - alpha)) for c, u in zip(rgb, under)]
    return rgb


def theme(mode):
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string((ROOT / f"design/generated/fuzzel-{mode}.ini").read_text())
    return parser


class ContrastTest(unittest.TestCase):
    def test_every_pair_is_legible_in_both_appearances(self):
        import json
        tokens = json.loads((ROOT / "design/tokens/floe.tokens.json").read_text())
        for mode in ("dark", "light"):
            colours = theme(mode)["colors"]
            panel = colour(colours["background"], [0, 0, 0])
            canvas = colour(tokens["semantic"][mode]["canvas"]["$value"].lstrip("#") + "ff")
            pairs = {  # (foreground, background, minimum)
                "text": ("text", panel, 4.5), "input": ("input", panel, 4.5), "placeholder": ("placeholder", panel, 4.5),
                "match": ("match", panel, 4.5), "counter": ("counter", panel, 4.5),
                "selected text": ("selection-text", colour(colours["selection"], panel), 4.5),
                "selected match": ("selection-match", colour(colours["selection"], panel), 4.5),
                # Non-text: the selected row against the panel, the panel's edge against panel and canvas.
                "selection": ("selection", panel, 3.0), "border": ("border", panel, 3.0), "edge": ("border", canvas, 3.0),
            }
            for name, (role, background, minimum) in pairs.items():
                with self.subTest(mode=mode, pair=name):
                    self.assertGreaterEqual(contrast(colour(colours[role], background), background), minimum)

    def test_rows_are_touch_sized_in_pixels(self):
        for mode in ("dark", "light"):
            self.assertEqual(theme(mode)["main"]["line-height"], "44px")


class WrapperTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="flow-fuzzel-"))
        self.addCleanup(shutil.rmtree, self.root)
        self.config = self.root / "config"
        self.config.mkdir()
        self.calls = self.root / "calls"
        stub = self.root / "fuzzel"
        stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > {self.calls}\n')
        stub.chmod(0o755)
        self.env = dict(os.environ, FLOW_FUZZEL_BIN=str(stub), FLOW_FUZZEL_THEMES="/themes",
                        XDG_CONFIG_HOME=str(self.config))

    def run_wrapper(self, *args):
        subprocess.run(["sh", str(WRAPPER), *args], env=self.env, check=True, timeout=10)
        return self.calls.read_text().splitlines()

    def appearance(self, text):
        (self.config / "floe-appearance.ini").write_text(text)

    def test_the_theme_follows_the_appearance_setting_as_qt_writes_it(self):
        self.assertEqual(self.run_wrapper("--dmenu"), ["--config=/themes/fuzzel-dark.ini", "--dmenu"])
        self.appearance("[General]\nlightMode=true\nreduceMotion=false\n")
        self.assertEqual(self.run_wrapper("--dmenu"), ["--config=/themes/fuzzel-light.ini", "--dmenu"])
        self.appearance("[General]\nlightMode=false\nreduceMotion=false\n")
        self.assertEqual(self.run_wrapper(), ["--config=/themes/fuzzel-dark.ini"])

    def test_the_shell_writes_the_key_the_wrapper_reads(self):
        self.assertIn("property bool lightMode: false", (ROOT / "session/shell.qml").read_text())
        self.assertIn("grep -qx 'lightMode=true'", WRAPPER.read_text())

    def test_a_users_own_fuzzel_configuration_wins(self):
        (self.config / "fuzzel").mkdir()
        (self.config / "fuzzel/fuzzel.ini").write_text("[main]\n")
        self.appearance("[General]\nlightMode=true\n")
        self.assertEqual(self.run_wrapper("--dmenu", "--index"), ["--dmenu", "--index"])

    def test_the_launcher_uses_the_wrapper(self):
        module = (ROOT / "modules/flow.nix").read_text()
        self.assertEqual(module.count("${themedFuzzel}/bin/flow-fuzzel"), 1)


if __name__ == "__main__":
    unittest.main()
