"""The resident controller: when it refreshes, what it pushes, and that it never waits on an action."""
import fcntl
import io
import json
import os
from pathlib import Path
import signal
import socket
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "session"))
import flow_controller as fc  # noqa: E402
import restore  # noqa: E402  the module the controller saves with, so patches reach it
import spaces  # noqa: E402  the module the controller runs, so patches reach it


class CoalescerTest(unittest.TestCase):
    def test_a_refresh_waits_for_a_quiet_moment(self):
        c = fc.Coalescer()
        self.assertIsNone(c.due())
        c.event(10.0)
        c.event(10.1)
        self.assertAlmostEqual(c.due(), 10.1 + fc.QUIET)

    def test_a_steady_stream_still_refreshes_after_the_maximum_wait(self):
        c = fc.Coalescer()
        for step in range(40):
            c.event(10.0 + step * 0.05)
        self.assertAlmostEqual(c.due(), 10.0 + fc.MAX_WAIT)

    def test_refreshes_keep_the_minimum_interval(self):
        c = fc.Coalescer()
        c.ran(10.0)
        c.event(10.05)
        self.assertAlmostEqual(c.due(), 10.0 + fc.MIN_INTERVAL)

    def test_a_title_shows_at_once_then_at_most_once_a_second(self):
        c = fc.Coalescer()
        c.event(10.0, title_only=True)
        self.assertAlmostEqual(c.due(), 10.0)
        c.ran(10.0)
        c.event(10.05, title_only=True)
        self.assertAlmostEqual(c.due(), 10.0 + fc.TITLE_INTERVAL)
        c.event(10.6, title_only=True)
        self.assertAlmostEqual(c.due(), 10.0 + fc.TITLE_INTERVAL)

    def test_a_requested_refresh_runs_at_once(self):
        c = fc.Coalescer()
        c.ran(10.0)
        c.now()
        self.assertLess(c.due(), 10.0)
        c.ran(10.01)
        self.assertIsNone(c.due())


class TitleOnlyTest(unittest.TestCase):
    def test_only_a_title_change_is_title_only(self):
        window = {"id": 1, "title": "a", "app_id": "foot", "is_focused": True, "layout": {"tile_size": [1, 2]}}
        self.assertTrue(fc.title_only(window, {**window, "title": "b"}))
        self.assertFalse(fc.title_only(window, {**window, "title": "b", "is_focused": False}))
        self.assertFalse(fc.title_only(window, dict(window)))
        self.assertFalse(fc.title_only(None, window))


WORKSPACE = {"id": 1, "idx": 1, "output": "Virtual-1", "is_active": True, "is_focused": True, "active_window_id": 7}
OUTPUTS = {"Virtual-1": {"name": "Virtual-1", "make": "Flow", "model": "Test", "serial": None,
                         "logical": {"width": 1280, "height": 800, "transform": "Normal"}}}


def window(ident, title, column):
    return {"id": ident, "title": title, "app_id": "foot", "pid": 100 + ident, "workspace_id": 1,
            "is_focused": ident == 7, "is_floating": False, "is_urgent": False,
            "layout": {"pos_in_scrolling_layout": [column, 1], "tile_size": [600.0, 800.0]}}


def end(stream):
    """Close a client's stream. Closing flushes what is still buffered, and that raises once the client has hung
    up: the client is gone either way, and the socket is closed even when the flush fails."""
    try:
        stream.close()
    except OSError:
        pass


class FakeNiri:
    """Niri's socket: queries answered from ``self.windows``; an event stream the test writes to."""

    def __init__(self, path):
        self.windows = [window(7, "one", 1), window(8, "two", 2)]
        self.streams = []
        self.lock = threading.Lock()  # the streams are shared by each connection's thread and the test's
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(path)
        self.server.listen()
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                connection, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self.handle, args=(connection,), daemon=True).start()

    def handle(self, connection):
        stream = connection.makefile("rwb")
        connection.close()  # the stream keeps the socket open until it is closed
        try:
            for line in stream:
                request = json.loads(line)
                if request == "EventStream":
                    stream.write(b'{"Ok":"Handled"}\n')
                    stream.flush()
                    with self.lock:
                        self.streams.append(stream)
                    self.send({"WorkspacesChanged": {"workspaces": [WORKSPACE]}})
                    self.send({"WindowsChanged": {"windows": self.windows}})
                    return
                answer = {"Workspaces": [WORKSPACE], "Windows": self.windows, "Outputs": OUTPUTS}[request]
                stream.write(json.dumps({"Ok": {request: answer}}).encode() + b"\n")
                stream.flush()
        except OSError:
            pass  # the client hung up before it had its answer
        end(stream)

    def send(self, event):
        with self.lock:  # so a stream is not ended between writing an event and flushing it
            for stream in list(self.streams):
                try:
                    stream.write(json.dumps(event).encode() + b"\n")
                    stream.flush()
                except OSError:
                    self.streams.remove(stream)
                    end(stream)

    def end_streams(self):
        with self.lock:
            for stream in self.streams:
                end(stream)
            self.streams = []

    def close(self):
        self.end_streams()
        self.server.close()


