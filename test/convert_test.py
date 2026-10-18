#!/usr/bin/env python3
"""
Run converter.js against a real, fully-rendered page and print the Markdown.

WHY A BROWSER IS REQUIRED: the converter walks the LIVE DOM and asks the
browser which elements are actually visible, so it cannot be tested by parsing
saved HTML -- a static parser has no layout and would report every hidden
mobile menu and inactive carousel slide as visible content.

Self-contained: standard library only, plus any installed Chromium-family
browser. It speaks just enough of the Chrome DevTools Protocol to load a page,
run the converter in it, and read the result back.

  ./convert_test.py https://example.com
  ./convert_test.py https://example.com --article        # main content only
  ./convert_test.py "file://$PWD/test/fixture.html" --out /tmp/out.md
"""
import argparse
import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request

BROWSERS = [
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    "brave-browser", "microsoft-edge", "microsoft-edge-stable", "vivaldi",
]


def find_browser():
    override = os.environ.get("MDCLIP_BROWSER")
    if override:
        return shutil.which(override) or (override if os.path.exists(override) else None)
    for name in BROWSERS:
        found = shutil.which(name)
        if found:
            return found
    return None


class WS:
    """
    Minimal RFC 6455 client -- enough for CDP, no dependencies.

    It handles 64-bit payload lengths and fragmented frames deliberately: a
    converted page comes back as one large JSON string and WILL arrive split
    across continuation frames. An implementation that reads a single frame
    looks fine on small pages and silently truncates big ones.
    """

    def __init__(self, url, timeout=120):
        rest = url.split("://", 1)[1]
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        self.sock = socket.create_connection((host, int(port or 80)), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((
            "GET /%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n"
            "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n" % (path, hostport, key)
        ).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("websocket handshake failed")
            buf += chunk
        if b"101" not in buf.split(b"\r\n", 1)[0]:
            raise RuntimeError("websocket upgrade refused")
        self._rest = buf.split(b"\r\n\r\n", 1)[1]

    def _exact(self, n):
        out, self._rest = self._rest[:n], self._rest[n:]
        while len(out) < n:
            chunk = self.sock.recv(min(1 << 20, n - len(out)))
            if not chunk:
                raise RuntimeError("websocket closed mid-frame")
            out += chunk
        return out

    def send(self, payload):
        data = payload.encode()
        header = bytearray([0x81])
        mask = os.urandom(4)
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < (1 << 16):
            header.append(0x80 | 126); header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127); header += struct.pack(">Q", n)
        header += mask
        self.sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        message = b""
        while True:
            b0, b1 = self._exact(2)
            fin, opcode, length = b0 & 0x80, b0 & 0x0F, b1 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._exact(8))[0]
            payload = self._exact(length) if length else b""
            if opcode == 0x8:
                raise RuntimeError("websocket closed by browser")
            if opcode == 0x9:
                self.sock.sendall(b"\x8a\x80" + os.urandom(4)); continue
            if opcode == 0xA:
                continue
            message += payload
            if fin:
                return message.decode("utf-8", "replace")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class CDP:
    def __init__(self, ws_url, timeout=120):
        self.ws = WS(ws_url, timeout=timeout)
        self._id = 0
        self.events = []

    def call(self, method, **params):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError("%s: %s" % (method, msg["error"].get("message")))
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)

    def wait_for(self, method, timeout=60):
        deadline = time.time() + timeout
        for ev in self.events:
            if ev.get("method") == method:
                return ev
        while time.time() < deadline:
            try:
                msg = json.loads(self.ws.recv())
            except (RuntimeError, socket.timeout):
                return None
            if "method" in msg:
                self.events.append(msg)
                if msg["method"] == method:
                    return msg
        return None


