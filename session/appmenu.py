#!/usr/bin/env python3
"""The opt-in app menu's reader (F-020): the focused window's menu bar, read over AT-SPI.

``flow-appmenu serve`` runs while the setting is on. The shell starts it (and starts it again if it stops) and
talks to it in JSON lines, one request on stdin, one answer on stdout, matched by ``id``:

- ``{"id": 1, "op": "probe"}`` when focus has settled: answers for the focused window,
  ``{"id": 1, "window": 7, "name": "Kate", "has_menu": true, "final": true}``. The first probe of a window finds
  its accessible app (by PID; by ``app_id`` and title for an X11 window), chooses the window's frame by its title
  and ``active`` state, which only works while the window itself has the keyboard, and remembers the frame and
  ``has_menu`` for the window until it closes; later probes answer from memory, re-choosing the frame if it is no
  longer ``active``. ``has_menu`` stops at the first showing menu bar with an item. ``final`` is false when
  nothing was remembered (the app is not on the bus, or not yet, or no frame could be chosen), so the next probe
  looks again. ``"fresh": true`` forgets the window first. With no window focused: ``{"window": null}``.
- ``{"id": 2, "op": "read", "window": 7}``: the menu of the remembered frame, ``{"menus": [...], "workspace": 3,
  ...}``. Paths in it lead from the frame to an item. A read that fails forgets the window's frame.
- ``{"id": 3, "op": "activate", "window": 7, "path": [...], "label": "New"}``: runs the item, if window 7 is
  still its workspace's active window and the item is still there, enabled and named so. ``{"done": true}``, with
  ``"unknown": true`` when the app did not answer in time (a modal dialog answers only when it closes): the
  command was sent.

A request that fails answers ``{"id": n, "error": "..."}``.

On start, ``serve`` switches accessibility on (``org.a11y.Status.IsEnabled``) if it is off, and records that Flow
did. at-spi persists the switch for the user. ``flow-appmenu release``, run by the shell when the setting goes
off, switches it off again, only if Flow switched it on and no screen reader is running.
"""
import json
import os
from pathlib import Path
import socket
import sys

IGNORED_APPS = ("quickshell", "niri")
NODE_CAP, DEPTH_CAP = 2000, 12  # a read's walk under one menu bar
# Where a probe looks for a menu bar: this deep under the frame, this many nodes, and never inside content.
BAR_DEPTH, BAR_NODES = 8, 400
# Every call's wait for a reply, as libatspi's set_timeout(val, startup_time) takes them; its defaults are 800 and
# 15000 ms, and the second applies to an app that registered moments ago, as a newly opened window's has: DB
# Browser's modal About, which replies only when it closes, held the call for 15 s (coverage report, finding 3).
# A command whose reply does not come in time was sent (activate).
METHOD_TIMEOUT_MS = (1000, 2000)
A11Y = ("org.a11y.Bus", "/org/a11y/bus", "org.a11y.Status")
PRUNED = ("DOCUMENT_FRAME", "DOCUMENT_TEXT", "DOCUMENT_WEB", "DOCUMENT_SPREADSHEET", "DOCUMENT_PRESENTATION", "TEXT",
          "TABLE", "TREE", "TREE_TABLE", "LIST", "LIST_BOX", "TERMINAL", "CANVAS")


