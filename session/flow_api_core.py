"""Policy and data shaping for the Flow Platform API (ADR-0009), independent of any D-Bus binding.

The service asks the bus for a caller's process ID; everything that decides what that caller may
see or do lives here, so it can be tested without a bus.
"""
import configparser
import errno
import hashlib
import os
from pathlib import Path
import time
import unicodedata
import uuid

NAME = "io.github.mattonik.Flow1"
VERSION = (1, 2)
FEATURES = ("spaces.list", "spaces.focus", "spaces.changed", "control.spaces", "control.pin", "control.reorder",
            "system.ui", "activities")
ACTIVITY_LIMIT = 3  # live activities per caller
ACTIVITY_IDLE = 600.0  # an activity without an update for this many seconds ends
TITLE_MAX, DETAIL_MAX = 80, 120
UI = {"desk": "desk_open", "overview": "overview_open", "launcher": "apps_open"}  # System requests → shell IPC
RATE = (20, 10.0)  # calls per caller within this many seconds


class ApiError(Exception):
    """A structured API error; ``kind`` becomes ``io.github.mattonik.Flow1.Error.<kind>``."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def caller(pid, proc=Path("/proc")):
    """Who is calling: a sandboxed Flatpak app (by app ID) or an unsandboxed process (by lineage)."""
    if not isinstance(pid, int) or pid <= 0:
        raise ApiError("NotAuthorized", "The caller could not be identified")
    info = proc / str(pid) / "root" / ".flatpak-info"
    try:
        os.stat(info)
        exists = True
    except FileNotFoundError:
        exists = False
    except OSError as exc:
        # An unreadable sandbox root counts as sandboxed, never as trusted (Path.exists hides EACCES).
        exists = exc.errno != errno.ENOENT
    if exists:
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read_string(info.read_text())
            app = parser.get("Application", "name")
        except (OSError, configparser.Error, UnicodeDecodeError):
            app = None
        if not app:
            raise ApiError("NotAuthorized", "The sandboxed caller has no application ID")
        return {"sandboxed": True, "app_id": app, "lineage": frozenset(), "pid": pid, "name": app}
    lineage, current = set(), pid
    while current and current > 1 and current not in lineage and len(lineage) < 64:
        lineage.add(current)
        try:
            stat = (proc / str(current) / "stat").read_text()
            current = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    try:
        name = (proc / str(pid) / "comm").read_text().strip() or "an app"
    except OSError:
        name = "an app"
    return {"sandboxed": False, "app_id": None, "lineage": frozenset(lineage), "pid": pid, "name": name}


def capabilities(identity):
    """The version and the features this caller can use."""
    features = [f for f in FEATURES if not (identity["sandboxed"] and f.startswith("control."))]
    return (*VERSION, features)


def limit_key(identity, sender):
    """Rate limits follow a Flatpak app across its connections; other callers are limited per connection,
    since they could reach Niri directly anyway."""
    return "app:" + identity["app_id"] if identity["sandboxed"] else sender


def opaque(session, window_id):
    """A session-scoped space ID that is not the raw Niri window ID."""
    return "window:" + hashlib.sha256(f"{session}\0{window_id}".encode()).hexdigest()[:16]


def project(snapshot, session):
    """Every space the snapshot shows, with the window IDs that decide ownership and focus."""
    display = (snapshot.get("display") or {}).get("name") or ""
    pins = snapshot.get("pins") or []
    claimed = {i for pin in pins for i in pin.get("window_ids") or []}
    result = []
    for position, pin in enumerate(pins, 1):
        result.append({"id": "pin:" + pin["id"], "kind": pin.get("kind", "app"), "pinned": True,
                       "position": position, "display": display, "label": pin.get("label", ""),
                       "available": bool(pin.get("available")), "window_ids": list(pin.get("window_ids") or []),
                       "local_pins": len(pins)})  # internal: a pin of the focused display, and how many it has
    offset = len(pins)
    unpinned = [s for s in snapshot.get("spaces") or [] if not {m["id"] for m in s["members"]} & claimed]
    for position, space in enumerate(unpinned, offset + 1):
        ids = [m["id"] for m in space["members"]]
        result.append({"id": "group:" + space["group_id"] if space.get("group_id") else opaque(session, ids[0]),
                       "kind": "group" if space.get("group_id") else "app", "pinned": False,
                       "position": position, "display": display, "label": space.get("label", ""),
                       "available": True, "window_ids": ids})
    numbered = {}
    for pin in snapshot.get("other_pins") or []:
        # Each other display numbers its own pins, in its own order.
        where = pin.get("display_name") or ""
        numbered[where] = numbered.get(where, 0) + 1
        result.append({"id": "pin:" + pin["id"], "kind": pin.get("kind", "app"), "pinned": True,
                       "position": numbered[where], "display": where, "label": pin.get("label", ""),
                       "available": bool(pin.get("available")), "window_ids": list(pin.get("window_ids") or [])})
    windows = {w["id"]: w for w in snapshot.get("all_windows") or []}
    for space in result:
        space["apps"] = sorted({windows[i]["app_id"] for i in space["window_ids"] if i in windows})
    active = (snapshot.get("spaces") and next((s for s in snapshot["spaces"] if s.get("active")), None)) or None
    active_ids = {m["id"] for m in active["members"]} if active else set()
    for space in result:
        space["active"] = bool(active_ids & set(space["window_ids"]))
    return result, windows


def own_window(identity, window):
    if not window:
        return False
    if identity["sandboxed"]:
        return window.get("app_id") == identity["app_id"]
    return window.get("pid") in identity["lineage"]


def owns(identity, space, windows):
    """Whether the space holds one of the caller's own windows."""
    return any(own_window(identity, windows.get(ident)) for ident in space["window_ids"])


