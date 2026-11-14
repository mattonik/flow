#!/usr/bin/env python3
"""Explicit, best-effort recovery of one Flow workspace."""
import json
import os
import fcntl
import hashlib
from collections import Counter
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid
import spaces
import groups as group_store


def state_path():
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "flow" / "session.json"


def pins_unchanged_or_rebound(before, after, session, windows):
    if len(before) != len(after):
        return False
    for old, new in zip(before, after):
        if old == new:
            continue
        if "members" in old or old.get("session") == session or new.get("session") != session:
            return False
        if {k: v for k, v in old.items() if k not in ("session", "window_id")} != {
                k: v for k, v in new.items() if k not in ("session", "window_id")}:
            return False
        matches = [w for w in windows if w.get("app_id") == old.get("app_id")]
        if len(matches) != 1 or matches[0].get("id") != new.get("window_id"):
            return False
    return True


def groups_unchanged_or_focused(before, after, workspace, windows):
    if len(before) != len(after):
        return False
    for old, new in zip(before, after):
        if old == new:
            continue
        if {k: v for k, v in old.items() if k != "last_focused_app"} != {
                k: v for k, v in new.items() if k != "last_focused_app"}:
            return False
        active = next((w for w in windows if w.get("id") == workspace.get("active_window_id")), None)
        if not active or new.get("last_focused_app") not in old.get("members", []) or active.get("app_id") != new.get("last_focused_app"):
            return False
    return True


def saved_order_matches(entries, restored, workspace, windows):
    current = [w for w in windows if w.get("workspace_id") == workspace["id"]]
    if len(restored) != len(entries) or {w["id"] for _, w in restored} != {w["id"] for w in current}:
        return False
    rows = Counter()
    for entry, saved_window in restored:
        column = entry.get("column")
        if not isinstance(column, int) or isinstance(column, bool) or column < 1:
            return False
        rows[column] += 1
        live = next((w for w in current if w["id"] == saved_window["id"]), None)
        if not live or live.get("is_floating") or live.get("layout", {}).get("pos_in_scrolling_layout") != [column, rows[column]]:
            return False
    return True


def automatic(op, path):
    runtime = Path(os.environ["XDG_RUNTIME_DIR"])
    marker = runtime / ("flow-recovery-" + hashlib.sha256(spaces.session_id().encode()).hexdigest())
    with marker.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = marker.read_text() if marker.exists() else ""
        if op == "auto-pause":
            marker.write_text("blocked")
            return {"message": "Save session to start automatic recovery."}
        if op == "auto-ready":
            marker.write_text("ready")
            return {"message": "Automatic session recovery is ready."}
        if op == "auto-start":
            if state:
                return {"message": "Automatic session recovery is ready."} if state == "ready" else {"error": "Automatic saving is paused; review and save the session."}
            marker.write_text("blocked")
            if path.exists():
                result = subprocess.run([sys.executable, __file__, "restore"], capture_output=True, text=True)
                try:
                    output = json.loads(result.stdout)
                except json.JSONDecodeError:
                    return {"error": "Automatic recovery failed; the saved session was preserved."}
                message = output.get("message", "")
                if result.returncode or not (message.startswith("Restored ") and message.endswith(" windows.")):
                    return {"error": (output.get("error") or message or "Automatic recovery failed") + " Automatic saving is paused; review and save the session."}
            marker.write_text("ready")
            return {"message": "Automatic session recovery is ready."}
        if state != "ready":
            return {"error": "Automatic saving is paused; review and save the session."}
        workspace, windows = spaces.snapshot()
        if not any(w.get("workspace_id") == workspace["id"] for w in windows):
            return {"message": "Automatic saving skipped an empty workspace."}
        result = subprocess.run([sys.executable, __file__, "auto-save-checked"], capture_output=True, text=True)
        try:
            output = json.loads(result.stdout)
        except json.JSONDecodeError:
            marker.write_text("blocked")
            return {"error": "Automatic saving failed; the saved session was preserved."}
        if result.returncode or output.get("error"):
            marker.write_text("blocked")
        return output


def write_snapshot(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as tmp:
        try:
            json.dump(data, tmp, ensure_ascii=False)
            tmp.flush()
            os.fsync(tmp.fileno())
            os.replace(tmp.name, path)
        finally:
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)


