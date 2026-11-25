#!/usr/bin/env python3
"""F-003 on the installed system: pinned spaces after a reboot and after a new login.

The checks are listed under *F-003 acceptance* in docs/IMPLEMENTATION_STATUS.md. A reboot ends every process, so
the test runs in phases, one command each, sharing a run folder that outlives the reboot:

- ``prepare RUN --mail THUNDERBIRD``: backs up the real Flow state, settings file and recovery markers into RUN,
  empties the state folder, turns automatic recovery on and adds desktop entries for the test apps. Then it opens
  Mail (Thunderbird, from the store path given, with a profile of its own in RUN), the stand-ins Studio and Notes,
  Files and Scratch, makes Studio and Notes a Group, pins Mail and the Group, and waits for the controller's
  automatic save. Checks the baseline.
- ``reboot RUN``: reboots the guest.
- ``logout RUN [--swap-pins]``: ends the Niri session and restarts the display manager, which logs in again
  automatically. With ``--swap-pins`` the two pins first change places, with the shell stopped, so the saved
  session's order is no longer the pin order and the Group must come back first.
- ``check RUN --after reboot|relogin``: waits for automatic recovery and checks everything again, and that a new
  window put between the pins ends up after them.
- ``put-back RUN``: closes the windows the test opened and puts the real state, settings, desktop entries and
  markers back byte for byte, with the shell stopped meanwhile; starts it again whatever happens. Once a run is put
  back, it does nothing more.

``tests/run_reboot_pins_smoke.sh`` runs the phases from the host. Each phase runs in the guest as the session's
user, over ssh or not: it reads the Niri session's environment from ``systemctl --user show-environment``. With
``--screen-lock PATH`` it holds that lock while it runs (the guest's tests share one screen). The guest must have
Flow installed, log in to Niri automatically, allow ``sudo -n`` for the reboot and the display manager, and have no
window open (the reboot would close it). Prints one JSON line per check; exits 1 at the first that fails.
"""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

SHELL = "/etc/flow/shell.qml"
APPS = {"mail": "thunderbird", "studio": "flow-f003-studio", "notes": "flow-f003-notes",
        "files": "org.gnome.Nautilus", "scratch": "flow-f003-scratch"}
NEW = "flow-f003-new"  # check 7's new window
NAMES = {app: name for name, app in APPS.items()} | {NEW: "new"}
# Left to right: Mail pinned first, the Group pinned second, then the dynamic windows in saved order.
COLUMNS = [["mail"], ["studio", "notes"], ["files"], ["scratch"]]
GROUP_LABEL = "Studio + Notes"
STATE_FILES = ("spaces.json", "groups.json", "session.json")
# What else may be in the state folder. A writer's leftover temporary file may not.
KNOWN = {*STATE_FILES, "groups.initialized", "session.lock", "spaces.lock", "layer-shapes.json", "spaces.v1.json"}
# Thunderbird's first start without its dialogs: no account set-up, no default-client question, no start page.
THUNDERBIRD_PREFS = """user_pref("mail.provider.suppress_dialog_on_startup", true);
user_pref("mail.shell.checkDefaultClient", false);
user_pref("mailnews.start_page.enabled", false);
user_pref("datareporting.policy.dataSubmissionPolicyBypassNotification", true);
user_pref("toolkit.telemetry.reportingpolicy.firstRun", false);
user_pref("mail.rights.version", 1);
"""


class Failed(RuntimeError):
    pass


def check(name, ok, detail=""):
    print(json.dumps({"check": name, "ok": bool(ok), **({"detail": detail} if detail else {})}), flush=True)
    if not ok:
        raise Failed(f"{name}: {detail}")


def wait_for(test, label, timeout=15, step=0.2):
    end = time.monotonic() + timeout
    while True:
        value = test()
        if value:
            return value
        if time.monotonic() >= end:
            raise Failed(f"Timed out waiting for {label() if callable(label) else label}")
        time.sleep(step)


# The session ---------------------------------------------------------------------------------------------------

