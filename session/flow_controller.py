#!/usr/bin/env python3
"""Flow's resident controller (docs/engineering/ADR-0011-resident-controller.md).

One process per session reads Niri's event stream, and runs Flow's refresh in-process, with the code and locks
of ``flow-spaces refresh``, when the events ask for one. The shell, and the Platform API while the shell runs,
read Flow's state from it over a Unix socket instead of starting ``flow-spaces`` per event. Actions still run as
``flow-spaces <action>``: they wait on apps and on Niri, which a resident loop must never do. For the same
reason, Layers to reshape after a display turned are left to a ``flow-spaces refresh`` process of its own.

Protocol, one JSON object per line on ``socket_path()``:

- ``{"watch": true}``: from now on the client gets ``{"state": ...}`` whenever Flow's state changes (and the
  current one at once), and ``{"event": ...}`` for the Niri events in ``FORWARDED``.
- ``{"refresh": N}``: refresh as soon as the locks allow, e.g. when Desk opens or an action finished. The client
  gets ``{"state": ..., "refreshed": N}`` once it has run, even if nothing changed.
- ``{"get": "observe"}``: answered with ``{"reply": <observe state>}``, read fresh, never written.
- ``{"get": "stats"}``: answered with ``{"reply": {...counters}}``, for tests/niri_performance_smoke.py.

A state that cannot be read is ``{"state": {"error": "..."}}``, as ``flow-spaces`` prints it.
"""
import collections
import fcntl
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import socket as unix_socket
import subprocess
import sys
import time
import traceback

from niri_state import NiriState
import spaces

QUIET = 0.15  # a refresh this long after the last structural event,
MAX_WAIT = 1.0  # but never later than this after the first one it answers,
MIN_INTERVAL = 0.5  # and never sooner than this after the previous refresh began.
TITLE_INTERVAL = 1.0  # A title-only change refreshes at once, then at most this often.
SAFETY = 30.0  # A refresh at least this often, in case an event was missed;
STREAM_DOWN = 3.0  # this often while Niri's event stream is down;
RECONNECT = 5.0  # which is reconnected this long after it ends.
# Automatic session saving (restore.py), while a client asks for it (the shell, while the setting is on and
# recovery is not paused): this long after the first refresh that events or a client asked for since the last save,
AUTO_SAVE_WAIT = 2.0
AUTO_SAVE_EVERY = 30.0  # and never sooner than this after the previous save began (a changed one syncs a file);
AUTO_SAVE_RETRY = 1.0  # while recovery or an action holds its locks, tried again this often.
BUSY_RETRY = 0.1  # An action holding Flow's locks is waited for by trying again this often.
INBOX_LIMIT = 1 << 20  # no request is this long
OUTBOX_LIMIT = 16 << 20  # a client this far behind has stopped reading
# The events the shell reacts to itself: the app menu follows focus, and Niri's own overview hides the shell.
FORWARDED = ("WindowsChanged", "WindowFocusChanged", "WorkspaceActiveWindowChanged", "WindowClosed",
             "OverviewOpenedOrClosed")


def state_home():
    """``$XDG_STATE_HOME``, as Flow's helpers and the shell read it (an empty one is unset)."""
    return os.environ.get("XDG_STATE_HOME") or os.environ.get("HOME", "") + "/.local/state"


def socket_path(runtime=None):
    """The controller's socket for this runtime folder and this Flow state: a development shell with state of its
    own gets a controller of its own beside the installed one. shell.qml builds the same name."""
    runtime = runtime or os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        raise FileNotFoundError("XDG_RUNTIME_DIR is not set")
    return Path(runtime) / f"flow-controller-{hashlib.md5(state_home().encode()).hexdigest()[:12]}.sock"


