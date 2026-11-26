#!/usr/bin/env python3
"""Explicit, best-effort recovery of one Flow workspace."""
import contextlib
import json
import os
import re
import fcntl
import hashlib
from collections import Counter
from pathlib import Path
import subprocess
import sys
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


def order_after_pins(entries, restored, workspace, windows, pins, socket, groups=()):
    """Whether the live row is the saved one, or will be once the next refresh puts pinned spaces first.

    The refresh moves pinned spaces to the front, in pin order, whenever it runs: before recovery looks, or after.
    So both rows are compared as it leaves them. Pins in another order than the saved session (reordered since the
    last save) then keep their new order, and the other windows follow them in their saved order."""
    if saved_order_matches(entries, restored, workspace, windows):
        return True
    rows, saved = Counter(), []
    for entry, window in restored:
        column = entry.get("column")
        if not isinstance(column, int) or isinstance(column, bool) or column < 1:
            return False
        rows[column] += 1
        saved.append({**window, "workspace_id": workspace["id"], "is_floating": False,
                      "layout": {**window.get("layout", {}), "pos_in_scrolling_layout": [column, rows[column]]}})
    columns = {w["id"]: w["layout"]["pos_in_scrolling_layout"][0]
               for w in spaces.pin_ordered(workspace, saved, pins, socket, groups)}
    moved = [({**entry, "column": columns[window["id"]]}, window) for entry, window in restored]
    return saved_order_matches([entry for entry, _ in moved], moved, workspace,
                               spaces.pin_ordered(workspace, windows, pins, socket, groups))


