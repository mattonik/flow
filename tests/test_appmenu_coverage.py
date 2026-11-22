import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("appmenu_coverage", Path(__file__).parent / "appmenu_coverage.py")
coverage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coverage)

MENUS = [
    {"label": "File", "children": [{"label": "New", "path": [0, 0, 0], "enabled": True},
                                   {"label": "Recent", "children": [{"label": "a.txt", "path": [0, 0, 1, 0], "enabled": True}]}]},
    {"label": "Help", "children": [{"label": "About Kate", "path": [0, 1, 0], "enabled": False},
                                   {"label": "About KDE", "path": [0, 1, 1], "enabled": True}]},
]


class CoverageHelpersTest(unittest.TestCase):
    def test_leaves_carry_their_menu_path(self):
        self.assertEqual([labels for labels, _ in coverage.leaves(MENUS)],
                         [("File", "New"), ("File", "Recent", "a.txt"), ("Help", "About Kate"), ("Help", "About KDE")])

    def test_command_is_the_first_enabled_match(self):
        labels, leaf = coverage.find_command(MENUS, ("^Help$", "^About"))
        self.assertEqual(labels, ("Help", "About KDE"))
        self.assertEqual(leaf["path"], [0, 1, 1])
        self.assertEqual(coverage.find_command(MENUS, ("^View$", ".")), (None, None))

    def test_accelerator_from_gtk_and_qt_bindings(self):
        self.assertEqual(coverage.accelerator("n;<Alt>f:n;<Control>n"), "<Control>n")
        self.assertEqual(coverage.accelerator("x;;<Alt>x"), "<Alt>x")  # GTK: a real Alt shortcut
        self.assertEqual(coverage.accelerator("n;;"), "")
        self.assertEqual(coverage.accelerator("Ctrl+N"), "Ctrl+N")
        self.assertEqual(coverage.accelerator("Ctrl+;"), "Ctrl+;")
        self.assertEqual(coverage.accelerator("Alt+N"), "")  # Qt: the mnemonic of an item without a shortcut
        self.assertEqual(coverage.accelerator("Alt+F4"), "Alt+F4")
        self.assertEqual(coverage.accelerator(""), "")

    def test_narrowing_needs_title_and_active_to_single_out_a_frame(self):
        frames = [{"name": "Home", "active": False}, {"name": "Home", "active": True}, {"name": "tmp", "active": False}]
        self.assertEqual(coverage.narrowing(frames, "Home"),
                         {"by_title": [0, 1], "by_active": [1], "both": [1], "resolved": True})
        self.assertFalse(coverage.narrowing(frames, "tmp")["resolved"])  # named, but not the active frame
        self.assertFalse(coverage.narrowing([{"name": "Home", "active": True}] * 2, "Home")["resolved"])


# --- A fake accessibility tree, walked by the reader and by the probe -------------------------------------------

ROLES = dict(ROLE_MENU_BAR=1, ROLE_MENU=2, ROLE_MENU_ITEM=3, ROLE_CHECK_MENU_ITEM=4, ROLE_RADIO_MENU_ITEM=5,
             ROLE_FRAME=6, ROLE_PANEL=7, ROLE_PUSH_BUTTON=8)


class State:
    def contains(self, _state):
        return True


class Node:
    def __init__(self, role, name="", children=(), pid=None):
        self.role, self.name, self.children, self.pid = ROLES[role], name, list(children), pid

    def getRole(self):
        return self.role

    @property
    def childCount(self):
        return len(self.children)

    def getChildAtIndex(self, i):
        return self.children[i]

    def getState(self):
        return State()

    def get_process_id(self):
        return self.pid


def fake_pyatspi(app):
    desktop = Node("ROLE_PANEL", children=[app])
    return types.SimpleNamespace(**ROLES, STATE_ENABLED=0, Registry=types.SimpleNamespace(getDesktop=lambda _: desktop))


def menu(name, *items):
    return Node("ROLE_MENU", name, [Node("ROLE_MENU_ITEM", i) if isinstance(i, str) else i for i in items])


