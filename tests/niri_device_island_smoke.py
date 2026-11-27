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
- ``edge``: the pointer pushed against the top edge opens the Island after its 150 ms rest, and it closes once the
  pointer has left; the time from the push to its first frame.
- ``shortcut``: Super+B opens it, the time from the key press to its first frame, and Escape closes it.
- ``pill``: the clock pill is off in a fresh configuration; "main" with Virtual-2 on shows it on the main display
  only, "all" on both; Virtual-2 unplugged drops it, with the main display's kept. Virtual-2 goes on and off through
  sysfs, and is off again at the end.
- ``moving``: the work indicator shows only while moving between spaces, then goes.
- ``home``: no home line without a touch device; a touchscreen plugged in (uinput) shows it, unplugged hides it.
- ``banner``: a notification's banner takes no clicks: a click on it reaches the page beneath.
- ``band``: a simulated 13-inch MacBook Air in Asahi's full-panel mode (the notchPanel override and a custom mode one
  32 px band taller than 1280x800): the band is drawn, windows start below it at 1280x800, the screen has no edge
  strip; and whether a fullscreen window covers the band.

Prints one JSON line per check and a summary; exits 1 when any check fails. ``--shots DIR`` saves screenshots.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import niri_portals_smoke as m  # noqa: E402
from isolated_state import IsolatedState, shell_config  # noqa: E402
from uinput import Devices  # noqa: E402

CHECKS = ["rest", "row", "edge", "shortcut", "pill", "moving", "home", "banner", "band"]
SHELL = os.environ.get("FLOW_SHELL", "/etc/flow/shell.qml")
OUTPUT = "Virtual-1"
CONNECTOR = "/sys/class/drm/card1-Virtual-2"
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


def layers():
    return m.read("layers")


def display(on):
    """Virtual-2 on or off through its connector's sysfs status, as the guest's preconditions do."""
    subprocess.run(["sudo", "-n", "tee", f"{CONNECTOR}/status"], input=("on" if on else "off") + "\n", text=True,
                   stdout=subprocess.DEVNULL, check=True, timeout=10)
    subprocess.run(["sudo", "-n", "udevadm", "trigger", "--action=change", "/sys/class/drm/card1"], check=True, timeout=10)
    wait_for(lambda: ("Virtual-2" in m.read("outputs")) == on, f"Virtual-2 to turn {'on' if on else 'off'}", timeout=15)
    time.sleep(1.5)


