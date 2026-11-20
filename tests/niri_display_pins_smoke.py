#!/usr/bin/env python3
"""Exercise display-owned pins across two real Niri outputs, including disconnect and reconnect.

Run inside a Niri session with two active outputs, from the Flow checkout:
    FLOW_HOTPLUG_CONNECTOR=/sys/class/drm/card1-Virtual-2 python3 tests/niri_display_pins_smoke.py
The second output's connector is switched off and on through its sysfs ``status`` file with
``sudo -n``, which raises the same DRM hotplug event as unplugging a cable; the test leaves it on.
Flow state is isolated under a temporary XDG_STATE_HOME. Real state files are checksummed and
disposable Foot windows are closed afterward.
"""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

CODE = Path(__file__).resolve().parents[1] / "session"
sys.path.insert(0, str(CODE))
import search  # noqa: E402


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


def run_tool(env, script, *args, expect_error=None):
    process = subprocess.run([sys.executable, str(CODE / script), *args], env=env, text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    value = json.loads(process.stdout)
    if expect_error:
        if expect_error not in value.get("error", ""):
            raise RuntimeError(f"{script} {args} should fail with {expect_error!r}: {value}")
        return value
    if process.returncode or "error" in value:
        raise RuntimeError(f"{script} {args}: {value.get('error') or process.stderr}")
    return value


def hotplug(connector, state):
    subprocess.run(["sudo", "-n", "tee", str(Path(connector) / "status")], input=state, text=True,
                   stdout=subprocess.DEVNULL, check=True)
    card = Path(connector).name.split("-")[0]
    subprocess.run(["sudo", "-n", "udevadm", "trigger", "--action=change", f"/sys/class/drm/{card}"], check=True)


def main():
    connector = os.environ.get("FLOW_HOTPLUG_CONNECTOR")
    if not connector:
        raise RuntimeError("Set FLOW_HOTPLUG_CONNECTOR to the second output's /sys/class/drm connector")
    second_name = Path(connector).name.split("-", 1)[1]
    outputs = {name: out for name, out in read("outputs").items() if out.get("logical")}
    original = focus()
    first_name = original["output"]
    if len(outputs) != 2 or second_name not in outputs or first_name == second_name:
        raise RuntimeError("Focus the first of exactly two active outputs; the second must be the hotplug connector")
    original_windows = read("windows")
    real_state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "flow"
    real_files = [real_state / name for name in ("spaces.json", "groups.json", "session.json")]
    real_digests = [digest(path) for path in real_files]
    active = {w["output"]: w for w in read("workspaces") if w.get("is_active")}
    if any(w.get("workspace_id") in {active[first_name]["id"], active[second_name]["id"]} for w in original_windows):
        raise RuntimeError("Both outputs' active workspaces must be empty")
    token = uuid.uuid4().hex[:8]
    apps = {s: f"flow-display-{token}-{s}" for s in ("a1", "b1", "b2")}
    processes, steps = [], []
    second_off = False
    result = None

    def window(suffix):
        found = [w for w in read("windows") if w.get("app_id") == apps[suffix]]
        if len(found) != 1:
            raise RuntimeError(f"Window {suffix} is missing or duplicated")
        return found[0]

    def where():
        """Each test window's workspace and column."""
        return {s: (window(s)["workspace_id"], window(s)["layout"]["pos_in_scrolling_layout"]) for s in apps}

    def spawn(suffix, output):
        niri("action", "focus-monitor", output)
        processes.append(subprocess.Popen(["foot", "--app-id", apps[suffix]], stdin=subprocess.DEVNULL,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        wait_for(lambda: any(w.get("app_id") == apps[suffix] for w in read("windows")), suffix)
        if read("workspaces") and next(w for w in read("workspaces") if w["id"] == window(suffix)["workspace_id"])["output"] != output:
            raise RuntimeError(f"{suffix} opened on the wrong output")

    try:
        spawn("a1", first_name)
        spawn("b1", second_name)
        spawn("b2", second_name)
        niri("action", "focus-window", "--id", window("b1")["id"])
        niri("action", "move-column-to-index", 1)
        with tempfile.TemporaryDirectory(prefix="flow-display-pins-") as tmp:
            env = dict(os.environ, XDG_STATE_HOME=tmp)

            def spaces(*args, **kw):
                return run_tool(env, "spaces.py", *args, **kw)

            def ids(rows):
                names = {ident: suffix for suffix, ident in pins.items()}
                return [names.get(p["id"], "?") for p in rows]

            pins = {}
            for suffix, output in (("a1", first_name), ("b1", second_name), ("b2", second_name)):
                niri("action", "focus-window", "--id", window(suffix)["id"])
                result = spaces("pin")
                pins[suffix] = next(p["id"] for p in result["pins"] if p["id"] not in pins.values())
            keys = {}
            niri("action", "focus-monitor", first_name)
            on_first = spaces("refresh")
            keys[first_name] = on_first["display"]["key"]
            niri("action", "focus-monitor", second_name)
            on_second = spaces("refresh")
            keys[second_name] = on_second["display"]["key"]
            if (ids(on_first["pins"]) != ["a1"] or ids(on_second["pins"]) != ["b1", "b2"]
                    or [p["display_state"] for p in on_first["other_pins"]] != ["connected", "connected"]
                    or keys[first_name] == keys[second_name] or on_first["display_count"] != 2):
                raise RuntimeError(f"Pins are not owned per display: {on_first} {on_second}")
            steps.append(f"pins owned per display: {keys}")

            niri("action", "focus-monitor", first_name)
            routed = spaces("focus", pins["b2"])
            if focus()["output"] != second_name or focus()["active_window_id"] != window("b2")["id"] \
                    or routed["display"]["key"] != keys[second_name]:
                raise RuntimeError("Focusing another display's pin did not route there")
            steps.append("another display's pin focused its display first")

            spaces("right", pins["b1"])
            reordered = wait_for(lambda: (r := spaces("refresh")) and ids(r["pins"]) == ["b2", "b1"]
                                 and window("b2")["layout"]["pos_in_scrolling_layout"][0] == 1 and r, "reordered pins")
            niri("action", "focus-monitor", first_name)
            if ids(spaces("refresh")["pins"]) != ["a1"]:
                raise RuntimeError("Reordering one display changed another")
            steps.append("reordered pins on one display only")

            before = where()
            hotplug(connector, "off")
            second_off = True
            wait_for(lambda: len([o for o in read("outputs").values() if o.get("logical")]) == 1, "disconnected output")
            unplugged = spaces("refresh")
            if (ids(unplugged["pins"]) != ["a1"] or unplugged["display_count"] != 1
                    or [p["display_state"] for p in unplugged["other_pins"]] != ["disconnected", "disconnected"]
                    or not all(p["available"] for p in unplugged["other_pins"])):
                raise RuntimeError(f"Disconnected display's pins were not kept aside: {unplugged}")
            moved = {s: before[s][1] != where()[s][1] for s in ("b1", "b2")}
            if any(moved.values()):
                raise RuntimeError(f"Disconnect reordered the other display's windows: {moved}")
            listed = [label for label, action in search.entries(unplugged, []) if action[0] == "pin"]
            if len(listed) != 3:
                raise RuntimeError(f"Search did not list disconnected pins with open windows: {listed}")
            spaces("focus", pins["b1"])
            if focus()["active_window_id"] != window("b1")["id"]:
                raise RuntimeError("A disconnected display's pin did not focus its window")
            spaces("refresh")
            if {s: where()[s][1] for s in ("b1", "b2")} != {s: before[s][1] for s in ("b1", "b2")}:
                raise RuntimeError("Refreshing beside the moved windows reordered them")
            steps.append("disconnect kept the pins aside; their windows were focused in place, not moved")

            hotplug(connector, "on")
            second_off = False
            wait_for(lambda: len([o for o in read("outputs").values() if o.get("logical")]) == 2, "reconnected output")
            wait_for(lambda: next(w for w in read("workspaces") if w["id"] == before["b1"][0])["output"] == second_name,
                     "workspace returned to the reconnected output")
            niri("action", "focus-monitor", second_name)
            replugged = spaces("refresh")
            if (ids(replugged["pins"]) != ["b2", "b1"] or replugged["display"]["key"] != keys[second_name]
                    or not all(p["available"] for p in replugged["pins"])):
                raise RuntimeError(f"Reconnected display did not get its pins back in order: {replugged}")
            if where() != before:
                raise RuntimeError(f"Windows moved across the reconnect: {before} → {where()}")
            steps.append("reconnect restored the display's pins in order without moving windows")

            niri("action", "focus-monitor", first_name)
            here = spaces("move-pin-here", pins["b1"])
            if ids(here["pins"]) != ["a1", "b1"] or here["pins"][1]["available"]:
                raise RuntimeError("Move here did not append the pin to this display")
            niri("action", "focus-monitor", second_name)
            if ids(spaces("move-pin-here", pins["b1"])["pins"]) != ["b2", "b1"]:
                raise RuntimeError("Pin did not move back")
            steps.append("pins moved between displays only on request")

            run_tool(env, "restore.py", "save")
            niri("action", "focus-monitor", first_name)
            run_tool(env, "restore.py", "restore", expect_error="another connected display")
            steps.append("recovery refused a session saved on the other display")
            result = {"result": "pass", "niri": niri("version").strip(), "outputs": [first_name, second_name],
                      "steps": steps}
    finally:
        if second_off:
            hotplug(connector, "on")
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        try:
            for w in read("windows"):
                if w.get("app_id") in apps.values():
                    niri("action", "close-window", "--id", w["id"])
            niri("action", "focus-monitor", first_name)
        except (OSError, subprocess.SubprocessError):
            pass
    if [digest(path) for path in real_files] != real_digests:
        raise RuntimeError("A real Flow Group, pin, or session file changed")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, StopIteration, subprocess.SubprocessError, ValueError) as exc:
        print(f"Niri display pins test failed: {exc}", file=sys.stderr)
        sys.exit(1)
