"""Run two app copies on fake pad Tellos and report position error and closest true distance.

argv: mode_a mode_b seconds pads(1|0|drop|state-drop) [irregular] [sync] [debug]
"""
import json, math, shutil, socket, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import arena, battle
SP = Path(tempfile.mkdtemp(prefix="tello-pad-sim-"))
APP = Path(__file__).resolve().parents[1]
PY = sys.executable
mode_a, mode_b, seconds, pads = sys.argv[1], sys.argv[2], float(sys.argv[3]), sys.argv[4]
if "irregular" in sys.argv[5:]:
    layout = arena.normalize({"pads": {"1": [-90, -70], "2": [-10, -75], "3": [70, -70],
                                       "4": [-160, 20], "5": [-10, 15], "6": [155, 35],
                                       "7": [-65, 110], "8": [100, 120]},
                              "field": [-220, 210, -160, 160],
                              "starts": {"A": [-160, 20, 0], "B": [155, 35, 180]}})
elif "simple" in sys.argv[5:]:
    layout = arena.normalize({"kind": "simple", "distance": 250, "back": 150, "side": 150})
else:
    layout = arena.normalize({"preset": "rows8", "spacing": 60})
(SP / "layout.json").write_text(json.dumps(layout))
procs = []
for i, (tp, stp, port) in enumerate(((9021, 9121, 5071), (9022, 9122, 5072))):
    procs.append(subprocess.Popen([PY, str(APP / "tests/fake_pad_tello.py"), str(tp), str(stp), f"FAKE{i}", "0", "0", "0",
                                   str(SP / f"true{i}.json"), pads, str(SP / "layout.json")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    env = {"PORT": str(port), "TELLO_PORT": str(tp), "STATE_PORT": str(stp), "TELLO_LOCAL_PORT": "0",
           "SCAN_HOSTS": "127.0.0.1", "TELLO_CONNECT_DATA": str(SP / f"d{i}"),
           "PEER_PORT": "47475", "PATH": "/usr/bin:/bin"}
    (SP / f"d{i}").mkdir()
    if not ({"sync", "sync_b"} & set(sys.argv[5:])):
        (SP / f"d{i}" / "arena.json").write_text(json.dumps(layout))
    procs.append(subprocess.Popen([PY, "app.py"], cwd=APP, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
time.sleep(3)
def call(port, path, body=None):
    if body is None:
        return json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}{path}"))
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", json.dumps(body).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))
try:
    for port in (5071, 5072): call(port, "/api/scan", {})
    time.sleep(3)
    for port in (5071, 5072):
        call(port, "/api/match", {"player": f"P{port}", "sn": call(port, "/api/state")["drones"][0]["sn"], "code": "4321"})
    time.sleep(3)
    starts = layout["starts"]
    roles = {}
    for i, port in enumerate((5071, 5072)):
        roles[i] = call(port, "/api/state")["role"]
        sx, sy, sh = starts[roles[i]]
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.sendto(f"SIMSTART {sx} {sy} {sh}".encode(), ("127.0.0.1", 9021 + i))
    print("roles:", roles)
    if {"sync", "sync_b"} & set(sys.argv[5:]):
        scanner_role = "B" if "sync_b" in sys.argv[5:] else "A"
        scanner = (5071, 5072)[next(i for i in roles if roles[i] == scanner_role)]
        call(scanner, "/api/plan", {"kind": "mode", "name": mode_a})
        blocked = call(scanner, "/api/ready", {"ready": True})
        print("unconfigured ready blocked:", blocked)
        assert not blocked["ok"]
        print(f"save on {scanner_role}:", call(scanner, "/api/arena", layout))
        time.sleep(2)
        hashes = [call(port, "/api/state")["arena_hash"] for port in (5071, 5072)]
        print("shared map:", hashes)
        assert hashes[0] and hashes[0] == hashes[1]
    call(5071, "/api/plan", {"kind": "mode", "name": mode_a}); call(5072, "/api/plan", {"kind": "mode", "name": mode_b})
    call(5071, "/api/ready", {"ready": True}); call(5072, "/api/ready", {"ready": True})
    time.sleep(7)
    errs, dists, pad_hits = [], [], 0; t0 = time.time()
    while time.time() - t0 < seconds:
        true = []
        for i in (0, 1):
            for _ in range(20):
                try:
                    true.append(json.load(open(SP / f"true{i}.json"))); break
                except (ValueError, FileNotFoundError):
                    time.sleep(0.01)
        for i, port in enumerate((5071, 5072)):
            pos = call(port, "/api/state")["me"]["pos"]
            predicted = battle.where_now(pos)
            e = math.hypot(predicted["x"] - true[i]["x"], predicted["y"] - true[i]["y"])
            errs.append(e)
            pad_hits += bool(pos.get("pad"))
            if "debug" in sys.argv[5:]:
                print(f"  {time.time()-t0:4.1f}s P{i} app({predicted['x']:5.0f},{predicted['y']:5.0f}) h{pos['heading']:4} true({true[i]['x']:5.0f},{true[i]['y']:5.0f}) h{true[i]['heading']:5.0f} err{e:4.0f} pad={pos.get('pad')} moving={'y' if pos.get('move') else '-'}")
        dists.append(math.hypot(true[0]["x"] - true[1]["x"], true[0]["y"] - true[1]["y"]))
        time.sleep(0.2)
    n = len(errs)
    print(f"位置のズレ: 平均 {sum(errs)/n:.0f}cm / 最大 {max(errs):.0f}cm ／ パッドで測れていた割合 {pad_hits/n*100:.0f}%")
    print(f"2台の本当の距離: 一番近く {min(dists):.0f}cm ／ 25cm以内（ぶつかる距離）{sum(d < 25 for d in dists)/len(dists)*100:.0f}%")
    for i, port in enumerate((5071, 5072)):
        log = call(port, "/api/state")["me"]["log"]
        print(f"P{i} log tail:", *log[-5:], sep="\n  ")
finally:
    for port in (5071, 5072):
        try: call(port, "/api/surrender", {})
        except Exception: pass
    time.sleep(1.5)
    for p in procs: p.terminate()
    for p in procs: p.wait(timeout=3)
    shutil.rmtree(SP)
