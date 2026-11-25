"""Flow's state writers make each new file durable: its contents synced before the rename, and the folder synced
after it, so a power cut cannot bring back the file from before a write that had finished. The rollback copy of a
version-1 pin file is made the same way, and is durable before the original is converted."""

import json
import os
from pathlib import Path
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
from test_orphan_io import NIRI_ANSWERS
from test_niri_query import FakeNiri

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


class LeftBehindTest(unittest.TestCase):
    def test_a_sweep_removes_writers_temporary_files_and_nothing_else(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, folder, True)
        temporary = [state_files.TEMP_PREFIX + "a1b2c3", "tmpk3_x9qz0"]  # Flow's prefix; tempfile's old name
        kept = ["spaces.json", "groups.json", "session.lock", "tmpnotes.txt", "tmpABCDEFGH", "tmp1234567", "notes"]
        for name in temporary + kept:
            (folder / name).write_text("{}")
        (folder / (state_files.TEMP_PREFIX + "folder")).mkdir()  # not a file: left, and no error
        self.assertEqual(sorted(state_files.sweep(folder)), sorted(temporary))
        self.assertEqual(sorted(p.name for p in folder.iterdir()), sorted(kept + [state_files.TEMP_PREFIX + "folder"]))
        self.assertEqual(state_files.sweep(folder / "missing"), [])

    @unittest.skipUnless(Path("/proc/sys/kernel/random/boot_id").exists(), "needs Linux's boot ID")
    def test_a_refresh_removes_what_a_writer_killed_by_sigkill_left(self):
        """A writer SIGKILLed between its synced temporary file and the rename leaves the file; the next
        flow-spaces refresh, which takes spaces.lock as the controller's refresh does, removes it."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        state = tmp / "state" / "flow"
        state.mkdir(parents=True)
        flag = tmp / "state" / "paused"
        env = dict(os.environ, PYTHONPATH=str(SESSION), XDG_STATE_HOME=str(tmp / "state"),
                   FLOW_TEST_PAUSE=f"groups.write={flag}")
        writer = subprocess.Popen([sys.executable, "-c", "import sys, groups; from pathlib import Path; "
                                   "groups.write(Path(sys.argv[1]), [])", str(state / "groups.json")], env=env)
        end = time.monotonic() + 20
        while not flag.exists() and time.monotonic() < end:
            time.sleep(0.01)
        writer.send_signal(signal.SIGKILL)
        writer.wait(timeout=10)
        left = [p.name for p in state.iterdir() if state_files.left_behind(p.name)]
        self.assertEqual(len(left), 1, "the killed writer left its temporary file")
        niri = FakeNiri(str(tmp / "niri.sock"), NIRI_ANSWERS)
        self.addCleanup(niri.close)
        del env["FLOW_TEST_PAUSE"]
        answer = subprocess.run([sys.executable, str(SESSION / "spaces.py"), "refresh"],
                                env=dict(env, NIRI_SOCKET=str(tmp / "niri.sock")), text=True,
                                stdout=subprocess.PIPE, timeout=30)
        self.assertEqual((answer.returncode, json.loads(answer.stdout).get("error")), (0, None))
        self.assertEqual([p.name for p in state.iterdir() if state_files.left_behind(p.name)], [])


if __name__ == "__main__":
    unittest.main()
