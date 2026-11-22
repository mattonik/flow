import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location("appmenu", Path(__file__).parents[1] / "session/appmenu.py")
appmenu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(appmenu)

ROLES = ("FRAME", "WINDOW", "DIALOG", "MENU_BAR", "MENU", "MENU_ITEM", "CHECK_MENU_ITEM", "RADIO_MENU_ITEM",
         "FILLER", "PANEL", "DOCUMENT_FRAME", "TEXT", "TERMINAL", "PUSH_BUTTON", "APPLICATION")
# A stand-in for pyatspi: roles and states as names, and the coordinate constant.
at = types.SimpleNamespace(**{f"ROLE_{r}": r for r in ROLES}, STATE_ACTIVE="active", STATE_SHOWING="showing",
                           STATE_ENABLED="enabled", DESKTOP_COORDS=0)


class Node:
    """An accessible node, as pyatspi's: counts every call that would cross the bus."""
    calls = 0

    def __init__(self, role, name="", children=(), states=("enabled", "showing"), pid=None, size=(100, 20),
                 action=None):
        self.role, self.name, self.children, self.states = role, name, list(children), set(states)
        self.pid, self.size, self.action = pid, size, action or Mock(return_value=True)

    @property
    def childCount(self):
        Node.calls += 1
        return len(self.children)

    def getChildAtIndex(self, i):
        Node.calls += 1
        return self.children[i]

    def getRole(self):
        Node.calls += 1
        return self.role

    def getState(self):
        Node.calls += 1
        return types.SimpleNamespace(contains=lambda state: state in self.states)

    def get_process_id(self):
        return self.pid

    def queryComponent(self):
        return types.SimpleNamespace(getExtents=lambda coords: types.SimpleNamespace(x=0, y=0, width=self.size[0], height=self.size[1]))

    def queryAction(self):
        return types.SimpleNamespace(doAction=self.action)


def item(name, **kw):
    return Node("MENU_ITEM", name, **kw)


def menu(name, *items, **kw):
    return Node("MENU", name, items, **kw)


def bar(*menus, **kw):
    return Node("MENU_BAR", "", menus, **kw)


def frame(name, *children, active=False):
    return Node("FRAME", name, children, states=("enabled", "showing") + (("active",) if active else ()))


def desktop(*apps):
    return types.SimpleNamespace(Registry=types.SimpleNamespace(getDesktop=lambda _: Node("DESKTOP", "", apps)))


def window(ident, pid, title, app_id="org.kde.kate", focused=True):
    return {"id": ident, "pid": pid, "title": title, "app_id": app_id, "is_focused": focused}


def niri_with(windows, active=None):
    """Niri's socket answers: these windows; the focused workspace's active window is ``active``."""
    def request(what):
        if what == "Windows":
            return {"Windows": windows}
        return {"Workspaces": [{"id": 1, "is_focused": True, "active_window_id": active}]}
    return request


def reader(apps, windows, active=None, proc="/nonexistent"):
    pyatspi = types.SimpleNamespace(**vars(at), **vars(desktop(*apps)))
    return appmenu.Reader(pyatspi, niri_with(windows, active), proc=proc)


def ask(r, **request):
    return json.loads(r.answer(json.dumps({"id": 9, **request})))


def kate_frame(title, active, new=None):
    return frame(title, bar(menu("File", item("New", action=new), item("Quit")), menu("Help", item("About Kate"))),
                 Node("PANEL", "", [Node("TEXT", "doc")]), active=active)


