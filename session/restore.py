#!/usr/bin/env python3
"""Explicit, best-effort recovery of one Flow workspace."""
import contextlib
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
import pin_store
import orphan_io
from launching import app_command


def state_path():
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "flow" / "session.json"


def pins_unchanged_or_rebound(before, after, session, windows, workspace=None, groups=()):
    if len(before) != len(after):
        return False
    for old, new in zip(before, after):
        if old == new:
            continue
        if old.get("display") is None and new.get("display") is not None:
            # Assigning an unassigned legacy pin to its display is not a user change.
            old = {**old, "display": new["display"]}
            if old == new:
                continue
        if "members" in old:
            excluded = ("session", "window_id", "window_ids", "last_focused_app")
            if ({k: v for k, v in old.items() if k not in excluded} != {k: v for k, v in new.items() if k not in excluded}
                    or not workspace or new.get("session") != session):
                return False
            candidate = {"group_id": old.get("group_id"), "members": old["members"], "session": None}
            pair = spaces.group_windows(candidate, workspace, windows, session, groups)
            if not pair or new.get("window_ids") != [w["id"] for w in pair] or new.get("window_id") not in new["window_ids"]:
                return False
            if old.get("session") == session and any(old.get(k) != new.get(k) for k in ("window_id", "window_ids")):
                return False
            if old.get("last_focused_app") != new.get("last_focused_app"):
                active = next((w for w in pair if w["id"] == workspace.get("active_window_id")), None)
                if not active or new.get("last_focused_app") != active.get("app_id"):
                    return False
            continue
        if old.get("session") == session or new.get("session") != session:
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
    # Focus tracking and window bindings change with ordinary navigation; anything else is a real edit.
    volatile = ("last_focused_app", "bound")
    for old, new in zip(before, after):
        if old == new:
            continue
        if {k: v for k, v in old.items() if k not in volatile} != {k: v for k, v in new.items() if k not in volatile}:
            return False
        if old.get("last_focused_app") == new.get("last_focused_app"):
            continue
        active = next((w for w in windows if w.get("id") == workspace.get("active_window_id")), None)
        if not active or new.get("last_focused_app") not in old.get("members", []) or active.get("app_id") != new.get("last_focused_app"):
            return False
    return True


def saved_order_matches(entries, restored, workspace, windows):
    """Whether every saved window is back in its saved column and row.

    Windows that are not in the saved session, such as apps opened at login, are spaces of their own: a column
    holding only such windows is left out, and the saved windows' columns count without it. A column that mixes
    them with saved windows is not the saved one."""
    current = [w for w in windows if w.get("workspace_id") == workspace["id"] and not w.get("is_floating")]
    saved_ids = {w["id"] for _, w in restored}
    if len(restored) != len(entries) or not saved_ids <= {w["id"] for w in current}:
        return False
    placed = [w for w in current if w.get("layout", {}).get("pos_in_scrolling_layout")]
    if len(placed) != len(current):
        return False  # a tiled window without a place: nothing can be confirmed
    kept = sorted({w["layout"]["pos_in_scrolling_layout"][0] for w in placed if w["id"] in saved_ids})
    if any(w["id"] not in saved_ids and w["layout"]["pos_in_scrolling_layout"][0] in kept for w in placed):
        return False
    renumbered = {column: index for index, column in enumerate(kept, 1)}
    rows = Counter()
    for entry, saved_window in restored:
        column = entry.get("column")
        if not isinstance(column, int) or isinstance(column, bool) or column < 1:
            return False
        rows[column] += 1
        live = next((w for w in placed if w["id"] == saved_window["id"]), None)
        if not live:
            return False
        live_column, live_row = live["layout"]["pos_in_scrolling_layout"]
        if [renumbered[live_column], live_row] != [column, rows[column]]:
            return False
    return True


