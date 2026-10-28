#!/Library/Frameworks/Python.framework/Versions/3.13/bin/python3
"""Tello connect helper: find drones, match two players and referee a battle.

It finds IPs, names drones, sets up the router connection (ap), matches
players by a 4-digit code, then flies each player's chosen mode or file at the
same moment and decides the winner: the drone still in the air at the end.
"""

import atexit
import os
import re
import threading
import time
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request

import battle
import pad_test
import pads
import plans
import tello_net


app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True  # screen changes show up without a restart
state = {"drones": [], "scanned_at": None, "scanning": False, "message": "",
         "matching": False, "ready": False, "plan": None, "start_at": None, "result": None}
BATTLE_SECONDS = 60
COUNTDOWN_SECONDS = 5
runner = battle.BattleRunner(
    tello_port=int(os.environ.get("TELLO_PORT", tello_net.TELLO_PORT)),
    state_port=int(os.environ.get("STATE_PORT", battle.STATE_PORT)))


def opponent_height() -> int | None:
    others = room_peers()
    return ((others[0].get("battle") or {}).get("height")) if others else None


runner.opponent_height = opponent_height


def opponent_pos(lead: float = 0.0) -> dict | None:
    """Where the opponent is now (or 'lead' seconds later), predicted along its current move."""
    others = room_peers()
    if not others:
        return None
    return battle.where_now((others[0].get("battle") or {}).get("pos"), others[0].get("offset", 0), lead)


runner.opponent_pos = opponent_pos


def opponent_raw() -> tuple[dict | None, float]:
    others = room_peers()
    if not others:
        return None, 0.0
    return (others[0].get("battle") or {}).get("pos"), others[0].get("offset", 0)


runner.opponent_raw = opponent_raw
# Closing the app must never leave the drone flying.
atexit.register(runner.shutdown)
tester = pad_test.PadTester(runner.monitor)
atexit.register(tester.shutdown)
lock = threading.Lock()


def my_drone() -> dict | None:
    """The drone this player chose, with its current IP if the last scan found it."""
    serial = tello_net.load_settings().get("drone_sn")
    if not serial:
        return None
    with lock:
        found = next((d for d in state["drones"] if d["sn"] == serial), {})
    # Live battery from the drone's state stream, else the last value we read.
    battery = runner.monitor.battery(found.get("ip")) or found.get("battery")
    return {"sn": serial, "name": tello_net.load_names().get(serial, ""),
            "ip": found.get("ip"), "ssid": found.get("ssid"), "battery": battery}


def missing_setup() -> list[str]:
    settings = tello_net.load_settings()
    labels = {"player": "プレイヤー名", "drone_sn": "自分のドローン", "code": "合言葉"}
    return [label for key, label in labels.items() if not settings.get(key)]


def my_room() -> str | None:
    """Only a fully set-up player who pressed "start matching" joins a room."""
    if missing_setup() or not state["matching"]:
        return None
    return tello_net.room_id(tello_net.load_settings().get("code"))


def public_info() -> dict:
    return {"player": tello_net.load_settings().get("player", ""), "drone": my_drone(),
            "room": my_room(), "ready": state["matching"] and state["ready"],
            "start_at": state["start_at"], "battle": runner.public()}


def room_peers() -> list[dict]:
    """Players matching with the same code; the first one is the opponent."""
    room = my_room()
    return [p for p in peers.current() if room and p.get("room") == room]


peers = tello_net.PeerFinder(public_info)

PORT = int(os.environ.get("PORT", 5050))
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}


@app.before_request
def only_this_browser_tab():
    """This app talks to drones on the LAN: refuse other websites and DNS rebinding."""
    if request.host not in ALLOWED_HOSTS:
        abort(403)
    origin = request.headers.get("Origin")
    if origin and origin.split("//", 1)[-1] not in ALLOWED_HOSTS:
        abort(403)
    # JSON posts need a CORS preflight, which a foreign page cannot pass.
    if request.method == "POST" and not request.is_json:
        abort(415)


@app.get("/")
def drones_page():
    return render_template("drones.html", page="drones")


@app.get("/peers")
def peers_page():
    return render_template("peers.html", page="peers")


@app.post("/api/import")
def api_import():
    """A .txt the player picked on their PC; the page sends its name and text."""
    body = request.get_json()
    result = plans.import_plan(str(body.get("filename", "")), str(body.get("text", "")))
    if result["ok"] and not state["start_at"]:
        state["plan"] = {"kind": "plan", "name": result["name"]}  # choose it right away
    return jsonify(result)


@app.post("/api/plans/delete")
def api_plans_delete():
    name = str(request.get_json().get("name", ""))
    if state["plan"] == {"kind": "plan", "name": name} and state["start_at"]:
        return jsonify(ok=False, message="バトル中のファイルは消せません")
    return jsonify(ok=plans.delete_plan(name))