def rename_group(path, group_id, label):
    label = label.strip()
    if not label or len(label) > 80:
        raise ValueError("Group name must be between 1 and 80 characters")
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("windows"), list) or not isinstance(data.get("groups"), list):
        raise ValueError("Invalid saved session")
    matches = [g for g in data["groups"] if isinstance(g, dict) and g.get("id") == group_id]
    if len(matches) != 1:
        raise ValueError("Saved Group no longer exists or is ambiguous")
    matches[0]["label"] = label
    write_snapshot(path, data)


def pinned_apps(pins):
    if not isinstance(pins, list) or any(not isinstance(pin, dict) or not isinstance(pin.get("app_id"), str)
            or ("members" in pin and (not isinstance(pin["members"], list)
                or len(pin["members"]) != 2 or not all(isinstance(app, str) and app for app in pin["members"])
                or len(set(pin["members"])) != 2)) for pin in pins):
        raise ValueError("Invalid pinned spaces; recovery was not started")
    return {app for pin in pins for app in pin.get("members", [pin["app_id"]])}


def recovery_warnings(entries, dirs):
    counts = Counter(entry.get("app_id") for entry in entries)
    warnings = []
    if any(not isinstance(app, str) or not app for app in counts):
        warnings.append("Windows without an application ID need manual recovery")
    for app in sorted(app for app in counts if isinstance(app, str) and app):
        if counts[app] > 1:
            warnings.append(f"{app}: multiple windows need manual recovery after restart")
        if not spaces.desktop_file(app, dirs):
            warnings.append(f"{app}: no matching installed application; reopen manually")
    if any(entry.get("column") is None for entry in entries):
        warnings.append("Floating or untiled windows need manual recovery")
    return warnings


def save(path, workspace, windows, session=None, pins=(), registered=None):
    if registered is not None and any(group.get("layout") == "side-by-side" or group.get("pending_layout")
                                      for group in registered):
        raise ValueError("Stack side-by-side Groups and finish layout changes before saving the session; the snapshot was not replaced")
    current = [w for w in windows if w.get("workspace_id") == workspace["id"]]
    current.sort(key=lambda w: w.get("layout", {}).get("pos_in_scrolling_layout") or [99999, 0])
    entries = [{"app_id": w.get("app_id"), "window_id": w["id"], "label": w.get("title") or w.get("app_id") or "Unknown application",
                "focused": w["id"] == workspace.get("active_window_id"),
                "column": ((w.get("layout", {}).get("pos_in_scrolling_layout") or [None])[0]
                           if not w.get("is_floating") else None)} for w in current]
    previous = spaces.saved_groups(path) if registered is None else registered
    old = json.loads(path.read_text()) if previous and path.exists() else {}
    live_counts = Counter(w.get("app_id") for w in windows)
    for group in previous:
        apps = group["members"]
        if any(live_counts[app] > 1 or live_counts[app] != sum(e["app_id"] == app for e in entries) for app in apps):
            raise ValueError("Saved Group members are ambiguous or on another workspace; session was not replaced")
        if all(live_counts[app] == 1 for app in apps):
            if registered is not None:
                pair = [e for e in entries if e["app_id"] in apps]
                column = pair[0]["column"]
                if column is None or pair[1]["column"] != column or sum(e["column"] == column for e in entries) != 2:
                    raise ValueError("Rejoin or dissolve the separated Group before saving; session was not replaced")
            continue
        missing = [app for app in apps if not live_counts[app]]
        old_entries = old.get("windows", [])
        if registered is not None and any(sum(isinstance(e, dict) and e.get("app_id") == app for e in old_entries) != 1 for app in missing):
            if any(pin.get("group_id") == group["id"] for pin in pins):
                raise ValueError("Reopen the pinned Group before saving; session was not replaced")
            continue
        present = [e for e in entries if e["app_id"] in apps]
        if present:
            column = present[0]["column"]
            if column is None or sum(e["column"] == column for e in entries) != 1:
                raise ValueError("A saved Group member is in another stack; session was not replaced")
        else:
            column = max((e["column"] for e in entries if e["column"] is not None), default=0) + 1
        for app in apps:
            if any(e["app_id"] == app for e in present):
                continue
            matches = [e for e in old_entries if isinstance(e, dict) and e.get("app_id") == app]
            if len(matches) != 1:
                raise ValueError("Saved Group membership is ambiguous; session was not replaced")
            entries.append({**matches[0], "column": column, "window_id": None, "focused": False})
        # Preserve the saved order so the recovery routine can reconstruct the stack.
        order = {app: index for index, app in enumerate(apps)}
        entries.sort(key=lambda e: (e["column"] if e["column"] is not None else 99999,
                                    order.get(e["app_id"], 0)))
    app_counts = Counter(e["app_id"] for e in entries)
    columns = {}
    for entry in entries:
        if entry["column"] is not None:
            columns.setdefault(entry["column"], []).append(entry)
    groups = []
    for members in columns.values():
        apps = [entry["app_id"] for entry in members]
        if len(apps) != 2 or not all(isinstance(app, str) and app and app_counts[app] == 1 for app in apps):
            continue
        matches = [g for g in previous if set(g["members"]) == set(apps)]
        if len(matches) == 1:
            groups.append({**matches[0], "members": apps})
        elif registered is None:
            groups.append({"id": uuid.uuid4().hex, "label": " + ".join(entry["label"] for entry in members), "members": apps})
    for pin in pins:
        matches = [g for g in previous if g["id"] == pin.get("group_id")]
        if len(matches) == 1 and not any(g["id"] == matches[0]["id"]
                                         and set(g["members"]) == set(matches[0]["members"]) for g in groups):
            raise ValueError("Rejoin or unpin the separated pinned Group before saving; session was not replaced")
    write_snapshot(path, {"version": 1, "session": session, "windows": entries, "groups": groups})
    return len(current)