def visible(identity, spaces, windows, control):
    """The spaces this caller may see through the interface it called.

    Through Spaces a caller sees only spaces holding its own windows, and only its own part of them:
    a column or Group shared with other apps never reveals their titles or app IDs.
    """
    if control:
        if identity["sandboxed"]:
            raise ApiError("NotAuthorized", "Sandboxed apps may only use their own spaces")
        return spaces
    shown = []
    for space in spaces:
        mine = [windows[i] for i in space["window_ids"] if own_window(identity, windows.get(i))]
        if mine:
            label = " + ".join(w.get("title") or w.get("app_id") or "Application" for w in mine)
            shown.append({**space, "label": label, "apps": sorted({w.get("app_id") or "" for w in mine})})
    return shown


def public(space):
    """The D-Bus dictionary for a space; internal fields stay inside the service."""
    return {key: space[key] for key in ("id", "kind", "pinned", "position", "display", "active",
                                        "available", "label", "apps")}


def focus_target(identity, space_id, spaces, windows, control):
    """The flow-spaces action that focuses an allowed space: ("focus", pin) or ("focus-window", id)."""
    allowed = {s["id"]: s for s in visible(identity, spaces, windows, control)}
    if space_id not in allowed:
        if any(s["id"] == space_id for s in spaces):
            raise ApiError("NotAuthorized", "This space does not belong to the caller")
        raise ApiError("NotFound", "No such space")
    space = allowed[space_id]
    if not control:
        # No focus stealing: an app moves focus only between its own spaces, while it has focus.
        focused = next((w for w in windows.values() if w.get("focused")), None)
        if not own_window(identity, focused):
            raise ApiError("NotAuthorized", "An app may change focus only while one of its own windows has it")
    if space_id.startswith("pin:"):
        return "focus", space_id[4:]
    if not space["window_ids"]:
        raise ApiError("AppNotRunning", "The space has no open window")
    return "focus-window", str(space["window_ids"][0])


