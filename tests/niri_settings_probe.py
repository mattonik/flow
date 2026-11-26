#!/usr/bin/env python3
"""The automatic-recovery setting as Qt's Settings keeps it, run in Quickshell from the shell's own source.

The shell used to keep the setting as ``autoRecovery``, off by default, and every shell that started without the key
wrote ``autoRecovery=false`` back. The setting is now ``sessionRecovery``, on by default, and the old key is not read.
This runs the shell's ``Core.Settings`` block for it (cut from SHELL, default the repository's ``session/shell.qml``)
in a windowless Quickshell, with a config folder of its own, and checks:

- **The migration:** a file as an old shell left it (``appMenuEnabled=false``, ``autoRecovery=false``) starts with
  recovery on, and the shell writes ``sessionRecovery=true`` back.
- **The user's choice holds:** switched off as the Settings switch does it (set, then ``sync()``), it is off at the
  next start, and at the one after.
- **A first start:** no settings file at all starts with recovery on.

Needs Quickshell and the Niri session's Wayland display (read from ``systemctl --user show-environment`` when
unset). Prints one JSON line per check; exits 1 if any failed.
"""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent


def recovery_block(shell):
    """The ``Core.Settings { ... }`` holding ``id: recoveryPreferences``, by brace depth."""
    for match in re.finditer(r"Core\.Settings\s*\{", shell):
        depth, index = 1, match.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(shell[index], 0)
            index += 1
        if re.search(r"\bid:\s*recoveryPreferences\b", shell[match.end():index]):
            return shell[match.start():index]
    raise SystemExit("No Settings block with id recoveryPreferences in the shell")


PROBE = """import QtQuick
import QtCore as Core
import Quickshell
ShellRoot {
    id: root
    readonly property string settingsFile: "file://" + Quickshell.env("XDG_CONFIG_HOME") + "/flow-settings.ini"
    BLOCK
    Component.onCompleted: {
        console.warn("probe-loaded " + JSON.stringify(recoveryPreferences.sessionRecovery))
        if (Quickshell.env("PROBE_SWITCH_OFF") === "1") {
            recoveryPreferences.sessionRecovery = false; recoveryPreferences.sync()  // as the Settings switch does
        }
        quitTimer.start()
    }
    Timer { id: quitTimer; interval: 1000; onTriggered: Qt.quit() }
}
"""


def session_environment():
    env = dict(os.environ)
    if not env.get("WAYLAND_DISPLAY"):
        shown = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True, text=True,
                               check=True).stdout
        for line in shown.splitlines():
            name, _, value = line.partition("=")
            if name in ("WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"):
                env[name] = value
    return env


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shell", type=Path, default=HERE.parent / "session/shell.qml")
    args = parser.parse_args()
    probe = PROBE.replace("BLOCK", recovery_block(args.shell.read_text()))
    results = []

    def check(name, ok, value):
        results.append(bool(ok))
        print(json.dumps({"check": name, "ok": bool(ok), "value": value}), flush=True)

    with tempfile.TemporaryDirectory(prefix="flow-settings-probe-") as tmp:
        tmp = Path(tmp)
        (tmp / "shell.qml").write_text(probe)

        def start(config, switch_off=False):
            """One shell start on ``config``: what it loaded, and the settings file it left."""
            env = dict(session_environment(), XDG_CONFIG_HOME=str(config), PROBE_SWITCH_OFF="1" if switch_off else "0")
            run = subprocess.run(["quickshell", "-p", str(tmp / "shell.qml")], env=env, capture_output=True,
                                 text=True, timeout=30)
            loaded = re.search(r"probe-loaded (\S+)", run.stdout + run.stderr)
            settings = config / "flow-settings.ini"
            return (json.loads(loaded.group(1)) if loaded else None,
                    settings.read_text() if settings.exists() else None)

        def keys(text):
            return dict(re.findall(r"(?m)^(\w+)=(.*)$", text or ""))

        old = tmp / "old"
        old.mkdir()
        (old / "flow-settings.ini").write_text("[General]\nappMenuEnabled=false\nautoRecovery=false\n")
        loaded, text = start(old)
        check("an old shell's file (autoRecovery=false): recovery on, and sessionRecovery=true written back",
              loaded is True and keys(text).get("sessionRecovery") == "true", {"loaded": loaded, "file": keys(text)})

        loaded, text = start(old, switch_off=True)
        check("switched off as the Settings switch does it: sessionRecovery=false written",
              loaded is True and keys(text).get("sessionRecovery") == "false", {"loaded": loaded, "file": keys(text)})
        for start_number in (1, 2):
            loaded, text = start(old)
            check(f"switched off, restart {start_number}: still off, the file unchanged",
                  loaded is False and keys(text).get("sessionRecovery") == "false",
                  {"loaded": loaded, "file": keys(text)})

        fresh = tmp / "fresh"
        fresh.mkdir()
        loaded, text = start(fresh)
        check("no settings file: recovery on, and sessionRecovery=true written",
              loaded is True and keys(text).get("sessionRecovery") == "true", {"loaded": loaded, "file": keys(text)})

    passed = all(results)
    print(json.dumps({"result": "pass" if passed else "fail"}))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