def niri(request):
    """One request on Niri's socket (no process): the ``Ok`` payload."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(os.environ["NIRI_SOCKET"])
        connection.sendall(json.dumps(request).encode() + b"\n")
        with connection.makefile("rb") as answers:
            reply = json.loads(answers.readline())
    if "Ok" not in reply:
        raise ValueError(f"Niri: {reply.get('Err')}")
    return reply["Ok"]


def app_name(app_id, title):
    if app_id:
        try:
            from gi.repository import GioUnix
            desktop = GioUnix.DesktopAppInfo.new(app_id if app_id.endswith(".desktop") else app_id + ".desktop")
            if desktop and desktop.get_display_name():
                return desktop.get_display_name()
        # PyGObject raises TypeError ("constructor returned NULL") when no desktop file has that name, or when
        # its Exec is not on PATH: LibreOffice Writer's app_id is libreoffice-writer, its file writer.desktop.
        except (ImportError, ValueError, TypeError):
            pass
    return app_id or title or "Application"


def is_x11(pid, proc="/proc"):
    """Whether Niri's PID for a window is xwayland-satellite's: every X11 window carries it. Its name may be
    a wrapper's (NixOS: ``.xwayland-satellite-wrapped``, cut to 15 characters)."""
    try:
        return Path(proc, str(pid), "comm").read_text().strip().lstrip(".").startswith("xwayland-sat")
    except (OSError, TypeError):
        return False


def choose_frame(frames, title, single_window=True):
    """The index of the window's frame among ``frames`` (``(name, active)`` pairs), or None if it is not clear.

    A frame named as the window is the one, if it is the only one; among several, the ``active`` one. With no
    frame of that name, the only frame of an app with one window, or the only ``active`` frame. ``active`` holds
    only while the window has the keyboard, not under Flow's own surfaces (coverage report, finding 10)."""
    named = [i for i, (name, _) in enumerate(frames) if name == title]
    if len(named) == 1:
        return named[0]
    if not named and len(frames) == 1 and single_window:
        return 0
    active = [i for i in (named or range(len(frames))) if frames[i][1]]
    return active[0] if len(active) == 1 else None


def loose(name):
    return "".join(c for c in (name or "").lower() if c.isalnum())


def same_app(app_name_, app_id):
    """Whether an accessible app's name fits a window's ``app_id`` (an X11 window's is its WM_CLASS)."""
    wanted = {loose(app_id), loose((app_id or "").rsplit(".", 1)[-1])} - {""}
    name = loose(app_name_)
    return bool(name) and any(name == w or name.startswith(w) or w.startswith(name) for w in wanted)


def timed_out(exc):
    text = str(exc)
    return "Did not receive a reply" in text or "Timeout was reached" in text or "timed out" in text


