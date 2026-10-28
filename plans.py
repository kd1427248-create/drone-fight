"""Player-made battle files: check each line, measure how far it travels, and store it."""

import math
import re
from pathlib import Path

PLAN_DIR = Path(__file__).resolve().parent / "plans"
REPEAT = "repeat"  # a line of its own: loop the file until the battle ends
MAX_LOOP_DRIFT = 50  # cm a repeating file may end away from where it started

MOVES = {"forward": (1, 0), "back": (-1, 0), "right": (0, 1), "left": (0, -1)}
HELP = [
    ("forward 100", "前へ（20〜500cm）"), ("back 100", "後ろへ"), ("left 100", "左へ"),
    ("right 100", "右へ"), ("up 50", "上へ"), ("down 50", "下へ"),
    ("cw 90", "右回り（1〜360度）"), ("ccw 90", "左回り"),
    ("go 100 50 0 60", "前・左・上 の方向へ斜めに（cm。左はマイナスで右、最後は速さ）"),
    ("speed 60", "速さ（10〜100cm/秒）"), ("flip l", "宙返り l/r/f/b（電池50%以上）"),
    ("delay 1", "待つ（0.1〜10秒）"), ("match_height", "相手の高さに合わせる"),
    ("land", "着陸（負けになります）"), (REPEAT, "最後まで行ったらくり返す"),
]


def _number(word: str, low: float, high: float, integer: bool = True) -> float:
    value = int(word) if integer else float(word)
    if not low <= value <= high:
        raise ValueError
    return value


def check(text: str) -> dict:
    """Validate a battle file; also report where it ends up and how far it goes."""
    errors, commands = [], []
    x = y = z = 0.0  # cm in the starting frame: x forward, y right, z up
    heading = 0  # degrees clockwise from the starting direction
    farthest = 0.0
    repeat = False
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        words = line.split()
        name, args = words[0].lower(), words[1:]
        try:
            if line == REPEAT:
                repeat = True
                continue
            if name in MOVES and len(args) == 1:
                distance = _number(args[0], 20, 500)
                ahead, side = MOVES[name]
                rad = math.radians(heading)
                x += distance * (ahead * math.cos(rad) - side * math.sin(rad))
                y += distance * (ahead * math.sin(rad) + side * math.cos(rad))
            elif name in ("up", "down") and len(args) == 1:
                z += _number(args[0], 20, 500) * (1 if name == "up" else -1)
            elif name in ("cw", "ccw") and len(args) == 1:
                heading = (heading + _number(args[0], 1, 360) * (1 if name == "cw" else -1)) % 360
            elif name == "go" and len(args) == 4:
                gx, gy, gz = (_number(a, -500, 500) for a in args[:3])
                _number(args[3], 10, 100)
                if all(-20 < v < 20 for v in (gx, gy, gz)):
                    raise ValueError
                # SDK 'go': x forward, y LEFT, z up, relative to the current heading.
                rad = math.radians(heading)
                x += gx * math.cos(rad) + gy * math.sin(rad)
                y += gx * math.sin(rad) - gy * math.cos(rad)
                z += gz
            elif name == "speed" and len(args) == 1:
                _number(args[0], 10, 100)
            elif name == "flip" and len(args) == 1 and args[0] in ("l", "r", "f", "b"):
                pass
            elif name == "delay" and len(args) == 1:
                _number(args[0], 0.1, 10, integer=False)
            elif line in ("match_height", "land"):
                pass
            else:
                raise ValueError
        except ValueError:
            message = ("command と takeoff はアプリが自動で行うので書かなくて大丈夫です"
                       if name in ("command", "takeoff") else "使えない命令か、数字が範囲外です")
            errors.append({"line": number, "text": raw, "message": message})
            continue
        commands.append(line)
        farthest = max(farthest, math.hypot(x, y))
    drift = math.hypot(x, y)
    if not commands and not errors:
        errors.append({"line": 0, "text": "", "message": "命令が1つもありません"})
    if repeat and drift > MAX_LOOP_DRIFT:
        errors.append({"line": 0, "text": "", "message":
                       f"くり返すファイルは、1周で元の位置から{MAX_LOOP_DRIFT}cm以内に戻る必要があります"
                       f"（今は{drift:.0f}cmずれます）"})
    return {"ok": not errors, "errors": errors, "commands": len(commands), "repeat": repeat,
            "end": {"forward": round(x), "right": round(y), "up": round(z), "heading": heading},
            "drift": round(drift), "farthest": round(farthest)}


def safe_name(name: str) -> str | None:
    """File name from what the player typed: no folders, at most 30 characters, .txt."""
    name = re.sub(r"[\\\\/:*?\"<>|\x00-\x1f]", "", name).strip().removesuffix(".txt").strip(". ")
    if not name or len(name) > 30:
        return None
    return name + ".txt"


def list_plans() -> list[dict]:
    PLAN_DIR.mkdir(exist_ok=True)
    found = []
    for path in sorted(PLAN_DIR.glob("*.txt")):
        result = check(path.read_text(encoding="utf-8"))
        found.append({"name": path.name, "ok": result["ok"], "repeat": result["repeat"]})
    return found


def read_plan(name: str) -> str | None:
    if name not in {p["name"] for p in list_plans()}:
        return None
    return (PLAN_DIR / name).read_text(encoding="utf-8")


def save_plan(name: str, text: str) -> dict:
    filename = safe_name(name)
    if filename is None:
        return {"ok": False, "errors": [{"line": 0, "text": "", "message": "ファイル名は1〜30文字で入れてください"}]}
    result = check(text)
    if result["ok"]:
        PLAN_DIR.mkdir(exist_ok=True)
        (PLAN_DIR / filename).write_text(text.rstrip() + "\n", encoding="utf-8")
    return {**result, "name": filename}


def delete_plan(name: str) -> bool:
    if name not in {p["name"] for p in list_plans()}:
        return False
    (PLAN_DIR / name).unlink()
    return True
