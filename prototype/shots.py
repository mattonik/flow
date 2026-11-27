"""Screenshots of the thin-margin prototype beside the current design, in the guest, at 1280x800, 1920x1080 and a
narrow width (800 px logical: 1280x800 at scale 1.6), over Kate, Firefox and Nautilus's chooser (Firefox's upload).

Run inside the Niri session under the screen lock, from a copy of the checkout, with the test apps on PATH (as
tests/run_portals_smoke.sh sets it). The current design is the installed shell. The prototype is
prototype/thin-margin.qml in a development shell with isolated state and a configuration with recovery off, while
the installed shell is stopped; it is started again however the run ends. The output's mode and scale are put back.
Usage: python3 prototype/shots.py OUT_DIR
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import niri_portals_smoke as m  # noqa: E402
from isolated_state import IsolatedState, shell_config  # noqa: E402

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
OUTPUT = "Virtual-1"
SIZES = [("1280x800", "1280x800@119.997", 1), ("1920x1080", "1920x1080@60.000", 1), ("narrow-800", "1280x800@119.997", 1.6)]
PROTO = ROOT / "prototype" / "thin-margin.qml"


def output(mode, scale):
    m.niri("output", OUTPUT, "mode", mode)
    m.niri("output", OUTPUT, "scale", scale)
    time.sleep(1.5)


def shot(name):
    m.run("grim", "-o", OUTPUT, OUT / f"{name}.png", timeout=30)


def proto(*args):
    subprocess.run(["quickshell", "ipc", "-p", str(PROTO), "call", "proto", *map(str, args)], check=True, timeout=10,
                   capture_output=True)


def main():
    before = m.read("outputs")[OUTPUT]
    mode0 = f"{before['modes'][before['current_mode']]['width']}x{before['modes'][before['current_mode']]['height']}@" \
            f"{before['modes'][before['current_mode']]['refresh_rate'] / 1000:.3f}"
    scale0 = before["logical"]["scale"]
    state = m.UserState()
    shell = None
    try:
        with tempfile.TemporaryDirectory(prefix="flow-proto-") as tmp, IsolatedState("flow-proto-state-") as dev_state:
            smoke = m.Smoke(tmp, None)
            try:
                notes = Path(tmp) / "notes.md"
                notes.write_text("# Thursday\n\nThe notes for Thursday are in the shared folder.\n\n- agenda\n- minutes\n")
                folder = Path(tempfile.mkdtemp(prefix="flow-proto-files-", dir=Path.home()))
                for name in ("agenda.txt", "minutes.txt", "slides.pdf"):
                    (folder / name).write_text(name)
                smoke.need_pages()
                firefox = smoke.need_firefox(mode="upload")
                _, kate = smoke.apps.start(["kate", notes], app_id="org.kde.kate")

                def views(prefix):
                    smoke.apps.focus(kate, "100%")
                    time.sleep(0.8)
                    shot(f"{prefix}-kate")
                    smoke.apps.focus(firefox, "100%")
                    time.sleep(0.8)
                    shot(f"{prefix}-firefox")
                    before_windows = set(m.windows())
                    smoke.pointer.click(smoke.apps.at(0.5, 0.5))
                    dialog = smoke.dialog("org.gnome.Nautilus", before_windows, "Firefox's upload dialog")
                    m.keys("ctrl+l", f"{folder}/", "Return")
                    time.sleep(1.2)
                    smoke.pointer.park()
                    time.sleep(2)
                    shot(f"{prefix}-chooser")
                    m.keys("Escape")
                    time.sleep(1)
                    smoke.apps.close_window(dialog)
                    smoke.apps.focus(firefox)

                for label, mode, scale in SIZES:
                    output(mode, scale)
                    smoke.pointer = m.Pointer(allow_installed_shell=True)  # the layout's size changed
                    smoke.apps.output = m.read("outputs")[OUTPUT]["logical"]
                    views(f"{label}-current")
                    subprocess.run(["systemctl", "--user", "stop", "flow-shell"], check=True)
                    env = {**os.environ, "XDG_STATE_HOME": dev_state, "XDG_CONFIG_HOME": shell_config(tmp)}
                    shell = subprocess.Popen(["quickshell", "-p", str(PROTO)], env=env, stdin=subprocess.DEVNULL,
                                             stdout=open(OUT / "proto.log", "a"), stderr=subprocess.STDOUT,
                                             start_new_session=True)
                    time.sleep(3)
                    proto("view", "rest")
                    views(f"{label}-proto-rest")
                    smoke.apps.focus(firefox)
                    for name in ("expanded", "chip", "notify", "moving"):
                        proto("view", name)
                        time.sleep(0.8)
                        shot(f"{label}-proto-{name}")
                    proto("view", "rest")
                    proto("homeline", "false")
                    time.sleep(0.5)
                    shot(f"{label}-proto-rest-no-touch")
                    proto("homeline", "true")
                    shell.terminate()
                    shell.wait(10)
                    shell = None
                    subprocess.run(["systemctl", "--user", "start", "flow-shell"], check=True)
                    time.sleep(4)
            finally:
                smoke.stop()
    finally:
        if shell and shell.poll() is None:
            shell.terminate()
            shell.wait(10)
        subprocess.run(["systemctl", "--user", "start", "flow-shell"])
        output(mode0, scale0)
        print("put back:", state.put_back(), "output:", mode0, scale0, flush=True)


if __name__ == "__main__":
    main()