@contextlib.contextmanager
def flow_locks(state, wait=True):
    """Flow's locks, as every action takes them: session.lock, then spaces.lock. Without ``wait``, a held
    session.lock raises BlockingIOError (every holder of spaces.lock holds session.lock first)."""
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state / "session.lock").open("a") as session, (state / "spaces.lock").open("a") as held:
        fcntl.flock(session, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        fcntl.flock(held, fcntl.LOCK_EX)
        yield


class Refused(ValueError):
    """The automatic save will not save what is there: windows it could not recover, a Group mid-change, or a
    Niri it could not read (no focused workspace while displays come and go, no answer)."""


def save_locked(path, checked=False, if_changed=False, reading=None):
    """Save session, under Flow's locks, which the caller holds. ``checked``: the automatic save, which refuses
    rather than save windows it could not recover, and writes only a changed snapshot (``if_changed``).
    ``reading``: Niri's focused workspace and windows, read under the same locks."""
    registered = group_store.load(path.parent)
    pins = pin_store.read(path.parent / "spaces.json")[0]
    pinned_apps(pins)
    workspace, windows = reading or spaces.snapshot()
    try:
        display = focused_display(workspace, path.parent)[0]
    except (ValueError, OSError) as exc:
        if checked:
            raise Refused(str(exc)) from exc
        raise
    dirs = spaces.data_dirs()
    if checked:
        entries = [{"app_id": w.get("app_id"), "column": (w.get("layout", {}).get("pos_in_scrolling_layout") or [None])[0]}
                   for w in windows if w.get("workspace_id") == workspace["id"] and not w.get("is_floating")]
        warnings = recovery_warnings(entries, dirs)
        if not entries or warnings:
            raise Refused("Automatic saving paused; the saved session was preserved. " + "; ".join(warnings))
    before = path.read_bytes() if path.exists() else None
    try:
        count = save(path, workspace, windows, spaces.session_id(), pins, registered, display, if_changed=if_changed)
    except ValueError as exc:  # save()'s refusals: a Group mid-change, pins to check
        if checked:
            raise Refused(str(exc)) from exc
        raise
    warnings = recovery_warnings(json.loads(path.read_text(encoding="utf-8"))["windows"], dirs)
    return {"message": f"Saved {count} open windows. Save documents in each app before restarting." +
            (" " + "; ".join(warnings) if warnings else ""), "written": path.read_bytes() != before}


PAUSED = "Automatic saving is paused; review and save the session."
# Where automatic recovery puts a saved session it does not reopen, so a save cannot replace it unseen: one file
# each time, named by when, so a second set-aside never replaces the first.
SET_ASIDE = "session-before-automatic-recovery-{}.json"
# Beside a set-aside file once Reopen it tried to open anything from it, with how it went: a second reopen would
# open those apps twice, and the shell that knew it may have restarted since. Its first line is the state:
# ``started`` (written before the first launch: a reopen that never finished, or whose last word could not be
# written), ``complete``, ``partial`` or ``stopped``; the second says how, for the user.
REOPENED = ".reopened"


class Recorded(ValueError):
    """A set-aside file another reopen has recorded (``"recorded": true`` in the answer): Settings stops offering
    Reopen it, which can only be refused now."""


def write_record(record, state, how="", first=False):
    """Write a set-aside file's reopen record; False if it could not be written (a full disk, say). Written to a
    file of its own, then put in place whole: a write that fails leaves the record as it was, never empty. The
    ``first`` (``started``) is put there only if no record is: another reopen that got there first raises
    FileExistsError."""
    temporary = record.with_name(f"{record.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(f"{state}\n{how}\n", encoding="utf-8")
        (os.link if first else os.replace)(temporary, record)
        return True
    except FileExistsError:
        raise
    except OSError as exc:
        print(f"flow-recovery: could not record the reopen in {record}: {exc}", file=sys.stderr)
        return False
    finally:
        temporary.unlink(missing_ok=True)


def read_record(record):
    """A reopen record's state and how it went. A one-line record from before the state line was written
    (dd8a386) is read from its sentence."""
    lines = record.read_text(encoding="utf-8").strip().splitlines()
    if len(lines) != 1:
        return (lines[0], "\n".join(lines[1:])) if lines else ("started", "")
    line = lines[0]
    if re.fullmatch(r"reopened (\d+) of \1 windows", line):
        return "complete", line
    if line.startswith("reopened "):
        return "partial", line
    return ("stopped" if line.startswith("stopped: ") else "started"), line


def pause_marker(marker, reason=""):
    """Pause automatic saving for this session, with why: a shell started again later says it again."""
    marker.write_text("blocked" + ("\n" + reason if reason else ""))


def set_aside(path):
    """Move a saved session recovery will not reopen out of the automatic save's way; where it went, or ""."""
    if not path.exists():
        return ""
    stamp, n = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()), 0  # UTC, so the names sort by when
    while (aside := path.with_name(SET_ASIDE.format(f"{stamp}-{n:02d}"))).exists():
        n += 1
    path.replace(aside)
    return str(aside)


def set_aside_source(path, name):
    """The set-aside file ``name`` beside ``path`` for Restore to read, so reopening it never goes through
    session.json, which the next automatic save replaces."""
    given = Path(name)
    source = path.with_name(given.name)
    prefix, suffix = SET_ASIDE.split("{}")
    beside = given.parent == Path() or given.parent.resolve() == path.parent.resolve()
    if not beside or source.is_symlink() or not (source.name.startswith(prefix) and source.name.endswith(suffix)):
        raise ValueError(f"Name a saved session that automatic recovery set aside in {path.parent}")
    record = source.with_name(source.name + REOPENED)
    if record.exists():
        state, how = read_record(record)
        again = f". To try again anyway, remove {record}"
        if state == "complete":
            raise Recorded(f"{source.name} was reopened already ({how}){again}")
        if state in ("partial", "stopped"):
            raise Recorded(f"{source.name} was reopened already ({how}); open the rest yourself, as reopening it "
                           f"again would open the others twice{again}")
        raise Recorded(f"{source.name} may be open in part: a reopen of it started and did not say how it "
                       f"ended; check your workspaces before opening the rest{again}")
    return source


def back_to(workspace, target):
    """Focus ``workspace`` again after a set-aside reopen that moved the user to ``target`` and opened nothing,
    if they are still there (not if they went elsewhere meanwhile)."""
    try:
        now = spaces.snapshot()[0]
        if workspace["id"] != target["id"] and now["id"] == target["id"]:
            spaces.command("action", "focus-workspace", workspace["idx"])
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        pass


def set_aside_answer(message, path):
    """``message``, with the saved session set aside and where: ``set_aside`` for the shell's notice."""
    aside = set_aside(path)
    return {"message": message + (f" The saved session is kept as {aside}." if aside else ""),
            **({"set_aside": aside} if aside else {})}


# With no marker, open windows count as work under way only this long after Niri started. A login's first shell
# starts with Niri (flow-shell is wanted by niri.service) and asks within seconds, however slow the boot; an app
# started with the session may be open by then.
UNDER_WAY_AFTER = 600


def niri_age(proc=Path("/proc")):
    """Seconds since this Niri instance started, on the boot clock, which a wall-clock jump after login (no RTC,
    NTP, a VM resumed late) does not move: Niri's process start (its PID is in the socket's name,
    ``niri.<display>.<pid>.sock``) against the uptime. The socket's creation by the wall clock otherwise."""
    socket = os.environ.get("NIRI_SOCKET", "")
    pid = re.search(r"\.(\d+)\.sock$", socket)
    if pid:
        try:
            stat = (proc / pid.group(1) / "stat").read_text()
            if stat[stat.index("(") + 1:stat.rindex(")")].startswith("niri"):
                started = int(stat[stat.rindex(")") + 2:].split()[19]) / os.sysconf("SC_CLK_TCK")  # field 22
                return float((proc / "uptime").read_text().split()[0]) - started
        except (OSError, ValueError, IndexError):
            pass
    return time.time() - os.stat(socket).st_ctime


def under_way_long_enough():
    """Whether this Niri instance has run past UNDER_WAY_AFTER; an instance whose age cannot be read counts."""
    try:
        return niri_age() > UNDER_WAY_AFTER
    except OSError:
        return True


def work_under_way():
    """The apps of the windows open in this Niri instance, by app ID: work, once the instance has run a while with
    no marker (a rollback to an older generation and a switch forward, a shell that failed at login and was started
    later). Whether or not the saved session lists them: a user's work is mostly the apps it lists. None readable:
    none."""
    try:
        _, windows = spaces.query("Workspaces", "Windows")
    except (OSError, ValueError, AttributeError):
        return []
    return sorted({w.get("app_id") or "an app without an ID" for w in windows})


def from_before_reopening(path):
    """Whether the saved session was written before snapshots said they may be reopened at login: by a version
    with automatic recovery off by default, possibly a Save session made long ago for a manual restore."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False  # unreadable: the restore says what is wrong
    return isinstance(data, dict) and data.get("reopen_at_login") is not True


def automatic(op, path, wait=True, pause=True):
    """An automatic-recovery step. Without ``wait``, recovery's or Flow's locks held elsewhere raise
    BlockingIOError (the resident controller saves this way, and tries again later). Without ``pause``, a save
    that refuses what is there says so (``"refused": true``) and leaves recovery ready: the controller tries
    once more before it pauses, as what it refused may not last (a second terminal for a quick command).

    The marker belongs to one Niri instance (``spaces.session_id``: boot, socket path, inode and creation). A
    shell writes it at its first start in that instance whatever the setting: ``auto-start`` (on), or
    ``auto-seen`` (off: ``off``). So ``auto-start`` reopens the saved session only at a Niri instance's first
    shell start, and a first start past UNDER_WAY_AFTER with windows open is work under way (below). A shell
    started again finds the marker: ``ready``, ``blocked`` (a pause, with its reason: ``"paused": true``, the
    user was told when it came) or ``off`` (recovery switched on since, by hand: nothing is reopened into the
    windows already there). A shell of a version from before the
    marker wrote none, but every snapshot it wrote lacks ``reopen_at_login``, and none of those is reopened:
    the upgrade's restart mid-session, or its first login, reopens nothing. With no marker, windows open
    (``work_under_way``) in an instance past UNDER_WAY_AFTER on the boot clock mean it is under way: a rollback
    and back, or a shell that failed at login and was started later; nothing is reopened into them. A login's
    first shell asks within seconds of Niri's start, and an app started with the session may be open then. Whenever
    recovery does not reopen a saved session it sets it aside (SET_ASIDE), so the next automatic save does not
    replace it unseen, and says where (``"set_aside"``)."""
    runtime = Path(os.environ["XDG_RUNTIME_DIR"])
    marker = runtime / ("flow-recovery-" + hashlib.sha256(spaces.session_id().encode()).hexdigest())
    with marker.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        state, _, reason = (marker.read_text() if marker.exists() else "").partition("\n")
        if op == "auto-pause":
            pause_marker(marker)
            return {"message": "Save session to start automatic recovery."}
        if op == "auto-ready":
            marker.write_text("ready")
            return {"message": "Automatic session recovery is ready."}
        if op == "auto-seen":
            if not state:
                marker.write_text("off")
            return {"message": "Automatic session recovery is off."}
        if op == "auto-start":
            if state == "off":
                marker.write_text("ready")
                return set_aside_answer("Automatic session recovery is ready. It was switched on during this session, "
                                        "so nothing was reopened; it saves this session from now on.", path)
            if state:
                return {"message": "Automatic session recovery is ready."} if state == "ready" else {
                    "error": reason or PAUSED, "paused": True}
            if path.exists() and from_before_reopening(path):
                marker.write_text("ready")
                return set_aside_answer("Automatic session recovery is ready. The saved session is from before it "
                                        "reopened sessions at login, so it was not reopened.", path)
            if path.exists() and (open_apps := work_under_way()) and under_way_long_enough():
                marker.write_text("ready")
                return set_aside_answer("Automatic session recovery is ready. Work was already open ("
                                        + ", ".join(open_apps[:3]) + (", …" if len(open_apps) > 3 else "")
                                        + "), so nothing was reopened into it.", path)
            pause_marker(marker)
            if path.exists():
                result = subprocess.run([sys.executable, __file__, "restore"], capture_output=True, text=True)
                try:
                    output = json.loads(result.stdout)
                except json.JSONDecodeError:
                    pause_marker(marker, "Automatic recovery failed; the saved session was preserved.")
                    return {"error": "Automatic recovery failed; the saved session was preserved."}
                message = output.get("message", "")
                if result.returncode or not (message.startswith("Restored ") and message.endswith(" windows.")):
                    error = (output.get("error") or message or "Automatic recovery failed") + " " + PAUSED
                    pause_marker(marker, error)
                    return {"error": error}
            marker.write_text("ready")
            return {"message": "Automatic session recovery is ready."}
        if state != "ready":
            return {"error": PAUSED}
        try:
            with flow_locks(path.parent, wait):
                try:
                    workspace, windows = spaces.snapshot()
                except (ValueError, OSError) as exc:
                    raise Refused(str(exc)) from exc
                if not any(w.get("workspace_id") == workspace["id"] for w in windows):
                    return {"message": "Automatic saving skipped an empty workspace."}
                return save_locked(path, checked=True, if_changed=True, reading=(workspace, windows))
        except BlockingIOError:
            raise
        except Refused as exc:
            if pause:
                pause_marker(marker, str(exc))
            return {"error": str(exc), "refused": True}
        except (ValueError, KeyError, OSError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
            pause_marker(marker, str(exc))
            return {"error": str(exc)}
        except Exception:  # noqa: BLE001 — a bug in saving pauses it, as a crashed save did
            pause_marker(marker, "Automatic saving failed; the saved session was preserved.")
            return {"error": "Automatic saving failed; the saved session was preserved."}


def write_snapshot(path, data):
    import state_files
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    state_files.replace_durably(path, json.dumps(data, ensure_ascii=False).encode(), pause="session.write")


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
    old = json.loads(path.read_text(encoding="utf-8")) if previous and path.exists() else {}
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
    # reopen_at_login: written since automatic recovery is on by default; older snapshots are not reopened at login.
    data = {"version": 1, "reopen_at_login": True, "session": session, "windows": entries, "groups": groups}
    if display:
        data["display"] = display
    if not (if_changed and path.exists() and path.read_text(encoding="utf-8") == json.dumps(data, ensure_ascii=False)):
        write_snapshot(path, data)  # as json.dump writes it, so an unchanged snapshot reads the same
    return len(current)


def restore(data, workspace, windows, dirs, launch, get_snapshot, action, wait=time.sleep, pinned=(), session=None,
            pinned_groups=(), registered=None, display=None, here=False):
    """Reopen ``data``'s windows on ``workspace``; the report and how many were restored. A saved app is taken as
    restored by an open window of it, anywhere. With ``here`` (a set-aside session reopened on an empty workspace,
    while the user's work is open elsewhere) recovery sees only ``workspace``'s windows, in every reading: a saved
    app or Group is matched only there, so the work is never taken for it or moved."""
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
    if here:
        windows = [w for w in windows if w.get("workspace_id") == workspace_id]

    def checked_snapshot():
        nonlocal workspace_changed
        current, live = get_snapshot()
        if current["id"] != workspace_id:
            workspace_changed = True
            raise ValueError("Workspace changed; session recovery stopped")
        return current, [w for w in live if w.get("workspace_id") == workspace_id] if here else live

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
                        if widths:
                            try:
                                display = spaces.display_width(current)
                            except ValueError:
                                report.append("Group proportions were not checked: Niri did not report the display's size")
                                break
                        if not widths or spaces.widths_settled(widths, ratios, display):
                            break
                        if attempt == 10:
                            report.append("Application size constraints prevented recovery of Group proportions")
                        else:
                            wait(0.1)
        elif not groups:
            local = spaces.local_pins(pinned_groups, display)
            for index, (_, window) in enumerate(restored, 1):
                current, live = checked_snapshot()
                if any(w.get("workspace_id") == workspace_id and w.get("app_id") in pinned for w in live):
                    if not order_after_pins(entries, restored, current, live, local, session or "", registered or ()):
                        report.append("Pinned spaces keep their positions; session order needs manual recovery")
                    break
                action("action", "focus-window", "--id", window["id"])
                current, live = checked_snapshot()
                if any(w.get("workspace_id") == workspace_id and w.get("app_id") in pinned for w in live):
                    # A pinned app appeared meanwhile (one opened at login, say): judged as at the top of the loop.
                    if not order_after_pins(entries, restored, current, live, local, session or "", registered or ()):
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
                        if not order_after_pins(entries, restored, workspace, live,
                                                spaces.local_pins(pinned_groups, display), session or "",
                                                registered or ()):
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
    identities, _ = pin_store.identities(spaces.query("Outputs")[0], pin_store.shared_serials(displays))
    record = identities.get(workspace.get("output"))
    return (record["key"] if record else None), {r["key"] for r in identities.values()}


def snapshot_display(workspace, outputs, displays, pins):
    """The focused display, for a snapshot's plan: its key and label, its pin count, how many are connected."""
    identities, _ = pin_store.identities(outputs, pin_store.shared_serials(displays))
    record = identities.get(workspace.get("output"))
    key = record["key"] if record else None
    return {"key": key, "label": record["label"] if record else workspace.get("output") or "this display",
            "pins": len(spaces.local_pins(pins, key)) if key else 0, "connected": len(identities)}


def export_snapshot(state, target):
    """Write the focused workspace as a portable snapshot to a new file (ADR-0012)."""
    import snapshot
    with flow_locks(state):
        groups = group_store.load(state, migrate=False)
        pins, displays, _ = pin_store.read(state / "spaces.json")
        workspace, windows, outputs, _ = spaces.full_state()
        display = snapshot_display(workspace, outputs, displays, pins)
        spaces.SESSION = spaces.session_id()  # this session's Group bindings tell same-app windows apart
        try:
            listed = spaces.display_spaces(workspace, windows, groups, pins)
        finally:
            spaces.SESSION = None
        here = spaces.local_pins(pins, display["key"]) if display["key"] else pins
        data, left = snapshot.export(listed, here, groups, spaces.app_name)
    layers = sum(1 for w in windows if w.get("workspace_id") == workspace["id"] and w.get("is_floating"))
    if layers:
        left.insert(0, f"{layers} Layer{'s' if layers > 1 else ''} (floating windows): Layers are not exported")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # never replaces a file
    with os.fdopen(fd, "wb") as out:
        out.write(snapshot.dump(data))
    count = len(data["spaces"])
    return {"message": f"Exported {count} space{'s' if count > 1 else ''} to {target}.", "left_out": left}


def launchable(path):
    """Whether a desktop entry is an app a user could have had open: ``Type=Application``, neither hidden nor
    kept out of menus. A snapshot's IDs come from a file, not from this machine's windows (ADR-0012)."""
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return False
    section, keys = None, {}
    for line in lines:
        line = line.strip()
        if line.startswith("["):
            section = line
        elif section == "[Desktop Entry]" and "=" in line:
            key, value = line.split("=", 1)
            keys.setdefault(key.strip(), value.strip())
    return (keys.get("Type") == "Application" and keys.get("Hidden", "false") != "true"
            and keys.get("NoDisplay", "false") != "true")


def import_plan(state, data, dirs):
    """The import's plan for this machine as it is now, and what it was computed from. Call under Flow's locks."""
    import snapshot
    groups = group_store.load(state, migrate=False)
    pins, displays, _ = pin_store.read(state / "spaces.json")
    pinned_apps(pins)
    workspace, windows, outputs, workspaces = spaces.full_state()
    display = snapshot_display(workspace, outputs, displays, pins)
    def installed(desktop):
        path = spaces.desktop_file(snapshot.app_id(desktop), dirs)
        return bool(path) and launchable(path)

    report = snapshot.plan(data, windows, groups, pins, installed, display)
    return report, {"groups": groups, "pins": pins, "workspace": workspace, "windows": windows,
                    "workspaces": workspaces, "display": display}


def empty_workspace(workspace, windows, workspaces, done="imported"):
    """The focused display's empty workspace: the focused one when it is empty, else Niri's last one there."""
    used = {w.get("workspace_id") for w in windows}
    if workspace["id"] not in used:
        return workspace
    mine = sorted((w for w in workspaces if w.get("output") == workspace.get("output")), key=lambda w: w["idx"])
    if not mine or mine[-1]["id"] in used:
        raise ValueError(f"This display has no empty workspace; nothing was {done}")
    return mine[-1]


def import_snapshot(state, data, confirm, dirs):
    """Import a previewed snapshot onto a new workspace; ``confirm`` is the preview's digest (ADR-0012)."""
    import snapshot
    with flow_locks(state):
        report, seen = import_plan(state, data, dirs)
        if confirm != report["confirm"]:
            raise ValueError("This is not the confirmation of the current preview: something changed here since, "
                             "or it belongs to another snapshot. Nothing was imported; preview again")
        if not report["open"]:
            raise ValueError("Nothing in the snapshot can open here; nothing was imported")
        target = empty_workspace(seen["workspace"], seen["windows"], seen["workspaces"])
        if target["id"] != seen["workspace"]["id"]:
            spaces.command("action", "focus-workspace", target["idx"])
        workspace, windows = spaces.snapshot()
        if workspace["id"] != target["id"] or any(w.get("workspace_id") == target["id"] for w in windows):
            raise ValueError("The new workspace did not come into focus empty; nothing was imported")
        restore_data, created = snapshot.restore_input(report)
        expected = seen["groups"] + created
        if created:
            group_store.write(state / "groups.json", expected)
    session, pins = spaces.session_id(), seen["pins"]

    def checked_state():
        with flow_locks(state):
            now_groups = group_store.load(state, migrate=False)
            now_pins = pin_store.read(state / "spaces.json")[0]
            current = spaces.snapshot()
            if (not groups_unchanged_or_focused(expected, now_groups, *current)
                    or not pins_unchanged_or_rebound(pins, now_pins, session, current[1], current[0], now_groups)):
                raise ValueError("Groups or pinned spaces changed; the import stopped")
            return current

    def launch(desktop):
        current, _ = checked_state()
        if current["id"] != workspace["id"]:
            raise ValueError("Workspace changed; the import stopped")
        subprocess.run(app_command(["gio", "launch", str(desktop)]), check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        notes, _ = restore(restore_data, workspace, windows, dirs, launch, checked_state, spaces.command,
                           session=session, registered=created, display=seen["display"]["key"])
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        kept = ("; Groups it registered stay: " + ", ".join(g["label"] for g in created)) if created else ""
        raise ValueError(f"The import stopped: {exc}{kept}") from exc
    left = list(report["left_out"])
    opened, broken = [], []
    with flow_locks(state):
        current, live = spaces.snapshot()
        registered = group_store.load(state, migrate=False)
        for space in report["open"]:
            apps = [snapshot.app_id(d) for d in space["apps"]]
            found = [w for w in live if w.get("app_id") in apps and w.get("workspace_id") == workspace["id"]]
            if len(found) != len(apps):
                missing = [d for d in space["apps"] if not any(w.get("app_id") == snapshot.app_id(d) for w in found)]
                left.append({**({"group": space["group"]["name"]} if space.get("group") else {"app": space["apps"][0]}),
                             "reason": "did not open: " + ", ".join(missing)})
                if not space.get("group"):
                    continue
            if space.get("group"):
                group = next(g for g in created if g["members"] == apps)
                candidate = {"group_id": group["id"], "members": group["members"], "session": None}
                if len(found) != len(apps) or not spaces.group_windows(candidate, current, live, session, registered):
                    # A Group recovery could not build is not left registered apart: its apps stay single spaces.
                    broken.append(group["id"])
                    if len(found) == len(apps):
                        left.append({"group": space["group"]["name"],
                                     "reason": "not created: Flow could not arrange its apps as the Group; "
                                               "they stay open as spaces of their own"})
                    continue
            opened.append(space)
        if broken:
            group_store.write(state / "groups.json", [g for g in registered if g["id"] not in broken])
    pins_report = []
    for space in sorted((s for s in report["open"] if s.get("pin")), key=lambda s: s["pin"]):
        name = space["group"]["name"] if space.get("group") else space["apps"][0]
        if space not in opened:
            pins_report.append({"space": name, "pinned": False, "reason": "did not open"})
            continue
        if space.get("group"):
            group_id = next(g["id"] for g in created if g["members"] == [snapshot.app_id(d) for d in space["apps"]])
            op, arg = "pin-group", group_id
        else:
            op = "pin"
            arg = str(next(w["id"] for w in live if w.get("app_id") == snapshot.app_id(space["apps"][0])
                           and w.get("workspace_id") == workspace["id"]))
        try:
            # As flow-spaces runs an action: session.lock held, and run_locked takes spaces.lock itself.
            with (state / "session.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                if spaces.snapshot()[0]["id"] != workspace["id"]:
                    raise ValueError("Workspace changed")
                spaces.run_locked(state, op, arg, emit=False)
            pins_report.append({"space": name, "pinned": True})
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            pins_report.append({"space": name, "pinned": False, "reason": str(exc)})
    message = f"Imported {len(opened)} of {len(report['open'])} spaces onto a new workspace."
    if left:
        message += " Left out: " + "; ".join(f"{e.get('group') or e.get('app')}: {e['reason']}" for e in left) + "."
    unpinned = [p for p in pins_report if not p["pinned"]]
    if unpinned:
        message += " Not pinned: " + "; ".join(f"{p['space']}: {p['reason']}" for p in unpinned) + "."
    if notes:
        message += " " + "; ".join(notes) + "."
    return {"message": message, "acted": True, "workspace": target["idx"], "opened": opened, "left_out": left,
            "pins": pins_report, "notes": notes}


def main():
    path = state_path()
    op = sys.argv[1] if len(sys.argv) > 1 else "status"
    if op in ("export", "preview", "import"):
        import snapshot
        needed = {"export": 3, "preview": 3, "import": 4}[op]
        if len(sys.argv) != needed:
            raise ValueError({"export": "Name a new file to export to", "preview": "Name a snapshot file to preview",
                              "import": "Name the snapshot file and the confirmation from its preview"}[op])
        if op == "export":
            print(json.dumps(export_snapshot(path.parent, sys.argv[2])))
            return
        with open(sys.argv[2], "rb") as source:
            data = snapshot.parse(source.read(snapshot.MAX_BYTES + 1))  # parse refuses more than MAX_BYTES
        if op == "preview":
            with flow_locks(path.parent):
                print(json.dumps(import_plan(path.parent, data, spaces.data_dirs())[0]))
            return
        print(json.dumps(import_snapshot(path.parent, data, sys.argv[3], spaces.data_dirs())))
        return
    if op in ("auto-start", "auto-save", "auto-ready", "auto-pause", "auto-seen"):
        print(json.dumps(automatic(op, path)))
        return
    if op == "status":
        print(json.dumps({"saved": path.exists()}))
        return
    if op == "restore" and len(sys.argv) > 3:
        raise ValueError("Restore reads session.json or one set-aside saved session")
    source = set_aside_source(path, sys.argv[2]) if op == "restore" and len(sys.argv) == 3 else path
    here = source != path
    if op in ("save", "rename-group", "restore"):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (path.parent / "session.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with (path.parent / "spaces.lock").open("a") as spaces_lock:
                fcntl.flock(spaces_lock, fcntl.LOCK_EX)
                registered = group_store.load(path.parent)
                if op == "save":
                    result = save_locked(path)
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
                    original_snapshot = source.read_bytes()
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
                    started_from = workspace
                    if here:
                        # Work is open (that is why it was set aside): reopened on the display's empty workspace.
                        workspaces, windows = spaces.query("Workspaces", "Windows")
                        target = empty_workspace(workspace, windows, workspaces, "reopened")
                        if target["id"] != workspace["id"]:
                            spaces.command("action", "focus-workspace", target["idx"])
                        workspace, windows = spaces.snapshot()
                        if workspace["id"] != target["id"] or any(w.get("workspace_id") == target["id"] for w in windows):
                            back_to(started_from, target)
                            raise ValueError("The empty workspace did not come into focus empty; nothing was reopened")
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
                        if (source.read_bytes() != original_snapshot or not groups_unchanged_or_focused(registered, now_groups, *current)
                                or not pins_unchanged_or_rebound(pins, now_pins, session, current[1], current[0], now_groups)):
                            raise ValueError("Session, Groups or pinned spaces changed; session recovery stopped")
                        return current
            launched = []  # tried
            # Launches gio reported done: their windows may land anywhere, or come late, so any keeps the record.
            # One gio reports failed is taken to have opened nothing: reasoned from GLib, not checked live
            # (g_desktop_app_info launch reports spawn errors at once; D-Bus activation returns without waiting).
            started = []
            record = source.with_name(source.name + REOPENED)

            def launch(desktop):
                current, _ = checked_state()
                if current["id"] != workspace["id"]:
                    raise ValueError("Workspace changed; session recovery stopped")
                if here and not launched:
                    try:
                        recorded = write_record(record, "started", "a reopen started", first=True)
                    except FileExistsError:
                        raise Recorded(f"Another reopen of {source.name} started first; nothing was reopened here, "
                                       "and that one is left to it") from None
                    if not recorded:
                        raise ValueError(f"Could not record the reopen beside {source.name}; nothing was reopened")
                launched.append(desktop)
                subprocess.run(app_command(["gio", "launch", str(desktop)]), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                started.append(desktop)

            def nothing_started():
                """Every launch failed: nothing opened, so the reopen may be tried again; the user back too."""
                record.unlink(missing_ok=True)
                launched.clear()
                back_to(started_from, workspace)
            # A set-aside session may predate Groups registered since: only those it saved are its own, as an
            # import passes only the Groups it created.
            saved_ids = {g.get("id") for g in data.get("groups") or [] if isinstance(g, dict)}
            try:
                report, count = restore(data, workspace, windows, dirs, launch, checked_state, spaces.command,
                                        pinned=pinned_apps(pins), session=session, pinned_groups=pins,
                                        registered=[g for g in registered if g.get("id") in saved_ids] if here else registered,
                                        display=display, here=here, wait=time.sleep)  # read now: tests stub it
            except Exception as exc:  # noqa: BLE001 — for a reopen, whatever stopped it is told with what opened
                if not here:
                    raise
                why = (str(exc) or type(exc).__name__).rstrip(".")
                if started:
                    write_record(record, "stopped", f"stopped: {why}")
                elif launched:
                    nothing_started()
                elif not isinstance(exc, Recorded):  # refused before opening anything: the user back where they were
                    back_to(started_from, workspace)  # (not when another reopen took it: that workspace is its own)
                # Settings offers Reopen it again only if nothing was tried: a second run would open those twice.
                print(json.dumps({"error": why + ".", "launched": len(launched),
                                  **({"recorded": True} if isinstance(exc, Recorded) else {})}))
                sys.exit(1)
            done = (f"Reopened {count} of {len(data['windows'])} windows on an empty workspace." if here
                    else f"Restored {count} windows.")
            if here and launched and not started:
                nothing_started()
            elif here and launched:
                # If this cannot be written, ``started`` stays, and still refuses a second reopen.
                write_record(record, "partial" if report else "complete",
                             done.removesuffix(" on an empty workspace.").lower())
            elif here:
                back_to(started_from, workspace)
            # Complete: every saved window is back. Settings offers a set-aside session until then, or until
            # anything was launched.
            print(json.dumps({"message": done + (" " + "; ".join(report) if report else ""), "complete": not report,
                              "launched": len(launched)}))
        return
    raise ValueError("Unknown recovery action")


if __name__ == "__main__":
    # A shell restart does not end this action, but it closes the pipes it prints to (session/orphan_io.py).
    kept = orphan_io.guard()
    code = 0
    try:
        main()
    except (ValueError, KeyError, OSError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"error": str(exc), **({"recorded": True} if isinstance(exc, Recorded) else {})}))
        code = 1
    finally:
        orphan_io.finish(kept)
    sys.exit(code)