def restore(data, workspace, windows, dirs, launch, get_snapshot, action, wait=time.sleep, pinned=(), session=None,
            pinned_groups=(), registered=None):
    if registered is not None and any(group.get("layout") == "side-by-side" or group.get("pending_layout")
                                      for group in registered):
        raise ValueError("Session recovery cannot safely restore a side-by-side or transitioning Group yet; keep its layout and resolve it manually")
    if data.get("version") != 1 or not isinstance(data.get("windows"), list):
        raise ValueError("Unsupported or invalid saved session")
    entries = data["windows"]
    saved_groups = data.get("groups", []) if isinstance(data.get("groups", []), list) else []
    if any(not isinstance(e, dict) or not isinstance(e.get("app_id"), (str, type(None))) for e in entries):
        raise ValueError("Saved session includes a window without an application ID")
    ids = [e["app_id"] for e in entries]
    app_counts = Counter(ids)
    window_ids = Counter(e.get("window_id") for e in entries if isinstance(e.get("window_id"), int))
    columns = {}
    for entry in entries:
        column = entry.get("column")
        if isinstance(column, int) and not isinstance(column, bool) and column > 0:
            columns.setdefault(column, []).append(entry)
    groups = [group for group in columns.values() if len(group) > 1]
    group_ids = {id(entry) for group in groups for entry in group}
    report, restored = [], []
    original = workspace.get("active_window_id")
    workspace_id = workspace["id"]
    workspace_changed = False

    def checked_snapshot():
        nonlocal workspace_changed
        current, live = get_snapshot()
        if current["id"] != workspace_id:
            workspace_changed = True
            raise ValueError("Workspace changed; session recovery stopped")
        return current, live

    def column_members(window, current, live):
        position = window and window.get("layout", {}).get("pos_in_scrolling_layout")
        if not position:
            return []
        return [w for w in live if w.get("workspace_id") == current["id"] and not w.get("is_floating")
                and (w.get("layout", {}).get("pos_in_scrolling_layout") or [None])[0] == position[0]]

    def group_column_matches(member_ids, current, live):
        pair = [next((w for w in live if w.get("id") == ident), None) for ident in member_ids]
        positions = [w.get("layout", {}).get("pos_in_scrolling_layout") if w else None for w in pair]
        return (all(positions) and positions[0][0] == positions[1][0] and positions[0][1] < positions[1][1]
                and {w["id"] for w in column_members(pair[0], current, live)} == set(member_ids))

    same_session = bool(session and data.get("session") == session)
    for entry in entries:
        app_id = entry["app_id"]
        if not app_id:
            report.append("Unknown application: reopen manually")
            continue
        window_id = entry.get("window_id")
        exact = [w for w in windows if same_session and isinstance(window_id, int) and not isinstance(window_id, bool)
                 and window_ids[window_id] == 1
                 and w.get("id") == window_id and w.get("app_id") == app_id]
        if app_counts[app_id] != 1 and len(exact) != 1:
            report.append(f"{app_id}: multiple saved windows require manual recovery")
            continue
        matches = exact or [w for w in windows if w.get("app_id") == app_id]
        if len(matches) > 1:
            report.append(f"{app_id}: multiple running windows require manual recovery")
            continue
        if not matches:
            desktop = spaces.desktop_file(app_id, dirs)
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
                workspace, windows = checked_snapshot()
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
        if not groups:
            for index, (_, window) in enumerate(restored, 1):
                current, live = checked_snapshot()
                if any(w.get("workspace_id") == workspace_id and w.get("app_id") in pinned for w in live):
                    if not saved_order_matches(entries, restored, current, live):
                        report.append("Pinned spaces keep their positions; session order needs manual recovery")
                    break
                action("action", "focus-window", "--id", window["id"])
                current, live = checked_snapshot()
                if any(w.get("workspace_id") == workspace_id and w.get("app_id") in pinned for w in live):
                    report.append("Pinned spaces keep their positions; session order needs manual recovery")
                    break
                action("action", "move-column-to-index", index)
        else:
            group_entry_ids = {id(entry) for group in groups for entry in group}
            group_destinations = []
            groups_ok = True
            for group in groups:
                group_apps = [entry["app_id"] for entry in group]
                snapshot_matches = [g for g in saved_groups if isinstance(g, dict) and isinstance(g.get("members"), list)
                                    and len(g["members"]) == 2 and set(g["members"]) == set(group_apps)]
                if registered is not None and snapshot_matches:
                    live_matches = [g for g in registered if g.get("id") == snapshot_matches[0].get("id")
                                    and set(g.get("members", [])) == set(group_apps)]
                    if len(snapshot_matches) != 1 or len(live_matches) != 1:
                        report.append("Saved Group was dissolved or changed; group not restored")
                        groups_ok = False
                        continue
                claiming_pins = [pin for pin in pinned_groups if isinstance(pin, dict)
                                 and any(app in pin.get("members", [pin.get("app_id")]) for app in group_apps)]
                pin = claiming_pins[0] if len(claiming_pins) == 1 else {}
                members = pin.get("members")
                saved_matches = [saved for saved in saved_groups if isinstance(saved, dict)
                                 and saved.get("id") == pin.get("group_id")]
                membership_matches = [saved for saved in saved_groups if isinstance(saved, dict)
                                      and isinstance(saved.get("members"), list) and len(saved["members"]) == 2
                                      and all(isinstance(app, str) and app for app in saved["members"])
                                      and set(saved["members"]) == set(group_apps)]
                pinned_group = (isinstance(pin.get("group_id"), str) and isinstance(members, list)
                                and len(members) == len(group_apps) == 2
                                and all(isinstance(app, str) and app for app in members)
                                and set(members) == set(group_apps) and len(saved_matches) == len(membership_matches) == 1
                                and isinstance(saved_matches[0].get("members"), list)
                                and len(saved_matches[0]["members"]) == 2
                                and all(isinstance(app, str) and app for app in saved_matches[0]["members"])
                                and set(saved_matches[0]["members"]) == set(group_apps))
                pinned_member = any(app in pinned for app in group_apps)
                if (len(group) != 2 or any(not app or app_counts[app] != 1 for app in group_apps)
                        or (pinned_member and not pinned_group)):
                    report.append("Group requires manual recovery (ambiguous, pinned, or more than two apps)")
                    groups_ok = False
                    continue
                workspace, live = checked_snapshot()
                pair = []
                for app in group_apps:
                    matches = [w for w in live if w.get("app_id") == app]
                    if len(matches) != 1 or matches[0].get("workspace_id") != workspace["id"] or matches[0].get("is_floating"):
                        break
                    pair.append(matches[0])
                if len(pair) != 2:
                    report.append("Group members unavailable or ambiguous; group not restored")
                    groups_ok = False
                    continue
                positions = [w.get("layout", {}).get("pos_in_scrolling_layout") for w in pair]
                if any(not pos for pos in positions):
                    report.append("Group members are not tiled; group not restored")
                    groups_ok = False
                    continue
                member_ids = tuple(w["id"] for w in pair)
                if positions[0][0] == positions[1][0]:
                    if not group_column_matches(member_ids, workspace, live):
                        report.append("Existing group differs from saved group; check manually")
                        groups_ok = False
                    else:
                        group_destinations.append((group[0]["column"], pair[0]["id"], member_ids))
                    continue
                if pinned_group:
                    left, right = sorted(pos[0] for pos in positions)
                    live_columns = [w.get("layout", {}).get("pos_in_scrolling_layout")
                                    for w in live if w.get("workspace_id") == workspace_id and not w.get("is_floating")]
                    if right != left + 1 or any(pos and pos[0] > right for pos in live_columns):
                        report.append("Pinned Group cannot be restored without shifting other spaces")
                        groups_ok = False
                        continue
                if not all(spaces.eligible(w, workspace, live) for w in pair):
                    report.append("Group members are not single windows; group not restored")
                    groups_ok = False
                    continue
                if not pinned_group and positions[1][0] != positions[0][0] + 1:
                    target = positions[0][0] + (positions[1][0] > positions[0][0])
                    action("action", "focus-window", "--id", pair[1]["id"])
                    action("action", "move-column-to-index", target)
                    workspace, live = checked_snapshot()
                    pair = [next((w for w in live if w.get("id") == member["id"]), None) for member in pair]
                    if (not all(spaces.eligible(w, workspace, live) for w in pair)
                            or pair[1]["layout"]["pos_in_scrolling_layout"][0]
                            != pair[0]["layout"]["pos_in_scrolling_layout"][0] + 1):
                        report.append("Group members moved unexpectedly; group not restored")
                        groups_ok = False
                        continue
                if pinned_group:
                    direction = "left" if positions[1][0] > positions[0][0] else "right"
                    action("action", "consume-or-expel-window-" + direction, "--id", pair[1]["id"])
                else:
                    action("action", "focus-window", "--id", pair[0]["id"])
                    action("action", "consume-window-into-column")
                workspace, live = checked_snapshot()
                if not group_column_matches(member_ids, workspace, live):
                    report.append("Group members moved unexpectedly; group not restored")
                    groups_ok = False
                    continue
                group_destinations.append((group[0]["column"], pair[0]["id"], member_ids))

            if groups_ok and len(restored) != len(entries):
                report.append("Session is incomplete; saved ordering needs manual recovery")
            elif groups_ok and any(not isinstance(entry.get("column"), int)
                                   or isinstance(entry.get("column"), bool) or entry["column"] <= 0
                                   for entry in entries):
                report.append("Saved columns are incomplete; session ordering needs manual recovery")
            elif groups_ok:
                destinations = group_destinations + [
                    (entry["column"], window["id"], None) for entry, window in restored
                    if id(entry) not in group_entry_ids
                ]
                destinations.sort(key=lambda item: item[0])
                if destinations:
                    workspace, live = checked_snapshot()
                    if any(w.get("workspace_id") == workspace_id and w.get("app_id") in pinned for w in live):
                        if not saved_order_matches(entries, restored, workspace, live):
                            report.append("Pinned spaces keep their positions; session order needs manual recovery")
                        destinations = []
                    else:
                        valid = all(group_column_matches(ids, workspace, live) if ids else
                                    spaces.eligible(next((w for w in live if w.get("id") == ident), None), workspace, live)
                                    for _, ident, ids in destinations)
                        if not valid:
                            report.append("Saved destinations changed during recovery; session order needs manual recovery")
                            destinations = []
                if destinations:
                    for target, (_, ident, member_ids) in enumerate(destinations, 1):
                        workspace, live = checked_snapshot()
                        window = next((w for w in live if w.get("id") == ident), None)
                        valid = group_column_matches(member_ids, workspace, live) if member_ids else spaces.eligible(window, workspace, live)
                        if not valid:
                            report.append("Saved destinations changed during recovery; remaining order needs manual recovery")
                            break
                        position = window.get("layout", {}).get("pos_in_scrolling_layout")
                        if position[0] != target:
                            action("action", "focus-window", "--id", ident)
                            workspace, live = checked_snapshot()
                            window = next((w for w in live if w.get("id") == ident), None)
                            valid = group_column_matches(member_ids, workspace, live) if member_ids else spaces.eligible(window, workspace, live)
                            if not valid:
                                report.append("Saved destinations changed during recovery; remaining order needs manual recovery")
                                break
                            action("action", "move-column-to-index", target)
    finally:
        if desired_focus and not workspace_changed:
            checked_snapshot()
            action("action", "focus-window", "--id", desired_focus)
    return report, len(restored)


