"""Screenshots and two measurements of the device-adaptive Island prototype (prototype/adaptive-island.qml), in the
guest, over Kate, Firefox and Nautilus's chooser (Firefox's upload).

A, a plain screen (nothing at rest), at 1280x800, 1920x1080 and narrow-800 (1280x800 at scale 1.6).
B, a notched MacBook in Asahi's full-panel mode, simulated with a custom mode one band taller than the rectangle apps
get, and the band drawn black with the cutout; the geometry is a real panel's, scaled to the guest (see NOTCHES).

Then, on A at 1280x800 over a full-window page with buttons along its top edge (tests/niri_portals_smoke.py's
``edges`` page): (a) which rows of the top edge reach the page through the 1 px edge catcher, and the clock pill's
bounds; (b) the time from pushing the pointer against the edge, or from the shortcut's call, to the Island on screen.

Run inside the Niri session under the screen lock, from a copy of the checkout, with the test apps on PATH (as
tests/run_portals_smoke.sh sets it). The installed shell is stopped while the prototype runs (a development shell with
isolated state and a configuration with recovery off) and started again however the run ends; the output's mode and
scale and the user's state are put back. Usage: python3 prototype/adaptive_shots.py OUT_DIR
"""
import json
import os
from pathlib import Path
import statistics
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
PROTO = ROOT / "prototype" / "adaptive-island.qml"
PLAIN = [("1280x800", "1280x800@119.997", 1), ("1920x1080", "1920x1080@60.000", 1), ("narrow-800", "1280x800@119.997", 1.6)]
# (label, custom mode = the full panel scaled, scale, band, notch width, notch corner radius), in the mode's pixels.
# 13.6" MacBook Air (M2): 2560x1664 panel, Asahi's default 2560x1600 (a 64-row band), cutout about 312 px wide with a
# 14 px corner (approximate). Scaled by 0.5: 1280x832, apps get 1280x800, band 32, cutout 156, corner 7.
# 14" MacBook Pro: 3024x1964 panel, default 3024x1890 (74 rows), cutout about 360 px with a 17 px corner
# (approximate). Scaled by 1920/3024 = 0.635: 1920x1247, apps get 1920x1200, band 47, cutout 229, corner 11.
NOTCHES = [("1280x800", "1280x832@60", 1, 32, 156, 7), ("1920-mbp14", "1920x1247@60", 1, 47, 229, 11),
           ("narrow-800", "1280x832@60", 1.6, 32, 156, 7)]


def output(mode, scale, custom=False):
    m.niri("output", OUTPUT, "custom-mode" if custom else "mode", mode)
    m.niri("output", OUTPUT, "scale", scale)
    time.sleep(1.5)


def shot(name):
    m.run("grim", "-o", OUTPUT, OUT / f"{name}.png", timeout=30)


def proto(*args):
    return subprocess.run(["quickshell", "ipc", "-p", str(PROTO), "call", "proto", *map(str, args)], check=True,
                          timeout=10, capture_output=True, text=True).stdout.strip()


def state():
    return json.loads(proto("state"))


def pixel(x, y):
    """The colour on screen at (x, y) in the output's logical pixels, from grim (scale 1)."""
    data = subprocess.run(["grim", "-g", f"{x},{y} 1x1", "-t", "ppm", "-"], check=True, timeout=10,
                          capture_output=True).stdout
    return tuple(data[-3:])


