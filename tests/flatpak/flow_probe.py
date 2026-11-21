#!/usr/bin/python3
"""FlowProbe: a Flatpak app that calls the Flow Platform API from inside its sandbox.

It is built by ``tests/niri_flatpak_api_smoke.py`` on the bare ``org.freedesktop.Platform`` runtime,
which has Python, GIO and GTK 3 but no PyGObject, so both are reached through ctypes. It opens one GTK
window per title given on the command line, with the Wayland app ID set to its Flatpak ID, then reads
JSON commands from standard input, one per line, and answers each on standard output:

- ``{"do": "connect"}`` opens another bus connection: ``{"conn": N, "name": unique name}``.
- ``{"do": "call", "conn": N, "interface": "Spaces", "method": "ListSpaces", "args": "()"}`` calls
  ``io.github.mattonik.Flow1.<interface>.<method>``; ``args`` is GVariant text. The answer is
  ``{"ok": [values]}`` or ``{"error": "NotAuthorized", "message": ...}``.

It exits when standard input ends.
"""
import ctypes
import json
import os
import sys
import threading

NAME = "io.github.mattonik.Flow1"
PATH = "/io/github/mattonik/Flow1"
APP = os.environ.get("FLATPAK_ID", "io.github.mattonik.FlowProbe")


class GError(ctypes.Structure):
    _fields_ = [("domain", ctypes.c_uint32), ("code", ctypes.c_int), ("message", ctypes.c_char_p)]


glib = ctypes.CDLL("libglib-2.0.so.0")
gio = ctypes.CDLL("libgio-2.0.so.0")
P, S = ctypes.c_void_p, ctypes.c_char_p
for lib, name, res, args in [
        (glib, "g_variant_parse", P, [P, S, S, P, ctypes.POINTER(ctypes.POINTER(GError))]),
        (glib, "g_variant_get_type_string", S, [P]),
        (glib, "g_variant_n_children", ctypes.c_size_t, [P]),
        (glib, "g_variant_get_child_value", P, [P, ctypes.c_size_t]),
        (glib, "g_variant_get_variant", P, [P]),
        (glib, "g_variant_get_string", S, [P, P]),
        (glib, "g_variant_get_boolean", ctypes.c_int, [P]),
        (glib, "g_variant_get_uint32", ctypes.c_uint32, [P]),
        (glib, "g_variant_get_int32", ctypes.c_int32, [P]),
        (glib, "g_variant_get_double", ctypes.c_double, [P]),
        (glib, "g_variant_unref", None, [P]),
        (glib, "g_set_prgname", None, [S]),
        (gio, "g_dbus_connection_new_for_address_sync", P, [S, ctypes.c_int, P, P,
                                                            ctypes.POINTER(ctypes.POINTER(GError))]),
        (gio, "g_dbus_connection_get_unique_name", S, [P]),
        (gio, "g_dbus_connection_call_sync", P, [P, S, S, S, S, P, P, ctypes.c_int, ctypes.c_int, P,
                                                 ctypes.POINTER(ctypes.POINTER(GError))]),
        (gio, "g_dbus_error_get_remote_error", S, [ctypes.POINTER(GError)])]:
    function = getattr(lib, name)
    function.restype, function.argtypes = res, args


def value(variant):
    """A GVariant as JSON-friendly Python: dictionaries, lists, strings and numbers."""
    kind = glib.g_variant_get_type_string(variant).decode()
    if kind == "v":
        inner = glib.g_variant_get_variant(variant)
        try:
            return value(inner)
        finally:
            glib.g_variant_unref(inner)
    if kind in ("s", "o", "g"):
        return glib.g_variant_get_string(variant, None).decode()
    if kind == "b":
        return bool(glib.g_variant_get_boolean(variant))
    if kind == "u":
        return glib.g_variant_get_uint32(variant)
    if kind == "i":
        return glib.g_variant_get_int32(variant)
    if kind == "d":
        return glib.g_variant_get_double(variant)
    children = []
    for index in range(glib.g_variant_n_children(variant)):
        child = glib.g_variant_get_child_value(variant, index)
        children.append(value(child))
        glib.g_variant_unref(child)
    if kind.startswith("a{"):
        return dict(children)
    return children


