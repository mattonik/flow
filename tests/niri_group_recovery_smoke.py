#!/usr/bin/env python3
"""A Group's life across a restart: recovered with every app closed, then changed, renamed and dissolved.

Run inside a Niri session from the Flow checkout, with the focused output's active workspace empty and
``flow-spaces`` and ``flow-recovery`` on PATH running this checkout's ``session/spaces.py`` and
``session/restore.py``. Flow state is isolated, and the apps are Foot windows with app IDs of their own,
each with a desktop entry in an isolated data folder so recovery can open them again.

A reboot closes every app and starts a new Niri session. Both are simulated without ending the guest's
session: the windows are closed, the saved session, the Groups' window bindings and the pins are marked as
coming from an earlier Niri session, and recovery runs as the shell runs it at the start of a session
(``flow-recovery auto-start``, which reports success only when every window was restored). Checked, for a pinned, renamed stacked Group beside a pinned app and
then for a side-by-side Group with a saved split:
- recovery opens every app again and rebuilds the Group: one column in saved member order, or its
  columns at the saved proportions;
- the Group keeps its ID and name, its pin and the neighbouring pin reach the new windows, and the Group
  is bound to them in this Niri session;
- the recovered Group goes on working: its layout changes both ways, and it dissolves with its apps open;
- with the pinned Group's apps apart and another app after them, Rejoin Group stacks them (the manual path),
  and so does recovery.
Real state files are checksummed.
"""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from isolated_state import IsolatedState


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
    raise RuntimeError(f"Timed out waiting for {label() if callable(label) else label}")


