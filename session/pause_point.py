"""A test-only pause inside Flow's state writers, so a restart test can kill the shell while one is mid-write.

``FLOW_TEST_PAUSE=<point>=<flag file>`` makes the process that reaches ``<point>`` (``groups.write``,
``spaces.write`` or ``session.write``: each writer's temporary file written and synced, the rename not yet
done) create ``<flag file>`` holding its PID, then sleep for ``PAUSE`` seconds before going on. The test waits
for the flag and kills the shell, or the writer itself, inside that window
(tests/niri_shell_restart_controller_smoke.py).

It is gated twice, so an installed session can never pause: the variable must be set, and ``XDG_STATE_HOME`` must
be set to a folder other than ``~/.local/state`` with the flag file inside it. The smokes set both only for the
development shell they start with isolated state. Without the variable a writer pays one dictionary lookup.
"""
import os
from pathlib import Path
import time

PAUSE = 30.0  # s; long enough to kill in, short enough that a test that never does still ends


def reached(point):
    spec = os.environ.get("FLOW_TEST_PAUSE", "")
    name, _, flag = spec.partition("=")
    if name != point or not flag:
        return False
    state = os.environ.get("XDG_STATE_HOME")
    if not state:
        return False
    state = Path(state).resolve()
    flag = Path(flag)
    if state == (Path.home() / ".local/state").resolve() or state not in flag.resolve().parents:
        return False
    flag.write_text(str(os.getpid()))
    time.sleep(PAUSE)
    return True
