#!/usr/bin/env python3
"""Experimental single-display, single-window Flow anchors for Niri."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid
import groups as group_store


class MultipleDisplays(ValueError):
    pass


def command(*args):
    return subprocess.check_output(["niri", "msg", *map(str, args)], text=True, stderr=subprocess.PIPE)


def snapshot():
    outputs = json.loads(command("-j", "outputs"))
    workspaces = json.loads(command("-j", "workspaces"))
    windows = json.loads(command("-j", "windows"))
    if sum(output.get("logical") is not None for output in outputs.values()) != 1:
        raise MultipleDisplays("Pinned spaces currently require one display")
    workspace = next((w for w in workspaces if w["is_focused"]), None)
    if workspace is None:
        raise ValueError("No active workspace")
    return workspace, windows


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


def resolve(pin, windows, socket):
    """Never guess which of several same-app windows is the saved space."""
    if pin.get("session") == socket:
        match = next((w for w in windows if w["id"] == pin.get("window_id") and w["app_id"] == pin["app_id"]), None)
        if match:
            return match
    matches = [w for w in windows if w["app_id"] == pin["app_id"]]
    return matches[0] if len(matches) == 1 else None


def eligible(window, workspace, windows):
    if not window or not window.get("app_id") or window.get("is_floating"):
        return False
    pos = window.get("layout", {}).get("pos_in_scrolling_layout")
    if not pos or window["workspace_id"] != workspace["id"]:
        return False
    return sum(1 for other in windows if other["workspace_id"] == workspace["id"] and
               (other.get("layout", {}).get("pos_in_scrolling_layout") or [None])[0] == pos[0]) == 1


def group_windows(pin, workspace, windows, socket, groups=()):
    members = pin.get("members")
    if (not isinstance(members, list) or len(members) != 2
            or not all(isinstance(app, str) and app for app in members) or len(set(members)) != 2):
        return None
    group_id = pin.get("group_id")
    saved = [g for g in groups if g.get("id") == group_id]
    matching_members = [g for g in groups if set(g.get("members", [])) == set(members)]
    if (not isinstance(group_id, str) or len(saved) != 1 or len(matching_members) != 1
            or matching_members[0].get("id") != group_id):
        return None
    if pin.get("session") == socket:
        ids = pin.get("window_ids", [])
        if len(ids) != 2:
            return None
        pair = [next((w for w in windows if w["id"] == ident and w.get("app_id") == app), None)
                for app, ident in zip(members, ids)]
    else:
        pair = []
    if not pair or any(w is None for w in pair):
        pair = []
        for app in members:
            matches = [w for w in windows if w.get("app_id") == app]
            pair.append(matches[0] if len(matches) == 1 else None)
    if any(not w or w.get("workspace_id") != workspace["id"] or w.get("is_floating")
           or not w.get("layout", {}).get("pos_in_scrolling_layout") for w in pair):
        return None
    positions = [w["layout"]["pos_in_scrolling_layout"] for w in pair]
    column = positions[0][0]
    group = saved[0]
    if group.get("pending_layout"):
        return None
    if group.get("layout", "stack") == "side-by-side":
        if positions[1][0] != column + 1 or not all(eligible(w, workspace, windows) for w in pair):
            return None
    elif positions[1][0] != column or sum(w.get("workspace_id") == workspace["id"] and not w.get("is_floating")
            and (w.get("layout", {}).get("pos_in_scrolling_layout") or [None])[0] == column for w in windows) != 2:
        return None
    if any(sum(w.get("app_id") == app for w in windows) != 1 for app in members):
        return None
    return pair


def pin_window(pin, workspace, windows, socket, groups=()):
    if "members" in pin:
        pair = group_windows(pin, workspace, windows, socket, groups)
        return next((w for w in pair if w["app_id"] == pin.get("last_focused_app")), pair[0]) if pair else None
    window = resolve(pin, windows, socket)
    return window if eligible(window, workspace, windows) else None


def has_side_layout(workspace, windows, groups):
    apps = {w.get("app_id") for w in windows if w.get("workspace_id") == workspace["id"]}
    return any((g.get("pending_layout") or g.get("layout") == "side-by-side") and
               apps.intersection(g["members"]) for g in groups)


def reconcile(pins, workspace, windows, socket, groups=()):
    # shortcut: absent or ambiguous apps have no physical placeholder; use compositor-backed placeholders when available.
    resolved = [pin_window(pin, workspace, windows, socket, groups) for pin in pins]
    active = workspace.get("active_window_id")
    # One unavailable pin must not prevent the remaining live pins from keeping their order.
    desired = [w for w in resolved if w]
    if has_side_layout(workspace, windows, groups):
        return
    if all(w["layout"]["pos_in_scrolling_layout"][0] == i for i, w in enumerate(desired, 1)):
        return
    try:
        for index, window in enumerate(desired, 1):
            command("action", "focus-window", "--id", window["id"])
            command("action", "move-column-to-index", index)
    finally:
        if active:
            command("action", "focus-window", "--id", active)


def pin_group(pins, group, workspace, windows, socket, groups):
    members = group.get("members", [])
    if len(members) != 2 or len(set(members)) != 2:
        raise ValueError("Select a saved two-app Group")
    pair = []
    for app in members:
        matches = [w for w in windows if w.get("app_id") == app]
        pair.append(matches[0] if len(matches) == 1 else None)
    candidate = {"group_id": group["id"], "members": members,
                 "window_ids": [w["id"] for w in pair if w], "session": socket}
    if len(pair) != 2 or any(not w for w in pair) or not group_windows(candidate, workspace, windows, socket, groups):
        raise ValueError("Saved Group is unavailable, ambiguous, or no longer one stack")
    if any(set(members) & set(p.get("members", [p.get("app_id")])) for p in pins):
        raise ValueError("A Group member is already pinned")
    focused = next((w for w in pair if w["id"] == workspace.get("active_window_id")), pair[0])
    pins.append({"id": uuid.uuid4().hex, "label": group["label"], "app_id": members[0],
                 "window_id": focused["id"], "last_focused_app": focused["app_id"], "members": members, "window_ids": [w["id"] for w in pair],
                 "group_id": group["id"], "session": socket})


def rejoin_pair(group, workspace, windows, pins):
    matches = [[w for w in windows if w.get("app_id") == app] for app in group["members"]]
    if any(len(found) != 1 for found in matches):
        raise ValueError("Group apps are missing or ambiguous")
    pair = [found[0] for found in matches]
    if not all(eligible(w, workspace, windows) for w in pair):
        raise ValueError("Both Group apps must be single tiled windows on this workspace")
    left, right = sorted(pair, key=lambda w: w["layout"]["pos_in_scrolling_layout"][0])
    left_col = left["layout"]["pos_in_scrolling_layout"][0]
    right_col = right["layout"]["pos_in_scrolling_layout"][0]
    if right_col != left_col + 1:
        raise ValueError("Place the Group apps in adjacent columns before rejoining")
    claims = [pin for pin in pins if set(group["members"]) & set(pin.get("members", [pin.get("app_id")]))]
    if len(claims) > 1 or any(pin.get("group_id") != group["id"] or
                              set(pin.get("members", [])) != set(group["members"]) for pin in claims):
        raise ValueError("Unpin conflicting apps before rejoining")
    if claims and any(w.get("workspace_id") == workspace["id"] and not w.get("is_floating")
                      and (w.get("layout", {}).get("pos_in_scrolling_layout") or [0])[0] > right_col for w in windows):
        raise ValueError("Move the pinned Group apps to the end before rejoining")
    pinned_apps = {app for pin in pins if pin not in claims for app in pin.get("members", [pin.get("app_id")])}
    if any(w.get("workspace_id") == workspace["id"] and w.get("app_id") in pinned_apps
           and (w.get("layout", {}).get("pos_in_scrolling_layout") or [0])[0] > right_col for w in windows):
        raise ValueError("Move other pinned spaces left before rejoining")
    return left, right


def run(pins, op, arg, workspace, windows, socket, groups=()):
    if op == "repair-side-group":
        repair_side_group(arg, workspace, windows, groups, pins)
        return [{"id": p["id"], "label": p["label"], "kind": "group" if "members" in p else "app",
                 "group_id": p.get("group_id"), "available": bool(pin_window(p, workspace, windows, socket, groups))}
                for p in pins]
    if op in ("pin", "pin-group", "left", "right", "create-group", "dissolve-group", "stack", "unstack") and has_side_layout(workspace, windows, groups):
        raise ValueError("Return side-by-side Groups to a stack before changing pins or Groups")
    for group in groups:
        candidate = {"group_id": group["id"], "members": group["members"], "session": None}
        pair = group_windows(candidate, workspace, windows, socket, groups)
        focused = next((w for w in pair or [] if w["id"] == workspace.get("active_window_id")), None)
        if focused and group.get("last_focused_app") != focused["app_id"]:
            group["last_focused_app"] = focused["app_id"]
    for pin in pins:
        if "group_id" in pin:
            matches = [group for group in groups if group.get("id") == pin["group_id"]]
            members = pin.get("members")
            if (len(matches) == 1 and isinstance(members, list) and len(members) == 2
                    and all(isinstance(app, str) for app in members)
                    and set(matches[0]["members"]) == set(members)):
                pin["label"] = matches[0]["label"]
            pair = group_windows(pin, workspace, windows, socket, groups)
            focused = next((w for w in pair or [] if w["id"] == workspace.get("active_window_id")), None)
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
        matches = [group for group in groups if group.get("id") == arg]
        if len(matches) != 1:
            raise ValueError("Saved Group no longer exists or is ambiguous")
        group = matches[0]
        if group.get("pending_layout") or group.get("layout", "stack") != "stack":
            raise ValueError("Finish the Group layout change before rejoining")
        pair = rejoin_pair(group, workspace, windows, pins)
        current_workspace, current_windows = snapshot()
        current_pair = rejoin_pair(group, current_workspace, current_windows, pins)
        if (current_workspace["id"] != workspace["id"]
                or current_workspace.get("active_window_id") != workspace.get("active_window_id")
                or [(w["id"], w["layout"]["pos_in_scrolling_layout"]) for w in current_pair]
                != [(w["id"], w["layout"]["pos_in_scrolling_layout"]) for w in pair]):
            raise ValueError("Group or focus changed; try again")
        member = next(w for w in current_pair if w["app_id"] == group["members"][1])
        direction = "right" if member["id"] == current_pair[0]["id"] else "left"
        # shortcut: Niri's targeted action can consume a neighbor after a race; use consume-only by ID when available.
        command("action", "consume-or-expel-window-" + direction, "--id", member["id"])
        workspace, windows = snapshot()
        check = {"group_id": group["id"], "members": group["members"], "session": ""}
        joined = group_windows(check, workspace, windows, socket, groups)
        if (not joined or {w["id"] for w in joined} != {w["id"] for w in pair}
                or joined[0]["layout"]["pos_in_scrolling_layout"][1] >= joined[1]["layout"]["pos_in_scrolling_layout"][1]):
            raise ValueError("Could not confirm Group rejoin; saved Group remains. Check the windows before retrying")
        for pin in pins:
            if pin.get("group_id") == group["id"]:
                pin["session"], pin["window_ids"] = socket, [w["id"] for w in joined]
                focused = next((w for w in joined if w["app_id"] == pin.get("last_focused_app")), joined[0])
                pin["window_id"] = focused["id"]
    elif op == "open-group-member":
        matches = [group for group in groups if group.get("id") == arg]
        if len(matches) != 1:
            raise ValueError("Saved Group no longer exists or is ambiguous")
        members = matches[0]["members"]
        current_workspace, current_windows = snapshot()
        if current_workspace["id"] != workspace["id"]:
            raise ValueError("Workspace changed; Group was not opened")
        candidates = [[w for w in current_windows if w.get("app_id") == app] for app in members]
        if any(len(found) > 1 for found in candidates):
            raise ValueError("Group has multiple windows for an app; resolve duplicates before retrying")
        if any(found and found[0].get("workspace_id") != workspace["id"] for found in candidates):
            raise ValueError("A Group app is on another workspace; move it back before retrying")
        missing = [app for app, found in zip(members, candidates) if not found]
        if not missing:
            raise ValueError("Both Group apps are open; stack them to rejoin the Group")
        app = missing[0]
        dirs = (os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share"),
                *os.environ.get("XDG_DATA_DIRS", "/run/current-system/sw/share:/usr/share").split(":"))
        desktop = desktop_file(app, dirs)
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
            found = [w for w in current_windows if w.get("app_id") == app]
            if len(found) > 1:
                raise ValueError("Multiple windows opened; resolve duplicates before retrying")
            if found:
                if found[0].get("workspace_id") != workspace["id"]:
                    raise ValueError("The Group app opened on another workspace; move it back before retrying")
                break
        else:
            raise ValueError("The Group app did not appear; it may still open later, so check before retrying")
    elif op in ("create-group", "dissolve-group"):
        active = next((space for space in display_spaces(workspace, windows, groups) if space["active"]), None)
        if not active or len(active["members"]) != 2:
            raise ValueError("Select a stack of two apps first")
        apps = [w["app_id"] for w in active["members"]]
        if (not all(apps) or len(set(apps)) != 2
                or any(sum(w.get("app_id") == app for w in windows) != 1 for app in apps)):
            raise ValueError("Group members must be distinct, unambiguous apps")
        if any(set(apps) & set(p.get("members", [p.get("app_id")])) for p in pins):
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
            groups.append({"id": uuid.uuid4().hex, "label": " + ".join(w["label"] for w in active["members"]), "members": apps})
        else:
            column = next(w for w in current_windows if w["id"] == workspace["active_window_id"])["layout"]["pos_in_scrolling_layout"][0]
            pinned_apps = {app for pin in pins for app in pin.get("members", [pin.get("app_id")])}
            if any(w.get("workspace_id") == workspace["id"] and not w.get("is_floating") and w.get("app_id") in pinned_apps
                   and (w.get("layout", {}).get("pos_in_scrolling_layout") or [0])[0] > column for w in current_windows):
                raise ValueError("Move pinned spaces left before dissolving this Group")
            # shortcut: Niri's targeted action can consume a neighbor after a race; use expel-only by ID when available.
            command("action", "consume-or-expel-window-right", "--id", workspace["active_window_id"])
            changed_workspace, changed_windows = snapshot()
            pair = [next((w for w in changed_windows if w.get("id") == member["id"]), None) for member in active["members"]]
            if (changed_workspace["id"] != workspace["id"]
                    or not all(eligible(w, changed_workspace, changed_windows) for w in pair)
                    or any(w.get("app_id") != app for w, app in zip(pair, apps))):
                raise ValueError("Could not confirm separation; Group remains saved. Stack its apps and retry")
            groups.remove(next(g for g in groups if g["id"] == arg))
    elif op == "pin":
        window = next((w for w in windows if w["id"] == workspace.get("active_window_id")), None)
        if not window:
            raise ValueError("Select one non-floating app window to pin")
        if window.get("is_floating"):
            raise ValueError("Select one non-floating app window to pin")
        pos = window.get("layout", {}).get("pos_in_scrolling_layout")
        stack = [w for w in windows if w.get("workspace_id") == workspace["id"] and not w.get("is_floating")
                 and pos and (w.get("layout", {}).get("pos_in_scrolling_layout") or [None])[0] == pos[0]]
        if len(stack) > 1:
            apps = [w.get("app_id") for w in stack]
            matches = [g for g in groups if set(g.get("members", [])) == set(apps)]
            if len(matches) != 1:
                raise ValueError("Create a Group from this stack before pinning it")
            pin_group(pins, matches[0], workspace, windows, socket, groups)
            window = None
        if window:
            if not eligible(window, workspace, windows):
                raise ValueError("Select one non-floating app window to pin")
            if any(window["app_id"] in p.get("members", [p["app_id"]]) for p in pins):
                raise ValueError("This app is already pinned")
            pins.append({"id": uuid.uuid4().hex, "label": window.get("title") or window["app_id"],
                         "app_id": window["app_id"], "window_id": window["id"], "session": socket})
    elif op == "pin-group":
        matches = [g for g in groups if g.get("id") == arg]
        if len(matches) != 1:
            raise ValueError("Select a saved two-app Group")
        pin_group(pins, matches[0], workspace, windows, socket, groups)
    elif op == "group-view":
        active = next((space for space in display_spaces(workspace, windows, groups) if space["active"]), None)
        if not arg or not active or active["group_id"] != arg:
            raise ValueError("Select the active saved two-app Group first")
        current_workspace, current_windows = snapshot()
        current = next((space for space in display_spaces(current_workspace, current_windows, groups) if space["active"]), None)
        if (current_workspace["id"] != workspace["id"]
                or current_workspace.get("active_window_id") != workspace.get("active_window_id")
                or not current or current["group_id"] != arg
                or {w["id"] for w in current["members"]} != {w["id"] for w in active["members"]}):
            raise ValueError("Group or focus changed; try again")
        # shortcut: Niri toggles only the focused column; use a targeted action if it becomes available.
        command("action", "toggle-column-tabbed-display")
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
                pair = group_windows(pin, workspace, windows, socket, groups)
                pin["session"], pin["window_id"], pin["window_ids"] = socket, window["id"], [w["id"] for w in pair]
                command("action", "focus-window", "--id", window["id"])
                command("action", "close-overview")
            elif not window and not any(w.get("app_id") == pin["app_id"] for w in windows):
                dirs = (os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share"),
                        *os.environ.get("XDG_DATA_DIRS", "/run/current-system/sw/share:/usr/share").split(":"))
                desktop = desktop_file(pin["app_id"], dirs)
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
            target = index + (-1 if op == "left" else 1)
            if 0 <= target < len(pins):
                pins[index], pins[target] = pins[target], pins[index]
    elif op in ("stack", "unstack"):
        active = next((w for w in windows if w["id"] == workspace.get("active_window_id")), None)
        pos = active and active.get("layout", {}).get("pos_in_scrolling_layout")
        if not pos or active.get("is_floating") or active.get("workspace_id") != workspace["id"]:
            raise ValueError("Select a tiled window first")
        def column(index):
            return [w for w in windows if w.get("workspace_id") == workspace["id"] and not w.get("is_floating")
                    and (w.get("layout", {}).get("pos_in_scrolling_layout") or [None])[0] == index]
        current = column(pos[0])
        pair = current + column(pos[0] + 1) if op == "stack" else current
        if op == "stack" and (len(current) != 1 or len(pair) != 2):
            raise ValueError("Select a single window with one single window to its right")
        if op == "unstack" and len(pair) != 2:
            raise ValueError("Select a stack containing exactly two windows")
        if op == "unstack" and any(set(g["members"]) == {w.get("app_id") for w in pair} for g in groups):
            raise ValueError("Use Dissolve Group to separate a saved Group")
        if any(w.get("app_id") in pin.get("members", [pin.get("app_id")]) for pin in pins for w in pair):
            raise ValueError("Unpin these windows before stacking or separating")
        command("action", "consume-window-into-column" if op == "stack" else "expel-window-from-column")
    elif op == "navigate":
        if arg not in ("left", "right"):
            raise ValueError("Choose left or right")
        destinations = display_spaces(workspace, windows, groups, pins)
        index = next((i for i, space in enumerate(destinations) if space["active"]), None)
        if index is not None:
            target = index + (-1 if arg == "left" else 1)
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
    if op not in ("focus", "focus-window", "focus-member", "navigate", "stack", "unstack", "group-view", "create-group", "dissolve-group", "open-group-member", "rejoin-group"):
        reconcile(pins, workspace, windows, socket, groups)
    return [{"id": p["id"], "label": p["label"], "kind": "group" if "members" in p else "app", "group_id": p.get("group_id"),
             "available": bool(pin_window(p, workspace, windows, socket, groups))} for p in pins]


def saved_groups(path):
    try:
        data = json.loads(path.read_text())
        groups = data.get("groups", []) if isinstance(data, dict) else []
        if not isinstance(groups, list):
            return []
        return [g for g in groups if isinstance(g, dict) and isinstance(g.get("id"), str)
                and isinstance(g.get("label"), str) and isinstance(g.get("members"), list)
                and len(g["members"]) == 2 and all(isinstance(a, str) and a for a in g["members"])
                and len(set(g["members"])) == 2]
    except (OSError, ValueError):
        return []


def display_spaces(workspace, windows, groups=(), pins=()):
    """Represent each tiled column once; floating windows remain separate."""
    current = [w for w in windows if w.get("workspace_id") == workspace["id"]]
    current.sort(key=lambda w: w.get("layout", {}).get("pos_in_scrolling_layout") or [99999, 0])
    columns = []
    for w in current:
        pos = w.get("layout", {}).get("pos_in_scrolling_layout")
        key = ("column", pos[0]) if pos and not w.get("is_floating") else ("window", w["id"])
        if not columns or columns[-1][0] != key:
            columns.append((key, []))
        columns[-1][1].append({"id": w["id"], "label": w.get("title") or w.get("app_id") or "Application",
                              "app_id": w.get("app_id") or "", "active": w["id"] == workspace.get("active_window_id")})
    result = []
    counts = {app: sum(w.get("app_id") == app for w in windows) for g in groups for app in g["members"]}
    skip = set()
    for index, (_, members) in enumerate(columns):
        if index in skip:
            continue
        for group in groups:
            if group.get("layout") != "side-by-side" or group.get("pending_layout"):
                continue
            candidate = {"group_id": group["id"], "members": group["members"], "session": None}
            pair = group_windows(candidate, workspace, windows, "", groups)
            if pair and members[0]["id"] == pair[0]["id"] and index + 1 < len(columns) and columns[index + 1][1][0]["id"] == pair[1]["id"]:
                joined = members + columns[index + 1][1]
                pin = next((p for p in pins if p.get("group_id") == group["id"]), {})
                last = pin.get("last_focused_app") or group.get("last_focused_app")
                selected = next((w for w in joined if w["active"]),
                                next((w for w in joined if w["app_id"] == last), joined[0]))
                result.append({"id": selected["id"], "label": group["label"], "group_id": group["id"],
                               "group_layout": "side-by-side", "active": any(w["active"] for w in joined), "members": joined})
                skip.add(index + 1)
                break
        else:
            group = None
        if group:
            continue
        selected = next((w for w in members if w["active"]), members[0])
        app_ids = [w["app_id"] for w in members]
        matches = [g for g in groups if g.get("layout", "stack") == "stack" and not g.get("pending_layout")
                   and len(app_ids) == 2 and len(set(app_ids)) == 2
                   and len(g["members"]) == 2 and len(set(g["members"])) == 2
                   and set(g["members"]) == set(app_ids) and all(counts.get(a) == 1 for a in app_ids)]
        group = matches[0] if len(matches) == 1 and sum(g["id"] == matches[0]["id"] for g in groups) == 1 else None
        if group and not any(w["active"] for w in members):
            pin = next((p for p in pins if p.get("group_id") == group["id"]), {})
            last = pin.get("last_focused_app") or group.get("last_focused_app")
            selected = next((w for w in members if w["app_id"] == last), selected)
        result.append({"id": selected["id"], "label": group["label"] if group else " + ".join(w["label"] for w in members),
                       "group_id": group["id"] if group else None, "group_layout": "stack" if group else None,
                       "active": any(w["active"] for w in members), "members": members})
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
        candidates = [[w for w in windows if w.get("app_id") == app] for app in group["members"]]
        missing = [app for app, found in zip(group["members"], candidates) if not found]
        can_rejoin = False
        can_repair = False
        can_resume = bool(group.get("pending_layout")) and all(
            len(found) == 1 and found[0].get("workspace_id") == workspace["id"] for found in candidates)
        if group.get("pending_layout"):
            status = "Layout change interrupted; finish it when both apps are available."
        elif any(len(found) > 1 for found in candidates):
            status = "Multiple windows match a Group app; resolve duplicates."
        elif any(found and found[0].get("workspace_id") != workspace["id"] for found in candidates):
            status = "A Group app is on another workspace."
        elif missing:
            status = ("Both apps are closed." if len(missing) == 2 else "A Group app is closed.")
        elif group.get("layout") == "side-by-side":
            try:
                _, first, last = side_group_repair_plan(group, workspace, windows, groups, pins)
                if last == first + 1:
                    raise ValueError("Group members are already adjacent")
                can_repair = True
                status = "Group members are separated by a safe standalone window; repair their order."
            except ValueError as exc:
                status = str(exc) + "."
        else:
            try:
                rejoin_pair(group, workspace, windows, pins)
                can_rejoin = True
                status = "Both apps are open; rejoin them as a Group."
            except ValueError as exc:
                status = str(exc) + "."
        can_open = bool(missing) and not any(len(found) > 1 or
                       (found and found[0].get("workspace_id") != workspace["id"]) for found in candidates)
        result.append({"id": group["id"], "label": group["label"], "status": status,
                       "can_open": can_open, "can_rejoin": can_rejoin, "can_resume_layout": can_resume,
                       "can_repair": can_repair,
                       "missing_app": missing[0] if can_open else ""})
    return result


def side_group_repair_plan(group, workspace, windows, groups, pins):
    """Return the saved-order pair and intervening columns, without guessing identities."""
    matches = [g for g in groups if g.get("id") == group.get("id")]
    if (len(matches) != 1 or matches[0] is not group or group.get("layout") != "side-by-side"
            or group.get("pending_layout")):
        raise ValueError("Saved side-by-side Group is missing or transitioning")
    pair = []
    for app in group["members"]:
        found = [w for w in windows if w.get("app_id") == app]
        if len(found) != 1:
            raise ValueError("Group members are missing or ambiguous")
        pair.append(found[0])
    if any(w.get("workspace_id") != workspace["id"] for w in pair):
        raise ValueError("A Group member is on another workspace")
    if any(w.get("is_floating") or not w.get("layout", {}).get("pos_in_scrolling_layout") for w in pair):
        raise ValueError("Group members must be standalone tiled windows")
    left, right = pair
    first = left["layout"]["pos_in_scrolling_layout"][0]
    last = right["layout"]["pos_in_scrolling_layout"][0]
    if first >= last:
        raise ValueError("Restore the saved Group member order before repair")
    claimed_apps = {app for other in groups if other.get("id") != group["id"]
                    for app in other.get("members", [])}
    pinned_apps = {app for pin in pins for app in pin.get("members", [pin.get("app_id")])}
    by_column = {}
    for w in windows:
        pos = w.get("layout", {}).get("pos_in_scrolling_layout")
        if w.get("workspace_id") == workspace["id"] and pos and not w.get("is_floating"):
            by_column.setdefault(pos[0], []).append(w)
    for member, column in zip(pair, (first, last)):
        if by_column.get(column) != [member]:
            raise ValueError("Group members must be the only windows in their columns")
    for col in range(first + 1, last):
        neighbors = by_column.get(col, [])
        if len(neighbors) != 1:
            raise ValueError("An intervening column is missing or stacked; resolve it manually")
        neighbor = neighbors[0]
        if (not neighbor.get("app_id") or neighbor.get("app_id") in claimed_apps
                or neighbor.get("app_id") in pinned_apps):
            raise ValueError("An intervening window belongs to another Group or pin")
    return pair, first, last


def repair_side_group(group_id, workspace, windows, groups, pins):
    matches = [g for g in groups if g.get("id") == group_id]
    if len(matches) != 1:
        raise ValueError("Saved Group no longer exists or is ambiguous")
    pair, first, last = side_group_repair_plan(matches[0], workspace, windows, groups, pins)
    if last == first + 1:
        raise ValueError("Group members are already adjacent")
    active = workspace.get("active_window_id")
    ids = [w["id"] for w in pair]

    def checked(expected_adjacent=False):
        ws, live = snapshot()
        plan, left_col, right_col = side_group_repair_plan(matches[0], ws, live, groups, pins)
        expected_focus = ids[1] if expected_adjacent else active
        if (ws["id"] != workspace["id"] or ws.get("active_window_id") != expected_focus
                or [w["id"] for w in plan] != ids
                or (expected_adjacent and left_col != first)
                or [w["layout"]["pos_in_scrolling_layout"][0] for w in plan] !=
                   ([left_col, left_col + 1] if expected_adjacent else [first, last])):
            raise ValueError("Group, workspace, or focus changed during repair")
        return ws, live, plan

    checked()
    try:
        # shortcut: Niri IPC calls are not atomic; revalidation narrows but cannot close the compositor race.
        command("action", "focus-window", "--id", pair[1]["id"])
        ws, live = snapshot()
        # Focus necessarily changes for the move; verify all other preconditions before acting.
        if ws["id"] != workspace["id"]:
            raise ValueError("Workspace changed during repair")
        live_plan, live_left, live_right = side_group_repair_plan(matches[0], ws, live, groups, pins)
        if ([w["id"] for w in live_plan] != ids or (live_left, live_right) != (first, last)
                or ws.get("active_window_id") != pair[1]["id"]):
            raise ValueError("Group or focus changed during repair")
        command("action", "move-column-to-index", first + 1)
        ws, live, moved = checked(expected_adjacent=True)
        if active in {w["id"] for w in live} and active != ids[1]:
            ws, live = snapshot()
            plan, left_col, right_col = side_group_repair_plan(matches[0], ws, live, groups, pins)
            if (ws["id"] != workspace["id"] or ws.get("active_window_id") != ids[1]
                    or not any(w.get("id") == active and w.get("workspace_id") == workspace["id"] for w in live)
                    or [w["id"] for w in plan] != ids or (left_col, right_col) != (first, first + 1)):
                raise ValueError("Focus or Group changed; previous focus was not restored")
            command("action", "focus-window", "--id", active)
            ws, live = snapshot()
            verified = {w.get("id"): w for w in live}
            verified_pair, left_col, right_col = side_group_repair_plan(matches[0], ws, live, groups, pins)
            if (ws["id"] != workspace["id"] or active not in verified
                    or verified[active].get("workspace_id") != workspace["id"]
                    or ws.get("active_window_id") != active or [w["id"] for w in verified_pair] != ids
                    or (left_col, right_col) != (first, first + 1)):
                raise ValueError("Could not safely restore the previous focus")
        return
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ValueError("Side-by-side repair interrupted; inspect the windows and retry") from exc


def change_group_layout(state, groups, pins, group_id, workspace, windows, socket, resume=False):
    matches = [group for group in groups if group.get("id") == group_id]
    if len(matches) != 1:
        raise ValueError("Saved Group no longer exists or is ambiguous")
    group = matches[0]
    source = group.get("layout", "stack")
    target = group.get("pending_layout") if resume else ("side-by-side" if source == "stack" else "stack")
    if (resume and not target) or (not resume and group.get("pending_layout")):
        raise ValueError("Finish the interrupted Group layout change first")
    claims = [pin for pin in pins if set(group["members"]) & set(pin.get("members", [pin.get("app_id")]))]
    if len(claims) > 1 or any(pin.get("group_id") != group_id or
                              set(pin.get("members", [])) != set(group["members"]) for pin in claims):
        raise ValueError("Unpin conflicting apps before changing the Group layout")

    def pair_for(layout, ws, live):
        clean = {k: v for k, v in group.items() if k != "pending_layout"}
        clean["layout"] = layout
        current_groups = [clean if g["id"] == group_id else g for g in groups]
        candidate = {"group_id": group_id, "members": group["members"], "session": None}
        return group_windows(candidate, ws, live, socket, current_groups)

    pair = pair_for(source, workspace, windows) or (pair_for(target, workspace, windows) if resume else None)
    if not pair:
        raise ValueError("Group layout is unavailable or ambiguous; check both apps")
    if not resume and workspace.get("active_window_id") not in {w["id"] for w in pair}:
        raise ValueError("Select the Group before changing its layout")
    ids = [w["id"] for w in pair]
    active = workspace.get("active_window_id")
    workspace_id = workspace["id"]

    def checked(layout):
        ws, live = snapshot()
        found = pair_for(layout, ws, live)
        if (ws["id"] != workspace_id or ws.get("active_window_id") != active or not found
                or [w["id"] for w in found] != ids):
            raise ValueError("Group or focus changed; check the windows and finish the layout change")
        return found

    current_source = pair_for(source, workspace, windows)
    if current_source and source == "stack" and (
            current_source[0]["layout"]["pos_in_scrolling_layout"][1] >=
            current_source[1]["layout"]["pos_in_scrolling_layout"][1]):
        raise ValueError("Restore the saved Group member order before changing layout")
    checked(source if current_source else target)
    if not resume:
        group["pending_layout"] = target
        group_store.write(state / "groups.json", groups)
    try:
        if current_source:
            # shortcut: Niri's targeted action can consume a neighbor after a race; use expel-only/consume-only by ID when available.
            command("action", "consume-or-expel-window-right" if target == "side-by-side"
                    else "consume-or-expel-window-left", "--id", ids[1])
            checked(target)
        for ident in (ids if target == "side-by-side" else ids[:1]):
            checked(target)
            command("action", "set-window-width", "--id", ident, "50%" if target == "side-by-side" else "100%")
            checked(target)
        group["layout"] = target
        group.pop("pending_layout", None)
        group_store.write(state / "groups.json", groups)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ValueError("Group layout change interrupted; check the windows and choose Finish layout change") from exc


def run_locked(state):
    path = state / "spaces.json"
    with (state / "spaces.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        pins = json.loads(path.read_text()) if path.exists() else []
        if not isinstance(pins, list):
            raise ValueError("Invalid saved spaces; state was not changed")
        workspace, windows = snapshot()
        before = json.dumps(pins, sort_keys=True)
        socket = session_id()
        groups = group_store.load(state)
        before_groups = json.dumps(groups, sort_keys=True)
        for pin in pins:
            if "members" in pin:
                pair = group_windows(pin, workspace, windows, socket, groups)
                if pair and (pin.get("session") != socket or pin.get("window_ids") != [w["id"] for w in pair]):
                    pin["session"], pin["window_id"], pin["window_ids"] = socket, pair[0]["id"], [w["id"] for w in pair]
                continue
            window = resolve(pin, windows, socket)
            if window and (pin.get("session") != socket or pin.get("window_id") != window["id"]):
                pin["session"], pin["window_id"] = socket, window["id"]
        op = sys.argv[1] if len(sys.argv) > 1 else "refresh"
        arg = sys.argv[2] if len(sys.argv) > 2 else None
        if op == "repair-side-group":
            run(pins, op, arg, workspace, windows, socket, groups)
            workspace, windows = snapshot()
            result = run(pins, "refresh", None, workspace, windows, socket, groups)
        elif op in ("group-layout", "resume-group-layout"):
            change_group_layout(state, groups, pins, arg, workspace, windows, socket, op == "resume-group-layout")
            workspace, windows = snapshot()
            before_groups = json.dumps(groups, sort_keys=True)
            result = run(pins, "refresh", None, workspace, windows, socket, groups)
        else:
            result = run(pins, op, arg, workspace, windows, socket, groups)
        if before_groups != json.dumps(groups, sort_keys=True):
            try:
                group_store.write(state / "groups.json", groups)
            except OSError as exc:
                if len(sys.argv) > 1 and sys.argv[1] == "dissolve-group":
                    raise ValueError("Could not confirm Group update; if it remains saved, stack its apps and retry") from exc
                raise ValueError("Could not confirm Group update; check the Group state before retrying") from exc
        if len(sys.argv) > 1 and sys.argv[1] in ("dissolve-group", "open-group-member", "rejoin-group"):
            workspace, windows = snapshot()
        if before != json.dumps(pins, sort_keys=True):
            with tempfile.NamedTemporaryFile("w", dir=state, delete=False) as tmp:
                try:
                    json.dump(pins, tmp, ensure_ascii=False)
                    tmp.flush()
                    os.fsync(tmp.fileno())
                    os.replace(tmp.name, path)
                finally:
                    if os.path.exists(tmp.name):
                        os.unlink(tmp.name)
        ordered = sorted((w for w in windows if w.get("workspace_id") == workspace["id"]),
                         key=lambda w: (w.get("layout", {}).get("pos_in_scrolling_layout") or [99999, 0]))
        visible = [{"id": w["id"], "label": w.get("title") or w.get("app_id") or "Application",
                    "app_id": w.get("app_id") or "", "active": w["id"] == workspace.get("active_window_id")}
                   for w in ordered]
        print(json.dumps({"pins": result, "windows": visible, "spaces": display_spaces(workspace, windows, groups, pins),
                          "incomplete_groups": incomplete_groups(workspace, windows, groups, pins),
                          "action": sys.argv[1] if len(sys.argv) > 1 else "refresh"}))


def main():
    state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "flow"
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state / "session.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            run_locked(state)
        except MultipleDisplays:
            op = sys.argv[1] if len(sys.argv) > 1 else ""
            direction = sys.argv[2] if len(sys.argv) > 2 else ""
            if (op, direction) not in (("navigate", "left"), ("navigate", "right"),
                                       ("focus-member", "up"), ("focus-member", "down")):
                raise
            command("action", ("focus-column-" if op == "navigate" else "focus-window-") + direction)
            print(json.dumps({"action": op}))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
