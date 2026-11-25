"""How Flow writes a file in its state folder: whole or not at all, durably, and as UTF-8.

A writer killed outright (SIGKILL, a power cut) between its temporary file and the rename leaves that file behind.
Each write first removes such files that are over ``STALE`` old: a write takes milliseconds, so none that old is in
use. A clock that jumps forward by more than that during a write could make a live one look old: that write then
fails, and the file it would have replaced stays whole.
"""
import os
import sys
import time

TEMP_PREFIX = ".flow-write-"
STALE = 3600.0  # s


def sweep(folder, now=None):
    """Remove the temporary files writers left in ``folder`` over STALE ago, and say so on stderr; the names
    removed."""
    removed, now = [], time.time() if now is None else now
    for name in os.listdir(folder):
        if name.startswith(TEMP_PREFIX):
            path = os.path.join(folder, name)
            try:
                if now - os.lstat(path).st_mtime <= STALE:
                    continue  # perhaps another writer's, being written
                os.unlink(path)
            except OSError:  # gone meanwhile, or a folder by that name: not this write's to fail on
                continue
            removed.append(name)
            print(f"flow: removed {name}, a temporary file left by a writer that was stopped", file=sys.stderr)
    return removed


def replace_durably(path, data, pause=None, like=None):
    """Put ``data`` (bytes) at ``path`` whole or not at all, and durably: a synced temporary file renamed into place,
    then the folder synced. ``pause``: the test-only pause point before the rename (session/pause_point.py).
    ``like``: a file whose mode and times the new one takes."""
    import shutil, tempfile  # here, not above: flow-spaces runs after every burst of Niri events and mostly only reads
    folder = path.parent
    sweep(folder)
    with tempfile.NamedTemporaryFile("wb", dir=folder, prefix=TEMP_PREFIX, delete=False) as tmp:
        try:
            tmp.write(data)
            tmp.flush()
            if like is not None:
                shutil.copystat(like, tmp.name)
            os.fsync(tmp.fileno())
            if pause and "FLOW_TEST_PAUSE" in os.environ:
                import pause_point
                pause_point.reached(pause)
            os.replace(tmp.name, path)
            directory = os.open(folder, os.O_RDONLY)
            try:
                os.fsync(directory)  # make the rename itself durable
            finally:
                os.close(directory)
        finally:
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)
