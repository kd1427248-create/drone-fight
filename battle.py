"""Fly one battle with the player's own drone and watch whether it is still airborne."""

import math
import socket
import threading
import time
from collections import deque
from pathlib import Path

import plans
import tello_net

APP_DIR = Path(__file__).resolve().parent
MODE_DIR = APP_DIR / "modes"
MIN_BATTERY = 30
MOVE_TIMEOUT = 12
STATE_PORT = 8890


def modes() -> list[dict]:
    """Built-in modes; the first comment line is '# name: description'."""
    found = []
    for path in sorted(MODE_DIR.glob("*.txt")):
        first = path.read_text(encoding="utf-8").splitlines()[0].lstrip("# ")
        name, _, description = first.partition(":")
        found.append({"id": path.stem, "name": name.strip(), "description": description.strip()})
    return found


MAX_HEIGHT = 180
# The arena, in cm from each player's own start. Players start facing each other.
START_DISTANCE = 250  # between the two drones at the start
ARENA_BACK = 150      # how far behind its start a drone may go
ARENA_SIDE = 150      # how far left / right
FLEE_HEIGHTS = (60, 160)
FLEE_STEP = 100  # cm per flee move, so it looks at the opponent again at least every second


CHARGE_OVERSHOOT = 80  # cm past the opponent, so the charge goes through it
CHARGE_LEAD = 1.0  # aim where a moving opponent will be this many seconds later


def charge_steps(me: tuple, opponent: tuple[float, float]) -> list[str]:
    """Bullfight charge: turn to face the opponent, match its height, run through it.

    The run stops at the edge of the whole field (both halves of the arena).
    """
    x, y, _, heading = me
    ox, oy = opponent
    dx, dy = ox - x, oy - y
    distance = math.hypot(dx, dy)
    steps = []
    if distance >= 1:
        bearing = math.degrees(math.atan2(dy, dx))  # clockwise from the start direction
        turn = (bearing - heading + 180) % 360 - 180
        if abs(turn) >= 10:
            steps.append(f"cw {round(turn)}" if turn > 0 else f"ccw {round(-turn)}")
        heading = bearing
    steps.append("match_height")
    run = min(500, distance + CHARGE_OVERSHOOT, _room_ahead(x, y, heading))
    if run >= 20:
        steps.append(f"forward {round(run)}")
    return steps


def _room_ahead(x: float, y: float, heading: float) -> float:
    """How far we can go straight on before leaving the field."""
    rad = math.radians(heading)
    ux, uy = math.cos(rad), math.sin(rad)
    limits = []
    for pos, step, low, high in ((x, ux, -ARENA_BACK, START_DISTANCE + ARENA_BACK),
                                 (y, uy, -ARENA_SIDE, ARENA_SIDE)):
        if step > 1e-6:
            limits.append((high - pos) / step)
        elif step < -1e-6:
            limits.append((low - pos) / step)
    return max(0.0, min(limits)) if limits else 0.0


GUARD_REACH = 100    # cm: closer than this and the guard hits back
GUARD_PUSH = 50      # cm of the counter hit
GUARD_HOME = 30      # cm: farther than this from the start and it walks back first
KEEPALIVE = 4        # s: poke the drone if nothing was sent for this long (it lands after 15 s)


def _face(x: float, y: float, heading: float, target: tuple[float, float]) -> tuple[list[str], float]:
    """The turn that points us at 'target' (none if already within 15 degrees)."""
    bearing = math.degrees(math.atan2(target[1] - y, target[0] - x))
    turn = (bearing - heading + 180) % 360 - 180
    if abs(turn) < 15:
        return [], heading
    return [f"cw {round(turn)}" if turn > 0 else f"ccw {round(-turn)}"], bearing


def guard_steps(me: tuple, opponent: tuple[float, float]) -> list[str]:
    """Hold the start, face the opponent, and hit back when it comes within reach.

    Returns [] when there is nothing to do but keep watching.
    """
    x, y, _, heading = me
    away = math.hypot(x, y)
    if away > GUARD_HOME:
        # Back to our spot first (a counter hit or a bump moved us).
        command = plans.go_command(-x, -y, 0, heading, 60)
        return [command] if command else []
    turn, heading = _face(x, y, heading, opponent)
    if math.hypot(opponent[0] - x, opponent[1] - y) <= GUARD_REACH:
        return turn + ["match_height", f"forward {GUARD_PUSH}"]
    return turn