@app.get("/router")
def router_page():
    return render_template("router.html", page="router")


@app.post("/api/pads")
def api_pads():
    """Save where each mission pad sits. Both PCs need the same numbers."""
    body = request.get_json(silent=True) or {}
    return jsonify(pads.save(body.get("pads")))


@app.post("/api/pad-test")
def api_pad_test():
    """Take off with this player's drone and read the pads under it."""
    body = request.get_json(silent=True) or {}
    action = str(body.get("action", ""))
    if action == "land":
        tester.land()
        return jsonify(ok=True, message="着陸します")
    if action == "emergency":
        tester.emergency()
        return jsonify(ok=True, message="緊急停止を送りました")
    if action == "move":
        error = tester.move(str(body.get("command", "")))
        return jsonify(ok=error is None, message=error or "動かします")
    if runner.phase in ("starting", "flying"):
        return jsonify(ok=False, message="バトル中は試せません")
    if tester.busy():
        return jsonify(ok=False, message="もう飛んでいます")
    drone = my_drone()
    if not drone or not drone.get("ip"):
        return jsonify(ok=False, message="先に「1. ドローンを探す」で自分の機体にして、この画面に戻ってください")
    if action == "hover":
        tester.start(drone["ip"], "hover")
        return jsonify(ok=True, message="離陸します。読めたらボタンか矢印キーで動かせます")
    if action == "scan":
        try:
            span = int(body.get("span", 80))
        except (TypeError, ValueError):
            span = 0
        if not 40 <= span <= 150:
            return jsonify(ok=False, message="前後左右は40〜150cmにしてください")
        tester.start(drone["ip"], "scan", span)
        return jsonify(ok=True, message=f"離陸して、前後左右{span}cmをスキャンします")
    return jsonify(ok=False, message="操作が分かりません")


@app.get("/api/state")
def api_state():
    ip = tello_net.local_ip()
    names = tello_net.load_names()
    with lock:
        drones = [{**d, "name": names.get(d["sn"], ""),
                   "battery": runner.monitor.battery(d["ip"]) or d.get("battery")}
                  for d in state["drones"]]
        snapshot = {key: state[key] for key in ("scanned_at", "scanning", "message")}
    return jsonify(
        local_ip=ip,
        mode=tello_net.network_mode(ip),
        drones=drones,
        # Only players matching with the same 4-digit code are shown.
        peers=[{"player": p.get("player", ""), "ip": p["ip"], "drone": p.get("drone"),
                "ready": bool(p.get("ready")), "battle": p.get("battle")} for p in room_peers()],
        matching=state["matching"],
        ready=state["ready"],
        plan=state["plan"],
        start_at=state["start_at"],
        now=tello_net.now(),
        battle_seconds=BATTLE_SECONDS,
        result=state["result"],
        me=runner.details(),
        choices=battle.choices(),
        plan_key=battle.plan_key(state["plan"]),
        code=tello_net.load_settings().get("code", ""),
        missing=missing_setup(),
        player=tello_net.load_settings().get("player", ""),
        my_drone=my_drone(),
        pads=pads.public(),
        pad_test=tester.public(),
        **snapshot,
    )


def run_scan() -> None:
    hosts = os.environ.get("SCAN_HOSTS")  # testing only: e.g. a fake Tello on 127.0.0.1
    drones = tello_net.scan(hosts.split(",") if hosts else None, port=runner.tello_port)
    with lock:
        # Keep what we already learnt from our own drone (battery, serial).
        known = {d["sn"]: d for d in state["drones"]}
        for d in drones:
            for key in ("battery", "serial"):
                d[key] = d.get(key) or known.get(d["sn"], {}).get(key)
        state.update(drones=drones, scanning=False, scanned_at=time.strftime("%H:%M:%S"),
                     message=f"{len(drones)}台見つかりました")


@app.post("/api/scan")
def api_scan():
    with lock:
        if state["scanning"]:
            return jsonify(ok=False, message="探している途中です")
        state.update(scanning=True, message="探しています…（数秒）")
    threading.Thread(target=run_scan, daemon=True).start()
    return jsonify(ok=True)


@app.post("/api/name")
def api_name():
    body = request.get_json()
    tello_net.save_name(str(body["sn"]), str(body.get("name", "")).strip()[:20])
    return jsonify(ok=True)


