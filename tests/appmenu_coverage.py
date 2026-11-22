#!/usr/bin/env python3
r"""How far Flow's AT-SPI app-menu reader reaches, app by app: the table in
docs/engineering/APPMENU_COVERAGE_2026-11.md.

For each app in APPS it opens one window on the focused output's empty workspace and records:
- whether the app is on the accessibility bus (an AT-SPI application with the window's PID; for an X11 app also
  the PID the run started) and has a menu bar, with each bar's frame, showing state and item count;
- what this checkout's reader (session/appmenu.py, run as ``flow-appmenu`` runs it: a fresh process, as the
  shell starts it) returns for the window: menu items, or its error, and its wall time (median of three runs).
  The reader's app_name() raises for an app_id with no matching desktop file (LibreOffice), and the reader then
  returns only that error; so the reader is also run with only that exception caught ("fixed"), to show what the
  walk itself reaches. The commands and the late column use the fixed reader;
- the walk itself (``menu_walk``, the reader's walk with counters; a unit test runs both on one tree), in a fresh
  process: AT-SPI nodes visited, menu items, empty menus (unpopulated submenus are dropped by the walk), time, and
  the same walk without the reader's caps (2000 nodes, depth 12), so a cap that cuts the menu shows;
- how many of the items carry a shortcut label (``Action.getKeyBinding``);
- whether a harmless command (an About dialog) runs through the reader's ``activate`` path, AT-SPI
  ``doAction``, and whether its effect shows (a new window of the app's process, closed again);
- how many Niri windows the window's process owns (the reader gives up when it owns more than one);
- the reader's result for a window opened while accessibility was off ("late": the reader switches it on).

Then, for the apps in MULTI, it opens more windows in the same process and checks, with each focused in turn,
whether the accessible frames' names and ``active`` state single out the focused window: by title alone, by
``active`` alone, and by both. It does that once with the window focused, and once more with an exclusive-
keyboard layer surface over it, as Flow's own menu surface is. For Kate it then invokes File > New through the
chosen frame's menu and checks which Niri window changed title: the ground truth.

Run it with ``tests/run_appmenu_coverage.sh`` (it takes the Python with pyatspi from the installed
``flow-appmenu`` wrapper, imports the session's display variables, clears ``NO_AT_BRIDGE`` and ``GTK_A11Y``, and
holds the screen lock) inside a Niri session where the session bus can start the accessibility bus. Apps not on
PATH are built with ``nix build --no-link`` from ``FLOW_NIXPKGS`` (default ``nixpkgs``); ``FLOW_NIX`` and
``FLOW_NIX_ARGS`` change the command (the test guest needs ``sudo -n nix``,
``--option ssl-cert-file /etc/floe-test/host-trust.pem`` and ``--option flake-registry ''`` for its pinned
nixpkgs). The system's configuration is not changed.

Safety on a shared session:
- Only windows of processes the run started (their session, or the app process their window joined) are
  measured and closed; an X11 window, whose PID is xwayland-satellite's, is taken only by its app_id.
- ``org.a11y.Status.IsEnabled`` is set only for the run. It is put back as it was at the end, also on SIGINT,
  SIGTERM and SIGHUP, unless someone else changed it meanwhile, which is reported and left alone; a late phase
  whose switch someone else changed is skipped. ``--cleanup`` undoes a run that was killed, from the state file
  ``$XDG_RUNTIME_DIR/flow-appmenu-coverage.json``.
- Each app gets its own temporary configuration, data and cache directories and browser profiles, and GSettings
  in memory, so real settings are not touched.
- Nautilus is single-instance, and a session's Nautilus usually runs already: it runs on a private session bus,
  with its own accessibility bus and runtime directory, and every process on that bus is stopped afterward.
  A private bus that does not start stops the run.

    tests/run_appmenu_coverage.sh [--only kate,writer] [--skip-multi] [--skip-late] [--json out.json]
    tests/run_appmenu_coverage.sh --dump kate     # print the app's menu tree, to pick a command
    tests/run_appmenu_coverage.sh --cleanup       # after a run that was killed
"""

import argparse
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
import uuid

READER = Path(__file__).resolve().parents[1] / "session" / "appmenu.py"
CAP_NODES, CAP_DEPTH = 2000, 12  # session/appmenu.py's walk limits
PROC = Path("/proc")
# Every process the run starts carries this in its environment, also one that detaches into a session of its
# own (VSCodium's launcher does): that is how the run knows its own processes and windows.
MARKER = "FLOW_APPMENU_COVERAGE_RUN"

