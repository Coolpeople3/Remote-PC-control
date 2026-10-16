"""Receiver: run on the PC you want to control.   Just run:  python receiver.py
Installs what it needs, announces itself on the network, streams the screen, obeys mouse commands."""
import hmac, importlib, io, json, os, secrets, socket, subprocess, sys, threading, time
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


ensure("mss", "PIL:pillow", "pynput")

if sys.platform == "win32":  # make screenshot and cursor coordinates line up on scaled displays
    import ctypes
    ctypes.windll.user32.SetProcessDPIAware()

import mss
from PIL import Image
from pynput.mouse import Button, Controller

PORT, DISCOVERY_PORT = 5555, 5556
WIDTH, QUALITY, FPS = 1280, 60, 20  # streamed width (px), JPEG quality, max frames/sec
BUTTONS = {"left": Button.left, "right": Button.right, "middle": Button.middle}
mouse = Controller()

# pairing PIN: created once, reused on later runs
PIN_FILE = Path.home() / ".remote_cursor_pin"
if not PIN_FILE.exists():
    PIN_FILE.write_text(f"{secrets.randbelow(10**6):06d}")
PIN = PIN_FILE.read_text().strip().encode()


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # no packet is sent; just picks the LAN interface
        return s.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())
    finally:
        s.close()


def open_firewall():
    """Best effort on Windows (needs admin; silently skipped otherwise)."""
    if sys.platform != "win32":
        return
    for proto, port in (("TCP", PORT), ("UDP", DISCOVERY_PORT)):
        name = f"RemoteCursor-{proto}"
        subprocess.run(f'netsh advfirewall firewall delete rule name="{name}"', shell=True, capture_output=True)
        subprocess.run(f'netsh advfirewall firewall add rule name="{name}" dir=in action=allow '
                       f'protocol={proto} localport={port}', shell=True, capture_output=True)


def recv_exact(s, n):
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return buf


def announce():
    """Answer discovery broadcasts so the controller can find this PC automatically."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", DISCOVERY_PORT))
    while True:
        data, addr = s.recvfrom(256)
        if data == b"RC_DISCOVER":
            s.sendto(b"RC_HERE|" + socket.gethostname().encode(), addr)


def stream(conn, mon):
    """Capture the screen, compress to JPEG, send as [4-byte length][data]."""
    last = None
    try:
        with mss.mss() as sct:
            while True:
                start = time.time()
                shot = sct.grab(mon)
                img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                img = img.resize((WIDTH, shot.height * WIDTH // shot.width))
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=QUALITY)
                data = buf.getvalue()
                if data != last:  # skip unchanged frames
                    conn.sendall(len(data).to_bytes(4, "big") + data)
                    last = data
                time.sleep(max(0, 1 / FPS - (time.time() - start)))
    except OSError:
        pass  # client disconnected


def serve():
    with mss.mss() as sct:
        mon = sct.monitors[1]  # primary monitor
    open_firewall()
    threading.Thread(target=announce, daemon=True).start()
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PORT))
    srv.listen(1)
    print(f"\n  Receiver ready: {socket.gethostname()} ({local_ip()})")
    print(f"  Pairing PIN:    {PIN.decode()}   <- enter this on the controller the first time")
    print("  (Ctrl+C to quit)\n")
    while True:
        conn, addr = srv.accept()
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            # challenge-response login: the PIN itself is never sent
            conn.settimeout(5)
            nonce = os.urandom(16)
            conn.sendall(nonce)
            ok = hmac.compare_digest(recv_exact(conn, 32), hmac.new(PIN, nonce, "sha256").digest())
            conn.sendall(b"\x01" if ok else b"\x00")
            if not ok:
                print("Rejected (wrong PIN):", addr[0])
                continue
            conn.settimeout(None)
            print("Connected:", addr[0])
            threading.Thread(target=stream, args=(conn, mon), daemon=True).start()
            for line in conn.makefile("r"):  # one JSON command per line
                m = json.loads(line)
                if m["t"] == "move":  # x, y are 0..1 fractions of the screen
                    mouse.position = (int(mon["left"] + m["x"] * mon["width"]),
                                      int(mon["top"] + m["y"] * mon["height"]))
                elif m["t"] == "btn":
                    (mouse.press if m["down"] else mouse.release)(BUTTONS[m["b"]])
                elif m["t"] == "scroll":
                    mouse.scroll(0, m["dy"])
        except (OSError, ValueError, KeyError) as e:
            print("Session ended:", e)
        finally:
            conn.close()


if __name__ == "__main__":
    serve()
