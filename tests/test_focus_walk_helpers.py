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



class ScrolledReadingOrderTest(unittest.TestCase):
    """The walk's expected order (``cycle_of``, which ``walk`` uses) with scrolling views (``controls()``'s
    ``views``). The shell's side, and that ``walk`` uses it, are checked only in the guest."""

    @staticmethod
    def names(listed):
        return [listed[i]["name"] for i in smoke.cycle_of(listed)]

    @staticmethod
    def c(name, rect, views=(), card=None):
        return {"name": name, "rect": rect, "views": [list(v) for v in views], "card": card}

    def test_a_button_scrolled_up_behind_the_islands_row_is_read_after_it(self):
        # Desk with Settings open, as the guest drew it (27 November 2026): Desk's view starts at 56, below the
        # Island's row (0 to 44), and Search is scrolled up to 9, so only 56 to 68 of it shows. Its rectangle,
        # read with the window's, is centred in the Island's row and left of floe.
        view = [(0, 56, 1280, 700)]
        listed = [self.c("floe.", [408, 0, 56, 44]), self.c("Apps", [464, 0, 57, 44]),
                  self.c("Search anything", [185, 9, 590, 59], view), self.c("Pin", [185, 187, 218, 44], view),
                  self.c("Return to work", [971, 1096, 124, 44], view)]
        self.assertEqual(self.names(listed), ["floe.", "Apps", "Search anything", "Pin", "Return to work"])

    def test_the_order_in_a_view_does_not_depend_on_its_scroll(self):
        # A row of a 64 px tile left of a 44 px button, top-aligned, with the view starting at 56: read with what
        # shows of each, it came out button first while the row straddled the view's top or bottom edge.
        view = [(0, 56, 1280, 700)]
        for y in (300, 100, 40, 10, -20, -200, 740, 780, 1400):
            with self.subTest(y=y):
                listed = [self.c("above", [600, 0, 44, 44]), self.c("tile", [100, y, 300, 64], view),
                          self.c("button", [410, y, 100, 44], view), self.c("next row", [100, y + 80, 100, 44], view)]
                self.assertEqual(self.names(listed), ["above", "tile", "button", "next row"])

    def test_cards_below_a_list_keep_their_order(self):
        # Notification cards scrolled below their list: each card's Open folder, then its ×.
        view = [(20, 120, 330, 500)]
        listed = [self.c("Clear all", [250, 60, 90, 44])]
        for n, y in enumerate((700, 800, 900)):
            listed += [self.c(f"open {n}", [40, y, 120, 44], view), self.c(f"close {n}", [300, y, 44, 44], view)]
        self.assertEqual(self.names(listed), ["Clear all", "open 0", "close 0", "open 1", "close 1", "open 2", "close 2"])

    def test_a_view_in_a_scrolled_view_is_read_in_its_place(self):
        # The switcher scrolled down: its header's Close is above its view's top (20), the row of space cards
        # (a horizontal view inside) starts at 0, and a card's controls are a block in it.
        outer, inner = (0, 20, 800, 700), (0, 0, 800, 200)
        card = [10, 0, 200, 180]
        listed = [self.c("space", [20, 10, 180, 44], (outer, inner), card),
                  self.c("pin", [20, 120, 80, 44], (outer, inner), card),
                  self.c("Close", [700, -30, 80, 44], (outer,)),
                  self.c("next space", [220, 10, 180, 44], (outer, inner), [210, 0, 200, 180]),
                  self.c("Layers", [20, 220, 120, 44], (outer,))]
        self.assertEqual(self.names(listed), ["Close", "space", "pin", "next space", "Layers"])

    def test_a_row_of_cards_scrolled_sideways_is_read_between_its_arrows(self):
        # The switcher's row of cards scrolled right, between its ‹ and › in the outer view: the first card is
        # off to the left, past ‹. Read in the outer view's terms, it would come before ‹.
        outer, inner = (0, 20, 800, 700), (40, 20, 720, 200)
        listed = [self.c("‹", [0, 90, 36, 44], (outer,)),
                  self.c("first", [-150, 30, 180, 44], (outer, inner), [-160, 20, 200, 180]),
                  self.c("second", [60, 30, 180, 44], (outer, inner), [50, 20, 200, 180]),
                  self.c("›", [764, 90, 36, 44], (outer,))]
        self.assertEqual(self.names(listed), ["‹", "first", "second", "›"])


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
