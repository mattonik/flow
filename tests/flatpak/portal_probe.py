#!/usr/bin/python3
"""PortalProbe: a Flatpak app that asks the desktop portals for files and screenshots from inside its sandbox.

``tests/niri_portals_smoke.py`` builds it on the bare ``org.freedesktop.Platform`` runtime (Python and GIO, no
PyGObject, so GIO is reached through ctypes) with no filesystem access and no D-Bus names beyond what every
Flatpak has: the portals. It reads JSON commands from standard input, one per line, and answers each on standard
output:

- ``{"do": "request", "interface": "FileChooser", "method": "OpenFile", "args": "('', 'Open', {...})"}`` calls
  ``org.freedesktop.portal.<interface>.<method>``; ``args`` is GVariant text, and the probe adds its own
  ``handle_token`` to the last argument, the options. It waits for the request's ``Response`` (up to
  ``timeout`` seconds, default 120) and answers ``{"response": code, "results": {...}}``, or ``{"error": ...}``.
- ``{"do": "read", "path": P}`` answers ``{"text": ...}``; ``{"do": "write", "path": P, "text": T}`` answers
  ``{"written": n}``; ``{"do": "list", "path": D}`` answers ``{"names": [...]}``. A refusal answers
  ``{"error": "FileNotFoundError", "message": ...}`` (or the exception's name).

It exits when standard input ends.
"""
import ctypes
import json
import os
import sys
import time
import uuid

DESKTOP, PATH = "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop"


class GError(ctypes.Structure):
    _fields_ = [("domain", ctypes.c_uint32), ("code", ctypes.c_int), ("message", ctypes.c_char_p)]


glib = ctypes.CDLL("libglib-2.0.so.0")
gio = ctypes.CDLL("libgio-2.0.so.0")
P, S = ctypes.c_void_p, ctypes.c_char_p
ERR = ctypes.POINTER(ctypes.POINTER(GError))
# void (*GDBusSignalCallback)(connection, sender, object_path, interface, signal, parameters, user_data)
SIGNAL = ctypes.CFUNCTYPE(None, P, S, S, S, S, P, P)
for lib, name, res, args in [
        (glib, "g_variant_parse", P, [P, S, S, P, ERR]),
        (glib, "g_variant_get_type_string", S, [P]),
        (glib, "g_variant_n_children", ctypes.c_size_t, [P]),
        (glib, "g_variant_get_child_value", P, [P, ctypes.c_size_t]),
        (glib, "g_variant_get_variant", P, [P]),
        (glib, "g_variant_get_string", S, [P, P]),
        (glib, "g_variant_get_boolean", ctypes.c_int, [P]),
        (glib, "g_variant_get_byte", ctypes.c_uint8, [P]),
        (glib, "g_variant_get_uint32", ctypes.c_uint32, [P]),
        (glib, "g_variant_get_int32", ctypes.c_int32, [P]),
        (glib, "g_variant_get_double", ctypes.c_double, [P]),
        (glib, "g_variant_unref", None, [P]),
        (glib, "g_main_context_iteration", ctypes.c_int, [P, ctypes.c_int]),
        (gio, "g_bus_get_sync", P, [ctypes.c_int, P, ERR]),
        (gio, "g_dbus_connection_get_unique_name", S, [P]),
        (gio, "g_dbus_connection_signal_subscribe", ctypes.c_uint, [P, S, S, S, S, S, ctypes.c_int, SIGNAL, P, P]),
        (gio, "g_dbus_connection_signal_unsubscribe", None, [P, ctypes.c_uint]),
        (gio, "g_dbus_connection_call_sync", P, [P, S, S, S, S, P, P, ctypes.c_int, ctypes.c_int, P, ERR]),
        (gio, "g_dbus_error_get_remote_error", S, [ctypes.POINTER(GError)])]:
    function = getattr(lib, name)
    function.restype, function.argtypes = res, args


