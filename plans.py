"""Imported battle files: check each line, measure how far one loop travels, and store them.

An imported file is used like the built-in modes: it repeats until the battle ends.
"""

import math
import re
from pathlib import Path

PLAN_DIR = Path(__file__).resolve().parent / "plans"
MAX_LOOP_DRIFT = 50  # cm one loop may end away from where it started
MAX_FILE_BYTES = 20_000

MOVES = {"forward": (1, 0), "back": (-1, 0), "right": (0, 1), "left": (0, -1)}
def _number(word: str, low: float, high: float, integer: bool = True) -> float:
    value = int(word) if integer else float(word)
    if not low <= value <= high:
        raise ValueError
    return value


APP_COMMANDS = ("match_height", "flee_far", "charge", "guard", "dodge", "land")  # handled by this app, no fixed move


def move(pose: tuple, line: str) -> tuple:
    """Where one command takes the drone: pose = (x forward, y right, z up, heading).

    x/y are cm in the starting frame, heading is degrees clockwise from the
    starting direction. Raises ValueError for anything the app does not allow.
    """
    x, y, z, heading = pose
    words = line.split()
    name, args = words[0].lower(), words[1:]
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
    elif line in APP_COMMANDS:
        pass
    else:
        raise ValueError
    return (x, y, z, heading)


def go_command(dx: float, dy: float, dz: float, heading: float, speed: int) -> str | None:
    """'go' that moves by (dx forward, dy right, dz up) in the starting frame, or None if tiny."""
    rad = math.radians(heading)
    ahead = dx * math.cos(rad) + dy * math.sin(rad)
    right = -dx * math.sin(rad) + dy * math.cos(rad)
    parts = [round(max(-500, min(500, v))) for v in (ahead, -right, dz)]  # go's y is LEFT
    if all(-20 < v < 20 for v in parts):
        return None
    return f"go {parts[0]} {parts[1]} {parts[2]} {speed}"


def check(text: str) -> dict:
    """Validate a battle file; also report where it ends up and how far it goes."""
    errors, commands = [], []
    pose = (0.0, 0.0, 0.0, 0)
    farthest = 0.0
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#") or line == "repeat":  # files always repeat now
            continue
        name = line.split()[0].lower()
        try:
            pose = move(pose, line)
        except ValueError:
            message = ("command と takeoff はアプリが自動で行うので書かなくて大丈夫です"
                       if name in ("command", "takeoff") else "使えない命令か、数字が範囲外です")
            errors.append({"line": number, "text": raw, "message": message})
            continue
        commands.append(line)
        farthest = max(farthest, math.hypot(pose[0], pose[1]))
    x, y, z, heading = pose
    drift = math.hypot(x, y)
    if not commands and not errors:
        errors.append({"line": 0, "text": "", "message": "命令が1つもありません"})
    if drift > MAX_LOOP_DRIFT:
        # It repeats until the end, so any drift adds up loop after loop.
        errors.append({"line": 0, "text": "", "message":
                       f"くり返して使うので、最後に元の位置から{MAX_LOOP_DRIFT}cm以内に戻るようにしてください"
                       f"（今は{drift:.0f}cmずれます）"})
    return {"ok": not errors, "errors": errors, "commands": len(commands),
            "end": {"forward": round(x), "right": round(y), "up": round(z), "heading": heading},
            "drift": round(drift), "farthest": round(farthest)}


def safe_name(name: str) -> str | None:
    """File name from what the player typed: no folders, at most 30 characters, .txt."""
    name = re.sub(r"[\\\\/:*?\"<>|\x00-\x1f]", "", name).strip().removesuffix(".txt").strip(". ")
    if not name or len(name) > 30:
        return None
    return name + ".txt"


def describe(text: str, filename: str) -> dict:
    """Card title and description: '# name: description' on the first line, like the modes."""
    first = next((l for l in text.splitlines() if l.strip()), "")
    if first.startswith("#") and ":" in first:
        name, _, description = first.lstrip("# ").partition(":")
        return {"title": name.strip() or filename, "description": description.strip()}
    return {"title": filename.removesuffix(".txt"), "description": "インポートしたファイル"}


def list_plans() -> list[dict]:
    PLAN_DIR.mkdir(exist_ok=True)
    found = []
    for path in sorted(PLAN_DIR.glob("*.txt")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if check(text)["ok"]:
            found.append({"name": path.name, **describe(text, path.name)})
    return found


def import_plan(filename: str, text: str) -> dict:
    """Check an uploaded file and keep it only if it can be flown safely."""
    name = safe_name(filename)
    if name is None:
        return {"ok": False, "errors": [{"line": 0, "text": "", "message": "ファイル名が使えません"}]}
    if len(text.encode("utf-8")) > MAX_FILE_BYTES:
        return {"ok": False, "errors": [{"line": 0, "text": "", "message": "ファイルが大きすぎます"}]}
    result = check(text)
    if result["ok"]:
        PLAN_DIR.mkdir(exist_ok=True)
        (PLAN_DIR / name).write_text(text.rstrip() + "\n", encoding="utf-8")
    return {**result, "name": name}


def delete_plan(name: str) -> bool:
    if name not in {p["name"] for p in list_plans()}:
        return False
    (PLAN_DIR / name).unlink()
    return True
