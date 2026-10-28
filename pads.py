"""Where the mission pads are on the floor, and where a drone is when it sees one.

Both PCs use the same map. x is centimeters forward from a point you choose on
the floor, y is centimeters to the right, and yaw is the direction the pad's
rocket points, in degrees clockwise from forward (0 forward, 90 right).

A Tello only reports a pad while it is 30-120 cm above it. In that report, x
is centimeters along the rocket and y is centimeters to the rocket's left.
mpry yaw is the nose, measured counterclockwise from the rocket.
"""

import json
import math

import tello_net

PAD_FILE = tello_net.DATA_DIR / "pads.json"
FIELD_MARGIN = 100  # cm of flying room around the outermost pads
_cache = {"mtime": None, "pads": {}}


def load() -> dict[int, dict]:
    """Pad id -> {x, y, yaw}. Missing or broken files mean no pads."""
    try:
        mtime = PAD_FILE.stat().st_mtime
    except OSError:
        _cache["mtime"] = None
        _cache["pads"] = {}
        return {}
    if mtime == _cache["mtime"]:
        return _cache["pads"]
    try:
        raw = json.loads(PAD_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        raw = {}
    found = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            pad = _pad(key, value if isinstance(value, dict) else {})
            if pad:
                found[pad["id"]] = pad
    _cache["mtime"] = mtime
    _cache["pads"] = found
    return found


def _pad(key, value: dict) -> dict | None:
    try:
        pad_id = int(key)
        x, y = int(value["x"]), int(value["y"])
        yaw = int(value.get("yaw", 0))
    except (KeyError, TypeError, ValueError):
        return None
    if not 1 <= pad_id <= 8 or not all(-500 <= n <= 500 for n in (x, y)) or not -360 <= yaw <= 360:
        return None
    return {"id": pad_id, "x": x, "y": y, "yaw": yaw % 360}


def public() -> list[dict]:
    return sorted(load().values(), key=lambda pad: pad["id"])


def save(rows) -> dict:
    """Replace the map with the rows from the settings page."""
    if not isinstance(rows, list):
        return {"ok": False, "message": "位置を読めませんでした", "pads": public()}
    cleaned = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            return {"ok": False, "message": "位置を読めませんでした", "pads": public()}
        if row.get("x") in ("", None) and row.get("y") in ("", None) and row.get("yaw") in ("", None):
            continue
        pad = _pad(row.get("id"), row)
        if not pad:
            return {"ok": False,
                    "message": "埋まっている行は、番号（1〜8）と前・右（-500〜500）と向き（-360〜360）を整数で入れてください",
                    "pads": public()}
        if pad["id"] in seen:
            return {"ok": False, "message": f"パッド{pad['id']}が重なっています", "pads": public()}
        seen.add(pad["id"])
        cleaned.append(pad)
    stored = {str(pad["id"]): {"x": pad["x"], "y": pad["y"], "yaw": pad["yaw"]} for pad in cleaned}
    PAD_FILE.write_text(json.dumps(stored, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _cache["mtime"] = None  # the next load reads what we just wrote
    message = f"{len(cleaned)}枚の位置を保存しました" if cleaned else "パッドの位置を消しました"
    return {"ok": True, "message": message, "pads": public()}


def world_pose(pad_id: int, dx: int, dy: int, dz: int, yaw: int,
               pads: dict | None = None) -> tuple | None:
    """Drone pose (x forward, y right, z up, heading clockwise) from one pad sighting.

    Returns None for an unknown pad or a reading outside the camera's range.
    """
    pad = (load() if pads is None else pads).get(int(pad_id))
    if not pad or not (-150 <= dx <= 150 and -150 <= dy <= 150 and 20 <= dz <= 200):
        return None
    # Pad +y is to the rocket's left; the arena's +y is to the right.
    rad = math.radians(pad["yaw"])
    x = pad["x"] + dx * math.cos(rad) + dy * math.sin(rad)
    y = pad["y"] + dx * math.sin(rad) - dy * math.cos(rad)
    heading = (pad["yaw"] - yaw) % 360
    return (x, y, float(dz), heading)


def pad_on_map(drone_x: float, drone_y: float, heading: float, dx: int, dy: int, yaw: int) -> dict:
    """Where the pad sits, given the drone's map pose and one sighting of that pad.

    This is the inverse of world_pose. heading is clockwise, yaw is the nose
    measured counterclockwise from the rocket.
    """
    pad_yaw = (heading + yaw) % 360
    rad = math.radians(pad_yaw)
    return {"x": round(drone_x - dx * math.cos(rad) - dy * math.sin(rad)),
            "y": round(drone_y - dx * math.sin(rad) + dy * math.cos(rad)),
            "yaw": round(pad_yaw) % 360}


def field_box(pads: dict | None = None) -> tuple | None:
    """(x min, x max, y min, y max) around every saved pad, or None if there are none."""
    pads = load() if pads is None else pads
    if not pads:
        return None
    xs = [pad["x"] for pad in pads.values()]
    ys = [pad["y"] for pad in pads.values()]
    return (min(xs) - FIELD_MARGIN, max(xs) + FIELD_MARGIN,
            min(ys) - FIELD_MARGIN, max(ys) + FIELD_MARGIN)
