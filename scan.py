"""Map the eight mission pads by flying over them with the player's own Tello.

No photo and no guessed layout: the drone takes off from pad 4, sweeps a
rectangle the player has declared safe, reads each pad's id and its offset
from the pad centre with the downward camera, and keeps the map only if every
pad was seen several times in agreeing places and the loop back to pad 4
closes. Any doubt lands the drone and saves nothing.

Map coordinates (cm): pad 4 is the origin, x follows the rocket arrows (from
side A toward side B), y points to the right of them (toward pads 7-8).
"""

import math
import statistics
import threading
import time

import arena
import tello_net

SCAN_HEIGHT = 100     # cm; the pads are readable from 30-120 cm
SPEED = 60            # cm/s; 100 would overshoot, 40 made the scan slow
STEP = 50             # cm per move between readings
LANE = 60             # cm between sweep lanes (the camera sees about ±40 cm at 1 m)
EDGE = 30             # cm kept clear of the declared boundary
SETTLE = 0.4          # s hovering to take a reading when a pad is in view
SETTLE_EMPTY = 0.25   # s hovering before giving up on a stop with no pad in view
MIN_SEEN = 2          # separate stops at which every pad must be seen
DONE_SPREAD = 15      # cm: sightings this close ends the sweep early (the saved map allows MAX_SPREAD)
MAX_SPREAD = 25       # cm: a pad's sightings must agree this well
MAX_CLOSURE = 30      # cm: back over pad 4, our tracked position may be this far off
MAX_YAW = 20          # degrees away from the rockets before we give up
TIME_LIMIT = 360      # s at most for the whole scan (the route sets the actual limit)
SECONDS_PER_STOP = 4.5  # move + settle per stop, used to size the time limit
START_BATTERY = 50
LOW_BATTERY = 25
STATE_TIMEOUT = 1.5   # s without a state packet = lost
RANGE_LIMITS = (50, 600)  # each boundary distance from pad 4, cm


def boundary(back: float, front: float, far: float, near: float) -> tuple[float, float, float, float]:
    """The safe rectangle around pad 4 as the field (x min, x max, y min, y max)."""
    values = {"後ろ": back, "前": front, "奥": far, "手前": near}
    for label, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{label}の距離を数字で入れてください")
        if not RANGE_LIMITS[0] <= value <= RANGE_LIMITS[1]:
            raise ValueError(f"{label}の距離は{RANGE_LIMITS[0]}〜{RANGE_LIMITS[1]}cmにしてください")
    return (-float(back), float(front), -float(far), float(near))