FIREFOX_PREFS = """user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.aboutwelcome.enabled", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("toolkit.telemetry.reportingpolicy.firstRun", false);
user_pref("trailhead.firstrun.didSeeAboutWelcome", true);
"""
LIBREOFFICE_PREFS = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema">
<item oor:path="/org.openoffice.Office.Common/Misc"><prop oor:name="ShowTipOfTheDay" oor:op="fuse"><value>false</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Misc"><prop oor:name="FirstRun" oor:op="fuse"><value>false</value></prop></item>
</oor:items>
"""
# An exclusive-keyboard overlay, as Flow's app-menu surface is (session/shell.qml, WlrKeyboardFocus.Exclusive).
LAYER_QML = """import Quickshell
import Quickshell.Wayland
import QtQuick
ShellRoot {
    PanelWindow {
        anchors { top: true; left: true }
        implicitWidth: 48; implicitHeight: 48
        color: "#40000000"
        exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive
        WlrLayershell.namespace: "flow-appmenu-coverage"
        FocusScope { anchors.fill: parent; focus: true }
    }
}
"""
# The login session's environment on the test guest when it was measured: its booted generation predates the
# Flow module's at-spi2-core, and the NixOS module then exports these, switching GTK's and the atk bridge's
# accessibility off for every app the session starts.
LOGIN_ENV = {"NO_AT_BRIDGE": "1", "GTK_A11Y": "none"}


def firefox_profile(tmp):
    profile = Path(tmp) / "ff"
    profile.mkdir(exist_ok=True)
    (profile / "user.js").write_text(FIREFOX_PREFS)
    return str(profile)


def firefox_window(url):
    return lambda root, tmp: ["firefox", "--profile", str(Path(tmp) / "ff"), "--new-window", url]


def libreoffice_profile(tmp):
    user = Path(tmp) / "lo" / "user"
    user.mkdir(parents=True, exist_ok=True)
    (user / "registrymodifications.xcu").write_text(LIBREOFFICE_PREFS)
    return "file://" + str(Path(tmp) / "lo")


def chromium(a11y=False):
    return lambda root, tmp: [f"{root}/bin/chromium", f"--user-data-dir={tmp}/cr", "--ozone-platform=wayland",
                              "--no-first-run", "--no-default-browser-check", "--password-store=basic",
                              *(["--force-renderer-accessibility"] if a11y else []), "about:blank"]


def vscodium(native=False, a11y=False):
    """VSCodium; its own (HTML) title bar and menu by default, or Electron's native menu bar."""
    def argv(root, tmp):
        user = Path(tmp) / "vsc" / "User"
        user.mkdir(parents=True, exist_ok=True)
        settings = {"workbench.startupEditor": "none", "update.mode": "none", "telemetry.telemetryLevel": "off"}
        if native:
            settings.update({"window.titleBarStyle": "native", "window.menuBarVisibility": "classic"})
        (user / "settings.json").write_text(json.dumps(settings))
        return [f"{root}/bin/codium", "--new-window", "--disable-workspace-trust", "--skip-welcome",
                "--skip-release-notes", f"--user-data-dir={tmp}/vsc", f"--extensions-dir={tmp}/vsx",
                "--ozone-platform=wayland", *(["--force-renderer-accessibility"] if a11y else [])]
    return argv


# command: (top-level menu, item), regular expressions matched against the reader's labels; its effect must be
# a new window of the app's process. None where the app has no menu bar. attr: nixpkgs attribute, or None for the
# command on PATH (the system's, as Flow installs it). x11: the app's windows are X11 (app_id identifies them).
# env: extra environment for the app.
APPS = [
    dict(key="kate", name="Kate", toolkit="Qt 6 / KF6", attr="kdePackages.kate",
         argv=lambda root, tmp: [f"{root}/bin/kate", "-n"], command=("^Help$", "^About Kate")),
    dict(key="sqlitebrowser", name="DB Browser for SQLite", toolkit="Qt 5", attr="sqlitebrowser",
         argv=lambda root, tmp: [f"{root}/bin/sqlitebrowser"], command=("^Help$", "^About")),
    dict(key="vlc", name="VLC", toolkit="Qt 5 on X11 (xwayland-satellite)", attr="vlc", x11="vlc",
         argv=lambda root, tmp: [f"{root}/bin/vlc", "-I", "qt", "--no-qt-privacy-ask"], command=("^Help$", "^About")),
    dict(key="writer", name="LibreOffice Writer", toolkit="VCL, gtk3 plugin", attr="libreoffice",
         argv=lambda root, tmp: [f"{root}/bin/libreoffice", "--writer", "--norestore", "--nologo",
                                 f"-env:UserInstallation={libreoffice_profile(tmp)}"],
         command=("^Help$", "^About LibreOffice")),
    dict(key="mousepad", name="Mousepad", toolkit="GTK 3", attr="mousepad",
         argv=lambda root, tmp: [f"{root}/bin/mousepad"], command=("^Help$", "^About")),
    dict(key="mousepad-login-env", name="Mousepad, login session's environment", toolkit="GTK 3",
         attr="mousepad", argv=lambda root, tmp: [f"{root}/bin/mousepad"], command=("^Help$", "^About"),
         env=LOGIN_ENV),
    dict(key="nautilus", name="Nautilus", toolkit="GTK 4 / libadwaita", attr=None,
         argv=lambda root, tmp: ["nautilus", "--new-window"], command=None, private_bus=True),
    dict(key="text-editor", name="GNOME Text Editor", toolkit="GTK 4 / libadwaita", attr="gnome-text-editor",
         argv=lambda root, tmp: [f"{root}/bin/gnome-text-editor", "--standalone", "--new-window"], command=None),
    dict(key="firefox", name="Firefox", toolkit="Gecko, GTK 3", attr=None,
         argv=lambda root, tmp: ["firefox", "--new-instance", "--profile", firefox_profile(tmp), "about:blank"],
         command=("^Help$", "^About ")),
    dict(key="chromium", name="Chromium", toolkit="Chromium views", attr="chromium", argv=chromium(), command=None),
    dict(key="chromium-a11y", name="Chromium --force-renderer-accessibility", toolkit="Chromium views",
         attr="chromium", argv=chromium(True), command=None),
    dict(key="vscodium", name="VSCodium, own title bar (default)", toolkit="Electron 43", attr="vscodium",
         argv=vscodium(), command=("^Help$", "^About")),
    dict(key="vscodium-a11y", name="VSCodium, own title bar, --force-renderer-accessibility",
         toolkit="Electron 43", attr="vscodium", argv=vscodium(a11y=True), command=("^Help$", "^About")),
    dict(key="vscodium-native", name="VSCodium, native title bar", toolkit="Electron 43 native menu bar",
         attr="vscodium", argv=vscodium(native=True), command=("^Help$", "^About")),
    dict(key="vscodium-native-a11y", name="VSCodium, native title bar, --force-renderer-accessibility",
         toolkit="Electron 43 native menu bar", attr="vscodium", argv=vscodium(native=True, a11y=True),
         command=("^Help$", "^About")),
    dict(key="foot", name="foot", toolkit="none", attr=None, argv=lambda root, tmp: ["foot"], command=None),
]

# More windows in the same process, opened one after another: an argv, or ("menu", top, item) invoked through
# AT-SPI in the first window. Each list gives two windows of one title, and Nautilus and Firefox one more of
# its own title.
MULTI = {
    "kate": [("menu", "^File$", "^New Window$")],
    "firefox": [firefox_window("about:robots"), firefox_window("about:blank")],
    "nautilus": [lambda root, tmp: ["nautilus", "--new-window", tmp], lambda root, tmp: ["nautilus", "--new-window"]],
}
# A window-scoped command invoked through the chosen frame's own menu bar: its window's title changes.
GROUND_TRUTH = {"kate": ("^File$", "^New$")}