class AppNameTest(unittest.TestCase):
    def gio(self, new):
        gi, repository = types.ModuleType("gi"), types.ModuleType("gi.repository")
        repository.GioUnix = types.SimpleNamespace(DesktopAppInfo=types.SimpleNamespace(new=new))
        return patch.dict(sys.modules, {"gi": gi, "gi.repository": repository})

    def test_uses_installed_application_name(self):
        desktop_file = Mock()
        desktop_file.get_display_name.return_value = "Kate"
        new = Mock(return_value=desktop_file)
        with self.gio(new):
            self.assertEqual(appmenu.app_name("org.kde.kate", "Document"), "Kate")
        new.assert_called_once_with("org.kde.kate.desktop")

    def test_falls_back_when_no_desktop_file_has_the_app_id(self):
        # PyGObject raises, it never returns None (coverage report, finding 1): LibreOffice's libreoffice-writer.
        with self.gio(Mock(side_effect=TypeError("constructor returned NULL"))):
            self.assertEqual(appmenu.app_name("libreoffice-writer", "Untitled 1"), "libreoffice-writer")
            self.assertEqual(appmenu.app_name(None, "Untitled 1"), "Untitled 1")
            self.assertEqual(appmenu.app_name(None, None), "Application")

    def test_probe_answers_for_an_app_without_desktop_file(self):
        app = Node("APPLICATION", "soffice", [kate_frame("Untitled 1 - LibreOffice Writer", True)], pid=5)
        r = reader([app], [window(7, 5, "Untitled 1 - LibreOffice Writer", "libreoffice-writer")])
        with self.gio(Mock(side_effect=TypeError("constructor returned NULL"))):
            answer = ask(r, op="probe", window=7)
        self.assertEqual((answer["name"], answer["has_menu"]), ("libreoffice-writer", True))


class FrameChoiceTest(unittest.TestCase):
    def test_title_and_active_together(self):
        # Two same-titled windows: only the active one (finding 10).
        self.assertEqual(appmenu.choose_frame([("Welcome — Kate", False), ("Welcome — Kate", True)], "Welcome — Kate", False), 1)
        self.assertIsNone(appmenu.choose_frame([("Welcome — Kate", False), ("Welcome — Kate", False)], "Welcome — Kate", False))
        self.assertIsNone(appmenu.choose_frame([("Welcome — Kate", True), ("Welcome — Kate", True)], "Welcome — Kate", False))

    def test_a_unique_title_decides(self):
        self.assertEqual(appmenu.choose_frame([("a — Kate", True), ("b — Kate", False)], "b — Kate", False), 1)

    def test_without_a_named_frame(self):
        self.assertEqual(appmenu.choose_frame([("Kate", False)], "Welcome — Kate", True), 0)
        # One frame but two windows: the frame could be the other window's, so only active decides.
        self.assertIsNone(appmenu.choose_frame([("Kate", False)], "Welcome — Kate", False))
        self.assertEqual(appmenu.choose_frame([("x", False), ("y", True)], "Welcome — Kate", False), 1)

    def test_probe_remembers_the_frame_and_read_uses_it_once_no_frame_is_active(self):
        first, second = kate_frame("Welcome — Kate", False), kate_frame("Welcome — Kate", True)
        app = Node("APPLICATION", "kate", [first, second], pid=5)
        windows = [window(7, 5, "Welcome — Kate", focused=False), window(8, 5, "Welcome — Kate")]
        r = reader([app], windows, active=8)
        self.assertEqual(ask(r, op="probe", window=8)["has_menu"], True)
        self.assertIs(r.frames[8][0], second)
        second.states.discard("active")  # Flow's menu surface took the keyboard
        menus = ask(r, op="read", window=8)["menus"]
        self.assertEqual([m["label"] for m in menus], ["File", "Help"])
        new = menus[0]["children"][0]
        self.assertEqual(ask(r, op="activate", window=8, path=new["path"], label="New"), {"id": 9, "window": 8, "done": True})
        second.children[0].children[0].children[0].action.assert_called_once_with(0)
        first.children[0].children[0].children[0].action.assert_not_called()

    def test_ambiguous_frames_are_not_remembered(self):
        app = Node("APPLICATION", "kate", [kate_frame("Welcome — Kate", False), kate_frame("Welcome — Kate", False)], pid=5)
        windows = [window(7, 5, "Welcome — Kate", focused=False), window(8, 5, "Welcome — Kate")]
        r = reader([app], windows)
        self.assertEqual(ask(r, op="probe", window=8), {"id": 9, "window": 8, "name": "org.kde.kate", "has_menu": False, "final": False})
        self.assertIn("error", ask(r, op="read", window=8))

    def test_probe_for_a_window_that_lost_focus_answers_moved(self):
        r = reader([], [window(7, 5, "a", focused=False), window(8, 6, "b")])
        self.assertEqual(ask(r, op="probe", window=7), {"id": 9, "window": 7, "moved": True})
        self.assertEqual(ask(r, op="probe")["window"], 8)  # no window: whichever is focused

    def test_closed_windows_are_forgotten(self):
        app = Node("APPLICATION", "kate", [kate_frame("A", True)], pid=5)
        windows = [window(7, 5, "A")]
        r = reader([app], windows)
        ask(r, op="probe", window=7)
        self.assertIn(7, r.frames)
        windows[:] = [window(9, 6, "foot")]
        ask(r, op="probe", window=9)
        self.assertNotIn(7, r.frames)


