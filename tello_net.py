"""Find Tellos and other players on the local network, so nobody types an IP."""

import hashlib
import ipaddress
import json
import os
import re
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path

TELLO_PORT = 8889
DIRECT_IP = "192.168.10.1"  # every Tello's address on its own TELLO-xxxx Wi-Fi
PEER_PORT = 47474
# TELLO_CONNECT_DATA lets two copies of the app run side by side (for testing).
DATA_DIR = Path(os.environ.get("TELLO_CONNECT_DATA", Path(__file__).parent))
NAMES_FILE = DATA_DIR / "drones.json"
SETTINGS_FILE = DATA_DIR / "settings.json"


CLOCK_SKEW = float(os.environ.get("CLOCK_SKEW", 0))  # testing only: pretend this PC's clock is off


def now() -> float:
    """Wall clock used for battle times (start, takeoff, fall)."""
    return time.time() + CLOCK_SKEW


def local_ip() -> str | None:
    """This PC's address on the current network (no packet is sent)."""
    # A Tello's own Wi-Fi has no default route, so ask the way to the Tello first.
    for target in (DIRECT_IP, "10.255.255.255"):
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect((target, 1))
            return probe.getsockname()[0]
        except OSError:
            pass
        finally:
            probe.close()
    try:
        ip = subprocess.run(["ipconfig", "getifaddr", "en0"], capture_output=True,
                            text=True, timeout=2).stdout.strip()
        return ip or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def network_mode(ip: str | None) -> str:
    if ip is None:
        return "offline"
    return "direct" if ip.startswith("192.168.10.") else "router"


def tello_socket(timeout: float) -> socket.socket:
    """UDP socket on local port 8889, like the official Tello samples.

    A Tello may keep answering the port that first sent it 'command', so
    always using 8889 keeps the replies coming to us. Falls back to any port
    while another program (e.g. tello_test2.py) holds 8889.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("", TELLO_PORT))
    except OSError:
        sock.bind(("", 0))
    sock.settimeout(timeout)
    return sock


def talk(ip: str, commands: list[str], timeout: float = 5,
         port: int = TELLO_PORT) -> list[str | None]:
    """Send SDK commands from one socket and return each reply (None on timeout).

    A Tello answers only the address that last sent it 'command', so the
    queries must come from the same socket; 'command' is sent first.
    """
    sock = tello_socket(timeout)
    replies: list[str | None] = []
    try:
        for command in ["command", *commands]:
            sock.sendto(command.encode(), (ip, port))
            reply = None
            try:
                while reply is None:
                    data, source = sock.recvfrom(1024)
                    if source[0] == ip:
                        reply = data.decode("utf-8", "replace").strip()
            except OSError:
                pass
            replies.append(reply)
            if command == "command" and reply != "ok":
                return [None] * len(commands)
    finally:
        sock.close()
    return replies[1:]


def tello_ssid(ip: str) -> str | None:
    """A Tello's own Wi-Fi name is TELLO- plus the last 3 bytes of its MAC."""
    try:
        out = subprocess.run(["arp", "-n", ip], capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"at ((?:[0-9a-f]{1,2}:){5}[0-9a-f]{1,2})", out, re.IGNORECASE)
    if not match:
        return None
    last3 = match.group(1).split(":")[-3:]
    return "TELLO-" + "".join(part.zfill(2) for part in last3).upper()


def scan(hosts: list[str] | None = None, wait: float = 2.5, port: int = TELLO_PORT) -> list[dict]:
    """Ask every address on this /24 network for 'command'; Tellos answer 'ok'."""
    if hosts is None:
        me = local_ip()
        # Unknown network: still try the Tello's fixed address.
        if me is None or network_mode(me) == "direct":
            hosts = [DIRECT_IP]
        else:
            network = ipaddress.ip_network(f"{me}/24", strict=False)
            hosts = [str(h) for h in network.hosts() if str(h) != me]
    sock = tello_socket(0.1)
    found: set[str] = set()
    deadline = time.monotonic() + wait
    resend_at = time.monotonic() + wait / 3
    try:
        for round_ in range(2):
            # The first packet to a new address can be lost while ARP resolves.
            for host in hosts:
                if host not in found:
                    try:
                        sock.sendto(b"command", (host, port))
                    except OSError:
                        pass
            until = resend_at if round_ == 0 else deadline
            while time.monotonic() < until:
                try:
                    reply, source = sock.recvfrom(1024)
                except socket.timeout:
                    continue
                if reply.strip().lower() == b"ok":
                    found.add(source[0])
    finally:
        sock.close()
    drones = []
    for ip in sorted(found, key=ipaddress.ip_address):
        serial, battery = talk(ip, ["sn?", "battery?"], 2, port)
        drones.append({"ip": ip, "sn": serial or ip, "battery": battery, "ssid": tello_ssid(ip)})
    return drones


def is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


def room_id(code: str | None) -> str | None:
    """Players with the same 4-digit code meet; only a hash goes on the network."""
    if not code or not re.fullmatch(r"\d{4}", code):
        return None
    return hashlib.sha256(f"tello_connect:{code}".encode()).hexdigest()[:16]


def load_settings() -> dict:
    """This player's name and chosen drone (by serial number)."""
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(**changes) -> None:
    settings = {**load_settings(), **changes}
    SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def load_names() -> dict:
    try:
        return json.loads(NAMES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_name(serial: str, name: str) -> None:
    names = load_names()
    if name:
        names[serial] = name
    else:
        names.pop(serial, None)
    # Names belong to serial numbers; drop ones saved under an IP by mistake.
    names = {key: value for key, value in names.items() if value and not is_ip(key)}
    NAMES_FILE.write_text(json.dumps(names, ensure_ascii=False, indent=2), encoding="utf-8")


class PeerFinder:
    """Announce this app on the LAN and collect other players' announcements."""

    def __init__(self, info, port: int = PEER_PORT) -> None:
        self.info = info  # callable returning this player's public details
        self.port = port
        self.instance = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.peers: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.bind(("", port))
        self.sock.settimeout(0.5)
        threading.Thread(target=self._listen, daemon=True).start()
        threading.Thread(target=self._announce, daemon=True).start()

    def send(self, message: dict, ip: str = "255.255.255.255") -> None:
        # 'sent' lets the receiver work out how far apart the two clocks are.
        message = {**message, "app": "tello_connect", "instance": self.instance, "sent": now()}
        try:
            self.sock.sendto(json.dumps(message).encode(), (ip, self.port))
        except OSError:
            pass

    def announce(self) -> None:
        self.send({"type": "hello", **self.info()})

    def _announce(self) -> None:
        while True:
            self.announce()
            time.sleep(2)

    def _listen(self) -> None:
        while True:
            try:
                data, source = self.sock.recvfrom(4096)
                message = json.loads(data)
            except (socket.timeout, ValueError):
                continue
            except OSError:
                return
            if message.get("app") != "tello_connect" or message.get("instance") == self.instance:
                continue
            with self.lock:
                received = now()
                # Their clock minus ours (LAN delay is a few ms, so it is ignored).
                offset = message.get("sent", received) - received
                self.peers[message["instance"]] = {**message, "ip": source[0], "seen": time.time(),
                                                   "offset": offset}

    def current(self) -> list[dict]:
        with self.lock:
            return [p for p in self.peers.values() if time.time() - p["seen"] < 7]
