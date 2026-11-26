"""Flow's Screenshot portal backend (session/screenshot_portal.py), on a fake bus: who is asked, what is captured,
and that every request is answered once."""

import datetime
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "session"))
import screenshot_portal as portal  # noqa: E402


class Variant:
    def __init__(self, signature, value):
        self.signature, self.value = signature, value

    def unpack(self):
        return self.value


class GLibError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


class GLib:
    Variant = Variant
    Error = GLibError
    MAXINT = 2 ** 31 - 1
    timeouts = []

    @staticmethod
    def VariantType(signature):
        return signature

    @classmethod
    def timeout_add(cls, ms, step):
        cls.timeouts.append((ms, step))


class Gio:
    class DBusNodeInfo:
        @staticmethod
        def new_for_xml(_xml):
            return type("Node", (), {"interfaces": [object()]})()

    class DBusCallFlags:
        NONE = 0

    class DesktopAppInfo:
        @staticmethod
        def new(name):
            raise TypeError("constructor returned NULL")  # PyGObject's answer to GDesktopAppInfo's NULL

    class File:
        @staticmethod
        def new_for_path(path):
            return type("F", (), {"get_uri": lambda self: "file://" + path})()


class Reply:
    def __init__(self, value=None, error=None):
        self.value, self.error = value, error


class Connection:
    """Records calls and registered objects; a test answers a call by running its ``done`` with a Reply."""

    def __init__(self):
        self.calls, self.objects, self.next_id = [], {}, 1

    def register_object(self, path, _info, method, _get, _set):
        self.objects[self.next_id] = (path, method)
        self.next_id += 1
        return self.next_id - 1

    def unregister_object(self, object_id):
        del self.objects[object_id]

    def call(self, name, path, interface, method, args, reply_type, flags, timeout, cancellable, done):
        self.calls.append({"name": name, "path": path, "method": method, "args": args, "timeout": timeout, "done": done})

    def call_finish(self, reply):
        if reply.error:
            raise reply.error
        return Variant("", reply.value)

    def close(self, handle):
        """The broker's Close on the request at ``handle``."""
        path, method = next(v for v in self.objects.values() if v[0] == handle)
        method(self, ":1.1", path, "org.freedesktop.impl.portal.Request", "Close", None, Invocation())


class Invocation:
    def __init__(self):
        self.answers = []

    def return_value(self, value):
        self.answers.append(value.value if value is not None else None)


