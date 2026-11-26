"""Flow's Screenshot portal backend: ``org.freedesktop.impl.portal.Screenshot`` (ADR-0005, F-012).

GNOME's backend answers an interactive screenshot request (``interactive: true``, "let the user choose") by
calling ``org.gnome.Shell.Screenshot.InteractiveScreenshot``. Niri implements that interface's ``Screenshot`` and
``PickColor`` but not ``InteractiveScreenshot``, so under GNOME's backend every interactive request fails. Flow
answers the interface instead:

- **Interactive:** a dialog from GTK's Access backend (the one that already asks whether an app may take
  screenshots) asks the user to take a screenshot of the whole screen, or to cancel. Then Niri's
  ``Screenshot`` saves it. There is no choice of area or window: Niri's own screenshot UI says nothing on D-Bus
  when the user cancels it, which would leave the app's request waiting for ever.
- **Not interactive:** Niri's ``Screenshot`` at once. The broker has already asked the user whether the app may
  take screenshots (the Access dialog) before it calls a backend.
- **PickColor:** Niri's own colour picker.

Niri saves the screenshot where its ``screenshot-path`` says (by default ``~/Pictures/Screenshots/Screenshot from
<time>.png``, as GNOME's backend names them), whatever name it is given, and answers with that path; it leaves the
clipboard alone.
"""

import sys
import uuid

NAME = "org.freedesktop.impl.portal.desktop.flow"
PATH = "/org/freedesktop/portal/desktop"
SHELL = ("org.gnome.Shell.Screenshot", "/org/gnome/Shell/Screenshot", "org.gnome.Shell.Screenshot")
ACCESS = ("org.freedesktop.impl.portal.desktop.gtk", PATH, "org.freedesktop.impl.portal.Access")
# Long enough for the consent dialog to be gone from the screen before the screenshot (measured in the guest:
# a screenshot taken as the dialog answered still showed it fading out).
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
    """The interactive request's dialog: title, subtitle, body, and its two buttons' labels."""
    who = app_name or "An app"
    return {"title": "Take a Screenshot?", "subtitle": f"{who} asks for a screenshot of the whole screen.",
            "body": "The screenshot is saved in Pictures and given to the app.",
            "deny_label": "Cancel", "grant_label": "Take Screenshot"}


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
        """The Request object the broker may Close while the user decides; ``answer`` replies once."""
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

        state["id"] = self.connection.register_object(handle, self.request_info, on_close, None, None)
        answer.done = lambda: state["answered"]
        return answer

    def screenshot(self, handle, app_id, parent_window, options, invocation):
        dialog = f"{PATH}/request/flow/{uuid.uuid4().hex}"

        def close_dialog():
            self.connection.call(ACCESS[0], dialog, "org.freedesktop.impl.portal.Request", "Close", None, None,
                                 self.Gio.DBusCallFlags.NONE, -1, None, None)
        answer = self.request(handle, invocation, close_dialog if options.get("interactive") else None)

        def take():
            # Nothing is captured for a request already answered: closed by the broker (the app gave up) while the
            # user decided, or while the dialog faded.
            if answer.done():
                return False

            def saved(connection, result):
                try:
                    ok, used = connection.call_finish(result).unpack()
                except self.GLib.Error as exc:
                    print(f"flow-screenshot-portal: Niri's Screenshot failed: {exc.message}", file=sys.stderr)
                    return answer(OTHER)
                answer(SUCCESS, {"uri": self.GLib.Variant("s", self.Gio.File.new_for_path(used).get_uri())}) if ok \
                    else answer(OTHER)
            self.call(SHELL, "Screenshot", self.GLib.Variant("(bbs)", (False, True, "Screenshot")), "(bs)", saved)
            return False

        if not options.get("interactive"):
            return take()
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
            if code != SUCCESS or answer.done():
                return answer(CANCELLED)
            self.GLib.timeout_add(AFTER_DIALOG_MS, take)
        self.call(ACCESS, "AccessDialog", self.GLib.Variant("(osssssa{sv})", (
            dialog, app_id, parent_window, text["title"], text["subtitle"], text["body"], dialog_options)),
            "(ua{sv})", decided, timeout=self.GLib.MAXINT)  # the user decides: no time limit (D-Bus's 25 s failed one)

    def pick_color(self, handle, app_id, parent_window, options, invocation):
        answer = self.request(handle, invocation)

        def picked(connection, result):
            try:
                results = connection.call_finish(result).unpack()[0]
            except self.GLib.Error as exc:
                print(f"flow-screenshot-portal: Niri's PickColor failed: {exc.message}", file=sys.stderr)
                return answer(OTHER)
            if "color" not in results:
                return answer(CANCELLED)
            answer(SUCCESS, {"color": self.GLib.Variant("(ddd)", tuple(results["color"]))})
        self.call(SHELL, "PickColor", None, "(a{sv})", picked)


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

    def acquired(connection, _name):
        holder["backend"] = Backend(connection, Gio, GLib)
        connection.register_object(PATH, node.interfaces[0], method, get_property, None)

    def lost(_connection, _name):
        if "backend" not in holder:
            print(f"flow-screenshot-portal: could not own {NAME}", file=sys.stderr)
        loop.quit()

    for signal in (15, 2):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal, lambda: loop.quit() or False)
    Gio.bus_own_name(Gio.BusType.SESSION, NAME, Gio.BusNameOwnerFlags.NONE, acquired, None, lost)
    loop.run()


if __name__ == "__main__":
    main()
