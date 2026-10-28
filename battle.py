"""Fly one battle with the player's own drone and watch whether it is still airborne."""

import socket
import threading
import time
from pathlib import Path

import plans
import tello_net

APP_DIR = Path(__file__).resolve().parent
MODE_DIR = APP_DIR / "modes"
FILE_DIR = APP_DIR.parent / "Tello-Python" / "Single_Tello_Test"
HIDDEN_FILES = {"wifi_setup.txt"}  # holds a Wi-Fi password
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


def own_files() -> list[str]:
    return sorted(p.name for p in FILE_DIR.glob("*.txt") if p.name not in HIDDEN_FILES)


MAX_HEIGHT = 180


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


def plan_lines(kind: str, name: str) -> list[str]:
    if kind == "mode" and name in {m["id"] for m in modes()}:
        path = MODE_DIR / f"{name}.txt"
    elif kind == "plan" and name in {p["name"] for p in plans.list_plans() if p["ok"]}:
        path = plans.PLAN_DIR / name
    elif kind == "file" and name in own_files():
        path = FILE_DIR / name
    else:
        raise ValueError("unknown plan")
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        # The runner does command / takeoff itself; 'ap' must never run mid-battle.
        if line and not line.startswith("#") and line not in ("command", "takeoff", plans.REPEAT) \
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
        self._reset()

    def _reset(self) -> None:
        self.phase = "idle"  # idle, starting, flying, down, landed
        self.ip: str | None = None
        self.plan: dict | None = None
        self.airborne = False
        self.takeoff_at: float | None = None
        self.down_at: float | None = None
        self.height: int | None = None
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

    def set_mode(self, mode_id: str) -> bool:
        if self.phase != "flying" or mode_id not in {m["id"] for m in modes()}:
            return False
        self.plan = {"kind": "mode", "name": mode_id}
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
        return {"phase": self.phase, "airborne": self.airborne, "down_at": self.down_at,
                "takeoff_at": self.takeoff_at, "height": self.height, "battery": self.battery,
                "plan": self._plan_label(), "current": self.current}

    def details(self) -> dict:
        return {**self.public(), "log": self.log[-12:], "mode_id": (self.plan or {}).get("name")
                if (self.plan or {}).get("kind") == "mode" else None}

    # ---- internals ---------------------------------------------------------
    @staticmethod
    def _repeats(plan: dict) -> bool:
        return plans.check((plans.PLAN_DIR / plan["name"]).read_text(encoding="utf-8"))["repeat"]

    def _plan_label(self) -> str:
        if not self.plan:
            return ""
        if self.plan["kind"] == "mode":
            return next((m["name"] for m in modes() if m["id"] == self.plan["name"]), self.plan["name"])
        return self.plan["name"]

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
            lines = plan_lines(plan["kind"], plan["name"])
            for line in lines:
                if self.stop_event.is_set() or self.phase != "flying" or self.plan != plan:
                    break  # stopped, knocked down, or the mode was switched
                if line.startswith("delay"):
                    self._delay(float(line.split()[1]))
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
                reply = self._send(line)
                self.moved_at = time.monotonic()
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
            loops = plan["kind"] == "mode" or (plan["kind"] == "plan" and self._repeats(plan))
            if not loops and self.plan == plan and self.phase == "flying" \
                    and not self.stop_event.is_set():
                # A file runs once; then hover (and keep the link alive) until the end.
                self._note("ファイルの最後まで実行。ホバリングして待ちます")
                while not self.stop_event.is_set() and self.phase == "flying" and self.plan == plan:
                    self._delay(4)

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