def main():
    workspace = focus()
    real_state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "flow"
    real_files = [real_state / name for name in ("spaces.json", "groups.json", "session.json")]
    real_digests = [digest(path) for path in real_files]
    if any(w.get("workspace_id") == workspace["id"] for w in read("windows")):
        raise RuntimeError("The focused output's active workspace must be empty")
    token = uuid.uuid4().hex[:8]
    apps = {s: f"flow-recovery-{token}-{s}" for s in "abnu"}
    steps, result = [], None
    with IsolatedState("flow-group-recovery-") as tmp:
        data = Path(tmp) / "data"
        (data / "applications").mkdir(parents=True)
        for suffix, app in apps.items():
            (data / "applications" / f"{app}.desktop").write_text(
                f"[Desktop Entry]\nType=Application\nName=Recovery {suffix.upper()}\nExec=foot --app-id {app}\n")
        # A runtime folder of its own: automatic recovery keeps its once-per-Niri-session marker there.
        runtime = Path(tmp) / "runtime"
        runtime.mkdir(mode=0o700)
        wayland = os.environ.get("WAYLAND_DISPLAY", "wayland-1")
        env = dict(os.environ, XDG_STATE_HOME=tmp, XDG_DATA_HOME=str(data), XDG_RUNTIME_DIR=str(runtime),
                   WAYLAND_DISPLAY=wayland if wayland.startswith("/") else str(Path(os.environ["XDG_RUNTIME_DIR"]) / wayland))
        state = Path(tmp) / "flow"

        def tool(name, *args):
            process = subprocess.run([name, *args], env=env, text=True, stdout=subprocess.PIPE, timeout=90)
            value = json.loads(process.stdout)
            if process.returncode or "error" in value:
                raise RuntimeError(f"{name} {args}: {value}")
            return value

        def spaces(*args):
            return tool("flow-spaces", *args)

        def ours():
            return [w for w in read("windows") if w.get("app_id") in apps.values()]

        def window(suffix):
            found = [w for w in ours() if w["app_id"] == apps[suffix]]
            if len(found) != 1:
                raise RuntimeError(f"Window {suffix} is missing or duplicated: {found}")
            return found[0]

        def columns():
            """Our tiled windows by column, as app suffixes: ["ab", "n"] is a stack of a and b, then n."""
            placed = sorted((w["layout"]["pos_in_scrolling_layout"], w["app_id"][-1]) for w in ours()
                            if w["workspace_id"] == workspace["id"] and not w["is_floating"])
            found = {}
            for (column, _), suffix in placed:
                found.setdefault(column, []).append(suffix)
            return ["".join(found[c]) for c in sorted(found)]

        def expect(layout, label):
            wait_for(lambda: columns() == layout, lambda: f"{label}: {layout}, have {columns()}")

        def open_app(suffix):
            subprocess.Popen(["foot", "--app-id", apps[suffix]], env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            wait_for(lambda: any(w["app_id"] == apps[suffix] for w in ours()), suffix)

        def groups():
            return json.loads((state / "groups.json").read_text())["groups"]

        def group(group_id):
            return next(g for g in groups() if g["id"] == group_id)

        def close_all():
            for w in ours():
                niri("action", "close-window", "--id", w["id"])
            wait_for(lambda: not ours(), "the apps to close")

        def reboot():
            """What a reboot leaves: no app open, the saved session, Group bindings and pins from another Niri
            session, and automatic recovery not yet run in this one."""
            close_all()
            session = json.loads((state / "session.json").read_text())
            session["session"] = "an-earlier-niri-session"
            (state / "session.json").write_text(json.dumps(session))
            saved = json.loads((state / "groups.json").read_text())
            for g in saved["groups"]:
                if isinstance(g.get("bound"), dict):
                    g["bound"]["session"] = "an-earlier-niri-session"
            (state / "groups.json").write_text(json.dumps(saved))
            pins = json.loads((state / "spaces.json").read_text())
            for pin in pins["pins"]:
                if "session" in pin:
                    pin["session"] = "an-earlier-niri-session"
            (state / "spaces.json").write_text(json.dumps(pins))
            for marker in runtime.glob("flow-recovery-*"):
                marker.unlink()

        def recover():
            """What the shell runs when it starts in a new Niri session with automatic recovery on."""
            answer = tool("flow-recovery", "auto-start")
            if answer.get("message") != "Automatic session recovery is ready.":
                raise RuntimeError(f"Automatic recovery after the apps closed failed: {answer}")

        def bound_here(group_id, suffixes):
            g = group(group_id)
            current = spaces("refresh")  # binds what it recognizes, as the shell's refreshes do
            live = {apps[s]: window(s)["id"] for s in suffixes}
            return g, current, (group(group_id).get("bound") or {}).get("windows") == live

        try:
            for index, suffix in enumerate("abn", 1):
                open_app(suffix)
                niri("action", "focus-window", "--id", window(suffix)["id"])
                niri("action", "move-column-to-index", index)
            expect(["a", "b", "n"], "the apps in order")

            # A pinned, renamed stacked Group beside a pinned app.
            niri("action", "focus-window", "--id", window("a")["id"])
            spaces("stack")
            group_id = next(s["group_id"] for s in spaces("create-group")["spaces"] if s.get("group_id"))
            tool("flow-recovery", "rename-group", group_id, "Recovery Desk")
            group_pin = next(p["id"] for p in spaces("pin-group", group_id)["pins"] if p.get("group_id") == group_id)
            niri("action", "focus-window", "--id", window("n")["id"])
            app_pin = next(p["id"] for p in spaces("pin")["pins"] if p["kind"] == "app")
            expect(["ab", "n"], "the stacked Group and its neighbour")
            tool("flow-recovery", "save")
            reboot()
            recover()
            expect(["ab", "n"], "the recovered stacked Group")
            _, current, bound = bound_here(group_id, "ab")
            pins = {p["id"]: p for p in current["pins"]}
            if group(group_id)["label"] != "Recovery Desk" or group(group_id)["members"] != [apps["a"], apps["b"]]:
                raise RuntimeError(f"The recovered Group lost its name or member order: {group(group_id)}")
            if not (pins.get(group_pin, {}).get("available") and pins.get(app_pin, {}).get("available")):
                raise RuntimeError(f"A pin did not reach the recovered windows: {current['pins']}")
            if set(pins[group_pin]["window_ids"]) != {window("a")["id"], window("b")["id"]} \
                    or pins[app_pin]["window_ids"] != [window("n")["id"]]:
                raise RuntimeError(f"A pin reached the wrong windows: {current['pins']}")
            if not bound:
                raise RuntimeError(f"The recovered Group is not bound to its new windows: {group(group_id)}")
            steps.append("with every app closed and state from an earlier Niri session, automatic recovery opened all "
                         "three again and rebuilt the pinned, renamed stacked Group in saved order, bound to the new "
                         "windows; both pins reached them")

            niri("action", "focus-window", "--id", window("a")["id"])
            spaces("group-layout", group_id)
            expect(["a", "b", "n"], "the recovered Group side by side")
            spaces("group-layout", group_id)
            expect(["ab", "n"], "the recovered Group stacked again")

            # The manual path: the pinned Group's apps in columns of their own with an unpinned app after them, as
            # when the user opens them again with recovery off. Rejoin Group stacks them all the same.
            niri("action", "focus-window", "--id", window("n")["id"])
            open_app("u")
            expect(["ab", "n", "u"], "an unpinned app after the pinned app")
            niri("action", "consume-or-expel-window-right", "--id", window("b")["id"])
            entry = next(e for e in spaces("refresh")["incomplete_groups"] if e["id"] == group_id)
            expect(["n", "a", "b", "u"], "the Group's apps apart, the app pin first")  # pins lead the strip
            if not entry["can_rejoin"]:
                raise RuntimeError(f"Rejoin Group was not offered: {entry}")
            spaces("rejoin-group", group_id)
            current = spaces("refresh")
            expect(["ab", "n", "u"], "the rejoined pinned Group back in pin order")
            pins = {p["id"]: p for p in current["pins"]}
            if not pins[group_pin]["available"] or set(pins[group_pin]["window_ids"]) != {window("a")["id"], window("b")["id"]}:
                raise RuntimeError(f"The Group pin did not reach the rejoined Group: {current['pins']}")

            # Recovery does the same: the pinned Group's apps apart with an app already open after them.
            tool("flow-recovery", "save")
            niri("action", "consume-or-expel-window-right", "--id", window("b")["id"])
            spaces("refresh")
            expect(["n", "a", "b", "u"], "the Group's apps apart again, an open app after them")
            for marker in runtime.glob("flow-recovery-*"):  # automatic recovery, as at the start of a session
                marker.unlink()
            recover()  # succeeds only when every window was restored in its saved order
            current = spaces("refresh")
            expect(["ab", "n", "u"], "the recovered pinned Group back in pin order")
            if not {p["id"]: p for p in current["pins"]}[group_pin]["available"]:
                raise RuntimeError(f"The Group pin did not reach the recovered Group: {current['pins']}")
            steps.append("with the pinned Group's apps apart and an app already open after them, automatic recovery "
                         "rebuilt the Group and succeeded, since the next refresh's pin order is the saved order")
            niri("action", "close-window", "--id", window("u")["id"])
            expect(["ab", "n"], "the unpinned app closed")
            steps.append("with the pinned Group's apps apart and an unpinned app after them, Rejoin Group was offered "
                         "and stacked them; the pins went back in order and the Group pin reached the Group")
            spaces("unpin", group_pin)
            expect(["n", "ab"], "the app pin first once the Group is unpinned")  # pinned spaces lead the strip
            niri("action", "focus-window", "--id", window("a")["id"])
            spaces("dissolve-group", group_id)
            expect(["n", "a", "b"], "the recovered Group dissolved")
            if any(g["id"] == group_id for g in groups()):
                raise RuntimeError("The dissolved Group is still saved")
            steps.append("the recovered Group changed to side by side and back, and dissolved with its apps open")

            # A side-by-side Group with a saved split.
            spaces("unpin", app_pin)
            niri("action", "focus-window", "--id", window("a")["id"])
            spaces("stack")
            side_id = next(s["group_id"] for s in spaces("create-group")["spaces"] if s.get("group_id"))
            spaces("group-layout", side_id)
            for _ in range(2):
                spaces("member-wider", str(window("a")["id"]))
            expect(["n", "a", "b"], "the side-by-side Group")
            split = group(side_id).get("side_ratio")
            saved_ratios = [split, 1 - split] if isinstance(split, float) else None
            if not saved_ratios or saved_ratios[0] <= saved_ratios[1]:
                raise RuntimeError(f"The wider first member was not saved: {group(side_id)}")
            tool("flow-recovery", "save")
            reboot()
            recover()
            expect(["n", "a", "b"], "the recovered side-by-side Group")
            widths = [window(s)["layout"]["tile_size"][0] for s in "ab"]
            if any(abs(w / sum(widths) - r) > 0.05 for w, r in zip(widths, saved_ratios)):
                raise RuntimeError(f"Recovery gave the Group {widths}, not the saved split {saved_ratios}")
            _, current, bound = bound_here(side_id, "ab")
            if not bound or group(side_id).get("layout") != "side-by-side":
                raise RuntimeError(f"The recovered side-by-side Group is not whole: {group(side_id)}")
            steps.append(f"a side-by-side Group with a {saved_ratios[0]:.2f}/{saved_ratios[1]:.2f} split was recovered "
                         "with every app closed: its columns, its split and its binding to the new windows")
            niri("action", "focus-window", "--id", window("a")["id"])
            spaces("group-layout", side_id)
            expect(["n", "ab"], "the recovered side-by-side Group stacked")
            spaces("dissolve-group", side_id)
            expect(["n", "a", "b"], "the recovered side-by-side Group dissolved")
            steps.append("the recovered side-by-side Group stacked and dissolved with its apps open")
            result = {"result": "pass", "niri": niri("version").strip(), "steps": steps}
        finally:
            try:
                close_all()
            except (OSError, RuntimeError, subprocess.SubprocessError):
                pass
    if [digest(path) for path in real_files] != real_digests:
        raise RuntimeError("A real Flow Group, pin, or session file changed")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, StopIteration, subprocess.SubprocessError, ValueError, KeyError) as exc:
        print(f"Group recovery test failed: {exc}", file=sys.stderr)
        sys.exit(1)
