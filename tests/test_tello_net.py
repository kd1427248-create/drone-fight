import unittest

import gesture_fly
import tello_net


class TelloNetTest(unittest.TestCase):
    def test_a_tello_stays_found_after_the_arp_cache_drops_it(self):
        targets = {"192.168.111.188", "192.168.111.182"}
        found = {}
        tello_net.remember_tellos(found, {"192.168.111.188": "34:d2:62:f1:8b:b1"}, targets)
        tello_net.remember_tellos(found, {"192.168.111.182": "34:d2:62:9f:1d:09"}, targets)
        # The next dump is what a full cache looks like: the drones are gone.
        tello_net.remember_tellos(found, {"192.168.111.140": "aa:bb:cc:dd:ee:ff"}, targets)
        self.assertEqual(set(found), targets)

    def test_probe_order_checks_known_addresses_then_the_top_of_the_subnet(self):
        targets = [f"192.168.111.{n}" for n in range(1, 6)]
        ordered = tello_net.probe_order(targets, {"192.168.111.2"})
        self.assertEqual(ordered, [
            "192.168.111.2",
            "192.168.111.5", "192.168.111.4", "192.168.111.3", "192.168.111.1",
        ])

    def test_a_practice_hop_stays_near_takeoff(self):
        moved = gesture_fly.hop(0, 0, 0, 1)
        self.assertEqual(moved[0], "go 30 0 0 40")
        self.assertIsNone(gesture_fly.hop(0, 0, 0, 0))
        self.assertIsNone(gesture_fly.hop(120, 0, 0, 1))


if __name__ == "__main__":
    unittest.main()
