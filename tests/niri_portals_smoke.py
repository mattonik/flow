#!/usr/bin/env python3
"""F-012 on the installed system: file chooser, screenshots, screen sharing, clipboard and drag and drop, in real
apps, through the portals and backends Flow installs (see *F-012 acceptance* in docs/IMPLEMENTATION_STATUS.md).

Run it with ``tests/run_portals_smoke.sh`` in the guest: it builds the test apps, takes the guest's screen lock and
gives the run the Niri session's environment. The installed shell keeps running: the portals' dialogs open over it
as they would for a user, and the pointer (``tests/pointer.py``, ``flow-pointer``) answers them where the dialog
draws its button. Keys go through wtype to the focused Wayland app. ``--only NAME`` runs one check (it may be
given more than once); ``--shots DIR`` keeps a screenshot (grim) of each dialog and result.

Checks, in order (names for ``--only``):

- ``chooser``: PortalProbe (``tests/flatpak/portal_probe.py``), a Flatpak with no filesystem access, asks
  FileChooser to open a file and to save one, in Nautilus's chooser (GNOME's backend), answered by keyboard:
  Escape cancels, the file's path typed in its location bar and Return opens it, Return saves under the
  suggested name. The probe reads and writes the chosen file through the document portal and nothing
  else: not the file beside it, not the original path, not the home folder. Cancel answers 1. Firefox's upload
  button, through the portal as a Flatpak Firefox asks, opens the chooser floating over Firefox, and the chosen
  file reaches the page.
- ``screenshot``: PortalProbe asks Screenshot for the screen. Its consent dialog (the Access backend) is denied
  once (answer 2, no file, and the next request is refused without asking), then allowed: the file the probe
  gets is the live screen, at the output's size, showing a window of a colour picked for this run. An interactive
  request (as GIMP sends) asks through Flow's backend: Cancel answers 1, Take Screenshot gives the live screen
  without the dialog. PickColor answers the colour under a click.
- ``screenshot-gimp``: GIMP 3's screenshot plug-in (what File > Create > Screenshot runs, here as a batch call),
  which always asks with ``interactive: true``: Cancel gives GIMP no image and leaves no file; Take Screenshot
  gives it the live screen.
- ``screencast``: Firefox's ``getDisplayMedia`` on a local page. Cancelling the portal's dialog refuses the page.
  Sharing gives a PipeWire stream that Niri lists, whose frames show the screen. It ends when the page stops it,
  and when it is stopped outside the app with Niri's ``stop-cast`` (what a key binding or a Flow control runs);
  then the page's track ends and Niri lists no cast.
- ``dnd-file``: a file dragged from Files (Nautilus) onto the page arrives with its name and content.
- ``dnd-text``: text dragged from Kate onto the page arrives as ``text/plain``.
- ``clipboard-text``: text copied by Firefox's page (GTK 3, Wayland) is pasted and saved in Kate (Qt 6, Wayland);
  Kate's is pasted in xterm (X11, through xwayland-satellite), which writes it to a file; xterm's is read by the
  page. GTK's own text widgets are not driven: they drop wtype's chords (see the check).
- ``clipboard-image``: an image copied in KolourPaint (Qt, Wayland) is pasted into Firefox's page (GTK, Wayland);
  the page's is pasted and saved in KolourPaint and read by Firefox on X11 (``MOZ_ENABLE_WAYLAND=0``); that one's is
  read by the Wayland page. Each image has the colour its copy had.
- ``chooser-pointer``: the same, answered with the pointer where Nautilus draws its buttons (the close button, the
  file, Open, Save), each 50 ms after the pointer arrives: Open and Save sit under the shell's work indicator, which
  fades and lets the click through as soon as the pointer comes onto it (``edges``, ADR-0013).
- ``backends``: every portal request above reached the backend Flow intends (from a D-Bus monitor of the
  ``org.freedesktop.impl.portal.*`` calls): FileChooser and ScreenCast the GNOME backend, Access the GTK one,
  Screenshot Flow's own (``session/screenshot_portal.py``). PipeWire and WirePlumber run.
- ``edges``: a full-window Firefox page (kiosk) has buttons along its window's top and bottom edges: left,
  middle and right. Each must take a click 50 ms after the pointer arrives, though the island (top middle), the home
  indicator (bottom middle) and the work indicator (bottom right) sit over the window: the shell's bands fade and
  let it through (ADR-0013). Each of those bands, reached on purpose by its screen edge or by Super+B's IPC call,
  must take a click again: the island's wordmark opens Desk, the home indicator Overview.

The run exits 0 when every check passes but those in ``EXPECTED_FAILURES`` (none), and fails when any other check
fails or when one of those passes.

X11 windows get no keys: xwayland-satellite does not take wtype's virtual keymap, so xterm and Firefox on X11 are
driven by the pointer only (a middle click pastes, a double click copies, page buttons copy and read).

What it touches and puts back: the test's own windows, on an empty workspace of their own (the focused workspace
is focused again at the end); PortalProbe, uninstalled with its data, and the documents it was given; the
permission store's screenshot entries; screenshots it caused in ``~/Pictures/Screenshots``; GTK's file-chooser and
Nautilus's dconf keys; ``recently-used.xbel``; and Flow's saved session and recovery markers, which the installed
shell's automatic save would otherwise replace with the test's windows (the shell is stopped to put them back, then
started). Firefox, Kate, KolourPaint and GIMP run with configuration and data folders of their own.
The clipboard is left holding the test's last copy.
"""

import argparse
import http.server
import json
import os
from pathlib import Path
import queue
import random
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pointer import Pointer, center  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROBE = "io.github.mattonik.PortalProbe"
RUNTIME, BRANCH = "org.freedesktop.Platform", "25.08"
# The clipboard checks run before the drag checks. Run after dnd-text, clipboard-text found Kate on its welcome page
# instead of the file it had just opened, every time in the guest, though each check's apps are ended and windows
# closed in between, every Kate gets folders of its own, and the dragged file is read-only. The cause is not known;
# each check passes alone and in this order.
CHECKS = ["chooser", "screenshot", "screenshot-gimp", "screencast", "clipboard-text", "clipboard-image", "dnd-file",
          "dnd-text", "chooser-pointer", "backends", "edges"]
# Checks expected to fail. The run fails if any other check fails, or if one of these passes (update this list and
# the status document). Empty since the shell's bands get out of apps' way (ADR-0013).
EXPECTED_FAILURES = set()
# The backend Flow intends for each interface the checks use (modules/flow.nix, ADR-0005).
INTENDED = {"FileChooser": "gnome", "Access": "gtk", "Screenshot": "flow", "ScreenCast": "gnome"}
PERMISSIONS = ("org.freedesktop.impl.portal.PermissionStore", "/org/freedesktop/impl/portal/PermissionStore",
               "org.freedesktop.impl.portal.PermissionStore")
DOCUMENTS = ("org.freedesktop.portal.Documents", "/org/freedesktop/portal/documents", "org.freedesktop.portal.Documents")
DCONF_DIRS = ("/org/gtk/settings/file-chooser/", "/org/gnome/nautilus/")


class Failed(AssertionError):
    pass


def check(condition, message):
    if not condition:
        raise Failed(message)


def niri(*args):
    return subprocess.check_output(["niri", "msg", *map(str, args)], text=True, timeout=10)


def read(kind):
    return json.loads(niri("-j", kind))


def windows():
    return {w["id"]: w for w in read("windows")}


def focused_workspace():
    return next(w for w in read("workspaces") if w["is_focused"])


def wait_for(test, label, timeout=15, every=0.2):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = test()
        if value:
            return value
        time.sleep(every)
    raise Failed(f"Timed out waiting for {label() if callable(label) else label}")


def run(*command, timeout=120, **kw):
    process = subprocess.run(list(map(str, command)), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             timeout=timeout, **kw)
    if process.returncode:
        raise RuntimeError(f"{' '.join(map(str, command[:4]))} failed: {process.stderr.strip()[-600:]}")
    return process.stdout


NAMED_KEYS = {"Return", "Escape", "Tab", "BackSpace", "Home", "End", "Up", "Down", "Left", "Right"}


def keys(*steps):
    """wtype, to the focused Wayland window: ``"ctrl+a"`` presses a chord, ``"Return"`` (and the other names in
    ``NAMED_KEYS``) that key, anything else is typed as text."""
    for step in steps:
        if step in NAMED_KEYS:
            args = ["-k", step]
        elif re.fullmatch(r"(ctrl|shift|alt)(\+(ctrl|shift|alt))*\+\S+", step):
            *mods, key = step.split("+")
            args = [a for m in mods for a in ("-M", m)] + (["-k", key] if len(key) > 1 else [key]) \
                + [a for m in reversed(mods) for a in ("-m", m)]
        else:
            args = [step]
        run("wtype", *args, timeout=15)
        time.sleep(0.25)


# --- PNG, read and written with the standard library -----------------------------------------------------------