class BackendTest(unittest.TestCase):
    def setUp(self):
        GLib.timeouts = []
        self.connection = Connection()
        self.backend = portal.Backend(self.connection, Gio, GLib)
        self.captures = []
        self.backend.capture = self.captures.append  # each capture's ``done`` (path or None)
        self.invocation = Invocation()

    def ask(self, options, handle="/r/1"):
        self.backend.screenshot(handle, "org.example.App", "", options, self.invocation)

    def dialog(self):
        calls = [c for c in self.connection.calls if c["method"] == "AccessDialog"]
        self.assertEqual(len(calls), 1, "one consent dialog")
        return calls[0]

    def answer(self, call, value=None, error=None):
        call["done"](self.connection, Reply(value, error))

    def run_timeouts(self):
        while GLib.timeouts:
            GLib.timeouts.pop(0)[1]()

    def test_a_request_the_broker_did_not_check_asks_first(self):
        # The broker skips its own check while it has not read this backend's version: such a request arrives
        # without permission_store_checked, and must not reach the screen without the user.
        for options in ({}, {"interactive": False}, {"permission_store_checked": False}):
            with self.subTest(options=options):
                self.setUp()
                self.ask(options)
                self.assertEqual(self.captures, [])
                self.answer(self.dialog(), (2, {}))  # the user cancels
                self.run_timeouts()
                self.assertEqual(self.captures, [])
                self.assertEqual(self.invocation.answers, [(1, {})])

    def test_only_a_checked_request_that_is_not_interactive_is_taken_at_once(self):
        self.ask({"permission_store_checked": True})
        self.assertEqual([c["method"] for c in self.connection.calls], [])
        self.assertEqual(len(self.captures), 1)
        self.captures[0](Path("/home/u/Pictures/Screenshots/s.png"))
        self.assertEqual(self.invocation.answers[0][0], 0)
        self.assertEqual(self.invocation.answers[0][1]["uri"].value, "file:///home/u/Pictures/Screenshots/s.png")

    def test_an_interactive_request_always_asks(self):
        self.ask({"interactive": True, "permission_store_checked": True})
        call = self.dialog()
        self.assertEqual(call["timeout"], GLib.MAXINT, "the user decides with no time limit")
        self.assertIn("org.example.App", call["args"].value[4], "the dialog names the app the broker passed")
        self.answer(call, (0, {}))
        self.assertEqual(self.captures, [], "not before the dialog has had time to leave the screen")
        self.assertEqual([ms for ms, _ in GLib.timeouts], [portal.AFTER_DIALOG_MS])
        self.run_timeouts()
        self.assertEqual(len(self.captures), 1)
        self.captures[0](None)  # grim failed
        self.assertEqual(self.invocation.answers, [(2, {})])

    def test_a_close_while_the_user_decides_closes_the_dialog_and_captures_nothing(self):
        self.ask({"interactive": True})
        call = self.dialog()
        self.connection.close("/r/1")
        closes = [c for c in self.connection.calls if c["method"] == "Close"]
        self.assertEqual([c["path"] for c in closes], [call["args"].value[0]])
        self.assertEqual(self.invocation.answers, [(2, {})])
        self.answer(call, (0, {}))  # the dialog's answer, after all
        self.run_timeouts()
        self.assertEqual(self.captures, [])
        self.assertEqual(self.invocation.answers, [(2, {})], "answered once")

    def test_a_close_while_the_dialog_fades_captures_nothing(self):
        self.ask({"interactive": True})
        self.answer(self.dialog(), (0, {}))
        self.connection.close("/r/1")
        self.run_timeouts()
        self.assertEqual(self.captures, [])
        self.assertEqual(self.invocation.answers, [(2, {})])

    def test_a_failed_dialog_or_a_failing_step_answers_2(self):
        self.ask({"interactive": True})
        self.answer(self.dialog(), error=GLibError("Timeout was reached"))
        self.assertEqual(self.invocation.answers, [(2, {})])
        self.setUp()
        self.ask({"interactive": True})
        self.answer(self.dialog(), error=RuntimeError("anything else"))
        self.assertEqual(self.invocation.answers, [(2, {})], "an exception in a step still answers")
        self.assertEqual(self.connection.objects, {}, "and the request is gone")

    def test_pick_color(self):
        self.backend.pick_color("/r/2", "", "", {}, self.invocation)
        call = self.connection.calls[0]
        self.assertEqual((call["method"], call["timeout"]), ("PickColor", GLib.MAXINT))
        self.answer(call, ({"color": (0.5, 0.25, 1.0)},))
        self.assertEqual(self.invocation.answers[0][0], 0)
        self.assertEqual(self.invocation.answers[0][1]["color"].value, (0.5, 0.25, 1.0))
        self.setUp()
        self.backend.pick_color("/r/2", "", "", {}, self.invocation)
        self.answer(self.connection.calls[0], error=GLibError("GDBus.Error:org.freedesktop.DBus.Error.Failed: no color picked"))
        self.assertEqual(self.invocation.answers, [(1, {})])

    def test_an_app_without_a_desktop_entry_is_named_by_its_id(self):
        self.assertEqual(self.backend.app_name("io.github.mattonik.PortalProbe"), "io.github.mattonik.PortalProbe")
        self.assertEqual(self.backend.app_name(""), "")


class FilesTest(unittest.TestCase):
    def test_a_new_private_file_never_an_existing_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "Pictures" / "Screenshots"
            now = datetime.datetime(2026, 11, 26, 21, 5, 9)
            first, second = portal.new_screenshot(folder, now), portal.new_screenshot(folder, now)
            self.assertEqual((first.name, second.name),
                             ("Screenshot from 2026-11-26 21-05-09.png", "Screenshot from 2026-11-26 21-05-09-2.png"))
            self.assertEqual(stat.S_IMODE(os.stat(first).st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(os.stat(folder).st_mode) & 0o077, 0, "a folder it creates is the user's")


class InterfaceTest(unittest.TestCase):
    def test_interfaces_match_the_portal_spec(self):
        iface = ET.fromstring(portal.XML).find("interface")
        self.assertEqual(iface.get("name"), "org.freedesktop.impl.portal.Screenshot")
        for method in ("Screenshot", "PickColor"):
            args = [(a.get("type"), a.get("direction")) for a in iface.find(f"method[@name='{method}']")]
            self.assertEqual(args, [("o", "in"), ("s", "in"), ("s", "in"), ("a{sv}", "in"), ("u", "out"), ("a{sv}", "out")])
        self.assertEqual(iface.find("property").get("name"), "version")

    def test_consent_names_the_app_and_the_whole_screen(self):
        text = portal.consent("PortalProbe")
        self.assertIn("PortalProbe", text["subtitle"])
        self.assertIn("whole screen", text["subtitle"])
        self.assertTrue(portal.consent("")["subtitle"].startswith("An app "))


if __name__ == "__main__":
    unittest.main()
