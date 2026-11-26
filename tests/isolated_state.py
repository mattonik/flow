"""A temporary Flow state folder for the Niri smokes, removed only once nothing started with it runs.

``with IsolatedState(prefix) as tmp:`` replaces ``tempfile.TemporaryDirectory``: a smoke points
``XDG_STATE_HOME`` at ``tmp`` for every Flow process it starts and stops them inside the block. A stopped
shell's ``flow-spaces`` helpers can outlive it, and one still starting writes the folder again, so on
leaving the block the folder is removed only after every process started with it has exited. The smoke
then fails if the folder came back, or such a process appeared, a second later.
"""

import os
from pathlib import Path
import re
import shutil
import signal
import sys
import tempfile
import time


def started_with(entry):
    """PIDs of the processes whose environment, as they were started, holds ``entry`` (``NAME=value``)."""
    found = []
    for proc in Path("/proc").iterdir():
        try:
            if proc.name.isdigit() and entry.encode() in (proc / "environ").read_bytes().split(b"\0"):
                found.append(int(proc.name))
        except OSError:  # gone, or another user's
            pass
    return found


class IsolatedState:
    """A fresh ``XDG_STATE_HOME`` under the system temporary folder; entering gives its path."""

    def __init__(self, prefix):
        self.path = tempfile.mkdtemp(prefix=prefix)

    def __enter__(self):
        return self.path

    def __exit__(self, exc_type, exc, traceback):
        leftover = self.remove()
        if leftover and exc_type is None:
            raise RuntimeError(f"The isolated Flow state outlived the test: {', '.join(leftover)}")
        return False

    def users(self):
        """The development shell and its helpers: everything started with this state."""
        return started_with(f"XDG_STATE_HOME={self.path}")

    def remove(self, timeout=20):
        """Remove the folder once nothing started with it runs; return what outlived the smoke.

        It waits until two scans in a row, 0.2 s apart, find no such process: a helper that forks and
        exits during one scan can escape it (a race the unit tests do not reproduce). A process still
        there after ``timeout`` s is what the smoke exists to catch: it is killed, so the guest stays
        clean, and reported. A second after the folder is removed, the folder, or any process started
        with it, is reported too.
        """
        killed = []

        def gone():
            if self.users():
                return False
            time.sleep(0.2)
            return not self.users()

        end = time.monotonic() + timeout
        while not gone():
            if time.monotonic() >= end:
                for pid in self.users():
                    try:
                        command = (Path("/proc") / str(pid) / "cmdline").read_bytes().replace(b"\0", b" ")
                        os.kill(pid, signal.SIGKILL)
                        killed.append(f"process {pid} ({command.decode(errors='replace').strip()}), killed after {timeout} s")
                    except OSError:  # it exited meanwhile
                        pass
                time.sleep(1)
                break
            time.sleep(0.2)
        shutil.rmtree(self.path, ignore_errors=True)
        time.sleep(1)
        leftover = (killed + ([f"folder {self.path}"] if Path(self.path).exists() else [])
                    + [f"process {pid}" for pid in self.users()])
        if leftover:
            print(f"Left behind: {', '.join(leftover)}", file=sys.stderr)
        return leftover


def shell_config(tmp):
    """A configuration folder in ``tmp`` for a development shell: the user's Flow settings and appearance, with
    automatic recovery switched off. It is on by default, and a shell on the user's own configuration would run
    it against the user's recovery marker (opening Settings when it is paused) or save the smoke's windows. Set
    it as the shell's ``XDG_CONFIG_HOME`` only: apps the smoke opens itself keep the user's configuration, though
    apps the shell launches would inherit the copy."""
    real = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    config = Path(tmp) / "shell-config"
    config.mkdir(exist_ok=True)
    for name in ("flow-settings.ini", "floe-appearance.ini"):
        if (real / name).is_file():
            shutil.copy2(real / name, config / name)
    settings = config / "flow-settings.ini"
    text = re.sub(r"(?m)^sessionRecovery=.*\n?", "", settings.read_text() if settings.exists() else "")
    off = "[General]\nsessionRecovery=false\n"
    settings.write_text(text.replace("[General]\n", off, 1) if "[General]\n" in text else off + text)
    return str(config)
