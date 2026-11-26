import contextlib
import fcntl
import io
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).parents[1] / "session"))
spec = importlib.util.spec_from_file_location("restore", Path(__file__).parents[1] / "session/restore.py")
restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore)


def asides(directory):
    """The saved sessions recovery set aside in ``directory``, by name: oldest first."""
    return sorted(Path(directory).glob("session-before-automatic-recovery-*.json"))


def window(ident, app, column):
    return {"id": ident, "app_id": app, "workspace_id": 1, "title": app,
            "layout": {"pos_in_scrolling_layout": [column, 1]}}


class NiriActions:
    def __init__(self, workspace, windows):
        self.workspace = workspace
        self.windows = windows
        self.focused = workspace.get("active_window_id")

    def __call__(self, _kind, action, *args):
        if action == "focus-window":
            self.focused = args[1]
            self.workspace["active_window_id"] = self.focused
            return
        columns = {}
        for item in self.windows:
            column = item["layout"]["pos_in_scrolling_layout"][0]
            columns.setdefault(column, []).append(item)
        ordered = [columns[key] for key in sorted(columns)]
        if action == "move-column-to-index":
            item = next(w for w in self.windows if w["id"] == self.focused)
            source = next(i for i, members in enumerate(ordered) if item in members)
            moving = ordered.pop(source)
            ordered.insert(args[0] - 1, moving)
        elif action == "consume-window-into-column":
            item = next(w for w in self.windows if w["id"] == self.focused)
            target = next(i for i, members in enumerate(ordered) if item in members)
            if target + 1 < len(ordered):
                ordered[target] += ordered.pop(target + 1)
        elif action == "consume-or-expel-window-left":
            item = next(w for w in self.windows if w["id"] == args[1])
            target = next(i for i, members in enumerate(ordered) if item in members)
            if target > 0:
                ordered[target - 1] += ordered.pop(target)
        elif action == "consume-or-expel-window-right":
            item = next(w for w in self.windows if w["id"] == args[1])
            target = next(i for i, members in enumerate(ordered) if item in members)
            if target + 1 < len(ordered):
                moving = ordered.pop(target)
                ordered[target] += moving
        for column, members in enumerate(ordered, 1):
            for row, item in enumerate(members, 1):
                item["layout"]["pos_in_scrolling_layout"] = [column, row]