class X11Test(unittest.TestCase):
    def setUp(self):
        self.proc = tempfile.TemporaryDirectory()
        (Path(self.proc.name) / "1022").mkdir()
        # NixOS runs it through a wrapper, so its name is cut from .xwayland-satellite-wrapped.
        (Path(self.proc.name) / "1022" / "comm").write_text(".xwayland-satel\n")
        self.addCleanup(self.proc.cleanup)

    def test_matches_by_app_id_and_title_not_pid(self):
        # Niri gives every X11 window xwayland-satellite's PID (finding 8); VLC's own process is on the bus.
        vlc = Node("APPLICATION", "vlc", [frame("VLC media player", bar(menu("Media", item("Quit"))))], pid=534556)
        other = Node("APPLICATION", "kate", [kate_frame("VLC media player", True)], pid=1022)
        r = reader([other, vlc], [window(3, 1022, "VLC media player", "vlc")], proc=self.proc.name)
        self.assertTrue(appmenu.is_x11(1022, self.proc.name))
        self.assertEqual(ask(r, op="probe", window=3)["has_menu"], True)
        self.assertIs(r.frames[3][0], vlc.children[0])
        self.assertEqual(ask(r, op="read", window=3)["menus"][0]["label"], "Media")

    def test_two_x11_windows_of_one_app_need_title_or_active(self):
        vlc = Node("APPLICATION", "vlc", [frame("a.mp4 - VLC media player", bar(menu("Media", item("Quit")))),
                                          frame("Playlist", bar(menu("Media", item("Quit"))))], pid=534556)
        windows = [window(3, 1022, "Playlist", "vlc"), window(4, 1022, "a.mp4 - VLC media player", "vlc", focused=False)]
        r = reader([vlc], windows, proc=self.proc.name)
        ask(r, op="probe", window=3)
        self.assertIs(r.frames[3][0], vlc.children[1])

    def test_a_wayland_window_is_still_matched_by_pid(self):
        kate = Node("APPLICATION", "kate", [kate_frame("A", True)], pid=5)
        imposter = Node("APPLICATION", "kate", [kate_frame("A", True)], pid=6)
        r = reader([imposter, kate], [window(7, 5, "A")], proc=self.proc.name)
        ask(r, op="probe", window=7)
        self.assertIs(r.frames[7][0], kate.children[0])