class Smoke(m.Smoke):
    def __init__(self, tmp, shots, keys):
        super().__init__(tmp, shots)
        self.keys = keys
        self.output = m.read("outputs")[OUTPUT]["logical"]

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
        check(got is None and s["open"] and s["by"] == "press", f"A press on row 0 reached {got!r}, Island {s}")
        self.close_island()
        self.passed("row", f"rows 1 to 3 reached the page at the left, middle and right ({len(took)} clicks); a press on "
                           "row 0 opened the Island and reached nothing")

    def check_edge(self):
        self.edges_page()
        o = self.output
        times = []
        for _ in range(5):
            self.close_island()
            time.sleep(0.5)
            pushed = time.time() * 1000
            self.pointer.move((o["x"] + o["width"] // 2, o["y"]))
            s = wait_for(lambda: (lambda s: s if s.get("open") and s.get("shown_at") else None)(island()),
                         "the Island to open from the edge", timeout=2)
            check(s["by"] == "edge", f"The Island opened by {s['by']!r}, not the edge")
            times.append(round(s["shown_at"] - pushed))
        self.shot("edge")
        self.pointer.park()
        wait_for(lambda: not island().get("open"), "the Island to close once the pointer left", timeout=2)
        times.sort()
        self.passed("edge", f"pushed against the top edge, the Island opened after its rest, and closed once the pointer "
                            f"left; push to first frame {times} ms (median {times[len(times) // 2]})")

    def check_shortcut(self):
        self.edges_page()
        self.pointer.park()
        times = []
        for _ in range(5):
            time.sleep(0.6)
            pressed = time.time() * 1000
            self.keys.keys("super", "b")
            s = wait_for(lambda: (lambda s: s if s.get("open") and s.get("shown_at") else None)(island()),
                         "Super+B to open the Island", timeout=3)
            check(s["by"] == "shortcut", f"The Island opened by {s['by']!r}, not Super+B")
            times.append(round(s["shown_at"] - pressed))
            self.keys.keys("esc")
            wait_for(lambda: not island().get("open"), "Escape to close the Island", timeout=2)
        times.sort()
        self.passed("shortcut", f"Super+B (a uinput key press, through Niri's binding) opened the Island and Escape closed "
                                f"it; key press to first frame {times} ms (median {times[len(times) // 2]})")

    def check_pill(self):
        s = island()
        check(s["clock_pill"] == "off" and s["pills"] == [], f"A fresh configuration shows a clock pill: {s}")
        try:
            display(True)
            ipc("clock_pill", "main")
            main = wait_for(lambda: island()["main"], "the main display")
            s = wait_for(lambda: (lambda s: s if s["pills"] else None)(island()), "the pill on the main display")
            check(s["pills"] == [main], f"With Main only, pills on {s['pills']}, not only {main}")
            self.shot("pill-main")
            ipc("clock_pill", "all")
            s = wait_for(lambda: (lambda s: s if len(s["pills"]) == 2 else None)(island()), "a pill on both displays")
            check(sorted(s["pills"]) == sorted([OUTPUT, "Virtual-2"]), f"With All screens, pills on {s['pills']}")
            self.shot("pill-all")
        finally:
            display(False)
        s = wait_for(lambda: (lambda s: s if s["pills"] == [OUTPUT] else None)(island()),
                     lambda: f"the pill to leave the unplugged display ({island()['pills']})", timeout=5)
        ipc("clock_pill", "off")
        wait_for(lambda: island()["pills"] == [], "the pill to go with the setting off")
        self.passed("pill", f"off by default; Main only showed it on {main} alone with Virtual-2 on, All screens on both, and "
                            "unplugging Virtual-2 took its pill away and kept Main's")

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
        wait_for(lambda: not island()["indicator"], "the work indicator to go after moving back", timeout=3)
        self.apps.close_window(kate)
        self.passed("moving", "the work indicator showed while moving between spaces (Super+Right, Super+Left), and went")

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
        subprocess.run(["busctl", "--user", "call", "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                        "org.freedesktop.Notifications", "Notify", "susssasa{sv}i", "Flow test", "0", "", "Over the page",
                        "A click here is the page's", "0", "0", "5000"], check=True, timeout=10, stdout=subprocess.DEVNULL)
        wait_for(lambda: island()["banner"], "the banner")
        # The banner hangs 8 px below the top edge in the middle, over the page's top-middle button (40 px tall).
        got = self.clicked((o["x"] + width // 2, o["y"] + 24))
        check(island()["banner"], "The banner was gone before the click")
        check(got == "top-middle", f"A click on the banner reached {got!r}, not the page's top-middle button")
        self.shot("banner")
        self.passed("banner", "a click on the banner reached the page's button beneath it")

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
        finally:
            ipc("notch_panel", "none")
            m.niri("output", OUTPUT, "mode", mode0)
            time.sleep(1.5)
        self.passed("band", f"a 32 px band on the simulated Air, the page tiled at 1280x800 below it, no edge strip there; "
                            f"fullscreen, the window was {full[0]}x{full[1]} (the band's surface still mapped: {covered})")


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
                env = {**os.environ, "XDG_STATE_HOME": dev_state, "XDG_CONFIG_HOME": shell_config(tmp)}
                shell = subprocess.Popen(["quickshell", "-p", SHELL], env=env, stdin=subprocess.DEVNULL,
                                         stdout=open(Path(tmp) / "shell.log", "w"), stderr=subprocess.STDOUT,
                                         start_new_session=True)
                wait_for(lambda: island(), "the shell under test", timeout=20)
                time.sleep(2)
                smoke = Smoke(tmp, args.shots, keys)
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
