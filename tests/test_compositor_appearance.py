"""Niri and the lock screen take Floe's colours from the tokens and follow the Desk's appearance switch.

- The generated Niri and swaylock files match the tokens, role by role, and meet the audit's contrast.
- niri.kdl holds no colour of its own: it includes Polar Night, then the per-user file the installed
  shell writes, at the same path the shell writes it.
- session/flow-lock.sh runs for real with a stand-in swaylock, and session/flow-niri-appearance.sh
  for real in a temporary home.
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
                                     "background-color": role(mode, "canvas"), "backdrop-color": role(mode, "border")})
                self.assertGreaterEqual(contrast(c["active-color"], c["background-color"]), 3.0)
                self.assertGreaterEqual(contrast(c["inactive-color"], c["background-color"]), 3.0)
                # In Overview the workspaces are the targets; they must stand out from the backdrop.
                self.assertGreaterEqual(contrast(c["backdrop-color"], c["background-color"]), 3.0)

    def test_lock_feedback_is_legible(self):
        for mode in ("dark", "light"):
            with self.subTest(mode=mode):
                c = swaylock(mode)
                self.assertEqual(c["color"], role(mode, "canvas").lstrip("#").lower())
                for text, inside in (("text-color", "inside-color"), ("text-ver-color", "inside-ver-color"),
                                     ("text-wrong-color", "inside-wrong-color"), ("text-clear-color", "inside-clear-color"),
                                     ("text-caps-lock-color", "inside-caps-lock-color"), ("layout-text-color", "layout-bg-color")):
                    self.assertGreaterEqual(contrast(c[text], c[inside]), 4.5, text)
                # Typing, checking and a wrong password show on the ring; each must stand out from it and the canvas.
                for signal in ("key-hl-color", "ring-ver-color", "ring-wrong-color", "ring-caps-lock-color", "caps-lock-key-hl-color"):
                    self.assertGreaterEqual(contrast(c[signal], c["color"]), 3.0, signal)
                    self.assertGreaterEqual(contrast(c[signal], c["ring-color"]), 3.0, signal)


    def test_every_lock_option_exists_in_swaylock(self):
        # swaylock 1.8.6's long options (swaylock --help). It stops reading a configuration at the first
        # unknown line and still starts, so a typo would leave the lock half themed.
        known = set("""config color debug ignore-empty-password show-failed-attempts daemonize ready-fd help image
            show-keyboard-layout hide-keyboard-layout disable-caps-lock-text indicator-caps-lock scaling tiling
            no-unlock-indicator version bs-hl-color caps-lock-bs-hl-color caps-lock-key-hl-color font font-size
            indicator-idle-visible indicator-radius indicator-thickness indicator-x-position indicator-y-position
            inside-color inside-clear-color inside-caps-lock-color inside-ver-color inside-wrong-color key-hl-color
            layout-bg-color layout-border-color layout-text-color line-color line-clear-color line-caps-lock-color
            line-ver-color line-wrong-color line-uses-inside line-uses-ring ring-color ring-clear-color
            ring-caps-lock-color ring-ver-color ring-wrong-color separator-color text-color text-clear-color
            text-caps-lock-color text-ver-color text-wrong-color""".split())
        for mode in ("dark", "light"):
            lines = (ROOT / f"design/generated/swaylock-{mode}.conf").read_text().splitlines()
            options = [line.split("=", 1)[0] for line in lines if line and not line.startswith("#")]
            self.assertEqual([o for o in options if o not in known], [], mode)
            self.assertIn("font-size", options)


class NiriConfigTest(unittest.TestCase):
    def test_niri_kdl_holds_no_colour_of_its_own(self):
        self.assertNotRegex(NIRI, r"#[0-9A-Fa-f]{3,8}\b|\brgba?\(|-c\s+\"?[0-9A-Fa-f]{6}")

    def test_niri_includes_polar_night_then_the_users_appearance(self):
        base = NIRI.index('include "@niriAppearance@"')
        user = NIRI.index('include optional=true "~/.config/flow/niri-appearance.kdl"')
        self.assertLess(base, user)  # later includes override earlier ones
        self.assertIn("niriAppearance = ../design/generated/niri-dark.kdl;", NIX)

    def test_the_sync_writes_the_file_niri_includes(self):
        included = re.search(r'include optional=true "~/([^"]+)"', NIRI).group(1)
        script = (ROOT / "session/flow-niri-appearance.sh").read_text()
        self.assertIn(f'link="$HOME/{included}"', script)
        for mode in ("dark", "light"):
            self.assertIn(f'{{ name = "niri-{mode}.kdl"; path = ../design/generated/niri-{mode}.kdl; }}', NIX)

    def test_the_installed_shell_syncs_at_startup_and_on_the_switch(self):
        self.assertIn('if (Quickshell.shellDir === "/etc/flow") Quickshell.execDetached(["flow-niri-appearance"])', SHELL)
        startup = SHELL[SHELL.index("    Component.onCompleted: {"):SHELL.index("    function syncCompositorAppearance()")]
        self.assertIn("root.syncCompositorAppearance()", startup)
        self.assertLess(startup.index('if (Quickshell.shellDir !== "/etc/flow") return'),
                        startup.index("root.syncCompositorAppearance()"))
        self.assertIn("appearancePreferences.sync(); root.syncCompositorAppearance()", SHELL)
        self.assertIn("niriAppearanceSync", NIX.split("environment.systemPackages")[1].split("]")[0])

    def test_locking_goes_through_the_themed_wrapper_without_colour_arguments(self):
        self.assertIn('"lock" "@lock@ -f" "before-sleep" "@lock@ -f"', NIRI)
        self.assertIn('{ spawn "@lock@"; }', NIRI)
        self.assertIn("lock = lib.getExe flowLock;", NIX)
        self.assertIn("export FLOW_SWAYLOCK_BIN=${lib.getExe pkgs.swaylock} FLOW_SWAYLOCK_THEMES=${../design/generated}", NIX)
        self.assertTrue((ROOT / "design/generated/swaylock-dark.conf").exists())


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


    def test_a_users_own_swaylock_configuration_wins(self):
        (self.root / "config/floe-appearance.ini").write_text("[General]\nlightMode=true\n")
        (self.root / "config/swaylock").mkdir()
        (self.root / "config/swaylock/config").write_text("color=000000\n")
        self.assertEqual(self.lock("-f"), ["-f"])


class NiriAppearanceSyncTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="flow-niri-"))
        self.addCleanup(shutil.rmtree, self.root)
        self.home = self.root / "home"
        (self.home / ".config").mkdir(parents=True)
        self.installed = self.root / "etc-flow"
        self.installed.mkdir()
        for mode in ("dark", "light"):
            (self.installed / f"niri-{mode}.kdl").write_text(f"// {mode}\n")
        self.link = self.home / ".config/flow/niri-appearance.kdl"
        self.env = dict(os.environ, HOME=str(self.home), FLOW_SHELL_DIR=str(self.installed), XDG_RUNTIME_DIR=str(self.root))
        self.env.pop("XDG_CONFIG_HOME", None)

    def sync(self):
        subprocess.run(["sh", str(ROOT / "session/flow-niri-appearance.sh")], env=self.env, check=True, timeout=10)
        return os.readlink(self.link)

    def appearance(self, light):
        (self.home / ".config/floe-appearance.ini").write_text(f"[General]\nlightMode={'true' if light else 'false'}\n")

    def test_the_link_follows_the_setting_it_reads_when_it_runs(self):
        self.assertEqual(self.sync(), str(self.installed / "niri-dark.kdl"))
        self.appearance(True)
        self.assertEqual(self.sync(), str(self.installed / "niri-light.kdl"))
        self.assertEqual(self.link.read_text(), "// light\n")
        self.appearance(False)
        self.assertEqual(self.sync(), str(self.installed / "niri-dark.kdl"))

    def test_an_unchanged_setting_leaves_the_link_alone(self):
        self.sync()
        before = os.lstat(self.link).st_ino
        self.sync()
        self.assertEqual(os.lstat(self.link).st_ino, before)  # Niri is not made to reload for nothing

    def test_leftovers_and_an_old_copy_never_block_it(self):
        self.link.parent.mkdir(parents=True)
        self.link.write_text("// a copy from an older Flow\n")
        self.link.with_name("niri-appearance.kdl.new").write_text("left behind")
        os.chmod(self.link.with_name("niri-appearance.kdl.new"), 0o444)
        self.appearance(True)
        self.assertEqual(self.sync(), str(self.installed / "niri-light.kdl"))
        self.assertFalse(self.link.with_name("niri-appearance.kdl.new").exists())


if __name__ == "__main__":
    unittest.main()
