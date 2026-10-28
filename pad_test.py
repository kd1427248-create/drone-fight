"""Fly one drone and read the mission pads under it.

Hover keeps it over the takeoff spot so you can see whether the camera reads a
pad. Scan flies a slow grid around that spot and records each pad it finds.
The takeoff point is the origin and the nose at takeoff is forward.
"""

import socket
import threading
import time

import pads
import plans
import tello_net

MIN_BATTERY = 30
HOVER_SECONDS = 45
SCAN_SECONDS = 90
SCAN_STEP = 50
SCAN_SPEED = 30


def scan_points(span: int, step: int = SCAN_STEP) -> list[tuple[int, int]]:
    """A serpentine that stays inside a square of the given half-size, in cm."""
    line = list(range(0, span + 1, step))
    if line[-1] != span:
        line.append(span)
    across = [-value for value in reversed(line[1:])] + line
    down = line + [-value for value in line[1:]]
    points = []
    for index, y in enumerate(down):
        row = across if index % 2 == 0 else list(reversed(across))
        for x in row:
            if not points or points[-1] != (x, y):
                points.append((x, y))
    return points


class PadTester:
    """One short flight used from the settings page. It never runs during a battle."""

    def __init__(self, monitor) -> None:
        self.monitor = monitor
        self.lock = threading.Lock()
        self.sock: socket.socket | None = None
        self._idle()

    def _idle(self) -> None:
        self.phase = "idle"  # idle, starting, flying, landing, done
        self.mode = ""
        self.ip: str | None = None
        self.span = 80
        self.airborne = False
        self.cut = False
        self.hovering = False
        self.pose = (0.0, 0.0, 0.0, 0)
        self.found: dict[int, dict] = {}
        self.live: dict | None = None
        self.height: int | None = None
        self.message = ""
        self.log: list[str] = []
        self._samples: list[tuple] = []
        self._stop = threading.Event()
        self._last_sent = 0.0

    def busy(self) -> bool:
        return self.phase in ("starting", "flying", "landing")

    def start(self, ip: str, mode: str, span: int = 80) -> bool:
        with self.lock:
            if self.busy():
                return False
            self._idle()
            self.phase = "starting"
            self.mode = mode
            self.ip = ip
            self.span = span
            self.message = "接続しています…"
        threading.Thread(target=self._watch, daemon=True).start()
        threading.Thread(target=self._run, daemon=True).start()
        return True

    def land(self) -> None:
        """Ask the flight to stop and land. Safe to call when it is already down."""
        self._stop.set()
        self._raw("stop")

    def emergency(self) -> None:
        self._stop.set()
        self.cut = True
        self.airborne = False
        for _ in range(3):
            self._raw("emergency")
        self.phase = "done"
        self.message = "緊急停止を送りました（プロペラ停止）"
        self._note(self.message)

    def shutdown(self) -> None:
        """The app is closing: land even though the flight thread is about to die."""
        if not self.busy():
            return
        self._stop.set()
        self.cut = True
        sock = self.sock or tello_net.tello_socket(0.5)
        self.sock = sock
        for _ in range(5):
            self._raw("land")
            time.sleep(0.3)

    def public(self) -> dict:
        with self.lock:
            found = [{"id": pad_id, **pose} for pad_id, pose in sorted(self.found.items())]
            live = dict(self.live) if self.live else None
        return {"phase": self.phase, "mode": self.mode, "message": self.message,
                "live": live, "height": self.height, "found": found, "log": self.log[-8:]}

    def _note(self, text: str) -> None:
        self.log.append(time.strftime("%H:%M:%S ") + text)

    def _raw(self, command: str) -> None:
        if self.sock and self.ip:
            try:
                self.sock.sendto(command.encode(), (self.ip, tello_net.TELLO_PORT))
            except OSError:
                pass

    def _send(self, command: str, timeout: float = 12) -> str | None:
        sock = self.sock
        if not sock or not self.ip:
            return None
        sock.settimeout(0)
        try:
            while True:
                sock.recvfrom(1024)
        except OSError:
            pass
        sock.settimeout(0.2)
        self._last_sent = time.monotonic()
        self._raw(command)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._stop.is_set() and command not in ("land", "stop"):
                return None
            try:
                data, source = sock.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                return None
            if source[0] == self.ip:
                return data.decode("utf-8", "replace").strip()
        return None

    def _run(self) -> None:
        self.sock = tello_net.tello_socket(0.2)
        try:
            if self._send("command", 5) != "ok":
                return self._finish("ドローンから返事がありません")
            battery = self._send("battery?", 5)
            if not battery or not battery.isdigit() or int(battery) < MIN_BATTERY:
                return self._finish(f"電池が{MIN_BATTERY}%未満なので離陸しません（{battery}）")
            if self._send("mon", 5) != "ok" or self._send("mdirection 0", 5) != "ok":
                return self._finish("パッド検出を開始できませんでした")
            self._note(f"離陸します（電池 {battery}%）")
            if self._send("takeoff", 20) != "ok":
                self._send("land", 8)
                return self._finish("離陸できませんでした")
            self.airborne = True
            self.phase = "flying"
            self.hovering = True
            self.message = "飛行中。パッドを探しています"
            self._note("離陸しました")
            if self._stop.is_set():
                return
            if self.mode == "scan":
                self._scan()
            else:
                self._hover()
        finally:
            if self.airborne and not self.cut:
                self._land()
            sock, self.sock = self.sock, None
            if sock:
                sock.close()

    def _hover(self) -> None:
        self.message = "その場で読んでいます。パッドの真ん中の上で番号が出ます"
        end = time.monotonic() + HOVER_SECONDS
        while not self._stop.is_set() and time.monotonic() < end:
            self._keepalive()
            self._stop.wait(0.4)
        if not self._stop.is_set():
            self._note("時間が来たので着陸します")

    def _scan(self) -> None:
        self.message = "周りを飛んでパッドを探しています"
        self._note(f"前後左右 {self.span}cm をスキャンします")
        self._pause(1.0)  # read the pad under the takeoff spot before moving
        end = time.monotonic() + SCAN_SECONDS
        for x, y in scan_points(self.span):
            if self._stop.is_set() or time.monotonic() >= end:
                break
            if not self._go_to(x, y):
                self._note("移動できなかったのでスキャンをやめます")
                break
            # Stay still long enough for a few steady camera readings.
            self._pause(0.8)
        else:
            self._note("スキャンが終わりました")
        if time.monotonic() >= end and not self._stop.is_set():
            self._note("時間が来たので着陸します")

    def _go_to(self, x: int, y: int) -> bool:
        dx, dy = x - self.pose[0], y - self.pose[1]
        command = plans.go_command(dx, dy, 0, self.pose[3], SCAN_SPEED)
        if command is None:
            return True
        self.hovering = False
        reply = self._send(command, 15)
        if reply != "ok":
            self._note(f"{command} → {reply or '返事なし'}")
            self.hovering = True
            return False
        try:
            self.pose = plans.move(self.pose, command)
        except ValueError:
            return False
        return True

    def _pause(self, seconds: float) -> None:
        self.hovering = True
        end = time.monotonic() + seconds
        while not self._stop.is_set() and time.monotonic() < end:
            self._stop.wait(0.1)

    def _keepalive(self) -> None:
        if time.monotonic() - self._last_sent > 4 and not self._stop.is_set():
            self._send("battery?", 3)

    def _land(self) -> None:
        self.phase = "landing"
        self.message = "着陸しています…"
        self._raw("stop")
        for _ in range(4):
            if self._send("land", 8) == "ok":
                break
        self.airborne = False
        self._finish("着陸しました")

    def _finish(self, message: str) -> None:
        self.airborne = False
        self.hovering = False
        self.phase = "done"
        self.message = message
        self._note(message)

    def _watch(self) -> None:
        seen = 0.0
        while self.phase in ("starting", "flying", "landing"):
            time.sleep(0.05)
            reading = self.monitor.latest.get(self.ip or "")
            if not reading or reading.get("at") == seen:
                continue
            seen = reading["at"]
            self.height = reading.get("h")
            mid = reading.get("mid")
            self.live = {"mid": mid, "x": reading.get("x"), "y": reading.get("y"),
                         "z": reading.get("z"), "yaw": reading.get("pad_yaw")}
            if self.phase == "flying":
                self._consider(reading)

    def _consider(self, reading: dict) -> None:
        """Record a new pad, or use a known one to correct where we think we are."""
        if not self.hovering:
            return
        mid = reading.get("mid")
        x, y, z, yaw = reading.get("x"), reading.get("y"), reading.get("z"), reading.get("pad_yaw")
        if mid is None or mid < 1 or None in (x, y, z, yaw):
            self._samples = []
            return
        if self.height is not None and self.height > 20 and abs(z - self.height) > 50:
            return
        now = time.monotonic()
        prev = self._samples[-1] if self._samples else None
        if prev and (prev[0] != mid or abs(x - prev[1]) > 15 or abs(y - prev[2]) > 15 or now - prev[5] > 0.4):
            self._samples = []
        self._samples.append((mid, x, y, z, yaw, now))
        self._samples = self._samples[-4:]
        if len(self._samples) < 3:
            return
        with self.lock:
            known = self.found.get(mid)
            if known:
                pose = pads.world_pose(mid, x, y, z, yaw, {mid: {"id": mid, **known}})
                if pose:
                    self.pose = pose
                return
            if not (-150 <= x <= 150 and -150 <= y <= 150 and 20 <= z <= 200):
                return
            placed = pads.pad_on_map(self.pose[0], self.pose[1], self.pose[3], x, y, yaw)
            self.found[mid] = placed
            self._note(f"パッド{mid}を記録（前{placed['x']}cm・右{placed['y']}cm・向き{placed['yaw']}）")
            self.message = f"パッド{mid}を読みました"