STATE = {"run": uuid.uuid4().hex, "a11y": None, "a11y_set": None, "procs": [], "sids": set(), "app_pids": set(), "windows": set(),
         "workspace": None, "run_workspace": None, "roots": [], "bus": None, "bus_proc": None, "notes": []}


# --- Niri, the session bus and the accessibility switch ------------------------------------------------------

def niri(what):
    return json.loads(subprocess.check_output(["niri", "msg", "-j", what], text=True))


def niri_action(*args):
    subprocess.run(["niri", "msg", "action", *args], check=True, stdout=subprocess.DEVNULL)


def windows():
    return {w["id"]: w for w in niri("windows")}


def note(text):
    STATE["notes"].append(text)
    print(f"note: {text}", file=sys.stderr, flush=True)


def bus_env(env=None):
    """``env`` (default: this process's), on the private session bus while one is up."""
    env = dict(os.environ if env is None else env)
    if STATE["bus"]:
        env["DBUS_SESSION_BUS_ADDRESS"] = STATE["bus"]
    return env


A11Y = ("org.a11y.Bus", "/org/a11y/bus", "org.a11y.Status", "IsEnabled")


def a11y_enabled():
    out = subprocess.check_output(["busctl", "--user", "get-property", *A11Y], text=True, env=bus_env())
    return out.strip() == "b true"


def set_a11y(on):
    """Sets the switch, on the shared bus only while it still holds what this run last set (False: someone else
    changed it, and it is left alone)."""
    shared = not STATE["bus"]
    if shared and STATE["a11y_set"] is not None and a11y_enabled() != STATE["a11y_set"]:
        note(f"IsEnabled changed by someone else (expected {STATE['a11y_set']}); left alone")
        STATE["a11y_set"] = None
        return False
    subprocess.run(["busctl", "--user", "set-property", *A11Y, "b", "true" if on else "false"], check=True,
                   env=bus_env())
    if shared:
        STATE["a11y_set"] = on
        save_state()
    return True


def start_private_bus(tmp):
    """A session bus of the run's own. The accessibility bus it starts would bind $XDG_RUNTIME_DIR/at-spi/bus,
    which every private session bus's accessibility bus shares, so the bus gets a runtime directory of its own.
    Fails closed: without the bus the app would join the session's own instance."""
    runtime = Path(tmp) / "bus-runtime"
    runtime.mkdir(mode=0o700)
    env = {k: v for k, v in app_env(tmp).items() if k != "DBUS_SESSION_BUS_ADDRESS"}
    env["XDG_RUNTIME_DIR"] = str(runtime)
    proc = subprocess.Popen(["dbus-daemon", "--session", "--nofork", "--print-address=1"], env=env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True, start_new_session=True)
    STATE["bus_proc"] = proc
    address = proc.stdout.readline().strip()
    if not address.startswith("unix:"):
        end(proc)
        STATE["bus_proc"] = None
        raise RuntimeError("the private session bus did not start")
    STATE["bus"] = address
    save_state()


def environ_of(pid, proc_root=None):
    try:
        return set(((proc_root or PROC) / str(pid) / "environ").read_bytes().split(b"\0"))
    except OSError:  # gone, or another user's
        return set()


def processes_with(entries, proc_root=None):
    """PIDs of this user's processes whose environment holds one of ``entries`` ("NAME=value")."""
    wanted = {e.encode() for e in entries}
    return [int(e.name) for e in (proc_root or PROC).iterdir()
            if e.name.isdigit() and environ_of(e.name, proc_root) & wanted]


def bus_clients(address, proc_root=None):
    """PIDs of this user's processes whose environment names ``address`` as their session or starter bus."""
    return processes_with([f"DBUS_SESSION_BUS_ADDRESS={address}", f"DBUS_STARTER_ADDRESS={address}"], proc_root)


def own_processes(proc_root=None):
    return processes_with([f"{MARKER}={STATE['run']}"], proc_root)