@contextlib.contextmanager
def flow_locks(state, wait=True):
    """Flow's locks, as every action takes them: session.lock, then spaces.lock. Without ``wait``, a held
    session.lock raises BlockingIOError (every holder of spaces.lock holds session.lock first)."""
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state / "session.lock").open("a") as session, (state / "spaces.lock").open("a") as held:
        fcntl.flock(session, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        fcntl.flock(held, fcntl.LOCK_EX)
        yield


def save_locked(path, checked=False, if_changed=False):
    """Save session, under Flow's locks, which the caller holds. ``checked``: the automatic save, which pauses
    rather than save windows it could not recover, and writes only a changed snapshot (``if_changed``)."""
    registered = group_store.load(path.parent)
    pins = pin_store.read(path.parent / "spaces.json")[0]
    pinned_apps(pins)
    workspace, windows = spaces.snapshot()
    display = focused_display(workspace, path.parent)[0]
    dirs = spaces.data_dirs()
    if checked:
        entries = [{"app_id": w.get("app_id"), "column": (w.get("layout", {}).get("pos_in_scrolling_layout") or [None])[0]}
                   for w in windows if w.get("workspace_id") == workspace["id"] and not w.get("is_floating")]
        warnings = recovery_warnings(entries, dirs)
        if not entries or warnings:
            raise ValueError("Automatic saving paused; the saved session was preserved. " + "; ".join(warnings))
    before = path.read_bytes() if path.exists() else None
    count = save(path, workspace, windows, spaces.session_id(), pins, registered, display, if_changed=if_changed)
    warnings = recovery_warnings(json.loads(path.read_text())["windows"], dirs)
    return {"message": f"Saved {count} open windows. Save documents in each app before restarting." +
            (" " + "; ".join(warnings) if warnings else ""), "written": path.read_bytes() != before}


def automatic(op, path, wait=True):
    """An automatic-recovery step. Without ``wait``, recovery's or Flow's locks held elsewhere raise
    BlockingIOError (the resident controller saves this way, and tries again later)."""
    runtime = Path(os.environ["XDG_RUNTIME_DIR"])
    marker = runtime / ("flow-recovery-" + hashlib.sha256(spaces.session_id().encode()).hexdigest())
    with marker.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
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
        try:
            with flow_locks(path.parent, wait):
                return save_locked(path, checked=True, if_changed=True)
        except BlockingIOError:
            raise
        except (ValueError, KeyError, OSError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
            marker.write_text("blocked")
            return {"error": str(exc)}
        except Exception:  # noqa: BLE001 — a bug in saving pauses it, as a crashed save did
            marker.write_text("blocked")
            return {"error": "Automatic saving failed; the saved session was preserved."}


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
            or ("members" in pin and not group_store.valid_members(pin["members"])) for pin in pins):
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


def valid_side_pins(group, pins):
    claims = [p for p in pins if set(group["members"]) & set(p.get("members", [p.get("app_id")]))]
    return not claims or (len(claims) == 1 and claims[0].get("group_id") == group["id"]
                          and claims[0].get("members") == group["members"]
                          and claims[0].get("app_id") == group["members"][0])


def save(path, workspace, windows, session=None, pins=(), registered=None, display=None, if_changed=False):
    """Save the focused workspace's tiled windows and Groups; how many windows. With ``if_changed`` a snapshot
    that would be the same as the saved one is not written again."""
    side_groups = [g for g in registered or [] if g.get("layout") == "side-by-side"]
    if any(g.get("pending_layout") for g in registered or []):
        raise ValueError("Finish Group layout changes before saving; the snapshot was not replaced")
    for group in side_groups:
        candidate = {"group_id": group["id"], "members": group["members"], "session": None}
        if (not valid_side_pins(group, pins)
                or not spaces.group_windows(candidate, workspace, windows, session, registered)):
            raise ValueError("Check side-by-side Groups and pins before saving; the snapshot was not replaced")
    # Layers (floating windows) are temporary and never part of a saved session.
    current = [w for w in windows if w.get("workspace_id") == workspace["id"] and not w.get("is_floating")]
    current.sort(key=lambda w: w.get("layout", {}).get("pos_in_scrolling_layout") or [99999, 0])
    entries = [{"app_id": w.get("app_id"), "window_id": w["id"], "label": w.get("title") or w.get("app_id") or "Unknown application",
                "focused": w["id"] == workspace.get("active_window_id"),
                "column": ((w.get("layout", {}).get("pos_in_scrolling_layout") or [None])[0]
                           if not w.get("is_floating") else None)} for w in current]
    previous = spaces.saved_groups(path) if registered is None else registered
    old = json.loads(path.read_text()) if previous and path.exists() else {}
    # A Layer of a Group app is not another member candidate; only tiled windows count.
    live_counts = Counter(w.get("app_id") for w in windows if not w.get("is_floating"))
    for group in previous:
        apps = group["members"]
        if group in side_groups:
            continue
        if any(live_counts[app] > 1 or live_counts[app] != sum(e["app_id"] == app for e in entries) for app in apps):
            raise ValueError("Saved Group members are ambiguous or on another workspace; session was not replaced")
        if all(live_counts[app] == 1 for app in apps):
            if registered is not None:
                members = [e for e in entries if e["app_id"] in apps]
                column = members[0]["column"]
                if (column is None or any(e["column"] != column for e in members)
                        or sum(e["column"] == column for e in entries) != len(apps)):
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
            if (column is None or any(e["column"] != column for e in present)
                    or sum(e["column"] == column for e in entries) != len(present)):
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
    for group in side_groups:
        candidate = {"group_id": group["id"], "members": group["members"], "session": None}
        pair = spaces.group_windows(candidate, workspace, windows, session, registered)
        saved = dict(group)
        if spaces.measured_ratios(pair):
            group_store.set_ratios(saved, spaces.measured_ratios(pair))
        groups.append(saved)
    for members in columns.values():
        apps = [entry["app_id"] for entry in members]
        if not group_store.valid_members(apps) or not all(app_counts[app] == 1 for app in apps):
            continue
        matches = [g for g in previous if set(g["members"]) == set(apps)]
        if len(matches) == 1:
            groups.append({**matches[0], "members": apps})
        elif registered is None:
            groups.append({"id": uuid.uuid4().hex, "label": " + ".join(entry["label"] for entry in members), "members": apps})
    if side_groups and len(groups) != len(side_groups):
        raise ValueError("Mixed stacked and side-by-side Groups cannot yet be saved; the snapshot was not replaced")
    for pin in pins:
        matches = [g for g in previous if g["id"] == pin.get("group_id")]
        if len(matches) == 1 and not any(g["id"] == matches[0]["id"]
                                         and set(g["members"]) == set(matches[0]["members"]) for g in groups):
            raise ValueError("Rejoin or unpin the separated pinned Group before saving; session was not replaced")
    data = {"version": 1, "session": session, "windows": entries, "groups": groups}
    if display:
        data["display"] = display
    if not (if_changed and path.exists() and path.read_text() == json.dumps(data, ensure_ascii=False)):
        write_snapshot(path, data)  # as json.dump writes it, so an unchanged snapshot reads the same
    return len(current)


def restore(data, workspace, windows, dirs, launch, get_snapshot, action, wait=time.sleep, pinned=(), session=None,
            pinned_groups=(), registered=None, display=None):
    side_groups = [g for g in registered or [] if g.get("layout") == "side-by-side"]
    if (any(g.get("pending_layout") for g in registered or []) or
            any(not valid_side_pins(g, pinned_groups) or
                (set(g["members"]) & set(pinned) and not any(p.get("group_id") == g["id"] for p in pinned_groups))
                for g in side_groups)):
        raise ValueError("Session recovery cannot safely restore conflicting pins or a transitioning Group; keep its layout and resolve it manually")
    if data.get("version") != 1 or not isinstance(data.get("windows"), list):
        raise ValueError("Unsupported or invalid saved session")
    entries = data["windows"]
    saved_groups = data.get("groups", []) if isinstance(data.get("groups", []), list) else []
    if any(not isinstance(e, dict) or not isinstance(e.get("app_id"), (str, type(None))) for e in entries):
        raise ValueError("Saved session includes a window without an application ID")
    for group in side_groups:
        matching = [g for g in saved_groups if isinstance(g, dict) and g.get("id") == group["id"]
                    and g.get("members") == group["members"] and g.get("layout") == "side-by-side"]
        if len(matching) == 1 and not group_store.valid_ratios(matching[0]):
            raise ValueError("Invalid saved Group proportions; recovery stopped")
        pair = [[e for e in entries if e.get("app_id") == app] for app in group["members"]]
        if (len(matching) != 1 or any(len(found) != 1 for found in pair)
                or not isinstance(pair[0][0].get("column"), int) or isinstance(pair[0][0].get("column"), bool)
                or pair[0][0]["column"] < 1
                or any(found[0].get("column") != pair[0][0]["column"] + i for i, found in enumerate(pair))):
            raise ValueError("Saved side-by-side Group is incomplete or changed; session recovery stopped")
    ids = [e["app_id"] for e in entries]
    app_counts = Counter(ids)
    window_ids = Counter(e.get("window_id") for e in entries if isinstance(e.get("window_id"), int))
    columns = {}
    for entry in entries:
        column = entry.get("column")
        if isinstance(column, int) and not isinstance(column, bool) and column > 0:
            columns.setdefault(column, []).append(entry)
    groups = [group for group in columns.values() if len(group) > 1]
    if side_groups and groups:
        raise ValueError("Mixed stacked and side-by-side Groups require manual recovery")
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
        members = [next((w for w in live if w.get("id") == ident), None) for ident in member_ids]
        positions = [w.get("layout", {}).get("pos_in_scrolling_layout") if w else None for w in members]
        return (all(positions) and all(pos[0] == positions[0][0] for pos in positions)
                and all(a[1] < b[1] for a, b in zip(positions, positions[1:]))
                and {w["id"] for w in column_members(members[0], current, live)} == set(member_ids))

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
        if side_groups:
            current, live = checked_snapshot()
            if not saved_order_matches(entries, restored, current, live):
                report.append("Side-by-side Group order needs manual recovery")
            else:
                for group in side_groups:
                    candidate = {"group_id": group["id"], "members": group["members"], "session": None}
                    pair = spaces.group_windows(candidate, current, live, session or "", registered)
                    if not pair:
                        # Every saved window is in its saved order, so the apps are there: another window that is
                        # not in the session sits between them.
                        report.append("Side-by-side Group order needs manual recovery: another window is between "
                                      "its apps; choose Repair Group layout")
                        continue
                    ids = [w["id"] for w in pair]
                    active = current.get("active_window_id")
                    saved_group = next(g for g in saved_groups if g.get("id") == group["id"])
                    ratios = group_store.ratios(saved_group)
                    for index, ident in enumerate(ids):
                        current, live = checked_snapshot()
                        pair = spaces.group_windows(candidate, current, live, session or "", registered)
                        if not pair or [w["id"] for w in pair] != ids or current.get("active_window_id") != active:
                            report.append("Side-by-side Group changed during recovery; check manually")
                            break
                        action("action", "set-window-width", "--id", ident, f"{round(ratios[index] * 100, 2):g}%")
                    # Niri acknowledges width requests before the client commits its new size.
                    wait(0.2)
                    for attempt in range(11):
                        current, live = checked_snapshot()
                        pair = spaces.group_windows(candidate, current, live, session or "", registered)
                        if not pair or [w["id"] for w in pair] != ids or current.get("active_window_id") != active:
                            report.append("Side-by-side Group changed during recovery; check manually")
                            break
                        widths = spaces.measured_ratios(pair)
                        if not widths or all(abs(w / sum(widths) - r) <= 0.05 for w, r in zip(widths, ratios)):
                            break
                        if attempt == 10:
                            report.append("Application size constraints prevented recovery of Group proportions")
                        else:
                            wait(0.1)
        elif not groups:
            for index, (_, window) in enumerate(restored, 1):
                current, live = checked_snapshot()
                if any(w.get("workspace_id") == workspace_id and w.get("app_id") in pinned for w in live):
                    if not saved_order_matches(entries, restored, current, live):
                        report.append("Pinned spaces keep their positions; session order needs manual recovery")
                    break
                action("action", "focus-window", "--id", window["id"])
                current, live = checked_snapshot()
                if any(w.get("workspace_id") == workspace_id and w.get("app_id") in pinned for w in live):
                    # A pinned app appeared meanwhile (one opened at login, say): judged as at the top of the loop.
                    if not saved_order_matches(entries, restored, current, live):
                        report.append("Pinned spaces keep their positions; session order needs manual recovery")
                    break
                action("action", "move-column-to-index", index)
        else:
            group_entry_ids = {id(entry) for group in groups for entry in group}
            group_destinations = []
            groups_ok = True
            for group in groups:
                group_apps = [entry["app_id"] for entry in group]
                n = len(group_apps)
                valid = group_store.valid_members
                snapshot_matches = [g for g in saved_groups if isinstance(g, dict) and valid(g.get("members"))
                                    and set(g["members"]) == set(group_apps)]
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
                                      and valid(saved.get("members")) and set(saved["members"]) == set(group_apps)]
                pinned_group = (isinstance(pin.get("group_id"), str) and valid(members)
                                and set(members) == set(group_apps) and len(saved_matches) == len(membership_matches) == 1
                                and valid(saved_matches[0].get("members"))
                                and set(saved_matches[0]["members"]) == set(group_apps))
                pinned_member = any(app in pinned for app in group_apps)
                if (not valid(group_apps) or any(not app or app_counts[app] != 1 for app in group_apps)
                        or (pinned_member and not pinned_group)):
                    report.append("Group requires manual recovery (ambiguous, pinned, or unsupported size)")
                    groups_ok = False
                    continue
                workspace, live = checked_snapshot()
                found = []
                for app in group_apps:
                    matches = [w for w in live if w.get("app_id") == app]
                    if len(matches) != 1 or matches[0].get("workspace_id") != workspace["id"] or matches[0].get("is_floating"):
                        break
                    found.append(matches[0])
                if len(found) != n:
                    report.append("Group members unavailable or ambiguous; group not restored")
                    groups_ok = False
                    continue
                positions = [w.get("layout", {}).get("pos_in_scrolling_layout") for w in found]
                if any(not pos for pos in positions):
                    report.append("Group members are not tiled; group not restored")
                    groups_ok = False
                    continue
                member_ids = tuple(w["id"] for w in found)
                columns = [pos[0] for pos in positions]
                if len(set(columns)) == 1:
                    if not group_column_matches(member_ids, workspace, live):
                        report.append("Existing group differs from saved group; check manually")
                        groups_ok = False
                    else:
                        group_destinations.append((group[0]["column"], found[0]["id"], member_ids))
                    continue
                if not all(spaces.eligible(w, workspace, live) for w in found):
                    report.append("Group members are not single windows; group not restored")
                    groups_ok = False
                    continue
                if pinned_group:
                    forward = columns == list(range(columns[0], columns[0] + n))
                    backward = columns == list(range(columns[0], columns[0] - n, -1))
                    # Joined as Rejoin Group joins one (spaces.stack_columns): the columns after the Group close up
                    # as its apps join one column, in the same order, and pinned ones go back in pin order at the
                    # next refresh.
                    if not (forward or backward):
                        report.append("Pinned Group apps are apart or out of order; group not restored")
                        groups_ok = False
                        continue
                    try:
                        spaces.stack_columns(list(member_ids), "left" if forward else "right", workspace_id,
                                             "Group members moved unexpectedly; group not restored",
                                             act=action, look=checked_snapshot)
                    except spaces.ColumnsMoved as exc:  # anything else, such as a changed workspace, stops recovery
                        report.append(str(exc))
                        groups_ok = False
                        continue
                else:
                    moved_ok = True
                    for index in range(1, n):
                        first = next(w for w in live if w.get("id") == found[0]["id"])
                        current = next(w for w in live if w.get("id") == found[index]["id"])
                        first_col = first["layout"]["pos_in_scrolling_layout"][0]
                        current_col = current["layout"]["pos_in_scrolling_layout"][0]
                        # Taking a column from the left shifts the first member one place left.
                        target = first_col + index - (current_col < first_col)
                        if current_col != target:
                            action("action", "focus-window", "--id", found[index]["id"])
                            action("action", "move-column-to-index", target)
                            workspace, live = checked_snapshot()
                        placed = [next((w for w in live if w.get("id") == member["id"]), None) for member in found[:index + 1]]
                        if (not all(spaces.eligible(w, workspace, live) for w in placed)
                                or [w["layout"]["pos_in_scrolling_layout"][0] for w in placed]
                                != list(range(placed[0]["layout"]["pos_in_scrolling_layout"][0],
                                              placed[0]["layout"]["pos_in_scrolling_layout"][0] + index + 1))):
                            moved_ok = False
                            break
                    if not moved_ok:
                        report.append("Group members moved unexpectedly; group not restored")
                        groups_ok = False
                        continue
                    action("action", "focus-window", "--id", found[0]["id"])
                    for _ in range(n - 1):
                        action("action", "consume-window-into-column")
                workspace, live = checked_snapshot()
                if not group_column_matches(member_ids, workspace, live):
                    report.append("Group members moved unexpectedly; group not restored")
                    groups_ok = False
                    continue
                group_destinations.append((group[0]["column"], found[0]["id"], member_ids))

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
                        # The next refresh puts pinned spaces first, in pin order: an order it will bring back is saved.
                        if not (saved_order_matches(entries, restored, workspace, live) or saved_order_matches(
                                entries, restored, workspace,
                                spaces.pin_ordered(workspace, live, spaces.local_pins(pinned_groups, display),
                                                   session or "", registered or ()))):
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


