#!/usr/bin/env python3
"""Keyboard focus and screen-reader semantics of a development shell's keyboard surfaces.

Desk, the Spaces switcher, Quick settings and Notifications are opened in turn. On each, Tab and
Shift+Tab walk every enabled control and the test checks that:
- every control is reached once, in reading order, and Shift+Tab walks the same order back;
- the focus ring is drawn around the focused control in the ``focus`` token and around no other
  (screenshot pixels, sampled where the control says its ring is);
- Return and keypad Enter activate the focused control;
- Escape closes the surface and keystrokes reach the app again, and none reached it while the surface
  was open.

Over AT-SPI it checks, on every visible surface, that each button and switch has a name containing
the words it shows (WCAG 2.5.3), that selected controls are described as "Current" and buttons whose
panel is showing as "Open", and that activity chips are progress bars.

Run inside a Niri session from the Flow checkout under ``dbus-run-session``, with:
- ``FLOW_SHELL`` set to the development ``shell.qml``;
- a Python with pyatspi and PyGObject (``GI_TYPELIB_PATH`` reaching Atspi);
- ``wtype`` and ``foot`` on PATH, and ``flow-spaces`` running this checkout's ``session/spaces.py``;
- no other Flow shell running (stop ``flow-shell`` first), since its surfaces would cover these;
- the session bus able to start the AT-SPI bus (``org.a11y.Bus``, from at-spi2-core).

It works on the focused output's empty workspace and focuses the original workspace again afterward.

The shell runs with its own ``XDG_RUNTIME_DIR``, state and configuration, so real settings, activities
and Flow state are never touched; real state files are checksummed. The test's Foot window is closed.
Screenshots go to a temporary directory (Niri also copies each to the clipboard).
"""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import uuid
import zlib

CODE = Path(__file__).resolve().parents[1] / "session"
# A ring check that fails keeps its screenshot here, outside the checkout.
FAILURE_SHOT = Path(tempfile.gettempdir()) / "flow-focus-walk-failure.png"


def niri(*args):
    return subprocess.check_output(["niri", "msg", *map(str, args)], text=True, timeout=10)