def stop_own_processes():
    """Every process the run started that is still alive, detached ones too: SIGTERM, then SIGKILL."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        pids = [p for p in own_processes() if p != os.getpid()]
        for pid in pids:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
        if pids and sig == signal.SIGTERM:
            time.sleep(3)


def end(proc):
    proc.terminate()
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(5)


def stop_private_bus():
    """Stops every process on the private bus (the app and what the bus started for it), then the bus."""
    address, proc = STATE["bus"], STATE["bus_proc"]
    STATE["bus"] = STATE["bus_proc"] = None
    if proc is None:
        return
    for _ in range(2):
        if address:
            for pid in bus_clients(address):
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            time.sleep(1)
    end(proc)
    save_state()


def state_file():
    return Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "flow-appmenu-coverage.json"


def save_state():
    proc = STATE["bus_proc"]
    state_file().write_text(json.dumps({
        "run": STATE["run"], "a11y": STATE["a11y"], "a11y_set": STATE["a11y_set"], "sids": sorted(STATE["sids"]),
        "windows": sorted(STATE["windows"]), "bus_pid": proc.pid if proc else None, "bus": STATE["bus"]}))


def cleanup():
    """Undoes a run that was killed: closes its windows, stops its processes and private bus, puts IsEnabled
    back if it still holds what the run set."""
    try:
        saved = json.loads(state_file().read_text())
    except FileNotFoundError:
        print("no state file: nothing to clean up")
        return
    STATE["sids"], STATE["run"] = set(saved["sids"]), saved["run"]
    current = windows()
    for wid, w in current.items():  # Niri never reuses a window id, so saved ones are still the run's own
        if wid in saved["windows"] or owned(w.get("pid")):
            close_window(wid, wait=3)
    stop_own_processes()
    if saved["bus"]:
        for pid in bus_clients(saved["bus"]):
            os.kill(pid, signal.SIGTERM)
    if saved["bus_pid"]:
        try:
            os.kill(saved["bus_pid"], signal.SIGTERM)
        except ProcessLookupError:
            pass
    if saved["a11y"] is not None and saved["a11y_set"] is not None and a11y_enabled() == saved["a11y_set"]:
        subprocess.run(["busctl", "--user", "set-property", *A11Y, "b", "true" if saved["a11y"] else "false"], check=True)
    state_file().unlink()
    print("cleaned up")


def restore():
    for wid in list(STATE["windows"]):
        close_window(wid, wait=0)
    for proc in STATE["procs"]:
        stop(proc)
    stop_private_bus()
    stop_own_processes()
    if STATE["a11y"] is not None and STATE["a11y_set"] is not None:
        if a11y_enabled() == STATE["a11y_set"]:
            subprocess.run(["busctl", "--user", "set-property", *A11Y, "b", "true" if STATE["a11y"] else "false"],
                           check=True)
        else:
            note("IsEnabled changed by someone else during the run; left alone")
    if STATE["workspace"] is not None:
        try:  # only if focus is still where the run put it
            focused = next((w for w in niri("workspaces") if w["is_focused"]), {})
            if focused.get("id") == STATE["run_workspace"]:
                niri_action("focus-workspace", str(STATE["workspace"]))
        except subprocess.CalledProcessError:
            pass
    try:
        state_file().unlink()
    except FileNotFoundError:
        pass


def on_signal(signum, _frame):
    restore()
    sys.exit(128 + signum)


# --- Apps and their windows -----------------------------------------------------------------------------------

def store_path(attr):
    nix = shlex.split(os.environ.get("FLOW_NIX", "nix"))
    extra = shlex.split(os.environ.get("FLOW_NIX_ARGS", ""))
    ref = f"{os.environ.get('FLOW_NIXPKGS', 'nixpkgs')}#{attr}^out"
    return subprocess.check_output([*nix, "build", "--no-link", "--print-out-paths", *extra, ref], text=True).split()[0]


def app_env(tmp, extra=None):
    env = bus_env()
    for name in ("CONFIG", "DATA", "CACHE", "STATE"):
        path = Path(tmp) / name.lower()
        path.mkdir(exist_ok=True)
        env[f"XDG_{name}_HOME"] = str(path)
    env["GSETTINGS_BACKEND"] = "memory"  # GTK apps save window state on close
    if not env.get("WAYLAND_DISPLAY", "wayland-0").startswith("/"):  # still found beside a private runtime dir
        env["WAYLAND_DISPLAY"] = str(Path(os.environ["XDG_RUNTIME_DIR"]) / env.get("WAYLAND_DISPLAY", "wayland-0"))
    env.pop("GI_TYPELIB_PATH", None)  # the runner's, for pyatspi; an app's own wrapper sets its own
    env[MARKER] = STATE["run"]
    env.update(extra or {})
    return env


def spawn(argv, tmp, extra_env=None):
    proc = subprocess.Popen(argv, env=app_env(tmp, extra_env), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)
    STATE["procs"].append(proc)
    STATE["sids"].add(proc.pid)
    save_state()
    return proc


def stop(proc):
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def session_of(pid, proc_root=None):
    try:
        stat = ((proc_root or PROC) / str(pid) / "stat").read_text()
    except OSError:
        return None
    return int(stat.rsplit(")", 1)[1].split()[3])  # fields after "(comm)": state ppid pgrp session


def owned(pid):
    """Whether ``pid`` is a process the run started (it carries the run's marker, or is in one of its sessions),
    or an app process its windows joined."""
    return bool(pid) and (pid in STATE["app_pids"] or session_of(pid) in STATE["sids"]
                          or f"{MARKER}={STATE['run']}".encode() in environ_of(pid))


def is_own_window(window, x11_app_id=None):
    if x11_app_id:  # an X11 window carries xwayland-satellite's PID
        return window.get("app_id") == x11_app_id
    return owned(window.get("pid"))


def wait_new_window(known, x11_app_id=None, timeout=90, settle=3.0):
    """The run's own windows not in ``known``, once a first one appeared and no further one for ``settle`` s.
    Other windows that appear meanwhile (another session's) are ignored, never closed."""
    deadline = time.monotonic() + timeout
    seen, since = [], None
    while time.monotonic() < deadline:
        new = [w for wid, w in windows().items() if wid not in known and is_own_window(w, x11_app_id)]
        if len(new) != len(seen):
            seen, since = new, time.monotonic()
        if seen and time.monotonic() - since >= settle:
            break
        time.sleep(0.25)
    STATE["windows"].update(w["id"] for w in seen)
    if not x11_app_id:
        STATE["app_pids"].update(w["pid"] for w in seen if w.get("pid"))
    save_state()
    return sorted(seen, key=lambda w: w["id"])


def close_window(wid, wait=10):
    STATE["windows"].discard(wid)
    try:
        niri_action("close-window", "--id", str(wid))
    except subprocess.CalledProcessError:
        return
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline and wid in windows():
        time.sleep(0.25)


def focus(wid):
    niri_action("focus-window", "--id", str(wid))
    time.sleep(0.8)


@contextmanager
def phase(app):
    """A temporary directory for one launch of ``app``, and its private session bus if it needs one."""
    with tempfile.TemporaryDirectory(prefix=f"flow-appmenu-{app['key']}-", ignore_cleanup_errors=True) as tmp:
        try:
            if app.get("private_bus"):
                start_private_bus(tmp)
            yield tmp
        finally:
            for wid, w in windows().items():  # also any the waiting missed
                if wid in STATE["windows"] or (not app.get("x11") and owned(w.get("pid"))):
                    close_window(wid, wait=5)
            for wid in list(STATE["windows"]):
                close_window(wid, wait=5)
            for proc in STATE["procs"]:
                stop(proc)
            STATE["procs"].clear()
            STATE["app_pids"].clear()
            stop_private_bus()
            stop_own_processes()
            time.sleep(1)  # before the directory goes: processes that were writing to it have exited


@contextmanager
def exclusive_layer(tmp):
    """An exclusive-keyboard overlay surface, up for the block's duration."""
    qml = Path(tmp) / "layer.qml"
    qml.write_text(LAYER_QML)
    before = {l.get("namespace") for l in niri("layers")}
    proc = spawn(["quickshell", "-p", str(qml)], tmp)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not any(l.get("namespace") == "flow-appmenu-coverage" for l in niri("layers")):
        time.sleep(0.25)
    up = "flow-appmenu-coverage" not in before and any(
        l.get("namespace") == "flow-appmenu-coverage" and l.get("keyboard_interactivity") == "Exclusive"
        for l in niri("layers"))
    time.sleep(1)
    try:
        yield up
    finally:
        stop(proc)
        STATE["procs"].remove(proc)
        time.sleep(0.5)


# --- The reader, and the probe, each in a fresh process ------------------------------------------------------

def run(argv, env, timeout=120):
    start = time.monotonic()
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)
    took = time.monotonic() - start
    try:
        data = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        data = {"error": (proc.stderr or proc.stdout).strip()[-300:]}
    return data, took, proc.returncode


def reader(*args, fixed=False):
    """session/appmenu.py with ``args``, the test apps' desktop files and programs findable as an installed
    app's are (GIO finds no desktop file whose Exec program is not on PATH). ``fixed``: with app_name()'s
    TypeError caught."""
    env = bus_env()
    for var, sub in (("XDG_DATA_DIRS", "share"), ("PATH", "bin")):
        env[var] = ":".join(filter(None, [*(f"{r}/{sub}" for r in STATE["roots"]), env.get(var, "")]))
    argv = [sys.executable, __file__, "reader-fixed", *args] if fixed else [sys.executable, str(READER), *args]
    return run(argv, env, timeout=60)


def load_reader():
    spec = importlib.util.spec_from_file_location("appmenu", READER)
    appmenu = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(appmenu)
    return appmenu


def reader_fixed_main(args):
    appmenu = load_reader()
    original = appmenu.app_name

    def app_name(app_id, title):
        try:
            return original(app_id, title)
        except TypeError:  # GioUnix.DesktopAppInfo.new: "constructor returned NULL"
            return app_id or title or "Application"
    appmenu.app_name = app_name
    sys.argv = ["flow-appmenu", *args]
    try:
        appmenu.main()
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)


