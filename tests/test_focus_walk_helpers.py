"""The focus-walk smoke's own judgements, which run only in the guest: label-in-name matching, reading
order, focus-ring sampling and screenshot decoding. tests/niri_focus_walk_smoke.py imports nothing outside the standard
library until it runs, so its helpers can be checked here."""

from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).parent))
import niri_focus_walk_smoke as smoke  # noqa: E402


def control(name, x, y, w=40, h=40, card=None):
    return {"name": name, "start": [x, y, w, h], "card": card}


class LabelInNameTest(unittest.TestCase):
    def test_the_visible_text_must_start_a_word_of_the_name(self):
        for name, label in [("Floe: return to work", "floe."), ("Open selected: foot", "↗  Open selected"),
                            ("Show keyboard shortcuts", "Shortcuts"), ("Decrease volume by 5%", "− 5%"),
                            ("Move to Built-in Display: foot", "Move to Built-in Displa")]:
            with self.subTest(name=name, label=label):
                self.assertTrue(smoke.contains(name, label))
        for name, label in [("Unpin foot", "Pin"), ("Enclose panel", "Close"), ("Reopen", "Open"),
                            ("Volume at 5", "5%"),
                            ("Return to work", "floe."), ("Open foot", "Open selected"), ("Search", "")]:
            with self.subTest(name=name, label=label):
                self.assertFalse(smoke.contains(name, label))


class ReadingOrderTest(unittest.TestCase):
    def test_rows_top_to_bottom_each_left_to_right(self):
        controls = [control("c", 0, 100), control("b", 60, 4, h=44), control("a", 0, 0, h=48)]
        self.assertEqual([c["name"] for c in smoke.reading_order(controls)], ["a", "b", "c"])

    def test_a_row_is_read_left_to_right_even_when_its_left_item_sits_lower(self):
        controls = [control("right", 200, 0, h=60), control("left", 0, 10, h=40)]
        self.assertEqual([c["name"] for c in smoke.reading_order(controls)], ["left", "right"])

    def test_an_item_joins_a_row_by_its_centre_not_its_top(self):
        # "tall" starts inside the first row (0 to 40), left of "a", but is centred below it: a row of its own.
        controls = [control("a", 100, 0), control("tall", 0, 30, h=100)]
        self.assertEqual([c["name"] for c in smoke.reading_order(controls)], ["a", "tall"])

    def test_a_card_is_read_whole_before_the_next(self):
        first, second = [0, 90, 220, 200], [230, 90, 220, 104]
        controls = [control("header", 0, 0), control("B", 240, 100, 200, 84, second),
                    control("A2", 110, 190, 90, 44, first), control("A", 10, 100, 200, 84, first),
                    control("A1", 10, 190, 90, 44, first)]
        self.assertEqual([c["name"] for c in smoke.reading_order(controls)], ["header", "A", "A1", "A2", "B"])


class RingTest(unittest.TestCase):
    FOCUS, BACKGROUND = (189, 218, 209), (28, 45, 54)

    def shot(self, box, thickness=2, size=(200, 120)):
        """A screenshot of a ring whose outer edge is ``box`` (float x, y, w, h), drawn with antialiased edges."""
        width, height = size
        x, y, w, h = box

        def cover(lo, hi, a, b):
            return max(0.0, min(hi, b) - max(lo, a))

        rows = []
        for py in range(height):
            row = bytearray()
            for px in range(width):
                inside_x, inside_y = cover(px, px + 1, x, x + w), cover(py, py + 1, y, y + h)
                hole_x = cover(px, px + 1, x + thickness, x + w - thickness)
                hole_y = cover(py, py + 1, y + thickness, y + h - thickness)
                amount = inside_x * inside_y - hole_x * hole_y
                row += bytes(round(f * amount + b * (1 - amount)) for f, b in zip(self.FOCUS, self.BACKGROUND)) + b"\xff"
            rows.append(row)
        return width, height, 4, rows

    def drawn(self, ring_box, control_y):
        button = {"kind": "button", "ring": [40, control_y, 100, 44, -3]}
        return smoke.ring_drawn(self.shot(ring_box), button, (0, 0), 200, self.FOCUS)

    def test_a_ring_three_px_outside_is_found_at_whole_and_half_pixels(self):
        self.assertIs(self.drawn((37, 17, 106, 50), 20), True)
        self.assertIs(self.drawn((37, 17.5, 106, 50), 20.5), True)

    def test_a_ring_two_px_away_from_its_place_is_not(self):
        self.assertIsNot(self.drawn((35, 15, 110, 54), 20), True)
        self.assertIsNot(self.drawn((39, 19, 102, 46), 20), True)

    def test_no_ring_is_not_a_ring(self):
        self.assertIs(self.drawn((0, 0, 0, 0), 20), False)


class PngTest(unittest.TestCase):
    def test_every_filter_type_decodes_to_the_same_pixels(self):
        # Pseudo-random pixels, so the Paeth predictor meets ties that only its own order resolves.
        width, height, bpp, seed = 64, 40, 4, 12345
        values = []
        for _ in range(width * height * bpp):
            seed = (seed * 1103515245 + 12345) % 2 ** 31
            values.append(seed >> 23)
        stride = width * bpp
        rows = [bytes(values[y * stride:(y + 1) * stride]) for y in range(height)]
        raw, prev = b"", bytes(stride)
        for y, row in enumerate(rows):  # filter types 0 to 4 in turn
            kind = y % 5
            out = bytearray()
            for i, value in enumerate(row):
                a = row[i - bpp] if i >= bpp else 0
                b, c = prev[i], prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                paeth = a if abs(p - a) <= abs(p - b) and abs(p - a) <= abs(p - c) else b if abs(p - b) <= abs(p - c) else c
                out.append((value - [0, a, b, (a + b) // 2, paeth][kind]) & 255)
            raw += bytes([kind]) + bytes(out)
            prev = row

        def chunk(kind, body):
            return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

        data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shot.png"
            path.write_bytes(data)
            self.assertEqual(smoke.png_pixels(path), (width, height, bpp, [bytearray(r) for r in rows]))


if __name__ == "__main__":
    unittest.main()
