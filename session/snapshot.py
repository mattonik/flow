#!/usr/bin/env python3
"""A portable, semantic snapshot of one workspace, and the plan to import one (ADR-0012).

Pure functions only: nothing here reads Niri, writes state or opens a connection. ``restore.py`` runs
``export``, ``preview`` and ``import`` with them.
"""
import hashlib
import json
import re
import uuid

import groups as group_store

FORMAT = "flow-workspace-snapshot"
VERSION = 1
MAX_BYTES = 64 * 1024
MAX_SPACES = 32
NAME_LENGTH = 80
_DESKTOP_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,246}\.desktop")
_KEYS = {"snapshot": {"format", "version", "spaces"}, "space": {"apps", "group", "pin"},
         "group": {"name", "layout", "proportions"}}


def desktop_id(app_id):
    """The desktop-file ID recovery would open for a Niri app ID, or None when it cannot be one."""
    if not isinstance(app_id, str):
        return None
    candidate = app_id if app_id.endswith(".desktop") else app_id + ".desktop"
    return candidate if _DESKTOP_ID.fullmatch(candidate) else None


def app_id(desktop):
    """The Niri app ID a desktop-file ID's window is expected to have."""
    return desktop[:-len(".desktop")]


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _only(value, kind, where):
    unknown = sorted(set(value) - _KEYS[kind])
    if unknown:
        raise ValueError(f"{where} has a key this Flow does not know: {', '.join(map(str, unknown))}")


def validate(data):
    """Return ``data`` when it is a snapshot this Flow can read; otherwise raise ValueError saying why."""
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError("Not a Flow workspace snapshot")
    version = data.get("version")
    if not _integer(version) or version < 1:
        raise ValueError("The snapshot has no valid version")
    if version > VERSION:
        raise ValueError(f"This snapshot comes from a newer Flow (version {version}); update Flow to import it")
    _only(data, "snapshot", "The snapshot")
    spaces = data.get("spaces")
    if not isinstance(spaces, list) or not 1 <= len(spaces) <= MAX_SPACES:
        raise ValueError(f"A snapshot holds 1 to {MAX_SPACES} spaces")
    seen, ranks = set(), set()
    for number, space in enumerate(spaces, 1):
        where = f"Space {number}"
        if not isinstance(space, dict):
            raise ValueError(f"{where} is not a space")
        _only(space, "space", where)
        apps = space.get("apps")
        if not isinstance(apps, list) or not apps or any(not isinstance(a, str) or not _DESKTOP_ID.fullmatch(a)
                                                         for a in apps):
            raise ValueError(f"{where} needs its apps as desktop-file IDs")
        if seen.intersection(apps) or len(set(apps)) != len(apps):
            raise ValueError(f"{where} names an app that is already in the snapshot")
        seen.update(apps)
        group = space.get("group")
        if group is None and "group" in space:
            raise ValueError(f"{where} has an empty Group")
        if group is None and len(apps) != 1:
            raise ValueError(f"{where} has several apps but no Group")
        if group is not None:
            if not isinstance(group, dict):
                raise ValueError(f"{where} has an invalid Group")
            _only(group, "group", f"{where}'s Group")
            name = group.get("name")
            if not isinstance(name, str) or not name.strip() or len(name) > NAME_LENGTH:
                raise ValueError(f"{where}'s Group needs a name of 1 to {NAME_LENGTH} characters")
            if not group_store.valid_members(apps):
                raise ValueError(f"{where}'s Group needs 2 to {group_store.MAX_MEMBERS} apps")
            if group.get("layout") not in ("stack", "side-by-side"):
                raise ValueError(f"{where}'s Group layout is stack or side-by-side")
            if "proportions" in group:
                shares = group["proportions"]
                if (group["layout"] != "side-by-side" or not isinstance(shares, list) or len(shares) != len(apps)
                        or any(isinstance(s, bool) or not isinstance(s, (int, float)) or not 0 < s < 1
                               for s in shares) or abs(sum(shares) - 1) > 0.01):
                    raise ValueError(f"{where}'s Group proportions are one fraction per app, summing to 1, "
                                     "and only side by side")
        if "pin" in space:
            if not _integer(space["pin"]) or space["pin"] < 1 or space["pin"] in ranks:
                raise ValueError(f"{where}'s pin is a rank not used by another space")
            ranks.add(space["pin"])
    return data


