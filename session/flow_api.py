#!/usr/bin/env python3
"""The Flow Platform API service (ADR-0009): spaces on the session D-Bus.

All policy is in flow_api_core; this file only binds it to GDBus, reads state through the read-only
``flow-spaces observe`` and focuses through the same ``flow-spaces`` actions the shell uses.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from gi.repository import Gio, GLib

import flow_api_core as core
import spaces

PATH = "/io/github/mattonik/Flow1"
SPACE = "aa{sv}"
XML = f"""
<node>
  <interface name="{core.NAME}.Spaces">
    <method name="GetCapabilities">
      <arg type="u" name="major" direction="out"/><arg type="u" name="minor" direction="out"/>
      <arg type="as" name="features" direction="out"/>
    </method>
    <method name="ListSpaces"><arg type="{SPACE}" name="spaces" direction="out"/></method>
    <method name="FocusSpace"><arg type="s" name="id" direction="in"/></method>
    <signal name="Changed"/>
  </interface>
  <interface name="{core.NAME}.Control">
    <method name="ListSpaces"><arg type="{SPACE}" name="spaces" direction="out"/></method>
    <method name="FocusSpace"><arg type="s" name="id" direction="in"/></method>
    <method name="PinSpace"><arg type="s" name="id" direction="in"/></method>
    <method name="UnpinSpace"><arg type="s" name="id" direction="in"/></method>
    <method name="MoveSpace">
      <arg type="s" name="id" direction="in"/><arg type="s" name="direction" direction="in"/>
    </method>
  </interface>
  <interface name="{core.NAME}.Activities">
    <method name="Start">
      <arg type="s" name="title" direction="in"/><arg type="a{{sv}}" name="options" direction="in"/>
      <arg type="s" name="id" direction="out"/>
    </method>
    <method name="Update">
      <arg type="s" name="id" direction="in"/><arg type="a{{sv}}" name="changes" direction="in"/>
    </method>
    <method name="Finish"><arg type="s" name="id" direction="in"/></method>
  </interface>
  <interface name="{core.NAME}.System">
    <method name="ShowDesk"/>
    <method name="ShowOverview"/>
    <method name="ShowLauncher"/>
  </interface>
