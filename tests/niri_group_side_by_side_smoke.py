#!/usr/bin/env python3
"""Exercise Flow's pinned Group side-by-side layout on disposable Foot windows.

Run inside a disposable Niri session, from the Flow checkout:
    nix shell --inputs-from . nixpkgs#python3 -c python3 tests/niri_group_side_by_side_smoke.py
Requires Niri, Foot, an empty workspace on the focused output, and no concurrent
interaction. Flow state is isolated under a temporary XDG_STATE_HOME; the test
checks real state checksums and restores the original workspace and focus.
"""

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import uuid

CODE = Path(__file__).resolve().parents[1] / "session"


def niri(*args):
    return subprocess.check_output(["niri", "msg", *map(str, args)], text=True, timeout=10)


def read(kind):
    return json.loads(niri("-j", kind))


def focus():
    return next(w for w in read("workspaces") if w["is_focused"])


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def layout(windows):
    return {w["id"]: (w.get("workspace_id"), w.get("layout", {}).get("pos_in_scrolling_layout"))
            for w in windows}


def wait_for(test, label):
    end = time.monotonic() + 12
    while time.monotonic() < end:
        value = test()
        if value:
            return value
        time.sleep(0.1)
    raise RuntimeError(f"Timed out waiting for {label}")


def cli(env, *args):
    result = subprocess.run([sys.executable, str(CODE / "spaces.py"), *args], env=env,
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    if result.returncode:
        raise RuntimeError(f"flow-spaces {args} failed: {result.stdout} {result.stderr}")
    value = json.loads(result.stdout)
    if "error" in value:
        raise RuntimeError(f"flow-spaces {args}: {value['error']}")
    return value


def main():
    original = focus()
    original_windows = read("windows")
    real_state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "flow"
    real_files = [real_state / name for name in ("spaces.json", "groups.json", "session.json")]
    real_digests = [digest(path) for path in real_files]
    occupied = {w.get("workspace_id") for w in original_windows}
    targets = [w for w in read("workspaces") if w.get("output") == original.get("output")
               and w["id"] != original["id"] and w["id"] not in occupied]
    if not targets:
        raise RuntimeError("An empty workspace on the focused output is required")
    output = read("outputs").get(original.get("output"), {}).get("logical")
    if not output or output["width"] < 600:
        raise RuntimeError("A focused output at least 600 logical pixels wide is required")

    token = uuid.uuid4().hex
    apps = [f"flow-layout-{token}-{suffix}" for suffix in ("a", "b", "neighbor")]
    intruder = f"flow-layout-{token}-intruder"
    processes = []
    test_workspace = None
    interrupted = False
    result = None

    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    old_handlers = {sig: signal.signal(sig, interrupt) for sig in (signal.SIGTERM, signal.SIGHUP)}

    def checked_windows():
        nonlocal interrupted
        if focus()["id"] != test_workspace:
            interrupted = True
            raise RuntimeError("Workspace changed during the test")
        windows = read("windows")
        if any(w.get("workspace_id") == test_workspace and w.get("app_id") not in [*apps, intruder] for w in windows):
            interrupted = True
            raise RuntimeError("Another window appeared on the test workspace")
        return windows

    def test_windows():
        windows = checked_windows()
        matches = [[w for w in windows if w.get("app_id") == app and w.get("workspace_id") == test_workspace]
                   for app in apps]
        if any(len(found) > 1 for found in matches):
            raise RuntimeError("Disposable app IDs are ambiguous")
        return [found[0] for found in matches] if all(matches) else None

    def group_pair(predicate):
        windows = test_windows()
        if not windows or not predicate(windows[:2]):
            return None
        return windows[:2]

    def ordered_columns():
        windows = test_windows()
        return windows if windows and all(
            w.get("layout", {}).get("pos_in_scrolling_layout", [None])[0] == index
            for w, index in zip(windows, (1, 2, 3))) else None

    def saved_groups(state):
        path = Path(state) / "flow/groups.json"
        return json.loads(path.read_text())["groups"]

    def pinned(result, group_id, pin_id=None):
        matches = [pin for pin in result["pins"] if pin.get("group_id") == group_id]
        if len(matches) != 1 or not matches[0]["available"] or (pin_id and matches[0]["id"] != pin_id):
            return None
        return matches[0]

    def group_space(result, group_id):
        matches = [space for space in result["spaces"] if space.get("group_id") == group_id]
        return matches[0] if len(matches) == 1 else None

    try:
        target = targets[0]
        if any(w.get("workspace_id") == target["id"] for w in read("windows")):
            raise RuntimeError("Test workspace is no longer empty")
        niri("action", "focus-workspace", target["idx"])
        test_workspace = focus()["id"]
        if test_workspace != target["id"]:
            interrupted = True
            raise RuntimeError("A different workspace was focused")

        for app in apps:
            checked_windows()
            processes.append(subprocess.Popen(["foot", "--app-id", app], stdin=subprocess.DEVNULL,
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            wait_for(lambda: any(w.get("app_id") == app for w in checked_windows()), app)

        wins = wait_for(test_windows, "three disposable windows")
        ids = [w["id"] for w in wins]
        if len(set(ids)) != 3 or not all(process.poll() is None for process in processes):
            raise RuntimeError("Foot process or window IDs were not stable")

        # Put the three single windows in the intended order before stacking A and B.
        for index, ident in enumerate(ids, 1):
            checked_windows()
            niri("action", "focus-window", "--id", ident)
            niri("action", "move-column-to-index", index)
        wait_for(ordered_columns, "ordered columns")

        niri("action", "focus-window", "--id", ids[0])
        cli_env = None
        with tempfile.TemporaryDirectory(prefix="flow-niri-layout-") as tmp:
            cli_env = dict(os.environ, XDG_STATE_HOME=tmp)
            temporary_state = Path(tmp) / "flow"
            cli(cli_env, "stack")
            wait_for(lambda: group_pair(lambda ws: ws[0]["layout"]["pos_in_scrolling_layout"][0] ==
                                        ws[1]["layout"]["pos_in_scrolling_layout"][0]),
                     "stacked Group members")
            if focus().get("active_window_id") != ids[0]:
                raise RuntimeError("Stacking changed the intended focus")

            created = cli(cli_env, "create-group")
            groups = saved_groups(tmp)
            if len(groups) != 1 or set(groups[0]["members"]) != set(apps[:2]):
                raise RuntimeError("flow-spaces did not create the expected Group")
            group = groups[0]
            group_id = group["id"]
            ordered_members = group["members"]
            if ordered_members != [w["app_id"] for w in sorted(test_windows()[:2],
                    key=lambda w: w["layout"]["pos_in_scrolling_layout"][1])]:
                raise RuntimeError("Saved Group member order differs from stack order")
            if not group_space(created, group_id):
                raise RuntimeError("Created Group was not shown as one switcher space")

            group_pin = cli(cli_env, "pin-group", group_id)
            pin = pinned(group_pin, group_id)
            if not pin:
                raise RuntimeError("Group pin is unavailable")
            pin_id = pin["id"]

            # Pin a separate neighboring app so layout changes must preserve its identity and availability.
            niri("action", "focus-window", "--id", ids[2])
            neighbor_pin = cli(cli_env, "pin")
            neighbors = [p for p in neighbor_pin["pins"] if p.get("kind") == "app" and p.get("available")]
            if len(neighbors) != 1:
                raise RuntimeError("Could not pin the neighboring disposable app")
            neighbor_pin_id = neighbors[0]["id"]
            niri("action", "focus-window", "--id", ids[0])

            focused_id = focus().get("active_window_id")
            side = cli(cli_env, "group-layout", group_id)
            wait_for(lambda: group_pair(lambda ws: ws[0]["layout"]["pos_in_scrolling_layout"][0] + 1 ==
                                        ws[1]["layout"]["pos_in_scrolling_layout"][0]),
                     "side-by-side Group columns")
            groups = saved_groups(tmp)
            if len(groups) != 1 or groups[0].get("id") != group_id or groups[0].get("layout") != "side-by-side":
                raise RuntimeError("Group registry did not record side-by-side layout")
            members = test_windows()
            if not members or [w["id"] for w in members] != ids or any(p.poll() is not None for p in processes):
                raise RuntimeError("Group process or window identity changed")
            ordered = sorted(members[:2], key=lambda w: w["layout"]["pos_in_scrolling_layout"][0])
            if [w["app_id"] for w in ordered] != ordered_members:
                raise RuntimeError("Side-by-side order differs from saved Group order")
            widths = [w["layout"]["tile_size"][0] for w in ordered]
            if not all(0.3 * output["width"] < width <= 0.55 * output["width"] for width in widths) or abs(widths[0] - widths[1]) >= 2:
                raise RuntimeError(f"Unexpected side-by-side widths: {widths}")
            if focus().get("active_window_id") != focused_id:
                raise RuntimeError("Side-by-side transition changed focus")
            cli(cli_env, "focus-member", "down")
            if focus().get("active_window_id") != ids[1]:
                raise RuntimeError("Could not focus the second Group member")
            cli(cli_env, "focus-member", "up")
            if focus().get("active_window_id") != ids[0]:
                raise RuntimeError("Could not return to the first Group member")
            side_group = group_space(side, group_id)
            if not side_group or side_group.get("group_layout") != "side-by-side" or [w["id"] for w in side_group["members"]] != ids[:2]:
                raise RuntimeError("Switcher did not show one side-by-side Group")
            if not pinned(side, group_id, pin_id):
                raise RuntimeError("Group pin identity or availability changed")
            neighbor = next((w for w in members if w["id"] == ids[2]), None)
            if not neighbor or neighbor["layout"]["pos_in_scrolling_layout"][0] <= ordered[-1]["layout"]["pos_in_scrolling_layout"][0]:
                raise RuntimeError("Neighboring pinned app was displaced into the Group")
            if not any(p.get("id") == neighbor_pin_id and p.get("available") for p in side["pins"]):
                raise RuntimeError("Neighboring pin was lost or became unavailable")
            niri("action", "focus-window", "--id", ids[2])
            cli(cli_env, "navigate", "left")
            if focus().get("active_window_id") != ids[0]:
                raise RuntimeError("Navigation did not enter the Group at its last focused member")
            cli(cli_env, "navigate", "right")
            if focus().get("active_window_id") != ids[2]:
                raise RuntimeError("Navigation did not skip both Group columns")
            niri("action", "focus-window", "--id", ids[0])

            # Niri may insert a new app between the two registered Group columns.
            intruder_process = subprocess.Popen(["foot", "--app-id", intruder], stdin=subprocess.DEVNULL,
                                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            processes.append(intruder_process)
            inserted = wait_for(lambda: next((w for w in checked_windows() if w.get("app_id") == intruder), None),
                                "intervening window")
            wait_for(lambda: group_pair(lambda ws: ws[1]["layout"]["pos_in_scrolling_layout"][0] ==
                                       ws[0]["layout"]["pos_in_scrolling_layout"][0] + 2),
                     "separated Group columns")
            attention = cli(cli_env, "refresh")
            if not any(g.get("id") == group_id and g.get("can_repair") for g in attention["incomplete_groups"]):
                raise RuntimeError("Separated Group did not offer a repair")
            focused_before_repair = focus().get("active_window_id")
            repaired = cli(cli_env, "repair-side-group", group_id)
            wait_for(lambda: group_pair(lambda ws: ws[1]["layout"]["pos_in_scrolling_layout"][0] ==
                                       ws[0]["layout"]["pos_in_scrolling_layout"][0] + 1),
                     "repaired Group columns")
            if not group_space(repaired, group_id) or not pinned(repaired, group_id, pin_id):
                raise RuntimeError("Repair did not restore the pinned Group")
            if not any(p.get("id") == neighbor_pin_id and p.get("available") for p in repaired["pins"]):
                raise RuntimeError("Repair changed the neighboring pin")
            if focus().get("active_window_id") != focused_before_repair:
                raise RuntimeError("Repair changed the focused window")
            if not any(w["id"] == inserted["id"] for w in checked_windows()):
                raise RuntimeError("Repair closed the intervening window")
            niri("action", "close-window", "--id", inserted["id"])
            wait_for(lambda: not any(w.get("app_id") == intruder for w in checked_windows()), "intervening window to close")
            intruder_process.wait(timeout=5)
            processes.remove(intruder_process)
            niri("action", "focus-window", "--id", ids[0])

            focused_id = focus().get("active_window_id")
            stacked = cli(cli_env, "group-layout", group_id)
            wait_for(lambda: group_pair(lambda ws: ws[0]["layout"]["pos_in_scrolling_layout"][0] ==
                                        ws[1]["layout"]["pos_in_scrolling_layout"][0]),
                     "stacked Group")
            groups = saved_groups(tmp)
            if len(groups) != 1 or groups[0].get("id") != group_id or groups[0].get("members") != ordered_members or groups[0].get("layout") != "stack":
                raise RuntimeError("Stacking changed Group identity, order, or layout")
            if [w["id"] for w in test_windows()] != ids or any(p.poll() is not None for p in processes):
                raise RuntimeError("Stacking changed a disposable window ID or process")
            stack_group = group_space(stacked, group_id)
            if not stack_group or stack_group.get("group_layout") != "stack" or [w["id"] for w in stack_group["members"]] != ids[:2]:
                raise RuntimeError("Switcher did not show one stacked Group")
            if focus().get("active_window_id") != focused_id:
                raise RuntimeError("Returning to stack changed focus")
            if not pinned(stacked, group_id, pin_id):
                raise RuntimeError("Group pin identity or availability changed after stacking")
            if not any(p.get("id") == neighbor_pin_id and p.get("available") for p in stacked["pins"]):
                raise RuntimeError("Neighboring pin was lost or became unavailable after stacking")

            result = {"result": "pass", "niri": niri("version").strip(), "group_id": group_id,
                      "pin_id": pin_id, "neighbor_pin_id": neighbor_pin_id, "window_ids": ids,
                      "side_by_side_widths": widths, "focus_preserved": True, "logical_navigation": True,
                      "separated_group_repaired": True}
            if (temporary_state / "session.json").exists():
                raise RuntimeError("Layout changes unexpectedly wrote a temporary session snapshot")
    finally:
        for process in processes:
            try:
                if process.poll() is None:
                    process.terminate()
            except ProcessLookupError:
                pass
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        try:
            for window in read("windows"):
                if window.get("app_id") in [*apps, intruder]:
                    niri("action", "close-window", "--id", window["id"])
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            if not interrupted and test_workspace is not None and focus()["id"] == test_workspace:
                live = read("windows")
                if any(w.get("id") == original.get("active_window_id") for w in live):
                    niri("action", "focus-window", "--id", original["active_window_id"])
                else:
                    old = next((w for w in read("workspaces") if w["id"] == original["id"]), None)
                    if old:
                        niri("action", "focus-workspace", old["idx"])
        finally:
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)

    if [digest(path) for path in real_files] != real_digests:
        raise RuntimeError("A real Flow Group, pin, or session file changed")
    if layout(read("windows")) != layout(original_windows):
        raise RuntimeError("Original window inventory or positions changed")
    current = focus()
    if current["id"] != original["id"] or current.get("active_window_id") != original.get("active_window_id"):
        raise RuntimeError("Original workspace or focus was not restored")
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, StopIteration, subprocess.SubprocessError, ValueError) as exc:
        print(f"Niri Group side-by-side test failed: {exc}", file=sys.stderr)
        sys.exit(1)
