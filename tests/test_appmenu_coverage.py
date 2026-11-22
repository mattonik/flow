import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

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
    reads = 0  # getState() calls, so the probe's walk can be held to the reader's per-node cost

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
        State.reads += 1
        return State()

    def get_process_id(self):
        return self.pid


def fake_pyatspi(app):
    desktop = Node("ROLE_PANEL", children=[app])
    return types.SimpleNamespace(**ROLES, STATE_ENABLED=0, Registry=types.SimpleNamespace(getDesktop=lambda _: desktop))


def menu(name, *items):
    return Node("ROLE_MENU", name, [Node("ROLE_MENU_ITEM", i) if isinstance(i, str) else i for i in items])


def nest(node, levels):
    for _ in range(levels):
        node = Node("ROLE_PANEL", children=[node])
    return node


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

    def probe_walk(self, app, capped=True):
        st, found = {"nodes": 0, "depth": 0, "bars": 0, "dropped": 0}, []
        return coverage.menu_walk(app, 0, False, st, capped, found, fake_pyatspi(app)), st, found

    def assert_same(self, app):
        menus, st, found = self.probe_walk(app)
        self.assertEqual(labels(menus), labels(self.reader_menus(app)))
        return [n.name for n in found], st

    def test_same_menus_and_counts_as_the_reader(self):
        bar = Node("ROLE_MENU_BAR", children=[
            menu("File", "New", menu("Recent", "a.txt", "b.txt"), menu("Lazy")),
            menu("Help", Node("ROLE_CHECK_MENU_ITEM", "Tips"), "About",
                 Node("ROLE_MENU_ITEM", "Odd", [Node("ROLE_PUSH_BUTTON", "x")]))])
        app = Node("ROLE_PANEL", "app", [Node("ROLE_FRAME", "w", [Node("ROLE_PANEL", children=[bar]),
                                                                    Node("ROLE_PUSH_BUTTON", "OK")])], pid=42)
        State.reads = 0
        self.reader_menus(app)
        reader_reads, State.reads = State.reads, 0
        found, st = self.assert_same(app)
        self.assertEqual(State.reads - reader_reads, reader_reads)  # assert_same ran both walks once more
        self.assertEqual(found, ["New", "a.txt", "b.txt", "Tips", "About"])
        # "Lazy" (an empty menu) and "Odd" (an item with children but no items) are both dropped, and counted
        self.assertEqual((st["bars"], st["dropped"]), (1, 2))

    def test_depth_cap_at_the_same_level_as_the_reader(self):
        # app at depth 0; an item at depth 12 is read, one at depth 13 is not
        at_12 = nest(Node("ROLE_MENU_BAR", children=[menu("A", "twelve")]), 9)   # bar 10, menu 11, item 12
        at_13 = nest(Node("ROLE_MENU_BAR", children=[menu("B", "thirteen")]), 10)  # bar 11, menu 12, item 13
        app = Node("ROLE_PANEL", "app", [at_12, at_13], pid=42)
        found, _ = self.assert_same(app)
        self.assertEqual(found, ["twelve"])

    def test_node_cap_at_the_same_node_as_the_reader(self):
        for buttons, expected in ((1996, ["last"]), (1997, [])):  # the item is node 2000, then node 2001
            app = Node("ROLE_PANEL", "app", [*(Node("ROLE_PUSH_BUTTON") for _ in range(buttons)),
                                             Node("ROLE_MENU_BAR", children=[menu("M", "last")])], pid=42)
            found, _ = self.assert_same(app)
            self.assertEqual(found, expected, buttons)
            _, _, uncapped = self.probe_walk(app, capped=False)
            self.assertEqual([n.name for n in uncapped], ["last"])


# --- Isolation from the session -------------------------------------------------------------------------------