class MenuBarTest(unittest.TestCase):
    def test_probe_stops_at_the_first_showing_bar_with_an_item(self):
        big = Node("PANEL", "", [Node("PUSH_BUTTON", str(i)) for i in range(300)])
        app = Node("APPLICATION", "kate", [frame("A", bar(menu("File", item("New"))), big, active=True)], pid=5)
        r = reader([app], [window(7, 5, "A")])
        Node.calls = 0
        self.assertTrue(ask(r, op="probe", window=7)["has_menu"])
        self.assertLess(Node.calls, 40)

    def test_items_behind_nodes_that_are_not_menus(self):
        # Qt puts a node that is not a menu between a menu and its items; the old reader looked through it.
        qt = bar(Node("MENU_ITEM", "File", [Node("PANEL", "", [item("New"), item("Quit")])]))
        app = Node("APPLICATION", "kate", [frame("A", qt, active=True)], pid=5)
        r = reader([app], [window(7, 5, "A")])
        self.assertTrue(ask(r, op="probe", window=7)["has_menu"])
        menus = ask(r, op="read", window=7)["menus"]
        self.assertEqual(menus, [{"label": "File", "enabled": True, "children": [
            {"label": "New", "path": [0, 0, 0, 0], "enabled": True}, {"label": "Quit", "path": [0, 0, 0, 1], "enabled": True}]}])

    def test_closed_menus_and_hidden_bars_are_no_menu(self):
        # Firefox: a bar that is not showing, and menus with no items until opened (finding 7).
        hidden = bar(menu("File", item("New")), states=("enabled",))
        empty = bar(menu("File"), menu("Edit"))
        app = Node("APPLICATION", "firefox", [frame("A", hidden, empty, active=True)], pid=5)
        self.assertFalse(ask(reader([app], [window(7, 5, "A")]), op="probe", window=7)["has_menu"])

    def test_content_is_not_searched(self):
        inside = Node("DOCUMENT_FRAME", "", [bar(menu("File", item("New")))])
        terminal = Node("TERMINAL", "", [bar(menu("File", item("New")))])
        app = Node("APPLICATION", "x", [frame("A", inside, terminal, active=True)], pid=5)
        self.assertFalse(ask(reader([app], [window(7, 5, "A")]), op="probe", window=7)["has_menu"])

    def test_a_bar_deeper_than_the_limit_is_not_found(self):
        node = bar(menu("File", item("New")))
        for _ in range(appmenu.BAR_DEPTH):
            node = Node("FILLER", "", [node])
        app = Node("APPLICATION", "x", [frame("A", node, active=True)], pid=5)
        self.assertFalse(ask(reader([app], [window(7, 5, "A")]), op="probe", window=7)["has_menu"])
        app.children[0].children = [node.children[0]]  # one level up: found
        self.assertTrue(ask(reader([app], [window(7, 5, "A")]), op="probe", window=7)["has_menu"])

    def test_only_the_bar_the_user_sees_is_listed(self):
        # LibreOffice shows two menu bars in its frame (finding 4); the one without a size is not seen.
        unseen = bar(*(menu(f"Unseen {i}", item("x")) for i in range(11)), size=(1280, 0))
        seen = bar(*(menu(f"Seen {i}", item("x")) for i in range(11)))
        app = Node("APPLICATION", "soffice", [frame("Untitled 1", Node("FILLER", "", [unseen, seen]), active=True)], pid=5)
        r = reader([app], [window(7, 5, "Untitled 1")])
        ask(r, op="probe", window=7)
        menus = ask(r, op="read", window=7)["menus"]
        self.assertEqual([m["label"] for m in menus], [f"Seen {i}" for i in range(11)])


class ActivateTest(unittest.TestCase):
    def setUp(self):
        self.new = Mock(return_value=True)
        self.app = Node("APPLICATION", "kate", [kate_frame("A", True, new=self.new)], pid=5)
        self.timeouts = []
        pyatspi = types.SimpleNamespace(**vars(at), **vars(desktop(self.app)))
        self.active = [7]
        self.r = appmenu.Reader(pyatspi, lambda what: niri_with([window(7, 5, "A")], self.active[0])(what),
                                set_timeout=lambda *a: self.timeouts.append(a))
        ask(self.r, op="probe", window=7)
        self.path = ask(self.r, op="read", window=7)["menus"][0]["children"][0]["path"]

    def test_runs_the_item_with_a_short_timeout(self):
        self.assertEqual(ask(self.r, op="activate", window=7, path=self.path, label="New")["done"], True)
        self.assertEqual(self.timeouts, [(appmenu.ACTION_TIMEOUT_MS,) * 2, appmenu.METHOD_TIMEOUT_MS])

    def test_a_reply_timeout_means_sent(self):
        # DB Browser's modal About answers only when it closes (finding 3).
        self.new.side_effect = Exception("atspi_error: Did not receive a reply. Possible causes include: ...")
        self.assertEqual(ask(self.r, op="activate", window=7, path=self.path, label="New"),
                         {"id": 9, "window": 7, "done": True, "unknown": True})
        self.assertEqual(self.timeouts[-1], appmenu.METHOD_TIMEOUT_MS)

    def test_other_failures_are_errors(self):
        self.new.side_effect = Exception("The application no longer exists")
        self.assertEqual(ask(self.r, op="activate", window=7, path=self.path, label="New")["error"], "The application no longer exists")
        self.new.side_effect, self.new.return_value = None, False
        self.assertIn("declined", ask(self.r, op="activate", window=7, path=self.path, label="New")["error"])

    def test_refused_when_focus_moved_or_the_item_changed(self):
        self.active[0] = 8
        self.assertEqual(ask(self.r, op="activate", window=7, path=self.path, label="New")["error"], "Application focus changed")
        self.active[0] = 7
        self.assertIn("no longer", ask(self.r, op="activate", window=7, path=self.path, label="Quit")["error"])
        self.assertIn("no longer", ask(self.r, op="activate", window=7, path=self.path + [0], label="New")["error"])
        self.app.children[0].children[0].children[0].states.discard("enabled")  # File disabled
        self.assertIn("no longer", ask(self.r, op="activate", window=7, path=self.path, label="New")["error"])
        self.new.assert_not_called()

    def test_unknown_window(self):
        self.assertEqual(ask(self.r, op="activate", window=99, path=self.path, label="New")["error"], "No app menu for this window")


