"""Flow's Screenshot portal backend: ``org.freedesktop.impl.portal.Screenshot`` (ADR-0005, F-012).

GNOME's backend answers an interactive screenshot request (``interactive: true``, "let the user choose") by
calling ``org.gnome.Shell.Screenshot.InteractiveScreenshot``. Niri implements that interface's ``Screenshot`` and
``PickColor`` but not ``InteractiveScreenshot``, so under GNOME's backend every interactive request fails. Flow
answers the interface instead:

- **Consent.** A request is captured at once only when the broker says it has checked the app's permission
  (``permission_store_checked``, which it sets after its own Access dialog or a stored answer). Every other request,
  interactive or not, first shows a dialog from GTK's Access backend asking the user to take a screenshot of the
  whole screen, or to cancel. The broker skips its own check while it has not read this backend's version (after
  the backend left the bus, for one), and nothing reaches the screen without one of the two.
- **Capture.** grim writes the whole layout, every display, to a new file under ``~/Pictures/Screenshots`` named as
  GNOME's backend names them, created by this backend (mode 0600, never an existing file). Niri's own D-Bus
  ``Screenshot`` was not used: it refuses more than one display, fails without a reply when the folder is missing,
  writes wherever Niri's ``screenshot-path`` says (``/tmp/screenshot.png`` when it is null), and hands concurrent
  callers each other's results.
- **No area or window choice.** Niri's screenshot UI says nothing on D-Bus when the user cancels it, which would
  leave the app's request waiting for ever.
- **PickColor:** Niri's own colour picker; cancelling it answers 1.

Every request is answered once: on a Close from the broker (the app gave up), a failure, or an exception in any
step, and nothing is captured for a request already answered.
"""

import datetime
import os
from pathlib import Path
import signal
import sys
import uuid

NAME = "org.freedesktop.impl.portal.desktop.flow"
PATH = "/org/freedesktop/portal/desktop"
SHELL = ("org.gnome.Shell.Screenshot", "/org/gnome/Shell/Screenshot", "org.gnome.Shell.Screenshot")
ACCESS = ("org.freedesktop.impl.portal.desktop.gtk", PATH, "org.freedesktop.impl.portal.Access")
# Long enough for the consent dialog to be gone from the screen before the screenshot: one taken as the dialog
# answered still showed it fading out.
AFTER_DIALOG_MS = 600
XML = """<node>
  <interface name="org.freedesktop.impl.portal.Screenshot">
    <method name="Screenshot">
      <arg type="o" name="handle" direction="in"/><arg type="s" name="app_id" direction="in"/>
      <arg type="s" name="parent_window" direction="in"/><arg type="a{sv}" name="options" direction="in"/>
      <arg type="u" name="response" direction="out"/><arg type="a{sv}" name="results" direction="out"/>
    </method>
    <method name="PickColor">
      <arg type="o" name="handle" direction="in"/><arg type="s" name="app_id" direction="in"/>
      <arg type="s" name="parent_window" direction="in"/><arg type="a{sv}" name="options" direction="in"/>
      <arg type="u" name="response" direction="out"/><arg type="a{sv}" name="results" direction="out"/>
    </method>
    <property name="version" type="u" access="read"/>
  </interface>
</node>"""
REQUEST_XML = """<node><interface name="org.freedesktop.impl.portal.Request"><method name="Close"/></interface></node>"""
SUCCESS, CANCELLED, OTHER = 0, 1, 2


def consent(app_name):
    """The consent dialog: title, subtitle, body, and its two buttons' labels."""
    who = app_name or "An app"
    return {"title": "Take a Screenshot?", "subtitle": f"{who} asks for a screenshot of the whole screen.",
            "body": "The screenshot is saved in Pictures and given to the app.",
            "deny_label": "Cancel", "grant_label": "Take Screenshot"}


def needs_consent(options):
    """Whether this backend must ask: always, unless the broker says it has checked the app's permission."""
    return bool(options.get("interactive")) or options.get("permission_store_checked") is not True


