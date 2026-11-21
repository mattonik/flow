#!/usr/bin/env python3
"""The Flow Platform API service (ADR-0009): spaces on the session D-Bus.

All policy is in flow_api_core; this file only binds it to GDBus, reads state through the read-only
``flow-spaces observe`` and focuses through the same ``flow-spaces`` actions the shell uses.
"""
import json
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
  </interface>
</node>
"""
CACHE_SECONDS = 0.3
DEBOUNCE_MS = 250


class Service:
    def __init__(self, connection, spaces_command=("flow-spaces",)):
        self.connection = connection
        self.spaces_command = list(spaces_command)
        self.limit = core.RateLimit()
        self.cache = (0.0, None)
        self.session = spaces.session_id()
        self.pending = 0

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

    def snapshot(self):
        stamp, value = self.cache
        if value is None or time.monotonic() - stamp > CACHE_SECONDS:
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
            return GLib.Variant("(uuas)", (*core.VERSION, list(core.FEATURES)))
        spaces, windows = self.snapshot()
        if method == "ListSpaces":
            shown = [core.public(s) for s in core.visible(identity, spaces, windows, control)]
            return GLib.Variant(f"({SPACE})", ([{k: variant(v) for k, v in s.items()} for s in shown],))
        if method == "FocusSpace":
            action, target = core.focus_target(identity, params[0], spaces, windows, control)
            self.run_spaces(action, target)
            self.cache = (0.0, None)
            return None
        raise core.ApiError("Unsupported", f"{method} is not supported")

    def changed(self):
        self.pending = 0
        self.cache = (0.0, None)
        self.connection.emit_signal(None, PATH, core.NAME + ".Spaces", "Changed", None)
        return False

    def niri_event(self):
        if self.pending:
            GLib.source_remove(self.pending)
        self.pending = GLib.timeout_add(DEBOUNCE_MS, self.changed)


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
    process = Gio.Subprocess.new(["niri", "msg", "-j", "event-stream"], Gio.SubprocessFlags.STDOUT_PIPE)
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
        except (OSError, subprocess.SubprocessError, GLib.Error) as exc:
            invocation.return_dbus_error(f"{core.NAME}.Error.Failed", str(exc))

    def acquired(connection, _name):
        holder["service"] = Service(connection)
        for interface in node.interfaces:
            connection.register_object(PATH, interface, handle, None, None)
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


if __name__ == "__main__":
    main()
