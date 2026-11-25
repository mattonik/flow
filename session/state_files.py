"""Flow's state folder: how its writers name their temporary files, and the removal of those that writers killed
outright (SIGKILL, a power cut) left behind.

Every writer of the state folder (groups.write, pin_store's, restore.write_snapshot) holds spaces.lock while it
writes, so whoever holds spaces.lock finds no temporary file in use: spaces.py removes them each time it takes the
lock, which every refresh, the controller's included, and every action does.
"""
import os

TEMP_PREFIX = ".flow-write-"
_TEMPFILE_NAME = set("abcdefghijklmnopqrstuvwxyz0123456789_")


def left_behind(name):
    """Whether ``name`` is a writer's temporary file: Flow's prefix, or tempfile's own name (tmp and eight of its
    characters) from before the writers used the prefix."""
    return name.startswith(TEMP_PREFIX) or (len(name) == 11 and name.startswith("tmp") and set(name[3:]) <= _TEMPFILE_NAME)


def sweep(folder):
    """Remove writers' temporary files from ``folder``; the names removed. Only while holding spaces.lock."""
    removed = []
    try:
        names = os.listdir(folder)
    except FileNotFoundError:
        return removed
    for name in names:
        if left_behind(name):
            try:
                os.unlink(os.path.join(folder, name))
                removed.append(name)
            except OSError:  # gone meanwhile, or a folder by that name: nothing a refresh should fail on
                pass
    return removed