class ProtocolTest(unittest.TestCase):
    def test_answers_carry_the_request_id_and_errors_do_not_stop_the_reader(self):
        r = reader([], [window(8, 6, "foot", "foot")])
        self.assertIsNone(r.answer("not json"))
        self.assertIsNone(r.answer("[1]"))
        self.assertEqual(json.loads(r.answer('{"id": 4, "op": "dance"}')), {"id": 4, "window": None, "error": "Unknown request 'dance'"})
        r.niri = Mock(side_effect=OSError("No such file or directory"))
        self.assertEqual(ask(r, op="probe", window=8)["error"], "No such file or directory")
        r.niri = niri_with([window(8, 6, "foot", "foot")])
        self.assertEqual(ask(r, op="probe", window=8),
                         {"id": 9, "window": 8, "name": "foot", "has_menu": False, "final": False})

    def test_niri_requests_go_over_its_socket(self):
        import socket
        import threading
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "niri.sock")
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(path)
            server.listen(1)
            seen = []

            def serve_one():
                connection, _ = server.accept()
                with connection, connection.makefile("rwb") as stream:
                    seen.append(json.loads(stream.readline()))
                    stream.write(b'{"Ok": {"Windows": []}}\n')
            thread = threading.Thread(target=serve_one)
            thread.start()
            with patch.dict(appmenu.os.environ, {"NIRI_SOCKET": path}):
                self.assertEqual(appmenu.niri("Windows"), {"Windows": []})
            thread.join()
            server.close()
        self.assertEqual(seen, ["Windows"])


class SwitchTest(unittest.TestCase):
    class Fake:
        def __init__(self, enabled, reader=False):
            self.values = {"IsEnabled": enabled, "ScreenReaderEnabled": reader}
            self.sets = []

        def get(self, name):
            return self.values[name]

        def set(self, value):
            self.sets.append(value)
            self.values["IsEnabled"] = value

    def test_switches_off_only_what_flow_switched_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            mark = Path(tmp) / "flow" / "mark"
            switch = self.Fake(False)
            appmenu.switch_on(switch, mark)
            self.assertEqual((switch.sets, mark.exists()), ([True], True))
            appmenu.switch_on(switch, mark)  # a restarted reader: already on, the mark stays
            self.assertEqual((switch.sets, mark.exists()), ([True], True))
            self.assertTrue(appmenu.release(switch, mark))
            self.assertEqual((switch.sets, mark.exists()), ([True, False], False))
            self.assertFalse(appmenu.release(switch, mark))

            user = self.Fake(True)  # the user had it on
            appmenu.switch_on(user, mark)
            self.assertEqual((user.sets, mark.exists()), ([], False))
            self.assertFalse(appmenu.release(user, mark))
            self.assertEqual(user.sets, [])

    def test_never_switches_off_under_a_screen_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            mark = Path(tmp) / "mark"
            switch = self.Fake(False)
            appmenu.switch_on(switch, mark)
            switch.values["ScreenReaderEnabled"] = True
            self.assertFalse(appmenu.release(switch, mark))
            self.assertEqual((switch.sets, mark.exists()), ([True], False))

    def test_mark_lives_in_flows_state(self):
        with patch.dict(appmenu.os.environ, {"XDG_STATE_HOME": "/s"}):
            self.assertEqual(appmenu.marker(), Path("/s/flow/appmenu-switched-accessibility-on"))


if __name__ == "__main__":
    unittest.main()
