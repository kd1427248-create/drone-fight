import threading
import time
import unittest

import arena
import battle
import plans


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

    def test_gesture_moves_along_the_nose(self):
        ahead = battle.gesture_step((-60, 0, 80, 0), 0, 1)
        landed = plans.move((-60, 0, 80, 0), ahead)
        self.assertEqual((round(landed[0]), round(landed[1])), (-20, 0))
        right = battle.gesture_step((-60, 0, 80, 0), 1, 0)
        landed = plans.move((-60, 0, 80, 0), right)
        self.assertEqual((round(landed[0]), round(landed[1])), (-60, 40))
        # Heading 180: the nose points toward -x, so a forward lean goes that way.
        turned = battle.gesture_step((60, 0, 80, 180), 0, 1)
        landed = plans.move((60, 0, 80, 180), turned)
        self.assertEqual((round(landed[0]), round(landed[1])), (20, 0))

    def test_gesture_stays_inside_and_ignores_an_upright_player(self):
        self.assertIsNone(battle.gesture_step((-60, 0, 80, 0), 0, 0))
        self.assertIsNone(battle.gesture_step((90, 0, 80, 0), 0, 1))
        self.assertTrue(plans.check("gesture\n")["ok"])
        choice = next(c for c in battle.choices() if c["key"] == "mode:6_gesture")
        self.assertTrue(choice["gesture"])
        self.assertIn("gesture", battle.plan_lines("mode", "6_gesture"))

    def test_a_stale_lean_is_not_fresh(self):
        runner = battle.BattleRunner.__new__(battle.BattleRunner)
        runner.lock = threading.Lock()
        runner.lean = (1, 0)
        runner.lean_at = time.monotonic() - 5
        self.assertFalse(runner._lean()[2])


if __name__ == "__main__":
    unittest.main()