def labels(menus):
    return [{"label": m["label"], **({"children": labels(m["children"])} if "children" in m else {})} for m in menus]


class WalkMatchesReaderTest(unittest.TestCase):
    """The probe's menu_walk counts what session/appmenu.py's walk reads: both run on one tree must agree."""

    def reader_menus(self, app):
        appmenu = coverage.load_reader()
        workspaces = [{"id": 1, "is_focused": True, "active_window_id": 5}]
        wins = [{"id": 5, "pid": 42, "app_id": "x", "title": "t"}]
        with patch.dict(sys.modules, {"pyatspi": fake_pyatspi(app)}), \
                patch.object(appmenu, "niri", side_effect=lambda what: workspaces if what == "workspaces" else wins), \
                patch.object(appmenu, "app_name", return_value="x"):
            return appmenu.active_window()["menus"]

    def probe_menus(self, app, capped=True):
        st, found = {"nodes": 0, "depth": 0, "bars": 0, "empty_menus": 0}, []
        return coverage.menu_walk(app, 0, False, st, capped, found, fake_pyatspi(app)), st, found

    def test_same_menus_and_counts_as_the_reader(self):
        bar = Node("ROLE_MENU_BAR", children=[
            menu("File", "New", menu("Recent", "a.txt", "b.txt"), menu("Lazy")),
            menu("Help", Node("ROLE_CHECK_MENU_ITEM", "Tips"), "About")])
        app = Node("ROLE_PANEL", "app", [Node("ROLE_FRAME", "w", [Node("ROLE_PANEL", children=[bar]),
                                                                    Node("ROLE_PUSH_BUTTON", "OK")])], pid=42)
        menus, st, found = self.probe_menus(app)
        self.assertEqual(labels(menus), labels(self.reader_menus(app)))
        self.assertEqual([n.name for n in found], ["New", "a.txt", "b.txt", "Tips", "About"])
        self.assertEqual((st["bars"], st["empty_menus"]), (1, 1))  # "Lazy" has no items: the walk drops it

    def test_caps_cut_at_the_same_place_as_the_reader(self):
        deep = menu("Deep", "x")
        for _ in range(14):  # a menu bar below depth 12, as in VSCodium's web content
            deep = Node("ROLE_PANEL", children=[deep])
        wide = [Node("ROLE_PUSH_BUTTON", str(i)) for i in range(2100)]  # more than 2000 nodes before the bar
        late_bar = Node("ROLE_MENU_BAR", children=[menu("Late", "y")])
        app = Node("ROLE_PANEL", "app", [Node("ROLE_MENU_BAR", children=[deep]), Node("ROLE_PANEL", children=wide),
                                         late_bar], pid=42)
        menus, _, found = self.probe_menus(app)
        self.assertEqual(labels(menus), labels(self.reader_menus(app)))
        self.assertEqual(found, [])
        _, _, uncapped = self.probe_menus(app, capped=False)
        self.assertEqual([n.name for n in uncapped], ["x", "y"])


# --- Safety on a shared session -------------------------------------------------------------------------------

def fake_proc(root, processes):
    """A /proc with ``processes``: {pid: (session, environ dict)}."""
    for pid, (session, environ) in processes.items():
        d = Path(root) / str(pid)
        d.mkdir()
        (d / "stat").write_text(f"{pid} (some app) S 1 {pid} {session} 0 -1")
        (d / "environ").write_bytes(b"\0".join(f"{k}={v}".encode() for k, v in environ.items()))


