"""Screenshots of resting chrome by device (ADR-0014) on the shell under test, in the guest, over Kate, Firefox and
Nautilus's chooser (Firefox's upload), for docs/screenshots.

A plain screen at 1280x800, 1920x1080 and narrow-800 (1280x800 at scale 1.6): at rest; the clock pill on; the Island
opened by pushing the pointer against the top edge; a notification's banner; the work indicator mid-move. The clock
pill on two displays (Virtual-2 on through sysfs, off again after): Main screen only, and All screens. A simulated
notched MacBook (the notchPanel override and a custom mode one band taller than the rectangle apps get): the 13-inch
Air at 1280x832 and at scale 1.6, the 14-inch Pro scaled to 1920x1247: at rest, an activity, a banner, mid-move, and a
fullscreen window.

Run inside the Niri session under the screen lock, from the checkout's root, with the test apps on PATH (as
tests/run_portals_smoke.sh sets them). The shell is ``FLOW_SHELL`` (default the installed /etc/flow/shell.qml), as a
development shell with isolated state and a configuration with recovery off, the installed service stopped and started
again however the run ends. The output's mode and scale, the second display and the user's state are put back, and
an activity file the run wrote is removed. Usage: python3 tests/device_island_shots.py OUT_DIR
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import niri_portals_smoke as m  # noqa: E402
from isolated_state import IsolatedState, shell_config  # noqa: E402
from niri_device_island_smoke import display  # noqa: E402

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
SHELL = os.environ.get("FLOW_SHELL", "/etc/flow/shell.qml")
OUTPUT = "Virtual-1"
PLAIN = [("1280x800", "1280x800@119.997", 1), ("1920x1080", "1920x1080@60.000", 1), ("narrow-800", "1280x800@119.997", 1.6)]
NOTCHED = [("1280x800", "air-13", "1280x832@60", 1), ("1920-pro14", "pro-14", "1920x1247@60", 1),
           ("narrow-800", "air-13", "1280x832@60", 1.6)]
ACTIVITIES = Path(os.environ.get("XDG_RUNTIME_DIR", "/nonexistent")) / "flow-activities.json"


def ipc(*args):
    return subprocess.run(["quickshell", "ipc", "-p", SHELL, "call", "desk", *map(str, args)], text=True,
                          capture_output=True, timeout=10).stdout.strip()


def island():
    try:
        return json.loads(ipc("island_state"))
    except ValueError:
        return {}


def shot(name, output=OUTPUT):
    m.run("grim", *(["-o", output] if output else []), OUT / f"{name}.png", timeout=30)


def output(mode, scale, custom=False):
    m.niri("output", OUTPUT, "custom-mode" if custom else "mode", mode)
    m.niri("output", OUTPUT, "scale", scale)
    time.sleep(1.5)


def notify(summary, body):
    subprocess.run(["busctl", "--user", "call", "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                    "org.freedesktop.Notifications", "Notify", "susssasa{sv}i", "Mail", "0", "", summary, body,
                    "0", "0", "5000"], check=True, timeout=10, stdout=subprocess.DEVNULL)


def main():
    before = m.read("outputs")[OUTPUT]
    cur = before["modes"][before["current_mode"]]
    mode0 = f"{cur['width']}x{cur['height']}@{cur['refresh_rate'] / 1000:.3f}"
    scale0 = before["logical"]["scale"]
    state0 = m.UserState()
    wrote_activities = False
    shell = None
    try:
        with tempfile.TemporaryDirectory(prefix="flow-island-shots-") as tmp, IsolatedState("flow-island-shots-state-") as dev:
            smoke = m.Smoke(tmp, None)
            try:
                notes = Path(tmp) / "notes.md"
                notes.write_text("# Thursday\n\nThe notes for Thursday are in the shared folder.\n\n- agenda\n- minutes\n")
                folder = Path(tempfile.mkdtemp(prefix="flow-island-files-", dir=Path.home()))
                for name in ("agenda.txt", "minutes.txt", "slides.pdf"):
                    (folder / name).write_text(name)
                smoke.need_pages()
                firefox = smoke.need_firefox(mode="upload")
                _, kate = smoke.apps.start(["kate", notes], app_id="org.kde.kate")
                subprocess.run(["systemctl", "--user", "stop", "flow-shell"], check=True)
                env = {**os.environ, "XDG_STATE_HOME": dev, "XDG_CONFIG_HOME": shell_config(tmp)}
                shell = subprocess.Popen(["quickshell", "-p", SHELL], env=env, stdin=subprocess.DEVNULL,
                                         stdout=open(OUT / "shell.log", "w"), stderr=subprocess.STDOUT,
                                         start_new_session=True)
                end = time.monotonic() + 20
                while not island() and time.monotonic() < end:
                    time.sleep(0.2)
                time.sleep(2)

                def resize(mode, scale, custom=False):
                    output(mode, scale, custom)
                    smoke.pointer = m.Pointer(allow_installed_shell=True)  # the layout's size changed
                    smoke.apps.output = m.read("outputs")[OUTPUT]["logical"]
                    smoke.apps.focus(firefox, "100%")
                    smoke.apps.focus(kate, "100%")

                def views(prefix, arm=None):
                    def take(name):
                        if arm:
                            arm()
                            time.sleep(0.8)
                        shot(f"{prefix}-{name}")
                    smoke.pointer.park()
                    smoke.apps.focus(kate, "100%")
                    time.sleep(0.8)
                    take("kate")
                    smoke.apps.focus(firefox, "100%")
                    time.sleep(0.8)
                    take("firefox")
                    before_windows = set(m.windows())
                    smoke.pointer.click(smoke.apps.at(0.5, 0.5))
                    dialog = smoke.dialog("org.gnome.Nautilus", before_windows, "Firefox's upload dialog")
                    m.keys("ctrl+l", f"{folder}/", "Return")
                    time.sleep(1.2)
                    smoke.pointer.park()
                    time.sleep(2)
                    take("chooser")
                    m.keys("Escape")
                    time.sleep(1)
                    smoke.apps.close_window(dialog)
                    smoke.apps.focus(firefox)
                    time.sleep(5)  # a banner from the last view has gone

                def moving(prefix):
                    # Mid-move: Niri slides to the space beside, and the work indicator shows.
                    smoke.apps.focus(kate, "100%")
                    time.sleep(1)
                    m.niri("action", "focus-window", "--id", firefox["id"])
                    time.sleep(0.15)
                    shot(f"{prefix}-moving")
                    time.sleep(1.5)

                def opened(prefix):
                    smoke.apps.focus(firefox, "100%")
                    smoke.pointer.park()
                    time.sleep(0.6)
                    o = smoke.apps.output
                    smoke.pointer.move((o["x"] + o["width"] // 2, o["y"]))
                    time.sleep(0.8)
                    shot(f"{prefix}-opened")
                    smoke.pointer.park()
                    time.sleep(1.2)

                arm = lambda: notify("Ada Lovelace", "The notes for Thursday are in the shared folder")  # noqa: E731

                # A: plain screens ----------------------------------------------------------------------------------
                ipc("notch_panel", "none")
                for label, mode, scale in PLAIN:
                    resize(mode, scale)
                    views(f"{label}-A-rest")
                    ipc("clock_pill", "main")
                    views(f"{label}-A-clock")
                    ipc("clock_pill", "off")
                    opened(f"{label}-A")
                    views(f"{label}-A-notify", arm=arm)
                    moving(f"{label}-A")

                # The clock pill on two displays.
                resize("1280x800@119.997", 1)
                display(True)
                m.niri("action", "focus-monitor", "Virtual-2")
                smoke.apps.start(["foot", "--app-id", "flow-island-shots-second"], app_id="flow-island-shots-second")
                m.niri("action", "focus-monitor", OUTPUT)
                smoke.apps.focus(kate, "100%")
                for setting in ("main", "all"):
                    ipc("clock_pill", setting)
                    time.sleep(1)
                    shot(f"two-displays-pill-{setting}", output=None)
                ipc("clock_pill", "off")
                display(False)

                # B: a simulated notched MacBook ---------------------------------------------------------------------
                for label, panel, mode, scale in NOTCHED:
                    ipc("notch_panel", panel)
                    resize(mode, scale, custom=True)
                    views(f"{label}-B-rest")
                    if not ACTIVITIES.exists():
                        ACTIVITIES.write_text(json.dumps({"version": 1, "activities": [
                            {"id": "build", "owner": "Build", "title": "flow", "progress": 0.62}]}))
                        wrote_activities = True
                        smoke.apps.focus(firefox, "100%")
                        time.sleep(1.2)
                        shot(f"{label}-B-activity")
                        ACTIVITIES.unlink()
                        wrote_activities = False
                    views(f"{label}-B-notify", arm=arm)
                    moving(f"{label}-B")
                    m.niri("action", "fullscreen-window", "--id", firefox["id"])
                    time.sleep(1.5)
                    shot(f"{label}-B-fullscreen")
                    m.niri("action", "fullscreen-window", "--id", firefox["id"])
                    time.sleep(1)
                ipc("notch_panel", "none")
            finally:
                if shell and shell.poll() is None:
                    shell.terminate()
                    shell.wait(10)
                smoke.stop()
    finally:
        if wrote_activities and ACTIVITIES.exists():
            ACTIVITIES.unlink()
        if "Virtual-2" in m.read("outputs"):
            display(False)
        subprocess.run(["systemctl", "--user", "start", "flow-shell"])
        output(mode0, scale0)
        print("put back:", state0.put_back(), "output:", mode0, scale0, flush=True)


if __name__ == "__main__":
    main()
