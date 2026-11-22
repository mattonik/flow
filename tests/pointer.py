"""A pointer for the guest tests: moves, clicks, drags and wheel notches through ``flow-pointer``
(tests/pointer/flow-pointer.c, the flake's ``flow-pointer`` package), which uses Niri's virtual-pointer
protocol as wtype uses its virtual keyboard.

Targets come from the shell's ``pointer_target`` IPC call: an item's rectangle in its layer window, the
window's size, anchors and margins, and its screen. ``Pointer.center`` turns one into a position in Niri's
logical layout, the space every output shares.

Run with the installed shell stopped: its home indicator, island and banners sit where a development
shell's do, and a click could reach either.
"""

import json
import shutil
import subprocess


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
    """Where a layer window sits on its screen, from its anchors and margins: anchored to an edge it keeps
    its margin there (anchored to both sides, it starts at the left or top one); anchored to neither side,
    it is centred."""
    (top, bottom, left, right), (mt, mb, ml, mr) = target["place"]["anchors"], target["place"]["margins"]
    (w, h), (_, _, sw, sh) = target["window"], target["screen"]
    x = ml if left else sw - w - mr if right else (sw - w) // 2
    y = mt if top else sh - h - mb if bottom else (sh - h) // 2
    return x, y


def center(target):
    """The middle of a ``pointer_target`` item, in Niri's logical layout."""
    x, y = window_origin(target)
    sx, sy = target["screen"][:2]
    rx, ry, rw, rh = target["rect"]
    return round(sx + x + rx + rw / 2), round(sy + y + ry + rh / 2)


class Pointer:
    def __init__(self, env=None):
        self.env = env
        if not shutil.which("flow-pointer", path=(env or {}).get("PATH")):
            raise RuntimeError("flow-pointer is not on PATH (nix build .#flow-pointer)")
        if subprocess.run(["systemctl", "--user", "is-active", "--quiet", "flow-shell"]).returncode == 0:
            raise RuntimeError("Stop the installed shell first: its surfaces would take the pointer")
        outputs = json.loads(subprocess.check_output(["niri", "msg", "-j", "outputs"], text=True, timeout=10))
        self.left, self.top, self.width, self.height = layout_box(outputs)

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
        subprocess.run(["flow-pointer", str(self.width), str(self.height), *args], env=self.env, check=True, timeout=30)

    def move(self, at):
        self.run(("move", *at))

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
        """Away from the shell's edges, so no hover lingers into the next check."""
        self.move((self.left + self.width // 2, self.top + self.height // 2))
