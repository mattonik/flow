#!/usr/bin/env python3
"""Persistent Group identities, separate from explicit session snapshots."""
import json
import os
import tempfile


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


def validate(data):
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("groups"), list):
        raise ValueError("Invalid saved Groups; state was not changed")
    groups = data["groups"]
    if any(not isinstance(g, dict) or not isinstance(g.get("id"), str) or not g["id"]
           or not isinstance(g.get("label"), str) or not isinstance(g.get("members"), list)
           or len(g["members"]) != 2 or not all(isinstance(a, str) and a for a in g["members"])
           or len(set(g["members"])) != 2 for g in groups):
        raise ValueError("Invalid saved Groups; state was not changed")
    ids, members = set(), set()
    for group in groups:
        if group["id"] in ids or members.intersection(group["members"]):
            raise ValueError("Ambiguous saved Groups; state was not changed")
        ids.add(group["id"])
        members.update(group["members"])
    return groups


def write(path, groups):
    data = {"version": 1, "groups": groups}
    validate(data)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as tmp:
        try:
            json.dump(data, tmp, ensure_ascii=False)
            tmp.flush()
            os.fsync(tmp.fileno())
            os.replace(tmp.name, path)
            sync_directory(path.parent)
        finally:
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)


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