def parse(raw):
    """A snapshot from a file's bytes."""
    if len(raw) > MAX_BYTES:
        raise ValueError(f"A snapshot is at most {MAX_BYTES // 1024} KiB")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Not a Flow workspace snapshot: it is not JSON") from exc
    return validate(data)


def dump(snapshot):
    return (json.dumps(validate(snapshot), ensure_ascii=False, indent=1) + "\n").encode()


def _proportions(shares):
    total = sum(shares)
    values = [round(s / total, 4) for s in shares]
    values[-1] = round(1 - sum(values[:-1]), 4)
    return values


def export(spaces, pins, groups, app_name=lambda app: None):
    """The snapshot of a workspace's spaces (``spaces.display_spaces``), and what it left out.

    ``pins`` are the display's pins in order and ``groups`` the registered Groups. Only desktop-file IDs,
    member order, layouts, proportions, pin order and names the user gave are kept: never titles,
    window IDs, Group IDs, sessions or displays. A Group the user never named was named after its
    windows' titles, so it is exported under its apps' names."""
    rank = {}
    for pin in pins:
        key = ("group", pin.get("group_id")) if "members" in pin else ("app", pin.get("app_id"))
        rank.setdefault(key, len(rank) + 1)
    out, left, seen = [], [], set()
    for space in spaces:
        members = [m.get("app_id") or None for m in space["members"]]
        ids = [desktop_id(app) for app in members]
        group = next((g for g in groups if g["id"] == space.get("group_id")), None) if space.get("group_id") else None
        if group:
            if not all(ids) or seen.intersection(ids):
                left.append("A Group whose apps have no desktop-file ID or are already exported")
                continue
            name = group["label"] if group.get("named") else " + ".join(app_name(app) or app for app in members)
            entry = {"apps": ids, "group": {"name": name[:NAME_LENGTH], "layout": space.get("group_layout") or "stack"}}
            if not group.get("named"):
                left.append("A Group's own name came from window titles; exported under its apps' names")
            if entry["group"]["layout"] == "side-by-side" and space.get("shares") and all(space["shares"]):
                entry["group"]["proportions"] = _proportions(space["shares"])
            if ("group", group["id"]) in rank:
                entry["pin"] = rank[("group", group["id"])]
            seen.update(ids)
            out.append(entry)
            continue
        if len(members) > 1:
            left.append("A stack that is not a Group: each app exported as a space of its own")
        for app, ident in zip(members, ids):
            if not app:
                left.append("A window without an application ID")
            elif not ident:
                left.append(f"{app}: its app ID is not a desktop-file ID")
            elif ident in seen:
                left.append(f"{app}: more than one window; exported once")
            else:
                seen.add(ident)
                entry = {"apps": [ident]}
                if ("app", app) in rank:
                    entry["pin"] = rank[("app", app)]
                out.append(entry)
    if not out:
        raise ValueError("Nothing to export: the workspace has no app with a desktop-file ID")
    if len(out) > MAX_SPACES:
        left.append(f"Spaces after the first {MAX_SPACES}")
        out = out[:MAX_SPACES]
    used = sorted(e["pin"] for e in out if "pin" in e)
    for entry in out:
        if "pin" in entry:
            entry["pin"] = used.index(entry["pin"]) + 1
    return validate({"format": FORMAT, "version": VERSION, "spaces": out}), left


