"""Flow's state writers make each new file durable: its contents synced before the rename, and the folder synced
after it, so a power cut cannot bring back the file from before a write that had finished. The rollback copy of a
version-1 pin file is made the same way, and is durable before the original is converted."""

import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "session"))
import groups
import pin_store
import restore

WRITES = {
    "groups.json": lambda path: groups.write(path, [{"id": "g", "label": "Kept", "members": ["a", "b"]}]),
    "spaces.json": lambda path: pin_store.write(path, [{"id": "p", "app_id": "a", "label": "A"}], {}, 2),
    "session.json": lambda path: restore.write_snapshot(path, {"version": 1, "session": None, "windows": [],
                                                              "groups": []}),
}


LEGACY = {"version": 1, "pins": [{"id": "p", "app_id": "a", "label": "A", "window_id": 7}]}


class DurableWriteTest(unittest.TestCase):
    def record(self, write, target, before):
        """Run ``write`` with ``target`` holding ``before``; every fsync and rename it made, in order.

        A synced descriptor is known by its device and inode, and a file by its size too: a rename keeps the inode,
        so the file synced before it is the one now in place only if both match, with all its bytes written."""
        target.write_text(json.dumps(before))
        seen, fsync, replace = [], os.fsync, os.replace

        def synced(fd):
            info = os.fstat(fd)
            folder_synced = stat.S_ISDIR(info.st_mode)
            seen.append(("folder" if folder_synced else "file", info.st_dev, info.st_ino,
                         None if folder_synced else info.st_size))
            return fsync(fd)

        def renamed(source, destination):
            seen.append(("rename", Path(destination).name))
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

    def test_the_rollback_copy_is_whole_and_durable_before_its_original_is_converted(self):
        target = self.folder() / "spaces.json"
        seen = self.record(lambda path: pin_store.write(path, LEGACY["pins"], {}, 1), target, LEGACY)
        rollback = target.parent / "spaces.v1.json"
        self.assertEqual(json.loads(rollback.read_text()), LEGACY)
        self.assertEqual([event for event in seen if event[0] == "rename"],
                         [("rename", "spaces.v1.json"), ("rename", "spaces.json")])
        copied, converted = seen.index(("rename", "spaces.v1.json")), seen.index(("rename", "spaces.json"))
        self.assertIn(self.identity(rollback), seen[:copied], "the copy, all its bytes, synced before its rename")
        self.assertIn(self.identity(target.parent, folder=True), seen[copied + 1:converted],
                      "the copy's name made durable before the original is replaced")
        self.assertEqual(json.loads(target.read_text())["version"], 2)


if __name__ == "__main__":
    unittest.main()
