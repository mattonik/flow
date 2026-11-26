"""Flow's Screenshot portal backend (session/screenshot_portal.py), the parts that need no bus."""

from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "session"))
import screenshot_portal as portal  # noqa: E402


class ScreenshotPortalTest(unittest.TestCase):
    def test_interfaces_match_the_portal_spec(self):
        iface = ET.fromstring(portal.XML).find("interface")
        self.assertEqual(iface.get("name"), "org.freedesktop.impl.portal.Screenshot")
        for method in ("Screenshot", "PickColor"):
            args = [(a.get("type"), a.get("direction")) for a in iface.find(f"method[@name='{method}']")]
            self.assertEqual(args, [("o", "in"), ("s", "in"), ("s", "in"), ("a{sv}", "in"), ("u", "out"), ("a{sv}", "out")])
        self.assertEqual(iface.find("property").get("name"), "version")
        request = ET.fromstring(portal.REQUEST_XML).find("interface")
        self.assertEqual((request.get("name"), request.find("method").get("name")), ("org.freedesktop.impl.portal.Request", "Close"))

    def test_consent_names_the_app_and_the_whole_screen(self):
        text = portal.consent("PortalProbe")
        self.assertIn("PortalProbe", text["subtitle"])
        self.assertIn("whole screen", text["subtitle"])
        self.assertEqual((text["deny_label"], text["grant_label"]), ("Cancel", "Take Screenshot"))
        self.assertTrue(portal.consent("")["subtitle"].startswith("An app "))

    def test_an_app_without_a_desktop_entry_is_named_by_its_id(self):
        class Info:
            @staticmethod
            def new(name):
                if name == "org.example.Known.desktop":
                    return type("Found", (), {"get_display_name": lambda self: "Known App"})()
                raise TypeError("constructor returned NULL")  # PyGObject's answer to GDesktopAppInfo's NULL

        class Gio:
            DesktopAppInfo = Info

        class GLib:
            class Error(Exception):
                pass
        backend = portal.Backend.__new__(portal.Backend)
        backend.Gio, backend.GLib = Gio, GLib
        self.assertEqual(backend.app_name("io.github.mattonik.PortalProbe"), "io.github.mattonik.PortalProbe")
        self.assertEqual(backend.app_name("org.example.Known"), "Known App")
        self.assertEqual(backend.app_name(""), "")

    def test_answer_codes_are_the_portal_spec(self):
        self.assertEqual((portal.SUCCESS, portal.CANCELLED, portal.OTHER), (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