class Reader:
    """What ``serve`` answers. ``at`` is pyatspi (a fake in the tests), ``niri`` a function like ``niri()``."""

    def __init__(self, at, niri_request=niri, proc="/proc"):
        self.at = at
        self.niri = niri_request
        self.proc = proc
        self.frames = {}  # window id → (frame, name, has_menu): the frame chosen while the window had the keyboard
        # Content, where no menu bar lives and most of a window's nodes do (LibreOffice's document, Kate's view).
        self.prune = {getattr(at, "ROLE_" + name) for name in PRUNED if hasattr(at, "ROLE_" + name)}
        self.menus = {at.ROLE_MENU, at.ROLE_MENU_ITEM, at.ROLE_CHECK_MENU_ITEM, at.ROLE_RADIO_MENU_ITEM}

    def answer(self, line):
        """One request line: the answer line, or None for a line that is not a request."""
        try:
            request = json.loads(line)
        except ValueError:
            return None
        if not isinstance(request, dict) or "op" not in request:
            return None
        handler = {"probe": self.probe, "read": self.read, "activate": self.activate}.get(request["op"])
        try:
            if handler is None:
                raise ValueError(f"Unknown request {request['op']!r}")
            reply = handler(request)
        except Exception as exc:  # one app's failure never stops the reader
            reply = {"window": request.get("window"), "error": str(exc) or type(exc).__name__}
        return json.dumps({"id": request.get("id"), **reply})

    # --- Finding the window's frame -----------------------------------------------------------------------
    def apps(self):
        """(app, PID) for each app on the bus. The PID comes from the bus daemon, not the app, so an app that
        hangs costs nothing here; one whose PID cannot be had is skipped."""
        desktop = self.at.Registry.getDesktop(0)
        for i in range(desktop.childCount):
            try:
                app = desktop.getChildAtIndex(i)
                if app is not None:
                    yield app, app.get_process_id()
            except Exception:  # gone, or its connection is
                continue

    def frames_of(self, app):
        kinds = (self.at.ROLE_FRAME, self.at.ROLE_WINDOW, self.at.ROLE_DIALOG)
        found = []
        for i in range(app.childCount):
            child = app.getChildAtIndex(i)
            if child is not None and child.getRole() in kinds:
                found.append(child)
        return found

    def described(self, frames):
        return [(f.name or "", f.getState().contains(self.at.STATE_ACTIVE)) for f in frames]

    def find_frame(self, window, windows):
        """The focused window's frame, or None: by PID, or for an X11 window by ``app_id`` and title."""
        title, pid = window.get("title") or "", window.get("pid")
        if pid and not is_x11(pid, self.proc):
            app = next((a for a, app_pid in self.apps() if app_pid == pid), None)
            if app is None:
                return None
            frames = self.frames_of(app)
            index = choose_frame(self.described(frames), title, sum(w.get("pid") == pid for w in windows) == 1)
            return None if index is None else frames[index]
        # X11 (xwayland-satellite's PID): only apps that own no Niri window, so never a Wayland app's frame. Among
        # them, the frames named as the window, preferring apps named as its app_id. An app that fails to answer
        # is left out, so one hung app costs its timeout, not the probe.
        wayland = {w.get("pid") for w in windows}
        candidates = []
        for app, app_pid in self.apps():
            if app_pid in wayland:
                continue
            try:
                name = app.name or ""
                if name.lower() not in IGNORED_APPS:
                    candidates += [(name, frame) for frame in self.frames_of(app)]
            except Exception:
                continue
        fitting = [(a, f) for a, f in candidates if same_app(a, window.get("app_id"))]
        named = [(a, f) for a, f in (fitting or candidates) if (f.name or "") == title]
        pool = named or fitting
        single = sum(w.get("app_id") == window.get("app_id") for w in windows) == 1
        index = choose_frame(self.described([f for _, f in pool]), title, single) if pool else None
        return None if index is None else pool[index][1]

    # --- Menu bars ---------------------------------------------------------------------------------------
    def bars(self, frame, first=False):
        """Showing menu bars with an item under ``frame``, as (bar, path from the frame), breadth first, never
        inside documents, lists, tables or terminals. ``first``: stop at the first."""
        found, queue, seen = [], [(frame, [])], 0
        while queue and seen < BAR_NODES:
            node, path = queue.pop(0)
            for i in range(node.childCount):
                if seen >= BAR_NODES:  # one node with thousands of children stops here too
                    return found
                child = node.getChildAtIndex(i)
                seen += 1
                if child is None:
                    continue
                role = child.getRole()
                if role == self.at.ROLE_MENU_BAR:
                    if child.getState().contains(self.at.STATE_SHOWING) and self.has_item(child):
                        found.append((child, path + [i]))
                        if first:
                            return found
                elif role not in self.prune and len(path) + 1 < BAR_DEPTH:
                    queue.append((child, path + [i]))
        return found

    def has_item(self, node, depth=0):
        """Whether a menu bar holds an item, stopping at the first (Firefox's closed menus hold none). Like
        ``walk``, it looks through nodes that are not menus (Qt puts some between a menu and its items)."""
        if depth > DEPTH_CAP:
            return False
        for i in range(node.childCount):
            child = node.getChildAtIndex(i)
            if child is None:
                continue
            role = child.getRole()
            if role in self.menus and role != self.at.ROLE_MENU and not child.childCount:
                return True
            if self.has_item(child, depth + 1):
                return True
        return False

    def extents(self, node):
        try:
            box = node.queryComponent().getExtents(self.at.DESKTOP_COORDS)
            return box.x, box.y, box.width, box.height
        except Exception:  # no Component interface
            return 0, 0, 0, 0

    def seen_bar(self, bars):
        """The one bar the user sees. LibreOffice shows two in its frame (finding 4): only one has a size."""
        if len(bars) > 1:
            sized = [b for b in bars if self.extents(b[0])[2] > 0 and self.extents(b[0])[3] > 0]
            bars = sized or bars
        return bars[0] if bars else None

    def walk(self, node, path, depth, state):
        state["seen"] += 1
        if node is None or state["seen"] > NODE_CAP or depth > DEPTH_CAP:
            return []
        role = node.getRole()
        children = [entry for i in range(node.childCount)
                    for entry in self.walk(node.getChildAtIndex(i), path + [i], depth + 1, state)]
        if role not in self.menus:  # looked through: Qt puts nodes between a menu and its items
            return children
        enabled = node.getState().contains(self.at.STATE_ENABLED)
        if children:
            return [{"label": node.name or "", "children": children, "enabled": enabled}]
        if role != self.at.ROLE_MENU and not node.childCount:
            return [{"label": node.name or "", "path": path, "enabled": enabled}]
        return []

    # --- Requests ----------------------------------------------------------------------------------------
    def probe(self, request):
        """The focused window's answer. A window seen before answers from memory, after one check that its frame
        is still the ``active`` one (re-chosen if not), so a probe on a focus change costs a request on Niri's
        socket and one accessibility call. ``"fresh": true`` (Super+M on a window without a menu) chooses again."""
        windows = self.niri("Windows")["Windows"]
        live = {w["id"] for w in windows}
        for gone in [k for k in self.frames if k not in live]:
            del self.frames[gone]
        focused = next((w for w in windows if w.get("is_focused")), None)
        if focused is None:
            return {"window": None}
        ident = focused["id"]
        if request.get("fresh"):
            self.frames.pop(ident, None)
        if ident in self.frames:
            try:
                still = self.frames[ident][0].getState().contains(self.at.STATE_ACTIVE)
            except Exception:  # the frame is gone
                still = False
            if not still:
                frame = self.find_frame(focused, windows)
                if frame is not None and frame != self.frames[ident][0]:
                    del self.frames[ident]
        if ident not in self.frames:
            frame = self.find_frame(focused, windows)
            name = app_name(focused.get("app_id"), focused.get("title"))
            if frame is None:
                return {"window": ident, "name": name, "has_menu": False, "final": False}
            self.frames[ident] = (frame, name, bool(self.bars(frame, first=True)))
        _, name, has_menu = self.frames[ident]
        return {"window": ident, "name": name, "has_menu": has_menu, "final": True}

    def remembered(self, window):
        if window not in self.frames:
            raise ValueError("No app menu for this window")
        return self.frames[window]

    def read(self, request):
        window = request.get("window")
        frame, name, _ = self.remembered(window)
        try:
            bar = self.seen_bar(self.bars(frame))
            if bar is None:
                raise ValueError("The app shows no menu now")
            node, path = bar
            state = {"seen": 0}
            menus = [entry for i in range(node.childCount)
                     for entry in self.walk(node.getChildAtIndex(i), path + [i], 1, state)]
        except Exception:  # chosen again at the next probe, whatever went wrong with this frame
            self.frames.pop(window, None)
            raise
        workspace = next((w.get("workspace_id") for w in self.niri("Windows")["Windows"] if w["id"] == window), None)
        return {"window": window, "workspace": workspace, "name": name, "menus": menus}

    def activate(self, request):
        window, path, label = request.get("window"), request.get("path"), request.get("label")
        frame = self.remembered(window)[0]
        if not isinstance(path, list) or not all(isinstance(i, int) for i in path):
            raise ValueError("Bad command path")
        workspace = next((w for w in self.niri("Workspaces")["Workspaces"] if w.get("is_focused")), None)
        if not workspace or workspace.get("active_window_id") != window:
            raise ValueError("Application focus changed")
        try:
            node = frame
            for index in path:
                node = node.getChildAtIndex(index) if node is not None and 0 <= index < node.childCount else None
                if node is None or (node.getRole() in self.menus and not node.getState().contains(self.at.STATE_ENABLED)):
                    raise ValueError("Command is no longer available")
            if node.getRole() not in self.menus or (node.name or "") != label:
                raise ValueError("Command is no longer available")
        except ValueError:
            raise
        except Exception:  # the frame is gone: chosen again at the next probe
            self.frames.pop(window, None)
            raise
        try:
            if not node.queryAction().doAction(0):
                raise ValueError("Application declined the command")
        except Exception as exc:
            if timed_out(exc):  # sent; the app answers only when, say, its modal dialog closes
                return {"window": window, "done": True, "unknown": True}
            raise
        return {"window": window, "done": True}


