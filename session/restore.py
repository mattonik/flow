#!/usr/bin/env python3
"""Explicit, best-effort recovery of one Flow workspace."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import spaces


def state_path():
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "flow" / "session.json"


def save(path, workspace, windows):
    current = [w for w in windows if w.get("workspace_id") == workspace["id"]]
    current.sort(key=lambda w: w.get("layout", {}).get("pos_in_scrolling_layout") or [99999, 0])
    entries = [{"app_id": w.get("app_id"), "label": w.get("title") or w.get("app_id") or "Unknown application",
                "focused": w["id"] == workspace.get("active_window_id"),
                "column": ((w.get("layout", {}).get("pos_in_scrolling_layout") or [None])[0]
                           if not w.get("is_floating") else None)} for w in current]
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as tmp:
        try:
            json.dump({"version": 1, "windows": entries}, tmp, ensure_ascii=False)
            tmp.flush()
            os.fsync(tmp.fileno())
            os.replace(tmp.name, path)
        finally:
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)
    return len(entries)


def desktop_file(app_id, dirs):
    # Match only an exact desktop ID; never interpret window titles as commands.
    if not app_id or "/" in app_id or app_id in (".", ".."):
        return None
    filename = app_id if app_id.endswith(".desktop") else app_id + ".desktop"
    return next((p for d in dirs if (p := Path(d) / "applications" / filename).is_file()), None)


def restore(data, workspace, windows, dirs, launch, get_snapshot, action, wait=time.sleep, pinned=()):
    if data.get("version") != 1 or not isinstance(data.get("windows"), list):
        raise ValueError("Unsupported or invalid saved session")
    entries = data["windows"]
    if any(not isinstance(e, dict) or not isinstance(e.get("app_id"), (str, type(None))) for e in entries):
        raise ValueError("Saved session includes a window without an application ID")
    ids = [e["app_id"] for e in entries]
    columns = {}
    for entry in entries:
        column = entry.get("column")
        if isinstance(column, int) and not isinstance(column, bool) and column > 0:
            columns.setdefault(column, []).append(entry)
    groups = [group for group in columns.values() if len(group) > 1]
    group_ids = {id(entry) for group in groups for entry in group}
    report, restored = [], []
    original = workspace.get("active_window_id")
    for entry in entries:
        app_id = entry["app_id"]
        if not app_id:
            report.append("Unknown application: reopen manually")
            continue
        if ids.count(app_id) != 1:
            report.append(f"{app_id}: multiple saved windows require manual recovery")
            continue
        matches = [w for w in windows if w.get("app_id") == app_id]
        if len(matches) > 1:
            report.append(f"{app_id}: multiple running windows require manual recovery")
            continue
        if not matches:
            desktop = desktop_file(app_id, dirs)
            if not desktop:
                report.append(f"{app_id}: no matching installed application")
                continue
            try:
                launch(desktop)
            except (OSError, subprocess.SubprocessError):
                report.append(f"{app_id}: launch failed")
                continue
            for _ in range(30):
                wait(0.5)
                workspace, windows = get_snapshot()
                matches = [w for w in windows if w.get("app_id") == app_id]
                if matches:
                    break
        valid_group_window = (id(entry) in group_ids and len(matches) == 1
                              and matches[0].get("workspace_id") == workspace["id"]
                              and not matches[0].get("is_floating")
                              and matches[0].get("layout", {}).get("pos_in_scrolling_layout"))
        if len(matches) != 1 or not (spaces.eligible(matches[0], workspace, windows) or valid_group_window):
            report.append(f"{app_id}: window unavailable, on another workspace, or ambiguous")
            continue
        restored.append((entry, matches[0]))
    desired_focus = next((w["id"] for e, w in restored if e.get("focused")), original)
    try:
        # shortcut: keep existing column order when restoring groups; add safe group-aware ordering later.
        if not groups:
            for index, (_, window) in enumerate(restored, 1):
                action("action", "focus-window", "--id", window["id"])
                action("action", "move-column-to-index", index)
        for group in groups:
            group_apps = [entry["app_id"] for entry in group]
            if len(group) != 2 or any(not app or ids.count(app) != 1 or app in pinned for app in group_apps):
                report.append("Group requires manual recovery (ambiguous, pinned, or more than two apps)")
                continue
            workspace, live = get_snapshot()
            pair = []
            for app in group_apps:
                matches = [w for w in live if w.get("app_id") == app]
                if len(matches) != 1 or matches[0].get("workspace_id") != workspace["id"] or matches[0].get("is_floating"):
                    break
                pair.append(matches[0])
            if len(pair) != 2:
                report.append("Group members unavailable or ambiguous; group not restored")
                continue
            positions = [w.get("layout", {}).get("pos_in_scrolling_layout") for w in pair]
            if any(not pos for pos in positions):
                report.append("Group members are not tiled; group not restored")
                continue
            if positions[0][0] == positions[1][0]:
                members = [w for w in live if w.get("workspace_id") == workspace["id"] and not w.get("is_floating")
                           and (w.get("layout", {}).get("pos_in_scrolling_layout") or [None])[0] == positions[0][0]]
                if len(members) != 2 or positions[0][1] >= positions[1][1]:
                    report.append("Existing group differs from saved group; check manually")
                continue
            if not all(spaces.eligible(w, workspace, live) for w in pair):
                report.append("Group members are not single windows; group not restored")
                continue
            if positions[1][0] != positions[0][0] + 1:
                target = positions[0][0] + (positions[1][0] > positions[0][0])
                action("action", "focus-window", "--id", pair[1]["id"])
                action("action", "move-column-to-index", target)
                workspace, live = get_snapshot()
                pair = [next((w for w in live if w.get("id") == member["id"]), None) for member in pair]
                if (not all(spaces.eligible(w, workspace, live) for w in pair)
                        or pair[1]["layout"]["pos_in_scrolling_layout"][0]
                        != pair[0]["layout"]["pos_in_scrolling_layout"][0] + 1):
                    report.append("Group members moved unexpectedly; group not restored")
                    continue
            action("action", "focus-window", "--id", pair[0]["id"])
            action("action", "consume-window-into-column")
    finally:
        if desired_focus:
            action("action", "focus-window", "--id", desired_focus)
    return report, len(restored)


def main():
    path = state_path()
    op = sys.argv[1] if len(sys.argv) > 1 else "status"
    if op == "status":
        print(json.dumps({"saved": path.exists()}))
        return
    workspace, windows = spaces.snapshot()
    if op == "save":
        count = save(path, workspace, windows)
        print(json.dumps({"message": f"Saved {count} open windows. Save documents in each app before restarting."}))
    elif op == "restore":
        data = json.loads(path.read_text())
        dirs = (os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share"), *os.environ.get("XDG_DATA_DIRS", "/run/current-system/sw/share:/usr/share").split(":"))
        pins_path = path.parent / "spaces.json"
        pins = json.loads(pins_path.read_text()) if pins_path.exists() else []
        if not isinstance(pins, list) or any(not isinstance(pin, dict) or not isinstance(pin.get("app_id"), str) for pin in pins):
            raise ValueError("Invalid pinned spaces; recovery was not started")
        report, count = restore(data, workspace, windows, dirs,
                                lambda desktop: subprocess.run(["gio", "launch", str(desktop)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
                                spaces.snapshot, spaces.command, pinned={pin["app_id"] for pin in pins})
        print(json.dumps({"message": f"Restored {count} windows." + (" " + "; ".join(report) if report else "")}))
    else:
        raise ValueError("Unknown recovery action")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
