#!/usr/bin/env python3
r"""How far Flow's AT-SPI app-menu reader reaches, app by app: the table in
docs/engineering/APPMENU_COVERAGE_2026-11.md.

For each app in APPS it opens one window on the focused output's empty workspace and records:
- whether the app is on the accessibility bus (an AT-SPI application with the window's PID) and has a menu bar;
- what this checkout's reader (session/appmenu.py, run as ``flow-appmenu`` runs it: a fresh process, as the
  shell starts it) returns for the window: menu items, or its error, and its wall time (median of three runs).
  The reader's app_name() raises for an app_id with no matching desktop file (LibreOffice, Electron), and the
  reader then returns only that error; so the reader is also run with only that exception caught ("fixed"),
  to show what the walk itself reaches. The commands and the late column use the fixed reader;
- the walk itself, in a fresh process: AT-SPI nodes visited, menu items, time, and the same walk without the
  reader's caps (2000 nodes, depth 12), so a cap that cuts the menu shows;
- how many of the items carry a shortcut label (``Action.getKeyBinding``);
- whether a harmless command (an About dialog) runs through the reader's ``activate`` path, AT-SPI
  ``doAction``, and whether its effect shows (a new window, closed again);
- how many Niri windows the window's process owns (the reader gives up when it owns more than one);
- the reader's result for a window opened while accessibility was off ("late": the reader switches it on).

Then, for the apps in MULTI, it opens more windows in the same process and checks, with each focused in turn,
whether the accessible frames' names and ``active`` state single out the focused window: by title alone, by
``active`` alone, and by both.

Run it with ``tests/run_appmenu_coverage.sh`` (it takes the Python with pyatspi from the installed
``flow-appmenu`` wrapper and holds the screen lock) inside a Niri session where the session bus can start the
accessibility bus. Apps not on PATH are built with ``nix build --no-link`` from ``FLOW_NIXPKGS`` (default
``nixpkgs``); ``FLOW_NIX`` and ``FLOW_NIX_ARGS`` change the command (the test guest needs ``sudo -n nix`` and
``--option ssl-cert-file /etc/floe-test/host-trust.pem``). The system's configuration is not changed.

``org.a11y.Status.IsEnabled`` is set only for the run and put back as it was, also on SIGINT, SIGTERM and
SIGHUP. Each app gets its own temporary configuration, data and cache directories and browser profiles, and
GSettings in memory, so real settings are not touched. Nautilus is single-instance, and a session's Nautilus
usually runs already: it runs on a private session bus, with its own accessibility bus and runtime directory,
and every process on that bus is stopped afterward. Only windows the run opened are closed and only processes
it started are stopped.

    tests/run_appmenu_coverage.sh [--only kate,writer] [--skip-multi] [--skip-late] [--json out.json]
    tests/run_appmenu_coverage.sh --dump kate     # print the app's menu tree, to pick a command
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

READER = Path(__file__).resolve().parents[1] / "session" / "appmenu.py"
CAP_NODES, CAP_DEPTH = 2000, 12  # session/appmenu.py's walk limits

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
# a new window. None where the app has no menu bar. attr: nixpkgs attribute, or None for the command on PATH
# (the system's, as Flow installs it).
APPS = [
    dict(key="kate", name="Kate", toolkit="Qt 6 / KDE Frameworks 6", attr="kdePackages.kate",
         argv=lambda root, tmp: [f"{root}/bin/kate", "-n"], command=("^Help$", "^About Kate")),
    dict(key="sqlitebrowser", name="DB Browser for SQLite", toolkit="Qt 5", attr="sqlitebrowser",
         argv=lambda root, tmp: [f"{root}/bin/sqlitebrowser"], command=("^Help$", "^About")),
    dict(key="vlc", name="VLC", toolkit="Qt 5, X11 only (xwayland-satellite)", attr="vlc",
         argv=lambda root, tmp: [f"{root}/bin/vlc", "-I", "qt", "--no-qt-privacy-ask"], command=("^Help$", "^About")),
    dict(key="writer", name="LibreOffice Writer", toolkit="LibreOffice VCL, gtk3 plugin", attr="libreoffice",
         argv=lambda root, tmp: [f"{root}/bin/libreoffice", "--writer", "--norestore", "--nologo",
                                 f"-env:UserInstallation={libreoffice_profile(tmp)}"],
         command=("^Help$", "^About LibreOffice")),
    dict(key="mousepad", name="Mousepad", toolkit="GTK 3", attr="mousepad",
         argv=lambda root, tmp: [f"{root}/bin/mousepad"], command=("^Help$", "^About")),
    dict(key="nautilus", name="Nautilus", toolkit="GTK 4 / libadwaita", attr=None,
         argv=lambda root, tmp: ["nautilus", "--new-window"], command=None, private_bus=True),
    dict(key="text-editor", name="GNOME Text Editor", toolkit="GTK 4 / libadwaita", attr="gnome-text-editor",
         argv=lambda root, tmp: [f"{root}/bin/gnome-text-editor", "--standalone", "--new-window"], command=None),
    dict(key="firefox", name="Firefox", toolkit="Gecko, GTK 3", attr=None,
         argv=lambda root, tmp: ["firefox", "--new-instance", "--profile", firefox_profile(tmp), "about:blank"],
         command=("^Help$", "^About ")),
    dict(key="chromium", name="Chromium", toolkit="Chromium views", attr="chromium",
         argv=lambda root, tmp: [f"{root}/bin/chromium", f"--user-data-dir={tmp}/cr", "--ozone-platform=wayland",
                                 "--no-first-run", "--no-default-browser-check", "--password-store=basic",
                                 "about:blank"],
         command=None),
    dict(key="vscodium", name="VSCodium", toolkit="Electron, own title bar (default)", attr="vscodium",
         argv=vscodium(), command=("^Help$", "^About")),
    dict(key="vscodium-a11y", name="VSCodium --force-renderer-accessibility",
         toolkit="Electron, own title bar (default)", attr="vscodium", argv=vscodium(a11y=True),
         command=("^Help$", "^About")),
    dict(key="vscodium-native", name="VSCodium, native title bar", toolkit="Electron, native menu bar",
         attr="vscodium", argv=vscodium(native=True), command=("^Help$", "^About")),
    dict(key="vscodium-native-a11y", name="VSCodium, native title bar, --force-renderer-accessibility",
         toolkit="Electron, native menu bar", attr="vscodium", argv=vscodium(native=True, a11y=True),
         command=("^Help$", "^About")),
    dict(key="foot", name="foot", toolkit="none (Wayland only)", attr=None,
         argv=lambda root, tmp: ["foot"], command=None),
]

# More windows in the same process, opened one after another: an argv, or ("menu", top, item) invoked through
# AT-SPI in the first window. Each list gives two windows of one title, and Nautilus and Firefox one more of
# its own title.
MULTI = {
    "kate": [("menu", "^File$", "^New Window$")],
    "firefox": [firefox_window("about:robots"), firefox_window("about:blank")],
    "nautilus": [lambda root, tmp: ["nautilus", "--new-window", tmp], lambda root, tmp: ["nautilus", "--new-window"]],
}

STATE = {"a11y": None, "procs": [], "windows": set(), "workspace": None, "roots": [], "bus": None, "bus_proc": None}


# --- Niri, the session bus and the accessibility switch ------------------------------------------------------

def niri(what):
    return json.loads(subprocess.check_output(["niri", "msg", "-j", what], text=True))


def niri_action(*args):
    subprocess.run(["niri", "msg", "action", *args], check=True, stdout=subprocess.DEVNULL)


def windows():
    return {w["id"]: w for w in niri("windows")}


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
    subprocess.run(["busctl", "--user", "set-property", *A11Y, "b", "true" if on else "false"], check=True,
                   env=bus_env())


def start_private_bus(tmp):
    """A session bus of the run's own. The accessibility bus it starts would bind $XDG_RUNTIME_DIR/at-spi/bus,
    which every private session bus's accessibility bus shares, so the bus gets a runtime directory of its own."""
    runtime = Path(tmp) / "bus-runtime"
    runtime.mkdir(mode=0o700)
    env = {k: v for k, v in app_env(tmp).items() if k != "DBUS_SESSION_BUS_ADDRESS"}
    env["XDG_RUNTIME_DIR"] = str(runtime)
    proc = subprocess.Popen(["dbus-daemon", "--session", "--nofork", "--print-address=1"], env=env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True, start_new_session=True)
    STATE["bus"], STATE["bus_proc"] = proc.stdout.readline().strip(), proc