class FakeNiriTest(unittest.TestCase):
    def test_a_stream_whose_reader_has_gone_ends_quietly(self):
        """The controller hangs up while an event is written to its stream but not yet flushed: ending the
        stream flushes it, which fails, and the stream is closed all the same."""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = str(Path(folder.name) / "niri.sock")
        niri = FakeNiri(path)
        reader = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        reader.connect(path)
        reader.sendall(b'"EventStream"\n')
        with reader.makefile("rb") as lines:
            for _ in range(3):  # Handled, then the workspaces and the windows
                lines.readline()
        reader.close()
        [stream] = niri.streams
        stream.write(b"{}\n")  # written, not yet flushed
        niri.close()
        self.assertTrue(stream.closed)


class ControllerCase(unittest.TestCase):
    """A controller on a fake Niri, and one client of it."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.tmp = Path(folder.name)
        self.niri = FakeNiri(str(self.tmp / "niri.sock"))
        self.addCleanup(self.niri.close)
        for name, value in (("RECONNECT", 0.2), ("TITLE_INTERVAL", 0.3)):
            patcher = patch.object(fc, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for patcher in (patch.dict(os.environ, {"NIRI_SOCKET": str(self.tmp / "niri.sock"),
                                                "XDG_RUNTIME_DIR": str(self.tmp / "runtime"),
                                                "XDG_STATE_HOME": str(self.tmp / "state-home")}),
                        patch.object(spaces, "session_id", return_value="session")):
            patcher.start()
            self.addCleanup(patcher.stop)
        (self.tmp / "runtime").mkdir()
        server, lock = fc.listen(self.tmp / "runtime", wait=False)
        self.first_lock = lock
        self.addCleanup(lock.close)
        self.addCleanup(server.close)
        self.controller = fc.Controller(self.tmp / "state", str(self.tmp / "niri.sock"), server)
        self.addCleanup(lambda: self.controller.stream and self.controller.stream.close())
        self.client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.client.connect(str(fc.socket_path()))
        self.addCleanup(self.client.close)
        self.client.settimeout(0)
        self.received = b""

    def ask(self, request):
        self.client.sendall(json.dumps(request).encode() + b"\n")

    def messages(self, seconds):
        """Run the controller for ``seconds`` and return what this client received meanwhile."""
        end, timeout = time.monotonic() + seconds, 0.0
        while time.monotonic() < end:
            timeout = self.controller.turn(min(timeout, 0.02))
            try:
                self.received += self.client.recv(1 << 20)
            except BlockingIOError:
                pass
        *lines, self.received = self.received.split(b"\n")
        return [json.loads(line) for line in lines]

    def states(self, seconds):
        return [m["state"] for m in self.messages(seconds) if "state" in m]

    # What should happen is waited for, up to a deadline: a slow or busy machine must not fail a test, only one
    # where it never comes. Each wait runs the controller first, so a condition true from the start proves nothing
    # without it, and a test that must show nothing more happened looks for a short while after.
    DEADLINE = 5.0  # s of real time a wait may take

    def wait_until(self, test, timeout=None):
        """Run the controller until ``test(messages received since)`` holds; those messages."""
        timeout = self.DEADLINE if timeout is None else timeout
        seen, end = [], time.monotonic() + timeout
        while True:
            seen += self.messages(0.02)
            if test(seen):
                return seen
            if time.monotonic() >= end:
                self.fail(f"the controller did not get there within {timeout} s; last received: {seen[-3:]}")

    def wait_for_states(self, test=bool, timeout=None):
        """Run the controller until the states received since pass ``test``; those states."""
        states = lambda seen: [m["state"] for m in seen if "state" in m]
        return states(self.wait_until(lambda seen: test(states(seen)), timeout))

    def wait_for_labels(self, labels, timeout=None):
        """The states received until one shows ``labels``."""
        return self.wait_for_states(lambda states: bool(states) and self.labels(states[-1]) == labels, timeout)

    def watching(self):
        """Watch, and run the controller until it has settled: the first state sent, Niri's first two events read
        (FakeNiri's WorkspacesChanged and WindowsChanged) and the refresh they ask for run. The messages received."""
        self.ask({"watch": True})
        return self.wait_until(lambda seen: any("state" in m for m in seen) and self.controller.stats["events"] >= 2
                               and self.controller.coalescer.due() is None)

    def labels(self, state):
        return [w["label"] for w in state["windows"]]


class ControllerTest(ControllerCase):
    def test_a_watcher_gets_the_state_then_each_change(self):
        self.ask({"watch": True})
        [state] = self.wait_for_states() + self.states(0.1)  # one state, and no second one
        self.assertEqual((self.labels(state), state["action"]), (["one", "two"], "refresh"))
        self.niri.windows = [window(7, "one", 1), window(8, "two", 2), window(9, "three", 3)]
        self.niri.send({"WindowOpenedOrChanged": {"window": self.niri.windows[-1]}})
        self.wait_for_labels(["one", "two", "three"])
        self.assertEqual(self.controller.stats["pushes"], 2)

    def test_a_refresh_asked_for_is_answered_even_unchanged(self):
        self.watching()
        self.ask({"refresh": 7})
        seen = self.wait_until(lambda seen: any("refreshed" in m for m in seen)) + self.messages(0.1)
        [message] = [m for m in seen if "state" in m]
        self.assertEqual((message["refreshed"], self.labels(message["state"])), (7, ["one", "two"]))

    def test_forwarded_events_reach_watchers(self):
        self.watching()
        self.niri.send({"WindowFocusChanged": {"id": 8}})
        self.niri.send({"OverviewOpenedOrClosed": {"is_open": True}})
        self.niri.send({"KeyboardLayoutSwitched": {"idx": 1}})
        seen = self.wait_until(lambda seen: sum("event" in m for m in seen) >= 2)
        seen += self.messages(0.1)  # and nothing for the third, sent after them
        events = [m["event"] for m in seen if "event" in m]
        self.assertEqual([next(iter(e)) for e in events], ["WindowFocusChanged", "OverviewOpenedOrClosed"])

    def test_an_action_holding_the_lock_is_waited_for_never_blocked_on_nor_spun_on(self):
        self.watching()
        refreshes = self.controller.stats["refreshes"]
        with (self.tmp / "state" / "session.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.ask({"refresh": 1})
            self.ask({"get": "observe"})
            end, timeout = time.monotonic() + 0.5, 0.0
            while time.monotonic() < end:  # the loop as it runs: each turn waits as long as the last one said
                timeout = self.controller.turn(timeout)
            self.assertEqual(self.controller.stats["refreshes"], refreshes)
            self.assertTrue(1 <= self.controller.stats["busy"] <= 2 * (0.5 / fc.BUSY_RETRY + 1),
                            self.controller.stats["busy"])
        self.wait_until(lambda _: self.controller.stats["refreshes"] > refreshes)
        self.messages(0.1)
        self.assertEqual(self.controller.stats["refreshes"], refreshes + 1)

    def test_a_refresh_asked_for_skips_the_minimum_interval(self):
        self.watching()
        refreshes = self.controller.stats["refreshes"]
        self.niri.send({"WindowLayoutsChanged": {"changes": []}})
        self.wait_until(lambda _: self.controller.stats["refreshes"] > refreshes)  # a refresh for the event has just run
        self.ask({"refresh": 3})
        started = time.monotonic()
        while not [m for m in self.messages(0.02) if m.get("refreshed") == 3]:
            self.assertLess(time.monotonic() - started, fc.MIN_INTERVAL / 2)

    def test_desktop_names_are_read_afresh_for_each_refresh(self):
        with patch.object(spaces.app_name, "cache_clear") as clear:
            self.wait_until(lambda _: self.controller.stats["refreshes"] >= 1)
        self.assertGreaterEqual(clear.call_count, 1)

    def test_a_focus_timestamp_does_not_make_the_next_title_change_structural(self):
        self.watching()
        events = self.controller.stats["events"]
        stamp = {"secs": 9, "nanos": 1}
        self.niri.send({"WindowFocusTimestampChanged": {"id": 7, "focus_timestamp": stamp}})
        self.niri.windows[0] = {**self.niri.windows[0], "title": "renamed", "focus_timestamp": stamp}
        self.niri.send({"WindowOpenedOrChanged": {"window": self.niri.windows[0]}})
        self.wait_until(lambda _: self.controller.stats["events"] >= events + 2)
        self.assertEqual(self.controller.stats["title_only"], 1)

    def test_a_client_that_floods_is_dropped(self):
        flood = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        flood.connect(str(fc.socket_path()))
        self.addCleanup(flood.close)

        def send():
            try:
                flood.sendall(b"x" * (fc.INBOX_LIMIT + 65536))
            except OSError:
                pass  # dropped part-way: the point

        sender = threading.Thread(target=send)
        sender.start()
        self.wait_until(lambda _: not sender.is_alive())  # its sendall ends when the controller drops it
        self.messages(0.05)
        sender.join(timeout=5)
        self.assertEqual(len(self.controller.clients), 1)  # only this test's quiet client is left
        flood.settimeout(1)
        try:
            self.assertEqual(flood.recv(10), b"")
        except ConnectionResetError:
            pass

    def test_layers_to_reshape_go_to_a_process_of_their_own_once(self):
        state = {"pins": [], "windows": [], "action": "refresh", "reshape_due": True}
        process = unittest.mock.Mock()
        process.poll.return_value = None
        with patch.object(spaces, "run_locked", return_value=state) as run, \
             patch.object(fc.subprocess, "Popen", return_value=process) as popen:
            self.controller.refresh(time.monotonic())
            self.controller.refresh(time.monotonic())
        self.assertEqual(run.call_args.kwargs["reshape"], False)
        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0], ["flow-spaces", "refresh"])
        process.poll.return_value = 0  # it finished: the next display turn reshapes again
        with patch.object(spaces, "run_locked", return_value=state), \
             patch.object(fc.subprocess, "Popen", return_value=process) as popen:
            self.controller.step(time.monotonic())
            self.controller.refresh(time.monotonic())
        popen.assert_called_once()

    def test_a_failing_refresh_is_an_error_state_and_the_loop_goes_on(self):
        self.watching()
        with patch.object(spaces, "run_locked", side_effect=TypeError("a bug")), \
             patch("sys.stderr", new_callable=lambda: open(os.devnull, "w")):
            self.ask({"refresh": 5})
            seen = self.wait_until(lambda seen: any(m.get("refreshed") == 5 for m in seen))
            self.assertIn("TypeError", next(m for m in seen if m.get("refreshed") == 5)["state"]["error"])
            self.messages(fc.MIN_INTERVAL + 0.3)  # not tried again while nothing asks: a retry would come by now
            self.assertEqual(self.controller.stats["failures"], 1)
        self.niri.send({"WindowLayoutsChanged": {"changes": []}})
        self.wait_for_labels(["one", "two"])

    def test_observe_is_answered_fresh_and_writes_nothing(self):
        self.ask({"get": "observe"})
        seen = self.wait_until(lambda seen: any("reply" in m for m in seen)) + self.messages(0.1)
        [reply] = [m["reply"] for m in seen if "reply" in m]
        self.assertEqual((self.labels(reply), reply["action"]), (["one", "two"], "observe"))

    def test_ask_answers_one_request_for_the_platform_api(self):
        answer = {}
        with patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.tmp / "runtime")}):
            asking = threading.Thread(target=lambda: answer.update(fc.ask({"get": "observe"})))
            asking.start()
            self.wait_until(lambda _: not asking.is_alive())
            asking.join(timeout=5)
        self.assertEqual((self.labels(answer), answer["action"]), (["one", "two"], "observe"))
        with patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.tmp / "nowhere")}), self.assertRaises(OSError):
            fc.ask({"get": "observe"})

    def test_the_stream_is_reconnected_and_refreshed_after(self):
        self.watching()
        self.niri.end_streams()
        self.messages(0.1)
        self.assertIsNone(self.controller.stream)
        self.niri.windows = [window(7, "renamed", 1), window(8, "two", 2)]
        self.wait_for_labels(["renamed", "two"], timeout=fc.RECONNECT + 5)
        self.assertIsNotNone(self.controller.stream)

    def test_a_malformed_niri_event_is_logged_and_the_loop_goes_on(self):
        self.watching()
        with patch("sys.stderr", new_callable=lambda: open(os.devnull, "w")):
            self.niri.send({"WindowOpenedOrChanged": {"window": 5}})
            self.niri.windows = [window(7, "one", 1), window(8, "two", 2), window(9, "three", 3)]
            self.niri.send({"WindowOpenedOrChanged": {"window": self.niri.windows[-1]}})
            self.wait_for_labels(["one", "two", "three"])
        self.assertEqual(self.controller.stats["failures"], 1)

    def test_every_observer_is_answered_when_one_has_gone(self):
        class Gone:
            """A client connection whose reader has gone: every send fails."""
            def __init__(self, connection):
                self.connection = connection
            def fileno(self):
                return self.connection.fileno()
            def send(self, data):
                raise BrokenPipeError
            def close(self):
                self.connection.close()

        ours, theirs = socket.socketpair()
        self.addCleanup(theirs.close)
        gone = fc.Client(Gone(ours))
        self.controller.clients.append(gone)
        self.controller.selector.register(gone.connection, fc.selectors.EVENT_READ, gone)
        self.controller.observers.append(gone)
        self.ask({"get": "observe"})
        seen = self.wait_until(lambda seen: any("reply" in m for m in seen)) + self.messages(0.1)
        replies = [m for m in seen if "reply" in m]
        self.assertEqual(len(replies), 1)
        self.assertNotIn(gone, self.controller.clients)

    def test_a_client_that_stops_reading_is_dropped(self):
        self.ask({"watch": True})
        with patch.object(fc, "OUTBOX_LIMIT", 64):
            # Once it has refreshed it has accepted this client and sent it the state: more than the outbox holds.
            self.wait_until(lambda _: self.controller.stats["refreshes"] >= 1)
            self.messages(0.1)
        self.assertEqual(self.controller.clients, [])

    def test_it_counts_how_soon_its_first_watcher_came_not_other_clients(self):
        self.messages(0.1)  # this test's client, connected in setUp, only connected
        self.assertIsNone(self.controller.stats["first_watcher_ms"])
        self.ask({"get": "stats"})
        self.messages(0.1)
        self.assertIsNone(self.controller.stats["first_watcher_ms"])  # asking for stats is not watching
        self.ask({"watch": True})
        self.wait_until(lambda _: self.controller.stats["first_watcher_ms"] is not None)
        first = self.controller.stats["first_watcher_ms"]
        self.assertIsInstance(first, int)
        self.ask({"watch": True})
        self.messages(0.1)
        self.assertEqual(self.controller.stats["first_watcher_ms"], first)  # only the first counts

    def test_a_second_controller_waits_until_the_first_has_gone(self):
        taken = []
        with patch("sys.stderr", new_callable=lambda: open(os.devnull, "w")):
            waiting = threading.Thread(target=lambda: taken.append(fc.listen(self.tmp / "runtime")))
            waiting.start()
            time.sleep(0.3)
            self.assertEqual(taken, [])  # it waits while this controller holds the lock
            self.controller.server.close()
            self.first_lock.close()
            waiting.join(timeout=5)
        server, lock = taken[0]
        self.addCleanup(lock.close)
        self.addCleanup(server.close)
        self.assertTrue(fc.socket_path().exists())

    def test_one_controller_per_runtime_folder_and_flow_state(self):
        self.assertEqual(fc.listen(self.tmp / "runtime", wait=False), (None, None))
        with patch.dict(os.environ, {"XDG_STATE_HOME": str(self.tmp / "other-state")}):
            server, lock = fc.listen(self.tmp / "runtime", wait=False)  # a development shell's own state
            self.assertIsNotNone(server)
            server.close()
            lock.close()