def probe(*args):
    return run([sys.executable, __file__, "probe", *map(str, args)], bus_env())[0]


def leaves(items, prefix=()):
    for item in items:
        if "children" in item:
            yield from leaves(item["children"], prefix + (item["label"],))
        else:
            yield prefix + (item["label"],), item


def count(result):
    return sum(1 for _ in leaves(result.get("menus", [])))


def find_command(menus, command):
    top, item = (re.compile(p, re.I) for p in command)
    for labels, leaf in leaves(menus):
        if top.search(labels[0].strip()) and item.search(labels[-1].strip()) and leaf.get("enabled", True):
            return labels, leaf
    return None, None


def accelerator(binding):
    """The shortcut in an AT-SPI key binding. GTK gives "mnemonic;path;accelerator". Qt gives the shortcut
    alone (which may itself contain ";"), or, for an item without one, its mnemonic ("Alt+N"), not a shortcut."""
    if not binding:
        return ""
    parts = binding.split(";")
    if len(parts) == 3 and not binding.startswith(("Ctrl+", "Alt+", "Shift+", "Meta+")):
        return parts[2].strip()
    return "" if re.fullmatch(r"Alt\+\S", binding.strip()) else binding.strip()


def narrowing(frames, title):
    """Which frames each rule picks for a focused window titled ``title``: by name, by active state, and both."""
    by_title = [i for i, f in enumerate(frames) if f["name"] == title]
    by_active = [i for i, f in enumerate(frames) if f["active"]]
    both = [i for i in by_title if i in by_active]
    return {"by_title": by_title, "by_active": by_active, "both": both, "resolved": len(both) == 1}


# --- One app -----------------------------------------------------------------------------------------------

def measure(app, root, tmp, late):
    row = {"key": app["key"], "late": late}
    known = set(windows())
    if not set_a11y(not late):
        row["error"] = "skipped: IsEnabled changed by someone else"
        return row
    time.sleep(0.5)
    proc = spawn(app["argv"](root, tmp), tmp, app.get("env"))
    new = wait_new_window(known, app.get("x11"))
    if not new:
        row["error"] = "no window"
        return row
    win = new[0]
    row.update(window=win["id"], title=win["title"], app_id=win.get("app_id"), pid=win.get("pid"),
               spawned_pid=proc.pid, extra_windows=[w["title"] for w in new[1:]])
    focus(win["id"])
    if late:
        first, took, _ = reader(fixed=True)
        row["late_first"] = {"items": count(first), "s": round(took, 2), "error": first.get("error")}
        time.sleep(3)
        second, took, _ = reader(fixed=True)
        row["late_second"] = {"items": count(second), "s": round(took, 2), "error": second.get("error")}
        if not STATE["bus"] and STATE["a11y_set"] is not None:
            STATE["a11y_set"] = True  # the reader switched it on, as flow-appmenu does
        return row
    row["pid_windows"] = sum(w.get("pid") == win.get("pid") for w in windows().values())
    for name, fixed in (("reader", False), ("fixed", True)):
        runs = [reader(fixed=fixed) for _ in range(3)]
        result = runs[-1][0]
        row[name] = {"items": count(result), "top": [m["label"] for m in result.get("menus", [])],
                     "median_s": round(statistics.median(took for _, took, _ in runs), 2), "error": result.get("error")}
    row["probe"] = probe(win.get("pid"))
    if app.get("x11") or not row["probe"].get("on_bus"):
        row["probe_spawned"] = probe(proc.pid)  # the process the run started, if the window's PID is another's
    row["command"] = invoke(app, result.get("menus", []), win)
    return row


