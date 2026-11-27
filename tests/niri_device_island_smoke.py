#!/usr/bin/env python3
"""Resting chrome by device (ADR-0014, F-012) on a running shell, in the guest.

Run with tests/run_device_island_smoke.sh, inside the Niri session under the screen lock, from the checkout's root.
The shell under test is ``FLOW_SHELL`` (default ``/etc/flow/shell.qml``, the installed one, so Niri's own Super+B
binding reaches it), started as a development shell with isolated state and a configuration with recovery off,
while the installed service is stopped; the service is started again however the run ends. Keys are pressed on a
real (uinput) keyboard, so they go through Niri's bindings; the pointer is tests/pointer.py's.

Checks (``--only NAME``, repeatable):
- ``rest``: over an app, nothing of the shell is on screen at rest: no Island, clock pill, work indicator, home
  line or banner, and the only shell layer surfaces over the work view are the 1 px edge strips.
- ``row``: on a full-window page with buttons from its top edge down, a click on row 1 (and rows 2 and 3) at the
  left, middle and right reaches the page; a press on row 0, the strip, opens the Island instead.
- ``edge``: the pointer resting against the top edge for 60 or 120 ms opens nothing; resting longer opens the Island,
  no sooner than 150 ms, it stays open while the pointer rests there (1.5 s), and it closes once the pointer has left;
  the time from the push to its first frame.
- ``shortcut``: Super+B opens it, the time from the key press to its first frame, and Escape closes it; opened again,
  a click elsewhere closes it.
- ``work``: the opened Island's Overview and Desk buttons open Overview and Desk (there is no hot corner).
- ``displays``: with Virtual-2 on, Super+B's Island closes on a click on Virtual-2; the Island opened from Virtual-2's
  edge opens the notifications panel there; and it closes when Virtual-2 is unplugged.
- ``pill``: the clock pill is off in a configuration that has never set it. "main" with Virtual-2 on shows it on the
  main display only, as the controller numbers it (``flow-spaces refresh``: display 0); with Virtual-2 unplugged, on
  Virtual-1 alone; with Virtual-2 made Main (the controller's ``make-main``, on the shell's own state), on Virtual-2.
  "all" shows it on both, and Virtual-2 unplugged takes its pill away. Virtual-2 goes on and off through sysfs and is
  off again at the end. Virtual-1 is never unplugged: in the guest, plugged back with ``on`` it loses its EDID (and
  so its identity, a new display to the controller) until the host sends a display change.
- ``moving``: the work indicator shows only while moving between spaces, then goes.
- ``home``: no home line without a touch device; a touchscreen plugged in (uinput) shows it, unplugged hides it.
- ``banner``: a notification's banner takes no clicks: a click on it reaches the page beneath.
- ``critical``: a critical notification's banner stays past 7 s, and goes for good once the Island opens.
- ``band``: a simulated 13-inch MacBook Air in Asahi's full-panel mode (the notchPanel override and a custom mode one
  32 px band taller than 1280x800): the band is drawn, windows start below it at 1280x800, the screen has no edge
  strip; and whether a fullscreen window covers the band. Then the panel matched, not forced: the Air's own
  2560x1664 as a custom mode, at scale 2 and at 1.6 (a 32 and a 40 px band, apps at 1280x800 and 1600x1000).

Prints one JSON line per check and a summary; exits 1 when any check fails. ``--shots DIR`` saves screenshots.
"""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import niri_portals_smoke as m  # noqa: E402
from isolated_state import IsolatedState, shell_config  # noqa: E402
from pointer import center  # noqa: E402
from uinput import Devices  # noqa: E402

CHECKS = ["rest", "row", "edge", "shortcut", "work", "pill", "displays", "moving", "home", "banner", "critical", "band"]
SHELL = os.environ.get("FLOW_SHELL", "/etc/flow/shell.qml")
OUTPUT = "Virtual-1"
CONNECTORS = {"Virtual-1": "/sys/class/drm/card1-Virtual-1", "Virtual-2": "/sys/class/drm/card1-Virtual-2"}
Failed = m.Failed


