#!/usr/bin/env python3
"""Read the active application's published accessibility menu, when available."""
import json
import subprocess
import sys

import pyatspi


def niri(what):
    return json.loads(subprocess.check_output(["niri", "msg", "-j", what], text=True))


def active_window():
    workspace = next((w for w in niri("workspaces") if w["is_focused"]), None)
    windows = niri("windows")
    active = next((w for w in windows if workspace and w["id"] == workspace.get("active_window_id")), None)
    if not active:
        return None
    # shortcut: shared-process windows are ambiguous; match the accessible window before supporting them.
    if not active.get("pid") or sum(w.get("pid") == active["pid"] for w in windows) != 1:
        return {"id": active["id"], "name": active.get("app_id") or active.get("title") or "Application", "menus": []}
    desktop = pyatspi.Registry.getDesktop(0)
    app = next((desktop.getChildAtIndex(i) for i in range(desktop.childCount)
                if (desktop.getChildAtIndex(i).name or "").lower() not in ("quickshell", "niri") and desktop.getChildAtIndex(i).get_process_id() == active["pid"]), None)
    result = {"id": active["id"], "name": active.get("app_id") or active.get("title") or "Application", "menus": []}
    if not app:
        return result
    seen = 0

    def walk(node, path, depth, menu=False):
        nonlocal seen
        seen += 1
        if node is None or seen > 2000 or depth > 12:
            return []
        role = node.getRole()
        bar = role == pyatspi.ROLE_MENU_BAR
        if menu and role in (pyatspi.ROLE_MENU, pyatspi.ROLE_MENU_ITEM, pyatspi.ROLE_CHECK_MENU_ITEM, pyatspi.ROLE_RADIO_MENU_ITEM):
            children = []
            for i in range(node.childCount):
                children.extend(walk(node.getChildAtIndex(i), path + [i], depth + 1, True))
            if children:
                return [{"label": node.name or "", "children": children,
                         "enabled": node.getState().contains(pyatspi.STATE_ENABLED)}]
            if role != pyatspi.ROLE_MENU and not node.childCount:
                return [{"label": node.name or "", "path": path,
                         "enabled": node.getState().contains(pyatspi.STATE_ENABLED)}]
            return []
        found = []
        for i in range(node.childCount):
            found.extend(walk(node.getChildAtIndex(i), path + [i], depth + 1, menu or bar))
        return found

    result["menus"] = walk(app, [], 0)
    return result


def find_action(items, path, label):
    for item in items:
        if item.get("path") == path and item.get("label") == label and item.get("enabled"):
            return True
        if item.get("enabled", True) and find_action(item.get("children", []), path, label):
            return True
    return False


def main():
    status = subprocess.check_output(["busctl", "--user", "get-property", "org.a11y.Bus", "/org/a11y/bus", "org.a11y.Status", "IsEnabled"], text=True)
    if status.strip() == "b false":
        subprocess.run(["busctl", "--user", "set-property", "org.a11y.Bus", "/org/a11y/bus", "org.a11y.Status", "IsEnabled", "b", "true"], check=True, stdout=subprocess.DEVNULL)
    active = active_window()
    if len(sys.argv) > 1 and sys.argv[1] == "activate":
        if not active or str(active["id"]) != sys.argv[2]:
            raise ValueError("Application focus changed")
        wanted = json.loads(sys.argv[3])
        label = sys.argv[4]
        if not find_action(active["menus"], wanted, label):
            raise ValueError("Command is no longer available")
        win = next(w for w in niri("windows") if w["id"] == active["id"])
        desktop = pyatspi.Registry.getDesktop(0)
        node = next(desktop.getChildAtIndex(i) for i in range(desktop.childCount)
                    if (desktop.getChildAtIndex(i).name or "").lower() not in ("quickshell", "niri") and desktop.getChildAtIndex(i).get_process_id() == win["pid"])
        for index in wanted:
            node = node.getChildAtIndex(index)
        current = next((w for w in niri("workspaces") if w["is_focused"]), None)
        if not current or current.get("active_window_id") != active["id"] or node.name != label or not node.getState().contains(pyatspi.STATE_ENABLED):
            raise ValueError("Application focus or command changed")
        if not node.queryAction().doAction(0):
            raise ValueError("Application declined the command")
    print(json.dumps(active))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