def invoke(app, menus, win):
    """The app's harmless command through the (fixed) reader's activate path; the new window of the app's
    process it opens is closed again."""
    if not app.get("command"):
        return None
    labels, leaf = find_command(menus, app["command"])
    if not leaf:
        return {"found": False}
    focus(win["id"])
    known = set(windows())
    start = time.monotonic()
    result, _, code = reader("activate", str(win["id"]), json.dumps(leaf["path"]), leaf["label"], fixed=True)
    took = time.monotonic() - start

    def opened():
        return [w for wid, w in windows().items() if wid not in known and w.get("pid") == win.get("pid")
                and (not app.get("x11") or w.get("app_id") == app["x11"])]
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and not opened():
        time.sleep(0.25)
    time.sleep(1)
    dialogs = opened()
    for w in dialogs:
        STATE["windows"].add(w["id"])
        close_window(w["id"])
    return {"found": True, "labels": [label.strip() for label in labels], "ok": code == 0, "s": round(took, 2),
            "error": result.get("error"), "opened": [w["title"] for w in dialogs]}


def narrow_case(pid, win):
    current = windows()
    title = current[win["id"]]["title"]
    frames = probe(pid, "--frames").get("frames", [])
    rules = narrowing(frames, title)
    case = {"title": title, "pid_windows": sum(w.get("pid") == pid for w in current.values()),
            "same_title_windows": sum(w.get("pid") == pid and w["title"] == title for w in current.values()),
            "frames": frames, **rules}
    if rules["resolved"]:
        case["frame_items"] = frames[rules["both"][0]].get("items")
        case["frame_ms"] = frames[rules["both"][0]].get("ms")
    return case


def ground_truth(app, pid, win, case, mine):
    """Invokes GROUND_TRUTH through the frame ``case`` chose; correct when only the focused window's title changed."""
    if not case["resolved"]:
        return {"invoked": False, "error": "no frame chosen", "changed": [], "correct": False}
    before = {w["id"]: windows()[w["id"]]["title"] for w in mine}
    result = probe(pid, "--invoke", case["both"][0], *GROUND_TRUTH[app["key"]])
    time.sleep(1.5)
    after = {w["id"]: windows()[w["id"]]["title"] for w in mine}
    changed = [wid for wid in before if before[wid] != after[wid]]
    return {"invoked": result.get("invoked"), "error": result.get("error"), "changed": changed,
            "correct": changed == [win["id"]]}


def multi(app, root, tmp):
    """Several windows of one process; with each focused, which accessible frame the title and active rules pick,
    without and with an exclusive-keyboard layer over it."""
    row = {"key": app["key"], "cases": [], "layer_cases": []}
    if not set_a11y(True):
        row["error"] = "skipped: IsEnabled changed by someone else"
        return row
    known = set(windows())
    spawn(app["argv"](root, tmp), tmp)
    first = wait_new_window(known)
    if not first:
        row["error"] = "no first window"
        return row
    pid = first[0].get("pid")
    mine = [first[0]]
    for opener in MULTI[app["key"]]:
        focus(mine[0]["id"])
        known = set(windows())
        if isinstance(opener, tuple):
            _, leaf = find_command(reader(fixed=True)[0].get("menus", []), opener[1:])
            if leaf:
                reader("activate", str(mine[0]["id"]), json.dumps(leaf["path"]), leaf["label"], fixed=True)
        else:
            spawn(opener(root, tmp), tmp)
        mine += [w for w in wait_new_window(known) if w.get("pid") == pid][:1]
    if len(mine) < 2:
        row["error"] = "no second window in the same process"
    for win in mine:
        focus(win["id"])
        case = narrow_case(pid, win)
        case["reader_items"] = count(reader(fixed=True)[0])
        row["cases"].append(case)
    for win in mine:
        focus(win["id"])
        with exclusive_layer(tmp) as up:
            case = narrow_case(pid, win)
            case["layer_up"] = up
        row["layer_cases"].append(case)
    if app["key"] in GROUND_TRUTH:  # last: the command changes the windows' titles
        row["truth"] = []
        for win in mine:
            focus(win["id"])
            chosen = narrow_case(pid, win)
            row["truth"].append({"window": win["id"], "title": chosen["title"], "mode": "focused",
                                 **ground_truth(app, pid, win, chosen, mine)})
        for win in mine:
            focus(win["id"])
            chosen = narrow_case(pid, win)  # chosen before the layer maps, as a reader could when the menu opens
            with exclusive_layer(tmp) as up:
                row["truth"].append({"window": win["id"], "title": chosen["title"], "mode": "frame chosen, then layer",
                                     "layer_up": up, **ground_truth(app, pid, win, chosen, mine)})
    return row


# --- The walk, and the probe: runs in its own process, with pyatspi ------------------------------------------

def menu_walk(node, depth, menu, st, capped, found_leaves, at):
    """session/appmenu.py's walk (active_window's ``walk``), with counters in ``st``: nodes visited, deepest
    level, menu bars, and menus dropped for having no items. ``capped`` applies its limits; ``at`` is pyatspi."""
    st["nodes"] += 1
    if node is None or (capped and (st["nodes"] > CAP_NODES or depth > CAP_DEPTH)):
        return []
    st["depth"] = max(st["depth"], depth)
    role = node.getRole()
    bar = role == at.ROLE_MENU_BAR
    st["bars"] += bar
    if menu and role in (at.ROLE_MENU, at.ROLE_MENU_ITEM, at.ROLE_CHECK_MENU_ITEM, at.ROLE_RADIO_MENU_ITEM):
        children = []
        for i in range(node.childCount):
            children.extend(menu_walk(node.getChildAtIndex(i), depth + 1, True, st, capped, found_leaves, at))
        if children:
            return [{"label": node.name or "", "children": children}]
        if role != at.ROLE_MENU and not node.childCount:
            found_leaves.append(node)
            return [{"label": node.name or ""}]
        st["empty_menus"] += role == at.ROLE_MENU
        return []
    found = []
    for i in range(node.childCount):
        found.extend(menu_walk(node.getChildAtIndex(i), depth + 1, menu or bar, st, capped, found_leaves, at))
    return found


