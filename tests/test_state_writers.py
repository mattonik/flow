"""Flow's state writers make each new file durable: its contents synced before the rename, and the folder synced
after it, so a power cut cannot bring back the file from before a write that had finished. The rollback copy of a
version-1 pin file is made the same way, and is durable before the original is converted. A temporary file that a
writer killed outright left is removed by a later write once it is an hour old, and only then."""

import io
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


LABEL = "Café ✓ 日本 \"q\" \\ 🎉"  # not ASCII, and JSON escapes


class FormatTest(unittest.TestCase):
    """Each writer's bytes are what its readers and restore.save's no-op check compare against: JSON with non-ASCII
    kept, encoded as UTF-8."""

    def test_each_writer_writes_non_ascii_text_as_utf8_json(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        groups_data = [{"id": "g", "label": LABEL, "members": ["a", "b"]}]
        groups.write(folder / "groups.json", groups_data)
        pins = [{"id": "p", "app_id": "a", "label": LABEL}]
        pin_store.write(folder / "spaces.json", pins, {}, 2)
        snapshot = {"version": 1, "session": None, "windows": [{"app_id": "a", "label": LABEL}], "groups": []}
        restore.write_snapshot(folder / "session.json", snapshot)
        expected = {"groups.json": {"version": 1, "groups": groups_data},
                    "spaces.json": {"version": 2, "displays": {}, "pins": pins},
                    "session.json": snapshot}
        for name, data in expected.items():
            with self.subTest(name=name):
                self.assertEqual((folder / name).read_bytes(), json.dumps(data, ensure_ascii=False).encode("utf-8"))
        self.assertEqual(groups.load(folder)[0]["label"], LABEL)
        self.assertEqual(pin_store.read(folder / "spaces.json")[0][0]["label"], LABEL)

    def test_the_rollback_copy_keeps_the_originals_mode(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        target = folder / "spaces.json"
        target.write_text(json.dumps(LEGACY))
        target.chmod(0o640)
        pin_store.write(target, LEGACY, {}, 1)
        self.assertEqual(stat.S_IMODE(os.stat(folder / "spaces.v1.json").st_mode), 0o640)


# A child that writes the state folder as Flow's callers do, under its locks.
LOCKED = """import sys
from pathlib import Path
import groups, pin_store, restore
state = Path(sys.argv[1])
with restore.flow_locks(state):
    {write}
"""
WRITE_GROUPS = "groups.write(state / 'groups.json', [{'id': 'g', 'label': 'New', 'members': ['a', 'b']}])"
WRITE_PINS = "pin_store.write(state / 'spaces.json', [], {}, 2)"


class LeftBehindTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.state = self.tmp / "flow"
        self.state.mkdir()
        self.env = dict(os.environ, PYTHONPATH=str(SESSION), XDG_STATE_HOME=str(self.tmp))

    def child(self, write, **env):
        return subprocess.Popen([sys.executable, "-c", LOCKED.format(write=write), str(self.state)],
                                env=dict(self.env, **env), stderr=subprocess.PIPE, text=True)

    def left(self):
        return sorted(p.name for p in self.state.iterdir() if p.name.startswith(state_files.TEMP_PREFIX))

    def paused(self, flag, writer):
        end = time.monotonic() + 20
        while not flag.exists():
            self.assertIsNone(writer.poll(), "the writer ended before its pause")
            self.assertLess(time.monotonic(), end, "the writer never paused")
            time.sleep(0.01)

    def test_a_sweep_removes_writers_temporary_files_once_stale_and_nothing_else(self):
        now = time.time()
        stale = [state_files.TEMP_PREFIX + "a1b2c3", state_files.TEMP_PREFIX + "x"]
        fresh = [state_files.TEMP_PREFIX + "live"]
        kept = ["spaces.json", "groups.json", "spaces.lock", "tmpk3_x9qz0", "notes"]
        for name in stale + fresh + kept:
            (self.state / name).write_text("{}")
            age = state_files.STALE + 1 if name in stale + kept else state_files.STALE - 60
            os.utime(self.state / name, (now - age, now - age))
        (self.state / (state_files.TEMP_PREFIX + "folder")).mkdir()  # not a file: left, and no error
        os.utime(self.state / (state_files.TEMP_PREFIX + "folder"), (now - 2 * state_files.STALE,) * 2)
        with patch("sys.stderr", new_callable=io.StringIO) as said:
            self.assertEqual(sorted(state_files.sweep(self.state, now)), sorted(stale))
        self.assertEqual(sorted(said.getvalue().splitlines()),
                         sorted(f"flow: removed {self.state / name}, a temporary file left by a writer that was stopped"
                                for name in stale))
        self.assertEqual(sorted(p.name for p in self.state.iterdir()),
                         sorted(fresh + kept + [state_files.TEMP_PREFIX + "folder"]))

    def test_the_next_write_removes_what_a_killed_writer_left_once_stale_and_says_so(self):
        flag = self.tmp / "paused"
        writer = self.child(WRITE_GROUPS, FLOW_TEST_PAUSE=f"groups.write={flag}")
        self.paused(flag, writer)
        writer.send_signal(signal.SIGKILL)
        writer.communicate(timeout=10)
        left = self.left()
        self.assertEqual(len(left), 1, "the killed writer left its temporary file")
        again = self.child(WRITE_PINS)
        _, err = again.communicate(timeout=30)
        self.assertEqual(again.returncode, 0, err)
        self.assertEqual(self.left(), left, "a fresh one may be another writer's: kept")
        old = time.time() - state_files.STALE - 1
        os.utime(self.state / left[0], (old, old))
        again = self.child(WRITE_PINS)
        _, err = again.communicate(timeout=30)
        self.assertEqual(again.returncode, 0, err)
        self.assertEqual(self.left(), [])
        self.assertIn(f"removed {self.state / left[0]}", err)

    def test_a_write_beside_one_in_progress_leaves_it_alone(self):
        """A writer paused before its rename; another write meanwhile, which does not wait on the locks (as a
        writer that skipped them would not). The first must still rename its temporary file into place."""
        flag = self.tmp / "paused"
        first = self.child("import pause_point; pause_point.PAUSE = 5.0\n    " + WRITE_GROUPS,
                           FLOW_TEST_PAUSE=f"groups.write={flag}")
        self.paused(flag, first)
        beside = subprocess.run([sys.executable, "-c", "import sys, pin_store; from pathlib import Path; "
                                 "pin_store.write(Path(sys.argv[1]) / 'spaces.json', [], {}, 2)", str(self.state)],
                                env=self.env, stderr=subprocess.PIPE, text=True, timeout=30)
        self.assertEqual(beside.returncode, 0, beside.stderr)
        self.assertIsNone(first.poll(), "the first writer was still paused while the other one wrote")
        self.assertEqual(len(self.left()), 1, "the first writer's temporary file was left alone")
        _, err = first.communicate(timeout=30)
        self.assertEqual(first.returncode, 0, f"the first writer did not finish its write: {err}")
        self.assertEqual(json.loads((self.state / "groups.json").read_text())["groups"][0]["label"], "New")
        self.assertEqual(self.left(), [])


if __name__ == "__main__":
    unittest.main()
