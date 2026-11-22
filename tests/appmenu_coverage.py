#!/usr/bin/env python3
r"""How far Flow's AT-SPI app-menu reader reaches, app by app: the table in
docs/engineering/APPMENU_COVERAGE_2026-11.md.

For each app in APPS it opens one window on the focused output's empty workspace and records:
- whether the app is on the accessibility bus (an AT-SPI application with the window's PID; for a window whose
  PID is another process's, such as an X11 window's, also the PID the run started) and has a menu bar, with each
  bar's frame, showing state, children and item count;
- what this checkout's reader (session/appmenu.py, run as ``flow-appmenu`` runs it: a fresh process, as the
  shell starts it) returns for the window: menu items, or its error, and its wall time (median of three runs).
  The reader's app_name() raises for an app_id with no matching desktop file (LibreOffice), and the reader then
  returns only that error; so the reader is also run with only that exception caught ("fixed"), to show what the
  walk itself reaches. The commands and the late column use the fixed reader;
- the walk itself (``menu_walk``: the reader's walk, state reads included, with counters; a unit test runs both
  on one tree), in a fresh process: AT-SPI nodes visited, menu items, menu nodes dropped for having no items
  (unpopulated submenus look like that), time, and the same walk without the reader's caps (2000 nodes, depth
  12), so a cap that cuts the menu shows;
- how many of the items carry a shortcut label (``Action.getKeyBinding``);
- whether a harmless command (an About dialog) runs through the reader's ``activate`` path, AT-SPI
  ``doAction``, and whether its effect shows (a new window of the app's process, closed again);
- how many Niri windows the window's process owns (the reader gives up when it owns more than one);
- the reader's result for a window opened while accessibility was off ("late": the reader switches it on).

Then, for the apps in MULTI, it opens more windows in the same process and checks, with each focused in turn,
whether the accessible frames' names and ``active`` state single out the focused window: by title alone, by
``active`` alone, and by both. It does that once with the window focused, and once more with an exclusive-
keyboard layer surface over it, as Flow's own menu surface is. For Kate (GROUND_TRUTH) it then opens fresh pairs
of same-titled windows and, in each, invokes File > New through the frame chosen for one of them, focused or with
the frame chosen before the layer maps, and checks which Niri window changed title.

Run it with ``tests/run_appmenu_coverage.sh`` (it takes the Python with pyatspi from the installed
``flow-appmenu`` wrapper, imports the session's display variables, clears ``NO_AT_BRIDGE`` and ``GTK_A11Y``, and
holds the screen lock) inside a Niri session. Apps not on PATH are built with ``nix build --no-link`` from
``FLOW_NIXPKGS`` (default ``nixpkgs``); ``FLOW_NIX`` and ``FLOW_NIX_ARGS`` change the command (the test guest
needs ``sudo -n nix``, ``--option ssl-cert-file /etc/floe-test/host-trust.pem`` and ``--option flake-registry ''``
for its pinned nixpkgs). The system's configuration is not changed.

Isolation from the session it runs in (it may be shared):
- Every launch runs on a session bus of its own, with its own runtime directory, home, configuration, data and
  cache directories, and GSettings in memory. Its accessibility bus is that bus's, so the session's
  ``org.a11y.Status.IsEnabled``, which at-spi persists in dconf (``toolkit-accessibility``), is never set, and
  apps that write to $HOME (VSCodium's ``~/.vscode-oss``) write to the temporary one. The run records the
  session's switch, the dconf database's modification time and the home directory's entries before and after.
- Only the run's own processes and their windows are measured, closed and stopped: those that carry the run's
  marker in their environment, name one of its temporary directories in their command line (VSCodium's main
  process detaches and rewrites its environment's memory), or descend from such a process, and are this user's.
  An X11 window, whose PID is xwayland-satellite's, is taken by its app_id.
- On SIGINT, SIGTERM and SIGHUP it cleans up; ``--cleanup`` undoes a run that was killed, from the state file
  ``$XDG_RUNTIME_DIR/flow-appmenu-coverage.json``.

    tests/run_appmenu_coverage.sh [--only kate,writer] [--skip-multi] [--skip-late] [--json out.json]
    tests/run_appmenu_coverage.sh --dump kate         # print the app's menu tree, to pick a command
    tests/run_appmenu_coverage.sh --table run.json    # the report's tables, from a run's JSON
    tests/run_appmenu_coverage.sh --cleanup           # after a run that was killed
"""