def main():
    before = m.read("outputs")[OUTPUT]
    cur = before["modes"][before["current_mode"]]
    mode0 = f"{cur['width']}x{cur['height']}@{cur['refresh_rate'] / 1000:.3f}"
    scale0 = before["logical"]["scale"]
    state0 = m.UserState()
    shell = None
    results = {}
    try:
        with tempfile.TemporaryDirectory(prefix="flow-adaptive-") as tmp, IsolatedState("flow-adaptive-state-") as dev_state:
            smoke = m.Smoke(tmp, None)
            try:
                notes = Path(tmp) / "notes.md"
                notes.write_text("# Thursday\n\nThe notes for Thursday are in the shared folder.\n\n- agenda\n- minutes\n")
                folder = Path(tempfile.mkdtemp(prefix="flow-adaptive-files-", dir=Path.home()))
                for name in ("agenda.txt", "minutes.txt", "slides.pdf"):
                    (folder / name).write_text(name)
                smoke.need_pages()
                firefox = smoke.need_firefox(mode="upload")
                _, kate = smoke.apps.start(["kate", notes], app_id="org.kde.kate")

                subprocess.run(["systemctl", "--user", "stop", "flow-shell"], check=True)
                env = {**os.environ, "XDG_STATE_HOME": dev_state, "XDG_CONFIG_HOME": shell_config(tmp)}
                shell = subprocess.Popen(["quickshell", "-p", str(PROTO)], env=env, stdin=subprocess.DEVNULL,
                                         stdout=open(OUT / "proto.log", "a"), stderr=subprocess.STDOUT,
                                         start_new_session=True)
                time.sleep(3)

                def resize(mode, scale, custom=False):
                    output(mode, scale, custom)
                    smoke.pointer = m.Pointer(allow_installed_shell=True)  # the layout's size changed
                    smoke.apps.output = m.read("outputs")[OUTPUT]["logical"]
                    smoke.apps.focus(firefox, "100%")
                    smoke.apps.focus(kate, "100%")

                def views(prefix, arm=None):
                    def take(name):
                        proto("app", "Kate" if name == "kate" else "Firefox")
                        time.sleep(0.3)  # the label's next frame, before grim
                        if arm:
                            arm()
                            time.sleep(0.6)
                        shot(f"{prefix}-{name}")
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

                def moving(prefix):
                    # Mid-move: the page dots while Niri slides to the next space.
                    proto("view", "moving")
                    m.niri("action", "focus-workspace-down")
                    time.sleep(0.02)
                    shot(f"{prefix}-moving")
                    time.sleep(1)
                    proto("view", "rest")
                    smoke.apps.focus(firefox)
                    time.sleep(0.8)

                # A: plain screen ------------------------------------------------------------------------------
                proto("device", "plain")
                for label, mode, scale in PLAIN:
                    resize(mode, scale)
                    views(f"{label}-A-rest")
                    proto("clock_pill", "true")
                    views(f"{label}-A-clock")
                    proto("clock_pill", "false")
                    # Revealed for real: the pointer pushed against the top edge, resting past the dwell.
                    smoke.apps.focus(firefox, "100%")
                    proto("app", "Firefox")
                    o = smoke.apps.output
                    smoke.pointer.move((o["x"] + o["width"] // 2, o["y"]))
                    time.sleep(0.8)
                    s = state()
                    shot(f"{label}-A-expanded")
                    smoke.pointer.park()
                    time.sleep(1.2)
                    print(label, "A revealed by", s["revealed_by"], "then", state()["state"], flush=True)
                    views(f"{label}-A-notify", arm=lambda: proto("view", "notify"))
                    proto("view", "rest")
                    moving(f"{label}-A")

                # B: notched MacBook, simulated ----------------------------------------------------------------
                proto("device", "notch")
                for label, mode, scale, band, width, radius in NOTCHES:
                    # The geometry is in the mode's pixels; the prototype draws in logical ones.
                    proto("geometry", round(band / scale), round(width / scale), round(radius / scale))
                    resize(mode, scale, custom=True)
                    print(label, "B output", smoke.apps.output, "windows start at",
                          m.windows()[firefox["id"]]["layout"].get("tile_pos_in_workspace_view"), flush=True)
                    views(f"{label}-B-rest")
                    smoke.apps.focus(firefox, "100%")
                    proto("app", "Firefox")
                    for name in ("expanded", "chip"):
                        proto("view", name)
                        time.sleep(0.8)
                        shot(f"{label}-B-{name}")
                    proto("view", "rest")
                    views(f"{label}-B-notify", arm=lambda: proto("view", "notify"))
                    proto("view", "rest")
                    moving(f"{label}-B")

                # Measurements: A at 1280x800 over the edges page ---------------------------------------------
                proto("device", "plain")
                resize("1280x800@119.997", 1)
                results = measure(smoke)
            finally:
                if shell and shell.poll() is None:
                    shell.terminate()
                    shell.wait(10)
                smoke.stop()
    finally:
        if shell and shell.poll() is None:
            shell.terminate()
            shell.wait(10)
        subprocess.run(["systemctl", "--user", "start", "flow-shell"])
        output(mode0, scale0)
        (OUT / "measure.json").write_text(json.dumps(results, indent=1))
        print("put back:", state0.put_back(), "output:", mode0, scale0, flush=True)


def measure(smoke):
    pages = smoke.need_pages()
    page = smoke.need_firefox(kiosk=True, mode="edges")
    o = smoke.apps.output
    width = m.windows()[page["id"]]["layout"]["window_size"][0]
    results = {"strip_px": state()["strip"], "reach": {}, "pill": {}, "latency": {}}

    def collapse():
        smoke.pointer.park()
        proto("view", "rest")
        time.sleep(0.9)

    def click(x, y, expect):
        pages.drain()
        smoke.pointer.click((o["x"] + x, o["y"] + y))
        try:
            event = pages.wait(["clicked"], timeout=2)
            got = event["name"]
        except m.Failed:
            got = None
        s = state()
        collapse()
        return {"page": got, "expected": expect, "island": s["state"], "by": s["revealed_by"] if s["state"] == "expanded" else ""}

    # (a) Which rows of the top edge reach the page's top buttons (40 px tall, from y=0).
    for name, x in (("top-left", 30), ("top-middle", width // 2), ("top-right", width - 30)):
        for y in (0, 1, 2, 3):
            results["reach"][f"{name} y={y}"] = click(x, y, name)
    # The clock pill: a click on it reveals the Island; 2 px beside it, the page.
    proto("clock_pill", "true")
    time.sleep(0.6)
    pw, ph, top = json.loads(proto("pill_size"))
    cx = width // 2
    results["pill"]["size"] = [pw, ph, top]
    results["pill"]["on it"] = click(cx, top + ph // 2, "the Island")
    results["pill"]["2 px right of it"] = click(cx + (pw + 1) // 2 + 2, top + ph // 2, "top-middle")
    results["pill"]["2 px below it"] = click(cx, top + ph + 2, "top-middle")
    proto("clock_pill", "false")
    time.sleep(0.5)

    # (b) Push (or shortcut) to the Island on screen: grim polls one pixel inside where the card shows.
    probe = (o["x"] + width // 2, 30)
    grim_ms = []
    for _ in range(10):
        t = time.monotonic()
        pixel(*probe)
        grim_ms.append((time.monotonic() - t) * 1000)
    results["grim_call_ms"] = {"median": round(statistics.median(grim_ms), 1), "max": round(max(grim_ms), 1)}

    def trial(go):
        collapse()
        base = pixel(*probe)
        t0 = time.monotonic()
        w0 = time.time() * 1000
        go()
        t1 = time.monotonic()
        while time.monotonic() - t1 < 3:
            if pixel(*probe) != base:
                t2 = time.monotonic()
                s = state()
                return {"from_start_ms": round((t2 - t0) * 1000), "from_return_ms": round((t2 - t1) * 1000),
                        "spawn_ms": round((t1 - t0) * 1000),
                        "entered_to_shown_ms": round(s["shown_at"] - s["entered_at"]) if s["revealed_by"] == "edge" else None,
                        "start_to_shown_ms": round(s["shown_at"] - w0), "by": s["revealed_by"]}
        return {"missed": True, "state": state()}

    def summary(rows):
        ok = [r for r in rows if not r.get("missed")]
        out = {"n": len(rows), "missed": len(rows) - len(ok)}
        for key in ("from_start_ms", "from_return_ms", "spawn_ms", "entered_to_shown_ms", "start_to_shown_ms"):
            vals = [r[key] for r in ok if r.get(key) is not None]
            if vals:
                out[key] = {"median": statistics.median(vals), "min": min(vals), "max": max(vals)}
        return out

    edge = (o["x"] + width // 2, o["y"])
    for dwell in (150, 0):
        proto("dwell", dwell)
        rows = [trial(lambda: smoke.pointer.move(edge)) for _ in range(15)]
        results["latency"][f"edge, dwell {dwell} ms"] = {"summary": summary(rows), "rows": rows}
    proto("dwell", 150)
    rows = [trial(lambda: proto("reveal")) for _ in range(15)]
    results["latency"]["shortcut (the ipc call a key binding spawns)"] = {"summary": summary(rows), "rows": rows}
    collapse()
    smoke.apps.close_window(page)
    print(json.dumps({k: v for k, v in results.items() if k != "latency"}, indent=1), flush=True)
    print(json.dumps({k: v["summary"] for k, v in results["latency"].items()}, indent=1), flush=True)
    return results


if __name__ == "__main__":
    main()