class Coalescer:
    """When the next refresh is due, from the events since the last one. Times are ``time.monotonic()``."""

    def __init__(self):
        self.first = self.last = self.title = None
        self.urgent = False
        self.previous = float("-inf")

    def event(self, now, title_only=False):
        if title_only:
            self.title = self.title if self.title is not None else now
        else:
            self.first = self.first if self.first is not None else now
            self.last = now

    def now(self):
        """Ask for a refresh at once: someone is waiting for it (Desk opening, an action that finished)."""
        self.urgent = True

    def due(self):
        """When to refresh, or None if nothing waits."""
        if self.urgent:
            return float("-inf")
        times = []
        if self.first is not None:
            times.append(max(min(self.last + QUIET, self.first + MAX_WAIT), self.previous + MIN_INTERVAL))
        if self.title is not None:
            times.append(max(self.title, self.previous + TITLE_INTERVAL))
        return min(times) if times else None

    def ran(self, when):
        """A refresh began: it answers every event before it."""
        self.first = self.last = self.title = None
        self.urgent = False
        self.previous = when


def title_only(before, after):
    """Whether a window's new record differs from the known one only in its title."""
    if not isinstance(before, dict) or not isinstance(after, dict) or before.get("title") == after.get("title"):
        return False
    return {k: v for k, v in before.items() if k != "title"} == {k: v for k, v in after.items() if k != "title"}


class Client:
    def __init__(self, connection):
        self.connection = connection
        self.inbox = b""
        self.outbox = bytearray()
        self.watching = False
        self.asked = None  # the id of a refresh it asked for and waits on
        self.auto_save = False  # it asks for automatic session saving