# --- The accessibility switch ------------------------------------------------------------------------------
def marker():
    """Present while Flow, not the user, has switched accessibility on."""
    state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "flow"
    return state / "appmenu-switched-accessibility-on"


class Switch:
    """``org.a11y.Status`` on the session bus. at-spi persists ``IsEnabled`` for the user."""

    def __init__(self, bus=None):
        from gi.repository import Gio
        self.Gio = Gio
        self.bus = bus or Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def get(self, name):
        from gi.repository import GLib
        reply = self.bus.call_sync(A11Y[0], A11Y[1], "org.freedesktop.DBus.Properties", "Get",
                                   GLib.Variant("(ss)", (A11Y[2], name)), None, self.Gio.DBusCallFlags.NONE, 5000, None)
        return bool(reply.unpack()[0])

    def set(self, value):
        from gi.repository import GLib
        self.bus.call_sync(A11Y[0], A11Y[1], "org.freedesktop.DBus.Properties", "Set",
                           GLib.Variant("(ssv)", (A11Y[2], "IsEnabled", GLib.Variant("b", value))), None,
                           self.Gio.DBusCallFlags.NONE, 5000, None)


def switch_on(switch, mark):
    """Accessibility on; the mark records that Flow switched it, so ``release`` may switch it off again."""
    if not switch.get("IsEnabled"):
        switch.set(True)
        mark.parent.mkdir(parents=True, exist_ok=True)
        mark.touch()