</node>
"""
CACHE_SECONDS = 0.3
DEBOUNCE_MS = 250
MAX_WAIT_MS = 1000  # under a steady stream of Niri events, Changed still goes out at least this often


class Service:
    def __init__(self, connection, session, spaces_command=("flow-spaces",)):
        self.connection = connection
        self.spaces_command = list(spaces_command)
        self.shell = os.environ.get("FLOW_SHELL_CONFIG", "/etc/flow/shell.qml")
        self.activities = core.ActivityBook()
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        self.activity_file = Path(runtime) / "flow-activities.json" if runtime else None
        self.write_activities()
        self.limit = core.RateLimit()
        self.cache = (0.0, None)
        self.session = session
        self.pending = 0
        self.first_event = None

    def run_spaces(self, *args):
        process = subprocess.run([*self.spaces_command, *args], text=True, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, timeout=60)
        try:
            value = json.loads(process.stdout)
        except ValueError as exc:
            raise core.ApiError("Failed", "Flow state is unavailable") from exc
        if process.returncode or "error" in value:
            raise core.error_from(value.get("error") or "Flow state is unavailable")
        return value

    def snapshot(self, fresh=False):
        """Flow state, cached briefly for listing. Decisions that grant something use fresh state."""
        stamp, value = self.cache
        if fresh or value is None or time.monotonic() - stamp > CACHE_SECONDS:
            value = self.run_spaces("observe")
            self.cache = (time.monotonic(), value)
        return core.project(value, self.session)

    def identity(self, sender):
        reply = self.connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                          "GetConnectionUnixProcessID", GLib.Variant("(s)", (sender,)),
                                          GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, 5000, None)
        return core.caller(reply.unpack()[0])

    def call(self, sender, interface, method, params):
        identity = self.identity(sender)
        # A Flatpak app is limited across all its connections; other callers per connection.
        self.limit.check(core.limit_key(identity, sender))
        control = interface.endswith(".Control")
        if method == "GetCapabilities" and not control:
            major, minor, features = core.capabilities(identity)
            return GLib.Variant("(uuas)", (major, minor, features))
        if interface.endswith(".Activities"):
            return self.activity(sender, identity, method, params)
        if interface.endswith(".System"):
            what = {"ShowDesk": "desk", "ShowOverview": "overview", "ShowLauncher": "launcher"}.get(method)
            if what is None:
                raise core.ApiError("Unsupported", f"{method} is not supported")
            # Only an app needs Flow state here: who has keyboard focus, read fresh.
            windows = self.snapshot(fresh=True)[1] if identity["sandboxed"] else {}
            self.show(core.ui_request(identity, what, windows))
            return None
        # Anything that acts is authorized on fresh state; a cached snapshot could let an app steal focus.
        spaces, windows = self.snapshot(fresh=method != "ListSpaces")
        if method == "ListSpaces":
            shown = [core.public(s) for s in core.visible(identity, spaces, windows, control)]
            return GLib.Variant(f"({SPACE})", ([{k: variant(v) for k, v in s.items()} for s in shown],))
        if method == "FocusSpace":
            action, target = core.focus_target(identity, params[0], spaces, windows, control)
            self.run_spaces(action, target)
            self.cache = (0.0, None)
            return None
        steps = None
        if method == "PinSpace":
            steps = core.pin_steps(identity, params[0], spaces, windows)
        elif method == "UnpinSpace":
            steps = core.unpin_steps(identity, params[0], spaces)
        elif method == "MoveSpace":
            steps = core.move_steps(identity, params[0], params[1], spaces)
        if steps is None:
            raise core.ApiError("Unsupported", f"{method} is not supported")
        for action, target in steps:
            self.run_spaces(*([action, target] if target is not None else [action]))
        self.cache = (0.0, None)
        return None

    def activity(self, sender, identity, method, params):
        if method == "Start":
            owner = core.owner_label(identity)
            ident = self.activities.start(sender, owner, params[0], dict(params[1]), core.limit_key(identity, sender))
            self.write_activities()
            return GLib.Variant("(s)", (ident,))
        if method == "Update":
            self.activities.update(sender, params[0], dict(params[1]))
        elif method == "Finish":
            self.activities.finish(sender, params[0])
        else:
            raise core.ApiError("Unsupported", f"{method} is not supported")
        self.write_activities()
        return None

    def write_activities(self):
        """Publish live activities for the shell, atomically, on the session's tmpfs."""
        if not self.activity_file:
            return
        data = json.dumps({"version": 1, "activities": self.activities.public()})
        temporary = self.activity_file.with_name(self.activity_file.name + ".tmp")
        try:
            temporary.write_text(data)
            os.replace(temporary, self.activity_file)
        except OSError as exc:
            print(f"flow-api: could not publish activities: {exc}", file=sys.stderr)

    def owner_gone(self, name):
        if self.activities.drop_connection(name):
            self.write_activities()

    def show(self, ipc):
        try:
            process = subprocess.run(["quickshell", "ipc", "-p", self.shell, "call", "desk", ipc], text=True,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=2)
        except subprocess.TimeoutExpired as exc:
            raise core.ApiError("Failed", "The Flow shell did not answer") from exc
        if process.returncode:
            running = "no running instances" not in process.stderr.lower()
            raise core.ApiError("Failed", "The Flow shell did not accept the request" if running
                                else "The Flow shell is not running")
        core.ui_answer(process.stdout)

    def changed(self):
        self.pending = 0
        self.first_event = None
        self.cache = (0.0, None)
        self.connection.emit_signal(None, PATH, core.NAME + ".Spaces", "Changed", None)
        return False

    def niri_event(self):
        now = time.monotonic()
        self.first_event = self.first_event or now
        if self.pending:
            GLib.source_remove(self.pending)
        # Wait for a quiet moment, but never longer than MAX_WAIT_MS after the first unsent event.
        delay = min(DEBOUNCE_MS, max(0, int(MAX_WAIT_MS - (now - self.first_event) * 1000)))
        self.pending = GLib.timeout_add(delay, self.changed)


