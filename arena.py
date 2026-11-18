"""Validated shared arena coordinates in centimetres. No guessed default layout."""

import hashlib
import json
import math
import os
from pathlib import Path

import tello_net

FILE = tello_net.DATA_DIR / "arena.json"


# The pad sets a field can use. Positions are always measured by the scan; only
# the numbering pattern and which pads the two drones start on are fixed here.
#   pads8:   1 2 3 / 4 5 6 / 7 8   (far row first), A starts on pad 4 and B on pad 6
#   square4: 1 2 / 3 4             (far row first), A starts on pad 3 and B on pad 4
SETS = {"pads8": {"ids": tuple("12345678"), "starts": {"A": "4", "B": "6"}},
        "square4": {"ids": tuple("1234"), "starts": {"A": "3", "B": "4"}}}


def pad_set(ids) -> str | None:
    """Which pad set these pad numbers form ('pads8' or 'square4'), or None."""
    keys = {str(k) for k in ids}
    return next((name for name, spec in SETS.items() if keys == set(spec["ids"])), None)


def start_pads(layout: dict) -> dict[str, str]:
    """{'A': pad id, 'B': pad id} the two drones take off from, for a layout with pads."""
    kind = pad_set(layout["pads"])
    if kind is None:
        raise ValueError("パッドの組み合わせが不正です")
    return dict(SETS[kind]["starts"])


def _number(value, label: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label}を数字で入れてください")
    if not low <= value <= high:
        raise ValueError(f"{label}は{low:g}〜{high:g}cmにしてください")
    return round(float(value), 1)


def simple(value: dict) -> dict:
    """No mission pads: A and B start 'distance' apart facing each other; positions are predicted."""
    distance = _number(value.get("distance"), "2台の距離", 100, 500)
    back = _number(value.get("back"), "後ろの余裕", 50, 300)
    side = _number(value.get("side"), "左右の余裕", 50, 300)
    result = {"kind": "simple", "distance": distance, "back": back, "side": side, "pads": {},
              "field": [-back, distance + back, -side, side],
              "starts": {"A": [0.0, 0.0, 0], "B": [distance, 0.0, 180]}}
    scanned_at = value.get("scanned_at")
    if isinstance(scanned_at, (int, float)) and not isinstance(scanned_at, bool) and math.isfinite(scanned_at):
        result["scanned_at"] = round(float(scanned_at), 3)
    return result


def normalize(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("フィールド設定がありません")
    if value.get("kind") == "simple":
        return simple(value)
    if value.get("preset") is not None:
        if value.get("preset") != "rows8":
            raise ValueError("配置の種類が不正です")
        spacing = value.get("spacing")
        if isinstance(spacing, bool) or not isinstance(spacing, (int, float)) or not math.isfinite(spacing) or not 50 <= spacing <= 120:
            raise ValueError("パッド間隔は50〜120cmにしてください")
        spacing = round(float(spacing), 1)
        s = spacing
        # Seen from above: 1 2 3 / 4 5 6 / 7 8.
        # X follows the rocket arrows (A -> B); Y points across the rows.
        value = {"pads": {"1": [-s, -s], "2": [0, -s], "3": [s, -s],
                          "4": [-s, 0], "5": [0, 0], "6": [s, 0],
                          "7": [-s/2, s], "8": [s/2, s]},
                 "field": [-2*s, 2*s, -2*s, 2*s],
                 "starts": {"A": [-s, 0, 0], "B": [s, 0, 180]},
                 "preset": "rows8", "spacing": spacing}

    def point(raw, label, length=2):
        if not isinstance(raw, (list, tuple)) or len(raw) != length:
            raise ValueError(f"{label}の座標を入力してください")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
               or abs(v) > 2000 for v in raw):
            raise ValueError(f"{label}の座標が不正です")
        return [round(float(v), 1) for v in raw]

    raw_pads = value.get("pads")
    kind = pad_set(raw_pads) if isinstance(raw_pads, dict) else None
    if kind is None:
        raise ValueError("パッドは1〜8の8枚、または1〜4の4枚をすべて登録してください")
    ids = SETS[kind]["ids"]
    pads = {k: point(raw_pads[k], f"パッド{k}") for k in ids}
    for n, a in enumerate(ids):
        for b in ids[n + 1:]:
            if math.dist(pads[a], pads[b]) < 20:
                raise ValueError(f"パッド{a}と{b}が近すぎます")
    # Distances may vary, but the numbered rows and left-to-right order must stay intact.
    if kind == "pads8":  # 1 2 3 / 4 5 6 / 7 8
        if not (pads["1"][0] < pads["2"][0] < pads["3"][0]
                and pads["4"][0] < pads["5"][0] < pads["6"][0]
                and pads["7"][0] < pads["8"][0]):
            raise ValueError("パッド番号の左右の並びを確認してください")
        if not (max(pads[i][1] for i in "123") + 15 < min(pads[i][1] for i in "456")
                and max(pads[i][1] for i in "456") + 15 < min(pads[i][1] for i in "78")):
            raise ValueError("パッド番号の奥から手前の並びを確認してください")
    else:  # square4: 1 2 / 3 4
        if not (pads["1"][0] < pads["2"][0] and pads["3"][0] < pads["4"][0]):
            raise ValueError("パッド番号の左右の並びを確認してください")
        if not max(pads[i][1] for i in "12") + 15 < min(pads[i][1] for i in "34"):
            raise ValueError("パッド番号の奥から手前の並びを確認してください")

    field = point(value.get("field"), "飛行範囲", 4)
    x0, x1, y0, y1 = field
    if x1 - x0 < 100 or y1 - y0 < 100 or x1 - x0 > 1000 or y1 - y0 > 1000:
        raise ValueError("飛行範囲の幅は100〜1000cmにしてください")
    for i, (x, y) in pads.items():
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            raise ValueError(f"パッド{i}が飛行範囲の外です")

    raw_starts = value.get("starts")
    if not isinstance(raw_starts, dict) or set(raw_starts) != {"A", "B"}:
        raise ValueError("A・Bの開始位置を入力してください")
    starts = {side: point(raw_starts[side], f"{side}の開始位置", 3) for side in ("A", "B")}
    if starts["A"][2] != 0 or starts["B"][2] != 180:
        raise ValueError("Aはロケット向き0°、Bは反対向き180°にしてください")
    if starts["B"][0] - starts["A"][0] < 100:
        raise ValueError("AとBの開始位置は前後100cm以上離してください")
    for side, (x, y, _) in starts.items():
        if not (x0 + 20 <= x <= x1 - 20 and y0 + 20 <= y <= y1 - 20):
            raise ValueError(f"{side}の開始位置は飛行範囲の端から20cm以上離してください")
    for side, pad in SETS[kind]["starts"].items():
        if math.dist(starts[side][:2], pads[pad]) > 10:
            raise ValueError(f"{side}の開始位置はパッド{pad}の中心にしてください")
    result = {"pads": pads, "field": field, "starts": starts}
    scanned_at = value.get("scanned_at")
    if isinstance(scanned_at, (int, float)) and not isinstance(scanned_at, bool) and math.isfinite(scanned_at):
        result["scanned_at"] = round(float(scanned_at), 3)
    if value.get("preset") == "rows8":
        result.update(preset="rows8", spacing=value["spacing"])
    return result


def fingerprint(value: dict | None) -> str | None:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16] if value else None


def load() -> dict | None:
    try:
        return normalize(json.loads(FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None


def save(value: dict) -> dict:
    result = normalize(value)
    FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = FILE.with_name(FILE.name + ".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, FILE)
    return result