def main():
    path = state_path()
    op = sys.argv[1] if len(sys.argv) > 1 else "status"
    if op in ("auto-start", "auto-save", "auto-ready", "auto-pause"):
        print(json.dumps(automatic(op, path)))
        return
    if op == "status":
        print(json.dumps({"saved": path.exists()}))
        return
    if op in ("save", "auto-save-checked", "rename-group", "restore"):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (path.parent / "session.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with (path.parent / "spaces.lock").open("a") as spaces_lock:
                fcntl.flock(spaces_lock, fcntl.LOCK_EX)
                registered = group_store.load(path.parent)
                if op in ("save", "auto-save-checked"):
                    pins_path = path.parent / "spaces.json"
                    pins = json.loads(pins_path.read_text()) if pins_path.exists() else []
                    pinned_apps(pins)
                    workspace, windows = spaces.snapshot()
                    dirs = (os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share"), *os.environ.get("XDG_DATA_DIRS", "/run/current-system/sw/share:/usr/share").split(":"))
                    if op == "auto-save-checked":
                        entries = [{"app_id": w.get("app_id"), "column": None if w.get("is_floating") else (w.get("layout", {}).get("pos_in_scrolling_layout") or [None])[0]} for w in windows if w.get("workspace_id") == workspace["id"]]
                        warnings = recovery_warnings(entries, dirs)
                        if not entries or warnings:
                            raise ValueError("Automatic saving paused; the saved session was preserved. " + "; ".join(warnings))
                    count = save(path, workspace, windows, spaces.session_id(), pins, registered)
                    warnings = recovery_warnings(json.loads(path.read_text())["windows"], dirs)
                    print(json.dumps({"message": f"Saved {count} open windows. Save documents in each app before restarting." +
                                      (" " + "; ".join(warnings) if warnings else "")}))
                elif op == "rename-group":
                    if len(sys.argv) != 4:
                        raise ValueError("Group name and ID are required")
                    group_store.rename(registered, sys.argv[2], sys.argv[3])
                    group_store.write(path.parent / "groups.json", registered)
                    print(json.dumps({"message": "Group renamed"}))
                else:
                    workspace, windows = spaces.snapshot()
                    original_snapshot = path.read_bytes()
                    data = json.loads(original_snapshot)
                    dirs = (os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share"), *os.environ.get("XDG_DATA_DIRS", "/run/current-system/sw/share:/usr/share").split(":"))
                    pins_path = path.parent / "spaces.json"
                    pins = json.loads(pins_path.read_text()) if pins_path.exists() else []
                    pinned_apps(pins)
                    session = spaces.session_id()
        if op == "restore":
            def checked_state():
                with (path.parent / "session.lock").open("a") as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    with (path.parent / "spaces.lock").open("a") as spaces_lock:
                        fcntl.flock(spaces_lock, fcntl.LOCK_EX)
                        now_groups = group_store.load(path.parent)
                        now_pins = json.loads(pins_path.read_text()) if pins_path.exists() else []
                        pinned_apps(now_pins)
                        current = spaces.snapshot()
                        if (path.read_bytes() != original_snapshot or not groups_unchanged_or_focused(registered, now_groups, *current)
                                or not pins_unchanged_or_rebound(pins, now_pins, session, current[1])):
                            raise ValueError("Session, Groups or pinned spaces changed; session recovery stopped")
                        return current
            def launch(desktop):
                current, _ = checked_state()
                if current["id"] != workspace["id"]:
                    raise ValueError("Workspace changed; session recovery stopped")
                subprocess.run(["gio", "launch", str(desktop)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            report, count = restore(data, workspace, windows, dirs, launch, checked_state, spaces.command,
                                    pinned=pinned_apps(pins), session=session, pinned_groups=pins, registered=registered)
            print(json.dumps({"message": f"Restored {count} windows." + (" " + "; ".join(report) if report else "")}))
        return
    raise ValueError("Unknown recovery action")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