def variant(value):
    if isinstance(value, bool):
        return GLib.Variant("b", value)
    if isinstance(value, int):
        return GLib.Variant("u", value)
    if isinstance(value, list):
        return GLib.Variant("as", value)
    return GLib.Variant("s", str(value))


def watch_niri(service, children):
    """Turn Niri's event stream into debounced Changed signals; reconnect if it ends."""
    # setpriv: the kernel ends the stream if the service dies, even by SIGKILL; without it the stream
    # still runs, only without that guarantee.
    process = Gio.Subprocess.new(["sh", "-c", "command -v setpriv >/dev/null && exec setpriv --pdeathsig TERM"
                                  " niri msg -j event-stream; exec niri msg -j event-stream"],
                                 Gio.SubprocessFlags.STDOUT_PIPE)
    children[:] = [process]  # stopped when the service exits, so no stream outlives it
    stream = Gio.DataInputStream.new(process.get_stdout_pipe())

    def read(source, result):
        try:
            line, _ = source.read_line_finish_utf8(result)
        except GLib.Error:
            line = None
        if line is None:
            GLib.timeout_add_seconds(5, lambda: watch_niri(service, children) and False)
            return
        service.niri_event()
        source.read_line_async(GLib.PRIORITY_DEFAULT, None, read)

    stream.read_line_async(GLib.PRIORITY_DEFAULT, None, read)
    return False


def main():
    name = core.NAME
    try:
        session = spaces.session_id()  # fail before owning the name, so systemd can restart us
    except (OSError, ValueError) as exc:
        print(f"flow-api: no Niri session: {exc}", file=sys.stderr)
        sys.exit(1)
    loop = GLib.MainLoop()
    node = Gio.DBusNodeInfo.new_for_xml(XML)
    holder, children = {}, []
    try:
        from gi.repository import GLibUnix
        signal_add = GLibUnix.signal_add
    except ImportError:  # PyGObject before GLibUnix
        signal_add = GLib.unix_signal_add
    for signal in (15, 2):  # SIGTERM from systemd, SIGINT from a terminal
        signal_add(GLib.PRIORITY_DEFAULT, signal, lambda: loop.quit() or False)

    def handle(connection, sender, path, interface, method, params, invocation):
        try:
            invocation.return_value(holder["service"].call(sender, interface, method, params.unpack()))
        except core.ApiError as exc:
            invocation.return_dbus_error(f"{core.NAME}.Error.{exc.kind}", str(exc))
        except Exception as exc:  # noqa: BLE001 — every call gets an answer, never a client timeout
            invocation.return_dbus_error(f"{core.NAME}.Error.Failed", f"{type(exc).__name__}: {exc}")

    def acquired(connection, _name):
        holder["service"] = Service(connection, session)
        for interface in node.interfaces:
            connection.register_object(PATH, interface, handle, None, None)

        def name_owner_changed(_c, _s, _p, _i, _signal, params):
            name, _old, new = params.unpack()
            if not new:  # a connection closed: its activities end with it
                holder["service"].owner_gone(name)

        connection.signal_subscribe("org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
                                    "/org/freedesktop/DBus", None, Gio.DBusSignalFlags.NONE, name_owner_changed)

        watch_niri(holder["service"], children)

    def lost(_connection, _name):
        if "service" not in holder:
            print(f"flow-api: could not own {name}", file=sys.stderr)
        loop.quit()  # also when the bus itself goes away

    Gio.bus_own_name(Gio.BusType.SESSION, name, Gio.BusNameOwnerFlags.NONE, acquired, None, lost)
    try:
        loop.run()
    finally:
        for child in children:
            child.force_exit()
        if "service" in holder and holder["service"].activity_file:
            holder["service"].activity_file.unlink(missing_ok=True)  # nothing outlives the service


if __name__ == "__main__":
    main()
