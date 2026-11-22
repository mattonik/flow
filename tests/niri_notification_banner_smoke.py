#!/usr/bin/env python3
"""Notification banners of a development shell, on a private bus.

Run inside a Niri session from the Flow checkout under ``dbus-run-session``, with ``FLOW_SHELL`` set
to the development ``shell.qml``. The shell owns the private bus's notification name, so the real
session's notifications are untouched; the test sends notifications with ``busctl`` and reads what the
banner shows through the shell's IPC (``banner``: the summary on screen, or ""). It runs with its own
``XDG_RUNTIME_DIR`` and state, is a child subreaper so a leaked event stream is caught, and checksums
real Flow state files.

Checked: a normal notification shows for about 5 s and then goes; a low-urgency one never shows; a
newer notification replaces the shown one; one that replaces an earlier notification (replaces_id) shows
again; one the app closes leaves at once; quick settings and Desk hide a banner, which shows when they
close; one that arrives with the panel open shows no banner after; a critical one stays past 9 s, a
normal one does not push it off, and the panel takes it for good. With the pointer (tests/pointer.py, so
``flow-pointer`` on PATH and the installed shell stopped): resting on a banner holds it, and its 5 s start
again when the pointer leaves; a click runs the default action (``ActionInvoked``), or opens the panel when
there is none; × dismisses it (``NotificationClosed``, reason 2).

``banner`` reports the shell's state (what the banner is set to show), not what the compositor draws;
stacking and rendering are checked by screenshots.
"""

import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from pointer import Pointer, center

