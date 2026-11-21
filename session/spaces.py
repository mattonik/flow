#!/usr/bin/env python3
"""Experimental Flow pinned spaces and Groups for Niri, scoped to the focused display."""
import fcntl
import functools
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
import groups as group_store
import pin_store

MAX_MEMBERS = group_store.MAX_MEMBERS
RESIZE_STEP = 0.1  # share of the Group's width moved per Narrower/Wider step
MIN_SHARE = 0.15  # no member is made narrower than this share of the Group
LAYER_SIZE = ("45%", "60%")  # a desktop Layer is a compact floating window over the space
SHEET_HEIGHT = 0.5  # on a portrait output a Layer is a full-width bottom sheet of this height
valid_members = group_store.valid_members


def command(*args):
    return subprocess.check_output(["niri", "msg", *map(str, args)], text=True, stderr=subprocess.PIPE)


def snapshot():
    workspaces = json.loads(command("-j", "workspaces"))
    windows = json.loads(command("-j", "windows"))
    workspace = next((w for w in workspaces if w["is_focused"]), None)
    if workspace is None:
        raise ValueError("No active workspace")
    return workspace, windows


def display_state():
    """Return Niri's outputs and every workspace, for display ownership."""
    return json.loads(command("-j", "outputs")), json.loads(command("-j", "workspaces"))


def session_id():
    socket_path = os.environ.get("NIRI_SOCKET", "")
    stat = os.stat(socket_path)
    return f"{Path('/proc/sys/kernel/random/boot_id').read_text().strip()}:{socket_path}:{stat.st_ino}:{stat.st_ctime_ns}"


def desktop_file(app_id, dirs):
    # Match only an exact desktop ID; never interpret window titles as commands.
    if not app_id or "/" in app_id or app_id in (".", ".."):
        return None
    filename = app_id if app_id.endswith(".desktop") else app_id + ".desktop"
    return next((p for d in dirs if (p := Path(d) / "applications" / filename).is_file()), None)


@functools.lru_cache(maxsize=None)
def app_name(app_id):
    """The installed app's display name from its exact desktop entry, or None."""
    path = desktop_file(app_id, data_dirs())
    try:
        lines = path.read_text(errors="replace").splitlines() if path else []
    except OSError:
        return None
    section = None
    for line in lines:
        line = line.strip()
        if line.startswith("["):
            section = line
        elif section == "[Desktop Entry]" and line.startswith("Name="):
            return line[5:].strip()[:60] or None
    return None


def data_dirs():
    return (os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share"),
            *os.environ.get("XDG_DATA_DIRS", "/run/current-system/sw/share:/usr/share").split(":"))


def resolve(pin, windows, socket):
    """Never guess which of several same-app windows is the saved space."""
    if pin.get("session") == socket:
        match = next((w for w in windows if w["id"] == pin.get("window_id") and w["app_id"] == pin["app_id"]), None)
        if match:
            return match
    matches = [w for w in windows if w["app_id"] == pin["app_id"]]
    return matches[0] if len(matches) == 1 else None


def position(window):
    return (window or {}).get("layout", {}).get("pos_in_scrolling_layout")


def column_windows(workspace, windows, column):
    """Tiled windows in one column of the workspace, top to bottom."""
    return sorted((w for w in windows if w.get("workspace_id") == workspace["id"] and not w.get("is_floating")
                   and (position(w) or [None])[0] == column), key=lambda w: position(w)[1])


def eligible(window, workspace, windows):
    if not window or not window.get("app_id") or window.get("is_floating"):
        return False
    pos = position(window)
    if not pos or window["workspace_id"] != workspace["id"]:
        return False
    return len(column_windows(workspace, windows, pos[0])) == 1


def split_of(ordered, workspace, windows):
    """Return k when the first k members fill one column in order and the rest follow it alone, in order.

    A stack is k == len(ordered) and side-by-side is k == 1; anything between is a layout change
    that was interrupted part-way. Return None for any other arrangement.
    """
    if not ordered or any(not w or w.get("workspace_id") != workspace["id"] or w.get("is_floating")
                          or not position(w) for w in ordered):
        return None
    column = position(ordered[0])[0]
    stacked = [w["id"] for w in column_windows(workspace, windows, column)]
    k = len(stacked)
    if k > len(ordered) or stacked != [w["id"] for w in ordered[:k]]:
        return None
    for offset, member in enumerate(ordered[k:], 1):
        if [w["id"] for w in column_windows(workspace, windows, column + offset)] != [member["id"]]:
            return None
    return k


SESSION = None  # this Niri session's identity, set by run_locked; Group bindings from other sessions are ignored


def member_candidates(group, app, windows):
    """(bound window, other candidates) for one Group member.

    In the Niri session that bound the Group, the member is its bound window while that window lives.
    If it closed, a window of that app stands in only when it shares a column with another of the
    Group's bound windows, i.e. the user stacked it back into the Group; otherwise the member is closed
    and opening it from the Group binds the new window. A member app the binding does not cover yet
    (after the user chose only some windows) takes the windows stacked with the bound ones, or else
    every window of the app. Without a binding from this session, every window of the app is a
    candidate, and several are a repair case, never a guess.
    """
    bound = group.get("bound")
    windows_of_app = [w for w in windows if w.get("app_id") == app]
    if not (SESSION and isinstance(bound, dict) and bound.get("session") == SESSION
            and isinstance(bound.get("windows"), dict)):
        return None, windows_of_app
    ident = bound["windows"].get(app)
    match = next((w for w in windows_of_app if w.get("id") == ident), None)
    if match:
        return match, []
    by_id = {w.get("id"): w for w in windows}
    columns = {(w.get("workspace_id"), position(w)[0]) for other, i in bound["windows"].items()
               if other != app and other in group["members"] and (w := by_id.get(i)) and w.get("app_id") == other
               and not w.get("is_floating") and position(w)}
    stacked = [w for w in windows_of_app if not w.get("is_floating") and position(w)
               and (w.get("workspace_id"), position(w)[0]) in columns]
    if app in bound["windows"]:
        return None, stacked  # its bound window closed: only a window stacked back in stands in
    return None, stacked or windows_of_app  # never bound here: the Group's own column narrows the choice


def group_members(group, windows):
    """One window per member in member order, or None where a member is missing or ambiguous."""
    found = []
    for app in group["members"]:
        match, others = member_candidates(group, app, windows)
        found.append(match or (others[0] if len(others) == 1 else None))
    return found


def group_of(window, groups, windows):
    """The saved Group this window may belong to, or None.

    A window is in the Group when it is the member's window, or one of the member's candidates while
    the member cannot be told apart: without a binding from this session that is every window of the
    app, so ambiguity stays conservative. A bound member that closed has no candidates.
    """
    for group in groups if window else ():
        if window.get("app_id") in group["members"]:
            match, others = member_candidates(group, window["app_id"], windows)
            # A bound member that closed has no candidates, so other windows of its app stay outside.
            if (match or {}).get("id") == window["id"] or (not match and any(w["id"] == window["id"] for w in others)):
                return group
    return None


def bind_group(group, found, windows):
    """Remember which windows are the Group in this session; False if nothing changed."""
    if not SESSION or not all(found):
        return False
    bound = {"session": SESSION, "windows": {w["app_id"]: w["id"] for w in found}}
    if group.get("bound") == bound:
        return False
    group["bound"] = bound
    return True


def group_windows(pin, workspace, windows, socket, groups=()):
    """Return the saved Group's member windows in saved order when it is intact on this workspace."""
    members = pin.get("members")
    if not valid_members(members):
        return None
    group_id = pin.get("group_id")
    saved = [g for g in groups if g.get("id") == group_id]
    matching_members = [g for g in groups if set(g.get("members", [])) == set(members)]
    if (not isinstance(group_id, str) or len(saved) != 1 or len(matching_members) != 1
            or matching_members[0].get("id") != group_id):
        return None
    group = saved[0]
    order = group["members"]
    ordered = []
    if pin.get("session") == socket:
        ids = pin.get("window_ids", [])
        if len(ids) != len(members):
            return None
        bound = dict(zip(members, ids))
        ordered = [next((w for w in windows if w["id"] == bound.get(app) and w.get("app_id") == app), None)
                   for app in order]
    if not ordered or any(w is None for w in ordered):
        ordered = group_members(group, windows)
    if any(not w or w.get("workspace_id") != workspace["id"] or w.get("is_floating")
           or not position(w) for w in ordered):
        return None
    if group.get("pending_layout"):
        return None
    if group.get("layout", "stack") == "side-by-side":
        if split_of(ordered, workspace, windows) != 1:
            return None
    else:
        column = position(ordered[0])[0]
        # A stack is recognized in any row order; layout changes require the saved order.
        if (any(position(w)[0] != column for w in ordered)
                or len(column_windows(workspace, windows, column)) != len(ordered)):
            return None
    return ordered


def pin_window(pin, workspace, windows, socket, groups=()):
    if "members" in pin:
        members = group_windows(pin, workspace, windows, socket, groups)
        return next((w for w in members if w["app_id"] == pin.get("last_focused_app")), members[0]) if members else None
    window = resolve(pin, windows, socket)
    return window if eligible(window, workspace, windows) else None


def has_side_layout(workspace, windows, groups):
    here = [w for w in windows if w.get("workspace_id") == workspace["id"]]
    return any((g.get("pending_layout") or g.get("layout") == "side-by-side") and
               any(group_of(w, [g], windows) for w in here) for g in groups)


def pinned_apps(pins):
    return {app for pin in pins for app in pin.get("members", [pin.get("app_id")])}


def local_pins(pins, display):
    """Pins of one display, in its order; ``None`` keeps the single-list behavior."""
    return list(pins) if display is None else [p for p in pins if p.get("display") == display]