def connect():
    error = ctypes.POINTER(GError)()
    # AUTHENTICATION_CLIENT | MESSAGE_BUS_CONNECTION: a new connection of its own, not the shared one.
    connection = gio.g_dbus_connection_new_for_address_sync(os.environ["DBUS_SESSION_BUS_ADDRESS"].encode(), 1 | 8,
                                                            None, None, ctypes.byref(error))
    if not connection:
        raise RuntimeError(error.contents.message.decode())
    return connection


def call(connection, interface, method, args):
    error = ctypes.POINTER(GError)()
    parameters = glib.g_variant_parse(None, args.encode(), None, None, ctypes.byref(error))
    if not parameters:
        return {"error": "BadArguments", "message": error.contents.message.decode()}
    reply = gio.g_dbus_connection_call_sync(connection, NAME.encode(), PATH.encode(),
                                            f"{NAME}.{interface}".encode(), method.encode(), parameters, None, 0,
                                            10000, None, ctypes.byref(error))
    if not reply:
        remote = gio.g_dbus_error_get_remote_error(error)
        name = remote.decode() if remote else "unknown"
        return {"error": name.rsplit(".", 1)[-1], "remote": name, "message": error.contents.message.decode()}
    try:
        return {"ok": value(reply)}
    finally:
        glib.g_variant_unref(reply)


def serve():
    connections = [connect()]
    print(json.dumps({"ready": True, "conn": 0, "name": gio.g_dbus_connection_get_unique_name(connections[0]).decode(),
                      "app": APP}), flush=True)
    for line in sys.stdin:
        try:
            command = json.loads(line)
            if command["do"] == "connect":
                connections.append(connect())
                answer = {"conn": len(connections) - 1,
                          "name": gio.g_dbus_connection_get_unique_name(connections[-1]).decode()}
            elif command["do"] == "call":
                answer = call(connections[command.get("conn", 0)], command["interface"], command["method"],
                              command.get("args", "()"))
            else:
                answer = {"error": "BadCommand", "message": f"Unknown command {command['do']!r}"}
        except (ValueError, KeyError, IndexError, RuntimeError) as exc:
            answer = {"error": "BadCommand", "message": str(exc)}
        print(json.dumps(answer), flush=True)
    os._exit(0)  # standard input ended: the app exits, and every connection with it


def main():
    titles = sys.argv[1:]
    if titles:
        os.environ["GDK_BACKEND"] = "wayland"
        gtk = ctypes.CDLL("libgtk-3.so.0")
        gtk.gtk_init_check.restype = ctypes.c_int
        gtk.gtk_window_new.restype = P
        gtk.gtk_window_new.argtypes = [ctypes.c_int]
        gtk.gtk_window_set_title.argtypes = [P, S]
        gtk.gtk_window_set_default_size.argtypes = [P, ctypes.c_int, ctypes.c_int]
        gtk.gtk_widget_show.argtypes = [P]
        glib.g_set_prgname(APP.encode())  # GTK 3 makes this the Wayland app ID
        if not gtk.gtk_init_check(None, None):
            print(json.dumps({"error": "NoDisplay", "message": "GTK could not open the Wayland display"}), flush=True)
            sys.exit(1)
        for title in titles:
            window = gtk.gtk_window_new(0)
            gtk.gtk_window_set_title(window, title.encode())
            gtk.gtk_window_set_default_size(window, 320, 200)
            gtk.gtk_widget_show(window)
        threading.Thread(target=serve, daemon=True).start()
        gtk.gtk_main()  # ctypes releases the GIL here, so serve() keeps answering
    else:
        serve()


if __name__ == "__main__":
    main()