def release(switch, mark):
    """Accessibility off again, only if Flow switched it on, and not under a running screen reader."""
    if not mark.exists():
        return False
    try:
        reading = switch.get("ScreenReaderEnabled")
    except Exception:  # at-spi2-core 2.62's launcher has no such property
        reading = False
    switched = switch.get("IsEnabled") and not reading
    if switched:
        switch.set(False)
    mark.unlink()
    return switched


def serve():
    switch_on(Switch(), marker())
    import gi
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi, GLib
    import pyatspi
    Atspi.set_timeout(*METHOD_TIMEOUT_MS)
    reader = Reader(pyatspi)
    # A GLib main loop, so the accessibility bus's signals are handled as they come rather than queued
    # forever; libatspi caches nothing outside its own event loop, so every read is fresh.
    loop, pending = GLib.MainLoop(), [b""]

    def readable(fd, condition):
        chunk = os.read(fd, 65536) if condition & GLib.IOCondition.IN else b""
        if not chunk:  # the shell has gone
            loop.quit()
            return False
        *lines, pending[0] = (pending[0] + chunk).split(b"\n")
        for line in lines:
            reply = reader.answer(line)
            if reply is not None:
                sys.stdout.write(reply + "\n")
                sys.stdout.flush()
        return True

    try:
        from gi.repository import GLibUnix
        add_watch = GLibUnix.fd_add_full
    except ImportError:  # GLib before 2.80
        add_watch = GLib.unix_fd_add_full
    add_watch(GLib.PRIORITY_DEFAULT, sys.stdin.fileno(), GLib.IOCondition.IN | GLib.IOCondition.HUP | GLib.IOCondition.ERR,
              readable)
    loop.run()


def main(args):
    if args == ["serve"]:
        serve()
    elif args == ["release"]:
        print(json.dumps({"released": release(Switch(), marker())}))
    else:
        print("usage: flow-appmenu serve | release", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