class Controller:
    def __init__(self, state_dir, niri_path, server, listened=None):
        self.state_dir = state_dir
        self.niri_path = niri_path
        self.server = server
        self.selector = selectors.DefaultSelector()
        self.selector.register(server, selectors.EVENT_READ, "accept")
        self.niri = NiriState()  # what Niri's events said, to tell a title-only change; refreshes ask Niri afresh
        self.stream = None
        self.stream_buffer = b""
        self.stream_retry = time.monotonic()
        self.coalescer = Coalescer()
        self.clients = []
        self.observers = []  # clients waiting for a fresh observe
        self.reshaper = None  # the flow-spaces refresh reshaping Layers, while it runs
        self.state_line = None  # the last state pushed, as sent
        self.last_refresh = float("-inf")  # so the safety refresh, the first state, is due at once
        self.recent = collections.deque(maxlen=1000)  # when the last refreshes ran (wall clock), for the smoke
        self.unsaved_since = None  # when the first refresh since the last automatic save ran
        self.last_save = float("-inf")
        self.save_retry = float("-inf")
        self.refused = False  # the last save refused what was there; the next refusal pauses recovery
        self.saves = collections.deque(maxlen=1000)  # [wall clock, written] of the last automatic saves
        self.stats = {"refreshes": 0, "observes": 0, "events": 0, "title_only": 0, "pushes": 0, "busy": 0,
                      "reshapes": 0, "failures": 0, "refresh_ms": 0.0, "first_watcher_ms": None,
                      "auto_saves": 0, "auto_save_writes": 0, "auto_save_ms": 0.0}
        # When it began listening: how soon the first watcher came, for the smoke.
        self.started = time.monotonic() if listened is None else listened

    # Niri's event stream -------------------------------------------------------------------------------------

    def connect_stream(self, now):
        try:
            stream = unix_socket.socket(unix_socket.AF_UNIX, unix_socket.SOCK_STREAM)
            stream.connect(self.niri_path)
            stream.sendall(b'"EventStream"\n')
            stream.setblocking(False)
        except OSError:
            self.stream_retry = now + RECONNECT
            return
        self.stream, self.stream_buffer = stream, b""
        self.niri.reset()
        self.selector.register(stream, selectors.EVENT_READ, "niri")

    def stream_ended(self, now):
        self.selector.unregister(self.stream)
        self.stream.close()
        self.stream = None
        self.niri.reset()
        self.stream_retry = now + RECONNECT
        self.coalescer.now()

    def read_stream(self, now):
        try:
            data = self.stream.recv(65536)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            self.stream_ended(now)
            return
        self.stream_buffer += data
        *lines, self.stream_buffer = self.stream_buffer.split(b"\n")
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict) or "Ok" in event or "Err" in event:
                continue  # the stream's own reply
            try:
                self.niri_event(event, now)
            except Exception:  # noqa: BLE001 — an event Niri changed the shape of must not stop the loop
                self.fail("A Niri event")
                self.coalescer.event(now)

    def niri_event(self, event, now):
        self.stats["events"] += 1
        name = next(iter(event), None)
        if name == "WindowOpenedOrChanged":
            window = (event[name] or {}).get("window") or {}
            only_title = title_only(self.niri.windows.get(window.get("id")), window)
            self.niri.apply(event)
            self.stats["title_only"] += only_title
            self.coalescer.event(now, title_only=only_title)
        elif name == "WindowFocusTimestampChanged":
            self.niri.apply(event)
        elif name in ("WorkspacesChanged", "WorkspaceActivated", "WorkspaceActiveWindowChanged", "WindowsChanged",
                      "WindowClosed", "WindowFocusChanged", "WindowLayoutsChanged"):
            self.niri.apply(event)
            self.coalescer.event(now)
        if name in FORWARDED:
            self.broadcast({"event": event})

    # Flow's state --------------------------------------------------------------------------------------------

    def fail(self, what):
        """Log an unexpected failure; the loop goes on."""
        self.stats["failures"] += 1
        print(f"flow-controller: {what} failed:\n{traceback.format_exc()}", file=sys.stderr, flush=True)

    def run(self, op):
        """Flow's state after ``op`` ("refresh" or "observe"), or None while an action holds Flow's locks.

        Every holder of spaces.lock takes session.lock first, so failing to take session.lock is all the waiting
        there is: what follows never waits on another process.
        """
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        spaces.app_name.cache_clear()  # desktop entries can change while the controller runs
        with (self.state_dir / "session.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self.stats["busy"] += 1
                return None
            try:
                return spaces.run_locked(self.state_dir, op, None, emit=False, reshape=False)
            except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as exc:
                return {"error": str(exc)}  # as flow-spaces says it
            except Exception as exc:  # noqa: BLE001 — a bug in a refresh must not stop the loop
                self.fail(f"A {op}")
                return {"error": f"Flow state could not be read ({type(exc).__name__})"}

    def refresh(self, now):
        started = time.perf_counter()
        result = self.run("refresh")
        if result is None:
            return False
        self.stats["refreshes"] += 1
        self.stats["refresh_ms"] += (time.perf_counter() - started) * 1000
        self.recent.append(time.time())
        self.coalescer.ran(now)
        self.last_refresh = now
        if result.get("reshape_due") and self.reshaper is None:
            # Reshaping runs Niri actions and waits for them: never in this loop. It holds the locks meanwhile,
            # and the events it causes ask for the next refresh.
            # Its errors go to the controller's stderr, which the shell logs.
            self.reshaper = subprocess.Popen(["flow-spaces", "refresh"], stdin=subprocess.DEVNULL,
                                             stdout=subprocess.DEVNULL)
            self.stats["reshapes"] += 1
        line = json.dumps({"state": result}).encode() + b"\n"
        changed = line != self.state_line
        self.state_line = line
        self.stats["pushes"] += changed
        for client in list(self.clients):
            if client.asked is not None:
                self.send(client, json.dumps({"state": result, "refreshed": client.asked}).encode() + b"\n")
                client.asked = None
            elif changed and client.watching:
                self.send(client, line)
        return True

    def answer_observers(self):
        if not self.observers:
            return True
        result = self.run("observe")
        if result is None:
            return False
        self.stats["observes"] += 1
        line = json.dumps({"reply": result}).encode() + b"\n"
        observers, self.observers = self.observers, []
        for client in observers:
            self.send(client, line)
        return True

    # Automatic session saving -------------------------------------------------------------------------------

    def save_due(self):
        """When the next automatic save is due, or None."""
        if self.unsaved_since is None or not any(client.auto_save for client in self.clients):
            return None
        return max(self.unsaved_since + AUTO_SAVE_WAIT, self.last_save + AUTO_SAVE_EVERY, self.save_retry)

    def auto_save(self, now):
        """Save the session as restore.py's automatic save does, in this process, and tell the clients that asked
        for it. While recovery or an action holds its locks, it is tried again a second later.

        The save reads Niri afresh. Should that catch a state on its way to another (focus overshooting and
        coming back), the events that follow it refresh, and the next save records where it came to rest. A
        state it refuses (a second window of one app) may not last either: the first refusal is kept from the
        clients and leaves the change unsaved, and only a refusal at the next save, an interval later, pauses.
        """
        started = time.perf_counter()
        try:
            import restore  # only once saving is asked for: the controller starts without it
            result = restore.automatic("auto-save", self.state_dir / "session.json", wait=False, pause=self.refused)
        except BlockingIOError:
            self.stats["busy"] += 1
            self.save_retry = now + AUTO_SAVE_RETRY
            return
        except (ValueError, OSError, subprocess.SubprocessError) as exc:  # Niri's, as flow-recovery said them
            result = {"error": str(exc)}
        except Exception:  # noqa: BLE001 — a bug in saving must not stop the loop
            self.fail("An automatic save")
            result = {"error": "Automatic saving failed; the saved session was preserved."}
        self.last_save = now
        self.stats["auto_saves"] += 1
        self.stats["auto_save_ms"] += (time.perf_counter() - started) * 1000
        if result.get("refused") and not self.refused:
            self.refused = True
            self.saves.append([time.time(), False])
            return
        self.refused = False
        self.unsaved_since = None
        written = bool(result.get("written"))
        self.stats["auto_save_writes"] += written
        self.saves.append([time.time(), written])
        line = json.dumps({"auto_saved": result}).encode() + b"\n"
        for client in list(self.clients):
            if client.auto_save:
                self.send(client, line)

    # Clients -------------------------------------------------------------------------------------------------

    def accept(self):
        try:
            connection, _ = self.server.accept()
        except OSError:
            return
        connection.setblocking(False)
        client = Client(connection)
        self.clients.append(client)
        self.selector.register(connection, selectors.EVENT_READ, client)

    def drop(self, client):
        if client not in self.clients:
            return
        self.selector.unregister(client.connection)
        client.connection.close()
        self.clients.remove(client)
        if client in self.observers:
            self.observers.remove(client)

    def read_client(self, client, now):
        try:
            data = client.connection.recv(65536)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            self.drop(client)
            return
        client.inbox += data
        if len(client.inbox) > INBOX_LIMIT:
            self.drop(client)
            return
        *lines, client.inbox = client.inbox.split(b"\n")
        for line in lines:
            try:
                request = json.loads(line)
            except ValueError:
                continue
            if not isinstance(request, dict):
                continue
            if request.get("watch"):
                client.watching = True
                if self.stats["first_watcher_ms"] is None:  # the shell, normally
                    self.stats["first_watcher_ms"] = round((time.monotonic() - self.started) * 1000)
                if self.state_line:
                    self.send(client, self.state_line)
            if request.get("refresh"):
                client.asked = request["refresh"]
                self.coalescer.now()
            if "auto_save" in request:
                # What is unsaved stays so while no client asks (from the first refresh on): asking saves it.
                if request["auto_save"] and not client.auto_save:
                    self.refused = False  # a refusal from before the pause or the toggle is no longer the last
                client.auto_save = bool(request["auto_save"])
            if request.get("get") == "observe":
                self.observers.append(client)
            elif request.get("get") == "stats":
                self.send(client, json.dumps({"reply": {**self.stats, "stream": self.stream is not None,
                                                        "ready": self.niri.ready, "recent": list(self.recent),
                                                        "saves": list(self.saves),
                                                        "pid": os.getpid()}}).encode() + b"\n")

    def send(self, client, line):
        if client not in self.clients:
            return
        client.outbox += line
        if len(client.outbox) > OUTBOX_LIMIT:
            self.drop(client)
            return
        self.flush(client)

    def flush(self, client):
        if client not in self.clients:
            return
        try:
            sent = client.connection.send(client.outbox)
        except BlockingIOError:
            sent = 0
        except OSError:
            self.drop(client)
            return
        del client.outbox[:sent]
        self.selector.modify(client.connection, selectors.EVENT_READ | (selectors.EVENT_WRITE if client.outbox else 0),
                             client)

    def broadcast(self, message):
        line = json.dumps(message).encode() + b"\n"
        for client in list(self.clients):
            if client.watching:
                self.send(client, line)

    # The loop ------------------------------------------------------------------------------------------------

    def next_deadline(self):
        deadlines = [self.last_refresh + (SAFETY if self.stream else STREAM_DOWN)]
        for due in (self.coalescer.due(), self.save_due()):
            if due is not None:
                deadlines.append(due)
        if self.stream is None:
            deadlines.append(self.stream_retry)
        return min(deadlines)

    def step(self, now):
        """Do what is due at ``now``; return how long the loop may sleep."""
        if self.stream is None and now >= self.stream_retry:
            self.connect_stream(now)
        if self.reshaper is not None and self.reshaper.poll() is not None:
            self.reshaper = None
        due = self.coalescer.due()
        safety = self.last_refresh + (SAFETY if self.stream else STREAM_DOWN)
        busy = False
        if (due is not None and now >= due) or now >= safety:
            busy = not self.refresh(now)
            # What a save records changes only with what Niri's events say or a client's action: a refresh that
            # answered them (even the safety one, when it came first) calls for a save, the safety one alone not,
            # unless the event stream is down and refreshes are all there is.
            if not busy and (due is not None or self.stream is None) and self.unsaved_since is None:
                self.unsaved_since = now
        if not self.answer_observers():
            busy = True
        saving = self.save_due()
        if not busy and saving is not None and now >= saving:  # while busy, saving would only meet the same lock
            self.auto_save(now)
        if busy:
            return BUSY_RETRY  # an action holds the locks: try again shortly, never spin
        return max(0.0, self.next_deadline() - now)

    def turn(self, timeout):
        """One turn of the loop: wait up to ``timeout`` for sockets, handle them, do what is due. Returns the
        next timeout."""
        for key, events in self.selector.select(timeout):
            now = time.monotonic()
            if key.data == "accept":
                self.accept()
            elif key.data == "niri":
                self.read_stream(now)
            else:
                if events & selectors.EVENT_WRITE:
                    self.flush(key.data)
                if events & selectors.EVENT_READ and key.data in self.clients:
                    self.read_client(key.data, now)
        return self.step(time.monotonic())

    def serve(self):
        timeout = 0.0
        while True:
            timeout = self.turn(timeout)


def ask(request, timeout=5.0):
    """The controller's reply to one ``{"get": ...}`` request.

    FileNotFoundError or ConnectionRefusedError: no controller serves this session. TimeoutError: one does but
    is busy (an action holds Flow's locks).
    """
    with unix_socket.socket(unix_socket.AF_UNIX, unix_socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(str(socket_path()))
        connection.sendall(json.dumps(request).encode() + b"\n")
        with connection.makefile("rb") as answers:
            for line in answers:
                message = json.loads(line)
                if isinstance(message, dict) and "reply" in message:
                    return message["reply"]
    raise ConnectionResetError("The controller closed the connection without answering")


def listen(runtime, wait=True):
    """The controller's socket and its lock. One controller serves a runtime folder and Flow state: with
    ``wait`` a second one waits until the first has gone, then takes over; without, it gets (None, None)."""
    path = socket_path(runtime)
    lock = path.with_suffix(".lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        if not wait:
            lock.close()
            return None, None
        print(f"flow-controller: another controller serves {path}; waiting for it to stop", file=sys.stderr,
              flush=True)
        fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        path.unlink()  # left by a controller that was killed: the lock says none runs
    except FileNotFoundError:
        pass
    server = unix_socket.socket(unix_socket.AF_UNIX, unix_socket.SOCK_STREAM)
    old = os.umask(0o077)
    try:
        server.bind(str(path))
    finally:
        os.umask(old)
    server.listen(16)
    server.setblocking(False)
    return server, lock


def main():
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    niri_path = os.environ.get("NIRI_SOCKET")
    if not runtime or not niri_path:
        print("flow-controller: XDG_RUNTIME_DIR and NIRI_SOCKET must be set", file=sys.stderr)
        return 2
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    server, lock = listen(Path(runtime))  # waits while another controller serves this session
    listened = time.monotonic()
    print("listening", flush=True)  # the shell connects on this line rather than waiting for its next try
    try:
        Controller(Path(state_home()) / "flow", niri_path, server, listened).serve()
    except KeyboardInterrupt:
        pass
    finally:
        # Before the lock is released: a waiting controller binds its socket only after this.
        socket_path(runtime).unlink(missing_ok=True)
        lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