def where_now(their_pos: dict | None, offset: float = 0.0, lead: float = 0.0) -> dict | None:
    """The opponent's position right now: part way along the move it is making, if any.

    A move is sent as {from, to, start, duration}; 'start' is on the opponent's
    clock, so 'offset' (their clock minus ours) brings it onto ours.
    """
    if not their_pos:
        return None
    move = their_pos.get("move")
    if not move:
        return their_pos
    elapsed = tello_net.now() + lead - (move["start"] - offset)
    t = max(0.0, min(1.0, elapsed / max(0.1, move["duration"])))
    return {"x": move["from"][0] + (move["to"][0] - move["from"][0]) * t,
            "y": move["from"][1] + (move["to"][1] - move["from"][1]) * t}


def opponent_in_my_frame(their_pos: dict | None) -> tuple[float, float]:
    """The opponent starts START_DISTANCE ahead, facing us: flip its own position into ours."""
    if not their_pos:
        return (START_DISTANCE, 0.0)
    return (START_DISTANCE - their_pos["x"], -their_pos["y"])


def flee_target(opponent: tuple[float, float], opponent_height: int | None) -> tuple[float, float, int]:
    """The corner of our half of the arena farthest from the opponent, and the height farthest from theirs."""
    xs = (-ARENA_BACK, START_DISTANCE / 2 - 50)  # never past the middle line
    ys = (-ARENA_SIDE, ARENA_SIDE)
    corners = [(x, y) for x in xs for y in ys]
    x, y = max(corners, key=lambda c: (c[0] - opponent[0]) ** 2 + (c[1] - opponent[1]) ** 2)
    their_h = opponent_height if opponent_height is not None else 80
    z = max(FLEE_HEIGHTS, key=lambda h: abs(h - their_h))
    return (x, y, z)


def height_fix(mine: int | None, theirs: int | None) -> str | None:
    """The up/down command that brings us to the opponent's height (60-180 cm), if needed."""
    if mine is None or theirs is None:
        return None
    target = max(60, min(MAX_HEIGHT, theirs))
    diff = target - mine
    if abs(diff) < 20:
        return None
    step = min(abs(diff), 100)
    return f"up {step}" if diff > 0 else f"down {step}"


def plan_key(plan: dict | None) -> str | None:
    """'mode:<id>' or 'plan:<file>' — one string the page can compare."""
    return f"{plan['kind']}:{plan['name']}" if plan else None


def choices() -> list[dict]:
    """Everything a player can pick: the built-in modes, then imported files."""
    return ([{"key": f"mode:{m['id']}", "title": m["name"], "description": m["description"],
              "imported": False} for m in modes()]
            + [{"key": f"plan:{p['name']}", "title": p["title"], "description": p["description"],
                "imported": True} for p in plans.list_plans()])


def plan_lines(kind: str, name: str) -> list[str]:
    if kind == "mode" and name in {m["id"] for m in modes()}:
        path = MODE_DIR / f"{name}.txt"
    elif kind == "plan" and name in {p["name"] for p in plans.list_plans()}:
        path = plans.PLAN_DIR / name
    else:
        raise ValueError("unknown plan")
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        # The runner does command / takeoff itself; 'ap' must never run mid-battle.
        if line and not line.startswith("#") and line not in ("command", "takeoff", "repeat") \
                and not line.startswith("ap "):
            lines.append(line)
    return lines


