"""The F-012 guest test's own parts that run without a session: its PNG reader and its portal monitor's parser."""

import struct
import tempfile
import unittest
from pathlib import Path
import zlib

import niri_portals_smoke as smoke


def filtered_png(width, height, pixel, kinds):
    """An RGBA PNG whose rows use the given filter types, encoded as a PNG writer would."""
    rows = [bytes(b for x in range(width) for b in pixel(x, y)) for y in range(height)]
    bpp, out, previous = 4, b"", bytes(width * 4)
    for y, row in enumerate(rows):
        kind, line = kinds[y % len(kinds)], bytearray()
        for x in range(len(row)):
            a, b = (row[x - bpp] if x >= bpp else 0), previous[x]
            c = previous[x - bpp] if x >= bpp else 0
            if kind == 0:
                predict = 0
            elif kind == 1:
                predict = a
            elif kind == 2:
                predict = b
            elif kind == 3:
                predict = (a + b) >> 1
            else:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                predict = a if pa <= pb and pa <= pc else b if pb <= pc else c
            line.append((row[x] - predict) & 255)
        out += bytes([kind]) + bytes(line)
        previous = row

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(out)) + chunk(b"IEND", b""))


class ImageTest(unittest.TestCase):
    def test_reads_what_it_writes(self):
        image = smoke.Image(smoke.png(7, 5, (12, 200, 99)))
        self.assertEqual((image.width, image.height), (7, 5))
        self.assertEqual(image.pixel(6, 4), (12, 200, 99))

    def test_every_filter_type(self):
        def pixel(x, y):
            return ((x * 37 + y * 11) % 256, (x * 5 + y * 73) % 256, (x * y * 13) % 256, 255)
        for kinds in ([0], [1], [2], [3], [4], [0, 1, 2, 3, 4]):
            image = smoke.Image(filtered_png(9, 6, pixel, kinds))
            for x, y in ((0, 0), (8, 0), (0, 5), (4, 3), (8, 5)):
                self.assertEqual(image.pixel(x, y), pixel(x, y)[:3], f"filters {kinds} at {x},{y}")

    def test_refuses_what_it_cannot_read(self):
        with self.assertRaises(ValueError):
            smoke.Image(b"GIF89a")

    def test_close_colours(self):
        self.assertTrue(smoke.close((10, 20, 30), (20, 10, 40)))
        self.assertFalse(smoke.close((10, 20, 30), (21, 20, 30)))

    def test_fresh_colours_differ(self):
        colours = {smoke.fresh_colour() for _ in range(20)}
        self.assertGreater(len(colours), 15)
        for rgb in colours:
            self.assertGreater(len(set(rgb)), 1)  # never a grey a theme might draw


class MonitorTest(unittest.TestCase):
    def test_reads_the_backend_calls(self):
        log = (
            "method call time=1.0 sender=:1.64 -> destination=:1.72 serial=9 path=/org/freedesktop/portal/desktop; "
            "interface=org.freedesktop.impl.portal.FileChooser; member=OpenFile\n"
            "   object path \"/org/freedesktop/portal/desktop/request/1_2/t\"\n"
            "method call time=1.1 sender=:1.300 -> destination=org.freedesktop.portal.Desktop serial=4 "
            "path=/org/freedesktop/portal/desktop; interface=org.freedesktop.portal.Screenshot; member=Screenshot\n"
            "method call time=1.2 sender=:1.64 -> destination=:1.69 serial=10 path=/org/freedesktop/portal/desktop; "
            "interface=org.freedesktop.impl.portal.ScreenCast; member=Start\n")
        with tempfile.TemporaryDirectory() as tmp:
            monitor = smoke.Monitor.__new__(smoke.Monitor)
            monitor.log = Path(tmp) / "monitor.log"
            monitor.log.write_text(log)
            self.assertEqual(monitor.calls(), [("FileChooser", "OpenFile", ":1.72"), ("ScreenCast", "Start", ":1.69")])


if __name__ == "__main__":
    unittest.main()