def fake_proc(root, processes):
    """A /proc with ``processes``: {pid: (environ dict, parent, cmdline)}."""
    for pid, (environ, parent, cmdline) in processes.items():
        d = Path(root) / str(pid)
        d.mkdir()
        (d / "stat").write_text(f"{pid} (some app) S {parent} {pid} {pid} 0 -1")
        (d / "environ").write_bytes(b"\0".join(f"{k}={v}".encode() for k, v in environ.items()))
        (d / "cmdline").write_bytes(cmdline.replace(" ", "\0").encode())


class SessionIsolationTest(unittest.TestCase):
    def setUp(self):
        self.saved = {k: (set(v) if isinstance(v, set) else list(v) if isinstance(v, list) else v)
                      for k, v in coverage.STATE.items()}
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.proc = Path(self.tmp.name) / "proc"
        self.proc.mkdir()
        mine = {coverage.MARKER: "run-1"}
        fake_proc(self.proc, {
            100: (mine, 1, "kate -n"),                               # started by the run
            101: ({}, 100, "kate-helper"),                           # its child, environment not inherited
            300: ({}, 1, "codium --user-data-dir=/tmp/flow-appmenu-vscodium-ab12/vsc"),  # detached
            301: ({}, 300, "codium --type=zygote"),                  # its child, environment rewritten
            200: ({}, 1, "firefox"),                                 # someone else's
            400: ({coverage.MARKER: "another-run"}, 1, "kate -n"),   # another run's
            500: ({}, 1, "codium --user-data-dir=/tmp/flow-appmenu-vscodium-other/vsc")})
        coverage.STATE.update(run="run-1", tmps={"/tmp/flow-appmenu-vscodium-ab12"}, windows=set(), procs=[],
                              bus=None, bus_proc=None, notes=[], restored=False)
        for target, value in (("PROC", self.proc), ("state_file", lambda: Path(self.tmp.name) / "state.json")):
            patcher = patch.object(coverage, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.windows = {1: {"id": 1, "pid": 200, "title": "old, someone else's"},
                        2: {"id": 2, "pid": 100, "title": "mine"},
                        3: {"id": 3, "pid": 200, "title": "new, someone else's"},
                        4: {"id": 4, "pid": 1022, "app_id": "vlc", "title": "X11"},
                        5: {"id": 5, "pid": 300, "title": "detached, mine"},
                        6: {"id": 6, "pid": 400, "title": "another run's"},
                        7: {"id": 7, "pid": 101, "title": "my child's"}}

    def tearDown(self):
        coverage.STATE.clear()
        coverage.STATE.update(self.saved)

    def test_own_processes_by_marker_directory_and_ancestry_and_user(self):
        self.assertEqual(coverage.own_processes(), [100, 101, 300, 301])
        with patch.object(coverage.os, "getuid", return_value=12345):  # another user's processes are never own
            self.assertEqual(coverage.own_processes(), [])

    def test_only_the_runs_own_windows_are_taken(self):
        with patch.object(coverage, "windows", return_value=self.windows), patch.object(coverage.time, "sleep"):
            self.assertEqual([w["id"] for w in coverage.wait_new_window({1}, settle=0)], [2, 5, 7])
            self.assertEqual(coverage.STATE["windows"], {2, 5, 7})
            self.assertEqual([w["id"] for w in coverage.wait_new_window({1, 2, 5, 7}, x11_app_id="vlc", settle=0)], [4])

    def test_phase_teardown_closes_only_own_windows(self):
        coverage.STATE["windows"] = {2}
        with patch.object(coverage, "windows", return_value=self.windows), \
                patch.object(coverage, "close_window") as close, \
                patch.object(coverage, "start_private_bus"), patch.object(coverage, "stop_private_bus"), \
                patch.object(coverage, "stop_own_processes") as stop_own, patch.object(coverage.time, "sleep"):
            with patch.object(coverage, "stop_private_bus") as stop_bus:  # the outer patch is only a default
                with coverage.phase({"key": "kate"}) as tmp:
                    saved = coverage.state_file().read_text()
        self.assertIn(tmp, saved)  # --cleanup knows the directory before anything starts in it
        self.assertEqual(sorted(c.args[0] for c in close.call_args_list), [2, 5, 7])
        stop_bus.assert_called()
        stop_own.assert_called()

    def test_cleanup_after_a_kill_closes_and_stops_only_the_runs_own(self):
        coverage.STATE.update(windows={2})
        coverage.save_state()
        coverage.STATE.update(run="", tmps=set(), windows=set())  # a new process: it knows only the state file
        killed = []
        with patch.object(coverage, "windows", return_value=self.windows), \
                patch.object(coverage, "close_window") as close, \
                patch.object(coverage.os, "kill", side_effect=lambda pid, sig: killed.append(pid)), \
                patch.object(coverage.time, "sleep"):
            coverage.cleanup()
        self.assertEqual(sorted(c.args[0] for c in close.call_args_list), [2, 5, 7])
        self.assertEqual(sorted(set(killed)), [100, 101, 300, 301])
        self.assertFalse(coverage.state_file().exists())

    def test_private_bus_clients_are_found_by_their_environment_only(self):
        bus = "unix:path=/tmp/dbus-mine"
        proc = Path(self.tmp.name) / "proc2"
        proc.mkdir()
        fake_proc(proc, {10: ({"DBUS_SESSION_BUS_ADDRESS": bus}, 1, "a"), 11: ({"DBUS_STARTER_ADDRESS": bus}, 1, "b"),
                         12: ({"DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"}, 1, "c"),
                         13: ({"DBUS_SESSION_BUS_ADDRESS": bus + "-longer"}, 1, "d")})
        self.assertEqual(sorted(coverage.bus_clients(bus, proc)), [10, 11])

    def test_stopping_the_private_bus_signals_its_clients_and_ends_it(self):
        daemon = MagicMock()
        coverage.STATE.update(bus="unix:path=/tmp/dbus-mine", bus_proc=daemon)
        with patch.object(coverage, "bus_clients", return_value=[10, 11]), \
                patch.object(coverage.os, "kill") as kill, patch.object(coverage.time, "sleep"):
            coverage.stop_private_bus()
        self.assertEqual(sorted({c.args[0] for c in kill.call_args_list}), [10, 11])
        daemon.terminate.assert_called_once()
        self.assertIsNone(coverage.STATE["bus"])

    def test_a_hung_private_bus_times_out(self):
        hung = MagicMock(pid=1)
        with patch.object(coverage.subprocess, "Popen", return_value=hung), \
                patch.object(coverage.select, "select", return_value=([], [], [])), \
                patch.dict(coverage.os.environ, {"XDG_RUNTIME_DIR": self.tmp.name}):
            with self.assertRaises(RuntimeError):
                coverage.start_private_bus(self.tmp.name)
        hung.stdout.readline.assert_not_called()

    def test_private_bus_that_does_not_start_stops_the_run(self):
        dead = MagicMock(pid=1)
        dead.stdout.readline.return_value = ""
        with patch.object(coverage.subprocess, "Popen", return_value=dead), \
                patch.object(coverage.select, "select", return_value=([dead.stdout], [], [])), \
                patch.dict(coverage.os.environ, {"XDG_RUNTIME_DIR": self.tmp.name}):
            with self.assertRaises(RuntimeError):
                coverage.start_private_bus(self.tmp.name)
        self.assertIsNone(coverage.STATE["bus"])
        self.assertIsNone(coverage.STATE["bus_proc"])

    def test_the_switch_is_set_only_on_a_private_bus(self):
        with patch.object(coverage.subprocess, "run") as run:
            with self.assertRaises(RuntimeError):
                coverage.set_a11y(True)
            run.assert_not_called()
            coverage.STATE["bus"] = "unix:path=/tmp/dbus-mine"
            coverage.set_a11y(True)
        self.assertEqual(run.call_args.kwargs["env"]["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/tmp/dbus-mine")

    def test_apps_get_their_own_home_runtime_and_settings(self):
        coverage.STATE["bus"] = "unix:path=/tmp/dbus-mine"
        with patch.dict(coverage.os.environ, {"XDG_RUNTIME_DIR": "/run/user/1000", "WAYLAND_DISPLAY": "wayland-1",
                                              "HOME": "/home/someone"}):
            env = coverage.app_env(self.tmp.name)
        for name in ("HOME", "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
            self.assertTrue(env[name].startswith(self.tmp.name), name)
        self.assertEqual(env["WAYLAND_DISPLAY"], "/run/user/1000/wayland-1")
        self.assertEqual((env["GSETTINGS_BACKEND"], env["DBUS_SESSION_BUS_ADDRESS"], env[coverage.MARKER]),
                         ("keyfile", "unix:path=/tmp/dbus-mine", "run-1"))

    def test_nothing_runs_on_the_sessions_bus(self):
        with self.assertRaises(RuntimeError):
            coverage.bus_env()
        with patch.object(coverage, "run") as run:  # the reader switches accessibility on: never the session's
            with self.assertRaises(RuntimeError):
                coverage.reader()
            with self.assertRaises(RuntimeError):
                coverage.probe(1)
            run.assert_not_called()
            coverage.STATE["bus"] = "unix:path=/tmp/dbus-mine"
            run.return_value = ({}, 0, 0)
            coverage.reader()
            coverage.reader(fixed=True)
            coverage.probe(1)
        for call in run.call_args_list:
            self.assertEqual(call.args[1]["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/tmp/dbus-mine")

    def test_no_bus_named_directly_is_inherited(self):
        coverage.STATE["bus"] = "unix:path=/tmp/dbus-mine"
        inherited = {"AT_SPI_BUS_ADDRESS": "unix:path=/run/user/1000/at-spi/bus_0",
                     "DBUS_STARTER_ADDRESS": "unix:path=/run/user/1000/bus", "DBUS_STARTER_BUS_TYPE": "session",
                     "XDG_RUNTIME_DIR": "/run/user/1000"}
        with patch.dict(coverage.os.environ, inherited):
            for env in (coverage.bus_env(), coverage.launch_env(self.tmp.name), coverage.app_env(self.tmp.name)):
                for name in ("AT_SPI_BUS_ADDRESS", "DBUS_STARTER_ADDRESS", "DBUS_STARTER_BUS_TYPE"):
                    self.assertNotIn(name, env)

    def test_apps_are_spawned_on_the_private_bus_in_their_own_home(self):
        with patch.object(coverage.subprocess, "Popen") as popen:
            with self.assertRaises(RuntimeError):
                coverage.spawn(["app"], self.tmp.name)
            popen.assert_not_called()
            coverage.STATE["bus"] = "unix:path=/tmp/dbus-mine"
            with patch.dict(coverage.os.environ, {"XDG_RUNTIME_DIR": "/run/user/1000", "HOME": "/home/someone"}):
                coverage.spawn(["app"], self.tmp.name)
        env = popen.call_args.kwargs["env"]
        self.assertEqual(env["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/tmp/dbus-mine")
        for name in ("HOME", "XDG_RUNTIME_DIR"):
            self.assertTrue(env[name].startswith(self.tmp.name), name)
        self.assertEqual(env[coverage.MARKER], "run-1")
        coverage.STATE["procs"].clear()

    def truth(self, mode, changed_to):
        """truth_case for window 0 of a same-titled pair (ids 10, 11; frames 0, 1), the title of
        ``changed_to`` changing after the invocation."""
        titles = {10: "Welcome", 11: "Welcome"}
        invoked = []

        def probe(pid, *args):
            invoked.append(args[1])
            titles[changed_to] = "Untitled"
            return {"invoked": True}
        case = {"title": "Welcome", "same_title_windows": 2, "resolved": True, "by_title": [0, 1], "both": [0]}
        pair = [{"id": 10}, {"id": 11}]
        with patch.object(coverage, "set_a11y"), patch.object(coverage, "open_windows", return_value=(42, pair)), \
                patch.object(coverage, "focus"), patch.object(coverage, "narrow_case", return_value=case), \
                patch.object(coverage, "probe", side_effect=probe), patch.object(coverage, "close_all"), \
                patch.object(coverage, "windows", side_effect=lambda: {k: {"title": v} for k, v in titles.items()}), \
                patch.object(coverage.time, "sleep"):
            return coverage.truth_case({"key": "kate"}, "", self.tmp.name, 0, mode), invoked

    def test_ground_truth_and_its_negative_control(self):
        result, invoked = self.truth("focused", changed_to=10)
        self.assertEqual((invoked, result["expected"], result["correct"]), ([0], 10, True))
        result, _ = self.truth("focused", changed_to=11)  # the command reached the other window
        self.assertFalse(result["correct"])
        result, invoked = self.truth("other window's frame", changed_to=11)
        self.assertEqual((invoked, result["expected"], result["correct"]), ([1], 11, True))
        result, _ = self.truth("other window's frame", changed_to=10)  # it went to the focused one regardless
        self.assertFalse(result["correct"])

    def test_private_bus_starts_with_its_own_runtime_settings_and_no_session_bus(self):
        daemon = MagicMock(pid=1)
        daemon.stdout.readline.return_value = "unix:path=/tmp/dbus-new,guid=1\n"
        with patch.object(coverage.subprocess, "Popen", return_value=daemon) as popen, \
                patch.object(coverage.select, "select", return_value=([daemon.stdout], [], [])), \
                patch.dict(coverage.os.environ, {"XDG_RUNTIME_DIR": "/run/user/1000",
                                                 "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"}):
            coverage.start_private_bus(self.tmp.name)
        env = popen.call_args.kwargs["env"]
        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", env)  # its at-spi launcher must not reach the session's bus
        for name in ("XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "HOME"):
            self.assertTrue(env[name].startswith(self.tmp.name), name)
        self.assertEqual(env["GSETTINGS_BACKEND"], "keyfile")
        self.assertEqual(coverage.STATE["bus"], "unix:path=/tmp/dbus-new,guid=1")

    def test_restore_runs_once_and_stops_everything(self):
        coverage.STATE.update(windows={2}, workspace=None)
        with patch.object(coverage, "close_window") as close, patch.object(coverage, "stop_own_processes") as own, \
                patch.object(coverage, "stop_private_bus") as bus:
            coverage.restore()
            coverage.restore()
        close.assert_called_once_with(2, wait=0)
        own.assert_called_once()
        bus.assert_called_once()

    def test_a_signal_restores_and_exits(self):
        with patch.object(coverage, "restore") as restore:
            with self.assertRaises(SystemExit) as stopped:
                coverage.on_signal(15, None)
        restore.assert_called_once()
        self.assertEqual(stopped.exception.code, 143)

    def test_own_processes_never_include_the_run_itself(self):
        with patch.object(coverage.os, "getpid", return_value=100):
            self.assertEqual(coverage.own_processes(), [101, 300, 301])

    def test_survivors_of_sigterm_get_sigkill(self):
        signals = []
        with patch.object(coverage, "own_processes", side_effect=[[100, 300], [300]]), \
                patch.object(coverage, "kill", side_effect=lambda pid, sig: signals.append((pid, sig))), \
                patch.object(coverage.time, "sleep"):
            coverage.stop_own_processes()
        self.assertEqual(signals, [(100, coverage.signal.SIGTERM), (300, coverage.signal.SIGTERM),
                                   (300, coverage.signal.SIGKILL)])

    def test_ground_truth_counts_only_windows_whose_title_changed(self):
        self.assertEqual(coverage.changed_windows({1: "Welcome", 2: "Welcome"}, {1: "Untitled", 2: "Welcome"}), [1])
        self.assertEqual(coverage.changed_windows({1: "a", 2: "a"}, {1: "a", 2: "a"}), [])


if __name__ == "__main__":
    unittest.main()
