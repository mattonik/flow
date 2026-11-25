"""Flow's state writers make each new file durable: its contents synced before the rename, and the folder synced
after it, so a power cut cannot bring back the file from before a write that had finished. The rollback copy of a
version-1 pin file is made the same way, and is durable before the original is converted. A temporary file that a
writer killed outright left is removed by the next write under Flow's locks, and only then."""

import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "session"))
import groups
import pin_store
import restore
import state_files

SESSION = Path(__file__).resolve().parents[1] / "session"

WRITES = {
    "groups.json": lambda path: groups.write(path, [{"id": "g", "label": "Kept", "members": ["a", "b"]}]),
    "spaces.json": lambda path: pin_store.write(path, [{"id": "p", "app_id": "a", "label": "A"}], {}, 2),
    "session.json": lambda path: restore.write_snapshot(path, {"version": 1, "session": None, "windows": [],
                                                              "groups": []}),
}


LEGACY = [{"id": "p", "app_id": "a", "label": "A", "window_id": 7}]  # a version-1 file: a bare list of pins


class DurableWriteTest(unittest.TestCase):
    def record(self, write, target, before):
        """Run ``write`` with ``target`` holding ``before``; every fsync and rename it made, in order.

        A synced descriptor is known by its device and inode, and a file by its size too: a rename keeps the inode,
        so the file synced before it is the one now in place only if both match, with all its bytes written."""
        target.write_text(json.dumps(before))
        seen, fsync, replace = [], os.fsync, os.replace
        self.temporary = []

        def synced(fd):
            info = os.fstat(fd)
            folder_synced = stat.S_ISDIR(info.st_mode)
            seen.append(("folder" if folder_synced else "file", info.st_dev, info.st_ino,
                         None if folder_synced else info.st_size))
            return fsync(fd)

        def renamed(source, destination):
            seen.append(("rename", Path(destination).name))
            self.temporary.append(Path(source).name)
            return replace(source, destination)

        with patch("os.fsync", synced), patch("os.replace", renamed):
            write(target)
        return seen

    @staticmethod
    def identity(path, folder=False):
        info = os.stat(path)
        return ("folder" if folder else "file", info.st_dev, info.st_ino, None if folder else info.st_size)

    def folder(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        return Path(folder.name)

    def test_each_writer_syncs_its_file_then_renames_then_syncs_the_folder(self):
        for name in WRITES:
            with self.subTest(name=name):
                target = self.folder() / name
                seen = self.record(WRITES[name], target, {})
                self.assertEqual(seen.count(("rename", name)), 1)
                rename = seen.index(("rename", name))
                self.assertIn(self.identity(target), seen[:rename],
                              "the new file, all its bytes written, synced before the rename")
                self.assertIn(self.identity(target.parent, folder=True), seen[rename + 1:],
                              "its folder synced after the rename")
                self.assertTrue(all(name.startswith(state_files.TEMP_PREFIX) for name in self.temporary), self.temporary)

    def test_the_rollback_copy_is_whole_and_durable_before_its_original_is_converted(self):
        target = self.folder() / "spaces.json"
        seen = self.record(lambda path: pin_store.write(path, LEGACY, {}, 1), target, LEGACY)
        rollback = target.parent / "spaces.v1.json"
        self.assertEqual(json.loads(rollback.read_text()), LEGACY)
        self.assertEqual([event for event in seen if event[0] == "rename"],
                         [("rename", "spaces.v1.json"), ("rename", "spaces.json")])
        copied, converted = seen.index(("rename", "spaces.v1.json")), seen.index(("rename", "spaces.json"))
        self.assertIn(self.identity(rollback), seen[:copied], "the copy, all its bytes, synced before its rename")
        self.assertIn(self.identity(target.parent, folder=True), seen[copied + 1:converted],
                      "the copy's name made durable before the original is replaced")
        self.assertEqual(json.loads(target.read_text())["version"], 2)

    def test_without_its_rollback_copy_the_original_is_not_converted(self):
        target = self.folder() / "spaces.json"
        target.write_text(json.dumps(LEGACY))
        before, replace = target.read_bytes(), os.replace

        def refused(source, destination):
            if Path(destination).name == "spaces.v1.json":
                raise OSError("No space left on device")
            return replace(source, destination)

        with patch("os.replace", refused), self.assertRaises(OSError):
            pin_store.write(target, LEGACY, {}, 1)
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in target.parent.iterdir()), ["spaces.json"])


# Children that write the state folder as Flow's callers do: under its locks, or (``bare``) without them.
LOCKED = """import sys
from pathlib import Path
import groups, pin_store, restore
state = Path(sys.argv[1])
with restore.flow_locks(state):
    {write}
"""
BARE = """import sys
from pathlib import Path
import groups, pin_store
state = Path(sys.argv[1])
{write}
"""
WRITE_GROUPS = "groups.write(state / 'groups.json', [{'id': 'g', 'label': 'New', 'members': ['a', 'b']}])"
WRITE_PINS = "pin_store.write(state / 'spaces.json', [], {}, 2)"


LINUX_LOCKS = unittest.skipUnless(Path("/proc/locks").exists(), "who holds a lock: /proc/locks, Linux's")


class LeftBehindTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.state = self.tmp / "flow"
        self.state.mkdir()
        (self.state / "session.lock").touch()
        (self.state / "spaces.lock").touch()
        self.env = dict(os.environ, PYTHONPATH=str(SESSION), XDG_STATE_HOME=str(self.tmp))

    def child(self, script, write, **env):
        return subprocess.Popen([sys.executable, "-c", script.format(write=write), str(self.state)],
                                env=dict(self.env, **env), stderr=subprocess.PIPE, text=True)

    def left(self):
        return sorted(p.name for p in self.state.iterdir() if p.name.startswith(state_files.TEMP_PREFIX))

    def killed_mid_write(self):
        """A writer SIGKILLed between its synced temporary file and the rename; the file it left."""
        flag = self.tmp / "paused"
        writer = self.child(LOCKED, WRITE_GROUPS, FLOW_TEST_PAUSE=f"groups.write={flag}")
        end = time.monotonic() + 20
        while not flag.exists():
            self.assertIsNone(writer.poll(), "the writer ended before its pause")
            self.assertLess(time.monotonic(), end, "the writer never paused")
            time.sleep(0.01)
        writer.send_signal(signal.SIGKILL)
        writer.communicate(timeout=10)
        self.assertEqual(len(self.left()), 1, "the killed writer left its temporary file")
        return self.left()

    def test_a_sweep_removes_writers_temporary_files_and_nothing_else(self):
        temporary = [state_files.TEMP_PREFIX + "a1b2c3", state_files.TEMP_PREFIX + "x"]
        kept = ["spaces.json", "groups.json", "spaces.lock", "tmpk3_x9qz0", "notes"]
        for name in temporary + kept:
            (self.state / name).write_text("{}")
        (self.state / (state_files.TEMP_PREFIX + "folder")).mkdir()  # not a file: left, and no error
        with patch("sys.stderr"):
            self.assertEqual(sorted(state_files.sweep(self.state)), sorted(temporary))
        self.assertEqual(sorted(p.name for p in self.state.iterdir()),
                         sorted(kept + ["session.lock", state_files.TEMP_PREFIX + "folder"]))

    @LINUX_LOCKS
    def test_the_next_write_under_the_locks_removes_what_a_killed_writer_left_and_says_so(self):
        name = self.killed_mid_write()[0]
        writer = self.child(LOCKED, WRITE_PINS)
        _, err = writer.communicate(timeout=30)
        self.assertEqual(writer.returncode, 0, err)
        self.assertEqual(self.left(), [])
        self.assertIn(f"removed {name}", err)

    def test_this_process_holds_the_lock_only_as_proc_locks_lists_it(self):
        lock = self.state / "spaces.lock"
        info = os.stat(lock)
        held = f"{os.major(info.st_dev):02x}:{os.minor(info.st_dev):02x}:{info.st_ino}"
        listing = self.tmp / "locks"
        cases = {f"1: FLOCK  ADVISORY  WRITE {os.getpid()} {held} 0 EOF\n": True,
                 f"1: FLOCK  ADVISORY  WRITE {os.getpid() + 1} {held} 0 EOF\n": False,  # another process holds it
                 f"1: FLOCK  ADVISORY  WRITE 99 {held} 0 EOF\n1: -> FLOCK  ADVISORY  WRITE {os.getpid()} {held} 0 EOF\n": False,
                 f"1: POSIX  ADVISORY  WRITE {os.getpid()} {held} 0 EOF\n": False,
                 "": False}
        for text, expected in cases.items():
            with self.subTest(text=text):
                listing.write_text(text)
                self.assertIs(state_files.holding_lock(self.state, str(listing)), expected)
        self.assertFalse(state_files.holding_lock(self.state, str(self.tmp / "no-such-listing")))
        self.assertFalse(state_files.holding_lock(self.tmp / "missing", str(listing)))

    @LINUX_LOCKS
    def test_a_write_without_the_locks_removes_nothing(self):
        left = self.killed_mid_write()
        writer = self.child(BARE, WRITE_PINS)
        _, err = writer.communicate(timeout=30)
        self.assertEqual(writer.returncode, 0, err)
        self.assertEqual(self.left(), left)

    @LINUX_LOCKS
    def test_a_write_beside_the_one_in_progress_leaves_it_alone(self):
        """A writer paused before its rename, under the locks; meanwhile a second one waits on the locks, and a
        third writes without them. The first must still rename its temporary file into place."""
        flag = self.tmp / "paused"
        first = self.child(LOCKED, "import pause_point; pause_point.PAUSE = 1.0\n    " + WRITE_GROUPS,
                           FLOW_TEST_PAUSE=f"groups.write={flag}")
        end = time.monotonic() + 20
        while not flag.exists():
            self.assertIsNone(first.poll(), "the first writer ended before its pause")
            self.assertLess(time.monotonic(), end, "the first writer never paused")
            time.sleep(0.01)
        second = self.child(LOCKED, WRITE_PINS)
        third = self.child(BARE, WRITE_PINS)
        _, third_err = third.communicate(timeout=30)
        _, first_err = first.communicate(timeout=30)
        _, second_err = second.communicate(timeout=30)
        self.assertEqual(first.returncode, 0, f"the first writer did not finish its write: {first_err}")
        self.assertEqual((second.returncode, third.returncode), (0, 0), second_err + third_err)
        self.assertEqual(json.loads((self.state / "groups.json").read_text())["groups"][0]["label"], "New")
        self.assertEqual(self.left(), [])


if __name__ == "__main__":
    unittest.main()
