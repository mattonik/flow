"""Helpers of the simulated Wi-Fi smoke (tests/niri_wifi_hwsim_smoke.py): nmcli's terse fields, and which
radios are mac80211_hwsim's."""

import os
import subprocess
from pathlib import Path
import tempfile
import unittest

from niri_wifi_hwsim_smoke import descends_from, hwsim_radios, nm_fields


class NmFieldsTest(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(nm_fields("wlan0:wifi:disconnected:"), ["wlan0", "wifi", "disconnected", ""])

    def test_escaped_colon_and_backslash_stay_in_the_field(self):
        self.assertEqual(nm_fields(r"cafe\:wifi:WPA2:\\x"), ["cafe:wifi", "WPA2", "\\x"])

    def test_escaped_backslash_before_a_separator(self):
        self.assertEqual(nm_fields("a\\\\:b"), ["a\\", "b"])


class HwsimRadiosTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sys = Path(self.tmp.name)
        (self.sys / "class/ieee80211").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def phy(self, name, device, interfaces):
        target = self.sys / device
        (target / "net").mkdir(parents=True)
        for interface in interfaces:
            (target / "net" / interface).mkdir()
        os.makedirs(self.sys / "class/ieee80211" / name)
        os.symlink(target, self.sys / "class/ieee80211" / name / "device")

    def test_only_simulated_radios_in_phy_order(self):
        self.phy("phy10", "devices/virtual/mac80211_hwsim/hwsim1", ["wlan2"])
        self.phy("phy2", "devices/pci0000:00/0000:00:05.0/usb1/1-1/1-1:1.0", ["wlp0s5u1"])
        self.phy("phy9", "devices/virtual/mac80211_hwsim/hwsim0", ["wlan1"])
        self.assertEqual(list(hwsim_radios(self.sys).items()), [("phy9", ["wlan1"]), ("phy10", ["wlan2"])])

    def test_a_radio_without_an_interface_yet(self):
        target = self.sys / "devices/virtual/mac80211_hwsim/hwsim0"
        target.mkdir(parents=True)
        os.makedirs(self.sys / "class/ieee80211/phy0")
        os.symlink(target, self.sys / "class/ieee80211/phy0/device")
        self.assertEqual(hwsim_radios(self.sys), {"phy0": []})

    def test_no_wireless_at_all(self):
        self.assertEqual(hwsim_radios(self.sys / "nowhere"), {})


class DescendsFromTest(unittest.TestCase):
    @unittest.skipUnless(Path("/proc/self/stat").exists(), "needs Linux's /proc")
    def test_own_process_and_child(self):
        child = subprocess.Popen(["sleep", "5"])
        try:
            self.assertTrue(descends_from(os.getpid(), os.getpid()))
            self.assertTrue(descends_from(child.pid, os.getpid()))
            self.assertFalse(descends_from(os.getppid(), os.getpid()))
        finally:
            child.kill()
            child.wait()

    def test_a_process_that_is_gone(self):
        self.assertFalse(descends_from(2 ** 22 + 7, os.getpid()))


if __name__ == "__main__":
    unittest.main()
