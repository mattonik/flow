#!/usr/bin/env python3
"""The Luma note's experiment (ADR-0012): import a semantic snapshot with an app that is not installed and a
two-app Group, with isolated state, and no network.

Run inside a Niri session from the Flow checkout, with ``flow-spaces`` and ``flow-recovery`` on PATH running
this checkout's ``session/`` (``tests/run_snapshot_import_smoke.sh`` makes them), and ``foot``, ``unshare`` and
``strace`` there too. Flow state is isolated; the apps are Foot windows with app IDs of their own, each with a
desktop entry in an isolated data folder, and with a window title that stands for private text.

Checked:
- export of the existing work writes no window title or window ID;
- the preview reports the missing app and the already open one, the Group to create and where its pin goes;
  it and a few seconds after it change no window, workspace, focus or state file;
- the import with the preview's confirmation opens the Group as one column on a new, previously empty workspace,
  appends its pin after the existing one, and leaves the existing window, its pin and the real state alone;
- a stale confirmation is refused with nothing done;
- preview and import run in a network namespace of their own with no interface but a loopback that is down, so
  no network connection can be made by them or by what they launch; and strace shows that their only connects
  are to Unix sockets.
With ``FLOW_SCREENSHOTS`` set to a folder, ``grim`` saves the screen before and after the import there.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

from isolated_state import IsolatedState

ISOLATE = ["unshare", "--user", "--map-current-user", "--net"]


def niri(*args):
    return subprocess.check_output(["niri", "msg", *map(str, args)], text=True, timeout=10)


def read(kind):
    return json.loads(niri("-j", kind))


def focus():
    return next(w for w in read("workspaces") if w["is_focused"])


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def wait_for(test, label, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = test()
        if value:
            return value
        time.sleep(0.2)
    raise RuntimeError(f"Timed out waiting for {label}")


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    original = focus()
    if not any(w.get("workspace_id") == original["id"] for w in read("windows")):
        # An empty one would be the import's own workspace, and Niri drops it again once we leave.
        raise RuntimeError("The focused workspace must hold a window")
    real_state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "flow"
    real_files = [real_state / name for name in ("spaces.json", "groups.json", "session.json")]
    real_digests = [digest(path) for path in real_files]
    token = uuid.uuid4().hex[:8]
    apps = {s: f"flow-snapshot-{token}-{s}" for s in ("existing", "a", "b")}
    missing = f"flow-snapshot-{token}-gone"
    secret = f"Private title {token}"
    shots = Path(os.environ["FLOW_SCREENSHOTS"]) if os.environ.get("FLOW_SCREENSHOTS") else None
    started = []
    with IsolatedState("flow-snapshot-import-") as tmp:
        data = Path(tmp) / "data"
        (data / "applications").mkdir(parents=True)
        for suffix, app in apps.items():
            (data / "applications" / f"{app}.desktop").write_text(
                f"[Desktop Entry]\nType=Application\nName=Snapshot {suffix}\n"
                f"Exec=foot --app-id {app} --title \"{secret} {suffix}\" sleep 600\n")
        wayland = os.environ.get("WAYLAND_DISPLAY", "wayland-1")
        env = dict(os.environ, XDG_STATE_HOME=tmp, XDG_DATA_HOME=str(data),
                   WAYLAND_DISPLAY=wayland if wayland.startswith("/") else str(Path(os.environ["XDG_RUNTIME_DIR"]) / wayland))
        state = Path(tmp) / "flow"

        def tool(*command, isolated=False, trace=None):
            if isolated:
                command = [*ISOLATE, *(["strace", "-qq", "-e", "trace=connect", "-o", str(trace)] if trace else []),
                           *command]
            process = subprocess.run(command, env=env, text=True, stdout=subprocess.PIPE, timeout=120)
            value = json.loads(process.stdout)
            return process.returncode, value

        def ok(*command, **kwargs):
            code, value = tool(*command, **kwargs)
            if code or "error" in value:
                raise RuntimeError(f"{command}: {value}")
            return value

        def ours():
            return [w for w in read("windows") if w.get("app_id") in apps.values()]

        def world():
            """Every window's place, every workspace and the focus: what preview must not change."""
            windows = sorted((w["id"], w["app_id"], w["workspace_id"], w["is_floating"],
                              tuple(w["layout"]["pos_in_scrolling_layout"] or ())) for w in read("windows"))
            workspaces = sorted((w["id"], w["idx"], w["output"]) for w in read("workspaces"))
            return windows, workspaces, focus()["id"], [digest(state / n) for n in ("spaces.json", "groups.json",
                                                                                  "session.json")]

        def only_unix(trace, label):
            calls = [line for line in trace.read_text().splitlines() if "connect(" in line]
            check(calls, f"{label}: strace saw no connect at all, so it traced nothing")
            other = [line for line in calls if "AF_UNIX" not in line]
            check(not other, f"{label} connected to something other than a Unix socket: {other}")
            return len(calls)

        try:
            # The namespace has no way out: only a loopback, down, and no route anywhere.
            probe = subprocess.run([*ISOLATE, "python3", "-c",
                                    "import socket, json\nnames = socket.if_nameindex()\ns = socket.socket()\n"
                                    "s.settimeout(3)\ntry:\n    s.connect(('1.1.1.1', 443)); reached = True\n"
                                    "except OSError:\n    reached = False\nprint(json.dumps([names, reached]))"],
                                   text=True, capture_output=True, timeout=30, check=True)
            names, reached = json.loads(probe.stdout)
            check(names == [[1, "lo"]] and reached is False, f"The network namespace is not isolated: {probe.stdout}")

            # The existing work: a window with a private title on the focused workspace, pinned.
            started.append(subprocess.Popen(["foot", "--app-id", apps["existing"], "--title", secret, "sleep", "600"],
                                            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, start_new_session=True))
            existing = wait_for(lambda: next((w for w in ours() if w["app_id"] == apps["existing"]), None),
                                "the existing window")
            wait_for(lambda: focus().get("active_window_id") == existing["id"], "the existing window's focus")
            home = focus()
            check(home["output"] == original["output"], "The existing window opened on another display")
            ok("flow-spaces", "pin", str(existing["id"]))
            existing = next(w for w in ours() if w["id"] == existing["id"])  # pinned spaces come first
            pins_before = json.loads((state / "spaces.json").read_text())["pins"]
            check([p["app_id"] for p in pins_before] == [apps["existing"]], f"Unexpected pins: {pins_before}")
            empty_before = {w["id"] for w in read("workspaces")} - {w["workspace_id"] for w in read("windows")}

            exported = Path(tmp) / "exported.json"
            answer = ok("flow-recovery", "export", str(exported))
            text = exported.read_text()
            check({"apps": [f"{apps['existing']}.desktop"], "pin": 1} in json.loads(text)["spaces"],
                  f"Unexpected export: {text} ({answer})")
            check(secret not in text and not re.search(rf"\b{existing['id']}\b", text),
                  f"The export holds a title or a window ID: {text}")

            snapshot = Path(tmp) / "luma.json"
            snapshot.write_text(json.dumps({"format": "flow-workspace-snapshot", "version": 1, "spaces": [
                {"apps": [f"{missing}.desktop"]},
                {"apps": [f"{apps['a']}.desktop", f"{apps['b']}.desktop"],
                 "group": {"name": "Writing", "layout": "stack"}, "pin": 1},
                {"apps": [f"{apps['existing']}.desktop"]}]}))

            before = world()
            if shots:
                subprocess.run(["grim", str(shots / "1-before-import.png")], check=True, timeout=30)
            preview = ok("flow-recovery", "preview", str(snapshot), isolated=True, trace=Path(tmp) / "preview.trace")
            connects = only_unix(Path(tmp) / "preview.trace", "preview")
            check(preview["acted"] is False, f"The preview says it acted: {preview}")
            check(preview["open"] == [{"apps": [f"{apps['a']}.desktop", f"{apps['b']}.desktop"],
                                       "group": {"name": "Writing", "layout": "stack"}, "pin": 1}],
                  f"Unexpected spaces to open: {preview['open']}")
            check([(e.get("app"), e["reason"].split(";")[0]) for e in preview["left_out"]]
                  == [(f"{missing}.desktop", "not installed"), (f"{apps['existing']}.desktop", "already open here")],
                  f"The preview does not report what it leaves out: {preview['left_out']}")
            check(preview["pins"] == [{"space": "Writing", "position": 2, "display": preview["workspace"]["display"]}],
                  f"Unexpected pins: {preview['pins']}")
            check(world() == before, "The preview changed a window, a workspace, the focus or a state file")
            time.sleep(3)
            check(world() == before and not [w for w in ours() if w["app_id"] in (apps["a"], apps["b"])],
                  "Something changed after the preview, before any confirmation")

            code, stale = tool("flow-recovery", "import", str(snapshot), "000000000000", isolated=True)
            check(code == 1 and "Nothing was imported" in stale.get("error", ""), f"A stale confirmation acted: {stale}")
            check(world() == before, "A refused import changed something")

            result = ok("flow-recovery", "import", str(snapshot), preview["confirm"], isolated=True,
                        trace=Path(tmp) / "import.trace")
            connects += only_unix(Path(tmp) / "import.trace", "import")
            target = focus()
            check(target["id"] != home["id"] and target["id"] in empty_before and target["output"] == home["output"],
                  f"The import did not use a new workspace on this display: {target}")
            group = wait_for(lambda: (lambda ws: sorted(ws, key=lambda w: w["layout"]["pos_in_scrolling_layout"])
                                      if len(ws) == 2 else None)(
                [w for w in ours() if w["app_id"] in (apps["a"], apps["b"]) and w["workspace_id"] == target["id"]]),
                "the Group's apps on the new workspace")
            positions = [w["layout"]["pos_in_scrolling_layout"] for w in group]
            check([w["app_id"] for w in group] == [apps["a"], apps["b"]] and positions == [[1, 1], [1, 2]],
                  f"The Group is not one column in member order: {[(w['app_id'], p) for w, p in zip(group, positions)]}")
            if shots:
                time.sleep(1)  # Niri's workspace and column animations end
                subprocess.run(["grim", str(shots / "2-after-import.png")], check=True, timeout=30)
            now = next(w for w in read("windows") if w["id"] == existing["id"])
            check((now["workspace_id"], now["layout"]["pos_in_scrolling_layout"])
                  == (existing["workspace_id"], existing["layout"]["pos_in_scrolling_layout"]),
                  f"The existing window moved: {now}")
            check(sorted(w for w in world()[0] if w[0] not in {g["id"] for g in group})
                  == sorted(w for w in before[0]), "A window other than the imported ones changed")
            groups = json.loads((state / "groups.json").read_text())["groups"]
            check([(g["label"], g["members"], g.get("named")) for g in groups]
                  == [("Writing", [apps["a"], apps["b"]], True)], f"Unexpected Groups: {groups}")
            pins = json.loads((state / "spaces.json").read_text())["pins"]
            keep = ("id", "label", "app_id", "display")
            check([{k: p.get(k) for k in keep} for p in pins[:1]] == [{k: p.get(k) for k in keep} for p in pins_before]
                  and len(pins) == 2 and pins[1].get("group_id") == groups[0]["id"]
                  and pins[1].get("display") == pins_before[0].get("display"),
                  f"The existing pin changed or the Group's pin was not appended: {pins}")
            check(result["left_out"] == preview["left_out"] and result["pins"] == [{"space": "Writing", "pinned": True}]
                  and result["message"].startswith("Imported 1 of 1 spaces onto a new workspace. Left out: "),
                  f"Unexpected import answer: {result}")
            print(json.dumps({"passed": True, "preview": preview, "import": result, "unix_connects": connects}))
        finally:
            for window in ours():
                niri("action", "close-window", "--id", window["id"])
            wait_for(lambda: not ours(), "our windows to close")
            for process in started:
                process.wait(timeout=10)
            niri("action", "focus-workspace", original["idx"])
            wait_for(lambda: focus()["id"] == original["id"], "the original workspace")
    if [digest(path) for path in real_files] != real_digests:
        raise RuntimeError("The real Flow state changed")


if __name__ == "__main__":
    main()
