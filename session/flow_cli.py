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
import math
import os
import select
import signal
import subprocess
import sys
import time

from gi.repository import Gio, GLib

NAME = "io.github.mattonik.Flow1"
PATH = "/io/github/mattonik/Flow1"


def call(bus, interface, method, args=None, reply="()"):
    return bus.call_sync(NAME, PATH, f"{NAME}.{interface}", method, args, GLib.VariantType(reply),
                         Gio.DBusCallFlags.NONE, 30000, None).unpack()


def main(argv):
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
        return run(bus, args[2], args[4:])
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


def quietly(bus, method, args, problems):
    """An activity call whose failure never stops the work it reports; the first problem is shown once."""
    try:
        return call(bus, "Activities", method, args, reply="(s)" if method == "Start" else "()")
    except GLib.Error as exc:
        if not problems:
            Gio.DBusError.strip_remote_error(exc)
            print(f"flow: the activity could not be shown: {exc.message}", file=sys.stderr)
        problems.append(exc)
        return None


def run(bus, title, command):
    """Show an activity while a command runs. The command's exit status is always the result."""
    problems = []
    started = quietly(bus, "Start", GLib.Variant("(sa{sv})", (title, {})), problems)
    child = subprocess.Popen(command)
    forwarded = {}
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        # The command decides how to stop; flow only passes the signal on and waits.
        forwarded[signum] = signal.signal(signum, lambda number, _frame: child.send_signal(number))
    try:
        status = child.wait()
    finally:
        for signum, handler in forwarded.items():
            signal.signal(signum, handler)
        if started:
            quietly(bus, "Finish", GLib.Variant("(s)", started), problems)
    return 128 - status if status < 0 else status  # killed by signal N: 128 + N, as shells report


def progress_value(text):
    """A percentage from 0 to 100 as a fraction, or None when the line is not a finite number."""
    try:
        value = float(text.strip().rstrip("%"))
    except ValueError:
        return None
    return min(max(value / 100, 0.0), 1.0) if math.isfinite(value) and text.strip()[:1] in "+-.0123456789" else None


def progress(bus, title, stream, interval=1.0, limit=4096):
    """Show progress read as 0-100 per line (newline or carriage return); other lines become the detail.

    At most one update per ``interval`` goes out, and the latest value always does, even without more
    input. Standard input is always read to its end, so the program feeding it never gets a broken pipe.
    """
    problems = []
    started = quietly(bus, "Start", GLib.Variant("(sa{sv})", (title, {"progress": GLib.Variant("d", 0.0)})), problems)
    fd, buffer, pending, sent = stream.fileno(), b"", {}, 0.0

    def take(raw):
        text = raw.decode(errors="replace").strip()
        value = progress_value(text)
        if value is not None:
            pending["progress"] = GLib.Variant("d", value)
        elif text:
            pending["detail"] = GLib.Variant("s", text[:200])

    def flush():
        nonlocal pending, sent
        if pending and started and not problems:
            quietly(bus, "Update", GLib.Variant("(sa{sv})", (started[0], pending)), problems)
        pending, sent = {}, time.monotonic()

    while True:
        wait = max(0.0, interval - (time.monotonic() - sent)) if pending else None
        ready, _, _ = select.select([fd], [], [], wait)
        if ready:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            *lines, buffer = (buffer + chunk).replace(b"\r", b"\n").split(b"\n")
            buffer = buffer[-limit:]  # a line longer than this is not progress
            for raw in lines:
                take(raw)
        if pending and time.monotonic() - sent >= interval:
            flush()
    take(buffer)  # the last line may have no newline
    flush()
    if started:
        quietly(bus, "Finish", GLib.Variant("(s)", started), problems)
    return 0

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
