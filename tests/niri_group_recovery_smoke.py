#!/usr/bin/env python3
"""A Group's life across a restart: recovered with every app closed, then changed, renamed and dissolved.

Run inside a Niri session from the Flow checkout, with the focused output's active workspace empty and
``flow-spaces`` and ``flow-recovery`` on PATH running this checkout's ``session/spaces.py`` and
``session/restore.py``. Flow state is isolated, and the apps are Foot windows with app IDs of their own,
each with a desktop entry in an isolated data folder so recovery can open them again.

A reboot closes every app and starts a new Niri session. Both are simulated without ending the guest's
session: the windows are closed, the saved session, the Groups' window bindings and the pins are marked as
coming from an earlier Niri session, an app is opened as at login (once), and recovery runs as the shell runs
it at the start of a session (``flow-recovery auto-start``, which reports success only when every saved window
was restored and, with a pinned app or a side-by-side Group on the workspace, confirmed in its saved order). Checked, for a pinned, renamed stacked Group beside a pinned app and
then for a side-by-side Group with a saved split:
- recovery opens every app again and rebuilds the Group: one column in saved member order, or its
  columns at the saved proportions;
- the Group keeps its ID and name, its pin and the neighbouring pin reach the new windows, and the Group
  is bound to them in this Niri session;
- the recovered Group goes on working: its layout changes both ways, and it dissolves with its apps open;
- with the pinned Group's apps apart and an unpinned app after them, Rejoin Group stacks them (the manual path),
  and so does recovery; Rejoin Group does too with a pinned app still after them, another display's, or this
  display's while a side-by-side Group pauses pin ordering, and once that Group is stacked or dissolved, the
  pins go back in pin order (at once, or at the next refresh); a layout change killed halfway pauses pin
  ordering too: one to side by side stays paused once finished, one back to a stack puts the pins back; and a
  change to side by side killed while its widths settle finishes at the saved split.
Real state files are checksummed.
"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
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
    apps = {s: f"flow-recovery-{token}-{s}" for s in "abnulv"}
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

        def killed_at(action, *args, nth=1, then_sleep=False):
            """Runs ``flow-spaces *args`` and kills it, as a crash would, at its ``nth`` request for Niri's ``action``:
            every earlier Niri action is done, and that one is never sent. With ``then_sleep`` it is sent, and the kill
            comes once the helper next sleeps (its kernel wait shows ``nanosleep``). A ``niri`` ahead on PATH does it."""
            held = Path(tmp) / "held"
            held.mkdir(exist_ok=True)
            (held / "count").write_text("0")
            real = shutil.which("niri")
            if not real:
                raise RuntimeError("niri is not on PATH")
            if then_sleep:  # in the background, with no pipe of the helper's held open
                kill = (f'"{real}" "$@"; s=$?; p=$PPID; (i=0; while [ $i -lt 500 ]; do '
                        f'grep -q nanosleep /proc/$p/wchan && {{ kill -KILL $p; exit; }}; sleep 0.01; i=$((i + 1)); '
                        f'done) </dev/null >/dev/null 2>&1 & exit $s')
            else:
                kill = "kill -KILL $PPID; exit 1"
            (held / "niri").write_text(
                f'#!/bin/sh\ncase " $* " in *" {action} "*)\n'
                f'  n=$(($(cat "{held}/count") + 1)); echo $n > "{held}/count"\n'
                f'  if [ $n -eq {nth} ]; then {kill}; fi;;\nesac\nexec "{real}" "$@"\n')
            (held / "niri").chmod(0o755)
            process = subprocess.run(["flow-spaces", *args], env=dict(env, PATH=f"{held}:{env['PATH']}"),
                                     stdout=subprocess.DEVNULL, timeout=90)
            if process.returncode != -signal.SIGKILL:
                raise RuntimeError(f"flow-spaces {args} ended (exit {process.returncode}) before it was killed at its "
                                   f"request {nth} for {action}")

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

        def move_pin(pin_id, display):
            """Give a pin to another display, as when its monitor is unplugged, or back; returns its display before."""
            saved = json.loads((state / "spaces.json").read_text())
            saved["displays"].setdefault("flow-test:elsewhere", {"connector": "HDMI-A-9", "label": "Elsewhere"})
            pin = next(p for p in saved["pins"] if p["id"] == pin_id)
            before, pin["display"] = pin["display"], display
            if not any(p.get("display") == "flow-test:elsewhere" for p in saved["pins"]):
                del saved["displays"]["flow-test:elsewhere"]  # back home: the made-up display goes too
            (state / "spaces.json").write_text(json.dumps(saved))
            return before

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
            open_app("l")  # an app opened at login, before recovery runs: not in the saved session
            recover()  # succeeds only when every saved window is back in its saved order
            expect(["l", "ab", "n"], "the recovered stacked Group after the app opened at login")
            _, current, bound = bound_here(group_id, "ab")
            expect(["ab", "n", "l"], "the pins first again")
            niri("action", "close-window", "--id", window("l")["id"])
            expect(["ab", "n"], "the app opened at login closed")
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
            steps.append("with every app closed, state from an earlier Niri session and an app opened at login, "
                         "automatic recovery opened all three again, rebuilt the pinned, renamed stacked Group in saved "
                         "order, bound to the new windows, and succeeded; both pins reached them")

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
            steps.append("with the pinned Group's apps apart and an unpinned app after them, Rejoin Group was offered "
                         "and stacked them; the pins went back in order and the Group pin reached the Group")

            # And with a pinned app still after the Group's apps: this display's pin ordering would move it ahead
            # first, so the app's pin belongs to another display for now and stays where it is.
            home_display = move_pin(app_pin, "flow-test:elsewhere")
            niri("action", "consume-or-expel-window-right", "--id", window("b")["id"])
            entry = next(e for e in spaces("refresh")["incomplete_groups"] if e["id"] == group_id)
            expect(["a", "b", "n", "u"], "the Group's apps apart with a pinned app still after them")
            if not entry["can_rejoin"]:
                raise RuntimeError(f"Rejoin Group was not offered with a pinned app after the Group: {entry}")
            spaces("rejoin-group", group_id)
            expect(["ab", "n", "u"], "the pinned Group rejoined before the pinned app")
            move_pin(app_pin, home_display)
            current = spaces("refresh")
            expect(["ab", "n", "u"], "both pins in pin order")
            pins = {p["id"]: p for p in current["pins"]}
            if set(pins[group_pin]["window_ids"]) != {window("a")["id"], window("b")["id"]}:
                raise RuntimeError(f"The Group pin did not reach the rejoined Group: {current['pins']}")
            steps.append("with another display's pinned app still after the pinned Group's apps, Rejoin Group was "
                         "offered and stacked them; the columns after closed up in order and the Group pin reached it")

            # And with this display's own pinned app after them: possible only while pin ordering is paused, as it
            # is while a side-by-side Group is on the workspace. A second Group of u and a new app v gives one.
            niri("action", "focus-window", "--id", window("u")["id"])
            open_app("v")
            expect(["ab", "n", "u", "v"], "another app after u")
            niri("action", "focus-window", "--id", window("u")["id"])
            spaces("stack")
            other_id = next(sp["group_id"] for sp in spaces("create-group")["spaces"] if sp["active"])
            spaces("group-layout", other_id)
            expect(["ab", "n", "u", "v"], "a side-by-side Group after the pinned app")
            niri("action", "consume-or-expel-window-right", "--id", window("b")["id"])
            apart = spaces("refresh")
            entry = next(e for e in apart["incomplete_groups"] if e["id"] == group_id)
            expect(["a", "b", "n", "u", "v"], "this display's pinned app after the Group's apps, pin ordering paused")
            if app_pin not in {p["id"] for p in apart["pins"]}:  # the refresh lists this display's pins only
                raise RuntimeError(f"The pinned app's pin is not this display's: {apart['pins']}")
            if not entry["can_rejoin"]:
                raise RuntimeError(f"Rejoin Group was not offered with this display's pinned app after it: {entry}")
            spaces("rejoin-group", group_id)
            current = spaces("refresh")
            expect(["ab", "n", "u", "v"], "the pinned Group rejoined, the side-by-side Group intact after it")
            pins = {p["id"]: p for p in current["pins"]}
            if (set(pins[group_pin]["window_ids"]) != {window("a")["id"], window("b")["id"]}
                    or group(other_id).get("layout") != "side-by-side"
                    or not any(sp.get("group_id") == other_id and sp.get("group_layout") == "side-by-side"
                               for sp in current["spaces"])):
                raise RuntimeError(f"The rejoin left a Group or pin behind: {current['pins']}, {group(other_id)}")
            steps.append("with this display's pinned app after the pinned Group's apps, while a side-by-side Group "
                         "paused pin ordering, Rejoin Group was offered and stacked them; the side-by-side Group "
                         "stayed whole after them and the Group pin reached the Group")

            # The pause ending by the unpinned side-by-side Group's own change (the pinned Group's ending is checked
            # above, where the next refresh puts the app pin first). The pinned Group goes to the end, behind it.
            def pinned_last(label):
                niri("action", "focus-window", "--id", window("a")["id"])
                niri("action", "move-column-to-index", 4)
                expect(["n", "u", "v", "ab"], f"the pinned Group moved to the end, {label}")
                paused = spaces("refresh")  # synchronous: any reordering is done when it returns
                if columns() != ["n", "u", "v", "ab"]:
                    raise RuntimeError(f"Pin ordering ran while paused, {label}: {columns()}")
                if {group_pin, app_pin} - {p["id"] for p in paused["pins"] if p.get("available")}:
                    raise RuntimeError(f"A pin is not this display's or not available while paused: {paused['pins']}")
                niri("action", "focus-window", "--id", window("u")["id"])

            def pins_reached(current):
                pins = {p["id"]: p for p in current["pins"]}
                if (set(pins[group_pin]["window_ids"]) != {window("a")["id"], window("b")["id"]}
                        or pins[app_pin]["window_ids"] != [window("n")["id"]]):
                    raise RuntimeError(f"A pin lost its windows after the pause: {current['pins']}")

            # Stacked: a layout change ends with a refresh, so the pins go back at once.
            pinned_last("before stacking the second Group")
            spaces("group-layout", other_id)
            expect(["ab", "n", "uv"], "the pins back in pin order once the second Group, stacked, ended the pause")
            pins_reached(spaces("refresh"))
            expect(["ab", "n", "uv"], "the pins still in pin order at the next refresh")
            # An unfinished layout change pauses pin ordering too, with no Group side by side yet. The stacked second
            # Group's change to side by side is killed once its apps are split, before their widths are set: the
            # pending layout saved, the stored layout still a stack, and the apps in columns of their own.
            niri("action", "focus-window", "--id", window("u")["id"])
            killed_at("set-window-width", "group-layout", other_id)
            expect(["ab", "n", "u", "v"], "the second Group's apps split, its layout change killed")
            if group(other_id).get("pending_layout") != "side-by-side" or group(other_id).get("layout", "stack") != "stack":
                raise RuntimeError(f"The killed change to side by side did not leave it unfinished: {group(other_id)}")
            niri("action", "focus-window", "--id", window("a")["id"])
            niri("action", "move-column-to-index", 4)
            expect(["n", "u", "v", "ab"], "the pinned Group moved to the end, a layout change unfinished")
            paused = spaces("refresh")  # synchronous: any reordering is done when it returns
            if columns() != ["n", "u", "v", "ab"]:
                raise RuntimeError(f"Pin ordering ran during an unfinished layout change: {columns()}")
            entry = next((e for e in paused["incomplete_groups"] if e["id"] == other_id), None)
            if not entry or not entry.get("can_resume_layout"):
                raise RuntimeError(f"Finish layout change was not offered: {paused['incomplete_groups']}")
            finished = spaces("resume-group-layout", other_id)  # Finish layout change: side by side, still paused
            expect(["n", "u", "v", "ab"], "the layout change finished, the second Group side by side")
            if group(other_id).get("pending_layout") or group(other_id).get("layout") != "side-by-side":
                raise RuntimeError(f"The finished layout change left a pending layout: {group(other_id)}")
            if not any(sp.get("group_id") == other_id and sp.get("group_layout") == "side-by-side"
                       for sp in finished["spaces"]):
                raise RuntimeError(f"The finished Group is not recognised side by side: {finished['spaces']}")
            steps.append("with the pinned Group moved behind the second Group while its change to side by side was "
                         "unfinished, killed with its apps split, a refresh left the pins out of order and offered "
                         "Finish layout change, which put the Group side by side with the pins still paused")

            # And an unfinished change back to a stack: paused until finished, and the finishing puts the pins back,
            # since the Group is then neither side by side nor mid-change. Killed once its apps are joined, before the
            # stack is given its full width. The pause holds by the stored layout, still side by side; the pending
            # layout alone is what holds it in the step above.
            niri("action", "focus-window", "--id", window("u")["id"])
            killed_at("set-window-width", "group-layout", other_id)
            expect(["n", "uv", "ab"], "the second Group's apps joined, its change to a stack killed")
            if group(other_id).get("pending_layout") != "stack" or group(other_id).get("layout") != "side-by-side":
                raise RuntimeError(f"The killed change to a stack did not leave it unfinished: {group(other_id)}")
            paused = spaces("refresh")  # synchronous: any reordering is done when it returns
            if columns() != ["n", "uv", "ab"]:
                raise RuntimeError(f"Pin ordering ran during an unfinished change to a stack: {columns()}")
            entry = next((e for e in paused["incomplete_groups"] if e["id"] == other_id), None)
            if not entry or not entry.get("can_resume_layout"):
                raise RuntimeError(f"Finish layout change was not offered: {paused['incomplete_groups']}")
            spaces("resume-group-layout", other_id)  # ends with a refresh, like a layout change
            expect(["ab", "n", "uv"], "the pins back in pin order once the change to a stack was finished")
            if group(other_id).get("pending_layout") or group(other_id).get("layout", "stack") != "stack":
                raise RuntimeError(f"The finished change to a stack left a pending layout: {group(other_id)}")
            # Finishing also gave the stack its full width, the change's last Niri action.
            full = json.loads(niri("--json", "focused-output"))["logical"]["width"]
            if window("u")["layout"]["tile_size"][0] < 0.9 * full:
                raise RuntimeError(f"The finished stack is not full width: {window('u')['layout']}, output {full}")
            pins_reached(spaces("refresh"))
            expect(["ab", "n", "uv"], "the pins still in pin order at the next refresh")
            steps.append("with the second Group's change back to a stack killed with its apps joined, a "
                         "refresh left the pins out of order and offered Finish layout change; finishing it put the "
                         "pinned Group and the pinned app back first, in pin order")

            # Side by side again, killed while the widths settle: in the pause that lets the apps take the widths sent,
            # before they are measured and the layout saved. A saved 25/75 split (written in as a stored preference)
            # tells the widths sent apart from the widths Niri gives new columns.
            saved = json.loads((state / "groups.json").read_text())
            next(g for g in saved["groups"] if g["id"] == other_id)["side_ratio"] = 0.25
            (state / "groups.json").write_text(json.dumps(saved))

            def share():
                sizes = [window(s)["layout"]["tile_size"][0] for s in "uv"]
                return sizes[0] / sum(sizes)

            niri("action", "focus-window", "--id", window("u")["id"])
            killed_at("set-window-width", "group-layout", other_id, nth=2, then_sleep=True)
            expect(["ab", "n", "u", "v"], "the second Group's apps split, killed while the widths settle")
            wait_for(lambda: abs(share() - 0.25) <= 0.05, lambda: f"the widths sent before the kill: u has {share():.2f}")
            if group(other_id).get("pending_layout") != "side-by-side" or group(other_id).get("layout", "stack") != "stack":
                raise RuntimeError(f"The change killed while the widths settle did not leave it unfinished: {group(other_id)}")
            entry = next((e for e in spaces("refresh")["incomplete_groups"] if e["id"] == other_id), None)
            if not entry or not entry.get("can_resume_layout"):
                raise RuntimeError(f"Finish layout change was not offered after a kill while the widths settle: {entry}")
            finished = spaces("resume-group-layout", other_id)
            expect(["ab", "n", "u", "v"], "the second Group side by side again, finished")
            if (group(other_id).get("pending_layout") or group(other_id).get("layout") != "side-by-side"
                    or abs(share() - 0.25) > 0.05):
                raise RuntimeError(f"Finishing after a kill while the widths settle: {group(other_id)}, u has {share():.2f}")
            if not any(sp.get("group_id") == other_id and sp.get("group_layout") == "side-by-side"
                       for sp in finished["spaces"]):
                raise RuntimeError(f"The finished Group is not recognised side by side: {finished['spaces']}")
            steps.append("with the second Group's change to side by side killed while its widths settled, "
                         "Finish layout change was offered and put it side by side at its saved 25/75 split")

            # Dissolved while side by side: dissolving does not order pins, so they go back at the next refresh.
            pinned_last("before dissolving the second Group")
            spaces("dissolve-group", other_id)
            if columns() != ["n", "u", "v", "ab"]:
                raise RuntimeError(f"Dissolving the side-by-side Group moved columns: {columns()}")
            pins_reached(spaces("refresh"))
            expect(["ab", "n", "u", "v"], "the pins back in pin order at the refresh after the dissolve")
            if any(g["id"] == other_id for g in groups()):
                raise RuntimeError("The dissolved second Group is still saved")
            niri("action", "close-window", "--id", window("v")["id"])
            expect(["ab", "n", "u"], "v closed")
            steps.append("with the pinned Group moved behind the unpinned side-by-side Group, a refresh left the pins "
                         "out of order while that Group paused pin ordering; stacking it put the pinned Group and the "
                         "pinned app back first, in pin order, and so did the next refresh after dissolving it while "
                         "side by side")

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
            # Dissolving with a pinned space after the Group: the app's pin belongs to another display for now (as
            # when its monitor was unplugged), so this display's pin ordering leaves it after the Group.
            home_display = move_pin(app_pin, "flow-test:elsewhere")
            spaces("unpin", group_pin)
            expect(["ab", "n"], "another display's pin after the unpinned Group")
            niri("action", "focus-window", "--id", window("a")["id"])
            spaces("dissolve-group", group_id)
            expect(["a", "b", "n"], "the recovered Group dissolved, the pinned space moved right")
            move_pin(app_pin, home_display)
            spaces("refresh")
            expect(["n", "a", "b"], "the app pin first once it is this display's again")  # pinned spaces lead
            if any(g["id"] == group_id for g in groups()):
                raise RuntimeError("The dissolved Group is still saved")
            steps.append("the recovered Group changed to side by side and back, and dissolved with its apps open, "
                         "with another display's pinned space after it moving right in order")

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