def plan(snapshot, windows, groups, pins, installed, display):
    """What an import would do here, as a report; nothing is done.

    ``installed(desktop_id)`` says whether this machine has that desktop entry; ``windows`` are every
    window Niri has; ``pins`` every display's pins. ``display`` is the focused display:
    ``{"key", "label", "pins": its pin count, "connected": number of displays}``. Apps already open,
    pinned or in a Group here are left out, so an import never moves an existing window or changes an
    existing pin or Group."""
    open_apps = {w.get("app_id") for w in windows if w.get("app_id")}
    pinned = {app for pin in pins for app in pin.get("members", [pin.get("app_id")])}
    grouped = {app for g in groups for app in g["members"]}

    def reason(desktop):
        app = app_id(desktop)
        if not installed(desktop):
            return "not installed"
        if app in open_apps:
            return "already open here; its window stays where it is"
        if app in pinned:
            return "already pinned here; import does not change existing pins"
        if app in grouped:
            return "already in a Group here; import does not change existing Groups"
        return None

    opening, left = [], []
    for space in snapshot["spaces"]:
        apps, group = space["apps"], space.get("group")
        why = {d: reason(d) for d in apps}
        if group and not any(why.values()):
            item = {"apps": list(apps), "group": dict(group)}
            if group["layout"] == "side-by-side" and "proportions" not in group:
                item["group"]["proportions"] = [round(1 / len(apps), 4)] * len(apps)
            opening.append({**item, "pin": space.get("pin")})
            continue
        if group:
            missing = [d for d in apps if why[d]]
            left.append({"group": group["name"], "reason": "not created: " + ", ".join(missing) + " left out; "
                         + ("its other apps open on their own" if len(missing) < len(apps) else "none of its apps open")})
        for desktop in apps:
            if why[desktop]:
                left.append({"app": desktop, "reason": why[desktop]})
            else:
                opening.append({"apps": [desktop], "pin": None if group else space.get("pin")})
    if any(s.get("group", {}).get("layout") == "stack" for s in opening):
        for space in opening:
            group = space.get("group")
            if group and group["layout"] == "side-by-side":
                group["layout"] = "stack"
                group.pop("proportions", None)
                left.append({"group": group["name"], "reason": "opens as a stack: Flow cannot yet rebuild stacked "
                                                               "and side-by-side Groups together"})
    pin_report = []
    after = display["pins"]
    wanted = sorted((s for s in snapshot["spaces"] if "pin" in s), key=lambda s: s["pin"])
    for space in wanted:
        name = space["group"]["name"] if space.get("group") else space["apps"][0]
        if any(o.get("pin") == space["pin"] for o in opening):
            after += 1
            pin_report.append({"space": name, "position": after, "display": display["label"]})
        else:
            pin_report.append({"space": name, "position": None, "reason": "not pinned: the space does not open whole"})
    report = {"acted": False,
              "workspace": {"new": True, "display": display["label"], "displays_connected": display["connected"]},
              "open": opening, "left_out": left, "pins": pin_report}
    report["confirm"] = hashlib.sha256(json.dumps([report, display["key"]], sort_keys=True).encode()).hexdigest()[:12]
    return report


def restore_input(report):
    """Recovery's saved-session data for the report's spaces, columns from 1, and the Groups to register."""
    entries, created = [], []
    column = 0
    for space in report["open"]:
        apps = [app_id(d) for d in space["apps"]]
        group = space.get("group")
        side = bool(group) and group["layout"] == "side-by-side"
        for index, app in enumerate(apps):
            if index == 0 or side:
                column += 1
            entries.append({"app_id": app, "column": column, "focused": False, "label": app})
        if group:
            record = {"id": uuid.uuid4().hex, "label": group["name"].strip(), "members": apps, "named": True}
            if side:
                record["layout"] = "side-by-side"
                group_store.set_ratios(record, group["proportions"])
            created.append(record)
    return {"version": 1, "session": None, "windows": entries, "groups": created}, created