class BattleRunner:
    """Takes off, runs the chosen mode (looped) or file (once) and reports airborne state."""

    def __init__(self, tello_port: int = tello_net.TELLO_PORT, state_port: int = STATE_PORT) -> None:
        self.tello_port = tello_port
        self.monitor = StateMonitor(state_port)
        self.lock = threading.Lock()
        self.opponent_height = lambda: None  # set by the app: opponent's latest height (cm)
        self.opponent_pos = lambda lead=0.0: None  # set by the app: opponent's pose {"x","y"} (cm)
        self._reset()

    def _reset(self) -> None:
        self.phase = "idle"  # idle, starting, flying, down, landed
        self.ip: str | None = None
        self.plan: dict | None = None
        self.airborne = False
        self.takeoff_at: float | None = None
        self.down_at: float | None = None
        self.height: int | None = None
        self.pose = (0.0, 0.0, 0.0, 0)  # where our commands have taken us, from the start
        self.speed = 100  # cm/s, updated by 'speed' commands
        self.speed_set = False  # True once the drone has confirmed a 'speed' command
        self.last_sent = time.monotonic()
        self.motion: dict | None = None  # the move in progress, so the opponent can predict us
        self.height_at = 0.0  # when the last state packet arrived (monotonic)
        self.moved_at = 0.0   # when the last command finished (monotonic)
        self.tof: int | None = None
        self.battery: str | None = None
        self.current = ""
        self.log: list[str] = []
        self.stop_reason: str | None = None
        self.stop_event = threading.Event()
        self.sock: socket.socket | None = None

    # ---- public API -------------------------------------------------------
    def start(self, ip: str, plan: dict) -> None:
        with self.lock:
            if self.phase not in ("idle", "down", "landed"):
                return
            self._reset()
            self.ip, self.plan, self.phase = ip, dict(plan), "starting"
        threading.Thread(target=self._run, daemon=True).start()
        threading.Thread(target=self._watch_state, daemon=True).start()

    def set_mode(self, kind: str, name: str) -> bool:
        """Switch to another mode or imported file mid-battle."""
        if self.phase != "flying":
            return False
        try:
            plan_lines(kind, name)
        except ValueError:
            return False
        self.plan = {"kind": kind, "name": name}
        self._note(f"モード変更: {self._plan_label()}")
        return True

    def land(self, reason: str) -> None:
        """Stop the plan and land (surrender, time up, result decided, app exit)."""
        if self.phase not in ("starting", "flying"):
            return
        self.stop_reason = reason
        self.stop_event.set()
        self._raw("stop")  # cut the current move short; the run thread then lands

    def shutdown(self) -> None:
        """The app is closing: land right here, since the run thread dies with the app."""
        if self.phase not in ("starting", "flying"):
            return
        self.stop_reason = "アプリを終了"
        self.stop_event.set()
        sock = self.sock or tello_net.tello_socket(0.5)
        self.sock = sock
        self._raw("stop")
        time.sleep(0.3)
        # Repeat in case a packet is lost or the drone is still finishing a move.
        for _ in range(5):
            self._raw("land")
            time.sleep(0.4)
        self._note("アプリ終了のため着陸を送りました")

    def emergency(self) -> None:
        self.stop_reason = "緊急停止"
        self.stop_event.set()
        for _ in range(3):
            self._raw("emergency")
        self._note("緊急停止を送りました（プロペラ停止）")

    def reset(self) -> None:
        with self.lock:
            if self.phase in ("idle", "down", "landed"):
                self._reset()

    def public(self) -> dict:
        x, y, _, heading = self.pose
        return {"phase": self.phase, "airborne": self.airborne, "down_at": self.down_at,
                "pos": {"x": round(x), "y": round(y), "heading": round(heading), "move": self.motion},
                "takeoff_at": self.takeoff_at, "height": self.height, "battery": self.battery,
                "plan": self._plan_label(), "current": self.current}

    def details(self) -> dict:
        return {**self.public(), "log": self.log[-12:], "plan_key": plan_key(self.plan)}

    # ---- internals ---------------------------------------------------------
    def _plan_label(self) -> str:
        if not self.plan:
            return ""
        if self.plan["kind"] == "mode":
            return next((m["name"] for m in modes() if m["id"] == self.plan["name"]), self.plan["name"])
        return next((p["title"] for p in plans.list_plans() if p["name"] == self.plan["name"]),
                    self.plan["name"])

    def _note(self, text: str) -> None:
        self.log.append(time.strftime("%H:%M:%S ") + text)

    def _raw(self, command: str) -> None:
        if self.sock and self.ip:
            try:
                self.sock.sendto(command.encode(), (self.ip, self.tello_port))
            except OSError:
                pass

    def _send(self, command: str, timeout: float = MOVE_TIMEOUT) -> str | None:
        sock = self.sock
        # Throw away late replies (e.g. to a 'stop') so they are not taken for this one.
        sock.settimeout(0)
        try:
            while True:
                sock.recvfrom(1024)
        except OSError:
            pass
        sock.settimeout(0.2)
        self.current = command
        self.last_sent = time.monotonic()
        self._raw(command)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, source = sock.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                return None
            if source[0] == self.ip:
                return data.decode("utf-8", "replace").strip()
        return None

    def _delay(self, seconds: float) -> None:
        # Long waits still poke the drone so its 15 s safety auto-land never fires.
        end = time.monotonic() + seconds
        while not self.stop_event.is_set() and time.monotonic() < end:
            self.stop_event.wait(min(5, end - time.monotonic()))
            if time.monotonic() < end and not self.stop_event.is_set():
                self.battery = self._send("battery?", 3) or self.battery

    def _run(self) -> None:
        self.sock = tello_net.tello_socket(0.2)
        try:
            if self._send("command", 5) != "ok":
                return self._finish("landed", "ドローンから返事がありません")
            self.battery = self._send("battery?", 5)
            if not self.battery or not self.battery.isdigit() or int(self.battery) < MIN_BATTERY:
                return self._finish("landed", f"電池が{MIN_BATTERY}%未満なので離陸しません（{self.battery}）")
            self._note(f"離陸します（電池 {self.battery}%）")
            reply = self._send("takeoff", 20)
            if reply != "ok":
                # A lost reply can still mean it is up: land to be safe.
                self._send("land", 10)
                return self._finish("landed", f"離陸できませんでした（{reply}）")
            self.airborne, self.takeoff_at, self.phase = True, tello_net.now(), "flying"
            self._note(f"離陸OK。{self._plan_label()} を開始")
            self._fly_plan()
        finally:
            if self.phase in ("starting", "flying"):
                self._land_now()
            elif self.phase == "down":
                self._send("land", 3)  # make sure the motors are off after a fall
            sock, self.sock = self.sock, None
            if sock:
                sock.close()
            self.current = ""

    def _fly_plan(self) -> None:
        errors = 0
        while not self.stop_event.is_set() and self.phase == "flying":
            plan = dict(self.plan)
            lines = deque(plan_lines(plan["kind"], plan["name"]))
            while lines:
                line = lines.popleft()
                if self.stop_event.is_set() or self.phase != "flying" or self.plan != plan:
                    break  # stopped, knocked down, or the mode was switched
                if line == "charge":
                    # Expands into: turn toward the opponent, match its height, run through.
                    lines.extendleft(reversed(self._charge_plan()))
                    continue
                if line.startswith("delay"):
                    self._delay(float(line.split()[1]))
                    continue
                if line.startswith("speed ") and self.speed_set and line == f"speed {self.speed}":
                    continue  # already flying at this speed: no need to send it every loop
                if line == "guard":
                    steps = self._guard_plan()
                    if steps:
                        lines.extendleft(reversed(steps))
                    else:
                        self._wait_watching()
                    continue
                if line == "flee_far":
                    deadline = time.monotonic() + 0.6  # wait for a height measured after the last move
                    while self.height_at <= self.moved_at + 0.1 and time.monotonic() < deadline:
                        time.sleep(0.05)
                    line = self._flee_step()
                    if line is None:
                        self._wait_watching()  # already as far away as we can be
                        continue
                if line == "match_height":
                    # Only trust a height measured after the last move, so a stale
                    # reading can never make it climb again and again.
                    deadline = time.monotonic() + 0.6
                    while self.height_at <= self.moved_at + 0.1 and time.monotonic() < deadline:
                        time.sleep(0.05)
                    fresh = self.height_at > self.moved_at + 0.1
                    line = height_fix(self.height, self.opponent_height()) if fresh else None
                    if line is None:
                        continue
                if line.startswith("up ") and (self.height or 0) + int(line.split()[1]) > MAX_HEIGHT + 20:
                    continue  # never climb far above the arena ceiling
                if self.stop_event.is_set():
                    break  # stopped while waiting for a fresh height: send nothing more
                self._announce_move(line)
                reply = self._send(line)
                self.motion = None
                self.moved_at = time.monotonic()
                if reply == "ok":
                    try:
                        self.pose = plans.move(self.pose, line)
                    except ValueError:
                        pass
                    if line.startswith("speed "):
                        self.speed = int(line.split()[1])
                        self.speed_set = True
                if line == "land":
                    self.stop_reason = "ファイルの land で着陸"
                    return self._finish("landed", self.stop_reason)
                if reply is None or reply.lower().startswith("error"):
                    errors += 1
                    self._note(f"{line} → {reply or '返事なし'}")
                    if errors >= 3:
                        self.stop_reason = "エラーが続いたので着陸"
                        return
                else:
                    errors = 0
            # Modes and imported files alike repeat until the battle ends.

    def _announce_move(self, line: str) -> None:
        """Tell the opponent where this move goes and how long it should take."""
        try:
            target = plans.move(self.pose, line)
        except ValueError:
            return
        x, y = self.pose[0], self.pose[1]
        distance = ((target[0] - x) ** 2 + (target[1] - y) ** 2) ** 0.5
        if distance < 1:
            return
        words = line.split()
        speed = int(words[4]) if words[0] == "go" else self.speed
        # About half a second to speed up and slow down on top of the cruise time.
        self.motion = {"from": [round(x), round(y)], "to": [round(target[0]), round(target[1])],
                       "start": tello_net.now(), "duration": round(distance / max(10, speed) + 0.5, 2)}

    def _wait_watching(self, seconds: float = 0.3) -> None:
        """Hover a moment before looking again, keeping the link alive so it never auto-lands."""
        self.stop_event.wait(seconds)
        if time.monotonic() - self.last_sent > KEEPALIVE and not self.stop_event.is_set():
            self.battery = self._send("battery?", 3) or self.battery

    def _guard_plan(self) -> list[str]:
        opponent = opponent_in_my_frame(self.opponent_pos(0.3))
        steps = guard_steps(self.pose, opponent)
        if any(s.startswith("forward") for s in steps):
            self._note(f"迎え撃つ: 相手({round(opponent[0])}, {round(opponent[1])}) → {' / '.join(steps)}")
        elif any(s.startswith("go") for s in steps):
            self._note("スタート地点に戻る")
        return steps

    def _charge_plan(self) -> list[str]:
        opponent = opponent_in_my_frame(self.opponent_pos(CHARGE_LEAD))
        steps = charge_steps(self.pose, opponent)
        self._note(f"突進: 相手({round(opponent[0])}, {round(opponent[1])}) → {' / '.join(steps)}")
        return steps

    def _flee_step(self) -> str | None:
        """One 'go' toward the spot farthest from the opponent, or None if we are there."""
        opponent = opponent_in_my_frame(self.opponent_pos())
        tx, ty, tz = flee_target(opponent, self.opponent_height())
        x, y, _, heading = self.pose
        # Change height only on a reading taken after the last move, and never above the ceiling,
        # so a stale height can never make it climb again and again.
        fresh = self.height is not None and self.height_at > self.moved_at + 0.1
        dz = min(tz - self.height, MAX_HEIGHT - self.height) if fresh else 0
        dx, dy = tx - x, ty - y
        far = (dx * dx + dy * dy) ** 0.5
        if far > FLEE_STEP:  # short hops, so a charging opponent is re-checked often
            dx, dy = dx * FLEE_STEP / far, dy * FLEE_STEP / far
        command = plans.go_command(dx, dy, dz, heading, 100)
        target = (round(tx), round(ty), tz)
        if command and target != getattr(self, "_flee_target", None):
            self._flee_target = target
            self._note(f"逃げる: 相手{tuple(round(v) for v in opponent)} → ({target[0]}, {target[1]}) 高さ{tz}")
        return command

    def _land_now(self) -> None:
        if self.phase == "down":
            return
        reason = self.stop_reason or "着陸"
        for _ in range(4):
            if self._send("land", 10) == "ok":
                break
        self._finish("landed", reason)

    def _finish(self, phase: str, reason: str) -> None:
        if self.airborne and self.down_at is None:
            self.down_at = tello_net.now()
        self.airborne = False
        self.phase = phase
        self._note(reason)

    def _watch_state(self) -> None:
        """On the ground (h≈0) for 1 s while 'flying' = knocked down."""
        if not self.monitor.listening:
            self._note("高さの情報を受け取れません（8890番が使用中）")
            return
        ground_since = None
        seen = 0.0
        while self.phase in ("starting", "flying"):
            time.sleep(0.05)
            reading = self.monitor.latest.get(self.ip)
            if not reading or reading["at"] == seen:
                continue
            seen = reading["at"]
            if reading["h"] is None or reading["tof"] is None:
                continue
            self.height, self.tof, self.height_at = reading["h"], reading["tof"], reading["at"]
            self.battery = reading["bat"] or self.battery
            on_ground = self.height <= 10 and self.tof <= 30
            settled = self.takeoff_at and tello_net.now() - self.takeoff_at > 3
            if self.phase == "flying" and on_ground and settled:
                ground_since = ground_since or time.monotonic()
                if time.monotonic() - ground_since > 1.0 and not self.stop_event.is_set():
                    self.stop_event.set()
                    self._finish("down", "地面に落ちました")
            else:
                ground_since = None


class StateMonitor:
    """Always listen for Tello state packets (sent ~10 times a second to the PC
    that sent 'command'), so battery and height are live even outside a battle."""

    def __init__(self, port: int = STATE_PORT) -> None:
        self.latest: dict[str, dict] = {}
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind(("", port))
            self.listening = True
        except OSError:
            self.listening = False
            return
        self.sock.settimeout(0.5)
        threading.Thread(target=self._listen, daemon=True).start()

    def _listen(self) -> None:
        while True:
            try:
                data, source = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                return
            fields = dict(item.split(":", 1) for item in data.decode("ascii", "replace").split(";")
                          if ":" in item)

            def number(key):
                try:
                    return int(fields[key])
                except (KeyError, ValueError):
                    return None
            self.latest[source[0]] = {"h": number("h"), "tof": number("tof"),
                                      "bat": fields.get("bat"), "at": time.monotonic()}

    def battery(self, ip: str | None, max_age: float = 3) -> str | None:
        reading = self.latest.get(ip or "")
        if reading and time.monotonic() - reading["at"] < max_age:
            return reading["bat"]
        return None