def value(variant):
    """A GVariant as JSON-friendly Python; a byte string (``ay``) becomes text without its terminating NUL."""
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
    if kind == "y":
        return glib.g_variant_get_byte(variant)
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
    if kind == "ay":
        return bytes(children).rstrip(b"\0").decode(errors="replace")
    if kind.startswith("a{"):
        return dict(children)
    return children


class Portal:
    def __init__(self):
        error = ctypes.POINTER(GError)()
        self.bus = gio.g_bus_get_sync(2, None, ctypes.byref(error))  # G_BUS_TYPE_SESSION, through Flatpak's proxy
        if not self.bus:
            raise RuntimeError(error.contents.message.decode())
        self.sender = gio.g_dbus_connection_get_unique_name(self.bus).decode()[1:].replace(".", "_")
        self.answer = None
        self.callback = SIGNAL(self.on_response)  # kept: ctypes must not free it while subscribed

    def on_response(self, _connection, _sender, _path, _interface, _signal, parameters, _data):
        self.answer = value(parameters)

    def request(self, interface, method, args, timeout=120):
        token = "probe" + uuid.uuid4().hex[:12]
        path = f"{PATH}/request/{self.sender}/{token}"
        text = args.strip()
        if not text.endswith("})"):
            raise ValueError("The last argument must be the options dictionary")
        text = text[:-2] + (", " if not text[:-2].rstrip().endswith("{") else "") + f"'handle_token': <'{token}'>}})"
        error = ctypes.POINTER(GError)()
        parameters = glib.g_variant_parse(None, text.encode(), None, None, ctypes.byref(error))
        if not parameters:
            return {"error": "BadArguments", "message": error.contents.message.decode()}
        self.answer = None
        sub = gio.g_dbus_connection_signal_subscribe(self.bus, DESKTOP.encode(), b"org.freedesktop.portal.Request",
                                                     b"Response", path.encode(), None, 0, self.callback, None, None)
        try:
            reply = gio.g_dbus_connection_call_sync(self.bus, DESKTOP.encode(), PATH.encode(),
                                                    f"org.freedesktop.portal.{interface}".encode(), method.encode(),
                                                    parameters, None, 0, 30000, None, ctypes.byref(error))
            if not reply:
                remote = gio.g_dbus_error_get_remote_error(error)
                return {"error": (remote.decode() if remote else "unknown"), "message": error.contents.message.decode()}
            handle = value(reply)[0]
            glib.g_variant_unref(reply)
            if handle != path:
                return {"error": "UnexpectedHandle", "message": handle}
            end = time.monotonic() + timeout
            while self.answer is None and time.monotonic() < end:
                glib.g_main_context_iteration(None, 0)
                time.sleep(0.02)
            if self.answer is None:
                return {"error": "Timeout", "message": f"No response to {method} within {timeout} s"}
            code, results = self.answer
            return {"response": code, "results": results}
        finally:
            gio.g_dbus_connection_signal_unsubscribe(self.bus, sub)


def serve():
    portal = Portal()
    print(json.dumps({"ready": True, "app": os.environ.get("FLATPAK_ID"), "name": portal.sender}), flush=True)
    for line in sys.stdin:
        try:
            command = json.loads(line)
            if command["do"] == "request":
                answer = portal.request(command["interface"], command["method"], command["args"],
                                        command.get("timeout", 120))
            elif command["do"] == "read":
                with open(command["path"], encoding="utf-8") as f:
                    answer = {"text": f.read()}
            elif command["do"] == "read_size":
                with open(command["path"], "rb") as f:
                    answer = {"size": len(f.read())}
            elif command["do"] == "write":
                with open(command["path"], "w", encoding="utf-8") as f:
                    answer = {"written": f.write(command["text"])}
            elif command["do"] == "list":
                answer = {"names": sorted(os.listdir(command["path"]))}
            else:
                answer = {"error": "BadCommand", "message": f"Unknown command {command['do']!r}"}
        except OSError as exc:
            answer = {"error": type(exc).__name__, "message": str(exc)}
        except (ValueError, KeyError, RuntimeError) as exc:
            answer = {"error": "BadCommand", "message": str(exc)}
        print(json.dumps(answer), flush=True)


if __name__ == "__main__":
    serve()