def reconcile(pins, workspace, windows, socket, groups=()):
    # shortcut: absent or ambiguous apps have no physical placeholder; use compositor-backed placeholders when available.
    resolved = [pin_window(pin, workspace, windows, socket, groups) for pin in pins]
    active = workspace.get("active_window_id")
    # One unavailable pin must not prevent the remaining live pins from keeping their order.
    desired = [w for w in resolved if w]
    if has_side_layout(workspace, windows, groups):
        return
    if all(position(w)[0] == i for i, w in enumerate(desired, 1)):
        return
    try:
        for index, window in enumerate(desired, 1):
            command("action", "focus-window", "--id", window["id"])
            command("action", "move-column-to-index", index)
    finally:
        if active:
            command("action", "focus-window", "--id", active)


def sync_group_pins(pins, groups):
    """Pins follow their Group's label and member order; a different membership is never adopted."""
    for pin in pins:
        matches = [g for g in groups if g.get("id") == pin.get("group_id")]
        members = pin.get("members")
        if (len(matches) != 1 or not isinstance(members, list)
                or not all(isinstance(app, str) for app in members) or set(matches[0]["members"]) != set(members)):
            continue
        group = matches[0]
        if members != group["members"]:
            bound = dict(zip(members, pin.get("window_ids") or []))
            pin["members"] = list(group["members"])
            pin["app_id"] = group["members"][0]
            if len(bound) == len(members):
                pin["window_ids"] = [bound[app] for app in group["members"]]
        pin["label"] = group["label"]


def pin_group(pins, group, workspace, windows, socket, groups, display=None):
    members = group.get("members", [])
    if not valid_members(members):
        raise ValueError("Select a saved Group")
    found = group_members(group, windows)
    candidate = {"group_id": group["id"], "members": members,
                 "window_ids": [w["id"] for w in found if w], "session": socket}
    if any(not w for w in found) or not group_windows(candidate, workspace, windows, socket, groups):
        raise ValueError("Saved Group is unavailable, ambiguous, or no longer one stack")
    if set(members) & pinned_apps(pins):
        raise ValueError("A Group member is already pinned")
    focused = next((w for w in found if w["id"] == workspace.get("active_window_id")), found[0])
    pins.append({"id": uuid.uuid4().hex, "label": group["label"], "app_id": members[0],
                 "window_id": focused["id"], "last_focused_app": focused["app_id"], "members": list(members),
                 "window_ids": [w["id"] for w in found], "group_id": group["id"], "session": socket,
                 "display": display})


def rejoin_plan(group, workspace, windows, pins):
    """Members as single adjacent columns, in saved or exactly reversed order, ready to stack."""
    found = group_members(group, windows)
    if any(w is None for w in found):
        raise ValueError("Group apps are missing or ambiguous")
    if not all(eligible(w, workspace, windows) for w in found):
        raise ValueError("Every Group app must be a single tiled window on this workspace")
    columns = [position(w)[0] for w in found]
    if columns == list(range(columns[0], columns[0] + len(columns))):
        direction = "left"
    elif columns == list(range(columns[0], columns[0] - len(columns), -1)):
        direction = "right"
    else:
        raise ValueError("Place the Group apps in adjacent columns, in saved order, before rejoining")
    right_col = max(columns)
    claims = [pin for pin in pins if set(group["members"]) & set(pin.get("members", [pin.get("app_id")]))]
    if len(claims) > 1 or any(pin.get("group_id") != group["id"] or
                              set(pin.get("members", [])) != set(group["members"]) for pin in claims):
        raise ValueError("Unpin conflicting apps before rejoining")
    if claims and any(w.get("workspace_id") == workspace["id"] and not w.get("is_floating")
                      and (position(w) or [0])[0] > right_col for w in windows):
        raise ValueError("Move the pinned Group apps to the end before rejoining")
    others = pinned_apps([pin for pin in pins if pin not in claims])
    if any(w.get("workspace_id") == workspace["id"] and w.get("app_id") in others
           and (position(w) or [0])[0] > right_col for w in windows):
        raise ValueError("Move other pinned spaces left before rejoining")
    return found, direction


def find_group(groups, group_id):
    matches = [group for group in groups if group.get("id") == group_id]
    if len(matches) != 1:
        raise ValueError("Saved Group no longer exists or is ambiguous")
    return matches[0]


def live_group(groups, group_id, workspace, windows, socket):
    group = find_group(groups, group_id)
    if group.get("pending_layout"):
        raise ValueError("Finish the Group layout change first")
    candidate = {"group_id": group["id"], "members": group["members"], "session": None}
    found = group_windows(candidate, workspace, windows, socket, groups)
    if not found:
        raise ValueError("The Group is unavailable, ambiguous, or not on this workspace")
    return group, found


def group_of_window(groups, window_id, workspace, windows, socket):
    window = next((w for w in windows if str(w.get("id")) == str(window_id)), None)
    group = group_of(window, groups, windows)
    if not group:
        raise ValueError("This window is not a Group member")
    group, found = live_group(groups, group["id"], workspace, windows, socket)
    index = next((i for i, w in enumerate(found) if w["id"] == window["id"]), None)
    if index is None:
        raise ValueError("This window is not a Group member")
    return group, found, index


def update_group_pins(pins, group, found, socket):
    for pin in pins:
        if pin.get("group_id") == group["id"]:
            pin["members"] = list(group["members"])
            pin["app_id"] = group["members"][0]
            pin["session"], pin["window_ids"] = socket, [w["id"] for w in found]
            if pin.get("last_focused_app") not in group["members"]:
                pin["last_focused_app"] = group["members"][0]
            pin["window_id"] = next(w["id"] for w in found if w["app_id"] == pin["last_focused_app"])
    if group.get("last_focused_app") not in group["members"]:
        group.pop("last_focused_app", None)


def measured_ratios(found):
    widths = [w.get("layout", {}).get("tile_size", [0])[0] for w in found]
    return widths if all(isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 for v in widths) else None


def can_resize(shares, index, direction):
    """Whether one Narrower (-1) or Wider (+1) step fits; width is traded with the next app, or the previous."""
    neighbor = index + 1 if index + 1 < len(shares) else index - 1
    step = 100 * RESIZE_STEP
    return min(shares[index] + direction * step, shares[neighbor] - direction * step) >= 100 * MIN_SHARE - 0.5


def settled_ratios(ids, ratios, workspace_id):
    """Wait briefly for clients to commit requested widths; return the windows, or None if they refuse."""
    time.sleep(0.2)
    for attempt in range(11):
        ws, live = snapshot()
        found = [next((w for w in live if w.get("id") == ident), None) for ident in ids]
        if ws["id"] != workspace_id or not all(found):
            raise ValueError("Group changed while resizing; check the windows")
        widths = measured_ratios(found)
        if not widths or all(abs(w / sum(widths) - r) <= 0.05 for w, r in zip(widths, ratios)):
            return found
        if attempt < 10:
            time.sleep(0.1)
    return None


def neighbor_to_add(group, found, workspace, windows, groups, pins):
    """The single standalone window right after the Group, if it may safely join."""
    if len(group["members"]) >= MAX_MEMBERS:
        return None, f"Groups can have up to {MAX_MEMBERS} apps"
    last = max(position(w)[0] for w in found)
    column = column_windows(workspace, windows, last + 1)
    if len(column) != 1 or not eligible(column[0], workspace, windows):
        return None, "Place one standalone app window directly after the Group first"
    window = column[0]
    app = window.get("app_id")
    if sum(w.get("app_id") == app for w in windows) != 1 and not SESSION:
        return None, "That app has several windows; resolve duplicates before adding it"
    if any(app in g["members"] for g in groups) or app in pinned_apps(pins):
        return None, "That app belongs to another Group or pin"
    return window, ""