class SharedSessionSafetyTest(unittest.TestCase):
    def setUp(self):
        self.saved = {k: (set(v) if isinstance(v, set) else v) for k, v in coverage.STATE.items()}
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        coverage.STATE.update(sids=set(), app_pids=set(), windows=set(), bus=None, bus_proc=None, notes=[])
        patcher = patch.object(coverage, "state_file", return_value=Path(self.tmp.name) / "state.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        coverage.STATE.update(self.saved)

    def test_only_the_runs_own_windows_are_taken(self):
        proc = Path(self.tmp.name) / "proc"
        proc.mkdir()
        fake_proc(proc, {100: (100, {}), 101: (100, {}), 200: (200, {})})  # 100/101 the run's, 200 someone else's
        coverage.STATE["sids"] = {100}
        current = {1: {"id": 1, "pid": 200, "title": "old"},
                   2: {"id": 2, "pid": 101, "title": "mine"},
                   3: {"id": 3, "pid": 200, "title": "another session's"},
                   4: {"id": 4, "pid": 1022, "app_id": "vlc", "title": "X11"}}
        with patch.object(coverage, "PROC", proc), patch.object(coverage, "windows", return_value=current), \
                patch.object(coverage.time, "sleep"):
            self.assertEqual([w["id"] for w in coverage.wait_new_window({1}, settle=0)], [2])
            self.assertEqual(coverage.STATE["windows"], {2})
            self.assertEqual([w["id"] for w in coverage.wait_new_window({1, 2}, x11_app_id="vlc", settle=0)], [4])

    def test_restore_closes_only_own_windows_and_returns_the_switch_only_if_untouched(self):
        coverage.STATE.update(windows={2}, a11y=False, a11y_set=True, workspace=None)
        for current, expect_set in ((True, True), (False, False)):  # untouched: put back; changed: left alone
            coverage.STATE.update(windows={2}, a11y_set=True)
            with patch.object(coverage, "close_window") as close, \
                    patch.object(coverage, "a11y_enabled", return_value=current), \
                    patch.object(coverage.subprocess, "run") as run:
                coverage.restore()
            close.assert_called_once_with(2, wait=0)
            sets = [c for c in run.call_args_list if "set-property" in c.args[0]]
            self.assertEqual(bool(sets), expect_set)
            if sets:
                self.assertEqual(sets[0].args[0][-1], "false")

    def test_late_phase_is_skipped_when_someone_else_changed_the_switch(self):
        coverage.STATE.update(a11y_set=True)
        with patch.object(coverage, "a11y_enabled", return_value=False), \
                patch.object(coverage.subprocess, "run") as run:
            self.assertFalse(coverage.set_a11y(False))
        run.assert_not_called()

    def test_private_bus_clients_are_found_by_their_environment_only(self):
        proc = Path(self.tmp.name) / "proc"
        proc.mkdir()
        mine, other = "unix:path=/tmp/dbus-mine", "unix:path=/run/user/1000/bus"
        fake_proc(proc, {10: (10, {"DBUS_SESSION_BUS_ADDRESS": mine}), 11: (11, {"DBUS_STARTER_ADDRESS": mine}),
                         12: (12, {"DBUS_SESSION_BUS_ADDRESS": other}),
                         13: (13, {"DBUS_SESSION_BUS_ADDRESS": mine + "-longer"})})
        self.assertEqual(sorted(coverage.bus_clients(mine, proc)), [10, 11])

    def test_private_bus_that_does_not_start_stops_the_run(self):
        class Dead:
            pid = 1
            stdout = types.SimpleNamespace(readline=lambda: "")

            def terminate(self):
                pass

            def wait(self, _timeout):
                return 1
        with patch.object(coverage.subprocess, "Popen", return_value=Dead()), \
                patch.dict(coverage.os.environ, {"XDG_RUNTIME_DIR": self.tmp.name}):
            with self.assertRaises(RuntimeError):
                coverage.start_private_bus(self.tmp.name)
        self.assertIsNone(coverage.STATE["bus"])
        self.assertIsNone(coverage.STATE["bus_proc"])

    def test_state_file_names_what_cleanup_needs(self):
        coverage.STATE.update(a11y=False, a11y_set=True, sids={7}, windows={3})
        coverage.save_state()
        saved = json.loads(coverage.state_file().read_text())
        self.assertEqual((saved["a11y"], saved["a11y_set"], saved["sids"], saved["windows"]), (False, True, [7], [3]))


if __name__ == "__main__":
    unittest.main()