@app.post("/api/match")
def api_match():
    """Start matching: remember name, drone and code, then join the code's room."""
    body = request.get_json()
    player = str(body.get("player", "")).strip()[:20]
    serial = str(body.get("sn", "")).strip()
    code = str(body.get("code", "")).strip()
    missing = [label for value, label in ((player, "プレイヤー名"), (serial, "自分のドローン"),
                                          (code, "合言葉")) if not value]
    if missing:
        return jsonify(ok=False, message="「" + "」「".join(missing) + "」を入れてください")
    if not re.fullmatch(r"\d{4}", code):
        return jsonify(ok=False, message="合言葉は4桁の数字にしてください")
    message = claim_drone(serial)
    if message:
        return jsonify(ok=False, message=message)
    tello_net.save_settings(player=player, drone_sn=serial, code=code)
    state.update(matching=True, ready=False)
    peers.announce()
    return jsonify(ok=True)


def claim_drone(drone_id: str) -> str | None:
    """Connect to the player's own drone only; returns an error message or None."""
    with lock:
        drone = next((d for d in state["drones"] if d["sn"] == drone_id), None)
    if drone is None:
        return "そのドローンが見つかっていません。「1. ドローンを探す」で探してください"
    info = tello_net.claim(drone["ip"], runner.tello_port)
    if info is None:
        return ("ドローンから返事がありません。ほかのPCが先につないだかもしれません。"
                "Telloの電源を入れ直して、もう一度押してください")
    with lock:
        drone.update(info)
    return None


@app.post("/api/claim")
def api_claim():
    drone_id = str(request.get_json().get("sn", ""))
    message = claim_drone(drone_id)
    if message:
        return jsonify(ok=False, message=message)
    tello_net.save_settings(drone_sn=drone_id)
    return jsonify(ok=True)


@app.post("/api/unmatch")
def api_unmatch():
    if runner.phase in ("starting", "flying"):
        return jsonify(ok=False, message="バトル中はやめられません")
    state.update(matching=False, ready=False, start_at=None, result=None)
    peers.announce()
    return jsonify(ok=True)


@app.get("/battle")
def battle_page():
    return render_template("battle.html", page="battle")


@app.post("/api/plan")
def api_plan():
    body = request.get_json()
    plan = {"kind": str(body.get("kind")), "name": str(body.get("name"))}
    try:
        battle.plan_lines(plan["kind"], plan["name"])
    except ValueError:
        return jsonify(ok=False, message="その作戦は使えません")
    if state["start_at"]:
        return jsonify(ok=False, message="バトルが始まってからは変えられません（モードは下のボタンで切り替え）")
    state["plan"] = plan
    return jsonify(ok=True)


@app.get("/api/plan_text")
def api_plan_text():
    kind, name = request.args.get("kind", ""), request.args.get("name", "")
    try:
        return jsonify(lines=battle.plan_lines(kind, name))
    except ValueError:
        return jsonify(lines=[]), 404


@app.post("/api/ready")
def api_ready():
    if not room_peers():
        return jsonify(ok=False, message="マッチしていません")
    ready = bool(request.get_json().get("ready"))
    if ready and not state["plan"]:
        return jsonify(ok=False, message="先にモードかファイルを選んでください")
    if ready and not (my_drone() or {}).get("ip"):
        return jsonify(ok=False, message="自分のドローンのIPがわかりません。「1. ドローンを探す」で探してください")
    if state["start_at"]:
        return jsonify(ok=False, message="もう始まっています")
    state["ready"] = ready
    peers.announce()  # tell the opponent now, not at the next 2-second hello
    return jsonify(ok=True)


@app.post("/api/mode")
def api_mode():
    kind, _, name = str(request.get_json().get("key", "")).partition(":")
    return jsonify(ok=runner.set_mode(kind, name))


@app.post("/api/surrender")
def api_surrender():
    runner.land("降参して着陸")
    return jsonify(ok=True)


@app.post("/api/emergency")
def api_emergency():
    runner.emergency()
    return jsonify(ok=True)


@app.post("/api/reset")
def api_reset():
    """Page reload = start over: forget the code, found drones, match and battle.

    Drone nicknames, the player name and imported files stay. Nothing is reset
    while the drone is in the air.
    """
    if runner.phase in ("starting", "flying") or tester.busy():
        return jsonify(ok=False, message="飛んでいる間はリセットしません")
    runner.reset()
    with lock:
        state.update(drones=[], scanned_at=None, scanning=False, message="", matching=False,
                     ready=False, plan=None, start_at=None, result=None)
    tello_net.save_settings(code="", drone_sn="")
    peers.announce()
    return jsonify(ok=True)


@app.post("/api/rematch")
def api_rematch():
    if runner.phase in ("starting", "flying"):
        return jsonify(ok=False, message="まだ飛んでいます")
    runner.reset()
    state.update(ready=False, start_at=None, result=None)
    peers.announce()
    return jsonify(ok=True)


