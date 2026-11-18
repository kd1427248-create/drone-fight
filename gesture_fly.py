"""Fly the player's one drone from a lean, with no match and no opponent."""

import threading
import time

import tello_net

STEP = 30          # cm of one lean
SPEED = 40
LIMIT = 120        # cm from the takeoff spot
SECONDS = 90
MIN_BATTERY = 30
STALE = 0.7


def hop(x: float, y: float, right: int, forward: int):
    """One step in the nose frame, or None if upright or past the takeoff circle.

    right and forward are -1, 0 or 1. Returns (command, new_x, new_y).
    """
    if right not in (-1, 0, 1) or forward not in (-1, 0, 1) or (right == 0 and forward == 0):
        return None
    nx, ny = x + forward * STEP, y + right * STEP
    if nx * nx + ny * ny > LIMIT * LIMIT:
        return None
    # SDK go: x is forward, y is LEFT.
    return f"go {forward * STEP} {-right * STEP} 0 {SPEED}", nx, ny


class GestureFlight:
    def __init__(self, tello_port: int) -> None:
        self.tello_port = tello_port
        self.lock = threading.Lock()
        self._reset()

    def _reset(self) -> None:
        self.phase = "idle"  # idle, starting, flying, landed
        self.ip: str | None = None
        self.message = ""
        self.lean = (0, 0)
        self.lean_at = 0.0
        self.x = 0.0
        self.y = 0.0
        self.stop_event = threading.Event()
        self.sock = None

    def public(self) -> dict:
        return {"phase": self.phase, "message": self.message,
                "x": round(self.x), "y": round(self.y)}

    def set_lean(self, right: int, forward: int) -> None:
        with self.lock:
            self.lean = (right, forward)
            self.lean_at = time.monotonic()

    def _lean(self) -> tuple[int, int, bool]:
        with self.lock:
            right, forward = self.lean
            fresh = time.monotonic() - self.lean_at <= STALE
        return right, forward, fresh

    def start(self, ip: str, battery) -> str | None:
        """Begin a practice flight. Returns an error message, or None if it started."""
        with self.lock:
            if self.phase in ("starting", "flying"):
                return "もう飛んでいます"
            try:
                level = int(battery)
            except (TypeError, ValueError):
                level = None
            if level is not None and level < MIN_BATTERY:
                return "電池が30%未満なので離陸しません"
            self._reset()
            self.phase = "starting"
            self.ip = ip
            self.message = "離陸します…"
        threading.Thread(target=self._run, daemon=True).start()
        return None

    def land(self, reason: str = "着陸します…") -> None:
        if self.phase not in ("starting", "flying"):
            return
        self.message = reason
        self.stop_event.set()

    def shutdown(self) -> None:
        """The app is closing: land now, because the flight thread dies with it."""
        if self.phase not in ("starting", "flying"):
            return
        self.stop_event.set()
        sock = self.sock or tello_net.tello_socket(0.5)
        self.sock = sock
        for _ in range(5):
            try:
                sock.sendto(b"land", (self.ip, self.tello_port))
            except OSError:
                pass
            time.sleep(0.4)

    def _send(self, command: str, timeout: float = 8) -> str | None:
        sock = self.sock
        sock.settimeout(0)
        try:
            while True:
                sock.recvfrom(1024)
        except OSError:
            pass
        sock.settimeout(0.2)
        try:
            sock.sendto(command.encode(), (self.ip, self.tello_port))
        except OSError:
            return None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self.stop_event.is_set():
            try:
                data, source = sock.recvfrom(1024)
            except OSError:
                continue
            if source[0] == self.ip:
                return data.decode("utf-8", "replace").strip()
        return None

    def _run(self) -> None:
        sock = tello_net.tello_socket(0.5)
        self.sock = sock
        try:
            if self.stop_event.is_set():
                self.phase = "idle"
                self.message = ""
                return
            took_off = self._send("command", 4) == "ok" and self._send("takeoff", 12) == "ok"
            if not took_off:
                if self.stop_event.is_set():
                    self._send("land", 5)
                    self.phase = "landed"
                    self.message = "着陸しました"
                else:
                    self.phase = "idle"
                    self.message = "ドローンから返事がありません。電源を入れ直して、もう一度押してください"
                return
            self.phase = "flying"
            self.message = "手を出すと一歩動きます。Escか「着陸」で終わります"
            deadline = time.monotonic() + SECONDS
            while not self.stop_event.is_set() and time.monotonic() < deadline:
                right, forward, fresh = self._lean()
                moved = hop(self.x, self.y, right, forward) if fresh else None
                if moved is None:
                    if fresh and (right or forward):
                        self.message = "離陸した場所から120cmより先には行きません"
                    self.stop_event.wait(0.3)
                    continue
                line, nx, ny = moved
                if self._send(line) == "ok":
                    self.x, self.y = nx, ny
                    self.message = "手を出すと一歩動きます。Escか「着陸」で終わります"
            timed_out = time.monotonic() >= deadline and not self.stop_event.is_set()
            self._send("land", 8)
            self.phase = "landed"
            self.message = "90秒たったので着陸しました" if timed_out else "着陸しました"
        finally:
            self.sock = None
            sock.close()
