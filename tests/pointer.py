"""A pointer for the guest tests: moves, clicks, drags and wheel notches through ``flow-pointer``
(tests/flow-pointer/flow-pointer.c, the flake's ``flow-pointer`` package), which uses Niri's virtual-pointer
protocol as wtype uses its virtual keyboard.

Targets come from the shell's ``pointer_target`` IPC call: an item's rectangle in its layer window, the
window's size, anchors and margins, and its screen. ``Pointer.center`` turns one into a position in Niri's
logical layout, the space every output shares.

Run with the installed shell stopped: its home indicator, island and banners sit where a development
shell's do, and a click could reach either.
"""

import atexit
import json
import shutil
import subprocess
import time


def layout_box(outputs):
    """The rectangle every enabled output covers together, as (left, top, width, height) in logical pixels:
    Niri maps a virtual pointer's absolute motion onto it."""
    boxes = [o["logical"] for o in outputs.values() if o.get("logical")]
    if not boxes:
        raise RuntimeError("Niri reports no enabled output")
    left, top = min(b["x"] for b in boxes), min(b["y"] for b in boxes)
    right, bottom = max(b["x"] + b["width"] for b in boxes), max(b["y"] + b["height"] for b in boxes)
    return left, top, right - left, bottom - top


def window_origin(target):
    """Where a layer window sits on its screen, as layer-shell places it from its anchors and margins:
    anchored to one edge it keeps its margin there; anchored to both opposite edges, or neither, it is
    centred between them (inside the margins when anchored to both)."""
    (top, bottom, left, right), (mt, mb, ml, mr) = target["place"]["anchors"], target["place"]["margins"]
    (w, h), (_, _, sw, sh) = target["window"], target["screen"]

    def along(start, end, m_start, m_end, size, room):
        if start and end:
            return m_start + (room - m_start - m_end - size) // 2
        return m_start if start else room - size - m_end if end else (room - size) // 2

    return along(left, right, ml, mr, w, sw), along(top, bottom, mt, mb, h, sh)


def center(target):
    """The middle of a ``pointer_target`` item, in Niri's logical layout."""
    x, y = window_origin(target)
    sx, sy = target["screen"][:2]
    rx, ry, rw, rh = target["rect"]
    return round(sx + x + rx + rw / 2), round(sy + y + ry + rh / 2)


def screen_edge(target, edge):
    """The point on the screen's outermost row (``edge`` "top" or "bottom") under the middle of a ``pointer_target``
    item. A work-view band over an app (the Island, the home indicator, the App button) fades and lets the pointer
    through when it comes from the app; through its screen edge it is the shell's (ADR-0013), as a user reaches it."""
    x, _ = center(target)
    _, sy, _, sh = target["screen"]
    return x, sy if edge == "top" else sy + sh - 1


class Pointer:
    def __init__(self, env=None, allow_installed_shell=False):
        """``allow_installed_shell``: for a test that drives the installed shell itself (niri_search_installed_smoke.py);
        the home indicator, island and banners may then sit under the pointer, so aim at the middle of the screen."""
        self.env = env
        if not shutil.which("flow-pointer", path=(env or {}).get("PATH")):
            raise RuntimeError("flow-pointer is not on PATH (nix build .#flow-pointer)")
        if not allow_installed_shell and subprocess.run(["systemctl", "--user", "is-active", "--quiet", "flow-shell"]).returncode == 0:
            raise RuntimeError("Stop the installed shell first: its surfaces would take the pointer")
        outputs = json.loads(subprocess.check_output(["niri", "msg", "-j", "outputs"], text=True, timeout=10))
        self.left, self.top, self.width, self.height = layout_box(outputs)
        # The pointer stays where the last gesture left it, across runs: one left resting on a banner holds
        # the next run's first banner. So start parked, and park again on the way out, failed or not.
        self.park()
        atexit.register(self._park_quietly)

    def _park_quietly(self):
        try:
            self.park()
        except (OSError, RuntimeError, subprocess.SubprocessError):
            pass

    def run(self, *steps):
        """Runs flow-pointer steps, with ``("move", x, y)`` in layout coordinates."""
        args = []
        for step in steps:
            if isinstance(step, tuple) and step[0] == "move":
                x, y = step[1] - self.left, step[2] - self.top
                if not (0 <= x < self.width and 0 <= y < self.height):
                    raise RuntimeError(f"({step[1]}, {step[2]}) is outside every output")
                args += ["move", str(x), str(y)]
            else:
                args += [str(part) for part in (step if isinstance(step, tuple) else (step,))]
        waited = sum(int(args[i + 1]) for i, arg in enumerate(args) if arg == "wait") / 1000
        subprocess.run(["flow-pointer", str(self.width), str(self.height), *args], env=self.env, check=True,
                       timeout=20 + waited)

    def move(self, at):
        self.run(("move", *at))

    def hover(self, read, label="the target", timeout=10, via=None):
        """Moves onto the item ``read()`` gives (a ``pointer_target``, or None while it is hidden) and waits until
        it shows the pointer, so a click that follows lands on a surface that is on screen. Returns its middle.
        ``via``: the screen edge ("top" or "bottom") to come through first, for a work-view band over an app."""
        end, last, tries = time.monotonic() + timeout, None, 0
        while time.monotonic() < end:
            last = read()
            if last:
                if last["hovered"]:
                    return center(last)
                # Moving again to the same place is not a motion the installed shell's panel answers (measured in the
                # guest): move a few pixels off and back instead, every time it still shows no pointer.
                x, y = center(last)
                if via:
                    self.move(screen_edge(last, via))
                self.move((x + 3 * (tries % 2), y))
                tries += 1
            time.sleep(0.2)
        raise RuntimeError(f"Timed out waiting for {label} to show the pointer ({last})")

    def click(self, at, button="left"):
        self.run(("move", *at), ("wait", 50), ("click", button))

    def scroll(self, at, notches):
        self.run(("move", *at), ("wait", 50), ("scroll", notches))

    def drag(self, start, end, steps=10, pause=16):
        """Press at start, move to end in even steps ``pause`` ms apart, and release: one gesture."""
        path = [("move", round(start[0] + (end[0] - start[0]) * i / steps), round(start[1] + (end[1] - start[1]) * i / steps))
                for i in range(1, steps + 1)]
        self.run(("move", *start), ("wait", 50), "down", *[part for move in path for part in (("wait", pause), move)],
                 ("wait", 50), "up")

    def park(self):
        """8 px in from the layout's bottom left corner, where the shell has nothing to hover (the work
        indicator is at the bottom right, the home indicator at the middle; Niri's hot corner is the top
        left), so no hover lingers into the next check."""
        self.move((self.left + 8, self.top + self.height - 8))