def control_space(identity, space_id, spaces):
    """A space for a Control action; sandboxed callers never get here."""
    if identity["sandboxed"]:
        raise ApiError("NotAuthorized", "Sandboxed apps may only use their own spaces")
    space = next((s for s in spaces if s["id"] == space_id), None)
    if not space:
        raise ApiError("NotFound", "No such space")
    return space


def pin_steps(identity, space_id, spaces, windows):
    """The single flow-spaces action that pins a space where it is, without moving focus."""
    space = control_space(identity, space_id, spaces)
    if space["pinned"]:
        raise ApiError("InvalidState", "This space is already pinned")
    if space_id.startswith("group:"):
        return [("pin-group", space_id[6:])]
    if not space["window_ids"]:
        raise ApiError("AppNotRunning", "The space has no open window")
    return [("pin", str(space["window_ids"][0]))]


def unpin_steps(identity, space_id, spaces):
    space = control_space(identity, space_id, spaces)
    if not space["pinned"]:
        raise ApiError("InvalidState", "This space is not pinned")
    return [("unpin", space_id[4:])]


def move_steps(identity, space_id, direction, spaces):
    """Reorder a pin of the focused display; a move past either end is refused, never a silent no-op."""
    space = control_space(identity, space_id, spaces)
    if direction not in ("left", "right"):
        raise ApiError("InvalidArgs", "Move a pinned space left or right")
    if not space["pinned"]:
        raise ApiError("InvalidState", "Only pinned spaces have an order to change")
    if "local_pins" not in space:
        raise ApiError("InvalidState", "This space is pinned on another display; reorder it from that display")
    if space["position"] == (1 if direction == "left" else space["local_pins"]):
        raise ApiError("InvalidState", "This space is already at that end")
    return [(direction, space_id[4:])]


def ui_request(identity, what, windows):
    """The shell IPC call for a System request. Apps may ask for system UI only while they have focus."""
    if what not in UI:
        raise ApiError("InvalidArgs", "Ask for the desk, the overview or the launcher")
    if identity["sandboxed"] and what == "launcher":
        # The launcher takes the keyboard: an app could time it to catch typing meant for itself.
        raise ApiError("NotAuthorized", "Apps may ask for the desk or the overview, not the launcher")
    if identity["sandboxed"]:
        focused = next((w for w in windows.values() if w.get("focused")), None)
        if not own_window(identity, focused):
            raise ApiError("NotAuthorized", "An app may ask for system UI only while one of its own windows has focus")
    return UI[what]


def ui_answer(reply):
    """The shell's answer to a show request: "busy" means other shell UI is open, which a request never closes."""
    if reply.strip() == "busy":
        raise ApiError("InvalidState", "Other shell UI is open; it is never closed by a request")
    if reply.strip() not in ("open", "opened"):
        raise ApiError("Failed", "The Flow shell did not accept the request")


def error_from(message):
    """Map a flow-spaces error message to an API error kind."""
    text = message.lower()
    if "not connected" in text:
        return ApiError("DisplayUnavailable", message)
    if "no longer exists" in text or "no longer available" in text:
        return ApiError("NotFound", message)
    if "unavailable" in text or "no matching installed" in text:
        return ApiError("AppNotRunning", message)
    if "already pinned" in text or "before reordering" in text or "return side-by-side groups" in text \
            or "already at that end" in text:
        return ApiError("InvalidState", message)
    return ApiError("Failed", message)


class RateLimit:
    """At most ``RATE[0]`` calls per caller in any ``RATE[1]`` seconds."""

    def __init__(self, rate=RATE, clock=time.monotonic):
        self.limit, self.window = rate
        self.clock = clock
        self.calls = {}

    def check(self, caller_name):
        now = self.clock()
        # Callers that have been quiet for a whole window are forgotten, so the table stays small.
        self.calls = {name: times for name, times in self.calls.items() if times and now - times[-1] < self.window}
        recent = [t for t in self.calls.get(caller_name, []) if now - t < self.window]
        if len(recent) >= self.limit:
            self.calls[caller_name] = recent
            raise ApiError("RateLimited", "Too many requests; try again shortly")
        recent.append(now)
        self.calls[caller_name] = recent


