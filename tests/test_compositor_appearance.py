"""Niri and the lock screen take Floe's colours from the tokens and follow the Desk's appearance switch.

- The generated Niri and swaylock files match the tokens, role by role, and meet the audit's contrast.
- niri.kdl holds no colour of its own: it includes Polar Night, then the per-user file the installed
  shell writes, at the same path the shell writes it.
- session/flow-lock.sh runs for real with a stand-in swaylock.
"""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).parents[1]
TOKENS = json.loads((ROOT / "design/tokens/floe.tokens.json").read_text())
NIRI = (ROOT / "session/niri.kdl").read_text()
NIX = (ROOT / "modules/flow.nix").read_text()
SHELL = (ROOT / "session/shell.qml").read_text()

spec = importlib.util.spec_from_file_location("floe_generator", ROOT / "design/scripts/generate_theme.py")
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


def rgb(value):
    value = value.lstrip("#")
    return [int(value[i:i + 2], 16) for i in (0, 2, 4)]


def contrast(a, b):
    def lum(c):
        c = [x / 255 for x in rgb(c)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    high, low = sorted((lum(a), lum(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def role(mode, name):
    return TOKENS["semantic"][mode][name]["$value"]


def niri_colours(mode):
    text = (ROOT / f"design/generated/niri-{mode}.kdl").read_text()
    found = dict(re.findall(r'(active-color|inactive-color|background-color|backdrop-color) "(#[0-9A-Fa-f]{6})"', text))
    return found


def swaylock(mode):
    lines = (ROOT / f"design/generated/swaylock-{mode}.conf").read_text().splitlines()
    return dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))


class GeneratedFilesTest(unittest.TestCase):
    def test_generated_files_are_in_sync(self):
        for mode in ("dark", "light"):
            self.assertEqual(generator.render_niri(TOKENS, mode), (ROOT / f"design/generated/niri-{mode}.kdl").read_text())
            self.assertEqual(generator.render_swaylock(TOKENS, mode),
                             (ROOT / f"design/generated/swaylock-{mode}.conf").read_text())

    def test_niri_colours_follow_their_roles_and_stand_out(self):
        for mode in ("dark", "light"):
            with self.subTest(mode=mode):
                c = niri_colours(mode)
                self.assertEqual(c, {"active-color": role(mode, "focus"), "inactive-color": role(mode, "border"),
                                     "background-color": role(mode, "canvas"), "backdrop-color": role(mode, "surface")})
                self.assertGreaterEqual(contrast(c["active-color"], c["background-color"]), 3.0)
                self.assertGreaterEqual(contrast(c["inactive-color"], c["background-color"]), 3.0)
                self.assertNotEqual(c["backdrop-color"].lower(), c["background-color"].lower())

    def test_lock_feedback_is_legible(self):
        for mode in ("dark", "light"):
            with self.subTest(mode=mode):
                c = swaylock(mode)
                self.assertEqual(c["color"], role(mode, "canvas").lstrip("#").lower())
                for text, inside in (("text-color", "inside-color"), ("text-ver-color", "inside-ver-color"),
                                     ("text-wrong-color", "inside-wrong-color"), ("text-clear-color", "inside-clear-color")):
                    self.assertGreaterEqual(contrast(c[text], c[inside]), 4.5, text)
                # Typing, checking and a wrong password show on the ring; each must stand out from it and the canvas.
                for signal in ("key-hl-color", "ring-ver-color", "ring-wrong-color"):
                    self.assertGreaterEqual(contrast(c[signal], c["color"]), 3.0, signal)
                    self.assertGreaterEqual(contrast(c[signal], c["ring-color"]), 3.0, signal)


class NiriConfigTest(unittest.TestCase):
    def test_niri_kdl_holds_no_colour_of_its_own(self):
        self.assertNotRegex(NIRI, r"#[0-9A-Fa-f]{3,8}\b|\brgba?\(|-c\s+\"?[0-9A-Fa-f]{6}")

    def test_niri_includes_polar_night_then_the_users_appearance(self):
        base = NIRI.index('include "@niriAppearance@"')
        user = NIRI.index('include optional=true "~/.config/flow/niri-appearance.kdl"')
        self.assertLess(base, user)  # later includes override earlier ones
        self.assertIn("niriAppearance = ../design/generated/niri-dark.kdl;", NIX)

    def test_the_shell_writes_the_file_niri_includes(self):
        included = re.search(r'include optional=true "~/([^"]+)"', NIRI).group(1)
        self.assertIn('d="$HOME/.config/flow"; t="$d/niri-appearance.kdl"', SHELL)
        self.assertEqual(included, ".config/flow/niri-appearance.kdl")
        for mode in ("dark", "light"):
            self.assertIn(f'{{ name = "niri-{mode}.kdl"; path = ../design/generated/niri-{mode}.kdl; }}', NIX)
        self.assertIn('"/niri-" + (appearancePreferences.lightMode ? "light" : "dark") + ".kdl"', SHELL)

    def test_locking_goes_through_the_themed_wrapper_without_colour_arguments(self):
        self.assertIn('"lock" "@lock@ -f" "before-sleep" "@lock@ -f"', NIRI)
        self.assertIn('{ spawn "@lock@"; }', NIRI)
        self.assertIn("lock = lib.getExe flowLock;", NIX)


class LockWrapperTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="flow-lock-"))
        self.addCleanup(shutil.rmtree, self.root)
        self.calls = self.root / "calls"
        stub = self.root / "swaylock"
        stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > {self.calls}\n')
        stub.chmod(0o755)
        (self.root / "config").mkdir()
        self.env = dict(os.environ, FLOW_SWAYLOCK_BIN=str(stub), FLOW_SWAYLOCK_THEMES="/themes",
                        XDG_CONFIG_HOME=str(self.root / "config"))

    def lock(self, *args):
        subprocess.run(["sh", str(ROOT / "session/flow-lock.sh"), *args], env=self.env, check=True, timeout=10)
        return self.calls.read_text().splitlines()

    def test_the_lock_follows_the_appearance_setting(self):
        self.assertEqual(self.lock("-f"), ["--config", "/themes/swaylock-dark.conf", "-f"])
        (self.root / "config/floe-appearance.ini").write_text("[General]\nlightMode=true\nreduceMotion=false\n")
        self.assertEqual(self.lock("-f"), ["--config", "/themes/swaylock-light.conf", "-f"])
        (self.root / "config/floe-appearance.ini").write_text("[General]\nlightMode=false\n")
        self.assertEqual(self.lock(), ["--config", "/themes/swaylock-dark.conf"])


if __name__ == "__main__":
    unittest.main()