def focused_display(workspace, state=None):
    """The focused workspace's display key and every connected display key, keyed as pins are."""
    displays = pin_store.read(state / "spaces.json")[1] if state else {}
    identities, _ = pin_store.identities(json.loads(spaces.command("-j", "outputs")), pin_store.shared_serials(displays))
    record = identities.get(workspace.get("output"))
    return (record["key"] if record else None), {r["key"] for r in identities.values()}


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
                    result = save_locked(path, checked=op == "auto-save-checked")
                    del result["written"]
                    print(json.dumps(result))
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
                    pins = pin_store.read(pins_path)[0]
                    pinned_apps(pins)
                    session = spaces.session_id()
                    display, connected = focused_display(workspace, path.parent)
                    if data.get("display") and data["display"] != display and data["display"] in connected:
                        raise ValueError("This session was saved on another connected display; "
                                         "focus that display to restore it")
        if op == "restore":
            def checked_state():
                with (path.parent / "session.lock").open("a") as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    with (path.parent / "spaces.lock").open("a") as spaces_lock:
                        fcntl.flock(spaces_lock, fcntl.LOCK_EX)
                        now_groups = group_store.load(path.parent)
                        now_pins = pin_store.read(pins_path)[0]
                        pinned_apps(now_pins)
                        current = spaces.snapshot()
                        if (path.read_bytes() != original_snapshot or not groups_unchanged_or_focused(registered, now_groups, *current)
                                or not pins_unchanged_or_rebound(pins, now_pins, session, current[1], current[0], now_groups)):
                            raise ValueError("Session, Groups or pinned spaces changed; session recovery stopped")
                        return current
            def launch(desktop):
                current, _ = checked_state()
                if current["id"] != workspace["id"]:
                    raise ValueError("Workspace changed; session recovery stopped")
                subprocess.run(app_command(["gio", "launch", str(desktop)]), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            report, count = restore(data, workspace, windows, dirs, launch, checked_state, spaces.command,
                                    pinned=pinned_apps(pins), session=session, pinned_groups=pins, registered=registered,
                                    display=display)
            print(json.dumps({"message": f"Restored {count} windows." + (" " + "; ".join(report) if report else "")}))
        return
    raise ValueError("Unknown recovery action")


if __name__ == "__main__":
    # A shell restart does not end this action, but it closes the pipes it prints to (session/orphan_io.py).
    kept = orphan_io.guard()
    code = 0
    try:
        main()
    except (ValueError, KeyError, OSError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"error": str(exc)}))
        code = 1
    finally:
        orphan_io.finish(kept)
    sys.exit(code)