def png(width, height, rgb):
    raw = b"".join(b"\0" + bytes(rgb) * width for _ in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class Image:
    """An 8-bit RGB or RGBA PNG, decoded: what grim, Niri, Firefox's canvas and KolourPaint write."""

    def __init__(self, data):
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError("not a PNG")
        pos, idat, header = 8, b"", None
        while pos < len(data):
            size, kind = struct.unpack(">I", data[pos:pos + 4])[0], data[pos + 4:pos + 8]
            body = data[pos + 8:pos + 8 + size]
            pos += 12 + size
            if kind == b"IHDR":
                header = struct.unpack(">IIBBBBB", body)
            elif kind == b"IDAT":
                idat += body
            elif kind == b"IEND":
                break
        self.width, self.height, depth, colour, _, _, interlace = header
        if depth != 8 or colour not in (2, 6) or interlace:
            raise ValueError(f"unsupported PNG: depth {depth}, colour type {colour}, interlace {interlace}")
        self.bpp = 3 if colour == 2 else 4
        stride, raw, previous, self.rows = self.width * self.bpp, zlib.decompress(idat), bytearray(self.width * self.bpp), []
        bpp, offset = self.bpp, 0
        for _ in range(self.height):
            kind, line = raw[offset], bytearray(raw[offset + 1:offset + 1 + stride])
            offset += 1 + stride
            if kind == 1:
                for x in range(bpp, stride):
                    line[x] = (line[x] + line[x - bpp]) & 255
            elif kind == 2:
                for x in range(stride):
                    line[x] = (line[x] + previous[x]) & 255
            elif kind == 3:
                for x in range(stride):
                    line[x] = (line[x] + (((line[x - bpp] if x >= bpp else 0) + previous[x]) >> 1)) & 255
            elif kind == 4:
                for x in range(stride):
                    a, b, c = (line[x - bpp] if x >= bpp else 0), previous[x], (previous[x - bpp] if x >= bpp else 0)
                    p = a + b - c
                    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                    line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
            self.rows.append(bytes(line))
            previous = line

    def pixel(self, x, y):
        at = x * self.bpp
        return tuple(self.rows[y][at:at + 3])


def close(a, b, tolerance=10):
    return all(abs(p - q) <= tolerance for p, q in zip(a, b))


def colour_hex(rgb):
    return "%02x%02x%02x" % tuple(rgb)


def fresh_colour():
    """A saturated colour no theme uses, different each run, so an old file cannot pass for a new one."""
    rng = random.Random(uuid.uuid4().int)
    while True:
        rgb = tuple(rng.choice((rng.randrange(20, 70), rng.randrange(170, 235))) for _ in range(3))
        if len(set(rgb)) > 1:
            return rgb


def screen_png(path):
    run("grim", path, timeout=30)
    return Image(Path(path).read_bytes())


# --- The user's state, put back at the end ----------------------------------------------------------------------

def busctl(*args, timeout=15):
    return run("busctl", "--user", "--json=short", *args, timeout=timeout)


class UserState:
    """What the test changes outside its own folder, recorded first and put back however the run ended."""

    def __init__(self):
        state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "flow"
        self.runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
        self.flow = {p: p.read_bytes() for p in state.glob("*") if p.is_file() and not p.name.endswith(".lock")}
        self.flow_dir = state
        self.markers = {p: p.read_bytes() for p in self.runtime.glob("flow-recovery-*")
                        if p.is_file() and not p.name.endswith(".lock")}
        self.recent = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "recently-used.xbel"
        self.recent_bytes = self.recent.read_bytes() if self.recent.exists() else None
        self.dconf = {d: run("dconf", "dump", d) for d in DCONF_DIRS}
        # Where GNOME's backend saves them (its Pictures folder; the guest has no xdg-user-dir to ask).
        self.pictures = Path(os.environ.get("XDG_PICTURES_DIR") or Path.home() / "Pictures") / "Screenshots"
        self.shots = set(self.pictures.glob("*")) if self.pictures.exists() else set()
        self.screenshot_permissions = self.permissions()

    @staticmethod
    def permissions():
        """The permission store's ``screenshot`` entry, by app: ``{app: [permissions]}``."""
        try:
            out = json.loads(busctl("call", *PERMISSIONS, "Lookup", "ss", "screenshot", "screenshot"))
            return out["data"][0]
        except (RuntimeError, KeyError, IndexError, ValueError):
            return {}  # no such table or entry yet

    def set_permissions(self, wanted):
        now = self.permissions()
        for app in set(now) | set(wanted):
            if app in wanted and now.get(app) != wanted[app]:
                busctl("call", *PERMISSIONS, "SetPermission", "sbssas", "screenshot", "true", "screenshot", app,
                       str(len(wanted[app])), *wanted[app])
            elif app not in wanted:
                busctl("call", *PERMISSIONS, "DeletePermission", "sss", "screenshot", "screenshot", app)

    def new_screenshots(self):
        return sorted(set(self.pictures.glob("*")) - self.shots) if self.pictures.exists() else []

    def put_back(self):
        problems = []
        for path in self.new_screenshots():
            path.unlink()
        try:
            self.set_permissions(self.screenshot_permissions)
        except RuntimeError as exc:
            problems.append(f"screenshot permissions: {exc}")
        for directory, dump in self.dconf.items():
            if run("dconf", "dump", directory) != dump:
                run("dconf", "reset", "-f", directory)
                subprocess.run(["dconf", "load", directory], input=dump, text=True, check=True, timeout=15)
        if self.recent_bytes is not None and self.recent.read_bytes() != self.recent_bytes:
            self.recent.write_bytes(self.recent_bytes)
        changed = ({p for p in self.flow_dir.glob("*") if p.is_file() and not p.name.endswith(".lock")
                    and self.flow.get(p) != p.read_bytes()}
                   | {p for p in self.runtime.glob("flow-recovery-*") if p.is_file() and not p.name.endswith(".lock")
                      and self.markers.get(p) != p.read_bytes()})
        if changed:
            # The controller's automatic save would write again while the shell runs.
            subprocess.run(["systemctl", "--user", "stop", "flow-shell"], timeout=60)
            try:
                for path in changed:
                    before = self.flow.get(path, self.markers.get(path))
                    if before is None:
                        path.unlink()
                    else:
                        path.write_bytes(before)
                same = all(p.read_bytes() == b for p, b in {**self.flow, **self.markers}.items())
                if not same:
                    problems.append("Flow's state or markers differ after putting them back")
            finally:
                subprocess.run(["systemctl", "--user", "start", "flow-shell"], timeout=60)
        return problems


# --- Apps ---------------------------------------------------------------------------------------------------------

class Apps:
    """The test's windows, on a workspace of their own; every process it starts ends with it."""

    def __init__(self, tmp):
        self.tmp, self.processes, self.own = Path(tmp), [], set()
        self.start_workspace = focused_workspace()
        output = self.start_workspace["output"]
        busy = {w["workspace_id"] for w in windows().values()}
        empty = [w for w in read("workspaces") if w["output"] == output and w["id"] not in busy]
        check(empty, "No empty workspace on the focused display")
        self.workspace = max(empty, key=lambda w: w["idx"])
        niri("action", "focus-workspace", self.workspace["idx"])
        wait_for(lambda: focused_workspace()["id"] == self.workspace["id"], "the test's workspace")
        self.output = read("outputs")[output]["logical"]

    def fresh_home(self):
        """Configuration, data, cache and state folders of their own for one app start: a second Kate started on
        the first one's folders showed its welcome page instead of the file it was given."""
        home = self.tmp / "app-home" / uuid.uuid4().hex[:8]
        folders = {name: str(home / name.lower()) for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
                                                                "XDG_STATE_HOME")}
        for folder in folders.values():
            Path(folder).mkdir(parents=True)
        return folders

    def start(self, command, app_id=None, title=None, env=None, isolated=True, timeout=30):
        """Starts an app and waits for its first new window (matching ``app_id``/``title`` when given)."""
        before = set(windows())
        full_env = {**os.environ, **(self.fresh_home() if isolated else {}), **(env or {})}
        process = subprocess.Popen(list(map(str, command)), env=full_env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=open(self.tmp / "apps.log", "a"),
                                   start_new_session=True)
        self.processes.append(process)
        window = self.new_window(before, app_id, title, timeout, f"a window of {command[0]}")
        return process, window

    def new_window(self, before, app_id=None, title=None, timeout=30, label="a new window"):
        def found():
            for w in windows().values():
                if w["id"] not in before and (app_id is None or w["app_id"] == app_id) \
                        and (title is None or re.search(title, w["title"] or "")):
                    return w
        window = wait_for(found, label, timeout)
        self.own.add(window["id"])
        return window

    def focus(self, window, width=None):
        niri("action", "focus-window", "--id", window["id"])
        wait_for(lambda: windows().get(window["id"], {}).get("is_focused"), f"focus on window {window['id']}")
        if width:
            niri("action", "set-column-width", width)
        time.sleep(0.4)

    def side_by_side(self, left, right):
        """``left`` and ``right`` as two half-width columns, left first, filling the screen."""
        for w in (left, right):
            self.focus(w, "50%")
        niri("action", "focus-window", "--id", right["id"])
        niri("action", "move-column-to-first")
        niri("action", "focus-window", "--id", left["id"])
        niri("action", "move-column-to-first")
        time.sleep(0.6)
        check(windows()[left["id"]]["layout"]["pos_in_scrolling_layout"][0] == 1
              and windows()[right["id"]]["layout"]["pos_in_scrolling_layout"][0] == 2,
              f"Could not place windows {left['id']} and {right['id']} side by side")

    def at(self, rx, ry):
        """A point of the screen, as fractions of the output."""
        o = self.output
        return round(o["x"] + o["width"] * rx), round(o["y"] + o["height"] * ry)

    def window_point(self, window, x, y):
        """A point in a floating window, ``x``/``y`` from its left/top (negative: from its right/bottom)."""
        w = windows()[window["id"]]
        (wx, wy), (ww, wh) = w["layout"]["tile_pos_in_workspace_view"], w["layout"]["window_size"]
        return (round(self.output["x"] + wx + (x if x >= 0 else ww + x)),
                round(self.output["y"] + wy + (y if y >= 0 else wh + y)))

    def close_window(self, window):
        if window["id"] in windows():
            niri("action", "close-window", "--id", window["id"])
            try:
                wait_for(lambda: window["id"] not in windows(), f"window {window['id']} to close", 10)
            except Failed:
                pass

    @staticmethod
    def end(processes):
        """SIGTERM to each app's process group, SIGKILL to any still there after 10 s."""
        for process in processes:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def stop(self):
        self.end(self.processes)
        for wid in list(self.own):
            if wid in windows():
                niri("action", "close-window", "--id", wid)
        time.sleep(1)
        try:
            niri("action", "focus-workspace", self.start_workspace["idx"])
        except subprocess.CalledProcessError:
            pass


# --- Firefox and its page ---------------------------------------------------------------------------------------

PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><style>
body { margin: 0; font: 28px sans-serif; } .half { position: fixed; left: 0; width: 100vw; height: 50vh; border: 0; }
#top { top: 0; } #bottom { top: 50vh; } #all { position: fixed; inset: 0; width: 100vw; height: 100vh; border: 0; }
</style></head><body><script>
const q = new URLSearchParams(location.search), mode = q.get('mode'), bg = '#' + (q.get('bg') || 'dddddd');
const post = (path, body) => fetch(path, {method: 'POST', body});
const report = event => post('/event', JSON.stringify(event));
document.body.style.background = bg;
function element(id, text, cls) { const e = document.createElement(id === 'zone' ? 'div' : 'button');
  e.id = id === 'zone' ? 'all' : id; e.className = cls || ''; e.textContent = text; e.style.background = bg;
  document.body.appendChild(e); return e; }