def session_environment():
    """The Niri session's environment, as systemd's user manager holds it (an ssh login has none of it)."""
    keep = ("NIRI_SOCKET", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DISPLAY", "XDG_DATA_DIRS", "XDG_CURRENT_DESKTOP")
    text = subprocess.run(["systemctl", "--user", "show-environment"], text=True, stdout=subprocess.PIPE,
                          check=True, timeout=10).stdout
    for line in text.splitlines():
        name, _, value = line.partition("=")
        if name in keep:
            if value.startswith("$'"):
                value = value[2:-1].encode().decode("unicode_escape")
            os.environ[name] = value
    if not os.environ.get("NIRI_SOCKET") or not Path(os.environ["NIRI_SOCKET"]).is_socket():
        raise Failed("No Niri session: its socket is not in systemd's user environment")


def wait_for_session(timeout=180):
    """A Niri session with the installed shell running and answering, after a login."""
    def up():
        try:
            session_environment()
            if subprocess.run(["systemctl", "--user", "is-active", "--quiet", "flow-shell"]).returncode:
                return False
            return isinstance(json.loads(ipc("spaces_view")), dict) and bool(read("workspaces"))
        except (Failed, OSError, subprocess.SubprocessError, ValueError):
            return False
    wait_for(up, "a Niri session with the installed shell", timeout=timeout, step=1)


def niri(*args):
    return subprocess.check_output(["niri", "msg", *map(str, args)], text=True, timeout=10)


def read(kind):
    return json.loads(niri("-j", kind))


def ipc(*args):
    return subprocess.run(["quickshell", "ipc", "-p", SHELL, "call", "desk", *map(str, args)], text=True, timeout=10,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout.strip()


def boot_id():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def niri_session():
    """As session/spaces.py names a Niri session: boot, socket path, its inode and creation time."""
    path = os.environ["NIRI_SOCKET"]
    stat = os.stat(path)
    return f"{boot_id()}:{path}:{stat.st_ino}:{stat.st_ctime_ns}"


def state_folder():
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "flow"


def settings_file():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "flow-settings.ini"


def applications():
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "applications"


def runtime():
    return Path(os.environ["XDG_RUNTIME_DIR"])


def controllers():
    """The installed shell's controllers: flow_controller processes with no XDG_STATE_HOME of their own."""
    found = []
    for proc in Path("/proc").iterdir():
        try:
            if (proc.name.isdigit() and b"flow_controller" in (proc / "cmdline").read_bytes()
                    and not any(e.startswith(b"XDG_STATE_HOME=") for e in (proc / "environ").read_bytes().split(b"\0"))
                    and (proc / "stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"):
                found.append(int(proc.name))
        except OSError:
            pass
    return found


def recovery():
    return json.loads(ipc("recovery_state"))



# Windows ---------------------------------------------------------------------------------------------------------

def ours():
    return [w for w in read("windows") if w.get("app_id") in NAMES]


def window(name):
    app = NEW if name == "new" else APPS[name]
    found = [w for w in read("windows") if w.get("app_id") == app]
    if len(found) != 1:
        raise Failed(f"{name}: {len(found)} windows of {app}, not one")
    return found[0]


def columns():
    """Every tiled window of the focused workspace, column by column from the first: [["mail"], ["studio", "notes"],
    ...]. A window that is not the test's shows as "other:" and its app ID, so its place counts too."""
    focused = next(w for w in read("workspaces") if w["is_focused"])
    placed = sorted((w["layout"]["pos_in_scrolling_layout"], NAMES.get(w.get("app_id"), f"other:{w.get('app_id')}"))
                    for w in read("windows") if w["workspace_id"] == focused["id"] and not w["is_floating"]
                    and w["layout"].get("pos_in_scrolling_layout"))
    found = {}
    for (column, _), name in placed:
        found.setdefault(column, []).append(name)
    if sorted(found) != list(range(1, len(found) + 1)):
        raise Failed(f"Niri reported columns {sorted(found)}, not 1 to {len(found)}")
    return [found[c] for c in sorted(found)]


def focused_name():
    ident = next(w for w in read("workspaces") if w["is_focused"])["active_window_id"]
    return next((NAMES[w["app_id"]] for w in ours() if w["id"] == ident), None)


def gio():
    """The gio Flow's installed helpers open apps with: the session's PATH has none."""
    wrapper = Path(shutil.which("flow-recovery")).read_text()
    found = re.search(r"/nix/store/[^/:\"]*-glib-[^/:\"]*/bin", wrapper)
    if not found:
        raise Failed("flow-recovery names no glib: its shape has changed")
    return found.group(0) + "/gio"


def launch(desktop):
    """Open an app from its desktop entry in an app scope of its own, as Flow opens apps (session/launching.py)."""
    subprocess.run(["systemd-run", "--user", "--scope", "--collect", "--quiet", "--", gio(), "launch", str(desktop)],
                   check=True, timeout=30, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


def desktop_file(app):
    found = [d / "applications" / f"{app}.desktop" for d in
             (applications().parent, *map(Path, os.environ.get("XDG_DATA_DIRS", "").split(":")))]
    return next((p for p in found if p.is_file()), None)


def open_app(name, timeout=30):
    app = NEW if name == "new" else APPS[name]
    launch(desktop_file(app))
    try:
        wait_for(lambda: any(w["app_id"] == app for w in read("windows")), f"a window of {app}", timeout=timeout)
    except Failed:
        # Nautilus's D-Bus activation once did nothing, a minute after its last instance had exited. The test's own
        # launches try again; recovery's launch after a restart gets no second try.
        print(json.dumps({"note": f"{app} did not open; launched again"}), flush=True)
        launch(desktop_file(app))
        wait_for(lambda: any(w["app_id"] == app for w in read("windows")), f"a window of {app}", timeout=timeout)
    wait_for(lambda: focused_name() == name, f"{name} to take focus", timeout=10)


def focus(name):
    niri("action", "focus-window", "--id", window(name)["id"])
    wait_for(lambda: focused_name() == name, f"focus on {name}")


def key(digit):
    """What Niri runs for Super+digit: the command the installed /etc/niri/config.kdl binds to it. Typing the key
    through Niri's virtual keyboard (wtype) does not reach Niri's bindings, which act on real input only."""
    line = next((l for l in Path("/etc/niri/config.kdl").read_text().splitlines()
                 if re.match(rf"\s*Super\+{digit}\s*{{\s*spawn ", l)), None)
    if not line:
        raise Failed(f"/etc/niri/config.kdl binds no spawn to Super+{digit}")
    command = re.findall(r'"([^"]*)"', line)
    if command[1:] != ["ipc", "-p", SHELL, "call", "desk", "focus", str(digit)]:
        raise Failed(f"Super+{digit} runs {command}, not the shell's desk focus {digit}")
    subprocess.run(command, check=True, timeout=10, stdout=subprocess.DEVNULL)


def spaces(*args):
    process = subprocess.run(["flow-spaces", *args], text=True, stdout=subprocess.PIPE, timeout=90)
    value = json.loads(process.stdout)
    if process.returncode or "error" in value:
        raise Failed(f"flow-spaces {args}: {value}")
    return value


def screenshot(run, name):
    """Niri's overview, once the shell shows the focused window's space active."""
    ident = next(w for w in read("workspaces") if w["is_focused"])["active_window_id"]
    wait_for(lambda: any(s["active"] and s["id"] == ident for s in json.loads(ipc("spaces_view"))["spaces"]),
             "the shell to show the focused window's space", timeout=10)
    # Niri's overview shows the whole row of columns; it opens, and the view scrolls, in animations.
    niri("action", "open-overview")
    time.sleep(1.5)
    subprocess.run(["grim", str(run / name)], check=False, timeout=30)
    niri("action", "close-overview")
    time.sleep(0.5)


# State files -----------------------------------------------------------------------------------------------------

def load(name):
    return json.loads((state_folder() / name).read_text())


def stray():
    return sorted(p.name for p in state_folder().iterdir() if p.name not in KNOWN)


def session_shape(data):
    """What a saved session must keep across a restart: apps in their columns, focus, Groups."""
    return {"windows": [(e.get("app_id"), e.get("column"), e.get("focused")) for e in data["windows"]],
            "groups": [(g.get("id"), g.get("label"), g.get("members"), g.get("layout", "stack"))
                       for g in data.get("groups", [])]}


def expected_columns(record):
    """The pins' columns first, in pin order, then the dynamic windows in their saved order."""
    return [["studio", "notes"] if "members" in pin else ["mail"] for pin in record["pins"]] + [["files"], ["scratch"]]


def expected_session(record):
    rows = [(APPS[name], index, name == "files") for index, column in enumerate(expected_columns(record), 1)
            for name in column]
    return {"windows": rows, "groups": [(record["group"]["id"], GROUP_LABEL, [APPS["studio"], APPS["notes"]], "stack")]}


def pin_identity(pin):
    """A pin without its binding to this Niri session's windows, and without its last focused member (check 6)."""
    return {k: v for k, v in pin.items() if k not in ("session", "window_id", "window_ids", "last_focused_app")}


def group_identity(group):
    """A Group without its binding to this Niri session's windows, and without its last focused member (check 6)."""
    return {k: v for k, v in group.items() if k not in ("bound", "last_focused_app")}


# The checks ------------------------------------------------------------------------------------------------------

def verify(how, record, session):
    """Checks 3 to 6 and 8 of the acceptance against the record's pins and Group, in Niri session ``session``."""
    want = expected_columns(record)
    wait_for(lambda: columns() == want, lambda: f"{how}: columns {want}, have {columns()}", timeout=30)
    check(f"{how}: the pins in columns 1 and 2 in pin order, then Files and Scratch, and no other window", True,
          str(columns()))
    windows = {name: window(name) for column in want for name in column}
    ids = {name: w["id"] for name, w in windows.items()}
    check(f"{how}: one window per app, all tiled on the focused workspace", True,
          ", ".join(f"{name} {ident}" for name, ident in ids.items()))
    check(f"{how}: focus on Files, as saved", focused_name() == "files", str(focused_name()))

    group_id = record["group"]["id"]
    groups = load("groups.json")["groups"]
    mine = [g for g in groups if set(g["members"]) & {APPS["studio"], APPS["notes"]}]
    check(f"{how}: one Group holds Studio and Notes", len(mine) == 1 and mine[0]["id"] == group_id,
          json.dumps(mine))
    group = mine[0]
    check(f"{how}: the Group keeps its ID, name and member order", group_identity(group) == record["group"],
          f"{group_identity(group)} (before: {record['group']})")
    bound = group.get("bound") or {}
    check(f"{how}: the Group is bound to the new windows in this Niri session",
          bound.get("session") == session and bound.get("windows") == {APPS["studio"]: ids["studio"],
                                                                     APPS["notes"]: ids["notes"]}, json.dumps(bound))

    pins = load("spaces.json")["pins"]
    check(f"{how}: the same two pins, in the same order", [pin_identity(p) for p in pins] == record["pins"],
          f"{[pin_identity(p) for p in pins]} (before: {record['pins']})")
    mail_pin = next(p for p in pins if "members" not in p)
    group_pin = next(p for p in pins if "members" in p)
    check(f"{how}: Mail's pin reaches the new Mail window in this Niri session",
          mail_pin.get("session") == session and mail_pin.get("window_id") == ids["mail"], json.dumps(mail_pin))
    check(f"{how}: the Group's pin reaches the new Group windows in this Niri session",
          group_pin.get("session") == session and group_pin.get("window_ids") == [ids["studio"], ids["notes"]],
          json.dumps(group_pin))

    def shown():
        view = json.loads(ipc("spaces_view"))
        return [(p["id"], p["window_ids"]) for p in view["pins"]]
    expected = [(p["id"], [ids["studio"], ids["notes"]] if "members" in p else [ids["mail"]]) for p in pins]
    wait_for(lambda: shown() == expected, lambda: f"{how}: the shell to show {expected}, it shows {shown()}")
    check(f"{how}: the shell shows both pins with their windows", True)
    check(f"{how}: no Flow error in the shell", ipc("spaces_error") == "", ipc("spaces_error"))

    # Check 6: what Super+1 and Super+2 run. The Group's shortcut reaches its last focused member, Notes.
    reached = []
    for digit, pin in enumerate(pins, 1):
        name = "notes" if "members" in pin else "mail"
        key(digit)
        wait_for(lambda: focused_name() == name, lambda: f"{how}: Super+{digit} to focus {name}, focus is on "
                                                         f"{focused_name()}", timeout=10)
        reached.append(f"Super+{digit}: {name}")
    check(f"{how}: each pin's shortcut reaches it (Mail; the Group's last focused member, Notes)", True,
          ", ".join(reached))
    focus("files")

    for name in STATE_FILES:
        try:
            load(name)
        except ValueError as exc:
            raise Failed(f"{how}: {name} is not valid JSON: {exc}") from None
    check(f"{how}: spaces.json, groups.json and session.json parse", True)
    check(f"{how}: no temporary file in the state folder", not stray(), str(stray()))
    saved = wait_for(lambda: session_shape(load("session.json")) == expected_session(record)
                     and load("session.json"),
                     lambda: f"{how}: session.json to hold {expected_session(record)}, it holds "
                             f"{session_shape(load('session.json'))}", timeout=60)
    check(f"{how}: session.json holds the five apps in their columns and the Group", True)
    return ids, saved


def new_window_follows(how, record):
    """Check 7: a new window put between the two pins ends up after them. Closed again, and saved without it."""
    before = expected_columns(record)
    focus(before[0][-1])
    open_app("new")
    # Wherever Niri placed it, put it between the pins, as Niri's default placement (right of the focused column) does.
    niri("action", "move-column-to-index", 2)
    want = before[:2] + [["new"]] + before[2:]
    wait_for(lambda: columns() == want, lambda: f"{how}: the new window after the pins: {want}, have {columns()}",
             timeout=15)
    check(f"{how}: a new window put between the pins ends up after them", True, str(columns()))
    focus("files")
    niri("action", "close-window", "--id", window("new")["id"])
    wait_for(lambda: not any(w["app_id"] == NEW for w in read("windows")), "the new window to close")
    wait_for(lambda: columns() == before and focused_name() == "files", "the columns from before")


# Phases ----------------------------------------------------------------------------------------------------------

def write_record(run, record):
    path = run / "record.json"
    path.with_suffix(".tmp").write_text(json.dumps(record, indent=1))
    path.with_suffix(".tmp").replace(path)


def read_record(run):
    return json.loads((run / "record.json").read_text())


def stop_shell():
    subprocess.run(["systemctl", "--user", "stop", "flow-shell"], check=False, timeout=60)
    end = time.monotonic() + 20
    while controllers() and time.monotonic() < end:
        time.sleep(0.2)
    for pid in controllers():  # a controller the unit's stop left must not write over what is put back
        os.kill(pid, signal.SIGKILL)


def prepare(run, mail):
    mail = Path(mail).resolve() / "bin" / "thunderbird"
    if (run / "record.json").exists():
        raise Failed(f"{run} holds an earlier run: put it back first (put-back {run})")
    if subprocess.run(["systemctl", "--user", "is-active", "--quiet", "flow-shell"]).returncode:
        raise Failed("The installed shell, the flow-shell unit, must be running")
    if read("windows"):
        raise Failed("No window may be open: the reboot would close it")
    if len([o for o in read("outputs").values() if o.get("current_mode") is not None]) != 1:
        raise Failed("Connect one display only: F-003 is about Main")
    if not os.access(mail, os.X_OK):
        raise Failed(f"No Thunderbird at {mail}")
    entries = {app: applications() / f"{app}.desktop" for app in (*APPS.values(), NEW) if app != APPS["files"]}
    if any(p.exists() for p in entries.values()) or not desktop_file(APPS["files"]):
        raise Failed("A test desktop entry exists already, or Files (Nautilus) is not installed")
    state, settings = state_folder(), settings_file()
    run.mkdir(parents=True, exist_ok=True)
    start = next(w for w in read("workspaces") if w["is_focused"])
    record = {"phase": "backing up", "created": time.time(), "boot": boot_id(), "session": niri_session(),
              "state": str(state), "state_existed": state.exists(), "settings": str(settings),
              "settings_existed": settings.exists(), "entries": [str(p) for p in entries.values()],
              "markers": sorted(p.name for p in runtime().glob("flow-recovery-*")),
              "start": [start["output"], start["idx"]]}
    write_record(run, record)
    stop_shell()
    backup = run / "backup"
    shutil.rmtree(backup, ignore_errors=True)
    backup.mkdir()
    if state.exists():
        shutil.copytree(state, backup / "flow", symlinks=True)
    if settings.exists():
        shutil.copy2(settings, backup / "flow-settings.ini")
    for name in record["markers"]:
        shutil.copy2(runtime() / name, backup / name)
    record["phase"] = "backed up"  # from here on put-back restores
    write_record(run, record)

    shutil.rmtree(state, ignore_errors=True)
    state.mkdir(mode=0o700, parents=True)
    text = settings.read_text() if settings.exists() else "[General]\n"
    text = re.sub(r"(?m)^autoRecovery=.*\n?", "", text)
    text = text.replace("[General]\n", "[General]\nautoRecovery=true\n", 1) if "[General]\n" in text \
        else "[General]\nautoRecovery=true\n" + text
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(text)
    profile = run / "thunderbird-profile"
    profile.mkdir(exist_ok=True)
    (profile / "user.js").write_text(THUNDERBIRD_PREFS)
    applications().mkdir(parents=True, exist_ok=True)
    # Thunderbird's own entry, with the test's profile; the stand-ins run cat, so their titles never change.
    entries_text = {
        APPS["mail"]: f"Name=Thunderbird\nExec={mail} --name thunderbird --profile {profile} %U\n"
                      "StartupWMClass=thunderbird\n",
        APPS["studio"]: f"Name=Studio\nExec=foot --app-id {APPS['studio']} --title Studio cat\n",
        APPS["notes"]: f"Name=Notes\nExec=foot --app-id {APPS['notes']} --title Notes cat\n",
        APPS["scratch"]: f"Name=Scratch\nExec=foot --app-id {APPS['scratch']} --title Scratch cat\n",
        NEW: f"Name=New window\nExec=foot --app-id {NEW} --title New cat\n"}
    for app, path in entries.items():
        path.write_text("[Desktop Entry]\nType=Application\n" + entries_text[app])

    subprocess.run(["systemctl", "--user", "start", "flow-shell"], check=True, timeout=60)
    wait_for_session(timeout=60)
    # A session start with automatic recovery on and nothing saved: auto-start finds no session.json and is ready.
    wait_for(lambda: (lambda r: r["setting"] and not r["paused"] and not r["working"])(recovery()),
             lambda: f"automatic recovery ready: {recovery()}", timeout=30)
    check("prepare: the installed shell runs with automatic recovery on, ready", True)

    for name in ("mail", "studio", "notes", "files", "scratch"):
        open_app(name)
    wait_for(lambda: columns() == [[n] for n in ("mail", "studio", "notes", "files", "scratch")],
             lambda: f"the five apps in a row, have {columns()}")
    focus("studio")
    spaces("stack")
    wait_for(lambda: columns() == COLUMNS, lambda: f"Studio over Notes, have {columns()}")
    group_id = next(s["group_id"] for s in spaces("create-group")["spaces"] if s.get("group_id"))
    renamed = subprocess.run(["flow-recovery", "rename-group", group_id, GROUP_LABEL], text=True,
                             stdout=subprocess.PIPE, timeout=60)
    if renamed.returncode or "error" in json.loads(renamed.stdout):
        raise Failed(f"rename-group: {renamed.stdout}")
    focus("mail")
    spaces("pin")
    spaces("pin-group", group_id)
    focus("notes")
    wait_for(lambda: next(g for g in load("groups.json")["groups"] if g["id"] == group_id).get("last_focused_app")
             == APPS["notes"], "the Group to remember Notes as focused")
    focus("files")
    spaces("refresh")

    record["group"] = group_identity(next(g for g in load("groups.json")["groups"] if g["id"] == group_id))
    record["pins"] = [pin_identity(p) for p in load("spaces.json")["pins"]]
    displays = load("spaces.json")["displays"]
    check("prepare: both pins belong to Main (display number 0)",
          all(displays.get(p.get("display"), {}).get("number") == 0 for p in record["pins"]), json.dumps(displays))
    check("prepare: the Group was made from Studio and Notes, in that order",
          record["group"].get("members") == [APPS["studio"], APPS["notes"]] and record["group"]["label"] == GROUP_LABEL,
          json.dumps(record["group"]))
    check("prepare: two pins, Mail then the Group",
          [p.get("app_id") for p in record["pins"]] == [APPS["mail"], APPS["studio"]]
          and record["pins"][1].get("group_id") == group_id, json.dumps(record["pins"]))
    # The controller saves 2 s after a refresh, and no sooner than 30 s after its previous save.
    wait_for(lambda: (state_folder() / "session.json").exists()
             and session_shape(load("session.json")) == expected_session(record),
             lambda: "the automatic save of the session: " + (
                 json.dumps(session_shape(load("session.json"))) if (state_folder() / "session.json").exists()
                 else "none yet"), timeout=90, step=0.5)
    check("prepare: the controller's automatic save holds the session", True)
    verify("before the restarts", record, niri_session())
    check("prepare: automatic recovery still on and not paused",
          (lambda r: r["setting"] and not r["paused"])(recovery()), json.dumps(recovery()))
    screenshot(run, "1-before.png")
    record["phase"] = "prepared"
    record["checked"] = {"boot": boot_id(), "session": niri_session()}
    write_record(run, record)
    thunderbird = subprocess.run([str(mail), "--version"], text=True, stdout=subprocess.PIPE, timeout=60).stdout
    return {"result": "prepared", "group": group_id, "mail": thunderbird.strip()}


def reboot(run):
    record = read_record(run)
    if record["phase"] not in ("prepared", "checked reboot", "checked relogin"):
        raise Failed(f"Not ready for a reboot: {record['phase']}")
    record["phase"] = "rebooting"
    write_record(run, record)
    print(json.dumps({"rebooting": boot_id()}), flush=True)
    subprocess.run(["sudo", "-n", "systemctl", "reboot"], check=True, timeout=60)
    return {"result": "rebooting"}


def swap_pins(record):
    """The two pins change places while no shell runs, so the saved session's order is no longer the pin order: as
    after reordering pins and restarting before the next automatic save."""
    stop_shell()
    path = state_folder() / "spaces.json"
    data = json.loads(path.read_text())
    data["pins"].reverse()
    temporary = path.with_name(".f003-swap.json")
    temporary.write_text(json.dumps(data))
    temporary.replace(path)
    record["pins"].reverse()
    check("logout: the pins swapped, the Group first; the saved session still has Mail first",
          [p["id"] for p in load("spaces.json")["pins"]] == [p["id"] for p in record["pins"]]
          and load("session.json")["windows"][0]["app_id"] == APPS["mail"])


def logout(run, swap=False):
    record = read_record(run)
    if record["phase"] not in ("prepared", "checked reboot", "checked relogin"):
        raise Failed(f"Not ready for a log out: {record['phase']}")
    before = niri_session()
    if swap:
        swap_pins(record)
    record["phase"] = "logging out"
    write_record(run, record)
    # Niri exits before it answers, so the call fails with an empty reply: what ended is checked below.
    subprocess.run(["niri", "msg", "action", "quit", "--skip-confirmation"], timeout=10,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_for(lambda: subprocess.run(["systemctl", "--user", "is-active", "--quiet", "niri"]).returncode != 0,
             "the Niri session to end", timeout=60, step=0.5)
    check("logout: the Niri session ended", True)
    # SDDM logs in automatically when it starts; its greeter does not (Relogin=false), and floe has no password.
    subprocess.run(["sudo", "-n", "systemctl", "restart", "display-manager"], check=True, timeout=60)

    def new_session():
        try:
            session_environment()
            return niri_session() != before
        except (Failed, OSError, subprocess.SubprocessError):
            return False
    wait_for(new_session, "a new Niri session", timeout=120, step=1)
    check("logout: logged in again, a new Niri session", True)
    return {"result": "logged in again"}


def check_after(run, how):
    record = read_record(run)
    expected = {"reboot": "rebooting", "relogin": "logging out"}[how]
    if record["phase"] != expected:
        raise Failed(f"The run's phase is {record['phase']!r}, not {expected!r}")
    started = time.monotonic()
    session = niri_session()
    if how == "reboot":
        check("reboot: a new boot", boot_id() != record["checked"]["boot"], boot_id())
    else:
        check("relogin: the same boot, a new Niri session",
              boot_id() == record["checked"]["boot"] and session != record["checked"]["session"], session)
    system = os.readlink("/run/current-system")
    check(f"{how}: the installed shell runs", True, system)

    def recovered():
        state = recovery()
        if state["paused"] and state["message"] and not state["working"]:
            raise Failed(f"{how}: automatic recovery paused: {state['message']}")
        return state["setting"] and not state["paused"] and not state["working"] and state
    # Not paused: the shell starts paused and un-pauses only when auto-start succeeds, having restored every saved
    # window. Its message is soon an automatic save's, so the message itself is not checked.
    state = wait_for(recovered, lambda: f"{how}: automatic recovery to finish: {recovery()}", timeout=120, step=0.5)
    check(f"{how}: automatic recovery finished on its own", True,
          f"{time.monotonic() - started:.0f} s after this check started; {state['message']}")
    verify(how, record, session)
    screenshot(run, {"reboot": "2-after-reboot.png", "relogin": "3-after-relogin.png"}[how])
    new_window_follows(how, record)
    wait_for(lambda: session_shape(load("session.json")) == expected_session(record),
             lambda: f"{how}: session.json without the new window: {session_shape(load('session.json'))}",
             timeout=60, step=0.5)
    check(f"{how}: saved again without the new window", True)
    check(f"{how}: automatic recovery on and not paused", (lambda r: r["setting"] and not r["paused"])(recovery()),
          json.dumps(recovery()))
    record["phase"] = f"checked {how}"
    record["checked"] = {"boot": boot_id(), "session": session}
    write_record(run, record)
    return {"result": "pass", "after": how, "system": system}


def test_windows(run):
    """The windows the test opened: its own app IDs, Thunderbird with its profile in ``run``, and Files only while
    it has one window (the test's; nobody else's opens during a run, which starts with none)."""
    def cmdline(pid):
        try:
            return Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            return b""
    windows = read("windows")
    files = [w for w in windows if w.get("app_id") == APPS["files"]]
    return [w for w in windows
            if (w.get("app_id") in NAMES and w["app_id"] not in (APPS["mail"], APPS["files"]))
            or (w.get("app_id") == APPS["mail"] and str(run).encode() in cmdline(w.get("pid")))
            or (w.get("app_id") == APPS["files"] and len(files) == 1)]


def write_marker(state):
    """Automatic recovery's marker for this Niri session (session/restore.py automatic)."""
    name = "flow-recovery-" + hashlib.sha256(niri_session().encode()).hexdigest()
    (runtime() / name).write_text(state)


def put_back(run):
    """Close the test windows; the real state, settings, desktop entries and markers back, byte for byte."""
    record = read_record(run)
    if record["phase"] == "put back":
        return {"result": "already put back"}  # a second put-back must not restore an old backup over newer state
    restored = False
    try:
        for w in test_windows(run):
            niri("action", "close-window", "--id", w["id"])
        try:
            wait_for(lambda: not test_windows(run), "the test windows to close", timeout=20)
        except Failed:
            for w in test_windows(run):  # an app that asks before it closes; never Files, which serves others
                if w["app_id"] != APPS["files"]:
                    os.kill(w["pid"], signal.SIGTERM)
        if record["phase"] == "backing up":
            return {"result": "nothing was changed"}  # the backup never finished; nothing was touched
        stop_shell()
        backup, state, settings = run / "backup", Path(record["state"]), Path(record["settings"])
        shutil.rmtree(state, ignore_errors=True)
        if record["state_existed"]:
            shutil.copytree(backup / "flow", state, symlinks=True)
        if record["settings_existed"]:
            shutil.copy2(backup / "flow-settings.ini", settings)
        else:
            settings.unlink(missing_ok=True)
        for entry in record["entries"]:
            Path(entry).unlink(missing_ok=True)
        for path in runtime().glob("flow-recovery-*"):
            if path.name not in record["markers"]:
                path.unlink()  # automatic recovery's markers for the test's sessions
        if record["boot"] == boot_id():  # after a reboot the old markers name sessions that are gone
            for name in record["markers"]:
                if (backup / name).exists():
                    shutil.copy2(backup / name, runtime() / name)
        if (record["settings_existed"] and re.search(r"(?m)^autoRecovery=true$", settings.read_text())
                and niri_session() != record["session"]):
            # With the real setting on, a shell starting in this new Niri session with no marker would restore the
            # real saved session now. Paused instead: the user reviews it, as after any recovery that needs review.
            write_marker("blocked")
        files = sorted(p.relative_to(state) for p in state.rglob("*")) if state.exists() else None
        saved = sorted(p.relative_to(backup / "flow") for p in (backup / "flow").rglob("*")) \
            if record["state_existed"] else None
        restored = (files == saved
                    and all((state / p).read_bytes() == (backup / "flow" / p).read_bytes()
                            for p in files or () if (state / p).is_file())
                    and (settings.read_bytes() == (backup / "flow-settings.ini").read_bytes()
                         if record["settings_existed"] else not settings.exists())
                    and not any(Path(e).exists() for e in record["entries"]))
    finally:
        subprocess.run(["systemctl", "--user", "start", "flow-shell"], check=False, timeout=60)
        check("put-back: the real state, settings and desktop entries back byte for byte; the shell started",
              restored or record["phase"] == "backing up")
    if restored:
        record["phase"] = "put back"
        write_record(run, record)
    return {"result": "put back"}


def held(path, timeout=1800):
    """The guest's screen lock, held until the process exits."""
    handle = open(path, "a")
    end = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except BlockingIOError:
            if time.monotonic() >= end:
                raise Failed(f"The screen lock {path} stayed taken for {timeout} s") from None
            time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--screen-lock", help="hold this lock file while the phase runs")
    phases = parser.add_subparsers(dest="phase", required=True)
    for name in ("prepare", "reboot", "logout", "check", "put-back"):
        phase = phases.add_parser(name)
        phase.add_argument("run", type=Path)
        if name == "prepare":
            phase.add_argument("--mail", required=True, help="Thunderbird's store path")
        if name == "check":
            phase.add_argument("--after", required=True, choices=("reboot", "relogin"))
        if name == "logout":
            phase.add_argument("--swap-pins", action="store_true", help="swap the two pins first, with the shell stopped")
    args = parser.parse_args()
    lock = held(args.screen_lock) if args.screen_lock else None
    if args.phase == "check":
        wait_for_session()
    else:
        session_environment()
    run = args.run.expanduser().resolve()
    result = {"prepare": lambda: prepare(run, args.mail), "reboot": lambda: reboot(run),
              "logout": lambda: logout(run, args.swap_pins), "check": lambda: check_after(run, args.after),
              "put-back": lambda: put_back(run)}[args.phase]()
    print(json.dumps({**result, "niri": niri("version").strip()}), flush=True)
    del lock


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, StopIteration, subprocess.SubprocessError, ValueError, KeyError) as exc:
        print(f"F-003 reboot test failed: {exc}", file=sys.stderr)
        sys.exit(1)