def launch(binary, profile_dir):
    """
    Start headless with remote debugging on an ephemeral port.

    Port 0 lets the OS choose and the browser writes the real port into
    DevToolsActivePort; hardcoding one makes two concurrent runs collide.
    """
    args = [
        binary, "--headless=new", "--remote-debugging-port=0",
        "--user-data-dir=" + profile_dir,
        "--no-first-run", "--no-default-browser-check",
        "--disable-background-networking", "--disable-sync", "--disable-extensions",
        "--mute-audio", "--hide-scrollbars", "--disable-gpu",
        # Containers often give /dev/shm only 64MB, which crashes Chromium.
        "--disable-dev-shm-usage",
        "--window-size=1440,900", "about:blank",
    ]
    # Chromium refuses to start as root without this; only added when needed,
    # since it weakens sandboxing.
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        args.insert(1, "--no-sandbox")
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)
    port_file = os.path.join(profile_dir, "DevToolsActivePort")
    deadline = time.time() + 40
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("browser exited during startup")
        if os.path.exists(port_file):
            try:
                with open(port_file) as fh:
                    port = int(fh.readline().strip())
                if port:
                    return proc, port
            except (ValueError, OSError):
                pass
        time.sleep(0.1)
    proc.kill()
    raise RuntimeError("browser did not expose a debugging port in time")


def shutdown(proc):
    """
    Kill the whole process GROUP. On many systems the browser command is a
    shell wrapper that execs the real binary as a child -- terminating only the
    wrapper leaves the browser running, and it then rewrites the profile
    directory after cleanup has deleted it.
    """
    import signal
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, OSError):
        pgid = None
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig) if pgid is not None else proc.send_signal(sig)
        except (ProcessLookupError, OSError):
            return
        try:
            proc.wait(timeout=8)
            return
        except subprocess.TimeoutExpired:
            continue


def main():
    ap = argparse.ArgumentParser(description="Convert a rendered page to Markdown")
    ap.add_argument("url")
    ap.add_argument("--out")
    ap.add_argument("--delay", type=float, default=2.0,
                    help="settle time after scrolling, seconds")
    ap.add_argument("--article", action="store_true",
                    help="reader mode: keep only the main content")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    binary = find_browser()
    if not binary:
        sys.exit("no Chromium-family browser found (set MDCLIP_BROWSER to override)")

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    converter_src = open(os.path.join(here, "converter.js")).read()

    profile = tempfile.mkdtemp(prefix="mdclip-test-")
    proc, port = launch(binary, profile)
    cdp = None
    try:
        targets = json.loads(urllib.request.urlopen(
            "http://127.0.0.1:%d/json/list" % port, timeout=20).read())
        page = next(t for t in targets if t.get("type") == "page")
        cdp = CDP(page["webSocketDebuggerUrl"], timeout=90)
        cdp.call("Page.enable")
        nav = cdp.call("Page.navigate", url=a.url)
        if nav.get("errorText"):
            sys.exit("navigation failed: " + nav["errorText"])
        cdp.wait_for("Page.loadEventFired", timeout=60)

        # Scroll the page first: lazy-loaded sections are not in the DOM until
        # they have been scrolled past, so skipping this silently omits exactly
        # the content a reader cares about.
        cdp.call("Runtime.evaluate", expression="""
            (async () => {
              const step = Math.max(200, window.innerHeight * 0.8);
              for (let y = 0; y < document.body.scrollHeight; y += step) {
                window.scrollTo(0, y);
                await new Promise(r => setTimeout(r, 80));
              }
              window.scrollTo(0, 0);
              await new Promise(r => setTimeout(r, %d));
            })()
        """ % int(a.delay * 1000), awaitPromise=True, returnByValue=True)

        cdp.call("Runtime.evaluate", expression=converter_src, returnByValue=True)
        mode = "article" if a.article else "page"
        res = cdp.call("Runtime.evaluate",
                       expression='window.MDCLIP.convert(document, {mode: "%s"})' % mode,
                       returnByValue=True)
        if "exceptionDetails" in res:
            sys.exit("converter threw: " + json.dumps(res)[:500])
        md = res.get("result", {}).get("value") or ""
    finally:
        if cdp:
            cdp.ws.close()
        shutdown(proc)
        shutil.rmtree(profile, ignore_errors=True)

    if a.out:
        open(a.out, "w").write(md)
        if not a.quiet:
            print("wrote %s (%d chars, ~%d tokens)" % (a.out, len(md), len(md) // 4))
    else:
        print(md)


if __name__ == "__main__":
    main()
