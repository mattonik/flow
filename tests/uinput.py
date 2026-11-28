"""Real input devices for the guest tests: a keyboard and a touchpad made with the kernel's uinput, and on request
a touchscreen (only its presence is used: the shell shows the home line when one is plugged in, ADR-0014).

Niri reads them through libinput as it reads a laptop's own, so a key press reaches Niri's bindings (keys sent
through Niri's virtual keyboard, as ``wtype`` sends them, never do) and a four-finger swipe goes through
libinput's gesture recogniser to Niri's touchpad gestures. What is not real is the hand: no palm, no jitter, no
firmware, and fingers that move in step.

``Devices()`` starts this file as root (``sudo -n``: /dev/uinput is root's) in a child that makes both devices
and runs one request per line; closing it, or the test exiting, removes them with every key released.
"""

import atexit
import fcntl
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time

# linux/uinput.h and linux/input-event-codes.h
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_ABSBIT, UI_SET_PROPBIT = 0x40045564, 0x40045565, 0x40045567, 0x4004556E
UI_DEV_SETUP, UI_ABS_SETUP, UI_DEV_CREATE, UI_DEV_DESTROY = 0x405C5503, 0x401C5504, 0x5501, 0x5502
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
ABS_X, ABS_Y, ABS_MT_SLOT, ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID = 0x00, 0x01, 0x2F, 0x35, 0x36, 0x39
BTN_LEFT, BTN_TOOL_FINGER, BTN_TOUCH = 0x110, 0x145, 0x14A
BTN_TOOL_DOUBLETAP, BTN_TOOL_TRIPLETAP, BTN_TOOL_QUADTAP, BTN_TOOL_QUINTTAP = 0x14D, 0x14E, 0x14F, 0x148
INPUT_PROP_POINTER, INPUT_PROP_DIRECT, INPUT_PROP_BUTTONPAD = 0x00, 0x01, 0x02
TOOLS = {1: BTN_TOOL_FINGER, 2: BTN_TOOL_DOUBLETAP, 3: BTN_TOOL_TRIPLETAP, 4: BTN_TOOL_QUADTAP, 5: BTN_TOOL_QUINTTAP}

KEYS = {"esc": 1, "tab": 15, "enter": 28, "leftctrl": 29, "leftshift": 42, "leftalt": 56, "home": 102, "up": 103,
        "left": 105, "right": 106, "end": 107, "down": 108, "leftmeta": 125, "super": 125}
KEYS.update({str(n): 1 + n for n in range(1, 10)}, **{"0": 11})
KEYS.update({c: code for c, code in zip("qwertyuiop", range(16, 26))})
KEYS.update({c: code for c, code in zip("asdfghjkl", range(30, 39))})
KEYS.update({c: code for c, code in zip("zxcvbnm", range(44, 51))})

# A 100 × 62 mm touchpad: libinput reads sizes from the resolution, in units per millimetre.
PAD_W, PAD_H, PAD_RES = 4000, 2480, 40
# A 290 × 181 mm touchscreen (a 13-inch panel's size).
SCREEN_W, SCREEN_H, SCREEN_RES = 2900, 1810, 10


class Device:
    def __init__(self, name, keys=(), absolute=(), props=()):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_SYN)
        if keys:
            fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
            for key in keys:
                fcntl.ioctl(self.fd, UI_SET_KEYBIT, key)
        if absolute:
            fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_ABS)
        for code, low, high, resolution in absolute:
            fcntl.ioctl(self.fd, UI_SET_ABSBIT, code)
            fcntl.ioctl(self.fd, UI_ABS_SETUP, struct.pack("HxxiiiiiI", code, 0, low, high, 0, 0, resolution))
        for prop in props:
            fcntl.ioctl(self.fd, UI_SET_PROPBIT, prop)
        # struct uinput_setup: input_id (bus, vendor, product, version), name[80], ff_effects_max
        fcntl.ioctl(self.fd, UI_DEV_SETUP, struct.pack("HHHH80sI", 0x03, 0x1D6B, 0xF007, 1, name.encode(), 0))
        fcntl.ioctl(self.fd, UI_DEV_CREATE)

    def emit(self, *events):
        """Each event is (type, code, value); a SYN_REPORT ends the frame."""
        data = b"".join(struct.pack("llHHi", 0, 0, *event) for event in events)
        os.write(self.fd, data + struct.pack("llHHi", 0, 0, EV_SYN, 0, 0))

    def close(self):
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        finally:
            os.close(self.fd)