import argparse
from contextlib import contextmanager
import datetime
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
         argv=lambda root, tmp: ["nautilus", "--new-window"], command=None),
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

STATE = {"run": uuid.uuid4().hex, "tmps": set(), "procs": [], "windows": set(), "workspace": None,
         "run_workspace": None, "roots": [], "bus": None, "bus_proc": None, "notes": [], "restored": False}


# --- Niri ------------------------------------------------------------------------------------------------------

def niri(what):
    return json.loads(subprocess.check_output(["niri", "msg", "-j", what], text=True))


def niri_action(*args):
    subprocess.run(["niri", "msg", "action", *args], check=True, stdout=subprocess.DEVNULL)


def windows():
    return {w["id"]: w for w in niri("windows")}


def note(text):
    STATE["notes"].append(text)
    print(f"note: {text}", file=sys.stderr, flush=True)


# --- The run's private session bus and its accessibility switch -------------------------------------------------

def bus_env(env=None):
    """``env`` (default: this process's) on the run's private session bus. Fails closed: whatever runs with it
    (the reader, which switches accessibility on, the probe, busctl, an app) must never reach the session's bus."""
    if not STATE["bus"]:
        raise RuntimeError("no private session bus: refusing to run on the session's bus")
    env = dict(os.environ if env is None else env)
    env["DBUS_SESSION_BUS_ADDRESS"] = STATE["bus"]
    return env


A11Y = ("org.a11y.Bus", "/org/a11y/bus", "org.a11y.Status", "IsEnabled")


def set_a11y(on):
    """The private bus's accessibility switch. Never the session's: it persists in the user's dconf."""
    subprocess.run(["busctl", "--user", "set-property", *A11Y, "b", "true" if on else "false"], check=True,
                   env=bus_env(), stdout=subprocess.DEVNULL)


def session_a11y():
    """The session's own switch, read only (for the record). The read may start the session's accessibility bus
    if it is not running; it does not change the switch."""
    out = subprocess.run(["busctl", "--user", "get-property", *A11Y], capture_output=True, text=True)
    return out.stdout.strip() or out.stderr.strip()


def start_private_bus(tmp):
    """A session bus of the launch's own, in its runtime directory, so its accessibility bus binds
    <tmp>/runtime/at-spi/bus, not the session's. Fails closed: without it an app could join the session's."""
    env = launch_env(tmp)
    proc = subprocess.Popen(["dbus-daemon", "--session", "--nofork", "--print-address=1"], env=env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True, start_new_session=True)
    STATE["bus_proc"] = proc
    address = proc.stdout.readline().strip()
    if not address.startswith("unix:"):
        end(proc)
        STATE["bus_proc"] = None
        raise RuntimeError("the private session bus did not start")
    STATE["bus"] = address


def end(proc):
    proc.terminate()
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(5)


def stop_private_bus():
    """Stops every process on the private bus (the apps and what the bus started for them), then the bus."""
    address, proc = STATE["bus"], STATE["bus_proc"]
    STATE["bus"] = STATE["bus_proc"] = None
    if proc is None:
        return
    for _ in range(2):
        pids = bus_clients(address) if address else []
        for pid in pids:
            kill(pid, signal.SIGTERM)
        if pids:
            time.sleep(1)
    end(proc)


# --- The run's own processes -----------------------------------------------------------------------------------

def kill(pid, sig):
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def user_pids(proc_root=None):
    """This user's processes."""
    pids = []
    for entry in (proc_root or PROC).iterdir():
        if entry.name.isdigit():
            try:
                if entry.stat().st_uid == os.getuid():
                    pids.append(int(entry.name))
            except OSError:
                pass
    return pids


def environ_of(pid, proc_root=None):
    try:
        return set(((proc_root or PROC) / str(pid) / "environ").read_bytes().split(b"\0"))
    except OSError:  # gone, or another user's
        return set()