def timed_walk(root, capped, at, depth=0):
    st, found_leaves = {"nodes": 0, "depth": 0, "bars": 0, "empty_menus": 0}, []
    start = time.monotonic()
    menus = menu_walk(root, depth, False, st, capped, found_leaves, at)
    st["ms"] = round((time.monotonic() - start) * 1000)
    st["items"] = len(found_leaves)
    st["top"] = [m["label"] for m in menus]
    return st, found_leaves, menus


def menu_bars(app, at):
    """Every menu bar in ``app``: its frame, whether it is showing and visible, and its items."""
    bars = []

    def visit(node, frame, depth):
        if node is None or depth > 40:
            return
        role = node.getRole()
        if role in (at.ROLE_FRAME, at.ROLE_WINDOW, at.ROLE_DIALOG):
            frame = node.name or ""
        if role == at.ROLE_MENU_BAR:
            st, _, menus = timed_walk(node, False, at)
            state = node.getState()
            bars.append({"frame": frame, "showing": state.contains(at.STATE_SHOWING),
                         "visible": state.contains(at.STATE_VISIBLE), "items": st["items"], "top": len(menus)})
            return
        for i in range(node.childCount):
            visit(node.getChildAtIndex(i), frame, depth + 1)
    visit(app, "", 0)
    return bars


def find_app(at, pid):
    desktop = at.Registry.getDesktop(0)
    for _ in range(40):  # an app registers on the bus a moment after its window maps
        for i in range(desktop.childCount):
            child = desktop.getChildAtIndex(i)
            if child is not None and (child.name or "").lower() not in ("quickshell", "niri") and child.get_process_id() == pid:
                return child
        time.sleep(0.25)
    return None


def frames_of(app, at):
    return [c for c in (app.getChildAtIndex(i) for i in range(app.childCount))
            if c is not None and c.getRole() in (at.ROLE_FRAME, at.ROLE_WINDOW, at.ROLE_DIALOG)]


def probe_main(pid, args):
    import pyatspi as at
    app = find_app(at, pid)
    if app is None:
        return {"on_bus": False}
    if "--frames" in args:
        frames = []
        for frame in frames_of(app, at):
            st, _, _ = timed_walk(frame, True, at, depth=1)  # a frame is at depth 1 in the reader's walk
            frames.append({"name": frame.name or "", "role": frame.getRoleName(),
                           "active": frame.getState().contains(at.STATE_ACTIVE),
                           "items": st["items"], "ms": st["ms"], "bars": st["bars"]})
        return {"on_bus": True, "frames": frames}
    if "--invoke" in args:
        index, top, item = args[args.index("--invoke") + 1:][:3]
        frame = frames_of(app, at)[int(index)]
        _, found_leaves, menus = timed_walk(frame, True, at, depth=1)
        top_re, item_re = re.compile(top), re.compile(item)
        target = next((node for node in found_leaves if item_re.search((node.name or "").strip())
                       and _top_label(node, at) and top_re.search(_top_label(node, at))), None)
        if target is None:
            return {"invoked": False, "error": "item not found in the frame"}
        return {"invoked": bool(target.queryAction().doAction(0))}
    capped, _, _ = timed_walk(app, True, at)
    full, found_leaves, _ = timed_walk(app, False, at)
    shortcuts = []
    for node in found_leaves:
        try:
            key = accelerator(node.queryAction().getKeyBinding(0))
        except Exception:  # no Action interface
            key = ""
        if key:
            shortcuts.append(f"{(node.name or '').strip()}={key}")
    return {"on_bus": True, "app_name": app.name, "capped": capped, "full": full, "bars": menu_bars(app, at),
            "shortcuts": len(shortcuts), "shortcut_examples": shortcuts[:4]}


def _top_label(node, at):
    """The label of the top-level menu ``node`` sits in: the menu whose parent is a menu bar."""
    while node is not None:
        parent = node.parent
        if parent is not None and parent.getRole() == at.ROLE_MENU_BAR:
            return (node.name or "").strip()
        node = parent
    return ""


def dump(app, root, tmp):
    set_a11y(True)
    known = set(windows())
    spawn(app["argv"](root, tmp), tmp, app.get("env"))
    new = wait_new_window(known, app.get("x11"))
    if new:
        focus(new[0]["id"])
        result = reader(fixed=True)[0]
        for labels, leaf in leaves(result.get("menus", [])):
            print(" > ".join(labels), "" if leaf.get("enabled") else "(disabled)")
        print(json.dumps({k: v for k, v in result.items() if k != "menus"}))


# --- Report ------------------------------------------------------------------------------------------------

def yes(flag):
    return "yes" if flag else "no"


def command_text(cmd):
    if cmd is None:
        return "no menu"
    if not cmd["found"]:
        return "no item"
    shown = f"opened {cmd['opened'][0]!r}" if cmd["opened"] else "no window"
    if cmd["ok"]:
        return f"{' > '.join(cmd['labels'])}: ran, {shown}"
    return f"{' > '.join(cmd['labels'])}: error after {cmd['s']} s ({(cmd['error'] or '')[:40]}), {shown}"