class Server:
    def __init__(self):
        self.keyboard = Device("Flow test keyboard", keys=sorted(set(KEYS.values())))
        self.pad = Device("Flow test touchpad", keys=[BTN_LEFT, BTN_TOUCH, *TOOLS.values()],
                          absolute=[(ABS_X, 0, PAD_W, PAD_RES), (ABS_Y, 0, PAD_H, PAD_RES), (ABS_MT_SLOT, 0, 4, 0),
                                    (ABS_MT_POSITION_X, 0, PAD_W, PAD_RES), (ABS_MT_POSITION_Y, 0, PAD_H, PAD_RES),
                                    (ABS_MT_TRACKING_ID, 0, 65535, 0)],
                          props=[INPUT_PROP_POINTER, INPUT_PROP_BUTTONPAD])
        self.held = []
        self.tracking = 0
        self.touchscreen = None

    def set_touchscreen(self, on):
        """Plug a touchscreen in (a direct multitouch device), or take it out."""
        if on and not self.touchscreen:
            self.touchscreen = Device("Flow test touchscreen", keys=[BTN_TOUCH],
                                      absolute=[(ABS_X, 0, SCREEN_W, SCREEN_RES), (ABS_Y, 0, SCREEN_H, SCREEN_RES),
                                                (ABS_MT_SLOT, 0, 9, 0), (ABS_MT_POSITION_X, 0, SCREEN_W, SCREEN_RES),
                                                (ABS_MT_POSITION_Y, 0, SCREEN_H, SCREEN_RES),
                                                (ABS_MT_TRACKING_ID, 0, 65535, 0)],
                                      props=[INPUT_PROP_DIRECT])
            time.sleep(1)  # udev and libinput add it
        elif not on and self.touchscreen:
            self.touchscreen.close()
            self.touchscreen = None
            time.sleep(0.5)

    def keys(self, names, hold_ms=40):
        """Press the keys in order and release them in reverse, as a chord."""
        codes = [KEYS[name] for name in names]
        for code in codes:
            self.keyboard.emit((EV_KEY, code, 1))
            self.held.append(code)
            time.sleep(0.01)
        time.sleep(hold_ms / 1000)
        self.release()

    def release(self):
        while self.held:
            self.keyboard.emit((EV_KEY, self.held.pop(), 0))
            time.sleep(0.01)

    def swipe(self, fingers, dx, dy, steps=24, pause_ms=10):
        """``fingers`` fingers down side by side in the pad's middle, moved together by (dx, dy) pad units in
        ``steps`` frames ``pause_ms`` apart, then lifted."""
        starts = [(PAD_W // 2 + (i - (fingers - 1) / 2) * 400, PAD_H // 2) for i in range(fingers)]

        def frame(at, down=None):
            events = []
            for slot, (x, y) in enumerate(at):
                events.append((EV_ABS, ABS_MT_SLOT, slot))
                if down is not None:
                    self.tracking += 1
                    events.append((EV_ABS, ABS_MT_TRACKING_ID, self.tracking))
                events += [(EV_ABS, ABS_MT_POSITION_X, round(x)), (EV_ABS, ABS_MT_POSITION_Y, round(y))]
            events += [(EV_ABS, ABS_X, round(at[0][0])), (EV_ABS, ABS_Y, round(at[0][1]))]
            if down is not None:
                events += [(EV_KEY, BTN_TOUCH, 1), (EV_KEY, TOOLS[fingers], 1)]
            self.pad.emit(*events)

        frame(starts, down=True)
        for step in range(1, steps + 1):
            time.sleep(pause_ms / 1000)
            frame([(x + dx * step / steps, y + dy * step / steps) for x, y in starts])
        time.sleep(pause_ms / 1000)
        lift = []
        for slot in range(fingers):
            lift += [(EV_ABS, ABS_MT_SLOT, slot), (EV_ABS, ABS_MT_TRACKING_ID, -1)]
        self.pad.emit(*lift, (EV_KEY, BTN_TOUCH, 0), (EV_KEY, TOOLS[fingers], 0))

    def close(self):
        try:
            self.release()
        finally:
            self.keyboard.close()
            self.pad.close()
            if self.touchscreen:
                self.touchscreen.close()


def serve(touchscreen=False):
    server = Server()
    try:
        server.set_touchscreen(touchscreen)
        time.sleep(1.5)  # udev and libinput add the devices; events before that are lost
        print("ready", flush=True)
        for line in sys.stdin:
            request = json.loads(line)
            if "keys" in request:
                server.keys(request["keys"], request.get("hold_ms", 40))
            elif "swipe" in request:
                server.swipe(**request["swipe"])
            elif "touchscreen" in request:
                server.set_touchscreen(request["touchscreen"])
            print("ok", flush=True)
    finally:
        server.close()


class Devices:
    """The test's side: a keyboard and a touchpad, and a touchscreen with ``touchscreen=True``, alive until
    ``close()`` or exit."""

    def __init__(self, touchscreen=False):
        self.process = subprocess.Popen(["sudo", "-n", sys.executable, "-I", str(Path(__file__).resolve()), "serve",
                                         *(["touchscreen"] if touchscreen else [])],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        atexit.register(self.close)
        if self.process.stdout.readline().strip() != "ready":
            raise RuntimeError("The uinput devices could not be made (sudo -n, /dev/uinput)")

    def ask(self, request):
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        if self.process.stdout.readline().strip() != "ok":
            raise RuntimeError(f"The uinput helper failed on {request}")

    def keys(self, *names, hold_ms=40):
        """``keys("super", "h")``: Super+H, pressed and released on the test keyboard."""
        self.ask({"keys": list(names), "hold_ms": hold_ms})

    def touchscreen(self, on):
        """Plug the touchscreen in, or take it out."""
        self.ask({"touchscreen": bool(on)})

    def swipe(self, fingers, dx, dy, steps=24, pause_ms=10):
        self.ask({"swipe": {"fingers": fingers, "dx": dx, "dy": dy, "steps": steps, "pause_ms": pause_ms}})

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()


if __name__ == "__main__" and sys.argv[1:2] == ["serve"]:
    serve(touchscreen=sys.argv[2:] == ["touchscreen"])