let track = null;
if (mode === 'cast') {
  element('all', 'Share the screen').onclick = async () => {
    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({video: true});
      track = stream.getVideoTracks()[0];
      track.onended = () => report({kind: 'ended'});
      const video = document.createElement('video'); video.muted = true; video.srcObject = stream; await video.play();
      report({kind: 'started', settings: track.getSettings()});
      await new Promise(r => setTimeout(r, 1500));
      const c = document.createElement('canvas'); c.width = video.videoWidth; c.height = video.videoHeight;
      c.getContext('2d').drawImage(video, 0, 0);
      await post('/blob', await new Promise(r => c.toBlob(r, 'image/png')));
      report({kind: 'frame', width: c.width, height: c.height});
    } catch (e) { report({kind: 'refused', name: e.name, message: e.message}); }
  };
} else if (mode === 'drop') {
  const zone = element('zone', 'Drop here');
  zone.ondragover = e => e.preventDefault();
  zone.ondrop = async e => { e.preventDefault(); const dt = e.dataTransfer, files = [];
    for (const f of dt.files) files.push({name: f.name, text: await f.text()});
    report({kind: 'drop', types: [...dt.types], files, text: dt.types.includes('text/plain') ? dt.getData('text/plain') : null}); };
} else if (mode === 'upload') {
  const label = document.createElement('label'); label.id = 'all'; label.textContent = 'Upload a file';
  label.style.background = bg; document.body.appendChild(label);
  const input = document.createElement('input'); input.type = 'file';
  input.style.cssText = 'position: fixed; inset: 0; opacity: 0; width: 100vw; height: 100vh'; label.appendChild(input);
  input.onchange = async () => { const files = []; for (const f of input.files) files.push({name: f.name, text: await f.text()});
    report({kind: 'upload', files}); };
} else if (mode === 'edges') {
  for (const [name, css] of [['top-left', 'top: 0; left: 0'], ['top-middle', 'top: 0; left: calc(50vw - 100px)'],
                             ['top-right', 'top: 0; right: 0'], ['bottom-left', 'bottom: 0; left: 0'],
                             ['bottom-middle', 'bottom: 0; left: calc(50vw - 100px)'], ['bottom-right', 'bottom: 0; right: 0']]) {
    const b = document.createElement('button'); b.textContent = name;
    b.style.cssText = `position: fixed; ${css}; width: 200px; height: 40px; font-size: 16px`;
    b.onclick = () => report({kind: 'clicked', name}); document.body.appendChild(b); }
} else if (mode === 'clip') {
  element('top', q.get('text') ? 'Copy text' : 'Copy an image', 'half').onclick = async () => {
    if (q.get('text')) {
      try { await navigator.clipboard.writeText(q.get('text')); report({kind: 'copied'}); }
      catch (e) { report({kind: 'copy-failed', message: String(e)}); }
      return; }
    const c = document.createElement('canvas'); c.width = 120; c.height = 90; const g = c.getContext('2d');
    g.fillStyle = '#' + q.get('copy'); g.fillRect(0, 0, 120, 90);
    try { await navigator.clipboard.write([new ClipboardItem({'image/png': await new Promise(r => c.toBlob(r, 'image/png'))})]);
      report({kind: 'copied'}); } catch (e) { report({kind: 'copy-failed', message: String(e)}); } };
  element('bottom', q.get('read') ? 'Read the clipboard' : 'Paste here', 'half').onclick = async () => {
    if (!q.get('read')) return;
    try { const types = []; let text = null; for (const item of await navigator.clipboard.read()) for (const t of item.types) {
        types.push(t); if (t === 'image/png') await post('/blob', await item.getType(t));
        if (t === 'text/plain') text = await (await item.getType(t)).text(); }
      report({kind: 'read', types, text}); } catch (e) { report({kind: 'read-failed', message: String(e)}); } };
  document.onpaste = async e => { e.preventDefault(); const types = [];
    for (const item of e.clipboardData.items) { types.push(item.type); if (item.type === 'image/png') await post('/blob', item.getAsFile()); }
    report({kind: 'paste', types}); };
}
setInterval(async () => { const r = await (await fetch('/command')).text();
  if (r === 'stop' && track) { track.stop(); report({kind: 'stopped'}); } }, 300);
</script></body></html>"""


class Pages:
    """A local web server for Firefox: the test page, the page's reports, and blobs it posts (frames, images)."""

    def __init__(self, folder):
        self.events, self.commands, self.blobs, self.folder = queue.Queue(), queue.Queue(), [], Path(folder)
        pages = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                path = urllib.parse.urlsplit(self.path).path
                if path == "/command":
                    try:
                        body = pages.commands.get_nowait().encode()
                    except queue.Empty:
                        body = b""
                else:
                    body = PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain" if path == "/command" else "text/html")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                data = self.rfile.read(int(self.headers["Content-Length"]))
                if self.path == "/blob":
                    path = pages.folder / f"blob-{len(pages.blobs) + 1}.png"
                    path.write_bytes(data)
                    pages.blobs.append(path)
                else:
                    pages.events.put(json.loads(data))
                self.send_response(200)
                self.end_headers()

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def url(self, **params):
        return f"http://127.0.0.1:{self.server.server_port}/?{urllib.parse.urlencode(params)}"

    def drain(self):
        while not self.events.empty():
            self.events.get_nowait()

    def wait(self, kinds, timeout=20):
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                raise Failed(f"The page reported none of {kinds} within {timeout} s")
            try:
                event = self.events.get(timeout=left)
            except queue.Empty:
                continue
            if event["kind"] in kinds:
                return event

    def stop(self):
        self.server.shutdown()


FIREFOX_PREFS = {
    "browser.shell.checkDefaultBrowser": False, "browser.aboutwelcome.enabled": False,
    "startup.homepage_welcome_url": "", "startup.homepage_welcome_url.additional": "",
    "browser.startup.homepage_override.mstone": "ignore", "browser.startup.page": 0,
    "datareporting.policy.dataSubmissionEnabled": False, "datareporting.healthreport.uploadEnabled": False,
    "toolkit.telemetry.reportingpolicy.firstRun": False, "browser.sessionstore.resume_from_crash": False,
    "app.update.enabled": False, "browser.tabs.warnOnClose": False, "browser.newtabpage.enabled": False,
    # Firefox's own permission doorhanger before the portal's dialog: the portal's is the consent F-012 tests.
    "media.navigator.permission.disabled": True,
    # Reading the clipboard from a page without Firefox's Paste popup: the X11 instance gets no keys.
    "dom.events.testing.asyncClipboard": True,
    # The file chooser through the portal, as Firefox from Flathub asks for it.
    "widget.use-xdg-desktop-portal.file-picker": 1,
}


def firefox(apps, name, url, x11=False, kiosk=False):
    profile = apps.tmp / f"firefox-{name}"
    profile.mkdir()
    (profile / "user.js").write_text("".join(f"user_pref({json.dumps(k)}, {json.dumps(v)});\n"
                                             for k, v in FIREFOX_PREFS.items()))
    env = {"MOZ_ENABLE_WAYLAND": "0", "GDK_BACKEND": "x11"} if x11 else {"MOZ_ENABLE_WAYLAND": "1"}
    return apps.start(["firefox", "--profile", profile, "--no-remote", "--new-instance", *(["--kiosk"] if kiosk else []), url], env=env,
                      title="Mozilla Firefox", timeout=60)[1]


# --- PortalProbe --------------------------------------------------------------------------------------------------

