#!/usr/bin/env python3
"""Persistent Group identities, separate from explicit session snapshots."""
import json
import os


def sync_directory(directory):
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def mark_initialized(directory):
    fd = os.open(directory / "groups.initialized", os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    sync_directory(directory)


# Side-by-side members share one output; four keeps each usable on a 1280-pixel-wide display.
MAX_MEMBERS = 4


def valid_members(members):
    return (isinstance(members, list) and 2 <= len(members) <= MAX_MEMBERS
            and all(isinstance(app, str) and app for app in members) and len(set(members)) == len(members))


def _fraction(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and 0 < value < 1


def valid_ratios(group):
    """Side proportions: legacy ``side_ratio`` for two apps, ``side_ratios`` for larger Groups."""
    if "side_ratio" in group and (len(group["members"]) != 2 or not _fraction(group["side_ratio"])):
        return False
    ratios = group.get("side_ratios")
    return "side_ratios" not in group or (
        isinstance(ratios, list) and len(ratios) == len(group["members"]) and len(group["members"]) > 2
        and all(_fraction(r) for r in ratios) and abs(sum(ratios) - 1) <= 0.01)


def ratios(group):
    """Return one width fraction per member, in member order."""
    n = len(group["members"])
    if isinstance(group.get("side_ratios"), list) and len(group["side_ratios"]) == n:
        return list(group["side_ratios"])
    if n == 2 and "side_ratio" in group:
        return [group["side_ratio"], 1 - group["side_ratio"]]
    return [1 / n] * n


def set_ratios(group, values):
    total = sum(values)
    values = [v / total for v in values]
    group.pop("side_ratio", None)
    group.pop("side_ratios", None)
    if len(values) == 2:
        group["side_ratio"] = round(values[0], 4)
    else:
        group["side_ratios"] = [round(v, 4) for v in values]


def remap_ratios(group, members):
    """Carry saved proportions to a changed membership; new members get an even share."""
    had = "side_ratio" in group or "side_ratios" in group
    old = dict(zip(group["members"], ratios(group)))
    group["members"] = list(members)
    if had:
        set_ratios(group, [old.get(app, 1 / len(members)) for app in members])
    else:
        group.pop("side_ratio", None)
        group.pop("side_ratios", None)


def validate(data):
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("groups"), list):
        raise ValueError("Invalid saved Groups; state was not changed")
    groups = data["groups"]
    if any(not isinstance(g, dict) or not isinstance(g.get("id"), str) or not g["id"]
           or not isinstance(g.get("label"), str) or not valid_members(g.get("members"))
           or any(key in g and g[key] not in ("stack", "side-by-side")
                  for key in ("layout", "pending_layout"))
           or ("auto_layout" in g and not isinstance(g["auto_layout"], bool))
           or not valid_ratios(g)
           or ("last_focused_app" in g and g["last_focused_app"] not in g["members"])
           or ("pending_layout" in g and g["pending_layout"] == g.get("layout", "stack")) for g in groups):
        raise ValueError("Invalid saved Groups; state was not changed")
    ids, members = set(), set()
    for group in groups:
        if group["id"] in ids or members.intersection(group["members"]):
            raise ValueError("Ambiguous saved Groups; state was not changed")
        ids.add(group["id"])
        members.update(group["members"])
    return groups


def write(path, groups):
    import state_files  # here, not above: flow-spaces runs after every burst of Niri events and mostly only reads
    data = {"version": 1, "groups": groups}
    validate(data)
    state_files.replace_durably(path, json.dumps(data, ensure_ascii=False).encode(), pause="groups.write")


def load(directory):
    path = directory / "groups.json"
    marker = directory / "groups.initialized"
    if path.exists():
        result = validate(json.loads(path.read_text()))
        if not marker.exists():
            mark_initialized(directory)
        return result
    if marker.exists():
        raise ValueError("Group registry is missing; state was not changed")
    snapshot = directory / "session.json"
    if snapshot.exists():
        data = json.loads(snapshot.read_text())
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("windows"), list):
            raise ValueError("Invalid saved session; Groups were not migrated")
        groups = validate({"version": 1, "groups": data.get("groups", [])})
    else:
        groups = []
    write(path, groups)
    mark_initialized(directory)
    return groups


def rename(groups, group_id, label):
    label = label.strip()
    if not label or len(label) > 80:
        raise ValueError("Group name must be between 1 and 80 characters")
    matches = [group for group in groups if group["id"] == group_id]
    if len(matches) != 1:
        raise ValueError("Saved Group no longer exists or is ambiguous")
    matches[0]["label"] = label