def new_screenshot(folder, now):
    """A new, empty file for a screenshot taken at ``now``: named as GNOME's backend names them, readable by its owner
    only, never an existing file (two in one second get ``-2``, ``-3``…). Returns its path."""
    folder = Path(folder)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    stem, n = f"Screenshot from {now:%Y-%m-%d %H-%M-%S}", 1
    while True:
        path = folder / (f"{stem}.png" if n == 1 else f"{stem}-{n}.png")
        try:
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
            return path
        except FileExistsError:
            n += 1


def pictures():
    return Path(os.environ.get("XDG_PICTURES_DIR") or Path.home() / "Pictures") / "Screenshots"


class Backend:
    def __init__(self, connection, Gio, GLib):
        self.connection, self.Gio, self.GLib = connection, Gio, GLib
        self.request_info = Gio.DBusNodeInfo.new_for_xml(REQUEST_XML).interfaces[0]

    def app_name(self, app_id):
        """The app's name from its desktop entry, else its ID: a sideloaded Flatpak may have no entry, and PyGObject
        raises TypeError for the NULL that GDesktopAppInfo then returns."""
        if not app_id:
            return ""
        try:
            info = self.Gio.DesktopAppInfo.new(app_id + ".desktop")
        except (TypeError, self.GLib.Error):
            info = None
        return (info.get_display_name() if info else "") or app_id

    def call(self, target, method, args, reply_type, done, timeout=-1):
        """``timeout`` in ms, D-Bus's default (25 s) unless given; ``GLib.MAXINT`` waits as long as it takes."""
        name, path, interface = target
        self.connection.call(name, path, interface, method, args, self.GLib.VariantType(reply_type),
                             self.Gio.DBusCallFlags.NONE, timeout, None, done)

    def request(self, handle, invocation, close=None):
        """The Request object the broker may Close while the user decides. ``answer`` replies once;
        ``answer.guard(step)`` wraps an asynchronous step so that an exception in it answers 2."""
        state = {"answered": False}

        def answer(code, results=None):
            if state["answered"]:
                return
            state["answered"] = True
            self.connection.unregister_object(state["id"])
            invocation.return_value(self.GLib.Variant("(ua{sv})", (code, results or {})))

        def on_close(_c, _s, _p, _i, _m, _params, close_invocation):
            close_invocation.return_value(None)
            if close:
                close()
            answer(OTHER)

        def guard(step):
            def run(*args):
                try:
                    return step(*args)
                except Exception as exc:  # noqa: BLE001 — the broker waits for an answer with no time limit
                    print(f"flow-screenshot-portal: {type(exc).__name__}: {exc}", file=sys.stderr)
                    answer(OTHER)
                    return False
            return run

        state["id"] = self.connection.register_object(handle, self.request_info, on_close, None, None)
        answer.done = lambda: state["answered"]
        answer.guard = guard
        return answer

    def capture(self, done):
        """The whole layout with grim, into a new private file; ``done(path)``, or ``done(None)`` if it failed."""
        path = new_screenshot(pictures(), datetime.datetime.now())
        process = self.Gio.Subprocess.new(["grim", str(path)], self.Gio.SubprocessFlags.STDERR_PIPE)

        def finished(proc, result):
            ok = False
            try:
                ok = proc.wait_check_finish(result)
            except self.GLib.Error as exc:
                print(f"flow-screenshot-portal: grim failed: {exc.message}", file=sys.stderr)
            if not ok:
                path.unlink(missing_ok=True)
            done(path if ok else None)
        process.wait_check_async(None, finished)

    def screenshot(self, handle, app_id, parent_window, options, invocation):
        dialog = f"{PATH}/request/flow/{uuid.uuid4().hex}"
        ask = needs_consent(options)

        def close_dialog():
            self.connection.call(ACCESS[0], dialog, "org.freedesktop.impl.portal.Request", "Close", None, None,
                                 self.Gio.DBusCallFlags.NONE, -1, None, None)
        answer = self.request(handle, invocation, close_dialog if ask else None)

        def saved(path):
            if path is None:
                answer(OTHER)
            else:
                answer(SUCCESS, {"uri": self.GLib.Variant("s", self.Gio.File.new_for_path(str(path)).get_uri())})

        def take():
            # Nothing is captured for a request already answered: closed by the broker while the user decided, or
            # while the dialog faded.
            if not answer.done():
                self.capture(answer.guard(saved))
            return False

        if not ask:
            return answer.guard(take)()
        text = consent(self.app_name(app_id))
        dialog_options = {k: self.GLib.Variant("s", text[k]) for k in ("deny_label", "grant_label")}
        dialog_options["icon"] = self.GLib.Variant("s", "applets-screenshooter-symbolic")
        dialog_options["modal"] = self.GLib.Variant("b", bool(options.get("modal", True)))

        def decided(connection, result):
            try:
                code, _ = connection.call_finish(result).unpack()
            except self.GLib.Error as exc:
                print(f"flow-screenshot-portal: the consent dialog failed: {exc.message}", file=sys.stderr)
                return answer(OTHER)
            if code != SUCCESS:
                return answer(CANCELLED)
            self.GLib.timeout_add(AFTER_DIALOG_MS, answer.guard(take))
        # The user decides: no time limit (D-Bus's default of 25 s would fail a slow answer).
        self.call(ACCESS, "AccessDialog", self.GLib.Variant("(osssssa{sv})", (
            dialog, app_id, parent_window, text["title"], text["subtitle"], text["body"], dialog_options)),
            "(ua{sv})", answer.guard(decided), timeout=self.GLib.MAXINT)

    def pick_color(self, handle, app_id, parent_window, options, invocation):
        answer = self.request(handle, invocation)

        def picked(connection, result):
            try:
                results = connection.call_finish(result).unpack()[0]
            except self.GLib.Error as exc:
                # Niri answers a picker closed without a pick with this error.
                if "no color picked" in exc.message:
                    return answer(CANCELLED)
                print(f"flow-screenshot-portal: Niri's PickColor failed: {exc.message}", file=sys.stderr)
                return answer(OTHER)
            answer(SUCCESS, {"color": self.GLib.Variant("(ddd)", tuple(results["color"]))})
        # The user picks: no time limit.
        self.call(SHELL, "PickColor", None, "(a{sv})", answer.guard(picked), timeout=self.GLib.MAXINT)


