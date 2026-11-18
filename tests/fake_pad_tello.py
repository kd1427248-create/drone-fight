"""Fake Tello EDU with sloppy movement and mission pads, for testing the pad map.

argv: port state_port serial start_x start_y start_heading true_pose_file [pads=1|0]
"""
import json, math, random, socket, sys, threading, time
port, state_port, serial = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
x, y, heading = float(sys.argv[4]), float(sys.argv[5]), float(sys.argv[6])
pose_file = sys.argv[7]
pads_mode = sys.argv[8] if len(sys.argv) > 8 else "1"
s = 60
PADS = {1: (-s, -s/2), 2: (0, -3*s/2), 3: (s, -s/2),
        4: (-2*s, s/2), 5: (0, s/2), 6: (2*s, s/2),
        7: (-s, 3*s/2), 8: (s, 3*s/2)}
if len(sys.argv) > 9:
    PADS = {int(k): tuple(v) for k, v in json.load(open(sys.argv[9]))["pads"].items()}
z = 0.0; flying = False; mon = False; speed = 100; takeoff_time = 0.0
import os
# How sloppy each move is: distance off by up to MOVE_ERROR, direction by up to ANGLE_ERROR degrees.
MOVE_ERROR = float(os.environ.get("FAKE_MOVE_ERROR", 0.2))
ANGLE_ERROR = float(os.environ.get("FAKE_ANGLE_ERROR", 10))
YAW_DRIFT = float(os.environ.get("FAKE_YAW_DRIFT", 0))  # degrees the body turns by itself per move
rng = random.Random(port)
srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); srv.bind(("127.0.0.1", port))

def save():
    with open(pose_file + ".tmp", "w") as file:
        json.dump({"x": x, "y": y, "z": z, "heading": heading, "flying": flying}, file)
    import os
    os.replace(pose_file + ".tmp", pose_file)

def move_body(ahead, right, up, duration):
    """Like a real drone: the distance is off by up to 20 % and the direction by up to 10 degrees."""
    global x, y, z
    global heading
    k = rng.uniform(1 - MOVE_ERROR, 1 + MOVE_ERROR); err = math.radians(heading + rng.uniform(-ANGLE_ERROR, ANGLE_ERROR))
    heading += YAW_DRIFT
    x0, y0, z0 = x, y, z
    dx = k * (ahead * math.cos(err) - right * math.sin(err))
    dy = k * (ahead * math.sin(err) + right * math.cos(err))
    end = time.monotonic() + duration
    while True:
        t = min(1.0, 1.0 - (end - time.monotonic()) / duration)
        x, y, z = x0 + dx * t, y0 + dy * t, max(20, z0 + up * t)
        if t >= 1.0:
            break
        time.sleep(0.02)

def stream():
    st = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    while True:
        since_takeoff = time.monotonic() - takeoff_time
        if flying and pads_mode == "state-drop" and since_takeoff > 2:
            time.sleep(0.05)
            continue
        mid, px, py, mpry = -1, 0, 0, "0,0,0"
        use_pads = pads_mode in ("1", "state-drop") or (pads_mode == "drop" and since_takeoff < 2)
        if flying and mon and use_pads and 30 <= z <= 120:
            k, (cx, cy) = min(PADS.items(), key=lambda kv: math.hypot(x - kv[1][0], y - kv[1][1]))
            # The downward camera sees about ±0.42 x height around the drone.
            if math.hypot(x - cx, y - cy) <= 0.42 * z:
                mid, px, py = k, round(x - cx), round(-(y - cy))  # pad y points left
                mpry = f"0,0,{round((heading + 180) % 360 - 180)}"
        h = round(z) if flying else 0
        st.sendto(f"mid:{mid};x:{px};y:{py};z:{h};mpry:{mpry};h:{h};tof:{h + 5};bat:77;".encode(), ("127.0.0.1", state_port))
        save(); time.sleep(0.05)
threading.Thread(target=stream, daemon=True).start()

while True:
    d, peer = srv.recvfrom(1024); c = d.decode(); w = c.split()
    r = "ok"
    if w and w[0] == "SIMSTART":  # test harness: where this drone really starts
        x, y, heading = float(w[1]), float(w[2]), float(w[3]); r = "ok"
    elif c == "sn?": r = serial
    elif c == "battery?": r = "77"
    elif c == "mon": mon = True
    elif c == "takeoff": time.sleep(1); flying = True; z = 80; takeoff_time = time.monotonic()
    elif c in ("land", "emergency"): flying = False; z = 0
    elif w[0] == "speed": speed = int(w[1])
    elif w[0] in ("forward", "back", "left", "right", "up", "down") and flying:
        n = int(w[1]); duration = n / speed + 0.5
        move_body(*{"forward": (n, 0, 0), "back": (-n, 0, 0), "right": (0, n, 0), "left": (0, -n, 0),
                    "up": (0, 0, n), "down": (0, 0, -n)}[w[0]], duration)
    elif w[0] in ("cw", "ccw") and flying:
        n = int(w[1]); time.sleep(n / 90); heading += (n if w[0] == "cw" else -n) + rng.uniform(-5, 5)
    elif w[0] == "go" and flying:
        gx, gy, gz, sp = (int(v) for v in w[1:5]); duration = math.sqrt(gx*gx + gy*gy + gz*gz) / sp + 0.5
        move_body(gx, -gy, gz, duration)  # go's y is left
    elif w[0] == "flip" and flying:
        time.sleep(1)
    srv.sendto(r.encode(), peer)