def ipc(*args):
    out = subprocess.run(["quickshell", "ipc", "-p", SHELL, "call", "desk", *map(str, args)], text=True,
                         capture_output=True, timeout=10)
    return out.stdout.strip()


def island():
    """The shell's island_state, or {} while it has not answered yet."""
    try:
        return json.loads(ipc("island_state"))
    except ValueError:
        return {}


def wait_for(test, label, timeout=5, every=0.05):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = test()
        if value:
            return value
        time.sleep(every)
    raise Failed(f"Timed out waiting for {label() if callable(label) else label}")


def check(condition, message):
    if not condition:
        raise Failed(message)


def notify(summary, critical=False):
    hints = ["1", "urgency", "y", "2"] if critical else ["0"]
    subprocess.run(["busctl", "--user", "call", "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                    "org.freedesktop.Notifications", "Notify", "susssasa{sv}i", "Flow test", "0", "", summary,
                    "A click here is the page's", "0", *hints, "5000"], check=True, timeout=10, stdout=subprocess.DEVNULL)


def target(name):
    try:
        return json.loads(ipc("pointer_target", name) or "null")
    except ValueError:
        return None


def layers():
    return m.read("layers")


def display(on, name="Virtual-2"):
    """An output on or off through its connector's sysfs status, as the guest's preconditions do."""
    subprocess.run(["sudo", "-n", "tee", f"{CONNECTORS[name]}/status"], input=("on" if on else "off") + "\n", text=True,
                   stdout=subprocess.DEVNULL, check=True, timeout=10)
    subprocess.run(["sudo", "-n", "udevadm", "trigger", "--action=change", "/sys/class/drm/card1"], check=True, timeout=10)
    wait_for(lambda: (name in m.read("outputs")) == on, f"{name} to turn {'on' if on else 'off'}", timeout=15)
    time.sleep(2)


