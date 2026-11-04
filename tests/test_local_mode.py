"""The 1-PC mode: one app flies drone A and drone B, nothing is sent to a real drone."""
import os
import tempfile
import time
import unittest

DATA = tempfile.mkdtemp(prefix="tello-local-test-")
os.environ.update(TELLO_CONNECT_DATA=DATA, PORT="5099", STATE_PORT="9199", PEER_PORT="47499",
                  TELLO_LOCAL_PORT="0")

import app as tello_app  # noqa: E402  (needs the environment above)
import arena  # noqa: E402
import battle  # noqa: E402
import tello_net  # noqa: E402


def post(client, path, body=None):
    return client.post(path, json=body or {}, headers={"Host": "127.0.0.1:5099"}).get_json()


def get(client, path):
    return client.get(path, headers={"Host": "127.0.0.1:5099"}).get_json()


class LocalModeTest(unittest.TestCase):
    def setUp(self):
        self.client = tello_app.app.test_client()
        layout = arena.normalize({"preset": "rows8", "spacing": 60})
        tello_app.arena_layout = layout
        battle.configure_arena(layout)
        tello_app.state.update(drones=[{"sn": "SN-A", "ip": "10.0.0.1", "ssid": "TELLO-A", "battery": "90"},
                                       {"sn": "SN-B", "ip": "10.0.0.2", "ssid": "TELLO-B", "battery": "88"}],
                               matching=False, ready=False, plan=None, plan_b=None, start_at=None,
                               result=None, local=False)
        self.started = []
        self.landed = []
        self.originals = (tello_net.claim, tello_app.runner.start, tello_app.runner2.start,
                          tello_app.runner.land, tello_app.runner2.land)
        tello_net.claim = lambda ip, port=0: {"serial": ip, "battery": "90"}
        tello_app.runner.start = lambda ip, plan, role="A": self.started.append(("A", ip, plan["name"], role))
        tello_app.runner2.start = lambda ip, plan, role="A": self.started.append(("B", ip, plan["name"], role))
        tello_app.runner.land = lambda reason: self.landed.append("A")
        tello_app.runner2.land = lambda reason: self.landed.append("B")

    def tearDown(self):
        (tello_net.claim, tello_app.runner.start, tello_app.runner2.start,
         tello_app.runner.land, tello_app.runner2.land) = self.originals
        tello_app.state.update(matching=False, ready=False, local=False, start_at=None, result=None,
                               plan=None, plan_b=None)

    def test_needs_two_different_drones(self):
        self.assertFalse(post(self.client, "/api/match", {"local": True, "sn": "SN-A", "sn_b": ""})["ok"])
        self.assertFalse(post(self.client, "/api/match", {"local": True, "sn": "SN-A", "sn_b": "SN-A"})["ok"])

    def test_local_match_needs_no_code_and_has_a_second_drone_as_opponent(self):
        r = post(self.client, "/api/match", {"local": True, "player": "ゆーしん", "sn": "SN-A", "sn_b": "SN-B"})
        self.assertTrue(r["ok"], r)
        s = get(self.client, "/api/state")
        self.assertTrue(s["local"])
        self.assertEqual(len(s["peers"]), 1)
        self.assertEqual(s["peers"][0]["drone"]["sn"], "SN-B")
        self.assertEqual(s["role"], "A")
        self.assertEqual(s["arena_hash"], s["peers"][0]["arena_hash"])

    def test_ready_needs_both_plans_then_both_drones_start_together(self):
        post(self.client, "/api/match", {"local": True, "sn": "SN-A", "sn_b": "SN-B"})
        modes = [c["key"].split(":")[1] for c in get(self.client, "/api/state")["choices"] if not c["imported"]]
        self.assertTrue(post(self.client, "/api/plan", {"kind": "mode", "name": modes[0]})["ok"])
        self.assertFalse(post(self.client, "/api/ready", {"ready": True})["ok"])  # B has no plan yet
        self.assertTrue(post(self.client, "/api/plan", {"kind": "mode", "name": modes[1], "side": "B"})["ok"])
        self.assertTrue(post(self.client, "/api/ready", {"ready": True})["ok"])
        tello_app.COUNTDOWN_SECONDS, saved = 0.3, tello_app.COUNTDOWN_SECONDS
        try:
            deadline = time.time() + 6
            while time.time() < deadline and len(self.started) < 2:
                time.sleep(0.1)
        finally:
            tello_app.COUNTDOWN_SECONDS = saved
        self.assertEqual(sorted(self.started), sorted([("A", "10.0.0.1", modes[0], "A"),
                                                       ("B", "10.0.0.2", modes[1], "B")]))

    def test_plan_for_b_is_refused_outside_local_mode(self):
        self.assertFalse(post(self.client, "/api/plan", {"kind": "mode", "name": "x", "side": "B"})["ok"])

    def test_surrender_and_emergency_reach_both_drones(self):
        post(self.client, "/api/match", {"local": True, "sn": "SN-A", "sn_b": "SN-B"})
        post(self.client, "/api/surrender")
        self.assertEqual(sorted(self.landed), ["A", "B"])
        sent = []
        orig = tello_app.runner.emergency, tello_app.runner2.emergency
        tello_app.runner.emergency = lambda: sent.append("A")
        tello_app.runner2.emergency = lambda: sent.append("B")
        try:
            post(self.client, "/api/emergency")
        finally:
            tello_app.runner.emergency, tello_app.runner2.emergency = orig
        self.assertEqual(sorted(sent), ["A", "B"])

    def test_decide_uses_the_two_runners_directly(self):
        a = {"phase": "flying", "airborne": True, "takeoff_at": 1, "down_at": None}
        b = {"phase": "down", "airborne": False, "takeoff_at": 1, "down_at": 5.0}
        self.assertEqual(tello_app.decide(a, b, 6)["outcome"], "win")

    def test_the_two_runners_share_one_state_monitor_and_see_each_other(self):
        self.assertIs(tello_app.runner.monitor, tello_app.runner2.monitor)
        tello_app.runner.pose = (10.0, 20.0, 0.0, 0)
        self.assertEqual(tello_app.runner2.opponent_raw()[0]["x"], 10)


if __name__ == "__main__":
    unittest.main()