def run(pins, op, arg, workspace, windows, socket, groups=(), display=None):
    sync_group_pins(pins, groups)
    here = local_pins(pins, display)

    def describe(p):
        return {"id": p["id"], "label": p.get("label", ""), "kind": "group" if "members" in p else "app",
                "group_id": p.get("group_id"), "display": p.get("display"), "size": len(p.get("members") or [1]),
                "available": bool(pin_window(p, workspace, windows, socket, groups))}

    if op == "repair-side-group":
        repair_side_group(arg, workspace, windows, groups, pins)
        return [describe(p) for p in here]
    if op in ("pin", "pin-group", "left", "right", "move-pin-here", "create-group", "stack", "unstack") and \
            has_side_layout(workspace, windows, groups):
        raise ValueError("Return side-by-side Groups to a stack before changing pins or Groups")
    for group in groups:
        candidate = {"group_id": group["id"], "members": group["members"], "session": None}
        found = group_windows(candidate, workspace, windows, socket, groups)
        focused = next((w for w in found or [] if w["id"] == workspace.get("active_window_id")), None)
        if focused and group.get("last_focused_app") != focused["app_id"]:
            group["last_focused_app"] = focused["app_id"]
    for pin in pins:
        if "group_id" in pin:
            found = group_windows(pin, workspace, windows, socket, groups)
            focused = next((w for w in found or [] if w["id"] == workspace.get("active_window_id")), None)
            if focused:
                pin["last_focused_app"] = focused["app_id"]

    def remember_focus(space, member):
        if not space.get("group_id"):
            return
        for group in groups:
            if group["id"] == space["group_id"]:
                group["last_focused_app"] = member["app_id"]
        for pin in pins:
            if pin.get("group_id") == space["group_id"]:
                pin["last_focused_app"] = member["app_id"]

    if op == "rejoin-group":
        group = find_group(groups, arg)
        if group.get("pending_layout") or group.get("layout", "stack") != "stack":
            raise ValueError("Finish the Group layout change before rejoining")
        found, _ = rejoin_plan(group, workspace, windows, pins)
        current_workspace, current_windows = snapshot()
        current, direction = rejoin_plan(group, current_workspace, current_windows, pins)
        if (current_workspace["id"] != workspace["id"]
                or current_workspace.get("active_window_id") != workspace.get("active_window_id")
                or [(w["id"], position(w)) for w in current] != [(w["id"], position(w)) for w in found]):
            raise ValueError("Group or focus changed; try again")
        ids = [w["id"] for w in current]
        for count, member in enumerate(current[1:], 2):
            # shortcut: Niri's targeted action can consume a neighbor after a race; use consume-only by ID when available.
            command("action", "consume-or-expel-window-" + direction, "--id", member["id"])
            if count == len(ids):
                break  # The final check below confirms the last step.
            ws, live = snapshot()
            first = next((w for w in live if w.get("id") == ids[0]), None)
            rest = [next((w for w in live if w.get("id") == ident), None) for ident in ids[count:]]
            if (ws["id"] != workspace["id"] or not first
                    or [w["id"] for w in column_windows(ws, live, position(first)[0])] != ids[:count]
                    or not all(eligible(w, ws, live) for w in rest)):
                raise ValueError("Could not confirm Group rejoin; saved Group remains. Check the windows before retrying")
        workspace, windows = snapshot()
        check = {"group_id": group["id"], "members": group["members"], "session": ""}
        joined = group_windows(check, workspace, windows, socket, groups)
        if not joined or [w["id"] for w in joined] != ids or split_of(joined, workspace, windows) != len(ids):
            raise ValueError("Could not confirm Group rejoin; saved Group remains. Check the windows before retrying")
        update_group_pins(pins, group, joined, socket)
    elif op == "open-group-member":
        group = find_group(groups, arg)
        members = group["members"]
        current_workspace, current_windows = snapshot()
        if current_workspace["id"] != workspace["id"]:
            raise ValueError("Workspace changed; Group was not opened")
        candidates = [[match] if match else others
                      for match, others in (member_candidates(group, app, current_windows) for app in members)]
        known = {w["id"] for w in current_windows}
        if any(len(found) > 1 for found in candidates):
            raise ValueError("Group has multiple windows for an app; resolve duplicates before retrying")
        if any(found and found[0].get("workspace_id") != workspace["id"] for found in candidates):
            raise ValueError("A Group app is on another workspace; move it back before retrying")
        missing = [app for app, found in zip(members, candidates) if not found]
        if not missing:
            raise ValueError("All Group apps are open; stack them to rejoin the Group")
        app = missing[0]
        desktop = desktop_file(app, data_dirs())
        if not desktop:
            raise ValueError("No matching installed application for the missing Group app")
        try:
            subprocess.run(["gio", "launch", str(desktop)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError("Could not open the missing Group app") from exc
        for _ in range(30):
            time.sleep(0.5)
            current_workspace, current_windows = snapshot()
            if current_workspace["id"] != workspace["id"]:
                raise ValueError("Workspace changed while opening the Group app; check before retrying")
            found = [w for w in current_windows if w.get("app_id") == app and w["id"] not in known]
            if len(found) > 1:
                raise ValueError("Multiple windows opened; resolve duplicates before retrying")
            if found:
                if found[0].get("workspace_id") != workspace["id"]:
                    raise ValueError("The Group app opened on another workspace; move it back before retrying")
                if isinstance(group.get("bound"), dict) and group["bound"].get("session") == SESSION:
                    group["bound"]["windows"][app] = found[0]["id"]
                break
        else:
            raise ValueError("The Group app did not appear; it may still open later, so check before retrying")
    elif op in ("create-group", "dissolve-group"):
        active = next((space for space in display_spaces(workspace, windows, groups) if space["active"]), None)
        if not active or not 2 <= len(active["members"]) <= MAX_MEMBERS:
            raise ValueError(f"Select a stack of 2 to {MAX_MEMBERS} apps first")
        apps = [w["app_id"] for w in active["members"]]
        if not all(apps) or len(set(apps)) != len(apps):
            raise ValueError("Group members must be distinct apps")
        if not SESSION and any(sum(w.get("app_id") == app for w in windows) != 1 for app in apps):
            raise ValueError("Group members must be distinct, unambiguous apps")
        if set(apps) & pinned_apps(pins):
            raise ValueError("Unpin these apps before changing their Group")
        if op == "create-group":
            if any(set(apps) & set(g["members"]) for g in groups):
                raise ValueError("An app already belongs to a saved Group; stack its members to recover it")
        elif not arg or active["group_id"] != arg or sum(g["id"] == arg for g in groups) != 1:
            raise ValueError("Select the active saved Group first")
        current_workspace, current_windows = snapshot()
        current = next((space for space in display_spaces(current_workspace, current_windows, groups) if space["active"]), None)
        if (current_workspace["id"] != workspace["id"]
                or current_workspace.get("active_window_id") != workspace.get("active_window_id")
                or not current or current["group_id"] != active["group_id"]
                or {w["id"] for w in current["members"]} != {w["id"] for w in active["members"]}):
            raise ValueError("Group or focus changed; try again")
        if op == "create-group":
            created = {"id": uuid.uuid4().hex, "label": " + ".join(w["label"] for w in active["members"]), "members": apps}
            bind_group(created, [next(w for w in windows if w["id"] == m["id"]) for m in active["members"]], windows)
            groups.append(created)
        elif active.get("group_layout") == "side-by-side":
            groups.remove(next(g for g in groups if g["id"] == arg))
        else:
            if has_side_layout(current_workspace, current_windows, groups):
                raise ValueError("Return side-by-side Groups to a stack before changing Groups")
            stacked = [next(w for w in current_windows if w["id"] == member["id"]) for member in current["members"]]
            column = position(stacked[0])[0]
            if any(w.get("workspace_id") == workspace["id"] and not w.get("is_floating") and w.get("app_id") in pinned_apps(pins)
                   and (position(w) or [0])[0] > column for w in current_windows):
                raise ValueError("Move pinned spaces left before dissolving this Group")
            stacked.sort(key=lambda w: position(w)[1])
            # Expel from the bottom so the separated windows keep the stack's top-to-bottom order.
            for member in reversed(stacked[1:]):
                # shortcut: Niri's targeted action can consume a neighbor after a race; use expel-only by ID when available.
                command("action", "consume-or-expel-window-right", "--id", member["id"])
            changed_workspace, changed_windows = snapshot()
            separated = [next((w for w in changed_windows if w.get("id") == member["id"]), None) for member in stacked]
            if (changed_workspace["id"] != workspace["id"]
                    or not all(eligible(w, changed_workspace, changed_windows) for w in separated)
                    or {w.get("app_id") for w in separated} != set(apps)):
                raise ValueError("Could not confirm separation; Group remains saved. Stack its apps and retry")
            groups.remove(next(g for g in groups if g["id"] == arg))
    elif op == "add-to-group":
        group, found = live_group(groups, arg, workspace, windows, socket)
        neighbor, problem = neighbor_to_add(group, found, workspace, windows, groups, pins)
        if not neighbor:
            raise ValueError(problem)
        if group.get("layout") == "side-by-side":
            joined = found + [neighbor]
            group_store.remap_ratios(group, group["members"] + [neighbor["app_id"]])
            if measured_ratios(joined):
                group_store.set_ratios(group, measured_ratios(joined))
        else:
            ws, live = snapshot()
            _, fresh = live_group(groups, arg, ws, live, socket)
            again, _ = neighbor_to_add(group, fresh, ws, live, groups, pins)
            if (ws["id"] != workspace["id"] or ws.get("active_window_id") != workspace.get("active_window_id")
                    or [w["id"] for w in fresh] != [w["id"] for w in found] or not again or again["id"] != neighbor["id"]):
                raise ValueError("Group or focus changed; try again")
            # The standalone window joins the bottom of the Group's column.
            command("action", "consume-or-expel-window-left", "--id", neighbor["id"])
            ws, live = snapshot()
            joined = [next((w for w in live if w.get("id") == w0["id"]), None) for w0 in found + [neighbor]]
            if ws["id"] != workspace["id"] or not all(joined) or split_of(joined, ws, live) != len(joined):
                raise ValueError("Could not confirm the new Group member; the Group was not changed. Check the windows")
            group_store.remap_ratios(group, group["members"] + [neighbor["app_id"]])
        bind_group(group, joined, windows)
        update_group_pins(pins, group, joined, socket)
    elif op == "remove-from-group":
        group, found, index = group_of_window(groups, arg, workspace, windows, socket)
        if len(found) <= 2:
            raise ValueError("A Group needs at least two apps; dissolve it instead")
        removed = found[index]
        remaining = found[:index] + found[index + 1:]
        side = group.get("layout") == "side-by-side"
        pinned = any(pin.get("group_id") == group["id"] for pin in pins)
        if side and pinned:
            raise ValueError("Stack the pinned Group before removing an app")
        ws, live = snapshot()
        _, fresh = live_group(groups, group["id"], ws, live, socket)
        if ws["id"] != workspace["id"] or [(w["id"], position(w)) for w in fresh] != [(w["id"], position(w)) for w in found]:
            raise ValueError("Group changed; try again")
        if not side:
            command("action", "consume-or-expel-window-right", "--id", removed["id"])
        elif index < len(found) - 1:
            command("action", "focus-window", "--id", removed["id"])
            ws, live = snapshot()
            _, fresh = live_group(groups, group["id"], ws, live, socket)
            if (ws["id"] != workspace["id"] or ws.get("active_window_id") != removed["id"]
                    or [(w["id"], position(w)) for w in fresh] != [(w["id"], position(w)) for w in found]):
                raise ValueError("Group or focus changed; try again")
            command("action", "move-column-to-index", position(found[-1])[0])
        ws, live = snapshot()
        kept = [next((w for w in live if w.get("id") == w0["id"]), None) for w0 in remaining]
        out = next((w for w in live if w.get("id") == removed["id"]), None)
        expected = len(kept) if not side else 1
        if (ws["id"] != workspace["id"] or not all(kept) or split_of(kept, ws, live) != expected
                or not eligible(out, ws, live)
                or (side and position(out)[0] != position(kept[-1])[0] + 1)):
            raise ValueError("Could not confirm the app left the Group; the Group was not changed. Check the windows")
        group_store.remap_ratios(group, [w["app_id"] for w in remaining])
        if side and measured_ratios(kept):
            group_store.set_ratios(group, measured_ratios(kept))
        update_group_pins(pins, group, kept, socket)
    elif op in ("member-left", "member-right"):
        group, found, index = group_of_window(groups, arg, workspace, windows, socket)
        target = index + (-1 if op == "member-left" else 1)
        if not 0 <= target < len(found):
            raise ValueError("This app is already at that end of the Group")
        side = group.get("layout") == "side-by-side"
        if not side and split_of(found, workspace, windows) != len(found):
            raise ValueError("Keep or restore the Group order before moving apps")
        moved = found[:]
        moved[index], moved[target] = moved[target], moved[index]
        member = found[index]
        command("action", "focus-window", "--id", member["id"])
        ws, live = snapshot()
        _, fresh = live_group(groups, group["id"], ws, live, socket)
        if (ws["id"] != workspace["id"] or ws.get("active_window_id") != member["id"]
                or [(w["id"], position(w)) for w in fresh] != [(w["id"], position(w)) for w in found]):
            raise ValueError("Group or focus changed; try again")
        if side:
            command("action", "move-column-" + ("left" if target < index else "right"))
        else:
            command("action", "move-window-" + ("up" if target < index else "down"))
        ws, live = snapshot()
        live_moved = [next((w for w in live if w.get("id") == w0["id"]), None) for w0 in moved]
        if ws["id"] != workspace["id"] or not all(live_moved) or split_of(live_moved, ws, live) != (1 if side else len(moved)):
            raise ValueError("Member move interrupted; check the Group and keep its current order if needed")
        group_store.remap_ratios(group, [w["app_id"] for w in moved])
        update_group_pins(pins, group, live_moved, socket)
        remember_focus({"group_id": group["id"]}, member)
    elif op in ("member-wider", "member-narrower"):
        group, found, index = group_of_window(groups, arg, workspace, windows, socket)
        if group.get("layout") != "side-by-side":
            raise ValueError("Only apps side by side can be resized")
        measured = measured_ratios(found)
        values = [w / sum(measured) for w in measured] if measured else group_store.ratios(group)
        # Width comes from the next app, or the previous one for the last app.
        neighbor = index + 1 if index + 1 < len(found) else index - 1
        step = RESIZE_STEP if op == "member-wider" else -RESIZE_STEP
        values[index], values[neighbor] = values[index] + step, values[neighbor] - step
        if min(values[index], values[neighbor]) < MIN_SHARE - 1e-6:
            raise ValueError("That app is already as wide as it can be" if step > 0
                             else "That app is already as narrow as it can be")
        for member in (found[index], found[neighbor]):
            command("action", "set-window-width", "--id", member["id"],
                    f"{round(values[found.index(member)] * 100, 2)}%")
        settled = settled_ratios([w["id"] for w in found], values, workspace["id"])
        if not settled:
            actual = measured_ratios([next(w for w in snapshot()[1] if w["id"] == m["id"]) for m in found])
            if actual:
                group_store.set_ratios(group, actual)
            raise ValueError("An app's size limits prevent that width; the Group keeps the closest size")
        group_store.set_ratios(group, values)
        remember_focus({"group_id": group["id"]}, found[index])
    elif op == "make-layer":
        window = next((w for w in windows if w["id"] == workspace.get("active_window_id")), None)
        if not window or window.get("workspace_id") != workspace["id"]:
            raise ValueError("Select an app window first")
        if window.get("is_floating"):
            raise ValueError("This app is already a Layer")
        if group_of(window, groups, windows):
            raise ValueError("Remove this app from its Group before making it a Layer")
        if window.get("app_id") in pinned_apps(pins):
            raise ValueError("Unpin this app before making it a Layer")
        if not eligible(window, workspace, windows):
            raise ValueError("Separate this app from its stack before making it a Layer")
        float_as_layer(window["id"], layer_output(workspace))
    elif op == "open-layer":
        desktop = desktop_file(arg, data_dirs())
        if not desktop:
            raise ValueError("No matching installed application")
        expected = {desktop.stem}
        for line in desktop.read_text(errors="replace").splitlines():
            if line.startswith("StartupWMClass="):
                expected.add(line.split("=", 1)[1].strip())
        known = {w["id"] for w in windows}
        try:
            subprocess.run(["gio", "launch", str(desktop)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError("Could not open the application") from exc
        for _ in range(30):
            time.sleep(0.5)
            current, live = snapshot()
            if current["id"] != workspace["id"]:
                raise ValueError("Workspace changed while opening the Layer")
            new = [w for w in live if w["id"] not in known and w.get("app_id") in expected]
            if new:
                break
        else:
            raise ValueError("The app did not open a new window here; it may already be running elsewhere")
        if len(new) != 1 or new[0].get("workspace_id") != workspace["id"]:
            raise ValueError("The app opened more than one window or opened elsewhere; it was left as it is")
        if not new[0].get("is_floating"):
            # Niri reactivates the column the window was opened from, so closing the Layer returns there.
            float_as_layer(new[0]["id"], layer_output(workspace))
    elif op == "layer-to-space":
        window = layer_window(arg, workspace, windows)
        command("action", "move-window-to-tiling", "--id", window["id"])
        # A space is a full-width column, as Flow opens apps; the Layer's compact size does not carry over.
        command("action", "set-window-width", "--id", window["id"], "100%")
        command("action", "reset-window-height", "--id", window["id"])
        command("action", "focus-window", "--id", window["id"])
    elif op == "close-layer":
        window = layer_window(arg, workspace, windows)
        command("action", "close-window", "--id", window["id"])
    elif op == "choose-member":
        group_id, _, ident = (arg or "").partition(":")
        group = find_group(groups, group_id)
        window = next((w for w in windows if str(w.get("id")) == ident), None)
        if not window or window.get("app_id") not in group["members"]:
            raise ValueError("That window is not one of this Group's apps")
        match, others = member_candidates(group, window["app_id"], windows)
        if match or len(others) < 2 or window not in others:
            raise ValueError("This Group app is not ambiguous; nothing to choose")
        if window.get("workspace_id") != workspace["id"]:
            raise ValueError("Choose a window on this workspace")
        if not SESSION:
            raise ValueError("Cannot identify this Niri session; the window was not chosen")
        known = group_members(group, windows)  # members that are already unambiguous stay as they are
        bound = {w["app_id"]: w["id"] for w in known if w}
        bound[window["app_id"]] = window["id"]
        group["bound"] = {"session": SESSION, "windows": bound}
    elif op == "adopt-group-order":
        group = find_group(groups, arg)
        if group.get("pending_layout"):
            raise ValueError("Finish the Group layout change first")
        found = live_order(group, workspace, windows, pins, groups)
        if [w["app_id"] for w in found] == group["members"]:
            raise ValueError("The Group already has this order")
        group_store.remap_ratios(group, [w["app_id"] for w in found])
        update_group_pins(pins, group, found, socket)
    elif op == "pin":
        window = next((w for w in windows if w["id"] == workspace.get("active_window_id")), None)
        if not window:
            raise ValueError("Select one non-floating app window to pin")
        if window.get("is_floating"):
            raise ValueError("Select one non-floating app window to pin")
        pos = position(window)
        stack = column_windows(workspace, windows, pos[0]) if pos and window.get("workspace_id") == workspace["id"] else []
        if len(stack) > 1:
            apps = [w.get("app_id") for w in stack]
            matches = [g for g in groups if set(g.get("members", [])) == set(apps)]
            if len(matches) != 1:
                raise ValueError("Create a Group from this stack before pinning it")
            pin_group(pins, matches[0], workspace, windows, socket, groups, display)
            window = None
        if window:
            if not eligible(window, workspace, windows):
                raise ValueError("Select one non-floating app window to pin")
            if window["app_id"] in pinned_apps(pins):
                raise ValueError("This app is already pinned")
            pins.append({"id": uuid.uuid4().hex, "label": window.get("title") or window["app_id"],
                         "app_id": window["app_id"], "window_id": window["id"], "session": socket,
                         "display": display})
    elif op == "pin-group":
        matches = [g for g in groups if g.get("id") == arg]
        if len(matches) != 1:
            raise ValueError("Select a saved Group")
        pin_group(pins, matches[0], workspace, windows, socket, groups, display)
    elif op == "group-view":
        active = next((space for space in display_spaces(workspace, windows, groups) if space["active"]), None)
        if not arg or not active or active["group_id"] != arg:
            raise ValueError("Select the active saved Group first")
        current_workspace, current_windows = snapshot()
        current = next((space for space in display_spaces(current_workspace, current_windows, groups) if space["active"]), None)
        if (current_workspace["id"] != workspace["id"]
                or current_workspace.get("active_window_id") != workspace.get("active_window_id")
                or not current or current["group_id"] != arg
                or {w["id"] for w in current["members"]} != {w["id"] for w in active["members"]}):
            raise ValueError("Group or focus changed; try again")
        # shortcut: Niri toggles only the focused column; use a targeted action if it becomes available.
        command("action", "toggle-column-tabbed-display")
    elif op == "move-pin-here":
        if display is None:
            raise ValueError("This display cannot be identified; pins were not moved")
        pin_store.move_here(pins, arg, display)
    elif op in ("unpin", "focus", "left", "right"):
        index = next((i for i, p in enumerate(pins) if p["id"] == arg), None)
        if index is None:
            raise ValueError("Pinned space no longer exists")
        if op == "unpin":
            pins.pop(index)
        elif op == "focus":
            pin = pins[index]
            window = pin_window(pin, workspace, windows, socket, groups)
            if "members" in pin:
                if not window:
                    raise ValueError("This pinned Group is unavailable or no longer one stack")
                found = group_windows(pin, workspace, windows, socket, groups)
                pin["session"], pin["window_id"], pin["window_ids"] = socket, window["id"], [w["id"] for w in found]
                command("action", "focus-window", "--id", window["id"])
                command("action", "close-overview")
            elif not window and not any(w.get("app_id") == pin["app_id"] for w in windows):
                desktop = desktop_file(pin["app_id"], data_dirs())
                if not desktop:
                    raise ValueError("No matching installed application for this pin")
                try:
                    subprocess.run(["gio", "launch", str(desktop)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except (OSError, subprocess.SubprocessError) as exc:
                    raise ValueError("Could not open the pinned application") from exc
                workspace_id = workspace["id"]
                for _ in range(30):
                    time.sleep(0.5)
                    workspace, windows = snapshot()
                    if workspace["id"] != workspace_id:
                        raise ValueError("Workspace changed while opening pinned app")
                    if any(w.get("app_id") == pin["app_id"] for w in windows):
                        break
                window = resolve(pin, windows, socket)
            if "members" not in pin and not eligible(window, workspace, windows):
                raise ValueError("This pinned app is unavailable or ambiguous")
            if "members" not in pin:
                pin["session"], pin["window_id"] = socket, window["id"]
                command("action", "focus-window", "--id", window["id"])
                command("action", "close-overview")
        else:
            if display is not None and pins[index].get("display") != display:
                raise ValueError("Move this pin to this display before reordering it")
            pin_store.swap(pins, arg, op, display)
    elif op in ("stack", "unstack"):
        active = next((w for w in windows if w["id"] == workspace.get("active_window_id")), None)
        pos = active and position(active)
        if not pos or active.get("is_floating") or active.get("workspace_id") != workspace["id"]:
            raise ValueError("Select a tiled window first")
        current = column_windows(workspace, windows, pos[0])
        stack = current + column_windows(workspace, windows, pos[0] + 1) if op == "stack" else current
        if op == "stack" and (len(current) >= MAX_MEMBERS or len(stack) != len(current) + 1):
            raise ValueError(f"Select a column of up to {MAX_MEMBERS - 1} windows with one single window to its right")
        if op == "unstack" and len(stack) < 2:
            raise ValueError("Select a stack of windows")
        if any(group_of(w, groups, windows) for w in stack):
            raise ValueError("Use Dissolve Group or the Group's member controls to change a saved Group")
        if any(w.get("app_id") in pinned_apps(pins) for w in stack):
            raise ValueError("Unpin these windows before stacking or separating")
        command("action", "consume-window-into-column" if op == "stack" else "expel-window-from-column")
    elif op == "navigate":
        if arg not in ("left", "right"):
            raise ValueError("Choose left or right")
        focused = next((w for w in windows if w["id"] == workspace.get("active_window_id")), None)
        layer = focused if focused and focused.get("is_floating") else None
        if layer:
            # From a Layer, move from the space beneath it: Niri remembers that column.
            command("action", "focus-tiling")
            workspace, windows = snapshot()
        destinations = display_spaces(workspace, windows, groups, pins)
        index = next((i for i, space in enumerate(destinations) if space["active"]), None)
        target = index + (-1 if arg == "left" else 1) if index is not None else None
        if layer and (target is None or not 0 <= target < len(destinations)):
            command("action", "focus-window", "--id", layer["id"])  # nowhere to go: keep the Layer
        if index is not None:
            if 0 <= target < len(destinations):
                selected = destinations[target]
                pin = next((p for p in pins if p.get("group_id") and p["group_id"] == selected.get("group_id")), None)
                member = next((w for w in selected["members"] if pin and w["app_id"] == pin.get("last_focused_app")), None)
                member = member or next(w for w in selected["members"] if w["id"] == selected["id"])
                command("action", "focus-window", "--id", member["id"])
                remember_focus(selected, member)
    elif op == "focus-member":
        if arg not in ("up", "down"):
            raise ValueError("Choose up or down")
        current = next((space for space in display_spaces(workspace, windows, groups, pins) if space["active"]), None)
        if current and current.get("group_layout") == "side-by-side":
            index = next(i for i, member in enumerate(current["members"]) if member["active"])
            target = index + (-1 if arg == "up" else 1)
            if 0 <= target < len(current["members"]):
                member = current["members"][target]
                command("action", "focus-window", "--id", member["id"])
                remember_focus(current, member)
        else:
            command("action", "focus-window-" + arg)
    elif op == "focus-window":
        window = next((w for w in windows if str(w["id"]) == arg and w["workspace_id"] == workspace["id"]), None)
        if not window:
            raise ValueError("This window is no longer available")
        command("action", "focus-window", "--id", window["id"])
        space = next((s for s in display_spaces(workspace, windows, groups, pins)
                      if s.get("group_id") and any(w["id"] == window["id"] for w in s["members"])), None)
        if space:
            member = next(w for w in space["members"] if w["id"] == window["id"])
            remember_focus(space, member)
    elif op != "refresh":
        raise ValueError("Unknown action")
    here = local_pins(pins, display)
    if op not in ("focus", "focus-window", "focus-member", "navigate", "stack", "unstack", "group-view", "create-group",
                  "dissolve-group", "open-group-member", "rejoin-group", "add-to-group", "remove-from-group",
                  "member-left", "member-right", "adopt-group-order", "member-wider", "member-narrower",
                  "make-layer", "open-layer", "layer-to-space", "close-layer", "choose-member"):
        reconcile(here, workspace, windows, socket, groups)
    return [describe(p) for p in here]


def live_order(group, workspace, windows, pins, groups):
    """Members in their current on-screen order, when the layout is otherwise intact."""
    found = group_members(group, windows)
    if any(w is None or w.get("workspace_id") != workspace["id"] or w.get("is_floating") or not position(w)
           for w in found):
        raise ValueError("Group apps are missing, ambiguous, or on another workspace")
    if group.get("layout", "stack") == "side-by-side":
        ordered = sorted(found, key=lambda w: position(w)[0])
        if split_of(ordered, workspace, windows) != 1:
            raise ValueError("Group apps must be single windows in adjacent columns")
    else:
        ordered = sorted(found, key=lambda w: position(w)[1])
        if split_of(ordered, workspace, windows) != len(ordered):
            raise ValueError("Group apps must be the only windows in one stack")
    return ordered


def saved_groups(path):
    try:
        data = json.loads(path.read_text())
        groups = data.get("groups", []) if isinstance(data, dict) else []
        if not isinstance(groups, list):
            return []
        return [g for g in groups if isinstance(g, dict) and isinstance(g.get("id"), str)
                and isinstance(g.get("label"), str) and valid_members(g.get("members"))]
    except (OSError, ValueError):
        return []


def layers(workspace, windows):
    """Floating windows on the workspace: Layers over its spaces, never spaces themselves."""
    return [{"id": w["id"], "label": w.get("title") or w.get("app_id") or "Application",
             "app_id": w.get("app_id") or "", "app_name": app_name(w.get("app_id")),
             "active": w["id"] == workspace.get("active_window_id")}
            for w in windows if w.get("workspace_id") == workspace["id"] and w.get("is_floating")]


def layer_output(workspace):
    """The logical size of the workspace's output, or None when Niri does not report it."""
    try:
        logical = (display_state()[0].get(workspace.get("output")) or {}).get("logical") or {}
    except (OSError, ValueError, subprocess.SubprocessError, AttributeError):
        return None
    size = logical.get("width"), logical.get("height")
    return size if all(isinstance(v, int) and v > 0 for v in size) else None


def float_as_layer(window_id, size=None):
    """Lift one window out of the strip; the columns around it close up and keep their sizes.

    In landscape the Layer is a compact centered window; in portrait it is a bottom sheet across
    the output, leaving the top of the space in view.
    """
    command("action", "move-window-to-floating", "--id", window_id)
    if size and size[1] > size[0]:
        command("action", "set-window-width", "--id", window_id, "100%")
        command("action", "set-window-height", "--id", window_id, f"{round(SHEET_HEIGHT * 100)}%")
        command("action", "move-floating-window", "--id", window_id, "-x", "0",
                "-y", str(size[1] - round(size[1] * SHEET_HEIGHT)))
    else:
        command("action", "set-window-width", "--id", window_id, LAYER_SIZE[0])
        command("action", "set-window-height", "--id", window_id, LAYER_SIZE[1])
        # Niri centers by the window's current size, so wait for the client to commit the new one.
        # Floating sizes are relative to the working area, so any change of height counts as committed.
        def height():
            return next((w.get("layout", {}).get("tile_size", [0, 0])[1] for w in snapshot()[1]
                         if w.get("id") == window_id), None)

        expected = size[1] * float(LAYER_SIZE[1].rstrip("%")) / 100 if size else None
        initial = height() if size else None
        for _ in range(10):
            time.sleep(0.1)
            if expected is None:
                break
            current = height()
            if current is None or current != initial or abs(current - expected) <= 16:
                break
        command("action", "center-window", "--id", window_id)
    command("action", "focus-window", "--id", window_id)


def layer_window(arg, workspace, windows):
    window = next((w for w in windows if str(w.get("id")) == str(arg) and w.get("workspace_id") == workspace["id"]
                   and w.get("is_floating")), None)
    if not window:
        raise ValueError("This Layer is no longer open here")
    return window


def display_spaces(workspace, windows, groups=(), pins=()):
    """Represent each tiled column once; floating windows are Layers, not spaces."""
    current = [w for w in windows if w.get("workspace_id") == workspace["id"]
               and not w.get("is_floating") and position(w)]
    current.sort(key=lambda w: position(w) or [99999, 0])
    columns = []
    for w in current:
        pos = position(w)
        key = ("column", pos[0])
        if not columns or columns[-1][0] != key:
            columns.append((key, []))
        columns[-1][1].append({"id": w["id"], "label": w.get("title") or w.get("app_id") or "Application",
                              "app_id": w.get("app_id") or "", "app_name": app_name(w.get("app_id")),
                              "active": w["id"] == workspace.get("active_window_id")})
    result = []
    skip = set()
    for index, (_, members) in enumerate(columns):
        if index in skip:
            continue
        for group in groups:
            if group.get("layout") != "side-by-side" or group.get("pending_layout"):
                continue
            candidate = {"group_id": group["id"], "members": group["members"], "session": None}
            found = group_windows(candidate, workspace, windows, "", groups)
            n = len(found or [])
            if (found and members[0]["id"] == found[0]["id"] and index + n <= len(columns)
                    and all(columns[index + i][1][0]["id"] == found[i]["id"] for i in range(1, n))):
                joined = [m for i in range(n) for m in columns[index + i][1]]
                pin = next((p for p in pins if p.get("group_id") == group["id"]), {})
                last = pin.get("last_focused_app") or group.get("last_focused_app")
                selected = next((w for w in joined if w["active"]),
                                next((w for w in joined if w["app_id"] == last), joined[0]))
                widths = measured_ratios(found)
                shares = [round(100 * w / sum(widths)) for w in widths] if widths else \
                    [round(100 * r) for r in group_store.ratios(group)]
                result.append({"id": selected["id"], "label": group["label"], "group_id": group["id"],
                               "group_layout": "side-by-side", "auto_layout": group.get("auto_layout", False),
                               "active": any(w["active"] for w in joined), "members": joined,
                               "shares": shares, "can_widen": [can_resize(shares, i, 1) for i in range(n)],
                               "can_narrow": [can_resize(shares, i, -1) for i in range(n)]})
                skip.update(range(index + 1, index + n))
                break
        else:
            group = None
        if group:
            continue
        selected = next((w for w in members if w["active"]), members[0])
        app_ids = [w["app_id"] for w in members]
        matches = [g for g in groups if g.get("layout", "stack") == "stack" and not g.get("pending_layout")
                   and valid_members(app_ids) and set(g["members"]) == set(app_ids)
                   and {w["id"] for w in group_members(g, windows) if w} == {m["id"] for m in members}]
        group = matches[0] if len(matches) == 1 and sum(g["id"] == matches[0]["id"] for g in groups) == 1 else None
        if group and not any(w["active"] for w in members):
            pin = next((p for p in pins if p.get("group_id") == group["id"]), {})
            last = pin.get("last_focused_app") or group.get("last_focused_app")
            selected = next((w for w in members if w["app_id"] == last), selected)
        space = {"id": selected["id"], "label": group["label"] if group else " + ".join(w["label"] for w in members),
                 "group_id": group["id"] if group else None, "group_layout": "stack" if group else None,
                 "auto_layout": group.get("auto_layout", False) if group else False,
                 "active": any(w["active"] for w in members), "members": members}
        if group:
            space["order_changed"] = [w["app_id"] for w in members] != group["members"]
        result.append(space)
    for space in result:
        if space.get("group_id"):
            group = next(g for g in groups if g["id"] == space["group_id"])
            found = [next(w for w in windows if w["id"] == m["id"]) for m in space["members"]]
            neighbor, _ = neighbor_to_add(group, found, workspace, windows, groups, pins)
            space["can_add"] = bool(neighbor)
            space["add_label"] = (neighbor.get("title") or neighbor.get("app_id")) if neighbor else ""
            space["can_remove"] = len(space["members"]) > 2 and not (
                space["group_layout"] == "side-by-side" and any(p.get("group_id") == group["id"] for p in pins))
    return result


def incomplete_groups(workspace, windows, groups, pins):
    """Describe retained Groups without guessing missing window identities."""
    workspace_ids = {w.get("workspace_id") for w in windows if w.get("workspace_id") is not None}
    visible = {space["group_id"] for ident in workspace_ids
               for space in display_spaces({"id": ident}, windows, groups)}
    result = []
    for group in groups:
        if group["id"] in visible:
            continue
        candidates = [[match] if match else others
                      for match, others in (member_candidates(group, app, windows) for app in group["members"])]
        missing = [app for app, found in zip(group["members"], candidates) if not found]
        can_rejoin = can_repair = can_adopt = False
        can_resume = bool(group.get("pending_layout")) and all(
            len(found) == 1 and found[0].get("workspace_id") == workspace["id"] for found in candidates)
        if group.get("pending_layout"):
            status = "Layout change interrupted; finish it when every Group app is available."
        elif any(len(found) > 1 for found in candidates):
            status = ("Several windows match a Group app; choose the ones that belong to it."
                      if any(len(found) > 1 and any(w.get("workspace_id") == workspace["id"] for w in found)
                             for found in candidates)
                      else "Several windows of a Group app are on another workspace; choose them there.")
        elif any(found and found[0].get("workspace_id") != workspace["id"] for found in candidates):
            status = "A Group app is on another workspace."
        elif missing:
            status = ("Every Group app is closed." if len(missing) == len(group["members"])
                      else "A Group app is closed." if len(missing) == 1
                      else f"{len(missing)} Group apps are closed.")
        elif group.get("layout") == "side-by-side":
            try:
                _, columns = side_group_repair_plan(group, workspace, windows, groups, pins)
                if columns == list(range(columns[0], columns[0] + len(columns))):
                    raise ValueError("Group members are already adjacent")
                can_repair = True
                status = "Group members are separated by a safe standalone window; repair their order."
            except ValueError as exc:
                status = str(exc) + "."
                try:
                    live_order(group, workspace, windows, pins, groups)
                    can_adopt = True
                    status = "Group apps were moved into a new order; keep it or move them back."
                except ValueError:
                    pass
        else:
            try:
                rejoin_plan(group, workspace, windows, pins)
                can_rejoin = True
                status = "Every Group app is open; rejoin them as a Group."
            except ValueError as exc:
                status = str(exc) + "."
        can_open = bool(missing) and not any(len(found) > 1 or
                       (found and found[0].get("workspace_id") != workspace["id"]) for found in candidates)
        # Several windows of a member app: the user says which one, never Flow.
        choices = [{"app": app, "app_name": app_name(app) or app,
                    "windows": [{"id": w["id"], "label": w.get("title") or app} for w in found
                                if w.get("workspace_id") == workspace["id"]]}
                   for app, found in zip(group["members"], candidates) if len(found) > 1]
        choices = [choice for choice in choices if len(choice["windows"]) > 0]
        result.append({"id": group["id"], "label": group["label"], "status": status,
                       "can_open": can_open, "can_rejoin": can_rejoin, "can_resume_layout": can_resume,
                       "can_repair": can_repair, "can_adopt_order": can_adopt, "choices": choices,
                       "missing_app": missing[0] if can_open else ""})
    return result


def side_group_repair_plan(group, workspace, windows, groups, pins):
    """Return the saved-order members and their columns, without guessing identities."""
    matches = [g for g in groups if g.get("id") == group.get("id")]
    if (len(matches) != 1 or matches[0] is not group or group.get("layout") != "side-by-side"
            or group.get("pending_layout")):
        raise ValueError("Saved side-by-side Group is missing or transitioning")
    found = group_members(group, windows)
    if any(w is None for w in found):
        raise ValueError("Group members are missing or ambiguous")
    if any(w.get("workspace_id") != workspace["id"] for w in found):
        raise ValueError("A Group member is on another workspace")
    if any(w.get("is_floating") or not position(w) for w in found):
        raise ValueError("Group members must be standalone tiled windows")
    columns = [position(w)[0] for w in found]
    if any(b <= a for a, b in zip(columns, columns[1:])):
        raise ValueError("Restore the saved Group member order before repair")
    claimed_apps = {app for other in groups if other.get("id") != group["id"]
                    for app in other.get("members", [])}
    pinned = pinned_apps(pins)
    for member, column in zip(found, columns):
        if [w["id"] for w in column_windows(workspace, windows, column)] != [member["id"]]:
            raise ValueError("Group members must be the only windows in their columns")
    for col in range(columns[0] + 1, columns[-1]):
        if col in columns:
            continue
        neighbors = column_windows(workspace, windows, col)
        if len(neighbors) != 1:
            raise ValueError("An intervening column is missing or stacked; resolve it manually")
        neighbor = neighbors[0]
        if (not neighbor.get("app_id") or neighbor.get("app_id") in claimed_apps
                or neighbor.get("app_id") in pinned):
            raise ValueError("An intervening window belongs to another Group or pin")
    return found, columns


def repair_side_group(group_id, workspace, windows, groups, pins):
    group = find_group(groups, group_id)
    found, columns = side_group_repair_plan(group, workspace, windows, groups, pins)
    first = columns[0]
    goal = list(range(first, first + len(columns)))
    if columns == goal:
        raise ValueError("Group members are already adjacent")
    active = workspace.get("active_window_id")
    ids = [w["id"] for w in found]

    def checked(expected_columns, expected_focus):
        ws, live = snapshot()
        plan, live_columns = side_group_repair_plan(group, ws, live, groups, pins)
        if (ws["id"] != workspace["id"] or ws.get("active_window_id") != expected_focus
                or [w["id"] for w in plan] != ids or live_columns != expected_columns):
            raise ValueError("Group, workspace, or focus changed during repair")
        return ws, live

    checked(columns, active)
    try:
        current = list(columns)
        focus = active
        for index in range(1, len(ids)):
            if current[index] == first + index:
                continue
            # shortcut: Niri IPC calls are not atomic; revalidation narrows but cannot close the compositor race.
            command("action", "focus-window", "--id", ids[index])
            focus = ids[index]
            checked(current, focus)
            command("action", "move-column-to-index", first + index)
            # Columns between the target and the old place shift right; later members keep theirs.
            current = current[:index] + [first + index] + current[index + 1:]
            checked(current, focus)
        if active in {w["id"] for w in snapshot()[1]} and active != focus:
            ws, live = checked(goal, focus)
            if not any(w.get("id") == active and w.get("workspace_id") == workspace["id"] for w in live):
                raise ValueError("Focus or Group changed; previous focus was not restored")
            command("action", "focus-window", "--id", active)
            checked(goal, active)
        return
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ValueError("Side-by-side repair interrupted; inspect the windows and retry") from exc


def change_group_layout(state, groups, pins, group_id, workspace, windows, socket, resume=False, allow_unfocused=False):
    group = find_group(groups, group_id)
    source = group.get("layout", "stack")
    target = group.get("pending_layout") if resume else ("side-by-side" if source == "stack" else "stack")
    if (resume and not target) or (not resume and group.get("pending_layout")):
        raise ValueError("Finish the interrupted Group layout change first")
    claims = [pin for pin in pins if set(group["members"]) & set(pin.get("members", [pin.get("app_id")]))]
    if len(claims) > 1 or any(pin.get("group_id") != group_id or
                              set(pin.get("members", [])) != set(group["members"]) for pin in claims):
        raise ValueError("Unpin conflicting apps before changing the Group layout")
    n = len(group["members"])
    goal = 1 if target == "side-by-side" else n

    def layout_windows(layout, ws, live):
        clean = {k: v for k, v in group.items() if k != "pending_layout"}
        clean["layout"] = layout
        current_groups = [clean if g["id"] == group_id else g for g in groups]
        candidate = {"group_id": group_id, "members": group["members"], "session": None}
        return group_windows(candidate, ws, live, socket, current_groups)

    if resume:
        ordered = group_members(group, windows)
        split = split_of(ordered, workspace, windows) if all(ordered) else None
        if split is None:
            raise ValueError("Group layout is unavailable or ambiguous; check every Group app")
    else:
        ordered = layout_windows(source, workspace, windows)
        if not ordered:
            raise ValueError("Group layout is unavailable or ambiguous; check every Group app")
        split = split_of(ordered, workspace, windows)
        if split != (n if source == "stack" else 1):
            raise ValueError("Restore the saved Group member order before changing layout")
        if not allow_unfocused and workspace.get("active_window_id") not in {w["id"] for w in ordered}:
            raise ValueError("Select the Group before changing its layout")
    ids = [w["id"] for w in ordered]
    active = workspace.get("active_window_id")
    workspace_id = workspace["id"]

    def checked(expected):
        ws, live = snapshot()
        found = [next((w for w in live if w.get("id") == ident), None) for ident in ids]
        if (ws["id"] != workspace_id or ws.get("active_window_id") != active or not all(found)
                or [w.get("app_id") for w in found] != group["members"]
                or split_of(found, ws, live) != expected):
            raise ValueError("Group or focus changed; check the windows and finish the layout change")
        return found

    checked(split)
    if not resume:
        if source == "side-by-side" and measured_ratios(ordered):
            group_store.set_ratios(group, measured_ratios(ordered))
        group["pending_layout"] = target
        group_store.write(state / "groups.json", groups)
    try:
        while split != goal:
            # shortcut: Niri's targeted action can consume a neighbor after a race; use expel-only/consume-only by ID when available.
            if split > goal:
                command("action", "consume-or-expel-window-right", "--id", ids[split - 1])
                split -= 1
            else:
                command("action", "consume-or-expel-window-left", "--id", ids[split])
                split += 1
            checked(split)
        ratios = group_store.ratios(group)
        for index, ident in enumerate(ids if target == "side-by-side" else ids[:1]):
            checked(goal)
            command("action", "set-window-width", "--id", ident,
                    f"{round(ratios[index] * 100, 2)}%" if target == "side-by-side" else "100%")
            checked(goal)
        if target == "side-by-side":
            # Allow clients to commit the sizes requested by Niri.
            time.sleep(0.2)
            for attempt in range(11):
                widths = measured_ratios(checked(goal))
                if not widths or all(abs(w / sum(widths) - r) <= 0.05 for w, r in zip(widths, ratios)):
                    break
                if attempt == 10:
                    raise ValueError("Application size constraints prevent the saved proportions")
                time.sleep(0.1)
        group["layout"] = target
        group.pop("pending_layout", None)
        group_store.write(state / "groups.json", groups)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ValueError(f"Group layout change interrupted: {exc}. Check the windows and choose Finish layout change") from exc


def move_plan(groups, workspace, destination, windows):
    """What moving the active space takes: (group, windows to move by column, windows already moved).

    A Group moves whole, in its saved order. A side-by-side Group whose first apps already reached the
    destination (an interrupted move) continues after them; any other split Group must be repaired first.
    """
    active = next((w for w in windows if w["id"] == workspace.get("active_window_id")), None)
    if not active or active.get("workspace_id") != workspace["id"]:
        raise ValueError("Select the space to move first")
    group = group_of(active, groups, windows)
    if active.get("is_floating") or not position(active):
        raise ValueError("Make this Layer a space before moving it to another display")
    if not group:
        return None, [column_windows(workspace, windows, position(active)[0])[0]], []
    if group.get("pending_layout"):
        raise ValueError("Finish the Group layout change first")
    found = group_members(group, windows)
    if not all(found) or any(w.get("is_floating") or not position(w) for w in found):
        raise ValueError("Group apps are missing or ambiguous; repair the Group before moving it")
    here = [w for w in found if w.get("workspace_id") == workspace["id"]]
    there = [w for w in found if w.get("workspace_id") == destination["id"]]
    side = group.get("layout", "stack") == "side-by-side"
    if len(here) == len(found):
        split = split_of(found, workspace, windows)
        if split != (1 if side else len(found)):
            raise ValueError("Repair the Group before moving it to another display")
        return group, found if side else found[:1], []
    if (side and here and len(here) + len(there) == len(found) and found[:len(there)] == there
            and split_of(there, destination, windows) == 1 and split_of(here, workspace, windows) == 1):
        return group, here, there
    raise ValueError("Group apps are on different workspaces; repair the Group before moving it")


def move_to_display(pins, groups, target_key, connected, workspaces, workspace, windows, socket,
                    outputs=None, state=None):
    """Move the active app, stack or Group to another connected display as one destination.

    Niri inserts a moved column after the destination's active column, so each column is moved
    with the previously moved one active there; the space keeps its order and focus. A pinned
    space joins the end of the destination's pinned spaces. Niri calls are separate requests:
    an interrupted Group move leaves its first apps on the destination, and moving again finishes it.
    A side-by-side Group that no longer fits there is stacked rather than cut off: when its apps refuse
    their saved proportions, or when it follows orientation and the destination is portrait. Returns
    the new focused workspace, its windows, and a notice for the user, or "".
    """
    target = next((name for name, record in connected.items() if record["key"] == target_key), None)
    if target is None:
        raise ValueError("That display is not connected")
    if target == workspace.get("output"):
        raise ValueError("This space is already on that display")
    destination = next((ws for ws in workspaces if ws.get("output") == target and ws.get("is_active")), None)
    if destination is None:
        raise ValueError("That display has no active workspace")
    group, columns, moved = move_plan(groups, workspace, destination, windows)
    active = workspace.get("active_window_id")
    ids = {w["id"] for w in moved}
    for column in columns:
        ids.update(w["id"] for w in column_windows(workspace, windows, position(column)[0]))
    if group:
        claims = [p for p in pins if p.get("group_id") == group["id"]]
    else:
        claims = [p for p in pins if "members" not in p and (resolve(p, windows, socket) or {}).get("id") in ids]
    anchor = moved[-1]["id"] if moved else None
    if anchor is None:
        # Never insert inside a side-by-side Group that is active on the destination.
        there = next((space for space in display_spaces(destination, windows, groups, pins) if space["active"]), None)
        if there and there.get("group_layout") == "side-by-side":
            anchor = there["members"][-1]["id"]
    try:
        for column in columns:
            if anchor:
                command("action", "focus-window", "--id", anchor)
            command("action", "focus-window", "--id", column["id"])
            command("action", "move-column-to-monitor", target)
            anchor = column["id"]
        command("action", "focus-window", "--id", active)
    except (OSError, subprocess.SubprocessError) as exc:
        if not group or anchor is None:
            raise
        raise ValueError(f"Moving the Group stopped part-way: {exc}. Select one of its apps still on this "
                         "display and move it again") from exc
    workspace, windows = snapshot()
    if (workspace.get("output") != target or workspace["id"] != destination["id"]
            or any(w.get("workspace_id") != destination["id"] for w in windows if w["id"] in ids)
            or len([w for w in windows if w["id"] in ids]) != len(ids)):
        raise ValueError("The space did not arrive on that display; check its windows and move it again")
    # Every window is there, so its pin is too, even if a later step needs the user's attention.
    for pin in claims:
        if pin.get("display") != target_key:
            pin_store.move_here(pins, pin["id"], target_key)
    ordered = group_members(group, windows) if group else []
    side = bool(group) and group.get("layout", "stack") == "side-by-side"
    if group and split_of(ordered, workspace, windows) != (1 if side else len(ordered)):
        raise ValueError("The Group arrived out of order; repair it on that display")
    notice = ""
    if side:
        saved = group_store.ratios(group)
        for ident, ratio in zip([w["id"] for w in ordered], saved):
            command("action", "set-window-width", "--id", ident, f"{round(ratio * 100, 2)}%")
        logical = ((outputs or {}).get(target) or {}).get("logical") or {}
        portrait = logical.get("height", 0) > logical.get("width", 0)
        fits = settled_ratios([w["id"] for w in ordered], saved, workspace["id"])
        if not fits or (group.get("auto_layout") and portrait):
            workspace, windows = snapshot()
            change_group_layout(state, groups, pins, group["id"], workspace, windows, socket, allow_unfocused=True)
            group_store.set_ratios(group, saved)  # keep the proportions for when it fits side by side again
            group_store.write(state / "groups.json", groups)
            side = False
            label = connected[target]["label"]
            notice = (f"{group['label']} was stacked to fit {label}" if not fits
                      else f"{group['label']} was stacked for the portrait {label}")
    if claims:
        # Pin reconciliation is paused beside side-by-side Groups, so place the moved pin explicitly:
        # it joins the end of this display's pinned spaces, its columns in order.
        workspace, windows = snapshot()
        live = {w["id"]: w for w in windows}
        if group:
            placed = group_members(group, windows)
            placed = placed if side else placed[:1]
        else:
            placed = [live[columns[-1]["id"]]]

        def width_of(pin):
            found = group_windows(pin, workspace, windows, socket, groups) if pin.get("group_id") else None
            return len(found) if found and split_of(found, workspace, windows) == 1 else 1

        width = sum(width_of(p) for p in local_pins(pins, target_key)
                    if p["id"] != claims[0]["id"] and pin_window(p, workspace, windows, socket, groups))
        start = position(placed[0])[0]
        if start != width + 1:
            sequence = list(enumerate(placed))
            for index, member in (sequence if width + 1 < start else reversed(sequence)):
                command("action", "focus-window", "--id", member["id"])
                command("action", "move-column-to-index", width + 1 + index)
            command("action", "focus-window", "--id", active)
    workspace, windows = snapshot()
    return workspace, windows, notice


def output_order(outputs, name):
    logical = (outputs.get(name) or {}).get("logical") or {}
    return logical.get("x", 0), logical.get("y", 0), name


def other_pins(pins, display, displays, connected, ambiguous, workspaces, windows, socket, groups):
    """Pins owned by other, disconnected or unidentified displays, with where they stand."""
    labels = {}
    result = []
    for pin in pins:
        if display is not None and pin.get("display") == display:
            continue
        key = pin.get("display")
        state = pin_store.status(key, connected, ambiguous)
        if key not in labels:
            labels[key] = next((r["label"] for r in connected.values() if r["key"] == key), None) or \
                          (displays.get(key, {}).get("label") if key else None) or "No display"
        live = any(pin_window(pin, ws, windows, socket, groups) for ws in workspaces)
        result.append({"id": pin["id"], "label": pin["label"], "kind": "group" if "members" in pin else "app",
                       "size": len(pin.get("members") or [1]), "group_id": pin.get("group_id"), "display": key, "display_label": labels[key],
                       "display_name": pin_store.display_name(displays.get(key)) if key else "",
                       "display_state": state, "available": live})
    return result


def run_locked(state):
    """Run one action; Group bindings apply only while it runs, in this Niri session."""
    global SESSION
    try:
        return _run_locked(state)
    finally:
        SESSION = None


def _run_locked(state):
    path = state / "spaces.json"
    with (state / "spaces.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        pins, displays, version = pin_store.read(path)
        before = json.dumps([pins, displays, version], sort_keys=True)
        workspace, windows = snapshot()
        outputs, workspaces = display_state()
        connected, ambiguous = pin_store.identities(outputs, pin_store.shared_serials(displays))
        socket = session_id()
        global SESSION
        SESSION = socket
        groups = group_store.load(state)
        before_groups = json.dumps(groups, sort_keys=True)
        sync_group_pins(pins, groups)

        def locate(pin):
            places = [ws.get("output") for ws in workspaces if ws.get("is_active") and ws.get("output") in connected
                      and pin_window(pin, ws, windows, socket, groups)]
            return places[0] if len(places) == 1 else None

        pin_store.migrate(pins, displays, version, connected, locate)
        screen_order = [connected[name]["key"] for name in sorted(connected, key=lambda n: output_order(outputs, n))]
        pin_store.assign_numbers(displays, screen_order)

        def here(ws):
            record = connected.get(ws.get("output"))
            return record["key"] if record else None

        display = here(workspace)
        op = sys.argv[1] if len(sys.argv) > 1 else "refresh"
        arg = sys.argv[2] if len(sys.argv) > 2 else None
        notice = ""
        if op == "focus-number":
            # "D:S" — pinned space S of display number D (0 is Main), as typed with Super or Super+Alt.
            try:
                number, index = (int(part) for part in (arg or "").split(":"))
            except ValueError:
                raise ValueError("Choose a display and pinned space number") from None
            key = pin_store.by_number(displays, number)
            name = "The main display" if number == 0 else f"External {number}"
            live = {record["key"] for record in connected.values()}
            if number == 0 and key not in live and live:
                # With Main away (a closed laptop on a dock), Super+number uses the lowest-numbered display.
                key = min(live, key=lambda k: displays.get(k, {}).get("number", 99))
                name = pin_store.display_name(displays.get(key))
                notice = f"The main display is not connected; using {name}"
            if key is None:
                raise ValueError(f"{name} has not been connected" if number else "No main display is set")
            if pin_store.status(key, connected, ambiguous) != "connected":
                raise ValueError(f"{name} is not connected")
            owned = local_pins(pins, key)
            if not 1 <= index <= len(owned):
                raise ValueError(f"{name} has no pinned space {index}")
            op, arg = "focus", owned[index - 1]["id"]
        elif op == "make-main":
            pin_store.make_main(displays, arg)
            op, arg = "refresh", None
        if op == "focus":
            pin = next((p for p in pins if p["id"] == arg), None)
            owner = pin and pin.get("display")
            if pin and display is not None and owner != display:
                if pin_store.status(owner, connected, ambiguous) == "connected":
                    connector = next(name for name, record in connected.items() if record["key"] == owner)
                    command("action", "focus-monitor", connector)
                    workspace, windows = snapshot()
                    if workspace.get("output") != connector:
                        raise ValueError("Display focus changed; the pinned space was not opened")
                    display = owner
                else:
                    # A pin whose display is gone is focused where its window is; it is never launched here.
                    places = [(ws, pin_window(pin, ws, windows, socket, groups)) for ws in workspaces]
                    places = [(ws, w) for ws, w in places if w]
                    if len(places) != 1:
                        raise ValueError("This space is pinned on a display that is not connected; "
                                         "move it to this display to open it here")
                    command("action", "focus-window", "--id", places[0][1]["id"])
                    command("action", "close-overview")
                    workspace, windows = snapshot()
                    display = here(workspace)
                    op = "refresh"
        for pin in pins:
            if "members" in pin:
                found = group_windows(pin, workspace, windows, socket, groups)
                if found and (pin.get("session") != socket or pin.get("window_ids") != [w["id"] for w in found]):
                    pin["session"], pin["window_id"], pin["window_ids"] = socket, found[0]["id"], [w["id"] for w in found]
                continue
            window = resolve(pin, windows, socket)
            if window and (pin.get("session") != socket or pin.get("window_id") != window["id"]):
                pin["session"], pin["window_id"] = socket, window["id"]
        if op == "move-to-display":
            try:
                workspace, windows, notice = move_to_display(pins, groups, arg, connected, workspaces, workspace,
                                                             windows, socket, outputs, state)
            except (ValueError, OSError, subprocess.SubprocessError):
                if before != json.dumps([pins, displays, 2], sort_keys=True):
                    pin_store.write(path, pins, displays, version)
                raise
            before_groups = json.dumps(groups, sort_keys=True) if notice else before_groups
            outputs, workspaces = display_state()
            display = here(workspace)
            result = run(pins, "refresh", None, workspace, windows, socket, groups, display)
        elif op == "repair-side-group":
            run(pins, op, arg, workspace, windows, socket, groups, display)
            workspace, windows = snapshot()
            result = run(pins, "refresh", None, workspace, windows, socket, groups, display)
        elif op == "toggle-auto-layout":
            active = next((space for space in display_spaces(workspace, windows, groups, pins)
                           if space["active"] and space.get("group_id") == arg), None)
            matches = [group for group in groups if group["id"] == arg]
            if not active or len(matches) != 1:
                raise ValueError("Select the active saved Group first")
            matches[0]["auto_layout"] = not matches[0].get("auto_layout", False)
            result = run(pins, "refresh", None, workspace, windows, socket, groups, display)
        elif op == "auto-layout":
            automatic = [space for space in display_spaces(workspace, windows, groups, pins)
                         if space.get("auto_layout")]
            if automatic:
                logical = outputs.get(workspace.get("output"), {}).get("logical")
                if not logical or not logical.get("width") or not logical.get("height"):
                    raise ValueError("Cannot determine output size for automatic Group layout")
                target = "stack" if logical["height"] > logical["width"] else "side-by-side"
                changed = next((space for space in automatic if space["group_layout"] != target), None)
                if changed:
                    change_group_layout(state, groups, pins, changed["group_id"], workspace, windows, socket,
                                        allow_unfocused=True)
                    workspace, windows = snapshot()
                    before_groups = json.dumps(groups, sort_keys=True)
            result = run(pins, "refresh", None, workspace, windows, socket, groups, display)
        elif op in ("group-layout", "resume-group-layout"):
            if op == "group-layout":
                for group in groups:
                    if group["id"] == arg and group.get("auto_layout"):
                        group["auto_layout"] = False
                        group_store.write(state / "groups.json", groups)
            change_group_layout(state, groups, pins, arg, workspace, windows, socket, op == "resume-group-layout")
            workspace, windows = snapshot()
            before_groups = json.dumps(groups, sort_keys=True)
            result = run(pins, "refresh", None, workspace, windows, socket, groups, display)
        else:
            result = run(pins, op, arg, workspace, windows, socket, groups, display)
        if op in ("dissolve-group", "open-group-member", "rejoin-group", "add-to-group", "remove-from-group",
                  "member-left", "member-right", "member-wider", "member-narrower",
                  "make-layer", "open-layer", "layer-to-space", "close-layer"):
            workspace, windows = snapshot()
        # Groups recognized here are bound to their windows, so other windows of their apps stay apart.
        for space in display_spaces(workspace, windows, groups, pins):
            if space.get("group_id"):
                group = find_group(groups, space["group_id"])
                bind_group(group, [next(w for w in windows if w["id"] == m["id"]) for m in space["members"]], windows)
        if before_groups != json.dumps(groups, sort_keys=True):
            try:
                group_store.write(state / "groups.json", groups)
            except OSError as exc:
                if op == "dissolve-group":
                    raise ValueError("Could not confirm Group update; if it remains saved, stack its apps and retry") from exc
                raise ValueError("Could not confirm Group update; check the Group state before retrying") from exc
        if before != json.dumps([pins, displays, 2], sort_keys=True):
            pin_store.write(path, pins, displays, version)
        ordered = sorted((w for w in windows if w.get("workspace_id") == workspace["id"]),
                         key=lambda w: (position(w) or [99999, 0]))
        visible = [{"id": w["id"], "label": w.get("title") or w.get("app_id") or "Application",
                    "app_id": w.get("app_id") or "", "active": w["id"] == workspace.get("active_window_id")}
                   for w in ordered]
        record = connected.get(workspace.get("output"))
        print(json.dumps({"pins": result, "windows": visible, "spaces": display_spaces(workspace, windows, groups, pins),
                          "layers": layers(workspace, windows),
                          "incomplete_groups": incomplete_groups(workspace, windows, groups, pins),
                          "other_pins": other_pins(pins, display, displays, connected, ambiguous, workspaces, windows, socket, groups),
                          "display": {"key": display, "label": record["label"] if record else workspace.get("output") or "",
                                      "name": pin_store.display_name(displays.get(display)),
                                      "number": (displays.get(display) or {}).get("number")},
                          "display_count": len(connected),
                          "displays": [{"key": key, "label": displays[key].get("label", ""),
                                        "name": pin_store.display_name(displays[key]),
                                        "number": displays[key].get("number"), "current": key == display}
                                       for key in screen_order],
                          "notice": notice,
                          "action": sys.argv[1] if len(sys.argv) > 1 else "refresh"}))


def main():
    state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "flow"
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state / "session.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        run_locked(state)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