def read(kind):
    return json.loads(niri("-j", kind))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def wait_for(test, label, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = test()
        if value:
            return value
        time.sleep(0.15)
    raise RuntimeError(f"Timed out waiting for {label() if callable(label) else label}")


def png_pixels(path):
    """RGB(A) rows of an 8-bit, non-interlaced PNG, as Niri writes them; no imaging library needed."""
    data = path.read_bytes()
    pos, idat = 8, []
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, color, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
    if depth != 8 or color not in (2, 6) or interlace:
        raise RuntimeError(f"Unexpected screenshot format: depth {depth}, colour type {color}")
    bpp = 4 if color == 6 else 3
    stride = width * bpp
    raw = zlib.decompress(b"".join(idat))
    rows, prev = [], bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        kind, line = raw[start], bytearray(raw[start + 1:start + 1 + stride])
        if kind == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif kind == 2:
            line = bytearray((a + b) & 255 for a, b in zip(line, prev))
        elif kind == 3:
            for i in range(stride):
                line[i] = (line[i] + ((line[i - bpp] if i >= bpp else 0) + prev[i]) // 2) & 255
        elif kind == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b, c = prev[i], prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(line)
        prev = line
    return width, height, bpp, rows


def focus_token():
    """The dark appearance's focus colour, from the generated theme the shell itself uses."""
    theme = (CODE / "FloeTheme.qml").read_text()
    match = re.search(r'property color focus: light \? "#[0-9A-Fa-f]{6}" : "#([0-9A-Fa-f]{6})"', theme)
    return tuple(int(match.group(1)[i:i + 2], 16) for i in (0, 2, 4))


def reading_order(controls):
    """Rows from top to bottom, each left to right: a control joins a row its vertical centre falls in."""
    rows = []
    for control in sorted(controls, key=lambda c: (c["start"][1], c["start"][0])):
        x, y, w, h = control["start"]
        for row in rows:
            if row["top"] <= y + h / 2 <= row["bottom"]:
                row["items"].append(control)
                break
        else:
            rows.append({"top": y, "bottom": y + h, "items": [control]})
    return [c for row in rows for c in sorted(row["items"], key=lambda c: c["start"][0])]


def words(text):
    return re.findall(r"[\w%]+", text.lower())


def contains(name, label):
    """WCAG 2.5.3: the label's words appear, in order and together, in the name (case and punctuation aside)."""
    name, label = words(name), words(label)
    return bool(label) and any(name[i:i + len(label)] == label for i in range(len(name) - len(label) + 1))


def main():
    shell = os.environ.get("FLOW_SHELL")
    bus = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    if not shell or "unix:" not in bus:
        raise RuntimeError("Run under dbus-run-session with FLOW_SHELL set")
    activatable = subprocess.run(["busctl", f"--address={bus}", "call", "org.freedesktop.DBus", "/org/freedesktop/DBus",
                                  "org.freedesktop.DBus", "ListActivatableNames"], text=True, stdout=subprocess.PIPE).stdout
    if '"org.a11y.Bus"' not in activatable:
        raise RuntimeError("The session bus cannot start the AT-SPI bus: at-spi2-core is not installed")
    others = "; ".join(line for line in subprocess.run(["pgrep", "-a", "quickshell"], text=True,
                                                       stdout=subprocess.PIPE).stdout.splitlines() if "shell.qml" in line)
    if others:
        raise RuntimeError(f"Another Flow shell is running; stop it (systemctl --user stop flow-shell) first: {others}")
    import gi
    gi.require_version("Atspi", "2.0")
    from gi.repository import Gio, GLib
    import pyatspi

    real_runtime = Path(os.environ["XDG_RUNTIME_DIR"])
    wayland = os.environ.get("WAYLAND_DISPLAY", "wayland-1")
    real_state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "flow"
    real_files = [real_state / name for name in ("spaces.json", "groups.json", "session.json")]
    real_digests = [digest(path) for path in real_files]
    original_workspace = next(w for w in read("workspaces") if w["is_focused"])
    output = next(o for o in read("outputs").values() if o.get("logical") and o["name"] == original_workspace["output"])
    screen = output["logical"]
    focus = focus_token()
    token = uuid.uuid4().hex[:8]
    app_id = f"flow-focus-{token}"
    processes, steps, problems, result, last_shot = [], [], [], None, [None]

    with tempfile.TemporaryDirectory(prefix="flow-focus-walk-") as tmp:
        tmp = Path(tmp)
        runtime, state, config, shots = (tmp / name for name in ("runtime", "state", "config", "shots"))
        runtime.mkdir(mode=0o700)
        for path in (state, config, shots):
            path.mkdir()
        # Reduced motion: every surface is drawn at rest when it is captured.
        (config / "floe-appearance.ini").write_text("[General]\nreduceMotion=true\n")
        (runtime / "flow-activities.json").write_text(json.dumps({"version": 1, "activities": [
            {"id": "a1", "owner": "Photos", "title": "Copy photos", "progress": 0.4, "detail": ""}]}))
        sink = tmp / "sink"
        sink.write_text("")
        env = dict(os.environ, XDG_STATE_HOME=str(state), XDG_CONFIG_HOME=str(config), XDG_RUNTIME_DIR=str(runtime),
                   WAYLAND_DISPLAY=wayland if wayland.startswith("/") else str(real_runtime / wayland),
                   PIPEWIRE_RUNTIME_DIR=str(real_runtime), QT_LINUX_ACCESSIBILITY_ALWAYS_ON="1")

        def ipc(*args):
            return subprocess.run(["quickshell", "ipc", "-p", shell, "call", "desk", *args], env=env, text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10).stdout.strip()

        def controls(surface=None):
            found = json.loads(ipc("controls") or "[]")
            return [c for c in found if surface is None or c["surface"] == surface]

        def key(*args, primer=["Shift_L"]):
            """Type with wtype. Niri does not resend a virtual keymap equal to the last one, so a client that
            got focus since would read the keys with the physical keymap (Tab as Escape). A lone modifier,
            Shift and Control in turn, makes each call's keymap differ from the one before."""
            primer[0] = "Control_L" if primer[0] == "Shift_L" else "Shift_L"
            subprocess.run(["wtype", "-k", primer[0], *args], check=True, timeout=10)
            time.sleep(0.12)

        def typed():
            return sink.read_text(errors="replace")

        def shell_app():
            desktop = pyatspi.Registry.getDesktop(0)
            for i in range(desktop.childCount):
                app = desktop.getChildAtIndex(i)
                if app is not None and app.get_process_id() == shell_process.pid:
                    return app
            return None

        def frame_of(size):
            app = shell_app()
            app.clear_cache()
            for i in range(app.childCount):
                frame = app.getChildAtIndex(i)
                extents = frame.queryComponent().getExtents(pyatspi.WINDOW_COORDS)
                if frame.getState().contains(pyatspi.STATE_SHOWING) and [extents.width, extents.height] == [round(v) for v in size]:
                    return frame
            raise RuntimeError(f"No showing AT-SPI window of size {size}")

        def nodes(node):
            """Visible descendants, including those a scrolled surface holds out of view (not "showing")."""
            for i in range(node.childCount):
                child = node.getChildAtIndex(i)
                if child is not None and child.getState().contains(pyatspi.STATE_VISIBLE):
                    yield child
                    yield from nodes(child)

        def rect(node):
            e = node.queryComponent().getExtents(pyatspi.WINDOW_COORDS)
            return [e.x, e.y, e.width, e.height]

        def matched(surface):
            """The surface's controls in the shell's order, each with the AT-SPI object at its place. Qt only
            exposes controls in view, so one a surface has scrolled away has no object until it is focused."""
            listed = controls(surface)
            if not listed:
                return None, []
            frame = frame_of(listed[0]["window"])
            objects = [n for n in nodes(frame) if n.getRole() in (pyatspi.ROLE_PUSH_BUTTON, pyatspi.ROLE_CHECK_BOX,
                                                                  pyatspi.ROLE_TOGGLE_BUTTON, pyatspi.ROLE_SWITCH)]
            width, height = listed[0]["window"]
            for control in listed:
                x, y, w, h = control["rect"]
                near = [o for o in objects if all(abs(a - b) <= 1 for a, b in zip(rect(o), control["rect"]))]
                in_view = x >= 0 and y >= 0 and x + w <= width and y + h <= height
                if len(near) > 1 or (in_view and not near):
                    raise RuntimeError(f"{surface}: {len(near)} AT-SPI objects at {control['rect']} for {control['text']!r}; "
                                       f"AT-SPI has {[(o.name, rect(o)) for o in objects]}")
                control["node"] = near[0] if near else None
            if len(objects) != sum(c["node"] is not None for c in listed):
                raise RuntimeError(f"{surface}: AT-SPI has controls the shell does not show: {[o.name for o in objects]}")
            return frame, listed

        def focused(frame):
            frame.clear_cache()
            hits = [n for n in nodes(frame) if n.getState().contains(pyatspi.STATE_FOCUSED)]
            if len(hits) > 1:
                raise RuntimeError(f"{len(hits)} objects report focus at once: {[(n.name, rect(n)) for n in hits]}")
            return hits[0] if hits else None

        def focused_index(surface, frame):
            """Which of the surface's controls (by place in the shell's list) AT-SPI reports as focused."""
            node = focused(frame)
            if node is None:
                return None, None, controls(surface)
            listed = controls(surface)
            here = [i for i, c in enumerate(listed) if all(abs(a - b) <= 1 for a, b in zip(rect(node), c["rect"]))]
            if len(here) != 1:
                raise RuntimeError(f"{surface}: focus is on {node.name!r} at {rect(node)}, not on one of its controls")
            return here[0], node, listed

        def origin(surface, size):
            """Where a surface sits on screen, from its anchors (Desk fills it, the switcher is a bottom sheet,
            the panels hang 60 px from the top, centred)."""
            w, h = size
            return {"desk": (0, 0), "switcher": (0, screen["height"] - h)}.get(surface, ((screen["width"] - w) // 2, 60))

        def screenshot(n=[0]):
            n[0] += 1
            path = shots / f"{n[0]:03}.png"
            last_shot[0] = path
            niri("action", "screenshot-screen", "-p", "false", "--path", path)
            wait_for(lambda: path.exists() and path.stat().st_size > 0, "the screenshot")
            time.sleep(0.1)
            return png_pixels(path)

        def ring_drawn(shot, control, at):
            """Whether the 2 px ring the control describes is in the focus colour, on all four sides."""
            width, height, bpp, rows = shot
            scale = width / screen["width"]
            x, y, w, h, inset = control["ring"]
            x, y = x + at[0], y + at[1]

            def hit(px, py):
                px, py = int(round(px * scale)), int(round(py * scale))
                if not (0 <= px < width and 0 <= py < height):
                    return False
                pixel = rows[py][px * bpp:px * bpp + 3]
                return max(abs(a - b) for a, b in zip(pixel, focus)) <= 28

            sides = []
            for offset in (inset, inset + 1):
                along = range(-3, 4)
                sides.append([hit(x + w / 2 + d, y + offset) for d in along])          # top
                sides.append([hit(x + w / 2 + d, y + h - 1 - offset) for d in along])  # bottom
                sides.append([hit(x + offset, y + h / 2 + d) for d in along])          # left
                sides.append([hit(x + w - 1 - offset, y + h / 2 + d) for d in along])  # right
            per_side = [sides[i] + sides[i + 4] for i in range(4)]
            found = [sum(side) >= len(side) / 2 for side in per_side]
            return all(found) or (any(found) and dict(zip(("top", "bottom", "left", "right"), found)))

        def check_rings(surface, reach, index, listed, shot):
            """The focused control, and only it, has its ring drawn (where the shell has it now, scrolled or not)."""
            at = origin(surface, listed[0]["window"])
            for i in reach:
                drawn = ring_drawn(shot, listed[i], at)
                if (drawn is True) != (i == index):
                    shutil.copy(last_shot[0], FAILURE_SHOT)
                    partly = f" (sides found: {drawn})" if isinstance(drawn, dict) else ""
                    raise RuntimeError(f"{surface}: {listed[i]['text']!r} {'has a' if drawn is True else 'has no whole'} "
                                       f"focus ring while {'focused' if i == index else 'not focused'}{partly}, "
                                       f"ring at {listed[i]['ring']} in a {listed[i]['window']} window; see {FAILURE_SHOT}")

        def check_control(surface, c, node):
            """WCAG 2.5.3 and state: a name that holds the visible label, "Current" or "Open" when it applies.
            Failures are collected, so one run names them all."""
            name, description = node.name or "", node.description or ""
            problem = None
            if not name.strip():
                problem = f"a {c['kind']} at {c['rect']} has no accessible name"
            elif c["kind"] != "glyph" and not any(c["text"] and c["text"] in shown for shown in c["words"]):
                problem = f"{name!r} does not show its text {c['text']!r} ({c['words']})"
            elif c["kind"] != "glyph" and not contains(name, c["text"]):
                problem = f"accessible name {name!r} does not contain the label {c['text']!r}"
            else:
                # A card's meta line ("Current  ·  Group  ·  2 apps") marks the current choice in words.
                shows_current = any(part.strip() == "Current" for w in c["words"] for part in w.split("·"))
                want = "Current" if c["selected"] or shows_current else "Open" if c["open"] else ""
                if description != want:
                    problem = f"{name!r} is described as {description!r}, not {want!r}"
            if problem and f"{surface}: {problem}" not in problems:
                problems.append(f"{surface}: {problem}")

        def walk(surface):
            """Tab through every enabled control and back, checking rings and each control's semantics."""
            frame, listed = matched(surface)
            reach = [i for i, c in enumerate(listed) if c["enabled"]]
            starts = [dict(c, start=c["rect"], at=i) for i, c in enumerate(listed) if c["enabled"]]
            order = [c["at"] for c in reading_order(starts)]
            n = len(order)
            if n < 2:
                raise RuntimeError(f"{surface}: only {n} enabled controls")
            label = lambda i: listed[i]["text"] or listed[i]["node"] and listed[i]["node"].name
            start, _, now = focused_index(surface, frame)
            check_rings(surface, reach, start, now, screenshot())
            seq = []
            for _ in range(n + 1):
                previous = seq[-1] if seq else start
                key("-k", "Tab")
                wait_for(lambda: focused_index(surface, frame)[0] not in (None, previous), f"Tab to move focus on {surface}", timeout=3)
                index, node, now = focused_index(surface, frame)
                seq.append(index)
                check_control(surface, now[index], node)
                check_rings(surface, reach, index, now, screenshot())
            k = order.index(seq[0]) if seq[0] in order else None
            if k is None or (start is None and k != 0) or seq != [order[(k + i) % n] for i in range(n + 1)]:
                raise RuntimeError(f"{surface}: Tab order {[label(i) for i in seq]} is not the reading order "
                                   f"{[label(i) for i in order]}")
            back = []
            for _ in range(n):
                previous = back[-1] if back else seq[-1]
                key("-M", "shift", "-k", "Tab", "-m", "shift")
                wait_for(lambda: focused_index(surface, frame)[0] not in (None, previous), f"Shift+Tab to move focus on {surface}", timeout=3)
                back.append(focused_index(surface, frame)[0])
            if back != [order[(k - i) % n] for i in range(1, n + 1)]:
                raise RuntimeError(f"{surface}: Shift+Tab order {[label(i) for i in back]} does not reverse the Tab order")
            steps.append(f"{surface}: Tab and Shift+Tab reached all {n} enabled controls in reading order, each with "
                         "its focus ring and no other")
            return frame

        def focus_on(frame, name):
            for _ in range(60):
                node = focused(frame)
                if node is not None and node.name == name:
                    return node
                key("-k", "Tab")
            raise RuntimeError(f"Tab never reached {name!r}")

        def close_and_type(surface, closing, marker):
            """``closing`` shuts the surface; keys then reach the app, and none reached it before."""
            closing()
            wait_for(lambda: not controls(surface), f"{surface} to close")
            time.sleep(0.3)
            key(marker)
            wait_for(lambda: typed().endswith(marker), lambda: f"{marker!r} to reach the app after {surface} closed "
                     f"(it has {typed()!r})", timeout=5)
            if typed() != expected_typed + marker:
                raise RuntimeError(f"Keys leaked to the app while {surface} was open: {typed()!r}")
            if (read("focused-window") or {}).get("app_id") != app_id:
                raise RuntimeError(f"Closing {surface} did not return focus to the app")
            return expected_typed + marker

        def check_semantics(where):
            """Every control in view on every visible surface (the walk checks the scrolled-away ones)."""
            checked = 0
            for surface in sorted({c["surface"] for c in controls()}):
                for c in matched(surface)[1]:
                    if c["node"] is not None:
                        check_control(surface, c, c["node"])
                        checked += 1
            steps.append(f"{where}: checked the names and states of {checked} controls in view")

        def progress_bars(size):
            return [n for n in nodes(frame_of(size)) if n.getRole() == pyatspi.ROLE_PROGRESS_BAR]

        try:
            # The output's empty workspace, so no other window is in the way; the original is focused again after.
            niri("action", "focus-workspace", max((w for w in read("workspaces") if w["output"] == output["name"]
                                                    and w["active_window_id"] is None), key=lambda w: w["idx"])["idx"])
            processes.append(subprocess.Popen(["foot", "--app-id", app_id, "sh", "-c",
                                               'stty -icanon -echo min 1; exec cat > "$1"', "sh", str(sink)],
                                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            wait_for(lambda: (read("focused-window") or {}).get("app_id") == app_id, "the test's Foot window to take focus")
            # The first keys may arrive before the shell in Foot has set up its terminal: type until one lands.
            def landed():
                key("0")
                time.sleep(0.3)
                return typed().endswith("0")
            wait_for(landed, lambda: f"keys to reach the test's Foot window (it has {typed()!r})")
            expected_typed = typed()

            shell_process = subprocess.Popen(["quickshell", "-p", shell], env=env, stdin=subprocess.DEVNULL,
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            processes.append(shell_process)
            wait_for(lambda: ipc("desk_state") == "closed", "the development shell", timeout=20)
            wait_for(shell_app, "the shell on the AT-SPI bus")
            island = wait_for(lambda: controls("island"), "the island")
            chips = progress_bars(island[0]["window"])
            if len(chips) != 1 or not chips[0].name.startswith("Activity from Photos: Copy photos") or chips[0].description != "40 percent":
                raise RuntimeError(f"The island's activity chip is not one progress bar: {[(c.name, c.description) for c in chips]}")
            check_semantics("work view")

            # Desk: walk, Return opens Settings, walk again with its switches, Return and keypad Enter toggle one.
            ipc("toggle")
            wait_for(lambda: controls("desk"), "Desk")
            time.sleep(0.4)
            cards = progress_bars(controls("desk")[0]["window"])
            if len(cards) != 2 or any(c.description != "40 percent" for c in cards):
                raise RuntimeError(f"Desk does not show the activity as progress bars: {[(c.name, c.description) for c in cards]}")
            frame = walk("desk")
            focus_on(frame, "Settings")
            key("-k", "Return")
            wait_for(lambda: any(c["kind"] == "switch" for c in controls("desk")), "Return to open Desk settings")
            frame = walk("desk")
            check_semantics("Desk with settings")
            motion = focus_on(frame, "Reduce motion: buttons and progress change instantly")
            for name, press, want in (("Return", "Return", False), ("keypad Enter", "KP_Enter", True)):
                key("-k", press)
                wait_for(lambda: (frame.clear_cache(), motion.getState().contains(pyatspi.STATE_CHECKED))[1] == want,
                         f"{name} to toggle Reduce motion")
            steps.append("Desk: Return opened Settings; Return and keypad Enter toggled Reduce motion off and on")
            expected_typed = close_and_type("desk", lambda: key("-k", "Escape"), "1")
            steps.append("Desk: Escape closed it and keys reached the app again; none leaked while it was open")

            # The switcher with the test's window as the current space ("Current" on its card), then as a Layer
            # (a selected Layer button, and the Layer row's controls).
            ipc("switcher")
            wait_for(lambda: any("Current" in " ".join(c["words"]) for c in controls("switcher")),
                     "the switcher with the current space")
            time.sleep(0.4)
            check_semantics("Spaces switcher")
            walk("switcher")
            expected_typed = close_and_type("switcher", lambda: key("-k", "Escape"), "2")
            spaces = subprocess.run(["flow-spaces", "make-layer"], env=env, text=True, stdout=subprocess.PIPE, timeout=60)
            if spaces.returncode or "error" in json.loads(spaces.stdout or "{}"):
                raise RuntimeError(f"flow-spaces make-layer: {spaces.stdout}")
            ipc("switcher")
            wait_for(lambda: any(c["selected"] for c in controls("switcher")), "the switcher with the Layer selected")
            time.sleep(0.4)
            check_semantics("Spaces switcher with a Layer")
            frame = walk("switcher")
            focus_on(frame, "Close Spaces switcher")
            expected_typed = close_and_type("switcher", lambda: key("-k", "Return"), "3")
            steps.append("Spaces switcher: Escape, and Return on Close, each closed it and returned keys to the app")

            ipc("quick_open")
            wait_for(lambda: controls("quick"), "Quick settings")
            time.sleep(0.4)
            check_semantics("Quick settings")
            walk("quick")
            expected_typed = close_and_type("quick", lambda: key("-k", "Escape"), "4")
            ipc("quick_open")
            frame = frame_of(wait_for(lambda: controls("quick"), "Quick settings again")[0]["window"])
            focus_on(frame, "Close")
            expected_typed = close_and_type("quick", lambda: key("-k", "KP_Enter"), "5")
            steps.append("Quick settings: Escape, and keypad Enter on Close, each closed it and returned keys to the app")

            notify = Gio.bus_get_sync(Gio.BusType.SESSION, None).call_sync(
                "org.freedesktop.Notifications", "/org/freedesktop/Notifications", "org.freedesktop.Notifications", "Notify",
                GLib.Variant("(susssasa{sv}i)", ("Focus test", 0, "", "Backup finished", "All files are safe.",
                                                 ["open", "Open folder"], {}, 0)),
                GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, 5000, None)
            if not notify.unpack()[0]:
                raise RuntimeError("The development shell did not take the notification")
            ipc("notifications")
            wait_for(lambda: controls("notifications"), "Notifications")
            time.sleep(0.4)
            check_semantics("Notifications")
            frame = walk("notifications")
            focus_on(frame, "Dismiss notification")
            key("-k", "Return")
            wait_for(lambda: not any(c["text"] == "Open folder" for c in controls("notifications")),
                     "Return to dismiss the notification")
            steps.append("Notifications: Return dismissed the focused notification")
            expected_typed = close_and_type("notifications", lambda: key("-k", "Escape"), "6")
            steps.append("Notifications: Escape closed it and keys reached the app again")
            if problems:
                raise RuntimeError("Screen-reader semantics:\n  " + "\n  ".join(problems))
            result = {"result": "pass", "niri": niri("version").strip(), "steps": steps}
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
            try:
                for w in read("windows"):
                    if w.get("app_id") == app_id:
                        niri("action", "close-window", "--id", w["id"])
                niri("action", "focus-workspace", original_workspace["idx"])
            except (OSError, subprocess.SubprocessError):
                pass
    if [digest(path) for path in real_files] != real_digests:
        raise RuntimeError("A real Flow Group, pin, or session file changed")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, StopIteration, subprocess.SubprocessError, ValueError, KeyError) as exc:
        print(f"Focus walk test failed: {exc}", file=sys.stderr)
        sys.exit(1)