class Smoke(m.Smoke):
    def __init__(self, tmp, shots, keys, state):
        super().__init__(tmp, shots)
        self.keys, self.state = keys, state
        self.output = m.read("outputs")[OUTPUT]["logical"]

    def controller(self, *args):
        """``flow-spaces`` on the shell's own (isolated) state."""
        out = subprocess.run(["flow-spaces", *args], env={**os.environ, "XDG_STATE_HOME": self.state}, text=True,
                             capture_output=True, timeout=30, check=True).stdout
        return json.loads(out)

    def controller_main(self):
        """The main display's connector as the controller has it, not the shell: display 0, or with it away the
        lowest-numbered connected one."""
        shown = [d for d in self.controller("refresh")["displays"] if d.get("output")]
        return min(shown, key=lambda d: d["number"] if d["number"] is not None else 99)["output"]

    def make_main(self, output):
        key = next(d["key"] for d in self.controller("refresh")["displays"] if d.get("output") == output)
        self.controller("make-main", key)
        # The shell reads the numbering on its next refresh, which a focus change brings.
        m.niri("action", "focus-monitor", output)
        time.sleep(0.5)
        m.niri("action", "focus-monitor", OUTPUT)

    def relayout(self):
        """After an output came or went: the pointer's layout and the page's output."""
        self.pointer = m.Pointer(allow_installed_shell=True)
        self.output = m.read("outputs").get(OUTPUT, {}).get("logical") or self.output

    def edges_page(self):
        page = self.need_firefox(kiosk=True, mode="edges")
        return page, m.windows()[page["id"]]["layout"]["window_size"]

    def clicked(self, at):
        """Click at ``at`` (layout coordinates); the page's button that took it, or None."""
        pages = self.need_pages()
        pages.drain()
        self.pointer.click(at)
        try:
            return pages.wait(["clicked"], timeout=2)["name"]
        except Failed:
            return None

    def close_island(self):
        self.pointer.park()
        if island().get("open"):
            wait_for(lambda: not island().get("open"), "the Island to close once the pointer left", timeout=3)

    # ---------------------------------------------------------------------------------------------------------

    def check_rest(self):
        self.edges_page()
        self.pointer.park()
        time.sleep(1.5)
        s = island()
        check(s and not s["open"], f"The Island is open at rest: {s}")
        check(s["pills"] == [] and not s["indicator"] and not s["home"] and not s["banner"],
              f"Something of the shell shows at rest: {s}")
        shell_layers = [layer for layer in layers() if layer.get("layer") in ("Top", "Overlay")
                        and layer.get("output") == OUTPUT]
        others = [layer for layer in shell_layers if layer.get("namespace") != "flow-edge"]
        check(not others and len(shell_layers) == 1, f"Over the work view, layer surfaces besides the edge strip: {shell_layers}")
        self.shot("rest")
        self.passed("rest", f"nothing at rest over the page; the only layer surface over it is the edge strip ({shell_layers})")

    def check_row(self):
        page, (width, height) = self.edges_page()
        o = self.output
        took, missed = [], []
        for name, x in (("top-left", 30), ("top-middle", width // 2), ("top-right", width - 30)):
            for y in (1, 2, 3):
                (took if self.clicked((o["x"] + x, o["y"] + y)) == name else missed).append(f"{name} y={y}")
                self.close_island()
        check(not missed, f"Clicks on rows 1 to 3 that did not reach the page: {missed}")
        got = self.clicked((o["x"] + width // 2, o["y"]))
        s = island()
        # A press there opens it at once; under load the rest may have opened it first. Either way the page gets nothing.
        check(got is None and s["open"] and s["by"] in ("press", "edge"), f"A press on row 0 reached {got!r}, Island {s}")
        self.close_island()
        self.passed("row", f"rows 1 to 3 reached the page at the left, middle and right ({len(took)} clicks); a press on "
                           "row 0 opened the Island and reached nothing")

    def check_edge(self):
        self.edges_page()
        o = self.output
        # A rest shorter than the 150 ms opens nothing.
        for rest in (60, 120, 60, 120):
            self.close_island()
            time.sleep(0.4)
            self.pointer.run(("move", o["x"] + o["width"] // 2, o["y"]), ("wait", rest),
                             ("move", o["x"] + o["width"] // 2, o["y"] + 300))
            time.sleep(0.5)
            check(not island().get("open"), f"A {rest} ms rest at the edge opened the Island: {island()}")
        times = []
        for _ in range(5):
            self.close_island()
            time.sleep(0.5)
            pushed = time.time() * 1000
            self.pointer.move((o["x"] + o["width"] // 2, o["y"]))
            s = wait_for(lambda: (lambda s: s if s.get("open") and s.get("shown_at") else None)(island()),
                         "the Island to open from the edge", timeout=2)
            check(s["by"] == "edge", f"The Island opened by {s['by']!r}, not the edge")
            check(s["asked_at"] - pushed >= 140, f"The Island was asked for {s['asked_at'] - pushed:.0f} ms after the push, "
                                                  "sooner than its 150 ms rest")
            times.append(round(s["shown_at"] - pushed))
        # Resting there, it stays open.
        time.sleep(1.5)
        check(island().get("open"), f"With the pointer resting at the edge, the Island closed: {island()}")
        self.shot("edge")
        self.pointer.park()
        wait_for(lambda: not island().get("open"), "the Island to close once the pointer left", timeout=2)
        times.sort()
        self.passed("edge", f"60 and 120 ms rests at the top edge opened nothing; pushed against it, the Island opened after "
                            f"its rest, stayed open while the pointer rested there, and closed once it left; push to first "
                            f"frame {times} ms (median {times[len(times) // 2]})")

    def shortcut(self):
        """Super+B through Niri's binding, which calls the installed shell; a development copy gets the same IPC call."""
        if SHELL == "/etc/flow/shell.qml":
            self.keys.keys("super", "b")
        else:
            ipc("island")

    def check_shortcut(self):
        self.edges_page()
        self.pointer.park()
        times = []
        for _ in range(5):
            time.sleep(0.6)
            pressed = time.time() * 1000
            self.shortcut()
            s = wait_for(lambda: (lambda s: s if s.get("open") and s.get("shown_at") else None)(island()),
                         "Super+B to open the Island", timeout=3)
            check(s["by"] == "shortcut", f"The Island opened by {s['by']!r}, not Super+B")
            times.append(round(s["shown_at"] - pressed))
            time.sleep(0.3)  # a user's Escape, not one in the same frame as the Island
            self.keys.keys("esc")
            wait_for(lambda: not island().get("open"), "Escape to close the Island", timeout=2)
        times.sort()
        # Opened again, a click on the page beside it closes it (as a menu: the click goes no further).
        self.shortcut()
        wait_for(lambda: island().get("open") and island().get("shown_at"), "Super+B to open the Island again", timeout=3)
        time.sleep(0.3)
        o = self.output
        self.pointer.click((o["x"] + 30, o["y"] + o["height"] // 2))
        wait_for(lambda: not island().get("open"), "a click beside the Island to close it", timeout=2)
        self.passed("shortcut", f"Super+B (a uinput key press, through Niri's binding) opened the Island and Escape closed "
                                f"it, and a click beside it closed it too; key press to first frame {times} ms "
                                f"(median {times[len(times) // 2]})")

    def check_pill(self):
        s = island()
        check(s["clock_pill"] == "off" and s["pills"] == [], f"A configuration that never set it shows a clock pill: {s}")

        def pills(want, label):
            return wait_for(lambda: (lambda s: s if sorted(s["pills"]) == sorted(want) else None)(island()),
                            lambda: f"{label} (pills {island()['pills']}, want {want})", timeout=15)["pills"]
        try:
            display(True)
            self.relayout()
            ipc("clock_pill", "main")
            main = self.controller_main()
            check(main == OUTPUT, f"The controller's main display is {main}, not {OUTPUT}")
            pills([main], "the pill on the main display only")
            self.shot("pill-main")
            display(False)
            pills([OUTPUT], "Main only, with Virtual-2 unplugged")
            display(True)
            self.relayout()
            self.make_main("Virtual-2")
            main = self.controller_main()
            check(main == "Virtual-2", f"Made Main, Virtual-2 is not the controller's main display ({main})")
            pills(["Virtual-2"], "the pill on Virtual-2 once it is Main")
            # Main away: the lowest-numbered display left.
            display(False)
            self.relayout()
            check(self.controller_main() == OUTPUT, f"With Main away the controller's main display is {self.controller_main()}")
            pills([OUTPUT], "the pill on Virtual-1 with Main (Virtual-2) away")
            display(True)
            self.relayout()
            pills(["Virtual-2"], "the pill back on Virtual-2, Main, once it returned")
            self.make_main(OUTPUT)
            pills([OUTPUT], "the pill back on Virtual-1 once it is Main again")
            ipc("clock_pill", "all")
            pills([OUTPUT, "Virtual-2"], "a pill on both displays")
            self.shot("pill-all")
            display(False)
            self.relayout()
            pills([OUTPUT], "the pill to leave the unplugged display")
        finally:
            ipc("clock_pill", "off")
            if "Virtual-2" in m.read("outputs"):
                display(False)
                self.relayout()
        pills([], "the pill to go with the setting off")
        self.passed("pill", "off by default; Main only showed it on the controller's main display alone with Virtual-2 on and "
                            "off, on Virtual-2 once it was made Main, on Virtual-1 while that Main was away; All screens on both, "
                            "and unplugging Virtual-2 took its pill away")

    def check_displays(self):
        self.edges_page()
        try:
            display(True)
            self.relayout()
            v2 = m.read("outputs")["Virtual-2"]["logical"]
            # Super+B's Island, then a click on the other display: it closes, and gives the keyboard back.
            self.pointer.park()
            self.shortcut()
            wait_for(lambda: island().get("open") and island().get("shown_at"), "Super+B to open the Island", timeout=3)
            check(island()["screen"] == OUTPUT, f"Super+B opened the Island on {island()['screen']}")
            time.sleep(0.3)
            self.pointer.click((v2["x"] + v2["width"] // 2, v2["y"] + v2["height"] // 2))
            wait_for(lambda: not island().get("open"), "a click on Virtual-2 to close Super+B's Island", timeout=2)
            # The Island from Virtual-2's edge: its panels open there.
            self.pointer.move((v2["x"] + v2["width"] // 2, v2["y"]))
            s = wait_for(lambda: (lambda s: s if s.get("open") and s.get("shown_at") else None)(island()),
                         "the Island from Virtual-2's edge", timeout=2)
            check(s["screen"] == "Virtual-2", f"Virtual-2's edge opened the Island on {s['screen']}")
            ipc("notifications")
            wait_for(lambda: json.loads(ipc("surfaces") or "{}").get("notifications"), "the notifications panel", timeout=3)
            time.sleep(0.5)
            grabs = [layer["output"] for layer in layers() if layer.get("keyboard_interactivity") == "Exclusive"]
            check(island()["panel_screen"] == "Virtual-2" and grabs == ["Virtual-2"],
                  f"The panel opened from Virtual-2's Island is on {grabs} ({island()['panel_screen']})")
            self.shot("displays-panel")
            ipc("notifications")
            wait_for(lambda: not json.loads(ipc("surfaces") or "{}").get("notifications"), "the panel to close", timeout=3)
            # Unplugged under the open Island: it closes.
            self.pointer.move((v2["x"] + v2["width"] // 2, v2["y"]))
            wait_for(lambda: island().get("open") and island().get("screen") == "Virtual-2", "the Island on Virtual-2", timeout=2)
        finally:
            display(False)
            self.relayout()
        wait_for(lambda: not island().get("open"), "the Island to close with its display unplugged", timeout=3)
        self.passed("displays", "Super+B's Island closed on a click on Virtual-2; the Island from Virtual-2's edge opened its "
                                "panel there; and it closed when Virtual-2 was unplugged")

    def check_moving(self):
        page, _ = self.edges_page()
        # A second space to move to: Kate's, beside the page.
        notes = Path(self.tmp) / "moving.txt"
        notes.write_text("moving\n")
        _, kate = self.apps.start(["kate", notes], app_id="org.kde.kate")
        self.apps.focus(page, "100%")
        time.sleep(2)
        check(not island()["indicator"], "The work indicator shows at rest")
        self.keys.keys("super", "right")
        wait_for(lambda: island()["indicator"], "the work indicator while moving", timeout=2)
        self.shot("moving")
        wait_for(lambda: not island()["indicator"], "the work indicator to go after the move", timeout=3)
        self.keys.keys("super", "left")
        wait_for(lambda: island()["indicator"], "the work indicator while moving back", timeout=2)
        # It sits over the page's bottom-right button and takes no clicks: the button gets this one.
        _, (width, height) = page, m.windows()[page["id"]]["layout"]["window_size"]
        o = self.output
        got = self.clicked((o["x"] + width - 40, o["y"] + height - 20))
        shown = island()["indicator"]
        wait_for(lambda: not island()["indicator"], "the work indicator to go after moving back", timeout=3)
        check(got == "bottom-right", f"A click on the work indicator reached {got!r}, not the page's bottom-right button"
                                     f" (indicator shown at the click: {shown})")
        self.apps.close_window(kate)
        self.passed("moving", "the work indicator showed while moving between spaces (Super+Right, Super+Left), a click on "
                              "it reached the page's button beneath, and it went")

    def check_home(self):
        ipc("touch_probe")
        time.sleep(1)
        s = island()
        check(not s["touch"] and not s["home"], f"A home line, or a touch device, with none plugged in: {s}")
        try:
            self.keys.touchscreen(True)
            ipc("touch_probe")
            s = wait_for(lambda: (lambda s: s if s["home"] else None)(island()), "the home line with a touchscreen")
            self.shot("home-touch")
        finally:
            self.keys.touchscreen(False)
        ipc("touch_probe")
        wait_for(lambda: not island()["home"], "the home line to go with the touchscreen unplugged")
        self.passed("home", "no home line without a touch device; a touchscreen plugged in showed it, unplugged hid it")

    def check_banner(self):
        _, (width, _) = self.edges_page()
        o = self.output
        self.pointer.park()
        notify("Over the page")
        wait_for(lambda: island()["banner"], "the banner")
        # The banner hangs 8 px below the top edge in the middle, over the page's top-middle button (40 px tall).
        got = self.clicked((o["x"] + width // 2, o["y"] + 24))
        check(island()["banner"], "The banner was gone before the click")
        check(got == "top-middle", f"A click on the banner reached {got!r}, not the page's top-middle button")
        self.shot("banner")
        self.passed("banner", "a click on the banner reached the page's button beneath it")

    def open_by_edge(self):
        self.close_island()
        time.sleep(0.4)
        o = self.output
        self.pointer.move((o["x"] + o["width"] // 2, o["y"]))
        return wait_for(lambda: (lambda s: s if s.get("open") and s.get("shown_at") else None)(island()),
                        "the Island to open from the edge", timeout=2)

    def check_work(self):
        self.edges_page()
        for name, surface in (("island_overview", "overview"), ("island_desk", "desk")):
            self.open_by_edge()
            button = wait_for(lambda: target(name), f"the Island's {name}")
            self.pointer.click(center(button))
            wait_for(lambda: json.loads(ipc("surfaces") or "{}").get(surface), f"{name} to open {surface}", timeout=3)
            self.shot(f"work-{surface}")
            self.keys.keys("esc")
            wait_for(lambda: not any(json.loads(ipc("surfaces") or "{}").values()), f"{surface} to close", timeout=3)
            self.pointer.park()
        self.passed("work", "the opened Island's Overview and Desk buttons opened Overview and Desk")

    def check_critical(self):
        self.edges_page()
        self.pointer.park()
        notify("Critical over the page", critical=True)
        wait_for(lambda: island()["banner"], "the critical banner")
        time.sleep(7)
        check(island()["banner"], "The critical banner went by itself")
        self.open_by_edge()
        check(not island()["banner"], "The critical banner shows with the Island open")
        self.close_island()
        time.sleep(1)
        check(not island()["banner"], "The critical banner came back after the Island closed")
        self.passed("critical", "a critical banner stayed past 7 s, and went for good once the Island opened")

    def check_band(self):
        before = m.read("outputs")[OUTPUT]
        mode = before["modes"][before["current_mode"]]
        mode0 = f"{mode['width']}x{mode['height']}@{mode['refresh_rate'] / 1000:.3f}"
        # A tiled window: kiosk Firefox is fullscreen, which Niri sizes to the whole output whatever the zones.
        page = self.need_firefox(mode="upload")
        try:
            ipc("notch_panel", "air-13")
            m.niri("output", OUTPUT, "custom-mode", "1280x832@60")
            time.sleep(2)
            s = wait_for(lambda: (lambda s: s if s["bands"] else None)(island()), "the band")
            band = s["bands"][0]
            check(band["height"] == 32 and band["simulated"] and OUTPUT not in s["edges"], f"The band or the strip: {s}")
            w = m.windows()[page["id"]]["layout"]
            # Tiled below the exclusive zone: the rectangle Asahi gives apps by default.
            check(w["window_size"] == [1280, 800], f"The page's window is {w['window_size']}, not 1280x800 below the band")
            self.shot("band")
            m.niri("action", "fullscreen-window", "--id", page["id"])
            time.sleep(1.5)
            full = m.windows()[page["id"]]["layout"]["window_size"]
            covered = any(layer.get("namespace") == "flow-band" for layer in layers())
            self.shot("band-fullscreen")
            m.niri("action", "fullscreen-window", "--id", page["id"])
            time.sleep(1)
            # The panel matched on the output's physical mode, at a whole and a fractional scale.
            ipc("notch_panel", "")
            matched = []
            for scale, want_band, want_window in ((2, 32, [1280, 800]), (1.6, 40, [1600, 1000])):
                m.niri("output", OUTPUT, "custom-mode", "2560x1664@60")
                m.niri("output", OUTPUT, "scale", scale)
                time.sleep(2.5)
                s = wait_for(lambda: (lambda s: s if s["bands"] else None)(island()), f"the band matched at scale {scale}", timeout=6)
                band = s["bands"][0]
                check(band["panel"] == "air-13" and not band["simulated"] and band["height"] == want_band,
                      f"At scale {scale}: {band}, not the Air's {want_band} px band")
                size = m.windows()[page["id"]]["layout"]["window_size"]
                check(size == want_window, f"At scale {scale} the window is {size}, not {want_window} below the band")
                matched.append(f"{band['height']} px at {scale}")
                self.shot(f"band-matched-{scale}")
        finally:
            ipc("notch_panel", "none")
            m.niri("output", OUTPUT, "mode", mode0)
            m.niri("output", OUTPUT, "scale", before["logical"]["scale"])
            time.sleep(1.5)
        self.passed("band", f"a 32 px band on the simulated Air, the page tiled at 1280x800 below it, no edge strip there; "
                            f"fullscreen, the window was {full[0]}x{full[1]} (the band's surface still mapped: {covered}); "
                            f"the Air's 2560x1664 matched without the override: {', '.join(matched)}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", action="append", choices=CHECKS)
    parser.add_argument("--shots", type=Path)
    args = parser.parse_args()
    for tool in ("niri", "grim", "firefox", "kate", "quickshell", "busctl", "flow-pointer"):
        if not shutil.which(tool):
            raise SystemExit(f"{tool} is not on PATH (tests/run_device_island_smoke.sh sets it)")
    check(any(w for w in m.windows().values() if w["workspace_id"] == m.focused_workspace()["id"]),
          "The focused workspace is empty: the test starts from a workspace in use")
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    chosen = args.only or CHECKS
    state = m.UserState()
    failures, results = [], []
    shell = None
    keys = Devices()
    try:
        with tempfile.TemporaryDirectory(prefix="flow-island-") as tmp, IsolatedState("flow-island-state-") as dev_state:
            smoke = None
            try:
                subprocess.run(["systemctl", "--user", "stop", "flow-shell"], check=True)
                config = shell_config(tmp)
                # A configuration that has never set the Island's keys: a shell that ran writes each key's default
                # back to the file it reads (Qt's Settings), and the copy is the user's.
                settings = Path(config) / "flow-settings.ini"
                settings.write_text(re.sub(r"(?m)^(clockPill|notchPanel)=.*\n?", "", settings.read_text()))
                env = {**os.environ, "XDG_STATE_HOME": dev_state, "XDG_CONFIG_HOME": config}
                shell = subprocess.Popen(["quickshell", "-p", SHELL], env=env, stdin=subprocess.DEVNULL,
                                         stdout=open(Path(tmp) / "shell.log", "w"), stderr=subprocess.STDOUT,
                                         start_new_session=True)
                wait_for(lambda: island(), "the shell under test", timeout=20)
                time.sleep(2)
                smoke = Smoke(tmp, args.shots, keys, dev_state)
                for name in chosen:
                    before, started = set(m.windows()), len(smoke.apps.processes)
                    try:
                        getattr(smoke, "check_" + name)()
                    except (Failed, RuntimeError, subprocess.SubprocessError, KeyError) as exc:
                        failures.append({"check": name, "result": "fail", "detail": f"{exc} (Island {island()})"})
                        print(json.dumps(failures[-1]), flush=True)
                        try:
                            smoke.shot(f"failed-{name}")
                        except RuntimeError:
                            pass
                    smoke.pointer.park()
                    smoke.apps.end(smoke.apps.processes[started:])
                    for wid in set(m.windows()) - before:
                        smoke.apps.close_window({"id": wid})
                    if smoke.firefox and smoke.firefox["id"] not in m.windows():
                        smoke.firefox = None
                results = smoke.results
                if failures:
                    print((Path(tmp) / "shell.log").read_text()[-3000:], flush=True)
            finally:
                if smoke:
                    smoke.stop()
                if shell and shell.poll() is None:
                    shell.terminate()
                    shell.wait(10)
    finally:
        keys.close()
        if "Virtual-2" in m.read("outputs"):
            display(False)
        subprocess.run(["systemctl", "--user", "start", "flow-shell"])
        for problem in state.put_back():
            failures.append({"check": "put back", "result": "fail", "detail": problem})
            print(json.dumps(failures[-1]), flush=True)
    print(json.dumps({"passed": len(results), "failed": sorted(f["check"] for f in failures)}), flush=True)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
