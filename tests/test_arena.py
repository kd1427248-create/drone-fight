import unittest

import arena


class ArenaTest(unittest.TestCase):
    def setUp(self):
        self.layout = arena.normalize({"preset": "rows8", "spacing": 60})

    def test_eight_pad_layout_has_shared_start_pads(self):
        self.assertEqual(set(self.layout["pads"]), set("12345678"))
        self.assertEqual(self.layout["starts"]["A"][:2], self.layout["pads"]["4"])
        self.assertEqual(self.layout["starts"]["B"][:2], self.layout["pads"]["6"])
        self.assertEqual(arena.fingerprint(self.layout), arena.fingerprint(arena.normalize(self.layout)))

    def test_rejects_missing_pad_and_unsafe_boundary(self):
        bad = {**self.layout, "preset": None, "pads": {k: v for k, v in self.layout["pads"].items() if k != "8"}}
        with self.assertRaises(ValueError):
            arena.normalize(bad)
        # A boundary that leaves pads 1-3 and 7-8 outside the flying area.
        bad = {**self.layout, "preset": None, "field": [-120, 120, -40, 40]}
        with self.assertRaises(ValueError):
            arena.normalize(bad)

    def test_rejects_wrong_numbered_pattern(self):
        bad = {**self.layout, "preset": None, "pads": dict(self.layout["pads"])}
        bad["pads"]["4"], bad["pads"]["6"] = bad["pads"]["6"], bad["pads"]["4"]
        with self.assertRaisesRegex(ValueError, "左右の並び"):
            arena.normalize(bad)

    # 1 2 / 3 4 seen from above; x follows the rockets, y grows toward the near side.
    SQUARE = {"pads": {"1": [-10, -105], "2": [95, -100], "3": [0, 0], "4": [100, 5]},
              "field": [-100, 250, -200, 60],
              "starts": {"A": [0, 0, 0], "B": [100, 5, 180]}}

    def test_four_pad_square_is_accepted_and_starts_on_pads_3_and_4(self):
        layout = arena.normalize(self.SQUARE)
        self.assertEqual(set(layout["pads"]), set("1234"))
        self.assertEqual(arena.pad_set(layout["pads"]), "square4")
        self.assertEqual(arena.start_pads(layout), {"A": "3", "B": "4"})
        self.assertEqual(layout["starts"]["B"][:2], layout["pads"]["4"])
        self.assertEqual(arena.pad_set(self.layout["pads"]), "pads8")
        self.assertEqual(arena.start_pads(self.layout), {"A": "4", "B": "6"})

    def test_four_pad_square_rejects_wrong_numbering_or_start(self):
        swapped = {**self.SQUARE, "pads": {**self.SQUARE["pads"], "3": [100, 5], "4": [0, 0]}}
        with self.assertRaisesRegex(ValueError, "左右の並び"):
            arena.normalize(swapped)
        wrong_row = {**self.SQUARE, "pads": {**self.SQUARE["pads"], "1": [-10, 40], "2": [95, 40]}}
        with self.assertRaisesRegex(ValueError, "奥から手前"):
            arena.normalize(wrong_row)
        off_start = {**self.SQUARE, "starts": {"A": [0, 40, 0], "B": [100, 5, 180]}}
        with self.assertRaisesRegex(ValueError, "パッド3の中心"):
            arena.normalize(off_start)
        # Only 1-4 or 1-8 are valid sets; five or six pads are not.
        for extra in ({"5": [200, 0]}, {"5": [200, 0], "6": [300, 0]}):
            with self.assertRaisesRegex(ValueError, "4枚"):
                arena.normalize({**self.SQUARE, "pads": {**self.SQUARE["pads"], **extra}})

    def test_simple_mode_needs_no_pads(self):
        layout = arena.normalize({"kind": "simple", "distance": 250, "back": 150, "side": 120})
        self.assertEqual(layout["pads"], {})
        self.assertEqual(layout["field"], [-150.0, 400.0, -120.0, 120.0])
        self.assertEqual(layout["starts"]["B"], [250.0, 0.0, 180])
        for bad in ({"distance": 50}, {"distance": 250, "back": 10}, {"distance": "x"}):
            with self.assertRaises(ValueError):
                arena.normalize({"kind": "simple", "back": 150, "side": 150, **bad})


if __name__ == "__main__":
    unittest.main()
