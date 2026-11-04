import unittest

import arena
import battle


class BattleSafetyTest(unittest.TestCase):
    def setUp(self):
        battle.configure_arena(arena.normalize({"preset": "rows8", "spacing": 60}))

    def test_cancel_before_takeoff_does_not_open_a_tello_socket(self):
        runner = battle.BattleRunner.__new__(battle.BattleRunner)
        runner._reset()
        runner.phase = "starting"
        runner.stop_reason = "降参して着陸"
        runner.stop_event.set()
        runner._run()
        self.assertEqual(runner.phase, "landed")
        self.assertIsNone(runner.takeoff_at)
        self.assertIsNone(runner.sock)

    def test_charge_stops_inside_arena_edge(self):
        start = battle.STARTS["A"]
        steps = battle.charge_steps((*start[:2], 80, start[2]), battle.STARTS["B"][:2])
        self.assertIn("forward 120", steps)


if __name__ == "__main__":
    unittest.main()
