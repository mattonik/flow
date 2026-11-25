"""Flow's state writers make each new file durable: its contents synced before the rename, and the folder synced
after it, so a power cut cannot bring back the file from before a write that had finished."""

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


class DurableWriteTest(unittest.TestCase):
    def events(self, name):
        """What the writer of ``name`` synced and renamed, in order, and the file and folder it wrote.

        A synced descriptor is known by its device and inode, and a file by its size too: a rename keeps the inode,
        so the file synced before it is the one now in place only if both match, with all its bytes written."""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        target = Path(folder.name) / name
        target.write_text(json.dumps({}))
        seen, fsync, replace = [], os.fsync, os.replace

        def synced(fd):
            info = os.fstat(fd)
            folder_synced = stat.S_ISDIR(info.st_mode)
            seen.append(("folder" if folder_synced else "file", info.st_dev, info.st_ino,
                         None if folder_synced else info.st_size))
            return fsync(fd)

        def renamed(source, destination):
            seen.append(("rename",))
            return replace(source, destination)

        with patch("os.fsync", synced), patch("os.replace", renamed):
            WRITES[name](target)
        written, parent = os.stat(target), os.stat(target.parent)
        return (seen, ("file", written.st_dev, written.st_ino, written.st_size),
                ("folder", parent.st_dev, parent.st_ino, None))

    def test_each_writer_syncs_its_file_then_renames_then_syncs_the_folder(self):
        for name in WRITES:
            with self.subTest(name=name):
                seen, written, folder = self.events(name)
                self.assertEqual(seen.count(("rename",)), 1)
                rename = seen.index(("rename",))
                self.assertIn(written, seen[:rename], "the new file, all its bytes written, synced before the rename")
                self.assertIn(folder, seen[rename + 1:], "its folder synced after the rename")


if __name__ == "__main__":
    unittest.main()
