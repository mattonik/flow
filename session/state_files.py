"""How Flow writes a file in its state folder: whole or not at all, and durably.

Every caller holds Flow's locks (session.lock, then spaces.lock) while it writes: spaces.py's refresh and
actions, and flow-recovery's saves, restores and renames; ``NamedTemporaryFile`` has no other call site in
session/. So while this process holds spaces.lock, a temporary file in the folder is one that a writer killed
outright (SIGKILL, a power cut) left, and each write removes those first. A write without the lock removes nothing.
"""
import os
import sys

TEMP_PREFIX = ".flow-write-"


def holding_lock(folder, locks="/proc/locks"):
    """Whether this process holds spaces.lock in ``folder``, as /proc/locks lists flock holders. Another process's
    hold says nothing about this writer's: it may be writing without the lock beside a writer that has it. Without
    /proc/locks (not Linux) it says no, and nothing is removed."""
    try:
        info = os.stat(os.path.join(folder, "spaces.lock"))
        with open(locks) as listing:
            lines = listing.read().splitlines()
    except OSError:  # no lock file, so never taken here; or no /proc/locks
        return False
    held = f"{os.major(info.st_dev):02x}:{os.minor(info.st_dev):02x}:{info.st_ino}"
    for line in lines:
        fields = line.split()
        # "1: FLOCK  ADVISORY  WRITE 1234 fe:02:393322 0 EOF"; a waiter's line has "->" after the number
        if len(fields) >= 6 and fields[1] == "FLOCK" and fields[5] == held and fields[4] == str(os.getpid()):
            return True
    return False


def sweep(folder):
    """Remove the temporary files writers left in ``folder``, and say so on stderr; the names removed. Only while
    spaces.lock is held."""
    removed = []
    for name in os.listdir(folder):
        if name.startswith(TEMP_PREFIX):
            try:
                os.unlink(os.path.join(folder, name))
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
    if holding_lock(folder):
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