def clean(text, limit):
    """Text an app shows in the island: printable characters only, on one line, at most ``limit`` long."""
    if not isinstance(text, str):
        raise ApiError("InvalidArgs", "Activity text must be a string")
    kept = "".join(c if unicodedata.category(c)[0] != "C" else " " for c in text)
    kept = " ".join(kept.split())
    return kept[:limit - 1] + "…" if len(kept) > limit else kept


def owner_label(identity, app_name=lambda app_id: None):
    """Who an activity comes from, decided by the service, never by the app."""
    if identity["sandboxed"]:
        return clean(app_name(identity["app_id"]) or identity["app_id"], 60)
    return clean(identity.get("name") or "an app", 60)


def progress_of(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ApiError("InvalidArgs", "Progress must be a number from 0 to 1, or -1 when unknown")
    if value == -1:
        return -1.0
    if not 0 <= value <= 1:
        raise ApiError("InvalidArgs", "Progress must be a number from 0 to 1, or -1 when unknown")
    return float(value)


class ActivityBook:
    """Live activities, each owned by the bus connection that started it (ADR-0010)."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.items = {}

    def start(self, connection, owner, title, options):
        if sum(a["connection"] == connection for a in self.items.values()) >= ACTIVITY_LIMIT:
            raise ApiError("RateLimited", f"A caller may show at most {ACTIVITY_LIMIT} activities at once")
        title = clean(title, TITLE_MAX)
        if not title:
            raise ApiError("InvalidArgs", "An activity needs a title")
        ident = uuid.uuid4().hex[:12]
        activity = {"connection": connection, "owner": owner, "title": title, "detail": "",
                    "progress": -1.0, "updated": self.clock()}
        self.change(activity, options)  # invalid options refuse the start; nothing is added
        self.items[ident] = activity
        return ident

    def own(self, connection, ident):
        activity = self.items.get(ident)
        if not activity or activity["connection"] != connection:
            raise ApiError("NotFound", "No such activity of this caller")
        return activity

    def change(self, activity, changes):
        """Apply changes to a copy and keep them only if every one is valid."""
        updated = dict(activity)
        if "title" in changes:
            title = clean(changes["title"], TITLE_MAX)
            if not title:
                raise ApiError("InvalidArgs", "An activity needs a title")
            updated["title"] = title
        if "detail" in changes:
            updated["detail"] = clean(changes["detail"], DETAIL_MAX)
        if "progress" in changes:
            updated["progress"] = progress_of(changes["progress"])
        updated["updated"] = self.clock()
        activity.update(updated)

    def update(self, connection, ident, changes):
        self.change(self.own(connection, ident), changes)

    def finish(self, connection, ident):
        self.own(connection, ident)
        del self.items[ident]

    def drop_connection(self, connection):
        """The owner went away; True if any of its activities ended."""
        gone = [i for i, a in self.items.items() if a["connection"] == connection]
        for ident in gone:
            del self.items[ident]
        return bool(gone)

    def expire(self):
        """End activities without an update for ACTIVITY_IDLE seconds; True if any ended."""
        now = self.clock()
        stale = [i for i, a in self.items.items() if now - a["updated"] >= ACTIVITY_IDLE]
        for ident in stale:
            del self.items[ident]
        return bool(stale)

    def public(self):
        """What the shell shows, newest first; connections and timestamps stay inside the service."""
        ordered = sorted(self.items.items(), key=lambda item: item[1]["updated"], reverse=True)
        return [{"id": i, "owner": a["owner"], "title": a["title"], "detail": a["detail"], "progress": a["progress"]}
                for i, a in ordered]