def decide(me: dict, op: dict | None, now: float) -> dict | None:
    """Last drone in the air wins; within 1 s of each other is a draw."""
    if op is None:
        return None
    failed = lambda b: b.get("phase") == "landed" and not b.get("takeoff_at")
    if failed(me) and failed(op):
        return {"outcome": "draw", "reason": "2台とも離陸できませんでした"}
    if failed(me):
        return {"outcome": "lose", "reason": "離陸できませんでした"}
    if failed(op):
        return {"outcome": "win", "reason": "相手が離陸できませんでした"}
    mine, theirs = me.get("down_at"), op.get("down_at")
    if mine and theirs:
        if abs(mine - theirs) < 1:
            return {"outcome": "draw", "reason": "2台ほぼ同時に地面へ"}
        return ({"outcome": "win", "reason": "相手が先に地面へ"} if theirs < mine
                else {"outcome": "lose", "reason": "自分が先に地面へ"})
    if theirs and me.get("airborne"):
        return {"outcome": "win", "reason": "相手が地面へ。最後まで飛んでいました"}
    if mine and op.get("airborne"):
        return {"outcome": "lose", "reason": "自分が先に地面へ"}
    return None


def referee() -> None:
    """Start both players together, end on time and decide the winner."""
    last_seen = tello_net.now()
    while True:
        time.sleep(0.2)
        now = tello_net.now()
        others = room_peers()
        op = others[0] if others else None
        # The opponent's times are on their clock; shift them onto ours.
        offset = (op or {}).get("offset", 0)
        if op:
            last_seen = now
        # 1) Both ready: the player with the smaller id sets the start time for both.
        if state["ready"] and op and op.get("ready") and not state["start_at"]:
            if peers.instance < op.get("instance", ""):
                state["start_at"] = now + COUNTDOWN_SECONDS
                peers.announce()
            elif op.get("start_at") and op["start_at"] - offset > now - 2:
                state["start_at"] = op["start_at"] - offset
        start_at = state["start_at"]
        if not start_at:
            continue
        # During a battle the opponent needs our state quickly, not every 2 s.
        if int(now * 5) % 3 == 0:
            peers.announce()
        # 2) Take off at the agreed moment.
        if runner.phase == "idle" and now >= start_at and not state["result"]:
            drone = my_drone() or {}
            if drone.get("ip") and state["plan"]:
                runner.start(drone["ip"], state["plan"])
        if state["result"] or runner.phase == "idle":
            continue
        # 3) Decide.
        theirs = (op or {}).get("battle")
        if theirs:
            theirs = {**theirs, **{key: theirs[key] - offset for key in ("down_at", "takeoff_at")
                                   if theirs.get(key)}}
        result = decide(runner.public(), theirs, now)
        if result is None and now > start_at + BATTLE_SECONDS:
            result = {"outcome": "draw", "reason": f"{BATTLE_SECONDS}秒たっても2台とも飛んでいました"}
        if result is None and now - last_seen > 6:
            result = {"outcome": "draw", "reason": "相手との通信が切れました"}
        if result:
            state["result"] = result
            runner.land("勝負がついたので着陸")
            peers.announce()


threading.Thread(target=referee, daemon=True).start()


@app.post("/api/router")
def api_router():
    """Send 'ap' so the Tello joins the router; only on its own Wi-Fi."""
    body = request.get_json()
    ssid, password = str(body.get("ssid", "")).strip(), str(body.get("password", ""))
    if tello_net.network_mode(tello_net.local_ip()) != "direct":
        return jsonify(ok=False, message="先にMacを設定したいTelloのWi-Fi（TELLO-xxxx）につないでください")
    if not ssid or " " in ssid or " " in password:
        return jsonify(ok=False, message="Wi-Fi名とパスワードを入れてください（空白は使えません）")
    # ap goes out right after 'command' on the same socket (talk sends it first).
    battery, reply = tello_net.talk(tello_net.DIRECT_IP, ["battery?", f"ap {ssid} {password}"], 3)
    if battery is None:
        return jsonify(ok=False, message="Telloから返事がありません。電源とWi-Fiを確認してください")
    if reply and reply.lower().startswith("ok"):
        return jsonify(ok=True, message="設定しました。Telloが再起動してルーターにつながります。"
                                         "MacもルーターのWi-Fiに戻して「ドローンを探す」を押してください")
    # The drone may reboot before its reply arrives.
    return jsonify(ok=True, message="返事は届きませんでしたが、設定できている場合があります。"
                                     "MacをルーターのWi-Fiに戻して「ドローンを探す」で確認してください")


if __name__ == "__main__":
    print(f"ブラウザで http://127.0.0.1:{PORT} を開いてね（終了は Ctrl+C）")
    app.run(host="127.0.0.1", port=PORT, debug=False)