class Clock:
    """The controller's clock in a ClockedCase: ``monotonic()`` moves only as the test runs the loop, so when
    something comes, measured on it, does not depend on how busy the machine is. Everything else is ``time``'s."""

    def __init__(self, now):
        self.now = now

    def monotonic(self):
        return self.now

    def __getattr__(self, name):
        return getattr(time, name)


class ClockedCase(ControllerCase):
    """A controller on a ``Clock`` of its own, moved STEP at a time as the test runs the loop: for tests of when
    things happen. What should happen is still waited for up to a deadline in real time (the fake Niri answers from
    a thread of its own); what should not is watched for a stretch of the controller's clock."""

    STEP = 0.01
    # Real time only guards against a hang here: what is waited for comes on the controller's clock, a STEP per
    # turn, and a turn takes longer in real time the busier the machine.
    DEADLINE = 60.0

    def setUp(self):
        super().setUp()
        self.clock = Clock(time.monotonic())  # the controller was made on the real clock: start from there
        patcher = patch.object(fc, "time", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def messages(self, seconds):
        """Run the controller for ``seconds`` of its clock, a STEP at a time: each turn reads what has arrived and
        does what is due at the clock's time. What this client received meanwhile."""
        end = self.clock.now + seconds - 1e-9
        while self.clock.now < end:
            self.controller.turn(0.001)
            self.clock.now += self.STEP
            try:
                self.received += self.client.recv(1 << 20)
            except BlockingIOError:
                pass
        *lines, self.received = self.received.split(b"\n")
        return [json.loads(line) for line in lines]


class TitleIntervalTest(ClockedCase):
    def test_a_title_change_shows_at_once_then_at_most_once_an_interval(self):
        self.watching()
        self.messages(fc.TITLE_INTERVAL + self.STEP)  # an interval since the last refresh
        refreshes, seen, started = self.controller.stats["refreshes"], [], self.clock.now
        for index in range(12):  # a title every 0.05 s for 0.6 s
            self.niri.windows[0] = {**self.niri.windows[0], "title": f"title {index}"}
            self.niri.send({"WindowOpenedOrChanged": {"window": self.niri.windows[0]}})
            seen += [(round(self.clock.now - started, 3), self.labels(s)[0]) for s in self.states(0.05)]
        seen += [(round(self.clock.now - started, 3), self.labels(s)[0]) for s in self.states(fc.TITLE_INTERVAL + 0.1)]
        self.assertEqual(self.controller.stats["title_only"], 12)
        self.assertEqual(seen[0], (0.05, "title 0"))  # the first title at once: in the first 0.05 s
        self.assertEqual(seen[-1][1], "title 11")  # the last one within an interval of the stream's end
        # At most one refresh an interval: 0.6 s / 0.3 s, plus the first and the last.
        self.assertLessEqual(self.controller.stats["refreshes"] - refreshes, 4)


class AutoSaveTest(ClockedCase):
    """Automatic session saving, with restore.automatic faked: when the controller saves and whom it tells, on the
    controller's own clock."""

    def setUp(self):
        super().setUp()
        for name, value in (("AUTO_SAVE_WAIT", 0.2), ("AUTO_SAVE_EVERY", 0.8), ("AUTO_SAVE_RETRY", 0.15),
                            ("SAFETY", 0.3)):
            patcher = patch.object(fc, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.saved = []  # (when, pause) of each save, by the test's clock
        self.answers = [{"message": "Saved 2 open windows.", "written": True}]
        self.told = []  # what this client was told

        def save(op, path, wait=True, pause=True):
            self.assertEqual((op, path, wait), ("auto-save", self.tmp / "state/session.json", False))
            self.saved.append((self.clock.now, pause))
            answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
            if isinstance(answer, BaseException):
                raise answer
            return answer
        patcher = patch.object(restore, "automatic", side_effect=save)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.refreshed = []  # when each refresh ran, on the controller's clock
        refresh = self.controller.refresh

        def recorded(now):
            ran = refresh(now)
            if ran:
                self.refreshed.append(now)
            return ran
        self.controller.refresh = recorded

    def focus(self, ident):
        self.niri.send({"WindowFocusChanged": {"id": ident}})

    def run_for(self, seconds):
        self.told += [m["auto_saved"] for m in self.messages(seconds) if "auto_saved" in m]

    def until(self, test, timeout=None):
        """Run the loop until ``test()`` holds; a deadline in real time, as it only guards against a hang."""
        timeout = self.DEADLINE if timeout is None else timeout
        end = time.monotonic() + timeout
        while True:
            self.run_for(self.STEP)
            if test():
                return
            if time.monotonic() >= end:
                self.fail(f"the controller did not get there within {timeout} s; saved {self.saved}, told {self.told}")

    def settled(self):
        """Watch, and run until Niri's first two events are read and the refresh they ask for has run."""
        self.ask({"watch": True})
        self.until(lambda: self.controller.stats["events"] >= 2 and self.controller.stats["refreshes"] >= 2
                   and self.controller.coalescer.due() is None)

    def started(self):
        """The shell's way: watch first, Niri's first events and their refresh, then ask for saving."""
        self.settled()
        self.ask({"auto_save": True})
        self.until(lambda: len(self.saved) == 1)

    def test_nothing_is_saved_until_a_client_asks(self):
        self.settled()
        self.focus(8)
        self.run_for(1.0)
        self.assertEqual(self.saved, [])
        self.ask({"auto_save": True})
        self.until(lambda: self.told)
        self.assertEqual(self.told, [{"message": "Saved 2 open windows.", "written": True}])
        self.assertEqual((self.controller.stats["auto_saves"], self.controller.stats["auto_save_writes"]), (1, 1))

    def test_with_nothing_changed_it_does_not_save_again_though_it_refreshes(self):
        self.started()
        refreshes = self.controller.stats["refreshes"]
        self.run_for(2.0)  # several safety refreshes, and more than two saves' intervals
        self.assertGreater(self.controller.stats["refreshes"], refreshes + 3)
        self.assertEqual(len(self.saved), 1)

    def test_a_change_is_saved_a_wait_after_it_and_an_interval_after_the_last_save(self):
        self.started()
        first = self.saved[0][0]
        self.focus(8)
        self.until(lambda: len(self.saved) == 2)
        self.assertGreaterEqual(self.saved[1][0] - first, fc.AUTO_SAVE_EVERY - 0.03)  # not at the wait's end
        self.run_for(1.0)  # idle again: once more after a change is all
        self.assertEqual(len(self.saved), 2)
        self.focus(7)
        changed = self.clock.now
        self.until(lambda: len(self.saved) == 3)
        # An interval after the last save: due the wait after the first refresh since the change, which comes up to
        # MIN_INTERVAL after the previous one.
        answered = next(when for when in self.refreshed if when > changed)
        self.assertAlmostEqual(self.saved[2][0] - answered, fc.AUTO_SAVE_WAIT, delta=self.STEP + 1e-9)
        self.assertLess(self.saved[2][0] - changed, fc.MIN_INTERVAL + fc.AUTO_SAVE_WAIT + 0.15)

    def test_with_the_event_stream_down_its_refreshes_call_for_saves(self):
        self.started()
        with patch.object(fc, "RECONNECT", 30.0), patch.object(fc, "STREAM_DOWN", 0.3):
            self.niri.end_streams()
            self.until(lambda: self.controller.stream is None)
            self.until(lambda: len(self.saved) == 2)  # the save the stream's end asks for
            self.niri.windows = list(reversed(self.niri.windows))  # a change no event reports
            self.until(lambda: len(self.saved) == 3)

    def test_a_held_lock_is_tried_again_a_retry_later_never_in_a_spin(self):
        self.answers = [BlockingIOError(), BlockingIOError(), {"message": "Saved", "written": True}]
        self.ask({"watch": True, "auto_save": True})
        self.until(lambda: self.told)
        self.assertEqual(self.told, [{"message": "Saved", "written": True}])
        self.assertEqual(len(self.saved), 3)
        self.assertGreaterEqual(self.saved[1][0] - self.saved[0][0], fc.AUTO_SAVE_RETRY - 0.03)
        self.assertEqual(self.controller.stats["auto_saves"], 1)

    def test_a_refusal_is_kept_back_once_and_only_the_next_pauses(self):
        refused = {"error": "foot: multiple windows need manual recovery after restart", "refused": True}
        self.answers = [refused, refused, {"message": "Saved", "written": True}]
        self.started()
        self.run_for(0.3)
        self.assertEqual(self.told, [])  # the first refusal is not the client's
        self.assertEqual(self.saved[0][1], False)
        self.until(lambda: self.told)  # an interval later, with nothing new: the change is still unsaved
        self.assertEqual(self.told, [refused])
        self.assertEqual(self.saved[1][1], True)
        self.assertGreaterEqual(self.saved[1][0] - self.saved[0][0], fc.AUTO_SAVE_EVERY - 0.03)
        self.focus(8)
        self.until(lambda: len(self.told) == 2)  # after a save that was not a refusal, the next one is tentative
        self.assertEqual(self.saved[2][1], False)

    def test_asking_again_starts_with_a_tentative_refusal(self):
        refused = {"error": "two windows", "refused": True}
        self.answers = [refused, refused]
        self.started()
        self.assertEqual(self.saved[0][1], False)
        self.ask({"auto_save": False})  # a Save session or Restore session in the shell
        self.run_for(0.1)
        self.ask({"auto_save": True})
        self.focus(8)
        self.until(lambda: len(self.saved) == 2)
        self.assertEqual(self.saved[1][1], False)
        self.assertEqual(self.told, [])

    def test_a_refusal_that_went_away_never_pauses(self):
        self.answers = [{"error": "two windows", "refused": True}, {"message": "Saved", "written": True}]
        self.started()
        self.until(lambda: self.told)
        self.assertEqual(self.told, [{"message": "Saved", "written": True}])
        self.assertEqual([pause for _, pause in self.saved], [False, True])

    def test_an_error_from_niri_is_reported_as_it_is_not_as_a_bug(self):
        self.answers = [ValueError("No focused workspace"), {"message": "Saved", "written": False}]
        self.ask({"watch": True, "auto_save": True})
        self.until(lambda: self.told)
        self.assertEqual(self.told, [{"error": "No focused workspace"}])
        self.assertEqual(self.controller.stats["failures"], 0)

    def test_a_bug_in_saving_is_reported_and_the_loop_goes_on(self):
        self.answers = [TypeError("bug"), {"message": "Saved", "written": False}]
        self.ask({"watch": True, "auto_save": True})
        with patch("sys.stderr", io.StringIO()):
            self.until(lambda: self.told)
        self.assertIn("preserved", self.told[0]["error"])
        self.assertEqual(self.controller.stats["failures"], 1)
        self.focus(8)
        self.until(lambda: len(self.told) == 2)
        self.assertEqual(self.told[1], {"message": "Saved", "written": False})

    def test_only_the_clients_that_asked_are_told_and_stopping_stops_it(self):
        other = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        other.connect(str(fc.socket_path()))
        self.addCleanup(other.close)
        other.settimeout(0)
        other.sendall(b'{"watch": true}\n')
        self.ask({"watch": True, "auto_save": True})
        self.until(lambda: self.told)
        heard = b""
        try:
            heard = other.recv(1 << 20)
        except BlockingIOError:
            pass
        self.assertNotIn(b"auto_saved", heard)
        self.ask({"auto_save": False})
        self.run_for(0.1)
        self.focus(8)
        self.run_for(1.2)
        self.assertEqual(len(self.saved), 1)


class MainTest(unittest.TestCase):
    def test_it_says_listening_once_its_socket_takes_connections(self):
        """The shell connects when it reads the line, instead of at its next try a quarter of a second on."""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        tmp = Path(folder.name)
        (tmp / "runtime").mkdir()
        said, before_listening = [], []
        listen = fc.listen

        def listening(*args, **kwargs):
            before_listening.append(output.getvalue())  # nothing said while it waits for the socket
            return listen(*args, **kwargs)

        class Serving:
            def __init__(self, *args):
                pass

            def serve(self):
                said.append(output.getvalue())
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as shell:
                    shell.connect(str(fc.socket_path()))  # as the shell would on reading it

        with patch.dict(os.environ, {"NIRI_SOCKET": str(tmp / "niri.sock"), "XDG_RUNTIME_DIR": str(tmp / "runtime"),
                                     "XDG_STATE_HOME": str(tmp / "state-home")}), \
                patch.object(fc, "Controller", Serving), patch.object(fc, "listen", listening), \
                patch.object(fc.signal, "signal"), patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(fc.main(), 0)
        self.assertEqual(before_listening, [""])
        self.assertEqual(said, ["listening\n"])


    def test_on_sigterm_it_closes_its_socket_and_removes_its_name_before_it_releases_its_lock(self):
        """A controller waiting on the lock must find nothing listening once it has it."""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        tmp = Path(folder.name)
        (tmp / "runtime").mkdir()
        listen, seen = fc.listen, []

        def listening(*args, **kwargs):
            server, lock = listen(*args, **kwargs)

            class Lock:
                def close(self):
                    seen.append((server.fileno(), fc.socket_path().exists()))
                    lock.close()
            return server, Lock()

        class Terminated:
            def __init__(self, *args):
                pass

            def serve(self):
                os.kill(os.getpid(), signal.SIGTERM)
                time.sleep(5)

        with patch.dict(os.environ, {"NIRI_SOCKET": str(tmp / "niri.sock"), "XDG_RUNTIME_DIR": str(tmp / "runtime"),
                                     "XDG_STATE_HOME": str(tmp / "state-home")}), \
                patch.object(fc, "Controller", Terminated), patch.object(fc, "listen", listening), \
                patch("sys.stdout", new_callable=io.StringIO):
            previous = signal.getsignal(signal.SIGTERM)
            try:
                with self.assertRaises(SystemExit):
                    fc.main()
            finally:
                signal.alarm(0)  # the handler's deadline, in this test's own process
                signal.signal(signal.SIGTERM, previous)
        self.assertEqual(seen, [(-1, False)])


class NiriStateFocusTimestampTest(unittest.TestCase):
    def test_a_focus_timestamp_updates_the_known_window(self):
        from niri_state import NiriState
        state = NiriState()
        state.apply({"WindowsChanged": {"windows": [{"id": 1, "title": "a", "focus_timestamp": None}]}})
        self.assertTrue(state.apply({"WindowFocusTimestampChanged": {"id": 1, "focus_timestamp": {"secs": 5, "nanos": 1}}}))
        self.assertEqual(state.windows[1]["focus_timestamp"], {"secs": 5, "nanos": 1})
        self.assertFalse(state.apply({"WindowFocusTimestampChanged": {"id": -1, "focus_timestamp": None}}))


if __name__ == "__main__":
    unittest.main()