def stop_private_bus():
    """Stops every process of this user on the private bus (the app and what the bus started for it), then it."""
    address, proc = STATE["bus"], STATE["bus_proc"]
    STATE["bus"] = STATE["bus_proc"] = None
    if not address:
        return
    for _ in range(2):
        for pid in filter(str.isdigit, os.listdir("/proc")):
            try:
                environ = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
            except OSError:  # gone, or another user's
                continue
            if f"DBUS_SESSION_BUS_ADDRESS={address}".encode() in environ or f"DBUS_STARTER_ADDRESS={address}".encode() in environ:
                try:
                    os.kill(int(pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
        time.sleep(1)
    proc.terminate()
    proc.wait(5)


def restore():
    for wid in list(STATE["windows"]):
        close_window(wid, wait=0)
    for proc in STATE["procs"]:
        stop(proc)
    stop_private_bus()
    if STATE["a11y"] is not None:
        set_a11y(STATE["a11y"])
    if STATE["workspace"] is not None:
        try:
            niri_action("focus-workspace", str(STATE["workspace"]))
        except subprocess.CalledProcessError:
            pass


def on_signal(signum, _frame):
    restore()
    sys.exit(128 + signum)


# --- Apps --------------------------------------------------------------------------------------------------

def store_path(attr):
    nix = shlex.split(os.environ.get("FLOW_NIX", "nix"))
    extra = shlex.split(os.environ.get("FLOW_NIX_ARGS", ""))
    ref = f"{os.environ.get('FLOW_NIXPKGS', 'nixpkgs')}#{attr}"
    out = subprocess.check_output([*nix, "build", "--no-link", "--print-out-paths", *extra, ref], text=True)
    return next(p for p in out.split() if not p.endswith(("-man", "-doc", "-dev")))


def app_env(tmp):
    env = bus_env()
    for name in ("CONFIG", "DATA", "CACHE", "STATE"):
        path = Path(tmp) / name.lower()
        path.mkdir(exist_ok=True)
        env[f"XDG_{name}_HOME"] = str(path)
    env["GSETTINGS_BACKEND"] = "memory"  # GTK apps save window state on close
    if not env.get("WAYLAND_DISPLAY", "wayland-0").startswith("/"):  # still found beside a private runtime dir
        env["WAYLAND_DISPLAY"] = str(Path(os.environ["XDG_RUNTIME_DIR"]) / env.get("WAYLAND_DISPLAY", "wayland-0"))
    env.pop("GI_TYPELIB_PATH", None)  # the runner's, for pyatspi; an app's own wrapper sets its own
    return env


def spawn(argv, tmp):
    proc = subprocess.Popen(argv, env=app_env(tmp), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)
    STATE["procs"].append(proc)
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


@contextmanager
def phase(app):
    """A temporary directory for one launch of ``app``, and its private session bus if it needs one."""
    with tempfile.TemporaryDirectory(prefix=f"flow-appmenu-{app['key']}-", ignore_cleanup_errors=True) as tmp:
        if app.get("private_bus"):
            start_private_bus(tmp)
        try:
            yield tmp
        finally:
            for proc in STATE["procs"]:
                stop(proc)
            STATE["procs"].clear()
            stop_private_bus()


def wait_new_window(known, timeout=90, settle=3.0):
    """The windows not in ``known``, once a first one appeared and no further one for ``settle`` s."""
    deadline = time.monotonic() + timeout
    seen, since = [], None
    while time.monotonic() < deadline:
        new = [w for wid, w in windows().items() if wid not in known]
        if len(new) != len(seen):
            seen, since = new, time.monotonic()
        if seen and time.monotonic() - since >= settle:
            STATE["windows"].update(w["id"] for w in seen)
            return sorted(seen, key=lambda w: w["id"])
        time.sleep(0.25)
    STATE["windows"].update(w["id"] for w in seen)
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


def reader_fixed_main(args):
    spec = importlib.util.spec_from_file_location("appmenu", READER)
    appmenu = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(appmenu)
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
        if top.search(labels[0]) and item.search(labels[-1].strip()) and leaf.get("enabled"):
            return labels, leaf
    return None, None


def accelerator(binding):
    """The shortcut in an AT-SPI key binding. GTK gives "mnemonic;path;accelerator"; Qt gives the shortcut
    alone, or, for an item without one, its mnemonic ("Alt+N"), which is not a shortcut and gives ""."""
    if not binding:
        return ""
    parts = binding.split(";")
    key = (parts[2] if len(parts) == 3 else parts[0] if len(parts) == 1 else parts[-1]).strip()
    return "" if re.fullmatch(r"(Alt\+|<Alt>)\S", key) else key


def narrowing(frames, title):
    """Which frames each rule picks for a focused window titled ``title``: by name, by active state, and both."""
    by_title = [i for i, f in enumerate(frames) if f["name"] == title]
    by_active = [i for i, f in enumerate(frames) if f["active"]]
    both = [i for i in by_title if i in by_active]
    return {"by_title": by_title, "by_active": by_active, "both": both, "resolved": len(both) == 1}


# --- One app -----------------------------------------------------------------------------------------------

def measure(app, root, tmp, late):
    known = set(windows())
    if late:
        set_a11y(False)
        time.sleep(0.5)
    else:
        set_a11y(True)
    spawn(app["argv"](root, tmp), tmp)
    new = wait_new_window(known)
    row = {"key": app["key"], "late": late}
    if not new:
        row["error"] = "no window"
        return row
    win = new[0]
    row.update(window=win["id"], title=win["title"], app_id=win.get("app_id"), pid=win.get("pid"),
               extra_windows=[w["title"] for w in new[1:]])
    focus(win["id"])
    if late:
        first, took, _ = reader(fixed=True)
        row["late_first"] = {"items": count(first), "s": round(took, 2), "error": first.get("error")}
        time.sleep(3)
        second, took, _ = reader(fixed=True)
        row["late_second"] = {"items": count(second), "s": round(took, 2), "error": second.get("error")}
    else:
        row["pid_windows"] = sum(w.get("pid") == win.get("pid") for w in windows().values())
        for name, fixed in (("reader", False), ("fixed", True)):
            runs = [reader(fixed=fixed) for _ in range(3)]
            result = runs[-1][0]
            row[name] = {"items": count(result), "top": [m["label"] for m in result.get("menus", [])],
                         "median_s": round(statistics.median(took for _, took, _ in runs), 2),
                         "error": result.get("error")}
        row["probe"] = probe(win.get("pid"), win["title"])
        row["command"] = invoke(app, result.get("menus", []), win)
    for w in new:
        close_window(w["id"])
    return row


def invoke(app, menus, win):
    """The app's harmless command through the (fixed) reader's activate path; its new window is closed again."""
    if not app.get("command"):
        return None
    labels, leaf = find_command(menus, app["command"])
    if not leaf:
        return {"found": False}
    focus(win["id"])
    known = set(windows())
    result, _, code = reader("activate", str(win["id"]), json.dumps(leaf["path"]), leaf["label"], fixed=True)
    deadline = time.monotonic() + 8
    opened = []
    while time.monotonic() < deadline and not opened:
        opened = [w for wid, w in windows().items() if wid not in known]
        time.sleep(0.25)
    time.sleep(1)
    opened = [w for wid, w in windows().items() if wid not in known]
    for w in opened:
        STATE["windows"].add(w["id"])
        close_window(w["id"])
    return {"found": True, "labels": [label.strip() for label in labels], "ok": code == 0,
            "error": result.get("error"), "opened": [w["title"] for w in opened]}


def multi(app, root, tmp):
    """Several windows of one process; with each focused, which accessible frame the title and active rules pick."""
    set_a11y(True)
    known = set(windows())
    spawn(app["argv"](root, tmp), tmp)
    first = wait_new_window(known)
    row = {"key": app["key"], "cases": []}
    if not first:
        row["error"] = "no first window"
        return row
    pid = first[0].get("pid")
    mine, others = [first[0]], first[1:]
    for opener in MULTI[app["key"]]:
        focus(mine[0]["id"])
        known = set(windows())
        if isinstance(opener, tuple):
            _, leaf = find_command(reader(fixed=True)[0].get("menus", []), opener[1:])
            if leaf:
                reader("activate", str(mine[0]["id"]), json.dumps(leaf["path"]), leaf["label"], fixed=True)
        else:
            spawn(opener(root, tmp), tmp)
        opened = wait_new_window(known)
        mine += [w for w in opened if w.get("pid") == pid][:1]
        others += [w for w in opened if w not in mine]
    if len(mine) < 2:
        row["error"] = "no second window in the same process"
    for win in mine:
        focus(win["id"])
        current = windows()
        title = current[win["id"]]["title"]
        frames = probe(pid, title, "--frames").get("frames", [])
        rules = narrowing(frames, title)
        case = {"title": title, "pid_windows": sum(w.get("pid") == pid for w in current.values()),
                "same_title_windows": sum(w.get("pid") == pid and w["title"] == title for w in current.values()),
                "frames": frames, **rules, "reader_items": count(reader(fixed=True)[0])}
        if rules["resolved"]:
            case["frame_items"] = frames[rules["both"][0]].get("items")
            case["frame_ms"] = frames[rules["both"][0]].get("ms")
        row["cases"].append(case)
    for win in mine + others:
        close_window(win["id"])
    return row


# --- The probe: runs in its own process, with pyatspi ---------------------------------------------------------

def probe_main(pid, frames_only):
    import pyatspi
    menu_roles = (pyatspi.ROLE_MENU, pyatspi.ROLE_MENU_ITEM, pyatspi.ROLE_CHECK_MENU_ITEM, pyatspi.ROLE_RADIO_MENU_ITEM)
    frame_roles = (pyatspi.ROLE_FRAME, pyatspi.ROLE_WINDOW, pyatspi.ROLE_DIALOG)
    desktop = pyatspi.Registry.getDesktop(0)
    app = None
    for _ in range(40):  # an app registers on the bus a moment after its window maps
        app = next((desktop.getChildAtIndex(i) for i in range(desktop.childCount)
                    if (desktop.getChildAtIndex(i).name or "").lower() not in ("quickshell", "niri")
                    and desktop.getChildAtIndex(i).get_process_id() == pid), None)
        if app is not None:
            break
        time.sleep(0.25)
    if app is None:
        return {"on_bus": False}

    def walk(node, depth, menu, st, capped, found_leaves):
        """session/appmenu.py's walk, counting nodes and menu bars; ``capped`` applies its limits."""
        st["nodes"] += 1
        if node is None or (capped and (st["nodes"] > CAP_NODES or depth > CAP_DEPTH)):
            return []
        st["depth"] = max(st["depth"], depth)
        role = node.getRole()
        bar = role == pyatspi.ROLE_MENU_BAR
        st["bars"] += bar
        if menu and role in menu_roles:
            children = []
            for i in range(node.childCount):
                children.extend(walk(node.getChildAtIndex(i), depth + 1, True, st, capped, found_leaves))
            if children:
                return [{"label": node.name or "", "children": children}]
            if role != pyatspi.ROLE_MENU and not node.childCount:
                found_leaves.append(node)
                return [{"label": node.name or ""}]
            return []
        found = []
        for i in range(node.childCount):
            found.extend(walk(node.getChildAtIndex(i), depth + 1, menu or bar, st, capped, found_leaves))
        return found

    def timed_walk(root, capped):
        st, found_leaves = {"nodes": 0, "depth": 0, "bars": 0}, []
        start = time.monotonic()
        menus = walk(root, 0, False, st, capped, found_leaves)
        st["ms"] = round((time.monotonic() - start) * 1000)
        st["items"] = len(found_leaves)
        st["top"] = [m["label"] for m in menus]
        return st, found_leaves

    if frames_only:
        frames = []
        for i in range(app.childCount):
            child = app.getChildAtIndex(i)
            if child is None or child.getRole() not in frame_roles:
                continue
            st, _ = timed_walk(child, True)
            frames.append({"name": child.name or "", "role": child.getRoleName(),
                           "active": child.getState().contains(pyatspi.STATE_ACTIVE),
                           "items": st["items"], "ms": st["ms"], "bars": st["bars"]})
        return {"on_bus": True, "frames": frames}

    capped, _ = timed_walk(app, True)
    full, found_leaves = timed_walk(app, False)
    shortcuts = []
    for node in found_leaves:
        try:
            key = accelerator(node.queryAction().getKeyBinding(0))
        except Exception:  # no Action interface
            key = ""
        if key:
            shortcuts.append(f"{(node.name or '').strip()}={key}")
    return {"on_bus": True, "app_name": app.name, "capped": capped, "full": full,
            "shortcuts": len(shortcuts), "shortcut_examples": shortcuts[:4]}


def dump(app, root, tmp):
    set_a11y(True)
    known = set(windows())
    spawn(app["argv"](root, tmp), tmp)
    new = wait_new_window(known)
    if new:
        focus(new[0]["id"])
        result = reader(fixed=True)[0]
        for labels, leaf in leaves(result.get("menus", [])):
            print(" > ".join(labels), "" if leaf.get("enabled") else "(disabled)")
        print(json.dumps({k: v for k, v in result.items() if k != "menus"}))
    for w in new:
        close_window(w["id"])


# --- Report ------------------------------------------------------------------------------------------------

def yes(flag):
    return "yes" if flag else "no"


def table(rows, late_rows, multi_rows):
    lines = ["| App | Toolkit | On a11y bus | Menu bar | Reader items (as is) | Items, app_name fixed | "
             "Nodes visited (uncapped) | Walk ms | Reader s | Shortcut labels | Command via doAction | Windows of PID | "
             "Late enable (1st / 2nd call) |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    late = {r["key"]: r for r in late_rows}
    for r in rows:
        app = next(a for a in APPS if a["key"] == r["key"])
        if "error" in r:
            lines.append(f"| {app['name']} | {app['toolkit']} | {r['error']} |" + " |" * 10)
            continue
        p, rd, fx = r.get("probe", {}), r["reader"], r["fixed"]
        full, capped = p.get("full", {}), p.get("capped", {})
        cmd = r.get("command")
        if cmd is None:
            cmd_text = "n/a"
        elif not cmd["found"]:
            cmd_text = "item not found"
        else:
            cmd_text = (f"{' > '.join(cmd['labels'])}: {'ran' if cmd['ok'] else 'failed'}"
                        f"{', opened ' + repr(cmd['opened'][0]) if cmd['opened'] else ', no window'}")
        lr = late.get(r["key"])
        late_text = f"{lr['late_first']['items']} / {lr['late_second']['items']}" if lr and "late_first" in lr else ""
        nodes = f"{capped.get('nodes', '')} ({full.get('nodes', '')})" if p.get("on_bus") else ""
        cut = f" (uncapped {full.get('items')})" if p.get("on_bus") and capped.get("items") != full.get("items") else ""
        as_is = "error" if rd["error"] else rd["items"]
        shortcuts = f"{p.get('shortcuts')} of {full.get('items')}" if p.get("on_bus") else ""
        lines.append(
            f"| {app['name']} | {app['toolkit']} | {yes(p.get('on_bus'))} | {yes(full.get('bars'))} | {as_is} | "
            f"{fx['items']}{cut} | {nodes} | {capped.get('ms', '')} | {fx['median_s']} | {shortcuts} | {cmd_text} | "
            f"{r['pid_windows']} | {late_text} |")
    out = "\n".join(lines)
    if multi_rows:
        out += ("\n\n| App | Focused window | Windows of PID | Of them with this title | Frames | By title | By active | "
                "Both | Resolved | Frame items | Frame walk ms | Reader items |\n")
        out += "|---|---|---|---|---|---|---|---|---|---|---|---|\n"
        for m in multi_rows:
            for c in m.get("cases", []):
                out += (f"| {m['key']} | {c['title']} | {c['pid_windows']} | {c['same_title_windows']} | "
                        f"{len(c['frames'])} | {len(c['by_title'])} | {len(c['by_active'])} | {len(c['both'])} | "
                        f"{yes(c['resolved'])} | {c.get('frame_items', '')} | {c.get('frame_ms', '')} | "
                        f"{c['reader_items']} |\n")
            if m.get("error"):
                out += f"| {m['key']} | {m['error']} |" + " |" * 10 + "\n"
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only")
    parser.add_argument("--skip-multi", action="store_true")
    parser.add_argument("--skip-late", action="store_true")
    parser.add_argument("--dump")
    parser.add_argument("--json")
    args = parser.parse_args()
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
    STATE["workspace"], STATE["a11y"] = current["idx"], a11y_enabled()
    niri_action("focus-workspace", str(empty["idx"]))
    rows, late_rows, multi_rows = [], [], []
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
                    late_rows.append(measure(app, root, tmp, late=True))
            print(f"{app['key']}: measure", file=sys.stderr, flush=True)
            with phase(app) as tmp:
                rows.append(measure(app, root, tmp, late=False))
            if app["key"] in MULTI and not args.skip_multi:
                print(f"{app['key']}: multi", file=sys.stderr, flush=True)
                with phase(app) as tmp:
                    multi_rows.append(multi(app, root, tmp))
            if args.json:  # after every app, so a run that stops part-way keeps what it measured
                Path(args.json).write_text(json.dumps({"rows": rows, "late": late_rows, "multi": multi_rows}, indent=1))
    finally:
        restore()
    if not args.dump:
        print(table(rows, late_rows, multi_rows))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "probe":
        print(json.dumps(probe_main(int(sys.argv[2]), "--frames" in sys.argv[4:])))
    elif len(sys.argv) > 1 and sys.argv[1] == "reader-fixed":
        reader_fixed_main(sys.argv[2:])
    else:
        main()
