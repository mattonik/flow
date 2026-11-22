"""Where tests/pointer.py points: the layout box Niri maps absolute motion onto, and a shell item's middle."""

import unittest

from pointer import center, layout_box, window_origin


def target(window, anchors, margins=(0, 0, 0, 0), rect=(0, 0, 10, 10), screen=(0, 0, 1280, 800)):
    return {"window": list(window), "screen": list(screen), "rect": list(rect),
            "place": {"anchors": list(anchors), "margins": list(margins)}}


class LayoutBoxTest(unittest.TestCase):
    def test_one_output(self):
        outputs = {"Virtual-1": {"logical": {"x": 0, "y": 0, "width": 1280, "height": 800}}}
        self.assertEqual(layout_box(outputs), (0, 0, 1280, 800))

    def test_outputs_left_and_above_the_origin_and_one_disabled(self):
        outputs = {"A": {"logical": {"x": 0, "y": 0, "width": 1280, "height": 800}},
                   "B": {"logical": {"x": -1920, "y": -200, "width": 1920, "height": 1080}},
                   "Off": {"logical": None}}
        self.assertEqual(layout_box(outputs), (-1920, -200, 3200, 1080))

    def test_no_output(self):
        with self.assertRaises(RuntimeError):
            layout_box({"Off": {"logical": None}})


class WindowOriginTest(unittest.TestCase):
    def test_bottom_anchored_is_centred_across(self):
        # The work home indicator: 126 × 24 at the bottom.
        self.assertEqual(window_origin(target((126, 24), (False, True, False, False))), (577, 776))

    def test_top_margin(self):
        # The banner: 370 wide, 56 below the top.
        self.assertEqual(window_origin(target((370, 80), (True, False, False, False), (56, 0, 0, 0))), (455, 56))

    def test_right_and_bottom_margins(self):
        self.assertEqual(window_origin(target((100, 50), (False, True, False, True), (0, 8, 0, 12))), (1168, 742))

    def test_anchored_all_round_fills_from_the_corner(self):
        self.assertEqual(window_origin(target((1280, 800), (True, True, True, True))), (0, 0))


class CenterTest(unittest.TestCase):
    def test_item_on_a_second_screen(self):
        item = target((126, 24), (False, True, False, False), rect=(0, 0, 126, 24), screen=(1280, 0, 1920, 1080))
        self.assertEqual(center(item), (1280 + 897 + 63, 1056 + 12))

    def test_item_inside_its_window(self):
        item = target((1280, 800), (True, True, True, True), rect=(300, 200, 680, 58))
        self.assertEqual(center(item), (640, 229))


if __name__ == "__main__":
    unittest.main()