def lanes(y_min: float, y_max: float, upper: float | None = None) -> list[float]:
    """Sweep lines: the origin pad's own row (y = 0), then outward, LANE apart.

    With eight pads, the pads of the far row (1-3) and the near row (7-8) lie on
    both sides of the start row, so lanes alternate sides; working outward finds
    them all without flying to the far edge of a roomy boundary. 'upper' cuts off
    the near side when no pad can be there (the 4-pad square has none).
    """
    low, high = y_min + EDGE, y_max - EDGE
    if upper is not None:
        high = min(high, upper)
    found = [k * LANE for k in range(-int(abs(low) // LANE), int(max(high, 0) // LANE) + 1) if low <= k * LANE <= high]
    if not found:
        return [(low + high) / 2]
    # A boundary edge farther than half a lane from the nearest sweep line still needs one.
    for edge in (low, high):
        if min(abs(edge - y) for y in found) > LANE / 1.5:
            found.append(edge)
    return sorted(found, key=lambda y: (abs(y), y))


def route(field: tuple, kind: str = "pads8") -> list[tuple[float, float]]:
    """Back-and-forth sweeps, starting along the start row, keeping EDGE clear of the boundary."""
    x_min, x_max, y_min, y_max = field
    xs = _ticks(x_min + EDGE, x_max - EDGE, STEP)
    points = []
    # The 4-pad square 1 2 / 3 4 has nothing on the near side of the start row.
    for i, y in enumerate(lanes(y_min, y_max, upper=0 if kind == "square4" else None)):
        sweep = xs if i % 2 == 0 else list(reversed(xs))
        points += [(x, y) for x in sweep]
    return points


def _ticks(low: float, high: float, step: float) -> list[float]:
    if high < low:
        return [(low + high) / 2]
    count = max(1, math.ceil((high - low) / step))
    return [low + (high - low) * i / count for i in range(count + 1)]


def hops(start: tuple[float, float], end: tuple[float, float], step: float = STEP) -> list[tuple[float, float]]:
    """Moves of at most 'step' that take us from start to end."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    count = max(1, math.ceil(math.hypot(dx, dy) / step))
    return [(dx / count, dy / count)] * count


class PadMap:
    """Every sighting of every pad, and where that puts its centre."""

    def __init__(self, kind: str = "pads8") -> None:
        self.kind = kind
        self.ids = tuple(int(k) for k in arena.SETS[kind]["ids"])
        # The pad that A takes off from is the origin of the map.
        self.origin = int(arena.SETS[kind]["starts"]["A"])
        self.sightings: dict[int, list[tuple[float, float]]] = {k: [] for k in self.ids}

    def center(self, pad: int) -> tuple[float, float] | None:
        if pad == self.origin:
            return (0.0, 0.0)  # the origin, by definition
        seen = self.sightings[pad]
        if not seen:
            return None
        return (statistics.fmean(p[0] for p in seen), statistics.fmean(p[1] for p in seen))

    def observe(self, pad: int, offset: tuple[float, float], tracked: tuple[float, float]) -> tuple[float, float]:
        """Record a sighting; returns our corrected position.

        'offset' is where we are relative to the pad centre (map axes), and
        'tracked' is where our own moves say we are. The centre implied by this
        sighting is tracked - offset; once the pad is known we re-anchor on it
        so move errors never pile up between pads.
        """
        self.sightings[pad].append((tracked[0] - offset[0], tracked[1] - offset[1]))
        cx, cy = self.center(pad)
        return (cx + offset[0], cy + offset[1])

    def spread(self, pad: int) -> float:
        seen = self.sightings[pad]
        if not seen:
            return 0.0
        cx, cy = self.center(pad)
        return max(math.hypot(p[0] - cx, p[1] - cy) for p in seen)

    def solid(self, pad: int) -> bool:
        """Seen from enough separate stops, in places that agree (the origin pad is 0,0 by definition)."""
        if len(self.sightings[pad]) < MIN_SEEN:
            return False
        return pad == self.origin or self.spread(pad) <= DONE_SPREAD

    def done(self) -> bool:
        return all(self.solid(k) for k in self.ids)

    def summary(self) -> dict:
        return {str(k): {"center": [round(v, 1) for v in self.center(k)] if self.center(k) else None,
                         "seen": len(self.sightings[k]), "spread": round(self.spread(k), 1)}
                for k in self.ids}


def build_layout(pads: PadMap, field: tuple, closure: float | None) -> dict:
    """The arena from a finished scan, or ValueError saying why it cannot be trusted."""
    ids, origin = pads.ids, pads.origin
    missing = [str(k) for k in ids if not pads.sightings[k]]
    if missing:
        raise ValueError(f"パッド{'・'.join(missing)}が見つかりませんでした")
    few = [str(k) for k in ids if len(pads.sightings[k]) < MIN_SEEN]
    if few:
        raise ValueError(f"パッド{'・'.join(few)}は見えた回数が足りません（{MIN_SEEN}回以上必要）")
    for k in ids:
        if pads.spread(k) > MAX_SPREAD:
            raise ValueError(f"パッド{k}の位置が安定しません（ズレ{pads.spread(k):.0f}cm、{MAX_SPREAD}cmまで）")
    if closure is None:
        raise ValueError(f"最後にパッド{origin}へ戻って確認できませんでした")
    if closure > MAX_CLOSURE:
        raise ValueError(f"1周してパッド{origin}に戻ったときのズレが{closure:.0f}cmでした（{MAX_CLOSURE}cmまで）")
    centers = {str(k): [round(c, 1) for c in pads.center(k)] for k in ids}
    b_pad = arena.SETS[pads.kind]["starts"]["B"]
    return arena.normalize({"pads": centers, "field": list(field),
                            "starts": {"A": [*centers[str(origin)], 0], "B": [*centers[b_pad], 180]},
                            "scanned_at": tello_net.now()})


class ScanAbort(Exception):
    pass


class ArenaScanner:
    """Flies one scan in a background thread. One drone, before any battle."""

    def __init__(self, monitor, tello_port: int = tello_net.TELLO_PORT) -> None:
        self.monitor = monitor
        self.tello_port = tello_port
        self.lock = threading.Lock()
        self._clear()

    def _clear(self) -> None:
        self.phase = "idle"  # idle, running, done, failed
        self.message = ""
        self.ip: str | None = None
        self.field: tuple | None = None
        self.kind = "pads8"
        self.pads = PadMap()
        self.pose = (0.0, 0.0)
        self.closure: float | None = None
        self.log: list[str] = []
        self.stops_done = 0
        self.stops_total = 0
        self.started = 0.0
        self.time_limit = TIME_LIMIT
        self.stop_event = threading.Event()
        self.sock = None
        self.airborne = False

    @property
    def active(self) -> bool:
        return self.phase == "running"

    def start(self, ip: str, field: tuple, on_success, kind: str = "pads8") -> str | None:
        """Begin a scan of the 8-pad ('pads8') or 4-pad square ('square4') field.

        Returns an error message instead if it cannot start.
        """
        with self.lock:
            if self.active:
                return "スキャン中です"
            if kind not in arena.SETS:
                return "パッドの枚数は4枚か8枚にしてください"
            if not self.monitor.listening:
                return "高さやパッドの情報を受け取れません（8890番が使用中）"
            self._clear()
            self.kind = kind
            self.pads = PadMap(kind)
            self.phase, self.ip, self.field = "running", ip, field
            self.stops_total = len(route(field, kind))
            # Sweep, revisits and the trip home, with room to spare; never more than TIME_LIMIT.
            self.time_limit = min(TIME_LIMIT, 90 + SECONDS_PER_STOP * self.stops_total)
            self.started = time.monotonic()
        threading.Thread(target=self._run, args=(on_success,), daemon=True).start()
        return None

    def stop(self) -> None:
        self.stop_event.set()

    def status(self) -> dict:
        return {"phase": self.phase, "message": self.message, "log": self.log[-10:],
                "kind": self.kind, "origin": self.pads.origin,
                "pads": self.pads.summary(), "pose": [round(v) for v in self.pose],
                "field": list(self.field) if self.field else None,
                "progress": [self.stops_done, self.stops_total], "time_limit": round(self.time_limit),
                "elapsed": round(time.monotonic() - self.started) if self.started else 0}

    # ---- flight ------------------------------------------------------------
    def _note(self, text: str) -> None:
        self.log.append(time.strftime("%H:%M:%S ") + text)

    def _send(self, command: str, timeout: float = 10) -> str | None:
        sock = self.sock
        sock.settimeout(0)
        try:
            while True:
                sock.recvfrom(1024)  # drop late replies
        except OSError:
            pass
        sock.settimeout(0.2)
        sock.sendto(command.encode(), (self.ip, self.tello_port))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, source = sock.recvfrom(1024)
            except OSError:
                continue
            if source[0] == self.ip:
                return data.decode("utf-8", "replace").strip()
        return None

    def _reading(self) -> dict:
        reading = self.monitor.latest.get(self.ip)
        if not reading or time.monotonic() - reading["at"] > STATE_TIMEOUT:
            raise ScanAbort("ドローンからの情報が途切れました")
        return reading

    def _guard(self) -> None:
        if self.stop_event.is_set():
            raise ScanAbort("停止ボタンで中止しました")
        if time.monotonic() - self.started > self.time_limit:
            raise ScanAbort(f"{self.time_limit:.0f}秒を超えたので中止しました")
        reading = self._reading()
        battery = reading.get("bat")
        if battery and str(battery).isdigit() and int(battery) < LOW_BATTERY:
            raise ScanAbort(f"電池が{LOW_BATTERY}%未満になりました")
        yaw = reading.get("pad_yaw")
        if reading.get("mid") in self.pads.ids and yaw is not None and abs(yaw) > MAX_YAW:
            raise ScanAbort(f"機体の向きがロケットから{abs(yaw)}°ずれました（{MAX_YAW}°まで）")

    def _look(self) -> tuple[int, tuple[float, float]] | None:
        """Hover briefly, then the pad most seen and our median offset from its centre.

        A stop with nothing in view is left after SETTLE_EMPTY; once a pad shows up we
        stay for SETTLE so the median is taken over several readings.
        """
        began = time.monotonic()
        end = began + SETTLE_EMPTY
        seen: dict[int, list[tuple[int, int]]] = {}
        last = None
        while time.monotonic() < end:
            self._guard()
            reading = self._reading()
            if reading["at"] != last:
                last = reading["at"]
                pad = reading.get("mid")
                if pad in self.pads.ids and reading.get("x") is not None and reading.get("y") is not None:
                    seen.setdefault(pad, []).append((reading["x"], reading["y"]))
                    end = max(end, began + SETTLE)
            time.sleep(0.05)
        if not seen:
            return None
        pad, offsets = max(seen.items(), key=lambda item: len(item[1]))
        # Pad y points left; the map's y points right.
        return pad, (statistics.median(o[0] for o in offsets), -statistics.median(o[1] for o in offsets))

    def _hop(self, dx: float, dy: float) -> None:
        """One short move with the drone still facing the rockets."""
        x, y = self.pose[0] + dx, self.pose[1] + dy
        x_min, x_max, y_min, y_max = self.field
        if not (x_min + 10 <= x <= x_max - 10 and y_min + 10 <= y <= y_max - 10):
            raise ScanAbort("安全な範囲の外へ出そうになったので中止しました")
        command = f"go {round(dx)} {round(-dy)} 0 {SPEED}"  # go's y points left
        if all(-20 < v < 20 for v in (round(dx), round(-dy))):
            return  # too small for the SDK; skip it
        for attempt in range(3):
            self._guard()
            reply = self._send(command, 15)
            if reply == "ok":
                self.pose = (x, y)
                return
            self._note(f"{command} → {reply or '返事なし'}")
        raise ScanAbort("移動の命令が3回続けて失敗しました")

    def _take_reading(self) -> None:
        sighting = self._look()
        if sighting:
            pad, offset = sighting
            self.pose = self.pads.observe(pad, offset, self.pose)

    def _run(self, on_success) -> None:
        try:
            self.sock = tello_net.tello_socket(0.2)
            self._fly()
            self._land()
            layout = build_layout(self.pads, self.field, self.closure)
            on_success(layout)
            self.phase, self.message = "done", f"{len(self.pads.ids)}枚の地図を保存しました"
            self._note(self.message)
        except (ScanAbort, ValueError) as exc:
            self.phase, self.message = "failed", f"{exc}。地図は保存していません"
            self._note(self.message)
        except Exception as exc:  # never leave the drone in the air
            self.phase, self.message = "failed", f"予期しないエラー（{exc}）。地図は保存していません"
            self._note(self.message)
        finally:
            if self.airborne:
                self._land()
            if self.sock:
                self.sock.close()
                self.sock = None

    def _fly(self) -> None:
        if self._send("command", 5) != "ok":
            raise ScanAbort("ドローンから返事がありません")
        battery = self._send("battery?", 5)
        if not battery or not battery.isdigit() or int(battery) < START_BATTERY:
            raise ScanAbort(f"電池が{START_BATTERY}%未満なのでスキャンしません（{battery}）")
        if self._send("mon", 5) != "ok" or self._send("mdirection 0", 5) != "ok":
            raise ScanAbort("ミッションパッドの検出をオンにできませんでした")
        if self.stop_event.is_set():
            raise ScanAbort("停止ボタンで中止しました")
        origin = self.pads.origin
        self._note(f"パッド{origin}から離陸します（電池 {battery}%）")
        self.airborne = True
        if self._send("takeoff", 20) != "ok":
            raise ScanAbort("離陸できませんでした")
        height = (self._reading().get("h") or 80)
        if SCAN_HEIGHT - height >= 20:
            self._send(f"up {round(SCAN_HEIGHT - height)}", 10)
        self._send(f"speed {SPEED}", 5)
        start = self._look()
        if not start or start[0] != origin:
            seen = f"パッド{start[0]}" if start else "パッドなし"
            raise ScanAbort(f"離陸した場所でパッド{origin}が見えません（{seen}）。"
                            f"パッド{origin}の中心から離陸してください")
        self.pose = self.pads.observe(origin, start[1], (0.0, 0.0))
        self._note(f"パッド{origin}を確認。スキャンを始めます")

        for point in route(self.field, self.kind):
            self._go_to(point)
            self.stops_done += 1
            if self.pads.done():
                self._note(f"{len(self.pads.ids)}枚とも十分に見えたので、パッド{origin}へ戻ります")
                break
        self._revisit()
        # Back to the origin pad, reading pads on the way. How far our tracked position is
        # from it when we get there shows whether the map can be trusted.
        for dx, dy in hops(self.pose, (0.0, 0.0)):
            self._hop(dx, dy)
            if self.pose != (0.0, 0.0):
                self._take_reading()
        tracked = self.pose
        sighting = self._look()
        if sighting and sighting[0] == origin:
            offset = sighting[1]
            self.closure = math.hypot(tracked[0] - offset[0], tracked[1] - offset[1])
            self.pose = offset  # the origin pad is (0, 0): this is exactly where we are
            self._note(f"パッド{origin}に戻りました（1周のズレ {self.closure:.0f}cm）")
        else:
            self._note(f"戻った場所でパッド{origin}が見えませんでした")

    def _go_to(self, point: tuple[float, float]) -> None:
        # Look after every hop, not only at the end: a long way (changing sweep lane,
        # fetching a pad again) passes pads, and each one seen corrects the drift.
        for dx, dy in hops(self.pose, point):
            self._hop(dx, dy)
            self._take_reading()

    def _revisit(self) -> None:
        """Fly back over pads seen too few times, so each is measured from more than one stop."""
        x_min, x_max, y_min, y_max = self.field
        for pad in self.pads.ids:
            for nudge in ((0, 0), (15, 0), (0, 15)):
                center = self.pads.center(pad)
                if center is None or self.pads.solid(pad):
                    break
                target = (min(max(center[0] + nudge[0], x_min + EDGE), x_max - EDGE),
                          min(max(center[1] + nudge[1], y_min + EDGE), y_max - EDGE))
                self._note(f"パッド{pad}をもう一度見に行きます")
                self._go_to(target)

    def _land(self) -> None:
        if not self.airborne or not self.sock:
            return
        for _ in range(4):
            if self._send("land", 10) == "ok":
                break
        self.airborne = False
        self._note("着陸しました")
