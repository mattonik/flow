#!/usr/bin/env python3
"""flow: a command-line client of the Flow Platform API (ADR-0009). It uses D-Bus only.

    flow version
    flow spaces list [--mine]
    flow spaces focus ID [--mine]
    flow spaces pin ID | unpin ID | move ID left|right
    flow desk show | overview show | launcher show
    flow activity run TITLE -- COMMAND [ARGS...]
    flow activity progress TITLE      (reads 0-100 per line from standard input)
    flow events watch

Without --mine it uses the Control interface (every space; unsandboxed callers only). With --mine it
uses the Spaces interface, which shows and focuses only spaces holding this process's own windows.
"""
import json
import os
import select
import subprocess
import sys
import time

from gi.repository import Gio, GLib

NAME = "io.github.mattonik.Flow1"
PATH = "/io/github/mattonik/Flow1"


def call(bus, interface, method, args=None, reply="()"):
    return bus.call_sync(NAME, PATH, f"{NAME}.{interface}", method, args, GLib.VariantType(reply),
                         Gio.DBusCallFlags.NONE, 30000, None).unpack()


def name_process():
    """Run as "flow", not "python3": the island labels activities with the caller's process name."""
    try:
        import ctypes
        ctypes.CDLL(None).prctl(15, b"flow", 0, 0, 0)  # PR_SET_NAME
    except (OSError, AttributeError):
        pass


def main(argv):
    name_process()
    mine = "--mine" in argv
    args = [a for a in argv if a != "--mine"]
    if mine and not (args == ["spaces", "list"] or args[:2] == ["spaces", "focus"]):
        print("--mine applies only to spaces list and spaces focus", file=sys.stderr)
        return 2
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    interface = "Spaces" if mine else "Control"
    if args == ["version"]:
        major, minor, features = call(bus, "Spaces", "GetCapabilities", reply="(uuas)")
        print(json.dumps({"version": f"{major}.{minor}", "features": features}))
    elif args == ["spaces", "list"]:
        (spaces,) = call(bus, interface, "ListSpaces", reply="(aa{sv})")
        print(json.dumps(spaces, indent=1))
    elif len(args) == 3 and args[:2] == ["spaces", "focus"]:
        call(bus, interface, "FocusSpace", GLib.Variant("(s)", (args[2],)))
    elif len(args) == 3 and args[:2] == ["spaces", "pin"]:
        call(bus, "Control", "PinSpace", GLib.Variant("(s)", (args[2],)))
    elif len(args) == 3 and args[:2] == ["spaces", "unpin"]:
        call(bus, "Control", "UnpinSpace", GLib.Variant("(s)", (args[2],)))
    elif len(args) == 4 and args[:2] == ["spaces", "move"]:
        call(bus, "Control", "MoveSpace", GLib.Variant("(ss)", (args[2], args[3])))
    elif len(args) == 2 and args[0] in ("desk", "overview", "launcher") and args[1] == "show":
        call(bus, "System", {"desk": "ShowDesk", "overview": "ShowOverview", "launcher": "ShowLauncher"}[args[0]])
    elif len(args) >= 4 and args[:2] == ["activity", "run"] and args[3] == "--" and len(args) > 4:
        # The activity lasts as long as the command; it also ends if this process dies.
        (ident,) = call(bus, "Activities", "Start", GLib.Variant("(sa{sv})", (args[2], {})), reply="(s)")
        try:
            return subprocess.call(args[4:])
        finally:
            call(bus, "Activities", "Finish", GLib.Variant("(s)", (ident,)))
    elif len(args) == 3 and args[:2] == ["activity", "progress"]:
        return progress(bus, args[2], sys.stdin)
    elif args == ["events", "watch"]:
        loop = GLib.MainLoop()

        def changed(*_):
            print(json.dumps({"event": "spaces-changed"}), flush=True)

        bus.signal_subscribe(NAME, f"{NAME}.Spaces", "Changed", PATH, None, Gio.DBusSignalFlags.NONE, changed)
        loop.run()
    else:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    return 0


def progress(bus, title, stream, interval=0.5):
    """Show progress read as 0-100 per line; other lines become the detail.

    Updates go out at most every ``interval`` seconds, and the latest value always goes out once the
    interval has passed, even if no further line arrives.
    """
    (ident,) = call(bus, "Activities", "Start", GLib.Variant("(sa{sv})", (title, {"progress": GLib.Variant("d", 0.0)})),
                    reply="(s)")
    fd, buffer, pending, sent = stream.fileno(), b"", {}, 0.0

    def flush():
        nonlocal pending, sent
        if pending:
            call(bus, "Activities", "Update", GLib.Variant("(sa{sv})", (ident, pending)))
            pending, sent = {}, time.monotonic()

    try:
        while True:
            wait = max(0.0, interval - (time.monotonic() - sent)) if pending else None
            ready, _, _ = select.select([fd], [], [], wait)
            if ready:
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                *lines, buffer = (buffer + chunk).split(b"\n")
                for raw in lines:
                    text = raw.decode(errors="replace").strip()
                    try:
                        pending["progress"] = GLib.Variant("d", min(max(float(text.rstrip("%")) / 100, 0.0), 1.0))
                    except ValueError:
                        if text:
                            pending["detail"] = GLib.Variant("s", text)
            if pending and time.monotonic() - sent >= interval:
                flush()
        flush()
        return 0
    finally:
        call(bus, "Activities", "Finish", GLib.Variant("(s)", (ident,)))

if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except GLib.Error as exc:
        # D-Bus errors arrive as "GDBus.Error:<name>: <message>"; print the structured kind.
        remote = Gio.DBusError.get_remote_error(exc) or ""
        kind = remote.rsplit(".", 1)[-1] if remote.startswith(NAME + ".Error.") else "Failed"
        Gio.DBusError.strip_remote_error(exc)
        print(json.dumps({"error": kind, "message": exc.message}), file=sys.stderr)
        sys.exit(1)