NAME = "org.freedesktop.Notifications"
URGENCY = {"low": 0, "normal": 1, "critical": 2}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def wait_for(test, label, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = test()
        if value:
            return value
        time.sleep(0.2)
    raise RuntimeError(f"Timed out waiting for {label() if callable(label) else label}")


def main():
    shell = os.environ.get("FLOW_SHELL")
    bus = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    if not shell or "unix:" not in bus:
        raise RuntimeError("Run under dbus-run-session with FLOW_SHELL set")
    real_runtime = Path(os.environ["XDG_RUNTIME_DIR"])
    wayland = os.environ.get("WAYLAND_DISPLAY", "wayland-1")
    ctypes.CDLL(None).prctl(36, 1, 0, 0, 0)  # PR_SET_CHILD_SUBREAPER
    real_state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "flow"
    real_files = [real_state / name for name in ("spaces.json", "groups.json", "session.json")]
    real_digests = [digest(path) for path in real_files]
    steps, result, shell_process = [], None, None

    def orphans():
        out = subprocess.run(["ps", "-eo", "pid,ppid,cmd"], text=True, stdout=subprocess.PIPE).stdout
        return {line.split()[0] for line in out.splitlines()[1:]
                if line.split()[1] == str(os.getpid()) and "event-stream" in line}

    with tempfile.TemporaryDirectory(prefix="flow-banner-") as tmp:
        runtime = Path(tmp) / "runtime"
        runtime.mkdir(mode=0o700)
        env = dict(os.environ, XDG_STATE_HOME=tmp, XDG_CONFIG_HOME=str(Path(tmp) / "config"), XDG_RUNTIME_DIR=str(runtime),
                   WAYLAND_DISPLAY=wayland if wayland.startswith("/") else str(real_runtime / wayland))

        def ipc(*call):
            try:
                return subprocess.run(["quickshell", "ipc", "-p", shell, "call", "desk", *call], env=env, text=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10).stdout.strip()
            except subprocess.SubprocessError:
                return None

        def notify(summary, urgency="normal", replaces=0, actions=()):
            out = subprocess.run(["busctl", f"--address={bus}", "--", "call", NAME, "/org/freedesktop/Notifications", NAME,
                                  "Notify", "susssasa{sv}i", "Smoke", str(replaces), "", summary, "A body line",
                                  str(len(actions)), *actions, "1", "urgency", "y", str(URGENCY[urgency]), "-1"],
                                 text=True, stdout=subprocess.PIPE, check=True).stdout
            return int(out.split()[1])

        def close(ident):
            subprocess.run(["busctl", f"--address={bus}", "call", NAME, "/org/freedesktop/Notifications", NAME,
                            "CloseNotification", "u", str(ident)], check=True, stdout=subprocess.DEVNULL)

        def owner():
            answer = subprocess.run(["busctl", f"--address={bus}", "call", "org.freedesktop.DBus", "/org/freedesktop/DBus",
                                     "org.freedesktop.DBus", "GetConnectionUnixProcessID", "s", NAME], text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            return int(answer.stdout.split()[1]) if answer.returncode == 0 else None

        def surfaces():
            try:
                return json.loads(ipc("surfaces") or "{}")
            except ValueError:
                return {}

        def target(name):
            try:
                return json.loads(ipc("pointer_target", name) or "null")
            except ValueError:
                return None

        signal_log = Path(tmp) / "signals"

        def signals(member):
            """The payloads of the notification signals named member that the shell has sent so far."""
            found = []
            for line in signal_log.read_text().splitlines():
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if message.get("member") == member:
                    found.append(message["payload"]["data"])
            return found

        def stays(value, seconds):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                if ipc("banner") != value:
                    raise RuntimeError(f"The banner showed {ipc('banner')!r}, expected {value!r} for {seconds} s")
                time.sleep(0.25)

        pointer, monitor = Pointer(), None
        try:
            with open(signal_log, "w") as log:
                monitor = subprocess.Popen(["busctl", f"--address={bus}", "--json=short", "monitor", "--match",
                                            f"type=signal,interface={NAME}"], stdout=log, stderr=subprocess.DEVNULL)
            shell_process = subprocess.Popen(["quickshell", "-p", shell], env=env, stdin=subprocess.DEVNULL,
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            wait_for(lambda: ipc("banner") == "", "the development shell", timeout=20)
            wait_for(lambda: owner() == shell_process.pid, lambda: f"the shell to own {NAME} ({owner()})")

            started = time.monotonic()
            notify("Normal one")
            wait_for(lambda: ipc("banner") == "Normal one", lambda: f"the banner ({ipc('banner')!r})", timeout=3)
            stays("Normal one", started + 4 - time.monotonic())
            wait_for(lambda: ipc("banner") == "", "the banner to go", timeout=4)
            gone = time.monotonic() - started
            if not 4.5 <= gone <= 6.5:
                raise RuntimeError(f"The banner showed for {gone:.1f} s, not about 5 s")
            steps.append(f"a normal notification's banner showed and went after {gone:.1f} s")

            notify("Low one", "low")
            stays("", 2)
            steps.append("a low-urgency notification showed no banner")

            notify("First")
            wait_for(lambda: ipc("banner") == "First", "the first banner", timeout=3)
            notify("Second")
            wait_for(lambda: ipc("banner") == "Second", lambda: f"the second to replace it ({ipc('banner')!r})", timeout=3)
            steps.append("a newer notification replaced the shown banner")

            first = notify("Chat: 1 new message")
            wait_for(lambda: ipc("banner") == "Chat: 1 new message", "the chat banner", timeout=3)
            ipc("notifications")
            wait_for(lambda: ipc("banner") == "", "the panel to take the chat banner", timeout=3)
            ipc("notifications")
            notify("Chat: 2 new messages", replaces=first)
            wait_for(lambda: ipc("banner") == "Chat: 2 new messages",
                     lambda: f"a replacing notification to show again ({ipc('banner')!r})", timeout=3)
            steps.append("a notification that replaced an earlier one (replaces_id) showed its banner again")

            ident = notify("Closed by app")
            wait_for(lambda: ipc("banner") == "Closed by app", "the banner of the one to be closed", timeout=3)
            close(ident)
            wait_for(lambda: ipc("banner") == "", "the banner to leave when the app closed it", timeout=2)
            steps.append("a notification the app closed left the banner at once")

            ipc("quick")
            notify("Under quick settings")
            stays("", 1.5)
            ipc("quick")
            wait_for(lambda: ipc("banner") == "Under quick settings", "the banner once quick settings closed", timeout=3)
            steps.append("quick settings hid a banner while open, and it showed when quick settings closed")

            ipc("toggle")
            wait_for(lambda: ipc("desk_state") == "open", "Desk to open", timeout=3)
            notify("Under Desk")
            stays("", 6)  # longer than the banner's 5 s: it counts only while it can be seen
            ipc("leave")
            wait_for(lambda: ipc("banner") == "Under Desk", "the banner once Desk closed", timeout=3)
            steps.append("Desk hid a banner for 6 s, and it showed when Desk closed, its 5 s not spent")

            ipc("notifications")
            notify("While the panel is open")
            stays("", 1)
            ipc("notifications")
            stays("", 1.5)
            steps.append("a notification that arrived with the panel open showed no banner after it closed")

            notify("Critical one", "critical")
            wait_for(lambda: ipc("banner") == "Critical one", "the critical banner", timeout=3)
            notify("Normal after critical")
            stays("Critical one", 9)
            ipc("notifications")
            wait_for(lambda: ipc("banner") == "", "the panel to take the critical banner", timeout=3)
            ipc("notifications")
            stays("", 1.5)
            steps.append("a critical banner stayed past 9 s, a normal notification did not push it off, "
                         "and it left when the panel opened and did not come back")

            # The pointer.
            pointer.park()
            notify("Held under the pointer")
            card = wait_for(lambda: ipc("banner") == "Held under the pointer" and target("banner"), "the banner to hold", timeout=3)
            pointer.move(center(card))
            wait_for(lambda: (t := target("banner")) and t["hovered"], "the banner to notice the pointer", timeout=2)
            stays("Held under the pointer", 7)
            pointer.park()
            left = time.monotonic()
            wait_for(lambda: ipc("banner") == "", "the banner to go once the pointer left", timeout=8)
            gone = time.monotonic() - left
            if not 4 <= gone <= 6.5:
                raise RuntimeError(f"The banner went {gone:.1f} s after the pointer left, not about 5 s")
            steps.append(f"the pointer resting on a banner held it for 7 s, and it went {gone:.1f} s after the pointer left")

            opened = notify("Open me", actions=["default", "Open"])
            card = wait_for(lambda: ipc("banner") == "Open me" and target("banner"), "the banner with a default action", timeout=3)
            pointer.click(center(card))
            wait_for(lambda: [opened, "default"] in signals("ActionInvoked"), lambda: f"ActionInvoked ({signals('ActionInvoked')})", timeout=3)
            wait_for(lambda: ipc("banner") == "", "the clicked banner to go", timeout=2)
            if surfaces().get("notifications"):
                raise RuntimeError("A click on a banner with a default action opened the panel as well")
            steps.append("a click on a banner ran the notification's default action, and opened no panel")

            notify("No action here")
            card = wait_for(lambda: ipc("banner") == "No action here" and target("banner"), "the banner without actions", timeout=3)
            pointer.click(center(card))
            wait_for(lambda: surfaces().get("notifications"), "a click on a banner without actions to open the panel", timeout=3)
            ipc("notifications")
            wait_for(lambda: not surfaces().get("notifications"), "the panel to close", timeout=3)
            steps.append("a click on a banner without a default action opened the panel")

            dismissed = notify("Dismiss me", actions=["default", "Open"])
            wait_for(lambda: ipc("banner") == "Dismiss me" and target("banner_dismiss"), "the banner to dismiss", timeout=3)
            pointer.click(center(target("banner_dismiss")))
            wait_for(lambda: [dismissed, 2] in signals("NotificationClosed"),
                     lambda: f"NotificationClosed, reason 2 ({signals('NotificationClosed')})", timeout=3)
            if ipc("banner") != "" or surfaces().get("notifications") or [dismissed, "default"] in signals("ActionInvoked"):
                raise RuntimeError("× left the banner, opened the panel or ran the default action")
            steps.append("× on a banner dismissed the notification (closed, reason 2) without running its action")
            pointer.park()
            result = {"result": "pass", "steps": steps}
        finally:
            if monitor:
                monitor.terminate()
                monitor.wait(timeout=5)
            if shell_process and shell_process.poll() is None:
                shell_process.terminate()
                try:
                    shell_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    shell_process.kill()
        time.sleep(1)
        if orphans():
            raise RuntimeError(f"The shell left its event stream running: {orphans()}")
    if [digest(path) for path in real_files] != real_digests:
        raise RuntimeError("A real Flow Group, pin, or session file changed")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, KeyError, subprocess.SubprocessError, ValueError) as exc:
        print(f"Notification banner test failed: {exc}", file=sys.stderr)
        sys.exit(1)