class RecoveryTest(unittest.TestCase):
    def test_saved_order_matches_the_saved_windows_beside_columns_of_others(self):
        ws = {"id": 1}
        a, b, c = window(1, "a", 1), window(2, "b", 2), window(3, "c", 2)
        c["layout"]["pos_in_scrolling_layout"] = [2, 2]
        entries = [{"column": 1}, {"column": 2}, {"column": 2}]
        restored = list(zip(entries, [a, b, c]))
        self.assertTrue(restore.saved_order_matches(entries, restored, ws, [a, b, c]))
        # A window not in the saved session, such as an app opened at login, in a column of its own: before, between
        # or after the saved columns, the saved windows keep their order. Stacked into a saved column, it does not.
        for column in (1, 2, 3):
            shifted = [w | {"layout": {"pos_in_scrolling_layout": [w["layout"]["pos_in_scrolling_layout"][0]
                                                                   + (w["layout"]["pos_in_scrolling_layout"][0] >= column),
                                                                   w["layout"]["pos_in_scrolling_layout"][1]]}}
                       for w in (a, b, c)]
            with self.subTest(login_column=column):
                self.assertTrue(restore.saved_order_matches(entries, list(zip(entries, shifted)), ws,
                                                            shifted + [window(4, "d", column)]))
        stacked = window(4, "d", 2) | {"layout": {"pos_in_scrolling_layout": [2, 3]}}
        self.assertFalse(restore.saved_order_matches(entries, restored, ws, [a, b, c, stacked]))
        # A tiled window without a place in the strip: nothing can be confirmed.
        self.assertFalse(restore.saved_order_matches(entries, restored, ws, [a, b, c, window(4, "d", 3) | {"layout": {}}]))
        # An open Layer floats above the restored work and is not part of it.
        layer = {**window(5, "notes", 1), "is_floating": True, "layout": {"pos_in_scrolling_layout": None}}
        self.assertTrue(restore.saved_order_matches(entries, restored, ws, [a, b, c, layer]))
        self.assertFalse(restore.saved_order_matches(entries, restored, ws, [a, c, b | {"layout": {"pos_in_scrolling_layout": [3, 1]}}]))
        self.assertFalse(restore.saved_order_matches(entries, restored[:-1], ws, [a, b, c]))

    def test_automatic_save_refuses_ambiguous_windows_without_overwriting_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            stack, patches = self.saving(directory, [window(1, "app", 1), window(2, "app", 2)])
            with stack:
                for item in patches:
                    stack.enter_context(item)
                path = Path(directory) / "flow/session.json"
                path.parent.mkdir()
                path.write_text("previous snapshot")
                restore.automatic("auto-ready", path)
                marker = next(p for p in Path(directory).glob("flow-recovery-*") if p.suffix != ".lock")
                once = restore.automatic("auto-save", path, wait=False, pause=False)
                self.assertTrue(once["refused"], once)
                self.assertIn("multiple windows", once["error"])
                self.assertEqual(marker.read_text(), "ready")  # a refusal without pause leaves recovery ready
                paused = restore.automatic("auto-save", path, wait=False)
                self.assertTrue(paused["refused"], paused)
                state, _, reason = marker.read_text().partition("\n")
                self.assertEqual(state, "blocked")
                self.assertIn("multiple windows", reason)  # kept for a shell started again later
                self.assertNotIn("refused", restore.automatic("auto-save", path, wait=False))  # paused now
                self.assertEqual(path.read_text(), "previous snapshot")

    def test_a_group_mid_change_is_a_refusal_too(self):
        group = {"id": "g", "label": "G", "members": ["one", "two"], "layout": "stack", "pending_layout": "side-by-side"}
        with tempfile.TemporaryDirectory() as directory:
            stack, patches = self.saving(directory, [window(1, "one", 1), window(2, "two", 2)], groups=[group])
            with stack:
                for item in patches:
                    stack.enter_context(item)
                path = Path(directory) / "flow/session.json"
                restore.automatic("auto-ready", path)
                once = restore.automatic("auto-save", path, wait=False, pause=False)
                self.assertTrue(once["refused"], once)
                self.assertIn("Group layout", once["error"])
                marker = next(p for p in Path(directory).glob("flow-recovery-*") if p.suffix != ".lock")
                self.assertEqual(marker.read_text(), "ready")

    def test_a_niri_that_cannot_be_read_is_a_refusal_not_a_pause_at_once(self):
        with tempfile.TemporaryDirectory() as directory:
            stack, patches = self.saving(directory, [window(1, "one", 1)])
            with stack:
                for item in patches:
                    stack.enter_context(item)
                path = Path(directory) / "flow/session.json"
                restore.automatic("auto-ready", path)
                marker = next(p for p in Path(directory).glob("flow-recovery-*") if p.suffix != ".lock")
                for failure in (ValueError("No active workspace"), TimeoutError("timed out")):
                    restore.spaces.snapshot.side_effect = failure
                    once = restore.automatic("auto-save", path, wait=False, pause=False)
                    self.assertTrue(once["refused"], once)
                    self.assertEqual(marker.read_text(), "ready")
                restore.spaces.snapshot.side_effect = None
                with patch.object(restore, "focused_display", side_effect=TimeoutError("timed out")):  # its outputs
                    self.assertTrue(restore.automatic("auto-save", path, wait=False, pause=False)["refused"])
                self.assertEqual(marker.read_text(), "ready")
                self.assertTrue(restore.automatic("auto-save", path, wait=False, pause=False)["written"])

    def test_a_recovery_step_from_the_command_line_waits_for_the_marker_lock(self):
        import threading
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict("os.environ", {"XDG_RUNTIME_DIR": directory}), \
                patch.object(restore.spaces, "session_id", return_value="session"):
            path = Path(directory) / "session.json"
            restore.automatic("auto-pause", path)
            held = next(Path(directory).glob("flow-recovery-*.lock")).open("a")
            fcntl.flock(held, fcntl.LOCK_EX)
            threading.Timer(0.3, held.close).start()  # closing releases it
            started = time.monotonic()
            self.assertIn("ready", restore.automatic("auto-ready", path)["message"])
            self.assertGreaterEqual(time.monotonic() - started, 0.25)

    def test_an_open_layer_neither_pauses_automatic_saving_nor_enters_the_snapshot(self):
        layer = {"id": 9, "app_id": "notes", "workspace_id": 1, "title": "notes", "is_floating": True,
                 "layout": {"pos_in_scrolling_layout": None}}
        with tempfile.TemporaryDirectory() as directory:
            stack, patches = self.saving(directory, [window(1, "app", 1), layer])
            with stack:
                for item in patches:
                    stack.enter_context(item)
                path = Path(directory) / "flow/session.json"
                restore.automatic("auto-ready", path)
                self.assertTrue(restore.automatic("auto-save", path, wait=False)["written"])
                self.assertEqual([w["app_id"] for w in json.loads(path.read_text())["windows"]], ["app"])

    def test_automatic_start_restores_only_once_per_compositor_session(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"XDG_RUNTIME_DIR": directory}), \
                patch.object(restore.spaces, "session_id", return_value="session"), \
                patch.object(restore.subprocess, "run", return_value=Mock(returncode=0, stdout='{"message":"Restored 2 windows."}')) as run:
            path = Path(directory) / "session.json"
            path.write_text("saved")
            self.assertIn("ready", restore.automatic("auto-start", path)["message"])
            self.assertIn("ready", restore.automatic("auto-start", path)["message"])
            self.assertEqual(run.call_count, 1)

    def test_automatic_start_reopens_only_at_a_niri_instances_first_shell_start(self):
        # A shell's first start in a Niri instance writes its marker whatever the setting (auto-start when on,
        # auto-seen when off). A shell started again 10 s later, with recovery switched on since, finds "off" and
        # reopens nothing into the windows already open; the saved session is set aside, not replaced unseen.
        saved = json.dumps({"version": 1, "reopen_at_login": True, "windows": []})
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"XDG_RUNTIME_DIR": directory}), \
                patch.object(restore.spaces, "session_id", return_value="session"), \
                patch.object(restore.subprocess, "run",
                             return_value=Mock(returncode=0, stdout='{"message":"Restored 2 windows."}')) as run:
            path = Path(directory) / "session.json"
            path.write_text(saved)
            self.assertIn("off", restore.automatic("auto-seen", path)["message"])
            answer = restore.automatic("auto-start", path)
            self.assertNotIn("error", answer)
            run.assert_not_called()
            self.assertEqual((path.exists(), [p.read_text() for p in asides(directory)]), (False, [saved]))
            self.assertEqual(answer["set_aside"], str(asides(directory)[0]))
            marker = next(Path(directory).glob("flow-recovery-*[0-9a-f]"))
            self.assertEqual(marker.read_text(), "ready")
            restore.automatic("auto-seen", path)  # a later shell with the setting off leaves the marker alone
            self.assertEqual(marker.read_text(), "ready")

    def test_automatic_start_at_a_late_first_start(self):
        # A first shell start long after Niri started has no marker either. With no window open yet (a slow boot)
        # it reopens the saved session: the wall clock ten minutes on and a socket made now are for a clock window
        # like the one the marker replaced, which, put back, fails here. With a window open past ten minutes it is
        # work under way (a shell that failed at login and was started later): nothing reopened, the session kept.
        saved = json.dumps({"version": 1, "reopen_at_login": True, "windows": []})
        for open_apps, restores in (([], True), (["foot"], False)):
            with self.subTest(open_apps=open_apps), tempfile.TemporaryDirectory() as directory:
                socket = Path(directory) / "niri.wayland-1.4242.sock"
                socket.write_text("")
                with patch.dict("os.environ", {"XDG_RUNTIME_DIR": directory, "NIRI_SOCKET": str(socket)}), \
                        patch.object(restore.spaces, "session_id", return_value="session"), \
                        patch.object(restore.time, "time", return_value=time.time() + 600), \
                        patch.object(restore, "niri_age", return_value=3600), \
                        patch.object(restore.spaces, "query",
                                     return_value=([], [window(n, app, n) for n, app in enumerate(open_apps, 1)])), \
                        patch.object(restore.subprocess, "run",
                                     return_value=Mock(returncode=0, stdout='{"message":"Restored 2 windows."}')) as run:
                    path = Path(directory) / "session.json"
                    path.write_text(saved)
                    answer = restore.automatic("auto-start", path)
                    self.assertNotIn("error", answer)
                    self.assertEqual(run.call_count, 1 if restores else 0)
                    self.assertEqual((path.exists(), [p.read_text() for p in asides(directory)]),
                                     (True, []) if restores else (False, [saved]))

    def test_automatic_start_does_not_reopen_a_snapshot_from_before_it_reopened_sessions(self):
        # A Save session from before automatic recovery was on by default, possibly long ago, kept aside.
        for data, restores in (({"version": 1, "windows": []}, False),
                               ({"version": 1, "reopen_at_login": True, "windows": []}, True)):
            with self.subTest(data=data), tempfile.TemporaryDirectory() as directory, \
                    patch.dict("os.environ", {"XDG_RUNTIME_DIR": directory}), \
                    patch.object(restore.spaces, "session_id", return_value="session"), \
                                        patch.object(restore.subprocess, "run",
                                 return_value=Mock(returncode=0, stdout='{"message":"Restored 0 windows."}')) as run:
                path = Path(directory) / "session.json"
                path.write_text(json.dumps(data))
                answer = restore.automatic("auto-start", path)
                self.assertNotIn("error", answer)
                self.assertEqual(run.call_count, 1 if restores else 0)
                self.assertEqual(len(asides(directory)), 0 if restores else 1)
                if not restores:
                    self.assertIn(answer["set_aside"], answer["message"])

    def test_automatic_start_reopens_nothing_into_work_already_under_way(self):
        # No marker, but windows open in a Niri instance past ten minutes: a rollback and back, or a shell that
        # failed at login and was started later. The apps the saved session lists are work too (the user's usual
        # ones). Early, open windows are apps started with the session; with none open nothing is disturbed.
        saved = json.dumps({"version": 1, "reopen_at_login": True, "windows": [{"app_id": "one", "column": 1}]})
        for open_apps, age, restores in ((["one", "editor"], 3600, False), (["one"], 3600, False),
                                         (["one", "editor"], 30, True), ([], 3600, True)):
            with self.subTest(open_apps=open_apps, age=age), tempfile.TemporaryDirectory() as directory, \
                    patch.dict("os.environ", {"XDG_RUNTIME_DIR": directory}), \
                    patch.object(restore.spaces, "session_id", return_value="session"), \
                    patch.object(restore, "niri_age", return_value=age), \
                    patch.object(restore.spaces, "query",
                                 return_value=([], [window(n, app, n) for n, app in enumerate(open_apps, 1)])), \
                    patch.object(restore.subprocess, "run",
                                 return_value=Mock(returncode=0, stdout='{"message":"Restored 1 windows."}')) as run:
                path = Path(directory) / "session.json"
                path.write_text(saved)
                answer = restore.automatic("auto-start", path)
                self.assertNotIn("error", answer)
                self.assertEqual(run.call_count, 1 if restores else 0)
                self.assertEqual(len(asides(directory)), 0 if restores else 1)
                if not restores:
                    self.assertIn("one", answer["message"])

    def test_niri_age_is_read_on_the_boot_clock(self):
        # Niri's start from /proc against the uptime; a socket whose PID is not Niri's falls back to its ctime.
        ticks = os.sysconf("SC_CLK_TCK")
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory) / "proc"
            (proc / "4242").mkdir(parents=True)
            fields = ["S"] + ["0"] * 18 + [str(100 * ticks)] + ["0"] * 10  # field 22: started 100 s after boot
            (proc / "4242" / "stat").write_text("4242 (niri) " + " ".join(fields))
            (proc / "uptime").write_text("400.00 800.00\n")
            socket = Path(directory) / "niri.wayland-1.4242.sock"
            socket.write_text("")
            with patch.dict("os.environ", {"NIRI_SOCKET": str(socket)}):
                self.assertAlmostEqual(restore.niri_age(proc), 300.0)
                (proc / "4242" / "stat").write_text("4242 (python3) " + " ".join(fields))
                self.assertLess(restore.niri_age(proc), 60)  # the socket file was just made

    def test_an_instance_whose_age_cannot_be_read_counts_as_under_way(self):
        with patch.object(restore, "niri_age", side_effect=OSError("no socket")):
            self.assertTrue(restore.under_way_long_enough())
        with patch.object(restore, "niri_age", return_value=30):
            self.assertFalse(restore.under_way_long_enough())

    def test_a_second_set_aside_keeps_the_first(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            path.write_text("first")
            restore.set_aside(path)
            path.write_text("second")
            restore.set_aside(path)
            self.assertEqual([p.read_text() for p in asides(directory)], ["first", "second"])  # by name, by when

    def test_a_failed_recovery_keeps_its_reason_for_a_shell_started_again(self):
        saved = json.dumps({"version": 1, "reopen_at_login": True, "windows": []})
        for stdout, reason in (('{"message":"Restored 1 windows. app: launch failed"}', "app: launch failed"),
                               ("not json", "Automatic recovery failed")):
            with self.subTest(stdout=stdout), tempfile.TemporaryDirectory() as directory, \
                    patch.dict("os.environ", {"XDG_RUNTIME_DIR": directory}), \
                    patch.object(restore.spaces, "session_id", return_value="session"), \
                    patch.object(restore.spaces, "query", return_value=([], [])), \
                    patch.object(restore.subprocess, "run", return_value=Mock(returncode=0, stdout=stdout)):
                path = Path(directory) / "session.json"
                path.write_text(saved)
                self.assertIn(reason, restore.automatic("auto-start", path)["error"])
                again = restore.automatic("auto-start", path)
                self.assertTrue(again.get("paused"))
                self.assertIn(reason, again["error"])

    def test_a_saved_session_says_it_may_be_reopened_at_login(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            restore.save(path, {"id": 1, "active_window_id": 1}, [window(1, "one", 1)])
            self.assertIs(json.loads(path.read_text())["reopen_at_login"], True)

    def reopen_set_aside(self, missing=(), empty=True, registered=()):
        """Settings' Reopen it run through the command line against a fake Niri: the user's foot and firefox on
        workspace 1 (focused), workspace 2 after it on the display empty unless ``empty`` is false. The set-aside
        session lists the same two apps; ``missing`` have no desktop entry; ``registered`` are the Groups now
        registered. Returns the printed answer, the Niri
        actions, the windows and the file's bytes before and after; session.json is rewritten at each launch,
        as an automatic save of the work would."""
        world = {"focused": 1, "windows": [
            {"id": 10, "app_id": "foot", "workspace_id": 1, "layout": {"pos_in_scrolling_layout": [1, 1]}},
            {"id": 11, "app_id": "firefox", "workspace_id": 1, "layout": {"pos_in_scrolling_layout": [2, 1]}}]}
        if not empty:
            world["windows"].append({"id": 12, "app_id": "editor", "workspace_id": 2,
                                     "layout": {"pos_in_scrolling_layout": [1, 1]}})
        actions = []

        def workspaces():
            return [{"id": n, "idx": n, "output": "Virtual-1", "is_focused": n == world["focused"],
                     "active_window_id": None} for n in (1, 2)]

        def on(ws):
            return sorted((w for w in world["windows"] if w["workspace_id"] == ws),
                          key=lambda w: w["layout"]["pos_in_scrolling_layout"][0])

        def command(_kind, action, *args):
            actions.append((action, *args))
            if action == "focus-workspace":
                world["focused"] = args[0]
            elif action == "focus-window":
                world["focused_window"] = args[1]
            elif action == "move-column-to-index":
                columns = on(world["focused"])
                moving = next(w for w in columns if w["id"] == world["focused_window"])
                columns.remove(moving)
                columns.insert(args[0] - 1, moving)
                for column, w in enumerate(columns, 1):
                    w["layout"]["pos_in_scrolling_layout"] = [column, 1]

        def run(argv, **kwargs):  # gio launch: the app opens on the focused workspace, at its end
            app = Path(argv[-1]).stem
            path.write_text("an automatic save of the work")
            world["windows"].append({"id": 20 + len(world["windows"]), "app_id": app, "workspace_id": world["focused"],
                                     "layout": {"pos_in_scrolling_layout": [len(on(world["focused"])) + 1, 1]}})
            return Mock(returncode=0)

        aside_data = {"version": 1, "windows": [{"app_id": "firefox", "column": 1}, {"app_id": "foot", "column": 2}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            path.write_text(json.dumps({"version": 1, "reopen_at_login": True, "windows": []}))
            aside = path.with_name(restore.SET_ASIDE.format("20261126T102642Z-00"))
            aside.write_text(json.dumps(aside_data))
            before = aside.read_bytes()
            with patch.object(restore, "state_path", return_value=path), \
                    patch.object(sys, "argv", ["flow-recovery", "restore", str(aside)]), \
                    patch.object(restore.spaces, "query", side_effect=lambda *_: (workspaces(), world["windows"])), \
                    patch.object(restore.spaces, "snapshot",
                                 side_effect=lambda: (next(w for w in workspaces() if w["is_focused"]), world["windows"])), \
                    patch.object(restore.spaces, "command", side_effect=command), \
                    patch.object(restore.spaces, "desktop_file",
                                 side_effect=lambda app, _dirs: None if app in missing else Path(f"/apps/{app}.desktop")), \
                    patch.object(restore.spaces, "session_id", return_value="session"), \
                    patch.object(restore.group_store, "load", return_value=list(registered)), \
                    patch.object(restore.subprocess, "run", side_effect=run), \
                    patch.object(restore, "focused_display", return_value=(None, set())), \
                    patch.object(restore, "groups_unchanged_or_focused", return_value=True), \
                    patch.object(restore, "pins_unchanged_or_rebound", return_value=True), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                restore.main()
            return json.loads(out.getvalue()), actions, world["windows"], before, aside.read_bytes()

    def test_reopening_a_set_aside_session_leaves_the_work_alone(self):
        # The session was set aside because work is open, mostly in the apps it lists: reopened on the display's
        # empty workspace, each app launched there, the user's own windows of them neither taken for it nor moved.
        answer, actions, windows, before, after = self.reopen_set_aside()
        self.assertEqual(answer, {"message": "Reopened 2 of 2 windows on an empty workspace.", "complete": True})
        self.assertEqual(actions[0], ("focus-workspace", 2))
        self.assertFalse([a for a in actions if a[0] == "focus-window" and a[-1] in (10, 11)])
        self.assertEqual([(w["id"], w["workspace_id"], w["layout"]["pos_in_scrolling_layout"]) for w in windows[:2]],
                         [(10, 1, [1, 1]), (11, 1, [2, 1])])
        self.assertEqual(sorted((w["app_id"], w["layout"]["pos_in_scrolling_layout"][0])
                                for w in windows if w["workspace_id"] == 2), [("firefox", 1), ("foot", 2)])
        self.assertEqual(after, before)  # read in place, kept; session.json rewritten meanwhile did not stop it

    def test_a_set_aside_session_is_reopened_beside_groups_registered_since(self):
        # A side-by-side Group the session does not mention (made after it was saved) is not its to restore: as
        # an import, only the Groups it saved are passed, or every Reopen would refuse it as incomplete.
        group = {"id": "g", "label": "Work", "members": ["one", "two"], "layout": "side-by-side"}
        answer, _, _, _, _ = self.reopen_set_aside(registered=[group])
        self.assertIs(answer["complete"], True)

    def test_a_set_aside_session_reopened_in_part_is_not_complete(self):
        answer, _, _, _, _ = self.reopen_set_aside(missing=("foot",))
        self.assertIs(answer["complete"], False)
        self.assertIn("foot: no matching installed application", answer["message"])

    def test_a_set_aside_session_is_not_reopened_without_an_empty_workspace(self):
        with self.assertRaisesRegex(ValueError, "no empty workspace; nothing was reopened"):
            self.reopen_set_aside(empty=False)

    def test_restore_reads_only_a_set_aside_file_beside_the_session(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "session.json"
            path.parent.mkdir()
            elsewhere = Path(directory) / restore.SET_ASIDE.format("20261126T102642Z-00")
            for name in (str(path), str(path.with_name("spaces.json")), str(elsewhere),
                         str(path.parent / ".." / elsewhere.name)):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    restore.set_aside_source(path, name)
            beside = path.with_name(elsewhere.name)
            self.assertEqual(restore.set_aside_source(path, str(beside)), beside)
            self.assertEqual(restore.set_aside_source(path, beside.name), beside)  # a bare name: beside session.json
            link = path.with_name(restore.SET_ASIDE.format("20261126T102643Z-00"))
            link.symlink_to(path)
            with self.assertRaises(ValueError):
                restore.set_aside_source(path, str(link))

    def test_restore_takes_at_most_one_file(self):
        with patch.object(sys, "argv", ["flow-recovery", "restore", "a", "b"]), \
                self.assertRaisesRegex(ValueError, "one set-aside saved session"):
            restore.main()

    def test_the_command_line_runs_auto_seen(self):
        with patch.object(sys, "argv", ["flow-recovery", "auto-seen"]), \
                patch.object(restore, "automatic", return_value={}) as automatic, \
                contextlib.redirect_stdout(io.StringIO()):
            restore.main()
        self.assertEqual(automatic.call_args.args[0], "auto-seen")

    def test_automatic_start_says_a_pause_from_earlier_in_the_session_was_already_told(self):
        # With its reason, which a shell started again later shows in Settings.
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"XDG_RUNTIME_DIR": directory}), \
                patch.object(restore.spaces, "session_id", return_value="session"):
            path = Path(directory) / "session.json"
            restore.automatic("auto-pause", path)
            answer = restore.automatic("auto-start", path)
            self.assertTrue(answer.get("paused"))
            self.assertEqual(answer["error"], restore.PAUSED)
            marker = next(Path(directory).glob("flow-recovery-*[0-9a-f]"))
            restore.pause_marker(marker, "app: multiple windows need manual recovery after restart")
            answer = restore.automatic("auto-start", path)
            self.assertEqual((answer.get("paused"), answer["error"]),
                             (True, "app: multiple windows need manual recovery after restart"))

    def test_automatic_recovery_preserves_snapshot_after_partial_restore(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"XDG_RUNTIME_DIR": directory}), \
                patch.object(restore.spaces, "session_id", return_value="session"), \
                patch.object(restore.subprocess, "run", return_value=Mock(returncode=0, stdout=json.dumps({"message": "Restored 2 windows. Session order needs manual recovery"}))) as run:
            path = Path(directory) / "session.json"
            path.write_text("saved")
            self.assertIn("paused", restore.automatic("auto-start", path)["error"])
            self.assertIn("paused", restore.automatic("auto-start", path)["error"])
            self.assertIn("paused", restore.automatic("auto-save", path)["error"])
            self.assertEqual(run.call_count, 1)
            self.assertEqual(path.read_text(), "saved")

    def test_automatic_save_skips_empty_and_pauses_after_failure(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"XDG_RUNTIME_DIR": directory}), \
                patch.object(restore.spaces, "session_id", return_value="session"), \
                patch.object(restore.spaces, "snapshot", return_value=({"id": 1}, [])) as snapshot, \
                patch.object(restore, "save_locked", side_effect=ValueError("unsafe")) as run:
            path = Path(directory) / "session.json"
            path.write_text("saved")
            restore.automatic("auto-ready", path)
            self.assertIn("empty", restore.automatic("auto-save", path)["message"])
            run.assert_not_called()
            snapshot.return_value = ({"id": 1}, [window(1, "app", 1)])
            self.assertEqual(restore.automatic("auto-save", path)["error"], "unsafe")
            self.assertIn("paused", restore.automatic("auto-save", path)["error"])
            self.assertEqual(path.read_text(), "saved")

    def saving(self, directory, windows, groups=()):
        """What an automatic save reads, patched: a ready marker in ``directory``, one workspace of windows."""
        return contextlib.ExitStack(), [
            patch.dict("os.environ", {"XDG_RUNTIME_DIR": directory, "XDG_STATE_HOME": directory}),
            patch.object(restore.spaces, "session_id", return_value="session"),
            patch.object(restore.spaces, "snapshot", return_value=({"id": 1, "active_window_id": 1}, windows)),
            patch.object(restore.group_store, "load", return_value=list(groups)),
            patch.object(restore, "focused_display", return_value=(None, set())),
            patch.object(restore.spaces, "desktop_file", return_value=Path("/tmp/app.desktop"))]

    def test_an_automatic_save_runs_in_this_process_and_leaves_an_unchanged_snapshot_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            stack, patches = self.saving(directory, [window(1, "one", 1), window(2, "two", 2)])
            with stack, patch.object(restore.subprocess, "run", side_effect=AssertionError("no process")):
                for item in patches:
                    stack.enter_context(item)
                path = Path(directory) / "flow/session.json"
                restore.automatic("auto-ready", path)
                first = restore.automatic("auto-save", path, wait=False)
                self.assertTrue(first["written"], first)
                self.assertEqual([w["app_id"] for w in json.loads(path.read_text())["windows"]], ["one", "two"])
                stamp = path.stat().st_mtime_ns
                reads = restore.spaces.snapshot.call_count
                reading = restore.spaces.snapshot.return_value

                def under_the_locks():
                    with (path.parent / "session.lock").open("a") as other, self.assertRaises(BlockingIOError):
                        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)  # this save holds it
                    return reading
                restore.spaces.snapshot.side_effect = under_the_locks
                again = restore.automatic("auto-save", path, wait=False)
                restore.spaces.snapshot.side_effect = None
                self.assertEqual(restore.spaces.snapshot.call_count - reads, 1)  # Niri read once, under the locks
                self.assertFalse(again["written"], again)
                self.assertEqual(path.stat().st_mtime_ns, stamp)
                restore.spaces.snapshot.return_value = ({"id": 1, "active_window_id": 2},
                                                        [window(1, "one", 1), window(2, "two", 2)])
                self.assertTrue(restore.automatic("auto-save", path, wait=False)["written"])
                self.assertEqual([w["focused"] for w in json.loads(path.read_text())["windows"]], [False, True])

    def test_an_automatic_save_without_waiting_gives_way_to_a_held_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            stack, patches = self.saving(directory, [window(1, "one", 1)])
            with stack:
                for item in patches:
                    stack.enter_context(item)
                path = Path(directory) / "flow/session.json"
                restore.automatic("auto-ready", path)
                path.parent.mkdir(exist_ok=True)
                marker = next(Path(directory).glob("flow-recovery-*.lock"))
                for held in (path.parent / "session.lock", marker):  # an action; another recovery step
                    with held.open("a") as other:
                        fcntl.flock(other, fcntl.LOCK_EX)
                        with self.assertRaises(BlockingIOError):
                            restore.automatic("auto-save", path, wait=False)
                self.assertFalse(path.exists())
                self.assertTrue(restore.automatic("auto-save", path, wait=False)["written"])

    def test_a_bug_in_an_automatic_save_pauses_it(self):
        with tempfile.TemporaryDirectory() as directory:
            stack, patches = self.saving(directory, [window(1, "one", 1)])
            with stack, patch.object(restore, "save", side_effect=TypeError("bug")):
                for item in patches:
                    stack.enter_context(item)
                path = Path(directory) / "flow/session.json"
                restore.automatic("auto-ready", path)
                self.assertIn("preserved", restore.automatic("auto-save", path, wait=False)["error"])
                self.assertIn("paused", restore.automatic("auto-save", path, wait=False)["error"])

    def test_recovery_accepts_only_matching_group_focus_updates(self):
        old = [{"id": "g", "label": "Writing", "members": ["one", "two"]}]
        new = [{**old[0], "last_focused_app": "one"}]
        ws, windows = {"id": 1, "active_window_id": 1}, [window(1, "one", 1), window(2, "two", 1)]
        self.assertTrue(restore.groups_unchanged_or_focused(old, new, ws, windows))
        self.assertFalse(restore.groups_unchanged_or_focused(old, [{**new[0], "label": "Other"}], ws, windows))
        self.assertFalse(restore.groups_unchanged_or_focused(old, [{**new[0], "last_focused_app": "two"}], ws, windows))
        self.assertFalse(restore.groups_unchanged_or_focused(old, [], ws, windows))
        # A refreshed window binding is not an edit to the Group.
        bound = [{**old[0], "bound": {"session": "s", "windows": {"one": 1}}}]
        self.assertTrue(restore.groups_unchanged_or_focused(old, bound, ws, windows))

    def test_recovery_accepts_only_unambiguous_rebinding_of_old_session_pin(self):
        old = [{"id": "pin", "app_id": "files", "label": "Home", "session": "old", "window_id": 9}]
        new = [{**old[0], "session": "current", "window_id": 2}]
        windows = [window(2, "files", 1)]
        self.assertTrue(restore.pins_unchanged_or_rebound(old, new, "current", windows))
        self.assertFalse(restore.pins_unchanged_or_rebound(old, new, "current", windows + [window(3, "files", 2)]))
        self.assertFalse(restore.pins_unchanged_or_rebound(old, [{**new[0], "label": "Other"}], "current", windows))
        self.assertFalse(restore.pins_unchanged_or_rebound(old, [{**new[0], "window_id": 3}], "current", windows))
        self.assertFalse(restore.pins_unchanged_or_rebound([{**old[0], "session": "current"}], new, "current", windows))
        self.assertFalse(restore.pins_unchanged_or_rebound(old, [], "current", windows))

    def test_group_pin_rebind_requires_matching_group_windows_and_focus(self):
        group = {"id": "g", "label": "Work", "members": ["one", "two"], "layout": "side-by-side"}
        old = [{"id": "p", "group_id": "g", "label": "Work", "app_id": "one", "members": ["one", "two"],
                "session": "old", "window_id": 10, "window_ids": [10, 11], "last_focused_app": "one"}]
        new = [{**old[0], "session": "current", "window_id": 1, "window_ids": [1, 2], "last_focused_app": "two"}]
        ws, windows = {"id": 1, "active_window_id": 2}, [window(1, "one", 1), window(2, "two", 2)]
        self.assertTrue(restore.pins_unchanged_or_rebound(old, new, "current", windows, ws, [group]))
        self.assertFalse(restore.pins_unchanged_or_rebound(old, [{**new[0], "window_ids": [2, 1]}], "current", windows, ws, [group]))
        self.assertFalse(restore.pins_unchanged_or_rebound(old, [{**new[0], "label": "Changed"}], "current", windows, ws, [group]))
        self.assertFalse(restore.pins_unchanged_or_rebound(old, new, "current", windows + [window(3, "one", 3)], ws, [group]))
        self.assertFalse(restore.pins_unchanged_or_rebound(old, new, "current", windows, {"id": 1, "active_window_id": 1}, [group]))
        self.assertFalse(restore.pins_unchanged_or_rebound([{**old[0], "session": "current"}], new, "current", windows, ws, [group]))

    def test_pinned_side_group_and_neighbor_preserve_snapshot_order(self):
        ws = {"id": 1, "active_window_id": 3}
        windows = [window(1, "neighbor", 1), window(2, "one", 2), window(3, "two", 3)]
        group = {"id": "g", "label": "Work", "members": ["one", "two"], "layout": "side-by-side"}
        pins = [{"id": "n", "app_id": "neighbor"}, {"id": "p", "group_id": "g", "app_id": "one", "members": ["one", "two"]}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            restore.save(path, ws, windows, "current", pins=pins, registered=[group])
            saved = json.loads(path.read_text())
            actions = Mock()
            report, count = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions,
                                            pinned={"neighbor", "one", "two"}, pinned_groups=pins,
                                            session="current", registered=[group])
            self.assertEqual((report, count), ([], 3))
            self.assertNotIn("move-column-to-index", [call.args[1] for call in actions.call_args_list])
            actions.assert_any_call("action", "set-window-width", "--id", 2, "50%")
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "snapshot was not replaced"):
                restore.save(path, ws, windows, "current", pins=pins + [{"app_id": "two"}], registered=[group])
            self.assertEqual(path.read_bytes(), before)
            with self.assertRaisesRegex(ValueError, "conflicting pins"):
                restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), Mock(),
                                pinned_groups=pins + [{"app_id": "two"}], session="current", registered=[group])

    def test_save_warnings_identify_windows_that_need_manual_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            apps = Path(directory) / "applications"
            apps.mkdir()
            (apps / "known.desktop").touch()
            entries = [{"app_id": "known", "column": 1}, {"app_id": "known", "column": 2},
                       {"app_id": "missing", "column": None}, {"app_id": None, "column": 3}]
            warnings = restore.recovery_warnings(entries, [directory])
            self.assertTrue(any("known: multiple windows" in warning for warning in warnings))
            self.assertTrue(any("missing: no matching" in warning for warning in warnings))
            self.assertTrue(any("without an application ID" in warning for warning in warnings))
            self.assertTrue(any("Floating or untiled" in warning for warning in warnings))
            self.assertFalse(any("known: no matching" in warning for warning in warnings))
            self.assertEqual(restore.recovery_warnings([{"app_id": "known", "column": 1}], [directory]), [])

    def test_save_preserves_existing_snapshot_for_side_by_side_or_pending_group(self):
        ws = {"id": 1, "active_window_id": 1}
        for group in ({"id": "g", "members": ["one", "two"], "layout": "side-by-side"},
                      {"id": "g", "members": ["one", "two"], "layout": "stack", "pending_layout": "side-by-side"}):
            with self.subTest(group=group), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "state.json"
                path.write_text('{"version":1,"windows":[]}')
                before = path.read_bytes()
                with self.assertRaisesRegex(ValueError, "snapshot was not replaced"):
                    restore.save(path, ws, [window(1, "one", 1)], registered=[group])
                self.assertEqual(path.read_bytes(), before)

    def test_a_layer_of_a_group_app_does_not_block_saving_the_group(self):
        ws = {"id": 1, "active_window_id": 1}
        stacked = [window(1, "one", 1), {**window(2, "two", 1), "layout": {"pos_in_scrolling_layout": [1, 2]}}]
        layer = {**window(3, "one", 1), "is_floating": True, "layout": {"pos_in_scrolling_layout": None}}
        group = {"id": "g", "label": "Pair", "members": ["one", "two"]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            self.assertEqual(restore.save(path, ws, stacked + [layer], "current", registered=[group]), 2)
            self.assertEqual([w["window_id"] for w in json.loads(path.read_text())["windows"]], [1, 2])

    def test_restore_refuses_to_change_side_by_side_or_transitioning_groups(self):
        ws = {"id": 1, "active_window_id": 1}
        windows = [window(1, "one", 1), window(2, "two", 2)]
        saved = {"version": 1, "windows": [{"app_id": "one"}, {"app_id": "two"}]}
        for group in (
                {"id": "g", "members": ["one", "two"], "layout": "side-by-side"},
                {"id": "g", "members": ["one", "two"], "layout": "stack", "pending_layout": "side-by-side"}):
            with self.subTest(group=group):
                launch, action = Mock(), Mock()
                before = json.dumps(windows, sort_keys=True)
                with self.assertRaisesRegex(ValueError, "cannot safely restore|incomplete or changed"):
                    restore.restore(saved, ws, windows, [], launch, lambda: (ws, windows), action,
                                    registered=[group])
                launch.assert_not_called()
                action.assert_not_called()
                self.assertEqual(json.dumps(windows, sort_keys=True), before)

    @patch.object(restore.spaces, "display_state", return_value=({"Virtual-1": {"logical": {"width": 1000, "height": 700}}}, []))
    def test_side_group_snapshot_recovers_custom_widths(self, _):
        ws = {"id": 1, "active_window_id": 2, "output": "Virtual-1"}
        windows = [window(1, "one", 1), window(2, "two", 2)]
        windows[0]["layout"]["tile_size"] = [650, 700]
        windows[1]["layout"]["tile_size"] = [350, 700]
        group = {"id": "g", "label": "Work", "members": ["one", "two"], "layout": "side-by-side"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            restore.save(path, ws, windows, "current", registered=[group])
            saved = json.loads(path.read_text())
            self.assertEqual(saved["groups"][0]["side_ratio"], 0.65)
            actions = Mock()
            restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions,
                            session="current", registered=[group])
            actions.assert_any_call("action", "set-window-width", "--id", 1, "65%")
            actions.assert_any_call("action", "set-window-width", "--id", 2, "35%")
            windows[0]["layout"]["tile_size"] = [900, 700]
            windows[1]["layout"]["tile_size"] = [100, 700]
            report, _ = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), Mock(),
                                        session="current", registered=[group])
            self.assertIn("Application size constraints prevented recovery of Group proportions", report)
            # 650 and 380 are 0.63 and 0.37 of their total, and of the display 0.65 and 0.38, each near
            # 65/35, but the row is wider than the display.
            windows[0]["layout"]["tile_size"] = [650, 700]
            windows[1]["layout"]["tile_size"] = [380, 700]
            report, _ = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), Mock(),
                                        session="current", registered=[group])
            self.assertIn("Application size constraints prevented recovery of Group proportions", report)
            def finish_resize(_):
                windows[0]["layout"]["tile_size"] = [650, 700]
                windows[1]["layout"]["tile_size"] = [350, 700]
            report, _ = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), Mock(),
                                        wait=finish_resize, session="current", registered=[group])
            self.assertEqual(report, [])
            saved["groups"][0]["side_ratio"] = 0
            actions.reset_mock()
            with self.assertRaisesRegex(ValueError, "proportions"):
                restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions,
                                session="current", registered=[group])
            actions.assert_not_called()

    def test_side_group_recovery_measures_against_its_own_display(self):
        ws = {"id": 1, "active_window_id": 2, "output": "eDP-1"}
        windows = [window(1, "one", 1), window(2, "two", 2)]
        windows[0]["layout"]["tile_size"] = [832, 700]
        windows[1]["layout"]["tile_size"] = [448, 700]
        group = {"id": "g", "label": "Work", "members": ["one", "two"], "layout": "side-by-side", "side_ratio": 0.65}
        saved = {"version": 1, "session": "current", "groups": [group],
                 "windows": [{"app_id": "one", "column": 1}, {"app_id": "two", "column": 2, "focused": True}]}
        for outputs, expected in (
                ({"eDP-1": {"logical": {"width": 1280, "height": 800}}}, []),
                ({}, ["Group proportions were not checked: Niri did not report the display's size"])):
            with self.subTest(outputs=outputs), \
                 patch.object(restore.spaces, "display_state", return_value=(outputs, [])):
                report, count = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), Mock(),
                                                session="current", registered=[group])
            self.assertEqual((report, count), (expected, 2))

    def test_unpinned_side_group_snapshot_and_recovery(self):
        ws = {"id": 1, "active_window_id": 2}
        windows = [window(1, "one", 1), window(2, "two", 2)]
        group = {"id": "g", "label": "Work", "members": ["one", "two"], "layout": "side-by-side"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            restore.save(path, ws, windows, "current", registered=[group])
            saved = json.loads(path.read_text())
            self.assertEqual(saved["groups"], [group])
            self.assertEqual([e["column"] for e in saved["windows"]], [1, 2])
            actions = Mock()
            report, count = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions,
                                            session="current", registered=[group])
            self.assertEqual((report, count), ([], 2))
            actions.assert_any_call("action", "set-window-width", "--id", 1, "50%")
            actions.assert_any_call("action", "set-window-width", "--id", 2, "50%")
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "snapshot was not replaced"):
                restore.save(path, ws, windows, "current", pins=[{"app_id": "one"}], registered=[group])
            self.assertEqual(path.read_bytes(), before)
            with self.assertRaisesRegex(ValueError, "cannot safely restore"):
                restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), Mock(),
                                pinned={"one"}, session="current", registered=[group])

    def test_side_group_recovery_beside_a_window_opened_at_login(self):
        group = {"id": "g", "label": "Work", "members": ["one", "two"], "layout": "side-by-side", "side_ratio": 0.6}
        saved = {"version": 1, "groups": [group], "windows": [{"app_id": "one", "column": 1},
                                                                {"app_id": "two", "column": 2}]}
        ws = {"id": 1, "active_window_id": 2}
        # Before the Group, the window opened at login is a space of its own, and the saved split is applied.
        windows = [window(9, "login", 1), window(1, "one", 2), window(2, "two", 3)]
        actions = Mock()
        report, count = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions,
                                        session="current", registered=[group])
        self.assertEqual((report, count), ([], 2))
        actions.assert_any_call("action", "set-window-width", "--id", 1, "60%")
        actions.assert_any_call("action", "set-window-width", "--id", 2, "40%")
        # Between the Group's apps it parts them: the report says so and points to the repair.
        windows = [window(1, "one", 1), window(9, "login", 2), window(2, "two", 3)]
        actions = Mock()
        report, _ = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions,
                                    session="current", registered=[group])
        self.assertEqual(report, ["Side-by-side Group order needs manual recovery: another window is between its "
                                  "apps; choose Repair Group layout"])
        self.assertNotIn("set-window-width", [call.args[1] for call in actions.call_args_list])

    def test_side_group_recovery_does_not_resize_wrong_order(self):
        ws = {"id": 1, "active_window_id": 1}
        windows = [window(1, "one", 2), window(2, "two", 1)]
        group = {"id": "g", "label": "Work", "members": ["one", "two"], "layout": "side-by-side"}
        saved = {"version": 1, "groups": [group], "windows": [{"app_id": "one", "column": 1},
                                                                    {"app_id": "two", "column": 2}]}
        actions = Mock()
        report, count = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions,
                                        session="current", registered=[group])
        self.assertEqual(count, 2)
        self.assertTrue(any("manual recovery" in message for message in report))
        self.assertNotIn("set-window-width", [call.args[1] for call in actions.call_args_list])

    def test_snapshot_order_and_focus(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            restore.save(path, {"id": 1, "active_window_id": 2}, [window(2, "two", 2), window(1, "one", 1)], "session")
            saved = json.loads(path.read_text())
            self.assertEqual([e["app_id"] for e in saved["windows"]], ["one", "two"])
            self.assertTrue(saved["windows"][1]["focused"])
            self.assertEqual([e["column"] for e in saved["windows"]], [1, 2])
            self.assertEqual((saved["session"], [e["window_id"] for e in saved["windows"]]), ("session", [1, 2]))

    def test_saved_group_keeps_identity_and_label_across_snapshots(self):
        ws = {"id": 1, "active_window_id": 1}
        windows = [window(1, "one", 1), window(2, "two", 1)]
        windows[1]["layout"]["pos_in_scrolling_layout"] = [1, 2]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            restore.save(path, ws, windows, "first")
            saved = json.loads(path.read_text())
            group = saved["groups"][0]
            group["label"] = "Studio"
            path.write_text(json.dumps(saved))
            windows[0]["title"] = "Changed title"
            restore.save(path, ws, windows, "second")
            self.assertEqual(json.loads(path.read_text())["groups"], [group])
            windows[0]["layout"]["pos_in_scrolling_layout"] = [1, 2]
            windows[1]["layout"]["pos_in_scrolling_layout"] = [1, 1]
            restore.save(path, ws, windows, "third")
            self.assertEqual(json.loads(path.read_text())["groups"], [{**group, "members": ["two", "one"]}])

    def test_ambiguous_group_is_not_saved_as_a_persistent_identity(self):
        ws = {"id": 1}
        windows = [window(1, "one", 1), window(2, "two", 1), window(3, "one", 2)]
        windows[1]["layout"]["pos_in_scrolling_layout"] = [1, 2]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            restore.save(path, ws, windows)
            self.assertEqual(json.loads(path.read_text())["groups"], [])

    def test_missing_group_members_survive_later_saves(self):
        ws = {"id": 1, "active_window_id": 1}
        pair = [window(1, "one", 1), window(2, "two", 1)]
        pair[1]["layout"]["pos_in_scrolling_layout"] = [1, 2]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            restore.save(path, ws, pair, "first")
            group = json.loads(path.read_text())["groups"][0]
            self.assertEqual(restore.save(path, ws, [pair[0]], "second"), 1)
            saved = json.loads(path.read_text())
            self.assertEqual(saved["groups"], [group])
            self.assertEqual([(e["app_id"], e["column"], e["window_id"]) for e in saved["windows"]],
                             [("one", 1, 1), ("two", 1, None)])
            restore.save(path, ws, [], "third")
            saved = json.loads(path.read_text())
            self.assertEqual(saved["groups"], [group])
            self.assertEqual([e["app_id"] for e in saved["windows"]], ["one", "two"])

    def test_ambiguous_missing_group_preserves_snapshot(self):
        ws = {"id": 1}
        pair = [window(1, "one", 1), window(2, "two", 1)]
        pair[1]["layout"]["pos_in_scrolling_layout"] = [1, 2]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            restore.save(path, ws, pair)
            before = path.read_text()
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                restore.save(path, ws, [pair[0], window(3, "one", 2)])
            self.assertEqual(path.read_text(), before)
            other = window(2, "two", 1)
            other["workspace_id"] = 2
            with self.assertRaisesRegex(ValueError, "another workspace"):
                restore.save(path, ws, [pair[0], other])
            self.assertEqual(path.read_text(), before)

    def test_save_does_not_orphan_a_separated_pinned_group(self):
        ws = {"id": 1}
        pair = [window(1, "one", 1), window(2, "two", 1)]
        pair[1]["layout"]["pos_in_scrolling_layout"] = [1, 2]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            restore.save(path, ws, pair)
            before = path.read_text()
            group_id = json.loads(before)["groups"][0]["id"]
            pin = {"app_id": "one", "members": ["one", "two"], "group_id": group_id}
            pair[1]["layout"]["pos_in_scrolling_layout"] = [2, 1]
            with self.assertRaisesRegex(ValueError, "pinned Group"):
                restore.save(path, ws, pair, pins=[pin])
            self.assertEqual(path.read_text(), before)
            restore.save(path, ws, pair)
            self.assertEqual(json.loads(path.read_text())["groups"], [])

    def test_existing_window_is_focused_without_relaunch(self):
        launch, action = Mock(), Mock()
        ws = {"id": 1, "active_window_id": 1}
        windows = [window(1, "one", 1)]
        report, count = restore.restore({"version": 1, "windows": [{"app_id": "one", "focused": True}]}, ws,
                                        windows, [], launch, lambda: (ws, windows), action)
        self.assertEqual((report, count), ([], 1))
        launch.assert_not_called()
        action.assert_any_call("action", "focus-window", "--id", 1)

    def test_single_window_recovery_beside_a_live_pin_not_in_the_session(self):
        ws = {"id": 1, "active_window_id": 2}
        saved = {"version": 1, "windows": [{"app_id": "one", "column": 1}]}
        windows = [window(1, "pin", 1), window(2, "one", 2)]
        actions = Mock()
        report, count = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions,
                                        pinned={"pin"})
        # The pin is not in the saved session: a space of its own, before the saved one, which keeps its order.
        self.assertEqual((report, count), ([], 1))
        self.assertNotIn("move-column-to-index", [call.args[1] for call in actions.call_args_list])

    def test_pinned_layout_already_in_saved_order_needs_no_manual_recovery(self):
        ws = {"id": 1, "active_window_id": 1}
        windows = [window(1, "pin", 1), window(2, "one", 2), window(3, "two", 2)]
        windows[2]["layout"]["pos_in_scrolling_layout"] = [2, 2]
        saved = {"version": 1, "windows": [{"app_id": "pin", "column": 1},
                                                {"app_id": "one", "column": 2}, {"app_id": "two", "column": 2}]}
        actions = Mock()
        report, count = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions, pinned={"pin"})
        self.assertEqual((report, count), ([], 3))
        self.assertNotIn("move-column-to-index", [call.args[1] for call in actions.call_args_list])

    def test_single_window_recovery_stops_if_a_pin_appears_before_move(self):
        # A pinned app that maps while recovery works (one opened at login): nothing more is moved, and the order
        # is judged as for any pin. In a column of its own it leaves the saved order; stacked with it, it does not.
        ws = {"id": 1, "active_window_id": 1}
        saved = {"version": 1, "windows": [{"app_id": "one", "column": 1}]}
        windows = [window(1, "one", 1)]
        stacked = window(2, "pin", 1) | {"layout": {"pos_in_scrolling_layout": [1, 2]}}
        for pin, expected in ((window(2, "pin", 2), []),
                              (stacked, ["Pinned spaces keep their positions; session order needs manual recovery"])):
            with self.subTest(pin=pin["layout"]):
                snapshots = Mock(side_effect=[(ws, windows), (ws, windows + [pin]), (ws, windows + [pin])])
                actions = Mock()
                report, _ = restore.restore(saved, ws, windows, [], Mock(), snapshots, actions, pinned={"pin"})
                self.assertEqual(report, expected)
                self.assertNotIn("move-column-to-index", [call.args[1] for call in actions.call_args_list])

    def test_single_window_recovery_accepts_pins_reordered_since_the_save(self):
        # Saved with Mail first; the pins were swapped since, and a refresh during the launches put Notes first.
        # The pins keep their new order; the unpinned windows must still be in their saved order.
        saved = {"version": 1, "windows": [{"app_id": "mail", "column": 1}, {"app_id": "notes", "column": 2},
                                           {"app_id": "files", "column": 3}, {"app_id": "scratch", "column": 4}]}
        pins = [{"app_id": "notes"}, {"app_id": "mail"}]
        for unpinned, expected in ((("files", "scratch"), []),
                                   (("scratch", "files"),
                                    ["Pinned spaces keep their positions; session order needs manual recovery"])):
            with self.subTest(unpinned=unpinned):
                ws = {"id": 1, "active_window_id": 1}
                windows = [window(2, "notes", 1), window(1, "mail", 2),
                           window(3, unpinned[0], 3), window(4, unpinned[1], 4)]
                actions = Mock()
                report, count = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions,
                                                pinned={"notes", "mail"}, pinned_groups=pins)
                self.assertEqual((report, count), (expected, 4))
                self.assertNotIn("move-column-to-index", [call.args[1] for call in actions.call_args_list])

    def test_single_window_recovery_accepts_reordered_pins_that_appear_after_the_focus(self):
        # The first look finds no pinned app here (both still on their way, on another workspace); after the first
        # focus they are here, already in their new pin order. Judged as at the top of the loop: restored.
        saved = {"version": 1, "windows": [{"app_id": "mail", "column": 1}, {"app_id": "notes", "column": 2},
                                           {"app_id": "files", "column": 3}, {"app_id": "scratch", "column": 4}]}
        pins = [{"app_id": "notes"}, {"app_id": "mail"}]
        ws = {"id": 1, "active_window_id": 3}
        here = [window(2, "notes", 1), window(1, "mail", 2), window(3, "files", 3), window(4, "scratch", 4)]
        away = [w | {"workspace_id": 2} if w["app_id"] in ("notes", "mail") else w for w in here]
        looks = []

        def snapshot():
            looks.append(1)
            return ws, (away if len(looks) == 1 else here)
        actions = Mock()
        report, count = restore.restore(saved, ws, here, [], Mock(), snapshot, actions,
                                        pinned={"notes", "mail"}, pinned_groups=pins)
        self.assertEqual((report, count), ([], 4))
        self.assertEqual([c.args[1] for c in actions.call_args_list if c.args[1] != "focus-window"], [])
        self.assertGreaterEqual(len(looks), 2)

    def test_single_window_recovery_counts_only_this_displays_pins(self):
        # Notes is pinned to another display, so here only Mail leads: Files and Notes must follow it in their
        # saved order. Counting the other display's pin would put Notes first in both rows and pass them.
        saved = {"version": 1, "windows": [{"app_id": "files", "column": 1}, {"app_id": "notes", "column": 2},
                                           {"app_id": "mail", "column": 3}]}
        pins = [{"app_id": "notes", "display": "ext"}, {"app_id": "mail", "display": "main"}]
        for display, expected in (("main", ["Pinned spaces keep their positions; session order needs manual recovery"]),
                                  (None, [])):
            with self.subTest(display=display):
                ws = {"id": 1, "active_window_id": 1}
                windows = [window(2, "notes", 1), window(3, "mail", 2), window(1, "files", 3)]
                report, _ = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), Mock(),
                                            pinned={"notes", "mail"}, pinned_groups=pins, display=display)
                self.assertEqual(report, expected)

    def test_single_window_recovery_ignores_pins_on_another_workspace(self):
        ws = {"id": 1, "active_window_id": 1}
        saved = {"version": 1, "windows": [{"app_id": "one", "column": 1}]}
        other_pin = window(2, "pin", 1)
        other_pin["workspace_id"] = 2
        windows = [window(1, "one", 2), other_pin]
        actions = Mock()
        report, count = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions,
                                        pinned={"pin"})
        self.assertEqual((report, count), ([], 1))
        actions.assert_any_call("action", "move-column-to-index", 1)

    def test_duplicate_or_unknown_windows_are_not_launched(self):
        launch = Mock()
        saved = {"version": 1, "windows": [{"app_id": "one"}, {"app_id": "one"}, {"app_id": None}]}
        report, count = restore.restore(saved, {"id": 1}, [], [], launch, Mock(), Mock())
        self.assertEqual(count, 0)
        self.assertEqual(len(report), 3)
        launch.assert_not_called()

    def test_duplicate_windows_resolve_only_in_the_original_session(self):
        saved = {"version": 1, "session": "old", "windows": [
            {"app_id": "term", "window_id": 2, "focused": True},
            {"app_id": "term", "window_id": 1}]}
        windows = [window(1, "term", 1), window(2, "term", 2)]
        ws = {"id": 1, "active_window_id": 1}
        launch, action = Mock(), Mock()
        report, count = restore.restore(saved, ws, windows, [], launch, lambda: (ws, windows), action, session="old")
        self.assertEqual((report, count), ([], 2))
        action.assert_any_call("action", "focus-window", "--id", 2)
        action.assert_any_call("action", "focus-window", "--id", 1)
        action.assert_called_with("action", "focus-window", "--id", 2)
        report, count = restore.restore(saved, ws, windows, [], launch, lambda: (ws, windows), Mock(), session="new")
        self.assertEqual((len(report), count), (2, 0))
        launch.assert_not_called()

    def test_missing_duplicate_is_never_relaunched(self):
        saved = {"version": 1, "session": "old", "windows": [
            {"app_id": "term", "window_id": 1}, {"app_id": "term", "window_id": 2}]}
        launch = Mock()
        ws, windows = {"id": 1}, [window(1, "term", 1)]
        report, count = restore.restore(saved, ws, windows, [], launch, lambda: (ws, windows), Mock(), session="old")
        self.assertEqual((len(report), count), (1, 1))
        launch.assert_not_called()

    def test_reused_saved_id_does_not_match_two_entries_to_one_window(self):
        saved = {"version": 1, "session": "old", "windows": [
            {"app_id": "term", "window_id": 1}, {"app_id": "term", "window_id": 1}]}
        report, count = restore.restore(saved, {"id": 1}, [window(1, "term", 1)], [], Mock(), Mock(), Mock(), session="old")
        self.assertEqual((len(report), count), (2, 0))

    def test_restores_saved_group_for_adjacent_unique_windows(self):
        ws = {"id": 1, "active_window_id": 2}
        saved = {"version": 1, "windows": [{"app_id": "one", "column": 1},
                                           {"app_id": "two", "column": 1, "focused": True}]}
        windows = [window(1, "one", 1), window(2, "two", 2)]
        action = Mock(side_effect=NiriActions(ws, windows))
        report, count = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), action)
        self.assertEqual((report, count), ([], 2))
        action.assert_any_call("action", "consume-window-into-column")
        action.assert_called_with("action", "focus-window", "--id", 2)

    def test_restores_mixed_group_and_singleton_column_order_with_extra_window(self):
        ws = {"id": 1, "active_window_id": 4}
        saved = {"version": 1, "windows": [
            {"app_id": "one", "column": 1},
            {"app_id": "two", "column": 2},
            {"app_id": "three", "column": 2},
            {"app_id": "four", "column": 3, "focused": True},
        ]}
        windows = [window(1, "one", 3), window(2, "two", 2), window(3, "three", 4),
                   window(4, "four", 1), window(5, "extra", 5)]
        actions = Mock(side_effect=NiriActions(ws, windows))
        report, count = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions)
        columns = {}
        for item in windows:
            columns.setdefault(item["layout"]["pos_in_scrolling_layout"][0], []).append(item["app_id"])
        self.assertEqual((report, count), ([], 4))
        self.assertEqual([set(columns[i]) for i in sorted(columns)],
                         [{"one"}, {"two", "three"}, {"four"}, {"extra"}])
        actions.assert_called_with("action", "focus-window", "--id", 4)

    def test_failed_group_keeps_other_saved_columns_in_place(self):
        ws = {"id": 1, "active_window_id": 3}
        saved = {"version": 1, "windows": [
            {"app_id": "one", "column": 1},
            {"app_id": "two", "column": 2},
            {"app_id": "missing", "column": 2},
            {"app_id": "three", "column": 3, "focused": True},
        ]}
        windows = [window(1, "one", 3), window(2, "two", 1), window(3, "three", 2)]
        before = [w["layout"]["pos_in_scrolling_layout"][:] for w in windows]
        actions = Mock()
        report, _ = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions)
        self.assertTrue(any("Group members unavailable or ambiguous" in message for message in report))
        self.assertEqual([w["layout"]["pos_in_scrolling_layout"] for w in windows], before)
        self.assertNotIn("move-column-to-index", [call.args[1] for call in actions.call_args_list])

    def test_ambiguous_group_does_not_trigger_singleton_reordering(self):
        ws = {"id": 1, "active_window_id": 4}
        saved = {"version": 1, "windows": [
            {"app_id": "one", "column": 1}, {"app_id": "two", "column": 2},
            {"app_id": "three", "column": 2}, {"app_id": "four", "column": 3, "focused": True},
        ]}
        windows = [window(1, "one", 3), window(2, "two", 1), window(5, "two", 4),
                   window(3, "three", 2), window(4, "four", 5)]
        before = [w["layout"]["pos_in_scrolling_layout"][:] for w in windows]
        actions = Mock()
        report, _ = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions)
        self.assertTrue(any("ambiguous" in message for message in report))
        self.assertEqual([w["layout"]["pos_in_scrolling_layout"] for w in windows], before)
        self.assertNotIn("move-column-to-index", [call.args[1] for call in actions.call_args_list])

    def test_moves_group_member_next_to_partner_in_either_direction(self):
        saved = {"version": 1, "windows": [{"app_id": "one", "column": 1}, {"app_id": "two", "column": 1}]}
        ws = {"id": 1}
        for order in (("one", "other", "two"), ("two", "other", "one")):
            windows = [window(i, app, i) for i, app in enumerate(order, 1)]
            actions = Mock(side_effect=NiriActions(ws, windows))
            report, count = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions)
            self.assertEqual((report, count), ([], 2))
            actions.assert_any_call("action", "consume-window-into-column")

    def test_existing_group_is_preserved_and_pins_prevent_grouping(self):
        ws = {"id": 1, "active_window_id": 1}
        saved = {"version": 1, "windows": [{"app_id": "one", "column": 1}, {"app_id": "two", "column": 1}]}
        windows = [window(1, "one", 1), window(2, "two", 1)]
        windows[1]["layout"]["pos_in_scrolling_layout"] = [1, 2]
        action = Mock()
        report, count = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), action)
        self.assertEqual((report, count), ([], 2))
        self.assertNotIn("consume-window-into-column", str(action.call_args_list))
        for app in ("one", "two"):
            with self.subTest(pinned_member=app):
                pinned_action = Mock()
                report, _ = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), pinned_action, pinned={app})
                self.assertIn("pinned", report[0])
                self.assertNotIn("move-column-to-index", [call.args[1] for call in pinned_action.call_args_list])

    def test_group_pin_protects_both_members_during_session_restore(self):
        pins = [{"app_id": "one", "members": ["one", "two"]}]
        self.assertEqual(restore.pinned_apps(pins), {"one", "two"})
        self.assertEqual(restore.pinned_apps([{"app_id": "one"}]), {"one"})
        with self.assertRaisesRegex(ValueError, "Invalid pinned spaces"):
            restore.pinned_apps([{"app_id": "one", "members": "two"}])
        with self.assertRaisesRegex(ValueError, "Invalid pinned spaces"):
            restore.pinned_apps([{"app_id": "one", "members": ["one", []]}])

    def test_recovers_adjacent_members_of_the_matching_pinned_group_in_place(self):
        ws = {"id": 1, "active_window_id": 3}
        windows = [window(3, "other", 1), window(1, "one", 2), window(2, "two", 3)]
        pin = {"id": "pin-id", "app_id": "one", "members": ["one", "two"],
               "group_id": "group-id", "window_ids": [11, 12], "session": "old"}
        saved = {"version": 1, "groups": [{"id": "group-id", "label": "Studio", "members": ["one", "two"]}],
                 "windows": [{"app_id": "one", "column": 1}, {"app_id": "two", "column": 1}]}
        pins_before = json.loads(json.dumps([pin]))
        actions = Mock(side_effect=NiriActions(ws, windows))
        report, count = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions,
                                       pinned={"one", "two", "other"}, pinned_groups=[pin])
        self.assertEqual((report, count), ([], 2))  # "other" is not in the saved session: a column of its own
        self.assertEqual(windows[1]["layout"]["pos_in_scrolling_layout"][0],
                         windows[2]["layout"]["pos_in_scrolling_layout"][0])
        self.assertEqual(windows[0]["layout"]["pos_in_scrolling_layout"], [1, 1])
        action_names = [call.args[1] for call in actions.call_args_list]
        self.assertIn("consume-or-expel-window-left", action_names)
        self.assertNotIn("focus-window", action_names[:-1])
        self.assertNotIn("move-column-to-index", action_names)
        self.assertEqual([pin], pins_before)

    def test_pinned_group_recovery_restores_saved_order_from_reversed_columns(self):
        ws = {"id": 1}
        windows = [window(3, "other", 1), window(2, "two", 2), window(1, "one", 3)]
        pin = {"app_id": "one", "members": ["one", "two"], "group_id": "group-id"}
        saved = {"version": 1, "groups": [{"id": "group-id", "members": ["one", "two"]}],
                 "windows": [{"app_id": "one", "column": 1}, {"app_id": "two", "column": 1}]}
        actions = Mock(side_effect=NiriActions(ws, windows))
        report, count = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions,
                                       pinned={"one", "two", "other"}, pinned_groups=[pin])
        self.assertEqual((report, count), ([], 2))
        self.assertEqual(windows[0]["layout"]["pos_in_scrolling_layout"], [1, 1])
        self.assertEqual(windows[1]["layout"]["pos_in_scrolling_layout"], [2, 2])
        self.assertEqual(windows[2]["layout"]["pos_in_scrolling_layout"], [2, 1])
        actions.assert_any_call("action", "consume-or-expel-window-right", "--id", 2)

    def test_pinned_group_recovery_accepts_reversed_members_but_rejects_overlapping_pins(self):
        saved = {"version": 1, "groups": [{"id": "group-id", "members": ["two", "one"]}],
                 "windows": [{"app_id": "one", "column": 1}, {"app_id": "two", "column": 1}]}
        group_pin = {"app_id": "one", "members": ["two", "one"], "group_id": "group-id"}
        for pins, expected in (([group_pin], True), ([group_pin, {"app_id": "two"}], False)):
            with self.subTest(pins=pins):
                ws = {"id": 1}
                windows = [window(1, "one", 1), window(2, "two", 2)]
                actions = Mock(side_effect=NiriActions(ws, windows))
                restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions,
                                pinned={"one", "two"}, pinned_groups=pins)
                self.assertEqual(any(call.args[1] == "consume-or-expel-window-left"
                                     for call in actions.call_args_list), expected)

    def test_pinned_group_recovery_after_a_restart_shifts_the_windows_it_opened(self):
        # After a restart nothing is open: recovery opens the apps in saved order, so the pinned app's window
        # follows the Group's members, and joining the Group's column shifts it.
        saved = {"version": 1, "groups": [{"id": "group-id", "members": ["one", "two"]}],
                 "windows": [{"app_id": "one", "column": 1}, {"app_id": "two", "column": 1},
                             {"app_id": "other", "column": 2}]}
        pins = [{"app_id": "one", "members": ["one", "two"], "group_id": "group-id"}, {"app_id": "other"}]
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "applications").mkdir()
            for app in ("one", "two", "other"):
                (Path(directory) / "applications" / f"{app}.desktop").touch()
            ws, windows = {"id": 1}, []

            def launch(desktop):
                windows.append(window(len(windows) + 1, desktop.stem, len(windows) + 1))

            actions = Mock(side_effect=NiriActions(ws, windows))
            report, count = restore.restore(saved, ws, [], [directory], launch, lambda: (ws, windows), actions,
                                            lambda _: None, pinned={"one", "two", "other"}, pinned_groups=pins)
        self.assertEqual((report, count), ([], 3))
        actions.assert_any_call("action", "consume-or-expel-window-left", "--id", 2)
        self.assertEqual([w["layout"]["pos_in_scrolling_layout"] for w in windows], [[1, 1], [1, 2], [2, 1]])

    def test_pinned_group_recovery_shifts_a_window_that_maps_during_recovery(self):
        # As Rejoin Group does: a window of the user's after the Group's apps closes up behind them too. It is not
        # in the saved session, so it is a space of its own, and the saved windows are in their saved order.
        saved = {"version": 1, "groups": [{"id": "group-id", "members": ["one", "two"]}],
                 "windows": [{"app_id": "one", "column": 1}, {"app_id": "two", "column": 1},
                             {"app_id": "other", "column": 2}]}
        pins = [{"app_id": "one", "members": ["one", "two"], "group_id": "group-id"}, {"app_id": "other"}]
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "applications").mkdir()
            for app in ("one", "two", "other"):
                (Path(directory) / "applications" / f"{app}.desktop").touch()
            ws, windows = {"id": 1}, []

            def launch(desktop):
                windows.append(window(len(windows) + 1, desktop.stem, len(windows) + 1))
                if desktop.stem == "two":  # the user's own window maps as recovery works
                    windows.append(window(9, "notes", len(windows) + 1))

            actions = Mock(side_effect=NiriActions(ws, windows))
            report, _ = restore.restore(saved, ws, [], [directory], launch, lambda: (ws, windows), actions,
                                        lambda _: None, pinned={"one", "two", "other"}, pinned_groups=pins)
        self.assertEqual(report, [])
        actions.assert_any_call("action", "consume-or-expel-window-left", "--id", 2)
        self.assertEqual([w["layout"]["pos_in_scrolling_layout"] for w in windows],
                         [[1, 1], [1, 2], [2, 1], [3, 1]])  # one and two joined; notes and other closed up

    def test_pinned_group_recovery_skips_nonadjacent_or_unmatched_identity(self):
        saved = {"version": 1, "groups": [{"id": "group-id", "members": ["one", "two"]}],
                 "windows": [{"app_id": "one", "column": 1}, {"app_id": "two", "column": 1}]}
        pin = {"app_id": "one", "members": ["one", "two"], "group_id": "group-id"}
        for windows, candidate in (
                ([window(1, "one", 1), window(3, "other", 2), window(2, "two", 3)], pin),
                ([window(1, "one", 1), window(2, "two", 2)], {**pin, "group_id": "other-id"})):
            with self.subTest(windows=windows, pin=candidate):
                ws = {"id": 1}
                before = [w["layout"]["pos_in_scrolling_layout"][:] for w in windows]
                actions = Mock()
                report, _ = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions,
                                            pinned={"one", "two"}, pinned_groups=[candidate])
                self.assertTrue(report)
                self.assertEqual([w["layout"]["pos_in_scrolling_layout"] for w in windows], before)
                self.assertNotIn("consume-or-expel-window-left", [call.args[1] for call in actions.call_args_list])
                self.assertNotIn("move-column-to-index", [call.args[1] for call in actions.call_args_list])

    def test_pinned_group_recovery_rebuilds_the_group_before_a_column_that_was_open(self):
        saved = {"version": 1, "groups": [{"id": "group-id", "members": ["one", "two"]}],
                 "windows": [{"app_id": "one", "column": 1}, {"app_id": "two", "column": 1}]}
        pin = {"app_id": "one", "members": ["one", "two"], "group_id": "group-id"}
        ws, windows = {"id": 1}, [window(1, "one", 1), window(2, "two", 2), window(3, "other", 3)]
        actions = Mock(side_effect=NiriActions(ws, windows))
        report, count = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions,
                                        pinned={"one", "two"}, pinned_groups=[pin])
        self.assertEqual((report, count), ([], 2))
        self.assertEqual([w["layout"]["pos_in_scrolling_layout"] for w in windows], [[1, 1], [1, 2], [2, 1]])

    def test_pinned_group_recovery_accepts_the_order_pin_ordering_brings_back(self):
        # Saved: the pinned Group, then a pinned app. A refresh moved the app ahead while the Group was apart; once
        # rebuilt, the next refresh puts the Group first again, so the session is restored as saved.
        saved = {"version": 1, "groups": [{"id": "group-id", "members": ["one", "two"]}],
                 "windows": [{"app_id": "one", "column": 1}, {"app_id": "two", "column": 1},
                             {"app_id": "notes", "column": 2}]}
        pins = [{"app_id": "one", "members": ["one", "two"], "group_id": "group-id"}, {"app_id": "notes"}]
        ws, windows = {"id": 1}, [window(3, "notes", 1), window(1, "one", 2), window(2, "two", 3)]
        actions = Mock(side_effect=NiriActions(ws, windows))
        report, count = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions,
                                        pinned={"one", "two", "notes"}, pinned_groups=pins,
                                        registered=[{"id": "group-id", "label": "Pair", "members": ["one", "two"]}])
        self.assertEqual((report, count), ([], 3))
        self.assertEqual([w["layout"]["pos_in_scrolling_layout"] for w in windows], [[1, 1], [2, 1], [2, 2]])

    def test_pinned_group_recovery_checks_each_step_of_the_join(self):
        # A step that does not land (Niri moved the window elsewhere) stops the join before the next one.
        saved = {"version": 1, "groups": [{"id": "group-id", "members": ["one", "two", "three"]}],
                 "windows": [{"app_id": app, "column": 1} for app in ("one", "two", "three")]}
        pin = {"app_id": "one", "members": ["one", "two", "three"], "group_id": "group-id"}
        ws, windows = {"id": 1}, [window(1, "one", 1), window(2, "two", 2), window(3, "three", 3)]
        niri = NiriActions(ws, windows)
        actions = Mock(side_effect=lambda kind, action, *args: None if args[-1:] == (2,) else niri(kind, action, *args))
        report, _ = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions,
                                    pinned={"one", "two", "three"}, pinned_groups=[pin])
        self.assertIn("Group members moved unexpectedly; group not restored", report)
        self.assertNotIn(call("action", "consume-or-expel-window-left", "--id", 3), actions.call_args_list)

    def test_pinned_group_recovery_counts_only_this_displays_pins(self):
        # Notes is pinned to another display, so here it is an unpinned window: the next refresh puts only the Group
        # first, and Notes and Files must follow it in their saved order. Counting the other display's pin would
        # put Notes first in both rows and pass them.
        saved = {"version": 1, "groups": [{"id": "group-id", "members": ["one", "two"]}],
                 "windows": [{"app_id": "files", "column": 1}, {"app_id": "notes", "column": 2},
                             {"app_id": "one", "column": 3}, {"app_id": "two", "column": 3}]}
        pins = [{"app_id": "notes", "display": "ext"},
                {"app_id": "one", "members": ["one", "two"], "group_id": "group-id", "display": "main"}]
        for display, expected in (("main", ["Pinned spaces keep their positions; session order needs manual recovery"]),
                                  (None, [])):
            with self.subTest(display=display):
                ws = {"id": 1}
                windows = [window(3, "notes", 1), window(1, "one", 2), window(2, "two", 3), window(4, "files", 4)]
                actions = Mock(side_effect=NiriActions(ws, windows))
                report, _ = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions,
                                            pinned={"one", "two", "notes"}, pinned_groups=pins, display=display,
                                            registered=[{"id": "group-id", "label": "Pair", "members": ["one", "two"]}])
                self.assertEqual(report, expected)

    def test_pinned_group_recovery_accepts_pins_reordered_since_the_save_whenever_the_refresh_runs(self):
        # The pins were swapped after the last save: the Group is now first, Mail second. Recovery joins the Group
        # with Mail still first; a refresh may put the pins in their new order before recovery looks or after.
        # Either way the pins end in pin order and Files follows them, so the session is restored.
        saved = {"version": 1, "groups": [{"id": "group-id", "members": ["one", "two"]}],
                 "windows": [{"app_id": "mail", "column": 1}, {"app_id": "one", "column": 2},
                             {"app_id": "two", "column": 2}, {"app_id": "files", "column": 3}]}
        pins = [{"app_id": "one", "members": ["one", "two"], "group_id": "group-id"}, {"app_id": "mail"}]
        registered = [{"id": "group-id", "label": "Pair", "members": ["one", "two"]}]
        for refresh in (False, True):
            with self.subTest(refresh_after_the_join=refresh):
                ws = {"id": 1}
                windows = [window(4, "mail", 1), window(1, "one", 2), window(2, "two", 3), window(3, "files", 4)]
                niri = NiriActions(ws, windows)

                def act(kind, action, *args):
                    niri(kind, action, *args)
                    if refresh and action.startswith("consume"):
                        for live, moved in zip(windows, restore.spaces.pin_ordered(ws, windows, pins, "", registered)):
                            live["layout"] = moved["layout"]

                report, count = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows),
                                                Mock(side_effect=act), pinned={"one", "two", "mail"},
                                                pinned_groups=pins, registered=registered)
                self.assertEqual((report, count), ([], 4))

    def test_pinned_group_recovery_still_reports_unpinned_windows_out_of_saved_order(self):
        # Pins in a new order are accepted; unpinned windows out of their saved order are not.
        saved = {"version": 1, "groups": [{"id": "group-id", "members": ["one", "two"]}],
                 "windows": [{"app_id": "mail", "column": 1}, {"app_id": "one", "column": 2},
                             {"app_id": "two", "column": 2}, {"app_id": "files", "column": 3},
                             {"app_id": "scratch", "column": 4}]}
        pins = [{"app_id": "one", "members": ["one", "two"], "group_id": "group-id"}, {"app_id": "mail"}]
        ws = {"id": 1}
        windows = [window(4, "mail", 1), window(1, "one", 2), window(2, "two", 3), window(5, "scratch", 4),
                   window(3, "files", 5)]
        actions = Mock(side_effect=NiriActions(ws, windows))
        report, _ = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions,
                                    pinned={"one", "two", "mail"}, pinned_groups=pins,
                                    registered=[{"id": "group-id", "label": "Pair", "members": ["one", "two"]}])
        self.assertEqual(report, ["Pinned spaces keep their positions; session order needs manual recovery"])

    def test_pinned_group_recovery_stops_when_the_workspace_changes_during_the_join(self):
        saved = {"version": 1, "groups": [{"id": "group-id", "members": ["one", "two", "three"]}],
                 "windows": [{"app_id": app, "column": 1} for app in ("one", "two", "three")]}
        pin = {"app_id": "one", "members": ["one", "two", "three"], "group_id": "group-id"}
        ws, windows = {"id": 1}, [window(1, "one", 1), window(2, "two", 2), window(3, "three", 3)]
        niri, moved = NiriActions(ws, windows), []

        def act(kind, action, *args):
            niri(kind, action, *args)
            moved.append(args)  # the user switched workspaces right after the first step

        actions = Mock(side_effect=act)
        with self.assertRaisesRegex(ValueError, "Workspace changed"):
            restore.restore(saved, ws, windows, [], Mock(), lambda: ({"id": 2} if moved else ws, windows), actions,
                            pinned={"one", "two", "three"}, pinned_groups=[pin])
        self.assertNotIn(call("action", "consume-or-expel-window-left", "--id", 3), actions.call_args_list)

    def test_pinned_group_recovery_stops_when_a_later_app_is_no_longer_a_single_window(self):
        saved = {"version": 1, "groups": [{"id": "group-id", "members": ["one", "two", "three"]}],
                 "windows": [{"app_id": app, "column": 1} for app in ("one", "two", "three")]}
        pin = {"app_id": "one", "members": ["one", "two", "three"], "group_id": "group-id"}
        ws, windows = {"id": 1}, [window(1, "one", 1), window(2, "two", 2), window(3, "three", 3)]
        niri = NiriActions(ws, windows)

        def act(kind, action, *args):
            niri(kind, action, *args)
            if args[-1:] == (2,):
                windows[2]["is_floating"] = True  # three became a floating window meanwhile

        actions = Mock(side_effect=act)
        report, _ = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions,
                                    pinned={"one", "two", "three"}, pinned_groups=[pin])
        self.assertIn("Group members moved unexpectedly; group not restored", report)
        self.assertNotIn(call("action", "consume-or-expel-window-left", "--id", 3), actions.call_args_list)

    def test_pinned_group_recovery_rejects_duplicate_saved_membership(self):
        saved = {"version": 1, "groups": [{"id": "first", "members": ["one", "two"]},
                                          {"id": "second", "members": ["two", "one"]}],
                 "windows": [{"app_id": "one", "column": 1}, {"app_id": "two", "column": 1}]}
        pin = {"app_id": "one", "members": ["one", "two"], "group_id": "first"}
        ws = {"id": 1}
        windows = [window(1, "one", 1), window(2, "two", 2)]
        actions = Mock()
        report, _ = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions,
                                    pinned={"one", "two"}, pinned_groups=[pin])
        self.assertTrue(report)
        self.assertNotIn("consume-or-expel-window-left", [call.args[1] for call in actions.call_args_list])

    def test_live_saved_or_unsaved_pin_blocks_the_new_ordering_pass(self):
        saved = {"version": 1, "windows": [
            {"app_id": "three", "column": 1}, {"app_id": "one", "column": 2},
            {"app_id": "two", "column": 2},
        ]}
        for pins, windows in (
            ({"one"}, [window(9, "pin", 1), window(3, "three", 3), window(1, "one", 2), window(2, "two", 2)]),
            ({"pin"}, [window(9, "pin", 1), window(3, "three", 3), window(1, "one", 2), window(2, "two", 2)]),
        ):
            with self.subTest(pins=pins):
                windows[-1]["layout"]["pos_in_scrolling_layout"] = [2, 2]
                ws = {"id": 1, "active_window_id": windows[0]["id"]}
                before = [w["layout"]["pos_in_scrolling_layout"][:] for w in windows]
                action = Mock()
                report, _ = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), action, pinned=pins)
                self.assertTrue(any("pinned" in message.lower() or "Pinned spaces keep" in message for message in report))
                self.assertEqual([w["layout"]["pos_in_scrolling_layout"] for w in windows], before)
                self.assertNotIn("move-column-to-index", [call.args[1] for call in action.call_args_list])

    def test_mixed_group_ordering_requires_valid_columns_for_every_entry(self):
        ws = {"id": 1, "active_window_id": 3}
        saved = {"version": 1, "windows": [
            {"app_id": "one", "column": 1}, {"app_id": "two", "column": 1},
            {"app_id": "three"},
        ]}
        windows = [window(1, "one", 2), window(2, "two", 2), window(3, "three", 1)]
        windows[1]["layout"]["pos_in_scrolling_layout"] = [2, 2]
        actions = Mock()
        report, _ = restore.restore(saved, ws, windows, [], Mock(), lambda: (ws, windows), actions)
        self.assertIn("Saved columns are incomplete", " ".join(report))
        self.assertNotIn("move-column-to-index", [call.args[1] for call in actions.call_args_list])

    def test_launch_only_exact_desktop_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            apps = Path(directory) / "applications"
            apps.mkdir()
            (apps / "one.desktop").write_text("[Desktop Entry]\n")
            self.assertEqual(restore.spaces.desktop_file("one", [directory]), apps / "one.desktop")
            self.assertIsNone(restore.spaces.desktop_file("../one", [directory]))
            launch = Mock()
            ws = {"id": 1, "active_window_id": None}
            after = [window(7, "one", 1)]
            report, count = restore.restore({"version": 1, "windows": [{"app_id": "one"}]}, ws, [], [directory],
                                            launch, lambda: (ws, after), Mock(), lambda _: None)
            self.assertEqual((report, count), ([], 1))
            launch.assert_called_once_with(apps / "one.desktop")

    def test_workspace_change_during_launch_stops_without_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            apps = Path(directory) / "applications"
            apps.mkdir()
            (apps / "one.desktop").touch()
            action, launch = Mock(), Mock()
            with self.assertRaisesRegex(ValueError, "Workspace changed"):
                restore.restore({"version": 1, "windows": [{"app_id": "one"}]}, {"id": 1}, [], [directory],
                                launch, lambda: ({"id": 2}, [window(1, "one", 1)]), action, lambda _: None)
            launch.assert_called_once()
            action.assert_not_called()

    def test_workspace_change_during_group_recovery_does_not_steal_focus(self):
        saved = {"version": 1, "windows": [{"app_id": "one", "column": 1}, {"app_id": "two", "column": 1}]}
        ws, windows = {"id": 1, "active_window_id": 1}, [window(1, "one", 1), window(2, "two", 2)]
        action = Mock()
        with self.assertRaisesRegex(ValueError, "Workspace changed"):
            restore.restore(saved, ws, windows, [], Mock(), lambda: ({"id": 2}, windows), action)
        action.assert_not_called()

    def test_workspace_change_during_ordering_stops_before_more_moves_or_focus(self):
        ws = {"id": 1, "active_window_id": 4}
        windows = [window(1, "one", 2), window(2, "two", 1), window(3, "three", 1), window(4, "four", 3)]
        windows[2]["layout"]["pos_in_scrolling_layout"] = [1, 2]
        saved = {"version": 1, "windows": [{"app_id": "one", "column": 1},
                                           {"app_id": "two", "column": 2},
                                           {"app_id": "three", "column": 2},
                                           {"app_id": "four", "column": 3, "focused": True}]}
        snapshots = 0
        def get_snapshot():
            nonlocal snapshots
            snapshots += 1
            return (ws if snapshots < 4 else {"id": 2}, windows)
        action = Mock()
        with self.assertRaisesRegex(ValueError, "Workspace changed"):
            restore.restore(saved, ws, windows, [], Mock(), get_snapshot, action)
        self.assertEqual(action.call_args_list, [
            unittest.mock.call("action", "focus-window", "--id", 1),
        ])


if __name__ == "__main__":
    unittest.main()