def main():
    from gi.repository import Gio, GLib
    loop = GLib.MainLoop()
    node = Gio.DBusNodeInfo.new_for_xml(XML)
    holder = {}

    def method(connection, _sender, _path, _interface, name, params, invocation):
        try:
            args = params.unpack()
            if name == "Screenshot":
                holder["backend"].screenshot(*args, invocation)
            elif name == "PickColor":
                holder["backend"].pick_color(*args, invocation)
            else:
                invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", name)
        except Exception as exc:  # noqa: BLE001 — every request gets an answer, never a client timeout
            invocation.return_dbus_error("org.freedesktop.portal.Error.Failed", f"{type(exc).__name__}: {exc}")

    def get_property(_connection, _sender, _path, _interface, name):
        return GLib.Variant("u", 2) if name == "version" else None

    def bus_acquired(connection, _name):
        # Before the name is owned: D-Bus activation delivers the call that started us as soon as it is, and a call
        # to a path not yet registered would fail.
        holder["backend"] = Backend(connection, Gio, GLib)
        connection.register_object(PATH, node.interfaces[0], method, get_property, None)

    def lost(_connection, _name):
        print(f"flow-screenshot-portal: lost or could not own {NAME}", file=sys.stderr)
        holder["failed"] = True
        loop.quit()

    for number in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, number, lambda: loop.quit() or False)
    Gio.bus_own_name(Gio.BusType.SESSION, NAME, Gio.BusNameOwnerFlags.NONE, bus_acquired, None, lost)
    loop.run()
    sys.exit(1 if holder.get("failed") else 0)


if __name__ == "__main__":
    main()
