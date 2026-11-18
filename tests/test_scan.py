import math
import unittest

import arena
import scan

# Pads around pad 4 (the origin) in the 1 2 3 / 4 5 6 / 7 8 order, irregular spacing.
CENTERS = {1: (-40, -110), 2: (60, -120), 3: (170, -100), 4: (0, 0),
           5: (90, 10), 6: (180, 5), 7: (30, 115), 8: (150, 120)}
FIELD = scan.boundary(80, 260, 170, 180)


def scanned(spread=0.0, seen=3) -> scan.PadMap:
    pads = scan.PadMap()
    for k, (x, y) in CENTERS.items():
        for i in range(seen):
            wobble = spread if i % 2 else -spread
            pads.sightings[k].append((x + wobble, y))
    return pads


class BoundaryAndRouteTest(unittest.TestCase):
    def test_boundary_is_checked(self):
        self.assertEqual(scan.boundary(80, 260, 170, 180), (-80.0, 260.0, -170.0, 180.0))
        for bad in ((10, 260, 170, 180), (80, 900, 170, 180), (80, None, 170, 180), (80, True, 170, 180)):
            with self.assertRaises(ValueError):
                scan.boundary(*bad)

    def test_route_stays_inside_and_covers_with_small_gaps(self):
        points = scan.route(FIELD)
        x_min, x_max, y_min, y_max = FIELD
        for x, y in points:
            self.assertTrue(x_min + scan.EDGE - 1e-6 <= x <= x_max - scan.EDGE + 1e-6)
            self.assertTrue(y_min + scan.EDGE - 1e-6 <= y <= y_max - scan.EDGE + 1e-6)
        # Neighbouring stops are STEP apart along a sweep; changing lane (the order is the start
        # row, then outward on alternating sides) never crosses more than the width of all lanes.
        widest = 2 * max(abs(y) for _, y in points)
        for a, b in zip(points, points[1:]):
            self.assertLessEqual(math.dist(a, b), max(scan.STEP, widest) + 1e-6)
        # Every pad lies within the camera's reach (about 42 cm at 1 m) of some stop.
        for k, c in CENTERS.items():
            self.assertLess(min(math.dist(c, p) for p in points), 42, f"pad {k}")

    def test_sweeps_start_on_pad_4s_row_and_work_outward(self):
        ys = scan.lanes(FIELD[2], FIELD[3])
        self.assertEqual(ys[0], 0)
        self.assertEqual(sorted(ys, key=lambda y: (abs(y), y)), ys)
        self.assertTrue(any(y < 0 for y in ys) and any(y > 0 for y in ys))
        # The first stops are on pad 4's row, so pads 5 and 6 are found before the far lanes.
        self.assertTrue(all(abs(y) < 1e-9 for _, y in scan.route(FIELD)[:5]))
        # A roomy boundary adds lanes only outward, never reordering the near ones.
        self.assertEqual(scan.lanes(-300, 300)[:len(ys)], ys)

    def test_a_pad_near_the_boundary_edge_still_gets_a_lane(self):
        ys = scan.lanes(-100, 170)  # edge at -70 is 10 cm past the lane at -60, so no extra lane needed
        self.assertNotIn(-70, ys)
        ys = scan.lanes(-140, 170)  # edge at -110 is 50 cm past the lane at -60
        self.assertIn(-110, ys)

    def test_hops_add_up_and_stay_short(self):
        moves = scan.hops((0, 0), (130, -40))
        self.assertAlmostEqual(sum(m[0] for m in moves), 130)
        self.assertAlmostEqual(sum(m[1] for m in moves), -40)
        self.assertTrue(all(math.hypot(*m) <= scan.STEP + 1e-6 for m in moves))


# The 4-pad square 1 2 / 3 4 (pad 3 is the origin, A takes off from it and B from pad 4).
SQUARE = {1: (-10, -105), 2: (105, -100), 3: (0, 0), 4: (110, 5)}
SQUARE_FIELD = scan.boundary(100, 250, 200, 60)


def scanned_square(spread=0.0, seen=2) -> scan.PadMap:
    pads = scan.PadMap("square4")
    for k, (x, y) in SQUARE.items():
        for i in range(seen):
            pads.sightings[k].append((x + (spread if i % 2 else -spread), y))
    return pads


