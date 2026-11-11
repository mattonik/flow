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
                "focused": w["id"] == workspace.get("active_window_id")} for w in current]
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


def restore(data, workspace, windows, dirs, launch, get_snapshot, action, wait=time.sleep):
    if data.get("version") != 1 or not isinstance(data.get("windows"), list):
        raise ValueError("Unsupported or invalid saved session")
    entries = data["windows"]
    if any(not isinstance(e, dict) or not isinstance(e.get("app_id"), (str, type(None))) for e in entries):
        raise ValueError("Saved session includes a window without an application ID")
    ids = [e["app_id"] for e in entries]
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
        if len(matches) != 1 or not spaces.eligible(matches[0], workspace, windows):
            report.append(f"{app_id}: window unavailable, on another workspace, or ambiguous")
            continue
        restored.append((entry, matches[0]))
    desired_focus = next((w["id"] for e, w in restored if e.get("focused")), original)
    try:
        for index, (_, window) in enumerate(restored, 1):
            action("action", "focus-window", "--id", window["id"])
            action("action", "move-column-to-index", index)
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
        report, count = restore(data, workspace, windows, dirs,
                                lambda desktop: subprocess.run(["gio", "launch", str(desktop)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
                                spaces.snapshot, spaces.command)
        print(json.dumps({"message": f"Restored {count} windows." + (" " + "; ".join(report) if report else "")}))
    else:
        raise ValueError("Unknown recovery action")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