class Probe:
    """PortalProbe, built and installed for the user, running in its sandbox."""

    def __init__(self, tmp):
        if subprocess.run(["flatpak", "info", "--user", f"{RUNTIME}//{BRANCH}"], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode:
            raise RuntimeError(f"Install the runtime first: flatpak install --user flathub {RUNTIME}//{BRANCH}")
        build = Path(tmp) / "probe-build"
        build.mkdir()
        run("flatpak", "build-init", build / "app", PROBE, RUNTIME, RUNTIME, BRANCH)
        target = build / "app" / "files" / "bin" / "portal-probe"
        target.parent.mkdir(parents=True)
        shutil.copy(ROOT / "tests" / "flatpak" / "portal_probe.py", target)
        target.chmod(0o755)
        # No --filesystem, --socket or --talk-name: what the probe reaches beyond the portals is what they grant.
        run("flatpak", "build-finish", build / "app", "--command=portal-probe")
        run("flatpak", "build-export", build / "repo", build / "app")
        run("flatpak", "build-bundle", build / "repo", build / "probe.flatpak", PROBE)
        run("flatpak", "install", "--user", "--bundle", "--reinstall", "--no-related", "-y", "--noninteractive",
            build / "probe.flatpak", timeout=300)
        metadata = run("flatpak", "info", "--user", "--show-metadata", PROBE)
        check("filesystems" not in metadata and "[Session Bus Policy]" not in metadata,
              f"PortalProbe was built with more than the portals:\n{metadata}")
        self.process = subprocess.Popen(["flatpak", "run", "--user", PROBE], text=True, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.lines = queue.Queue()
        threading.Thread(target=self.pump, daemon=True).start()
        ready = self.answer(60)
        check(ready.get("ready"), f"PortalProbe did not start: {ready}")

    def pump(self):
        for line in self.process.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def answer(self, timeout):
        try:
            line = self.lines.get(timeout=timeout)
        except queue.Empty:
            raise Failed("PortalProbe did not answer") from None
        if line is None:
            raise Failed("PortalProbe exited")
        return json.loads(line)

    def send(self, wait=30, **command):
        self.process.stdin.write(json.dumps(command) + "\n")
        self.process.stdin.flush()
        return self.answer(wait)

    def request(self, interface, method, args, timeout=120):
        """Sends a portal request from the sandbox and returns (a thread's) answer once it comes."""
        result = {}

        def ask():
            try:
                result["answer"] = self.send(timeout + 10, do="request", interface=interface, method=method,
                                             args=args, timeout=timeout)
            except Failed as exc:
                result["error"] = str(exc)
        thread = threading.Thread(target=ask, daemon=True)
        thread.start()
        return thread, result

    def stop(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                subprocess.run(["flatpak", "kill", PROBE], timeout=15)
        # Its documents: the files it was given go out of the document store with it.
        try:
            docs = json.loads(busctl("call", *DOCUMENTS, "List", "s", PROBE))["data"][0]
        except (RuntimeError, ValueError, KeyError, IndexError):
            docs = {}
        for doc in docs:
            busctl("call", *DOCUMENTS, "Delete", "s", doc)
        subprocess.run(["flatpak", "uninstall", "--user", "--delete-data", "-y", "--noninteractive", PROBE],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)


def finish(thread, result, label, timeout=30):
    thread.join(timeout)
    check(not thread.is_alive(), f"{label}: no answer within {timeout} s")
    check("error" not in result, f"{label}: {result.get('error')}")
    return result["answer"]


def doc_path(uri):
    check(uri.startswith("file://"), f"Not a file URI: {uri}")
    return urllib.parse.unquote(uri[len("file://"):])


# --- The portal monitor -------------------------------------------------------------------------------------------

class Monitor:
    """Every call the broker makes to a backend (``org.freedesktop.impl.portal.*``), with the backend's bus name."""

    def __init__(self, folder):
        self.log = Path(folder) / "monitor.log"
        self.process = subprocess.Popen(
            ["dbus-monitor", "--session", "type='method_call',path='/org/freedesktop/portal/desktop'",
             "type='signal',interface='org.freedesktop.DBus',member='NameOwnerChanged'"],
            stdout=open(self.log, "w"), stderr=subprocess.DEVNULL)
        time.sleep(1)

    def calls(self, sender=None):
        """``[(interface, method, destination)]`` from the monitor's log, only those ``sender`` made when given: a
        backend calls others too (GNOME's FileChooser hands the dialog to Nautilus over the same interface)."""
        found = []
        for line in self.log.read_text(errors="replace").splitlines():
            match = re.search(r"sender=(\S+) -> destination=(\S+) .*interface=org\.freedesktop\.impl\.portal\.(\w+); "
                              r"member=(\w+)", line)
            if match and sender in (None, match.group(1)):
                found.append((match.group(3), match.group(4), match.group(2)))
        return found

    @staticmethod
    def broker():
        """The portal broker's unique bus name."""
        return json.loads(busctl("call", "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                 "GetNameOwner", "s", "org.freedesktop.portal.Desktop"))["data"][0]

    def access_dialogs(self):
        """The text each AccessDialog call carried (the backend's consent dialogs), one string per call."""
        found, current = [], None
        for line in self.log.read_text(errors="replace").splitlines():
            if line.startswith("method call"):
                current = [] if "member=AccessDialog" in line else None
                if current is not None:
                    found.append(current)
            elif current is not None:
                match = re.match(r'\s+string "(.*)"$', line)
                if match:
                    current.append(match.group(1))
        return [" | ".join(f) for f in found]

    def owners(self):
        """Bus name → the portal backend it is (``gtk``, ``gnome``, ...): the well-known names themselves, every
        unique name that owned one during the run (a backend restarted meanwhile has a new one), and today's."""
        backend = re.compile(r"org\.freedesktop\.impl\.portal\.desktop\.(\w+)")
        names, lines = {}, self.log.read_text(errors="replace").splitlines()
        for i, line in enumerate(lines):
            if "member=NameOwnerChanged" in line:
                args = [m.group(1) for m in (re.match(r'\s+string "(.*)"$', l) for l in lines[i + 1:i + 4]) if m]
                if len(args) == 3 and backend.fullmatch(args[0]):
                    for unique in args[1:]:
                        if unique:
                            names[unique] = backend.fullmatch(args[0]).group(1)
        for row in json.loads(busctl("list")):
            match = backend.fullmatch(row.get("name", ""))
            if match:
                names[row["name"]] = match.group(1)
                if row.get("connection"):
                    names[row["connection"]] = match.group(1)
        return names

    def stop(self):
        self.process.terminate()
        self.process.wait(timeout=10)


# --- The checks ---------------------------------------------------------------------------------------------------

class Smoke:
    def __init__(self, tmp, shots):
        self.tmp, self.shots, self.results = Path(tmp), shots, []
        self.apps = Apps(tmp)
        self.pointer = Pointer(allow_installed_shell=True)
        self.probe = None
        self.pages = None
        self.firefox = None

    def passed(self, name, detail):
        self.results.append({"check": name, "result": "pass", "detail": detail})
        print(json.dumps(self.results[-1]), flush=True)

    def shot(self, name):
        if self.shots:
            self.shots.mkdir(parents=True, exist_ok=True)
            run("grim", self.shots / f"{name}.png", timeout=30)

    def need_probe(self):
        if not self.probe:
            self.probe = Probe(self.tmp)
        return self.probe

    @staticmethod
    def ipc(call, *args):
        """A call on the installed shell's IPC (target ``desk``): its answer, or the end of its error."""
        out = subprocess.run(["quickshell", "ipc", "-p", "/etc/flow/shell.qml", "call", "desk", call, *args], text=True,
                             capture_output=True, timeout=10)
        return out.stdout.strip() or out.stderr.strip()[-200:]

    def need_pages(self):
        if not self.pages:
            self.pages = Pages(self.tmp)
        return self.pages

    def need_firefox(self, kiosk=False, **params):
        """The Wayland Firefox, full width and focused, showing the test page with ``params``; ``kiosk``: the page
        fills the window, with no tabs or toolbar above it."""
        pages = self.need_pages()
        if self.firefox and self.firefox["id"] in windows():
            self.apps.close_window(self.firefox)
        self.firefox = firefox(self.apps, uuid.uuid4().hex[:8], pages.url(**params), kiosk=kiosk)
        self.apps.focus(self.firefox, "100%")
        time.sleep(2)  # the page's first paint
        return self.firefox

    def dialog(self, app_id, before, label, timeout=20):
        window = self.apps.new_window(before, app_id=app_id, timeout=timeout, label=label)
        time.sleep(0.8)  # its first frame, before a click
        return window

    # FileChooser ------------------------------------------------------------------------------------------------

    def chooser_places(self, window):
        """Where Nautilus's chooser (GNOME's backend) draws what the test clicks, in its window: the window's close
        button, top right, which cancels; Open or Save, at the bottom right; the first file of the folder's grid,
        right of the sidebar (measured in the guest)."""
        w = windows()[window["id"]]
        size = w["layout"]["window_size"]
        origin = w["layout"]["tile_pos_in_workspace_view"] if w["is_floating"] else (0, 0)

        def place(x, y):
            return (round(self.apps.output["x"] + origin[0] + (x if x >= 0 else size[0] + x)),
                    round(self.apps.output["y"] + origin[1] + (y if y >= 0 else size[1] + y)))
        return {"cancel": place(-24, 22), "primary": place(-62, -34), "first": place(352, 125)}

    def check_chooser(self):
        self.chooser(by_pointer=False)

    def check_chooser_pointer(self):
        self.chooser(by_pointer=True)

    def chooser(self, by_pointer):
        """Nautilus's chooser, answered by keyboard (``by_pointer`` False: Escape; Home and Return, which open the
        folder's first file; Return on the suggested name) or with the pointer (its close button; the file, then Open;
        Save), where it draws them."""
        name = "chooser-pointer" if by_pointer else "chooser"
        probe = self.need_probe()
        folder = Path(tempfile.mkdtemp(prefix="flow-f012-files-", dir=Path.home()))
        target = Path(tempfile.mkdtemp(prefix="flow-f012-save-", dir=Path.home()))
        steps = []

        def click(window, what):
            at = self.chooser_places(window)[what]
            self.pointer.click(at)
            return at

        def covered(at, label):
            """What a click that came to nothing hit: the shell's surfaces sit over the window there."""
            layers = [l for l in read("layers") if l.get("namespace") == "quickshell"]
            return (f"{label} at {at} came to nothing; the installed shell has {len(layers)} overlay surfaces and its "
                    f"work indicator sits over the window's bottom right corner; its bands: {self.ipc('give_way')}")
        try:
            secret, other = uuid.uuid4().hex, uuid.uuid4().hex
            # Named so that the chosen file is the first in the grid, as Nautilus sorts it.
            (folder / "chosen.txt").write_text(secret)
            (folder / "other.txt").write_text(other)
            options = f"{{'current_folder': <b'{folder}'>}}"

            # Cancel.
            before = set(windows())
            thread, result = probe.request("FileChooser", "OpenFile", f"('', 'Open a test file', {options})")
            window = self.dialog(None, before, "the file chooser")
            click(window, "cancel") if by_pointer else keys("Escape")
            answer = finish(thread, result, "Cancel")
            check(answer.get("response") == 1 and not answer["results"].get("uris"),
                  f"Cancel answered {answer}, not 1 with no file")
            steps.append("Cancel answered 1")

            # Open.
            before = set(windows())
            thread, result = probe.request("FileChooser", "OpenFile", f"('', 'Open a test file', {options})")
            window = self.dialog(None, before, "the file chooser")
            if by_pointer:
                click(window, "first")
                time.sleep(0.6)
                self.shot("chooser-open-pointer")
                at = click(window, "primary")
            else:
                # The first file of the folder (Home), opened (Return).
                keys("Home")
                self.shot("chooser-open")
                keys("Return")
            thread.join(15)
            if thread.is_alive() and by_pointer:
                self.shot("chooser-open-pointer-missed")
                keys("Escape")
                finish(thread, result, "Escape after the missed click")
                raise Failed(covered(at, "A click on Open"))
            answer = finish(thread, result, "Open")
            uris = answer.get("results", {}).get("uris") or []
            check(answer.get("response") == 0 and len(uris) == 1, f"Open answered {answer}")
            path = doc_path(uris[0])
            check(path.startswith(f"/run/user/{os.getuid()}/doc/") and path.endswith("/chosen.txt"),
                  f"The sandbox was given {path}, not a document portal path")
            doc = str(Path(path).parent)
            got = probe.send(do="read", path=path)
            check(got.get("text") == secret, f"The probe read {got} from the chosen file")
            for label, attempt in (("the file beside it", f"{doc}/other.txt"),
                                   ("its original path", str(folder / "chosen.txt")),
                                   ("the file beside its original path", str(folder / "other.txt"))):
                got = probe.send(do="read", path=attempt)
                check(got.get("error") in ("FileNotFoundError", "PermissionError"),
                      f"The probe could read {label}: {got}")
            listed = probe.send(do="list", path=doc)
            check(listed.get("names") == ["chosen.txt"], f"The probe's document folder lists {listed}")
            home = probe.send(do="list", path=str(Path.home()))
            check(folder.name not in (home.get("names") or []), f"The probe sees the test folder in home: {home}")
            edited = uuid.uuid4().hex
            check(probe.send(do="write", path=path, text=edited).get("written"), "The probe could not write the chosen file")
            check((folder / "chosen.txt").read_text() == edited, "The probe's write did not reach the chosen file")
            check((folder / "other.txt").read_text() == other, "The file beside the chosen one changed")
            steps.append("Open gave the sandbox only the chosen file (read and written; the file beside it, its "
                         "original path and the home folder unseen)")

            # Save, with the suggested name.
            before = set(windows())
            save_options = f"{{'current_folder': <b'{target}'>, 'current_name': <'saved-by-probe.txt'>}}"
            thread, result = probe.request("FileChooser", "SaveFile", f"('', 'Save a test file', {save_options})")
            window = self.dialog(None, before, "the save dialog")
            self.shot("chooser-save-pointer" if by_pointer else "chooser-save")
            if by_pointer:
                at = click(window, "primary")
            else:
                keys("Return")
            thread.join(15)
            if thread.is_alive() and by_pointer:
                keys("Escape")
                finish(thread, result, "Escape after the missed click")
                raise Failed(covered(at, "A click on Save") + f" ({'; '.join(steps)})")
            answer = finish(thread, result, "Save")
            uris = answer.get("results", {}).get("uris") or []
            check(answer.get("response") == 0 and len(uris) == 1, f"Save answered {answer}")
            path = doc_path(uris[0])
            check(path.startswith(f"/run/user/{os.getuid()}/doc/") and path.endswith("/saved-by-probe.txt"),
                  f"Save gave the sandbox {path}")
            written = uuid.uuid4().hex
            check(probe.send(do="write", path=path, text=written).get("written"), "The probe could not write the saved file")
            saved = target / "saved-by-probe.txt"
            check(saved.exists() and saved.read_text() == written, "The saved file is not where the user chose")
            check(sorted(p.name for p in target.iterdir()) == ["saved-by-probe.txt"],
                  "Saving left other files in the chosen folder")
            steps.append("Save wrote where the user chose")

            # A real app's upload dialog: Firefox, as a Flatpak Firefox asks (the portal, with its window as the
            # parent, so Niri floats the chooser over it as a Layer).
            pages = self.need_pages()
            self.need_firefox(mode="upload")
            pages.drain()
            before = set(windows())
            self.pointer.click(self.apps.at(0.5, 0.5))
            window = self.dialog("org.gnome.Nautilus", before, "Firefox's upload dialog")
            check(windows()[window["id"]]["is_floating"], "Firefox's upload dialog is not floating over Firefox")
            # It opens in the folder Nautilus last used for Firefox: go to the test folder by its path.
            keys("ctrl+l", f"{folder}/", "Return")
            time.sleep(1)
            self.shot("chooser-firefox-pointer" if by_pointer else "chooser-firefox")
            if by_pointer:
                click(window, "first")
                time.sleep(0.6)
                at = click(window, "primary")
            else:
                keys("Home", "Return")
            try:
                upload = pages.wait(["upload"], timeout=15)
            except Failed:
                if by_pointer:
                    keys("Escape")
                    raise Failed(covered(at, "A click on Open in Firefox's upload dialog") + f" ({'; '.join(steps)})")
                raise
            check(upload["files"] == [{"name": "chosen.txt", "text": edited}], f"Firefox uploaded {upload}")
            steps.append("Firefox's upload dialog, floating over it, uploaded the chosen file")
            self.passed(name, ("pointer: " if by_pointer else "keyboard: ") + "; ".join(steps))
        finally:
            shutil.rmtree(folder, ignore_errors=True)
            shutil.rmtree(target, ignore_errors=True)

    # Overlap: the shell's surfaces over an app's edges -----------------------------------------------------------

    def check_edges(self):
        """An app's own controls along its window's top and bottom edges take clicks, as a user's (50 ms after the
        pointer arrives), where the island (top middle), the home indicator (bottom middle) and the work indicator
        (bottom right) sit over the window: the bands fade and let the click through (ADR-0013). Each band is still
        the shell's when reached on purpose, by its edge and by Super+B (its IPC call): the island's wordmark then
        opens Desk, the home indicator Overview, and a click on the work indicator stays the shell's."""
        pages = self.need_pages()
        page = self.need_firefox(kiosk=True, mode="edges")

        def bands():
            """The installed shell's bands as it holds them (faded or not, and their input)."""
            answer = self.ipc("give_way")
            try:
                return json.loads(answer)
            except ValueError:
                raise Failed(f"The installed shell's give_way answered {answer!r}") from None
        w = windows()[page["id"]]
        width, height = w["layout"]["window_size"]
        o = self.apps.output

        def at(x, y):
            return o["x"] + x, o["y"] + y
        took, missed, waits = [], [], []
        # The band over each middle and right point: the Island, the home indicator, the work indicator.
        over = {"top-middle": "island", "bottom-middle": "home", "bottom-right": "indicator"}
        points = {"top-left": (30, 12), "top-middle": (width // 2, 12), "top-right": (width - 30, 12),
                  "bottom-left": (30, height - 12), "bottom-middle": (width // 2, height - 12),
                  "bottom-right": (width - 30, height - 12)}
        for name, (x, y) in points.items():
            pages.drain()
            self.pointer.click(at(x, y))
            try:
                event = pages.wait(["clicked"], timeout=4)
                (took if event["name"] == name else missed).append(name)
                if name in over:
                    # How long after the pointer came the band gave it back to the page. -1: it never faded on this
                    # click (each fade starts it afresh), so the band did not cover the point and the check proves
                    # nothing there.
                    left = bands()[over[name]]["left_after_ms"]
                    check(left >= 0, f"The {over[name]} band did not fade for the {name} click: {bands()}")
                    waits.append(f"{over[name]} {left:.0f} ms")
            except Failed:
                missed.append(f"{name} ({json.dumps(bands())})")
                # The click reached the shell instead (Overview from the home indicator, a panel from the island,
                # the next space from the work indicator): back to the page before the next one.
                keys("Escape", "Escape")
                self.apps.focus(page)
            self.pointer.park()
        self.shot("edges")
        check(not missed, f"The page's own {', '.join(missed)} buttons took no click (the shell's surfaces sit over "
                          f"them); {', '.join(took) or 'none'} did")
        # Once the pointer has left them (parked), the faded bands come back: the probe, every 1.5 s, finds no pointer.
        left_at = time.monotonic()
        wait_for(lambda: not any(b["yielded"] for b in bands().values()), "the bands to come back once the pointer left", 4)
        back = time.monotonic() - left_at

        # Reached on purpose. The pointer comes from the app first (the band fades), then goes to the band's screen
        # edge, or Super+B's call is made; the band must be the shell's again and take a click on its control.
        def control(surface, text):
            found = [c for c in json.loads(self.ipc("controls")) if c["surface"] == surface and c["text"] == text]
            check(found, f"No {text!r} on the {surface} band")
            return center(found[0])
        reach = {"island": ((width // 2, 0), lambda: control("island", "floe."),
                            lambda: json.loads(self.ipc("surfaces"))["desk"], "Desk"),
                 "home": ((width // 2, height - 1), lambda: center(json.loads(self.ipc("pointer_target", "home"))),
                          lambda: self.ipc("overview_state") == "open", "Overview"),
                 "indicator": ((width - 1, height - 1), lambda: control("indicator", "‹"), None, None)}
        reached = []
        for name, (edge, target, opened, label) in reach.items():
            for way in ("edge", "Super+B"):
                self.pointer.move(at(*points[{"island": "top-middle", "home": "bottom-middle"}.get(name, "bottom-right")]))
                wait_for(lambda: bands()[name]["yielded"], f"the {name} band to fade under the pointer", 3)
                if way == "edge":
                    self.pointer.move(at(*edge))
                else:
                    self.ipc("bands")
                wait_for(lambda: (b := bands()[name])["reached"] and not b["yielded"], f"the {name} band back by its {way}", 3)
                pages.drain()
                self.pointer.click(target())
                try:
                    if opened:
                        wait_for(opened, f"a click on the {name} band, reached by its {way}, to open {label}", 4)
                    try:
                        stray = pages.wait(["clicked"], timeout=0.8)
                    except Failed:
                        stray = None
                    check(not stray, f"The page took a click on the {name} band reached by its {way}: {stray}")
                    reached.append(f"{name} by its {way}" + (f" opened {label}" if label else " kept the click"))
                finally:
                    if opened and opened():
                        keys("Escape")
                        wait_for(lambda: not opened(), f"{label} to close")
                    self.apps.focus(page)
                    self.pointer.park()
        self.passed("edges", f"every edge button of a full-window page took a click 50 ms after the pointer came: "
                             f"{', '.join(took)}; the bands let go of the pointer after {', '.join(waits)}, and were back "
                             f"{back:.1f} s after it left; reached on purpose, {'; '.join(reached)}")

    # Screenshot -------------------------------------------------------------------------------------------------

    def access_dialog(self, before):
        """The Access backend's consent dialog, and its Deny and Allow buttons (GTK 3: two halves of its foot)."""
        window = self.dialog("xdg-desktop-portal-gtk", before, "the screenshot consent dialog")
        w = windows()[window["id"]]
        check(w["is_floating"], "The consent dialog is not floating over the space")
        ww = w["layout"]["window_size"][0]
        return window, self.apps.window_point(window, ww // 4, -23), self.apps.window_point(window, 3 * ww // 4, -23)

    def marker(self):
        """A full-width window of a colour chosen for this run, focused, for screenshots to show."""
        colour = fresh_colour()
        _, marker = self.apps.start(["foot", "-a", "flow-f012-marker", "-o", f"colors-dark.background={colour_hex(colour)}",
                                     "-o", f"colors-light.background={colour_hex(colour)}", "sh", "-c", "sleep 900"],
                                    app_id="flow-f012-marker", isolated=False)
        self.apps.focus(marker, "100%")
        time.sleep(1)
        return colour, marker

    def shows(self, image, colour):
        """How many of a grid of points, away from the shell's island and indicators, show ``colour``."""
        o, scale = self.apps.output, self.apps.output["scale"]
        points = [(x, y) for x in range(40, o["width"] - 40, 60) for y in range(80, o["height"] - 80, 60)
                  if not (x > o["width"] - 300 and y > o["height"] - 120)]
        return sum(close(image.pixel(round(x * scale), round(y * scale)), colour) for x, y in points), len(points)

    def private(self, path):
        """How ``path`` is kept from other local users (None if it is not): its own mode, and the closed folders
        above it."""
        if path.stat().st_mode & 0o077:
            return None
        closed = [str(f) for f in path.parents if f.stat().st_mode & 0o005 == 0]
        return f"the file is {oct(path.stat().st_mode & 0o777)}" + (f", under {closed[0]} (closed)" if closed else "")

    def check_screenshot(self, state):
        probe = self.need_probe()
        colour, marker = self.marker()
        state.set_permissions({a: p for a, p in state.permissions().items() if a != PROBE})
        shot_args = "('', {'interactive': <false>})"

        # Denied: no file, and the choice is kept.
        before = set(windows())
        thread, result = probe.request("Screenshot", "Screenshot", shot_args)
        dialog, deny, _ = self.access_dialog(before)
        self.shot("screenshot-consent")
        self.pointer.click(deny)
        answer = finish(thread, result, "Deny")
        check(answer.get("response") == 2 and not answer["results"].get("uri"), f"Deny answered {answer}")
        check(not state.new_screenshots(), f"Deny left screenshots: {state.new_screenshots()}")
        check(state.permissions().get(PROBE) == ["no"], f"Deny stored {state.permissions()}")
        before = set(windows())
        answer = finish(*probe.request("Screenshot", "Screenshot", shot_args), "a request after Deny")
        check(answer.get("response") == 2, f"A request after Deny answered {answer}")
        check(not [w for w in windows() if w not in before], "A request after Deny asked again")

        # Allowed: the live screen.
        state.set_permissions({a: p for a, p in state.permissions().items() if a != PROBE})
        before = set(windows())
        thread, result = probe.request("Screenshot", "Screenshot", shot_args)
        dialog, _, allow = self.access_dialog(before)
        w = windows()[dialog["id"]]
        (dx, dy), (dw, dh) = w["layout"]["tile_pos_in_workspace_view"], w["layout"]["window_size"]
        self.pointer.click(allow)
        answer = finish(thread, result, "Allow")
        uri = answer.get("results", {}).get("uri")
        check(answer.get("response") == 0 and uri, f"Allow answered {answer}")
        path = doc_path(uri)
        check(path.startswith(f"/run/user/{os.getuid()}/doc/"), f"The sandbox was given {path}")
        got = probe.send(do="read_size", path=path)
        new = state.new_screenshots()
        check(len(new) == 1, f"Allow made {new}")
        check(got.get("size") == new[0].stat().st_size, f"The probe read {got} of {new[0].stat().st_size} bytes")
        image = Image(new[0].read_bytes())
        o = self.apps.output
        check((image.width, image.height) == (round(o["width"] * o["scale"]), round(o["height"] * o["scale"])),
              f"The screenshot is {image.width}×{image.height}")
        # The marker fills the screen but the island (top middle), the work indicator (bottom right), the home
        # indicator (bottom middle) and the closing dialog: sample a grid away from them.
        samples = [(x, y) for x in range(40, o["width"] - 40, 60) for y in range(80, o["height"] - 80, 60)
                   if not (dx - 20 <= x <= dx + dw + 20 and dy - 20 <= y <= dy + dh + 20)
                   and not (x > o["width"] - 300 and y > o["height"] - 120)]
        scale = o["scale"]
        hits = sum(close(image.pixel(round(x * scale), round(y * scale)), colour) for x, y in samples)
        check(hits >= 0.95 * len(samples), f"Only {hits} of {len(samples)} sample points show this run's colour")
        check(state.permissions().get(PROBE) == ["yes"], f"Allow stored {state.permissions()}")
        detail = (f"consent asked and denied (answer 2, no file, kept), then allowed: a {image.width}×{image.height} "
                  f"file of the live screen ({hits}/{len(samples)} points this run's colour), read by the sandbox")

        # A request while the backend is off the bus: the broker has forgotten its version and forwards the request
        # unchecked, and the backend D-Bus starts for it must ask itself. With the app's stored answer "no", either
        # the broker refuses (it read the version in time) or a dialog asks; nothing is captured without consent.
        for path in state.new_screenshots():
            path.unlink()
        state.set_permissions({**{a: p for a, p in state.permissions().items() if a != PROBE}, PROBE: ["no"]})
        subprocess.run(["systemctl", "--user", "stop", "flow-screenshot-portal.service"], timeout=30)
        time.sleep(1)
        before = set(windows())
        thread, result = probe.request("Screenshot", "Screenshot", shot_args)
        asked = None
        try:
            asked, deny, _ = self.access_dialog(before)
            self.pointer.click(deny)
        except Failed:
            pass  # no dialog: the broker refused by itself
        answer = finish(thread, result, "a request after the backend left the bus")
        check(answer.get("response") != 0 and not state.new_screenshots(),
              f"With the backend restarted and the app's answer stored as no, the request answered {answer} "
              f"and made {state.new_screenshots()}")
        detail += ("; with the backend off the bus and the stored answer no, " +
                   ("its own dialog asked and Cancel captured nothing" if asked else "the broker refused"))

        # Interactive: the user's own choice. Flow's backend asks through the Access dialog, then takes the whole
        # screen once the dialog is gone.
        for path in state.new_screenshots():
            path.unlink()
        interactive = "('', {'interactive': <true>})"
        before = set(windows())
        thread, result = probe.request("Screenshot", "Screenshot", interactive)
        dialog, cancel, _ = self.access_dialog(before)
        self.shot("screenshot-interactive")
        self.pointer.click(cancel)
        answer = finish(thread, result, "Cancel on the interactive dialog")
        check(answer.get("response") == 1 and not state.new_screenshots(), f"Cancel answered {answer}")
        told = self.monitor.access_dialogs()
        check(told and PROBE in told[-1], f"The consent dialog was not told the app's name: {told[-1:] or 'no dialog'}")
        # Closed instead of answered: the same as Cancel.
        before = set(windows())
        thread, result = probe.request("Screenshot", "Screenshot", interactive)
        dialog, _, _ = self.access_dialog(before)
        self.apps.close_window(dialog)
        answer = finish(thread, result, "Closing the interactive dialog")
        check(answer.get("response") == 1 and not state.new_screenshots(), f"Closing the dialog answered {answer}")
        before = set(windows())
        thread, result = probe.request("Screenshot", "Screenshot", interactive)
        dialog, _, take = self.access_dialog(before)
        self.pointer.click(take)
        answer = finish(thread, result, "Take Screenshot")
        uri = answer.get("results", {}).get("uri")
        check(answer.get("response") == 0 and uri, f"Take Screenshot answered {answer}")
        new = state.new_screenshots()
        check(len(new) == 1, f"Take Screenshot made {new}")
        check(probe.send(do="read_size", path=doc_path(uri)).get("size") == new[0].stat().st_size,
              "The sandbox could not read the interactive screenshot")
        image = Image(new[0].read_bytes())
        # The dialog is gone by then: its place shows the marker too.
        everywhere = [(x, y) for x in range(40, o["width"] - 40, 60) for y in range(80, o["height"] - 80, 60)
                      if not (x > o["width"] - 300 and y > o["height"] - 120)]
        hits = sum(close(image.pixel(round(x * scale), round(y * scale)), colour) for x, y in everywhere)
        check(hits >= 0.95 * len(everywhere), f"Only {hits} of {len(everywhere)} points of the interactive "
                                               "screenshot show this run's colour (the dialog still on it?)")
        privacy = self.private(new[0])
        check(privacy, f"{new[0]} ({oct(new[0].stat().st_mode & 0o777)}) can be read by other local users")
        detail += f"; interactive: the dialog named {PROBE}; Cancel, or closing it, answered 1 and captured nothing; " \
                  f"Take Screenshot gave the live screen without the dialog ({hits}/{len(everywhere)} points); {privacy}"

        # PickColor: Niri's picker, answered with a click on the marker.
        thread, result = probe.request("Screenshot", "PickColor", "('', {})")
        time.sleep(1.5)
        self.pointer.click(self.apps.at(0.3, 0.6))
        answer = finish(thread, result, "PickColor")
        picked = answer.get("results", {}).get("color")
        check(answer.get("response") == 0 and picked and close([round(c * 255) for c in picked], colour, 3),
              f"PickColor answered {answer}, not this run's colour {colour}")
        self.apps.close_window(marker)
        self.passed("screenshot", detail + "; PickColor picked the marker's colour")

    GIMP_SHOT = """
import gi
gi.require_version("Gimp", "3.0")
from gi.repository import Gimp, Gio
procedure = Gimp.get_pdb().lookup_procedure("plug-in-screenshot")
config = procedure.create_config()
config.set_property("run-mode", Gimp.RunMode.WITH_LAST_VALS)
config.set_property("shoot-type", "screen")
result = procedure.run(config)
print("GIMP-STATUS", result.index(0).value_nick, flush=True)
if result.index(0) == Gimp.PDBStatusType.SUCCESS:
    Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, result.index(1), Gio.File.new_for_path(%r), None)
    print("GIMP-SAVED", flush=True)
"""

    def gimp_shot(self, out):
        """GIMP 3's screenshot plug-in, the one File > Create > Screenshot runs, as a batch call with its last
        values (it refuses the non-interactive run mode when the portal is its backend, before asking): on Wayland it
        asks the Screenshot portal with ``interactive: true`` (plug-ins/screenshot/screenshot-freedesktop.c)."""
        home = self.tmp / "gimp-home"
        env = {**os.environ, "XDG_CONFIG_HOME": str(home / "config"), "XDG_CACHE_HOME": str(home / "cache"),
               "XDG_DATA_HOME": str(home / "data")}
        result = {}

        def shoot():
            done = subprocess.run(["gimp-console", "-i", "--batch-interpreter=python-fu-eval", "-b",
                                   self.GIMP_SHOT % str(out), "--quit"], env=env, text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
            result["output"] = done.stdout
        thread = threading.Thread(target=shoot, daemon=True)
        thread.start()
        return thread, result

    def check_screenshot_gimp(self):
        state = self.state
        for path in state.new_screenshots():  # an earlier check's: put back at the end anyway
            path.unlink()
        colour, marker = self.marker()
        out = self.tmp / "gimp-shot.png"

        def status(result):
            match = re.search(r"GIMP-STATUS (\S+)", result.get("output", ""))
            return match.group(1) if match else None

        before = set(windows())
        thread, result = self.gimp_shot(out)
        try:
            dialog, cancel, _ = self.access_dialog(before)
        except Failed:
            thread.join(120)
            tail = " ".join(line for line in result.get("output", "").splitlines()[-6:] if "GIMP" in line or "rror" in line)
            raise Failed(f"GIMP's screenshot showed no consent dialog and answered {status(result)}: {tail}")
        self.shot("screenshot-gimp-consent")
        self.pointer.click(cancel)
        thread.join(120)
        check(status(result) not in (None, "success") and not out.exists() and not state.new_screenshots(),
              f"Cancel gave GIMP {status(result)}, file {out.exists()}, screenshots {state.new_screenshots()}")
        cancelled = status(result)

        before = set(windows())
        thread, result = self.gimp_shot(out)
        dialog, _, take = self.access_dialog(before)
        # A user may take a while: longer than D-Bus's default call timeout (25 s).
        time.sleep(30)
        self.pointer.click(take)
        thread.join(120)
        check(status(result) == "success" and "GIMP-SAVED" in result.get("output", "") and out.exists(),
              f"Take Screenshot gave GIMP {status(result)}: {result.get('output', '')[-400:]}")
        hits, points = self.shows(Image(out.read_bytes()), colour)
        check(hits >= 0.95 * points, f"Only {hits} of {points} points of GIMP's screenshot show this run's colour")
        self.apps.close_window(marker)
        self.passed("screenshot-gimp", f"GIMP 3's screenshot asked for consent; Cancel gave it {cancelled} and no "
                                       f"file; Take Screenshot, clicked after 30 s, gave it the live screen "
                                       f"({hits}/{points} points)")

    # ScreenCast -------------------------------------------------------------------------------------------------

    def share_dialog(self, before):
        """GNOME's Share Screen dialog: Cancel and Share in its header bar."""
        window = self.dialog("xdg-desktop-portal-gnome", before, "the Share Screen dialog")
        return window, self.apps.window_point(window, 47, 23), self.apps.window_point(window, -45, 23)

    def casts(self):
        return [c for c in read("casts") if c.get("kind") == "PipeWire"]

    def check_screencast(self):
        pages, colour = self.need_pages(), fresh_colour()
        self.need_firefox(mode="cast", bg=colour_hex(colour))
        check(not self.casts(), f"Casts before the test: {self.casts()}")

        def ask():
            pages.drain()
            before = set(windows())
            self.pointer.click(self.apps.at(0.5, 0.5))
            return self.share_dialog(before)

        # Cancelled.
        dialog, cancel, _ = ask()
        self.shot("screencast-dialog")
        self.pointer.click(cancel)
        refused = pages.wait(["refused", "started"])
        check(refused["kind"] == "refused" and refused["name"] == "NotAllowedError", f"Cancel gave the page {refused}")
        check(not self.casts(), "A cancelled share left a cast")

        def share():
            dialog, _, ok = ask()
            self.pointer.click(ok)
            started = pages.wait(["started", "refused"])
            check(started["kind"] == "started", f"Share gave the page {started}")
            cast = wait_for(lambda: self.casts(), "Niri to list the cast", 10)
            check(len(cast) == 1 and cast[0]["target"].get("Output"), f"Niri lists {cast}")
            return started, cast[0]

        started, cast = share()
        frame_event = pages.wait(["frame"])
        frame = Image(pages.blobs[-1].read_bytes())
        screen = screen_png(self.tmp / "screen-during-cast.png")
        self.shot("screencast-sharing")
        check((frame.width, frame.height) == (screen.width, screen.height),
              f"Frames are {frame.width}×{frame.height}, the screen {screen.width}×{screen.height}")
        samples = [(x, y) for x in range(20, screen.width - 20, 50) for y in range(20, screen.height - 20, 50)]
        same = sum(close(frame.pixel(x, y), screen.pixel(x, y), 16) for x, y in samples)
        page = sum(close(frame.pixel(x, y), colour) for x, y in samples)
        check(same >= 0.9 * len(samples), f"Only {same} of {len(samples)} frame points match the screen")
        check(page >= 0.5 * len(samples), f"Only {page} of {len(samples)} frame points show the page's colour")
        node = cast["pw_node_id"]
        check(f'"id":{node},' in run("pw-dump", node).replace(" ", "").replace("\n", ""),
              f"PipeWire has no node {node}")

        pages.commands.put("stop")
        pages.wait(["stopped"])
        wait_for(lambda: not self.casts(), "the cast to end after the page stopped it", 10)

        started, cast = share()
        niri("action", "stop-cast", "--session-id", cast["session_id"])
        pages.wait(["ended"])
        wait_for(lambda: not self.casts(), "the cast to end after stop-cast", 10)
        self.passed("screencast", f"Cancel refused the page (NotAllowedError); Share streamed PipeWire node {node} "
                                  f"({frame.width}×{frame.height}, {same}/{len(samples)} points as on screen); "
                                  "the share ended when the page stopped it and when Niri's stop-cast did")

    # Drag and drop ------------------------------------------------------------------------------------------------

    def drag(self, start, end):
        self.pointer.run(("move", *start), ("wait", 150), "down", ("wait", 300),
                         *[s for i in range(1, 25) for s in (("wait", 30), ("move", round(start[0] + (end[0] - start[0]) * i / 24),
                                                                                    round(start[1] + (end[1] - start[1]) * i / 24)))],
                         ("wait", 300), "up")

    def check_dnd_file(self):
        pages = self.need_pages()
        folder = Path(tempfile.mkdtemp(prefix="flow-f012-drag-", dir=self.tmp))
        name, content = f"dragged-{uuid.uuid4().hex[:8]}.txt", uuid.uuid4().hex
        (folder / name).write_text(content)
        files = self.apps.start(["nautilus", "--new-window", folder], app_id="org.gnome.Nautilus", isolated=False)[1]
        page = self.need_firefox(mode="drop")
        self.apps.side_by_side(files, page)
        pages.drain()
        self.shot("dnd-file")
        # Nautilus's grid at half width: the only file is the first cell (measured in the guest).
        self.drag(self.apps.at(90 / 1280, 120 / 800), self.apps.at(0.75, 0.5))
        drop = pages.wait(["drop"])
        check("Files" in drop["types"] and drop["files"] == [{"name": name, "text": content}],
              f"The page got {drop}")
        self.apps.close_window(files)
        self.passed("dnd-file", f"{name} dragged from Files reached Firefox with its content")

    def check_dnd_text(self):
        pages = self.need_pages()
        word = "drag" + uuid.uuid4().hex[:12]
        source = self.tmp / "kate-drag.txt"
        source.write_text(word + "\n")
        # Read-only, so the drag copies the text rather than moving it out of the file.
        source.chmod(0o444)
        kate = self.apps.start(["kate", "-n", source], app_id="org.kde.kate", title="kate-drag")[1]
        page = self.need_firefox(mode="drop")
        self.apps.side_by_side(kate, page)
        self.apps.focus(kate)
        keys("ctrl+a")
        pages.drain()
        self.shot("dnd-text")
        # The selected first line, in Kate's editor at half width (measured in the guest).
        self.drag(self.apps.at(110 / 1280, 147 / 800), self.apps.at(0.75, 0.5))
        drop = pages.wait(["drop"])
        check("text/plain" in drop["types"] and (drop["text"] or "").strip() == word, f"The page got {drop}")
        self.passed("dnd-text", "text dragged from Kate (Qt) reached Firefox (GTK) as text/plain")

    # Clipboard --------------------------------------------------------------------------------------------------

    def wayland_clipboard(self):
        """The clipboard's text, as wl-paste picks it from what the owner offers (GTK 4 offers only
        ``text/plain;charset=utf-8``)."""
        out = subprocess.run(["wl-paste", "-n"], stdout=subprocess.PIPE, timeout=10)
        return out.stdout

    # Keys go to Wayland apps only, and only before an X11 window has had focus in a check: after one, wtype's chords
    # can reach a Wayland app wrong (KolourPaint's Ctrl+V opened an empty image), a virtual-keyboard artifact a real
    # keyboard does not have. So the X11 steps come last,
    # and the copy from X11 is read by a page with the pointer.

    def check_clipboard_text(self):
        """Text from Firefox (GTK 3) to Kate (Qt), Kate to xterm (X11), and xterm back to Firefox. GTK's own text
        widgets are not in it: GNOME Text Editor (GTK 4) and Mousepad (GTK 3) dropped every wtype chord in the guest
        (Ctrl+A, Ctrl+C), in one call or several, slow or not, though named keys such as Return reached them; a
        virtual-keyboard artifact of the test's keys, not of Flow. Firefox's page copies and reads on a click."""
        pages = self.need_pages()
        a, b, c = (f"clip{n}{uuid.uuid4().hex[:12]}" for n in "abc")
        qt_file, x11_file = self.tmp / "qt.txt", self.tmp / "x11.txt"
        qt_file.write_text("")

        # GTK → Qt: the page copies, Kate pastes and saves. Kate opens after the copy and takes its keys before focus
        # moves: keys sent once focus has come back to a window were lost now and then in the guest.
        page = self.need_firefox(mode="clip", text=a, read=1)
        pages.drain()
        self.pointer.click(self.apps.at(0.5, 0.25))
        copied = pages.wait(["copied", "copy-failed"])
        check(copied["kind"] == "copied", f"Firefox could not copy: {copied}")
        wait_for(lambda: self.wayland_clipboard().decode().strip() == a, "Firefox's copy")
        qt = self.apps.start(["kate", "-n", qt_file], app_id="org.kde.kate", title="qt.txt")[1]
        wait_for(lambda: windows()[qt["id"]]["is_focused"], "Kate to have focus")
        niri("action", "set-column-width", "100%")
        time.sleep(1.5)
        keys("ctrl+a", "ctrl+v", "ctrl+s")
        wait_for(lambda: qt_file.read_text().strip() == a, lambda: f"Kate to save the paste ({qt_file.read_text()!r})")

        # Qt → X11.
        keys("ctrl+a")
        keys(b)
        keys("ctrl+a", "ctrl+c")
        wait_for(lambda: self.wayland_clipboard().decode().strip() == b, "Kate's copy")
        size = len(b)
        x11 = self.apps.start(["xterm", "-fa", "Monospace", "-fs", "14", "-xrm", "XTerm*selectToClipboard: true",
                               "-e", "sh", "-c", f"stty -icanon -echo; head -c {size} > {x11_file}; echo {c}; sleep 900"],
                              app_id="XTerm")[1]
        self.apps.focus(x11, "100%")
        self.pointer.click(self.apps.at(0.5, 0.5), button="middle")
        wait_for(lambda: x11_file.exists() and x11_file.read_text() == b,
                 lambda: f"xterm's paste ({x11_file.read_text() if x11_file.exists() else None!r})")

        # X11 → GTK: xterm prints c on its first line once the paste is read; a double click there copies it, and
        # the page reads it.
        time.sleep(0.5)
        self.shot("clipboard-xterm")
        self.pointer.run(("move", *self.apps.at(60 / 1280, 40 / 800)), ("wait", 50), "click", ("wait", 60), "click")
        wait_for(lambda: self.wayland_clipboard().decode().strip() == c, "xterm's copy to reach Wayland")
        self.apps.focus(page, "100%")
        pages.drain()
        self.pointer.click(self.apps.at(0.5, 0.75))
        read_event = pages.wait(["read", "read-failed"])
        check(read_event["kind"] == "read" and (read_event.get("text") or "").strip() == c,
              f"Firefox read {read_event}, not xterm's copy")
        self.passed("clipboard-text", "text copied in Firefox (GTK) was pasted and saved in Kate (Qt), Kate's pasted "
                                      "in xterm (X11), and xterm's read by Firefox")

    def check_clipboard_image(self):
        pages = self.need_pages()
        c1, c2, c3 = fresh_colour(), fresh_colour(), fresh_colour()
        src = self.tmp / "copy-from.png"
        src.write_bytes(png(120, 90, c1))

        def blob_colour(count):
            wait_for(lambda: len(pages.blobs) >= count, "the page's image")
            image = Image(pages.blobs[count - 1].read_bytes())
            return (image.width, image.height), image.pixel(60, 45)

        def offered():
            return subprocess.run(["wl-paste", "-l"], stdout=subprocess.PIPE, timeout=10).stdout

        # Qt → GTK: KolourPaint copies, Firefox's page gets the paste.
        kp = self.apps.start(["kolourpaint", src], app_id="org.kde.kolourpaint", title="copy-from")[1]
        self.apps.focus(kp, "100%")
        time.sleep(2.5)  # its document loaded
        keys("ctrl+a", "ctrl+c")
        wait_for(lambda: b"image/png" in offered(), "KolourPaint's copy")
        self.need_firefox(mode="clip", copy=colour_hex(c2))
        pages.drain()
        count = len(pages.blobs) + 1
        self.pointer.click(self.apps.at(0.5, 0.75))
        keys("ctrl+v")
        paste = pages.wait(["paste"])
        size, got = blob_colour(count)
        check("image/png" in paste["types"] and size == (120, 90) and close(got, c1),
              f"Firefox got {paste}: {size}, colour {got}, not {c1}")

        # GTK → Qt: the page copies, the same KolourPaint pastes it over its image and saves. (A second KolourPaint
        # started as the first closes showed no document in the guest.)
        self.pointer.click(self.apps.at(0.5, 0.25))
        copied = pages.wait(["copied", "copy-failed"])
        check(copied["kind"] == "copied", f"Firefox could not copy: {copied}")
        dst = src
        self.apps.focus(kp)
        time.sleep(1)
        keys("ctrl+v")
        time.sleep(1)
        self.shot("clipboard-image-kolourpaint")
        keys("ctrl+s")

        def saved():
            try:
                image = Image(dst.read_bytes())
            except (ValueError, zlib.error, struct.error, TypeError):
                return None
            return close(image.pixel(60, 45), c2) and image
        image = wait_for(saved, lambda: "KolourPaint to save the pasted image", 15)
        self.apps.close_window(kp)

        # GTK → X11: Firefox on X11 reads what the Wayland page copied.
        x11 = firefox(self.apps, "x11", pages.url(mode="clip", copy=colour_hex(c3), read=1), x11=True)
        self.apps.focus(x11, "100%")
        time.sleep(2)
        pages.drain()
        count = len(pages.blobs) + 1
        self.pointer.click(self.apps.at(0.5, 0.75))
        read_event = pages.wait(["read", "read-failed"])
        check(read_event["kind"] == "read" and "image/png" in read_event["types"], f"Firefox on X11 read {read_event}")
        size, got = blob_colour(count)
        check(size == (120, 90) and close(got, c2), f"Firefox on X11 read {size}, colour {got}, not {c2}")

        # X11 → GTK: Firefox on X11 copies, the Wayland page reads it.
        self.pointer.click(self.apps.at(0.5, 0.25))
        copied = pages.wait(["copied", "copy-failed"])
        check(copied["kind"] == "copied", f"Firefox on X11 could not copy: {copied}")
        wait_for(lambda: b"TIMESTAMP" in offered(), "the X11 copy to reach Wayland (xwayland-satellite's offer)")
        self.need_firefox(mode="clip", copy="000000", read=1)
        pages.drain()
        count = len(pages.blobs) + 1
        self.pointer.click(self.apps.at(0.5, 0.75))
        read_event = pages.wait(["read", "read-failed"])
        size, got = blob_colour(count)
        check(read_event["kind"] == "read" and size == (120, 90) and close(got, c3),
              f"Firefox read {read_event}: {size}, colour {got}, not {c3}")
        self.passed("clipboard-image", "an image copied in KolourPaint was pasted in Firefox; Firefox's pasted and "
                                       f"saved in KolourPaint ({image.width}×{image.height}) and read by Firefox on "
                                       "X11; that one's copy read by Firefox: each with the colour the copy had")

    # Backends ---------------------------------------------------------------------------------------------------

    def check_backends(self):
        owners = self.monitor.owners()
        seen = {}
        for interface, method, destination in self.monitor.calls(Monitor.broker()):
            seen.setdefault(interface, set()).add(owners.get(destination, destination))
        wrong = {i: sorted(b) for i, b in seen.items() if i in INTENDED and b != {INTENDED[i]}}
        missing = [i for i in INTENDED if i not in seen and self.ran_for(i)]
        conf = Path("/etc/xdg/xdg-desktop-portal/niri-portals.conf").read_text()
        env = run("systemctl", "--user", "show-environment")
        for unit in ("pipewire", "wireplumber", "xdg-desktop-portal"):
            check(subprocess.run(["systemctl", "--user", "is-active", "--quiet", unit]).returncode == 0, f"{unit} is not active")
        check("XDG_CURRENT_DESKTOP=niri" in env.splitlines(), "The session's XDG_CURRENT_DESKTOP is not niri")
        check("FileChooser=" not in conf and "default=gnome;gtk" in conf, f"niri-portals.conf:\n{conf}")
        check(any(self.ran_for(i) for i in INTENDED), "No portal check ran before backends: nothing to check")
        check(not wrong and not missing, f"Backends: {seen}, intended {INTENDED}; wrong {wrong}, never called {missing}")
        self.passed("backends", "; ".join(f"{i} → {', '.join(sorted(b))}" for i, b in sorted(seen.items())))

    def ran_for(self, interface):
        return {"FileChooser": "chooser", "Access": "screenshot", "Screenshot": "screenshot",
                "ScreenCast": "screencast"}[interface] in self.ran

    def stop(self):
        if self.probe:
            self.probe.stop()
        self.apps.stop()
        if self.pages:
            self.pages.stop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", action="append", choices=CHECKS)
    parser.add_argument("--shots", type=Path)
    args = parser.parse_args()
    for tool in ("niri", "wtype", "grim", "wl-paste", "flatpak", "firefox", "nautilus", "kate", "kolourpaint", "gimp-console", "xterm", "foot", "dconf", "busctl", "dbus-monitor", "pw-dump", "flow-pointer"):
        if not shutil.which(tool):
            raise SystemExit(f"{tool} is not on PATH (tests/run_portals_smoke.sh builds the test apps)")
    check(any(w for w in windows().values() if w["workspace_id"] == focused_workspace()["id"]),
          "The focused workspace is empty: the test starts from a workspace in use")
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    chosen = args.only or CHECKS
    state = UserState()
    failures = []
    with tempfile.TemporaryDirectory(prefix="flow-f012-") as tmp:
        smoke = None
        try:
            smoke = Smoke(tmp, args.shots)
            smoke.monitor = Monitor(tmp)
            smoke.ran = []
            for name in chosen:
                smoke.ran.append(name)
                before, started = set(windows()), len(smoke.apps.processes)
                try:
                    if name == "screenshot":
                        smoke.check_screenshot(state)
                    elif name == "screenshot-gimp":
                        smoke.state = state
                        smoke.check_screenshot_gimp()
                    else:
                        getattr(smoke, "check_" + name.replace("-", "_"))()
                except (Failed, RuntimeError, subprocess.SubprocessError) as exc:
                    failures.append({"check": name, "result": "fail", "detail": str(exc)})
                    print(json.dumps(failures[-1]), flush=True)
                    if args.shots:
                        try:
                            smoke.shot(f"failed-{name}")
                        except RuntimeError:
                            pass
                # Each check starts from the windows and apps the previous one found: what one left (a dialog still
                # waiting, an app to come back to) took the next one's keys and clicks in the guest.
                smoke.apps.end(smoke.apps.processes[started:])
                for wid in set(windows()) - before:
                    smoke.apps.close_window({"id": wid})
                if smoke.firefox and smoke.firefox["id"] not in windows():
                    smoke.firefox = None
        finally:
            if smoke:
                smoke.monitor.stop() if hasattr(smoke, "monitor") else None
                smoke.stop()
            problems = state.put_back()
            for problem in problems:
                failures.append({"check": "put back", "result": "fail", "detail": problem})
                print(json.dumps(failures[-1]), flush=True)
    failed = {f["check"] for f in failures}
    unexpected = failed - EXPECTED_FAILURES
    now_passing = (EXPECTED_FAILURES & set(chosen)) - failed
    print(json.dumps({"passed": len(set(chosen) - failed), "failed": sorted(failed),
                      "expected_failures": sorted(failed & EXPECTED_FAILURES), "unexpected": sorted(unexpected),
                      "expected_to_fail_but_passed": sorted(now_passing)}), flush=True)
    sys.exit(1 if unexpected or now_passing else 0)


if __name__ == "__main__":
    main()