def table(data):
    lines = ["| App | Toolkit | On a11y bus | Menu bars (showing) | Reader items, as is | Items, app_name fixed | "
             "Nodes visited, capped (uncapped) | Empty menus | Walk ms | Reader s | Shortcut labels | "
             "Command via doAction | Windows of PID | Late enable, 1st / 2nd call |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    late = {r["key"]: r for r in data["late"]}
    for r in data["rows"]:
        app = next(a for a in APPS if a["key"] == r["key"])
        if "error" in r:
            lines.append(f"| {app['name']} | {app['toolkit']} | {r['error']} |" + " |" * 11)
            continue
        p, rd, fx = r.get("probe", {}), r["reader"], r["fixed"]
        full, capped = p.get("full", {}), p.get("capped", {})
        on_bus = yes(p.get("on_bus"))
        if not p.get("on_bus") and "probe_spawned" in r:
            on_bus = f"no (window PID {r['pid']}); own PID {r['spawned_pid']}: {yes(r['probe_spawned'].get('on_bus'))}"
        bars = p.get("bars", [])
        bar_text = f"{len(bars)} ({sum(b['showing'] for b in bars)})" if p.get("on_bus") else "—"
        cut = f" (uncapped {full.get('items')})" if p.get("on_bus") and capped.get("items") != full.get("items") else ""
        timed = rd if not rd["error"] else fx
        lr = late.get(r["key"], {})
        late_text = (f"{lr['late_first']['items']} / {lr['late_second']['items']}" if "late_first" in lr
                     else lr.get("error", ""))
        has_items = p.get("on_bus") and full.get("items")
        lines.append(" | ".join([
            f"| {app['name']}", app["toolkit"], on_bus, bar_text, "error" if rd["error"] else str(rd["items"]),
            f"{fx['items']}{cut}", f"{capped['nodes']} ({full['nodes']})" if p.get("on_bus") else "—",
            str(capped.get("empty_menus", "—")) if p.get("on_bus") else "—",
            str(capped.get("ms", "—")) if p.get("on_bus") else "—",
            f"{timed['median_s']}{'*' if rd['error'] else ''}",
            f"{p['shortcuts']} of {full['items']}" if has_items else "—",
            command_text(r.get("command")), str(r["pid_windows"]), f"{late_text} |"]))
    out = "\n".join(lines)
    for kind, title in (("cases", "Focused"), ("layer_cases", "Focused, exclusive-keyboard layer up")):
        if not any(m.get(kind) for m in data["multi"]):
            continue
        out += (f"\n\n{title}:\n\n| App | Focused window | Windows of PID | With this title | Frames | Match title | "
                "Active | Both | Resolved | Items in that frame | Frame walk ms |\n")
        out += "|---|---|---|---|---|---|---|---|---|---|---|\n"
        for m in data["multi"]:
            for c in m.get(kind, []):
                layer = "" if kind == "cases" or c.get("layer_up") else " (layer not up)"
                out += (f"| {m['key']} | {c['title']}{layer} | {c['pid_windows']} | {c['same_title_windows']} | "
                        f"{len(c['frames'])} | {len(c['by_title'])} | {len(c['by_active'])} | {len(c['both'])} | "
                        f"{yes(c['resolved'])} | {c.get('frame_items', '—')} | {c.get('frame_ms', '—')} |\n")
            if m.get("error"):
                out += f"| {m['key']} | {m['error']} |" + " |" * 10 + "\n"
    truths = [(m["key"], t) for m in data["multi"] for t in m.get("truth", [])]
    if truths:
        out += ("\n\nGround truth (File > New through the chosen frame's menu):\n\n"
                "| App | Window | How the frame was chosen | Invoked | Windows whose title changed | Correct |\n"
                "|---|---|---|---|---|---|\n")
        for key, t in truths:
            mode = t["mode"] + ("" if t.get("layer_up", True) else " (layer not up)")
            out += (f"| {key} | {t['window']} ({t['title']}) | {mode} | {yes(t['invoked'])}{' ' + t['error'] if t.get('error') else ''} | "
                    f"{t['changed']} | {yes(t['correct'])} |\n")
    if data.get("notes"):
        out += "\n\nNotes:\n" + "\n".join(f"- {n}" for n in data["notes"])
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only")
    parser.add_argument("--skip-multi", action="store_true")
    parser.add_argument("--skip-late", action="store_true")
    parser.add_argument("--dump")
    parser.add_argument("--json")
    parser.add_argument("--table", help="print the table of an earlier run's JSON")
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()
    if args.table:
        print(table(json.loads(Path(args.table).read_text())))
        return
    if args.cleanup:
        cleanup()
        return
    if state_file().exists():
        sys.exit(f"{state_file()} exists: a run is going, or one was killed (then run with --cleanup)")
    keys = [args.dump] if args.dump else args.only.split(",") if args.only else [a["key"] for a in APPS]
    apps = [a for a in APPS if a["key"] in keys]
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, on_signal)
    roots = {a["attr"]: store_path(a["attr"]) for a in apps if a["attr"]}
    STATE["roots"] = list(roots.values())
    for a in apps:
        if not a["attr"] and not shutil.which(a["argv"]("", "")[0]):
            sys.exit(f"{a['argv']('', '')[0]} not on PATH")
    workspaces = niri("workspaces")
    current = next(w for w in workspaces if w["is_focused"])
    occupied = {w["workspace_id"] for w in windows().values()}
    empty = next((w for w in workspaces if w["output"] == current["output"] and w["id"] not in occupied), None)
    if empty is None:
        sys.exit("no empty workspace on the focused output")
    STATE["workspace"], STATE["run_workspace"], STATE["a11y"] = current["idx"], empty["id"], a11y_enabled()
    save_state()
    niri_action("focus-workspace", str(empty["idx"]))
    data = {"rows": [], "late": [], "multi": [], "notes": STATE["notes"]}
    try:
        for app in apps:
            root = roots.get(app["attr"], "")
            if args.dump:
                with phase(app) as tmp:
                    dump(app, root, tmp)
                continue
            if not args.skip_late:
                print(f"{app['key']}: late", file=sys.stderr, flush=True)
                with phase(app) as tmp:
                    data["late"].append(measure(app, root, tmp, late=True))
            print(f"{app['key']}: measure", file=sys.stderr, flush=True)
            with phase(app) as tmp:
                data["rows"].append(measure(app, root, tmp, late=False))
            if app["key"] in MULTI and not args.skip_multi:
                print(f"{app['key']}: multi", file=sys.stderr, flush=True)
                with phase(app) as tmp:
                    data["multi"].append(multi(app, root, tmp))
            if args.json:  # after every app, so a run that stops part-way keeps what it measured
                Path(args.json).write_text(json.dumps(data, indent=1))
    finally:
        restore()
    if args.json:
        Path(args.json).write_text(json.dumps(data, indent=1))
    if not args.dump:
        print(table(data))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "probe":
        print(json.dumps(probe_main(int(sys.argv[2]), sys.argv[3:])))
    elif len(sys.argv) > 1 and sys.argv[1] == "reader-fixed":
        reader_fixed_main(sys.argv[2:])
    else:
        main()
