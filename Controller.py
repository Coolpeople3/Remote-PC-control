"""Controller: run on YOUR PC.   Just run:  python controller.py
Finds the receiver on your network, shows its screen; your mouse over the window controls its cursor.
(Optional: python controller.py <receiver-ip> to skip the search.)"""
import hmac, importlib, io, json, socket, subprocess, sys, threading
from pathlib import Path


def ensure(*pkgs):
    """pip-install any missing packages. Format: 'import_name' or 'import_name:pip_name'."""
    for spec in pkgs:
        mod, _, pip_name = spec.partition(":")
        try:
            __import__(mod)
        except ImportError:
            print(f"Installing {pip_name or mod} ...")
            cmd = [sys.executable, "-m", "pip", "install", "-q", pip_name or mod]
            if subprocess.call(cmd) != 0:  # e.g. "externally managed" Linux Python
                subprocess.check_call(cmd + ["--break-system-packages"])
            importlib.invalidate_caches()


ensure("pygame")
import pygame

PORT, DISCOVERY_PORT = 5555, 5556
BTN = {1: "left", 2: "middle", 3: "right"}
PIN_STORE = Path.home() / ".remote_cursor_pins.json"  # remembers the PIN per receiver


def recv_exact(s, n):
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return buf


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())
    finally:
        s.close()


def discover(rounds=6):
    """Broadcast on the LAN and collect {ip: hostname} from any receivers that answer."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(0.5)
    targets = ["255.255.255.255", local_ip().rsplit(".", 1)[0] + ".255"]
    found = {}
    for _ in range(rounds):
        for t in targets:
            try:
                s.sendto(b"RC_DISCOVER", (t, DISCOVERY_PORT))
            except OSError:
                pass
        try:
            while True:
                data, (ip, _) = s.recvfrom(256)
                if data.startswith(b"RC_HERE|"):
                    found[ip] = data[8:].decode(errors="replace")
        except socket.timeout:
            pass
        if found:
            break
    return found


def pick_receiver():
    print("Looking for a receiver on your network ...")
    found = discover()
    if not found:
        return input("None found. Enter the receiver's IP manually: ").strip()
    ips = list(found)
    if len(ips) == 1:
        print(f"Found {found[ips[0]]} ({ips[0]})")
        return ips[0]
    for i, ip in enumerate(ips, 1):
        print(f"  {i}. {found[ip]} ({ip})")
    return ips[int(input("Choose a number: ")) - 1]


def connect(ip):
    """Connect and log in with the saved PIN (asks for it only the first time)."""
    pins = json.loads(PIN_STORE.read_text()) if PIN_STORE.exists() else {}
    for _ in range(3):
        pin = pins.get(ip) or input(f"Enter the PIN shown on the receiver ({ip}): ").strip()
        sock = socket.create_connection((ip, PORT), timeout=5)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.sendall(hmac.new(pin.encode(), recv_exact(sock, 16), "sha256").digest())
        if recv_exact(sock, 1) == b"\x01":
            pins[ip] = pin
            PIN_STORE.write_text(json.dumps(pins))
            sock.settimeout(None)
            return sock
        print("Wrong PIN.")
        pins.pop(ip, None)
        sock.close()
    sys.exit("Too many wrong PINs.")


HOST = sys.argv[1] if len(sys.argv) > 1 else pick_receiver()
try:
    sock = connect(HOST)
except OSError as e:
    sys.exit(f"Couldn't connect to {HOST}: {e}\nIs receiver.py running there, and is port {PORT} allowed by its firewall?")

# background thread keeps only the newest frame
state = {}


def receive():
    try:
        while True:
            state["frame"] = recv_exact(sock, int.from_bytes(recv_exact(sock, 4), "big"))
    except OSError:
        state["closed"] = True


threading.Thread(target=receive, daemon=True).start()

pygame.init()
screen = pygame.display.set_mode((1280, 720), pygame.RESIZABLE)
pygame.display.set_caption(f"Remote screen - {HOST}")
clock = pygame.time.Clock()
frame = scaled = None
offset = (0, 0)


def layout():
    """Scale factor and offset that fit the frame in the window (keeps aspect ratio)."""
    ww, wh = screen.get_size()
    fw, fh = frame.get_size()
    s = min(ww / fw, wh / fh)
    return s, ((ww - fw * s) / 2, (wh - fh * s) / 2)


def to_remote(pos):
    """Window pixel -> (0..1, 0..1) position on the remote screen, or None if outside the image."""
    if not frame:
        return None
    s, (ox, oy) = layout()
    x = (pos[0] - ox) / (frame.get_width() * s)
    y = (pos[1] - oy) / (frame.get_height() * s)
    return (x, y) if 0 <= x <= 1 and 0 <= y <= 1 else None


def send(**msg):
    sock.sendall((json.dumps(msg) + "\n").encode())


running = True
try:
    while running and "closed" not in state:
        data = state.pop("frame", None)
        if data:
            frame = pygame.image.load(io.BytesIO(data), "frame.jpg").convert()
            scaled = None
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                running = False
            elif e.type == pygame.VIDEORESIZE:
                scaled = None
            elif e.type == pygame.MOUSEMOTION and (p := to_remote(e.pos)):
                send(t="move", x=p[0], y=p[1])
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button in BTN and to_remote(e.pos):
                send(t="btn", b=BTN[e.button], down=True)
            elif e.type == pygame.MOUSEBUTTONUP and e.button in BTN:
                send(t="btn", b=BTN[e.button], down=False)
            elif e.type == pygame.MOUSEWHEEL:
                send(t="scroll", dy=e.y)
        if frame and scaled is None:  # rescale only on new frame / resize
            s, offset = layout()
            scaled = pygame.transform.smoothscale(
                frame, (int(frame.get_width() * s), int(frame.get_height() * s)))
        screen.fill((0, 0, 0))
        if scaled:
            screen.blit(scaled, offset)
        pygame.display.flip()
        clock.tick(60)
except OSError:
    pass
finally:
    pygame.quit()
    print("Disconnected.")