def cmdline_of(pid, proc_root=None):
    try:
        return ((proc_root or PROC) / str(pid) / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        return ""


def parent_of(pid, proc_root=None):
    try:
        stat = ((proc_root or PROC) / str(pid) / "stat").read_text()
    except OSError:
        return None
    return int(stat.rsplit(")", 1)[1].split()[1])


def marked(pid, proc_root=None):
    """The run's marker in the environment, or one of its temporary directories in the command line."""
    if f"{MARKER}={STATE['run']}".encode() in environ_of(pid, proc_root):
        return True
    cmdline = cmdline_of(pid, proc_root)
    return any(tmp in cmdline for tmp in STATE["tmps"])


def processes_with(entries, proc_root=None):
    """This user's processes whose environment holds one of ``entries`` ("NAME=value")."""
    wanted = {e.encode() for e in entries}
    return [pid for pid in user_pids(proc_root) if environ_of(pid, proc_root) & wanted]


def bus_clients(address, proc_root=None):
    return processes_with([f"DBUS_SESSION_BUS_ADDRESS={address}", f"DBUS_STARTER_ADDRESS={address}"], proc_root)


def own_processes(proc_root=None):
    """This user's processes that are the run's: marked ones and all their descendants."""
    pids = user_pids(proc_root)
    parents = {pid: parent_of(pid, proc_root) for pid in pids}
    own = {pid for pid in pids if marked(pid, proc_root)}
    grown = True
    while grown:
        more = {pid for pid in pids if pid not in own and parents.get(pid) in own}
        own |= more
        grown = bool(more)
    own.discard(os.getpid())
    return sorted(own)


def owned(pid, proc_root=None):
    return bool(pid) and pid in own_processes(proc_root)


def stop_own_processes():
    """Every process of the run that is still alive, detached ones too: SIGTERM, then SIGKILL."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        pids = own_processes()
        for pid in pids:
            kill(pid, sig)
        if pids and sig == signal.SIGTERM:
            time.sleep(3)


def is_own_window(window, x11_app_id=None):
    if x11_app_id:  # an X11 window carries xwayland-satellite's PID
        return window.get("app_id") == x11_app_id
    return owned(window.get("pid"))


# --- State for --cleanup, and cleaning up ---------------------------------------------------------------------

def state_file():
    return Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "flow-appmenu-coverage.json"


def save_state():
    """Written whole or not at all, so a kill mid-write leaves the previous state for --cleanup."""
    partial = state_file().with_suffix(".partial")
    partial.write_text(json.dumps({"run": STATE["run"], "tmps": sorted(STATE["tmps"]),
                                   "windows": sorted(STATE["windows"])}))
    partial.replace(state_file())


def close_owned_windows(x11_app_id=None):
    """The run's windows: those it took, and any of its processes' it missed."""
    own = set(own_processes())
    for wid, w in windows().items():
        if wid in STATE["windows"] or (not x11_app_id and w.get("pid") in own):
            close_window(wid, wait=5)
    STATE["windows"].clear()


def cleanup():
    """Undoes a run that was killed: closes its windows and stops its processes (its private buses among them)."""
    try:
        saved = json.loads(state_file().read_text())
    except FileNotFoundError:
        print("no state file: nothing to clean up")
        return
    STATE["run"], STATE["tmps"], STATE["windows"] = saved["run"], set(saved["tmps"]), set(saved["windows"])
    close_owned_windows()  # Niri never reuses a window id, so saved ones are still the run's own
    stop_own_processes()
    for tmp in STATE["tmps"]:
        shutil.rmtree(tmp, ignore_errors=True)
    state_file().unlink()
    print("cleaned up")


def restore():
    if STATE["restored"]:
        return
    STATE["restored"] = True
    for wid in list(STATE["windows"]):
        close_window(wid, wait=0)
    for proc in STATE["procs"]:
        stop(proc)
    stop_private_bus()
    stop_own_processes()
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


def launch_env(tmp, extra=None):
    """A launch's environment without a session bus: a runtime directory, home and XDG directories of its own,
    and GSettings in a key file under that configuration directory. The private bus and its accessibility bus are
    started with it, so the launcher's switch (which at-spi persists through GSettings) lands in that key file,
    where the launch's apps read it as they would dconf, and never in the user's dconf."""
    env = {k: v for k, v in os.environ.items() if k != "DBUS_SESSION_BUS_ADDRESS"}
    real_runtime = os.environ.get("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid())
    display = env.get("WAYLAND_DISPLAY", "wayland-0")
    env["WAYLAND_DISPLAY"] = display if display.startswith("/") else str(Path(real_runtime) / display)
    for name, sub in (("XDG_RUNTIME_DIR", "runtime"), ("HOME", "home"), ("XDG_CONFIG_HOME", "config"),
                      ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache"), ("XDG_STATE_HOME", "state")):
        path = Path(tmp) / sub
        path.mkdir(mode=0o700, exist_ok=True)
        env[name] = str(path)
    env["GSETTINGS_BACKEND"] = "keyfile"  # under XDG_CONFIG_HOME/glib-2.0/settings/keyfile
    env.pop("GI_TYPELIB_PATH", None)  # the runner's, for pyatspi; an app's own wrapper sets its own
    env[MARKER] = STATE["run"]
    env.update(extra or {})
    return env


def app_env(tmp, extra=None):
    """A launch's environment on the private bus."""
    return bus_env(launch_env(tmp, extra))


def spawn(argv, tmp, extra_env=None):
    proc = subprocess.Popen(argv, env=app_env(tmp, extra_env), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)
    STATE["procs"].append(proc)
    return proc


def stop(proc):
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(5)
    except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


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
    """A temporary directory and private session bus for one launch of ``app``; everything it started is
    stopped afterward."""
    with tempfile.TemporaryDirectory(prefix=f"flow-appmenu-{app['key']}-", ignore_cleanup_errors=True) as tmp:
        STATE["tmps"].add(tmp)
        save_state()
        try:
            start_private_bus(tmp)
            yield tmp
        finally:
            close_owned_windows(app.get("x11"))
            for proc in STATE["procs"]:
                stop(proc)
            STATE["procs"].clear()
            stop_private_bus()
            stop_own_processes()
            time.sleep(1)  # before the directory goes: processes that were writing to it have exited


@contextmanager
def exclusive_layer(tmp):
    """An exclusive-keyboard overlay surface, up for the block's duration; yields whether Niri shows it up."""
    qml = Path(tmp) / "layer.qml"
    qml.write_text(LAYER_QML)

    def up():
        return any(l.get("namespace") == "flow-appmenu-coverage" and l.get("keyboard_interactivity") == "Exclusive"
                   for l in niri("layers"))
    was_up = up()
    proc = spawn(["quickshell", "-p", str(qml)], tmp)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not up():
        time.sleep(0.25)
    time.sleep(1)
    try:
        yield not was_up and up()
    finally:
        stop(proc)
        STATE["procs"].remove(proc)
        time.sleep(0.5)


# --- The reader, and the probe, each in a fresh process ------------------------------------------------------

def run(argv, env, timeout=120):
    start = time.monotonic()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return {"error": f"timed out after {timeout} s"}, timeout, -1
    took = time.monotonic() - start
    try:
        data = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        data = {"error": (proc.stderr or proc.stdout).strip()[-300:]}
    return data, took, proc.returncode


def reader(*args, fixed=False):
    """session/appmenu.py with ``args``, on the private bus, the test apps' desktop files and programs findable
    as an installed app's are (GIO finds no desktop file whose Exec program is not on PATH). ``fixed``: with
    app_name()'s TypeError caught."""
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
    return sum(1 for _ in leaves((result or {}).get("menus", [])))


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
    set_a11y(not late)
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
        row["late_first"] = {"items": count(first), "s": round(took, 2), "error": first.get("error"), "id": first.get("id")}
        time.sleep(3)
        second, took, _ = reader(fixed=True)
        row["late_second"] = {"items": count(second), "s": round(took, 2), "error": second.get("error"),
                              "id": second.get("id")}
        if app.get("x11"):  # the reader cannot reach it: is it on the bus under its own PID now?
            row["late_spawned"] = probe(proc.pid)
        return row
    row["pid_windows"] = sum(w.get("pid") == win.get("pid") for w in windows().values())
    for name, fixed in (("reader", False), ("fixed", True)):
        runs = [reader(fixed=fixed) for _ in range(3)]
        result = runs[-1][0]
        row[name] = {"items": count(result), "top": [m["label"] for m in result.get("menus", [])],
                     "median_s": round(statistics.median(took for _, took, _ in runs), 2), "error": result.get("error"),
                     "ids": sorted({r.get("id") for r, _, _ in runs if "id" in r})}
        if row[name]["ids"] not in ([], [win["id"]]):
            row["error"] = f"the reader read window {row[name]['ids']}, not {win['id']}: focus moved"
            return row
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
    case = {"window": win["id"], "title": title, "pid_windows": sum(w.get("pid") == pid for w in current.values()),
            "same_title_windows": sum(w.get("pid") == pid and w["title"] == title for w in current.values()),
            "frames": frames, **rules}
    if rules["resolved"]:
        case["frame_items"] = frames[rules["both"][0]].get("items")
        case["frame_ms"] = frames[rules["both"][0]].get("ms")
    return case


def open_windows(app, root, tmp):
    """The app's first window and the MULTI windows in its process: (pid, windows of that process)."""
    known = set(windows())
    spawn(app["argv"](root, tmp), tmp)
    first = wait_new_window(known)
    if not first:
        return None, []
    pid, mine = first[0].get("pid"), [first[0]]
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
    return pid, mine


def close_all(wins):
    for w in wins:
        close_window(w["id"])


def multi(app, root, tmp):
    """Several windows of one process; with each focused, which accessible frame the title and active rules pick,
    without and with an exclusive-keyboard layer over it."""
    row = {"key": app["key"], "cases": [], "layer_cases": []}
    set_a11y(True)
    pid, mine = open_windows(app, root, tmp)
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
    close_all(mine)
    return row


def truth_case(app, root, tmp, which, mode):
    """In a fresh pair of same-titled windows (the phase's own), GROUND_TRUTH through the frame chosen for window
    ``which``: correct when only that window's title changes."""
    set_a11y(True)
    pid, pair = open_windows(app, root, tmp)
    result = {"mode": mode, "valid": False}
    if len(pair) < 2:
        result["error"] = "no pair"
        close_all(pair)
        return result
    win = pair[which]
    focus(win["id"])
    chosen = narrow_case(pid, win)  # with the window focused; in "layer" mode, before the layer maps
    result.update(window=win["id"], title=chosen["title"], same_title_windows=chosen["same_title_windows"],
                  resolved=chosen["resolved"], valid=chosen["same_title_windows"] >= 2)
    before = {w["id"]: windows()[w["id"]]["title"] for w in pair}

    # The negative control goes through the other same-titled frame: then the other window must change.
    target, frame = win, chosen["both"][0] if chosen["resolved"] else None
    if mode == "other window's frame":
        others = [i for i in chosen["by_title"] if i != frame]
        target, frame = pair[1 - which], (others[0] if chosen["resolved"] and len(others) == 1 else None)
    result["expected"] = target["id"]

    def invoke_through_frame():
        if frame is None:
            return {"invoked": False, "error": "no frame chosen"}
        return probe(pid, "--invoke", frame, *GROUND_TRUTH[app["key"]])
    if mode == "frame chosen, then layer":
        with exclusive_layer(tmp) as up:
            result["layer_up"] = up
            done = invoke_through_frame()
    else:
        done = invoke_through_frame()
    time.sleep(1.5)
    after = {w["id"]: windows()[w["id"]]["title"] for w in pair}
    result["changed"] = changed_windows(before, after)
    result.update(invoked=done.get("invoked", False), invoke_error=done.get("error"),
                  correct=result["changed"] == [target["id"]])
    close_all(pair)
    return result


def changed_windows(before, after):
    """Windows whose title differs between the two {id: title} maps."""
    return [wid for wid in before if before[wid] != after.get(wid)]


# --- The walk, and the probe: runs in its own process, with pyatspi ------------------------------------------

MENU_ROLES = ("ROLE_MENU", "ROLE_MENU_ITEM", "ROLE_CHECK_MENU_ITEM", "ROLE_RADIO_MENU_ITEM")


def menu_walk(node, depth, menu, st, capped, found_leaves, at):
    """session/appmenu.py's walk (active_window's ``walk``), state reads included, with counters in ``st``: nodes
    visited, deepest level, menu bars, and menu nodes dropped for yielding no items. ``capped`` applies its
    limits; ``at`` is pyatspi."""
    st["nodes"] += 1
    if node is None or (capped and (st["nodes"] > CAP_NODES or depth > CAP_DEPTH)):
        return []
    st["depth"] = max(st["depth"], depth)
    role = node.getRole()
    bar = role == at.ROLE_MENU_BAR
    st["bars"] += bar
    if menu and role in tuple(getattr(at, r) for r in MENU_ROLES):
        children = []
        for i in range(node.childCount):
            children.extend(menu_walk(node.getChildAtIndex(i), depth + 1, True, st, capped, found_leaves, at))
        if children:
            node.getState().contains(at.STATE_ENABLED)
            return [{"label": node.name or "", "children": children}]
        if role != at.ROLE_MENU and not node.childCount:
            node.getState().contains(at.STATE_ENABLED)
            found_leaves.append(node)
            return [{"label": node.name or ""}]
        st["dropped"] += 1
        return []
    found = []
    for i in range(node.childCount):
        found.extend(menu_walk(node.getChildAtIndex(i), depth + 1, menu or bar, st, capped, found_leaves, at))
    return found


def timed_walk(root, capped, at, depth=0):
    st, found_leaves = {"nodes": 0, "depth": 0, "bars": 0, "dropped": 0}, []
    start = time.monotonic()
    menus = menu_walk(root, depth, False, st, capped, found_leaves, at)
    st["ms"] = round((time.monotonic() - start) * 1000)
    st["items"] = len(found_leaves)
    st["top"] = [m["label"] for m in menus]
    return st, found_leaves, menus


def frames_of(app, at):
    return [(i, c) for i, c in ((i, app.getChildAtIndex(i)) for i in range(app.childCount))
            if c is not None and c.getRole() in (at.ROLE_FRAME, at.ROLE_WINDOW, at.ROLE_DIALOG)]


def menu_bars(app, at):
    """Every menu bar in ``app``: which frame (its index among the app's children) holds it, whether it is
    showing and visible, its children and its items."""
    bars = []

    def visit(node, frame, depth):
        if node is None or depth > 40:
            return
        if node.getRole() == at.ROLE_MENU_BAR:
            st, _, menus = timed_walk(node, False, at)
            state = node.getState()
            bars.append({"frame": frame, "showing": state.contains(at.STATE_SHOWING),
                         "visible": state.contains(at.STATE_VISIBLE), "children": node.childCount,
                         "items": st["items"], "top": len(menus), "dropped": st["dropped"]})
            return
        for i in range(node.childCount):
            visit(node.getChildAtIndex(i), frame, depth + 1)
    for index, frame in frames_of(app, at):
        visit(frame, {"index": index, "name": frame.name or ""}, 1)
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


def _top_label(node, at):
    """The label of the top-level menu ``node`` sits in: the menu whose parent is a menu bar."""
    while node is not None:
        parent = node.parent
        if parent is not None and parent.getRole() == at.ROLE_MENU_BAR:
            return (node.name or "").strip()
        node = parent
    return ""


def probe_main(pid, args):
    import pyatspi as at
    app = find_app(at, pid)
    if app is None:
        return {"on_bus": False}
    if "--frames" in args:
        frames = []
        for _, frame in frames_of(app, at):
            st, _, _ = timed_walk(frame, True, at, depth=1)  # a frame is at depth 1 in the reader's walk
            frames.append({"name": frame.name or "", "role": frame.getRoleName(),
                           "active": frame.getState().contains(at.STATE_ACTIVE),
                           "items": st["items"], "ms": st["ms"], "bars": st["bars"]})
        return {"on_bus": True, "frames": frames}
    if "--invoke" in args:
        index, top, item = args[args.index("--invoke") + 1:][:3]
        frame = [f for _, f in frames_of(app, at)][int(index)]
        _, found_leaves, _ = timed_walk(frame, True, at, depth=1)
        top_re, item_re = re.compile(top), re.compile(item)
        target = next((n for n in found_leaves if item_re.search((n.name or "").strip())
                       and top_re.search(_top_label(n, at))), None)
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
             "Nodes visited, capped (uncapped) | Dropped menu nodes | Walk ms | Reader s | Shortcut labels | "
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
            str(capped.get("dropped", "—")) if p.get("on_bus") else "—",
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
                out += f"| {m['key']} | {m['error']} |" + " |" * 9 + "\n"
    truths = [(m["key"], t) for m in data["multi"] for t in m.get("truth", [])]
    if truths:
        out += ("\n\nGround truth (in a fresh pair of windows, File > New through the frame chosen for one):\n\n"
                "| App | Focused window | Windows with its title | Frame invoked through | Invoked | "
                "Window that should change | Windows whose title changed | Correct |\n|---|---|---|---|---|---|---|---|\n")
        for key, t in truths:
            if "window" not in t:
                out += f"| {key} | {t.get('error')} | | {t['mode']} | | | | |\n"
                continue
            mode = t["mode"] + ("" if t.get("layer_up", True) else " (layer not up)")
            invoked = yes(t["invoked"]) + (f" ({t['invoke_error']})" if t.get("invoke_error") else "")
            out += (f"| {key} | {t['window']} ({t['title']}) | {t['same_title_windows']} | {mode} | {invoked} | "
                    f"{t.get('expected', t['window'])} | {t['changed']} | "
                    f"{yes(t['correct'])}{'' if t['valid'] else ' (title unique: not a test)'} |\n")
    env = data.get("env", {})
    if env:
        out += ("\n\nThe session around the run: accessibility switch (`org.a11y.Status.IsEnabled`) "
                f"{env['session_a11y']['before']} before, {env['session_a11y']['after']} after; "
                f"dconf database {'unchanged' if env['dconf']['before'] == env['dconf']['after'] else 'modified'}; "
                f"home directory entries added {env['home']['added'] or 'none'}, removed {env['home']['removed'] or 'none'}. "
                f"Run from {env['started']} to {env['finished']}.")
    if data.get("notes"):
        out += "\n\nNotes:\n" + "\n".join(f"- {n}" for n in data["notes"])
    return out


def versions():
    """The system's NixOS, Niri and at-spi versions, for the record."""
    found = {}
    for name, argv in (("nixos", ["nixos-version"]), ("niri", ["niri", "--version"])):
        try:
            found[name] = subprocess.run(argv, capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            found[name] = None
    launcher = shutil.which("at-spi-bus-launcher") or ""
    found["at-spi2-core"] = next((p for p in os.environ.get("GI_TYPELIB_PATH", "").split(":") if "at-spi2-core" in p),
                                 launcher)
    return found


def session_snapshot():
    dconf = Path.home() / ".config" / "dconf" / "user"
    return {"a11y": session_a11y(), "dconf": dconf.stat().st_mtime_ns if dconf.exists() else None,
            "home": sorted(p.name for p in Path.home().iterdir())}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only")
    parser.add_argument("--skip-multi", action="store_true")
    parser.add_argument("--skip-late", action="store_true")
    parser.add_argument("--dump")
    parser.add_argument("--json")
    parser.add_argument("--table", help="print the tables of an earlier run's JSON")
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
    system = {}
    for a in apps:
        if not a["attr"]:
            found = shutil.which(a["argv"]("", "")[0])
            if not found:
                sys.exit(f"{a['argv']('', '')[0]} not on PATH")
            system[a["argv"]("", "")[0]] = os.path.realpath(found)
    workspaces = niri("workspaces")
    current = next(w for w in workspaces if w["is_focused"])
    occupied = {w["workspace_id"] for w in windows().values()}
    empty = next((w for w in workspaces if w["output"] == current["output"] and w["id"] not in occupied), None)
    if empty is None:
        sys.exit("no empty workspace on the focused output")
    STATE["workspace"], STATE["run_workspace"] = current["idx"], empty["id"]
    before = session_snapshot()
    data = {"rows": [], "late": [], "multi": [], "notes": STATE["notes"],
            "env": {"started": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                    "roots": roots, "system": system, "versions": versions()}}
    save_state()
    niri_action("focus-workspace", str(empty["idx"]))
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
                    row = multi(app, root, tmp)
                if app["key"] in GROUND_TRUTH:
                    row["truth"] = []
                    for mode in ("focused", "frame chosen, then layer", "other window's frame"):
                        for which in (0, 1):
                            print(f"{app['key']}: ground truth, {mode}, window {which + 1}", file=sys.stderr, flush=True)
                            with phase(app) as tmp:
                                row["truth"].append(truth_case(app, root, tmp, which, mode))
                data["multi"].append(row)
            if args.json:  # after every app, so a run that stops part-way keeps what it measured
                Path(args.json).write_text(json.dumps(data, indent=1))
    finally:
        restore()
    after = session_snapshot()
    data["env"].update(finished=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                       session_a11y={"before": before["a11y"], "after": after["a11y"]},
                       dconf={"before": before["dconf"], "after": after["dconf"]},
                       home={"added": sorted(set(after["home"]) - set(before["home"])),
                             "removed": sorted(set(before["home"]) - set(after["home"]))})
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