class SquareTest(unittest.TestCase):
    def test_origin_and_pads_of_each_set(self):
        self.assertEqual((scan.PadMap().origin, len(scan.PadMap().ids)), (4, 8))
        square = scan.PadMap("square4")
        self.assertEqual((square.origin, square.ids), (3, (1, 2, 3, 4)))
        self.assertEqual(square.center(3), (0.0, 0.0))

    def test_square_sweep_skips_the_empty_near_side_and_still_reaches_every_pad(self):
        points = scan.route(SQUARE_FIELD, "square4")
        self.assertTrue(all(y <= 1e-9 for _, y in points))
        self.assertTrue(all(abs(y) < 1e-9 for _, y in points[:5]))  # the start row comes first
        for k, c in SQUARE.items():
            self.assertLess(min(math.dist(c, p) for p in points), 42, f"pad {k}")
        # With a roomy near side the 8-pad sweep goes there too; the square's does not.
        roomy = scan.boundary(100, 250, 200, 180)
        self.assertLess(len(scan.route(roomy, "square4")), len(scan.route(roomy)))

    def test_square_sweep_ends_once_all_four_are_seen_twice(self):
        self.assertTrue(scanned_square(spread=5, seen=2).done())
        self.assertFalse(scanned_square(spread=5, seen=1).done())
        missing = scanned_square(spread=5)
        missing.sightings[2] = []
        self.assertFalse(missing.done())

    def test_good_square_scan_builds_a_layout_with_pad_3_as_origin(self):
        layout = scan.build_layout(scanned_square(spread=6, seen=3), SQUARE_FIELD, closure=10)
        self.assertEqual(set(layout["pads"]), set("1234"))
        self.assertEqual(layout["starts"]["A"], [0.0, 0.0, 0])
        self.assertEqual(layout["starts"]["B"][:2], layout["pads"]["4"])
        self.assertEqual(arena.start_pads(layout), {"A": "3", "B": "4"})

    def test_doubtful_square_scans_are_rejected(self):
        missing = scanned_square()
        missing.sightings[1] = []
        with self.assertRaisesRegex(ValueError, "パッド1が見つかりません"):
            scan.build_layout(missing, SQUARE_FIELD, 10)
        with self.assertRaisesRegex(ValueError, "パッド3に戻ったときのズレ"):
            scan.build_layout(scanned_square(), SQUARE_FIELD, 45)
        with self.assertRaisesRegex(ValueError, "パッド3へ戻って確認"):
            scan.build_layout(scanned_square(), SQUARE_FIELD, None)
        swapped = scanned_square()
        swapped.sightings[3], swapped.sightings[4] = [(110, 5)] * 2, [(-40, 5)] * 2  # 4 left of 3
        with self.assertRaises(ValueError):
            scan.build_layout(swapped, SQUARE_FIELD, 10)


class PadMapTest(unittest.TestCase):
    def test_sweep_ends_when_every_pad_is_seen_twice_in_agreeing_places(self):
        self.assertTrue(scanned(spread=5, seen=2).done())
        self.assertFalse(scanned(spread=5, seen=1).done())
        self.assertFalse(scanned(spread=scan.DONE_SPREAD + 5, seen=2).done())
        # Pad 4 is the origin by definition: its sightings only need to be numerous enough.
        loose = scanned(spread=5, seen=2)
        loose.sightings[4] = [(40, 40), (-40, -40)]
        self.assertTrue(loose.done())

    def test_reanchoring_removes_drift(self):
        pads = scan.PadMap()
        # Seen pad 5 once from a good estimate; later our tracking is 30 cm off.
        pads.observe(5, (5, 0), (95, 10))
        corrected = pads.observe(5, (-5, 2), (115, 12))
        self.assertAlmostEqual(corrected[0], 90 + 15 - 5)  # centre (100) moved halfway by the bad sighting
        self.assertEqual(pads.center(4), (0.0, 0.0))

    def test_good_scan_builds_the_layout(self):
        layout = scan.build_layout(scanned(spread=8, seen=4), FIELD, closure=12)
        self.assertEqual(layout["starts"]["A"][:2], [0.0, 0.0])
        self.assertEqual(layout["starts"]["B"][:2], list(map(float, CENTERS[6])))
        self.assertEqual(layout["field"], list(FIELD))
        self.assertIn("scanned_at", layout)

    def test_doubtful_scans_are_rejected(self):
        missing = scanned()
        missing.sightings[7] = []
        cases = {
            "7が見つかりません": (missing, 10),
            "回数が足りません": (scanned(seen=1), 10),
            "安定しません": (scanned(spread=40), 10),
            "戻ったときのズレ": (scanned(), 45),
            "戻って確認": (scanned(), None),
        }
        for text, (pads, closure) in cases.items():
            with self.assertRaisesRegex(ValueError, text):
                scan.build_layout(pads, FIELD, closure)
        swapped = scanned()
        swapped.sightings[4], swapped.sightings[6] = swapped.sightings[6], swapped.sightings[4]
        swapped.sightings[4] = [(0, 0)] * 3
        swapped.sightings[6] = [(-80, 0)] * 3  # pad 6 left of pad 4: wrong order
        with self.assertRaises(ValueError):
            scan.build_layout(swapped, FIELD, 10)


if __name__ == "__main__":
    unittest.main()
