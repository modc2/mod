"""
freewash — a free-roam WebGL game set in Washington Square Park.

Walk or skate around the marble arch, the fountain plaza and the tree-lined
paths of Greenwich Village, rendered entirely in the browser with WebGL
(three.js). The whole game is a single static HTML file in ``web/index.html``.

This module is fully self-contained: it only serves that file over a tiny
stdlib HTTP server and opens it in a browser. It does not import or depend on
any other mod.

Usage:
    import mod as m
    m.mod('freewash')().play()          # serve + open browser

    # or from the shell
    m freewash play
    m freewash serve port=8799 open=False
"""
import os
import threading
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler


def _make_handler(web_dir, mod):
    """Build a request handler that serves the game dir and answers ``mp.json``
    (multiplayer discovery: tells the browser which port the relay is on).

    Defined inside a function (not at module scope) so the mod loader, which
    scans top-level classes, never tries to instantiate a request handler.
    """
    import json as _json

    class _Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=web_dir, **k)

        def log_message(self, *a):
            pass

        def do_GET(self):
            # the gateway forwards /freewash/* — serve those as if from root
            if self.path == "/freewash" or self.path.startswith("/freewash/"):
                self.path = self.path[len("/freewash"):] or "/"
            if self.path.split("?")[0].rstrip("/").endswith("mp.json"):
                host = (self.headers.get("Host", "") or "").split(":")[0]
                body = _json.dumps({"ws_port": mod._relay_port, "host": host}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            return super().do_GET()

    return _Handler

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")


class Mod:
    description = "Free-roam WebGL game in Washington Square Park — walk or skate. Renders in the browser via three.js/WebGL."

    fns = ["play", "serve", "deploy", "register", "stop", "url", "path", "test"]

    name = "freewash"

    def __init__(self, web_dir: str = WEB_DIR, port: int = 8799, host: str = "127.0.0.1"):
        self.web_dir = os.path.expanduser(web_dir)
        self.port = int(port)
        self.host = host
        self._server = None
        self._thread = None
        self._relay = None          # the websocket relay server object
        self._relay_loop = None     # its asyncio loop
        self._relay_port = None

    # -- the default entry point -------------------------------------------
    def __call__(self, *a, **kw):
        return self.play(*a, **kw)

    def forward(self, *a, **kw):
        return self.play(*a, **kw)

    # -- serving -----------------------------------------------------------
    def serve(self, port: int = None, host: str = None, open: bool = False,
              background: bool = True, multiplayer: bool = True):
        """Start the static server hosting the game. Returns the URL.

        Also starts a best-effort WebSocket multiplayer relay on ``port + 1``;
        the browser auto-discovers it. Set ``multiplayer=False`` to skip it.
        """
        if port is not None:
            self.port = int(port)
        if host is not None:
            self.host = host

        if not os.path.exists(os.path.join(self.web_dir, "index.html")):
            raise FileNotFoundError(f"game not found at {self.web_dir}/index.html")

        handler = _make_handler(self.web_dir, self)

        # find a free port if the chosen one is busy
        for p in range(self.port, self.port + 25):
            try:
                self._server = HTTPServer((self.host, p), handler)
                self.port = p
                break
            except OSError:
                continue
        else:
            raise OSError(f"no free port in range {self.port}-{self.port + 25}")

        if multiplayer:
            # Relay port is browser-discovered via /ws-discovery, so it needs no
            # stable number. port+1 used to land on 8800 and squat chain's API
            # port at boot — scan far above the fleet's declared-port space.
            self._start_relay(self.port + 50000)

        if background:
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
            print(f"freewash serving at {self.url()}  (Ctrl-C / .stop() to halt)")
            if open:
                self._open()
            return self.url()
        else:
            print(f"freewash serving at {self.url()}  (Ctrl-C to halt)")
            if open:
                threading.Timer(0.6, self._open).start()
            try:
                self._server.serve_forever()
            except KeyboardInterrupt:
                self.stop()
            return self.url()

    # -- multiplayer relay -------------------------------------------------
    def _start_relay(self, port: int):
        """Tiny fan-out WebSocket relay: every client's state is broadcast to
        all others. Pure relay, no game logic. Runs in a daemon thread."""
        try:
            import asyncio
            import json as _json
            import websockets
        except Exception as e:
            print(f"freewash: multiplayer relay disabled ({e})")
            return

        clients = {}
        counter = {"n": 0}

        async def handler(ws):
            counter["n"] += 1
            cid = str(counter["n"])
            clients[cid] = ws
            try:
                await ws.send(_json.dumps({"type": "welcome", "id": cid}))
                async for msg in ws:
                    try:
                        data = _json.loads(msg)
                    except Exception:
                        continue
                    data["type"] = "state"
                    data["id"] = cid
                    out = _json.dumps(data)
                    for oid, o in list(clients.items()):
                        if oid == cid:
                            continue
                        try:
                            await o.send(out)
                        except Exception:
                            clients.pop(oid, None)
            except Exception:
                pass
            finally:
                clients.pop(cid, None)
                leave = _json.dumps({"type": "leave", "id": cid})
                for o in list(clients.values()):
                    try:
                        await o.send(leave)
                    except Exception:
                        pass

        async def main():
            server = None
            for p in range(port, port + 25):
                try:
                    server = await websockets.serve(handler, self.host, p)
                    self._relay_port = p
                    break
                except OSError:
                    continue
            if server is None:
                print("freewash: no free port for multiplayer relay")
                return
            print(f"freewash multiplayer relay on ws://{self.host}:{self._relay_port}")
            await asyncio.Future()  # run forever

        def run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._relay_loop = loop
            try:
                loop.run_until_complete(main())
            except Exception as e:
                print(f"freewash relay stopped: {e}")

        try:
            self._relay_port = port  # provisional; main() corrects after binding
            t = threading.Thread(target=run, daemon=True)
            t.start()
            self._relay = t
        except Exception as e:
            print(f"freewash: could not start relay ({e})")

    def play(self, port: int = None, host: str = None, block: bool = False):
        """Serve the game and open it in the default browser."""
        url = self.serve(port=port, host=host, open=True, background=not block)
        if block:
            return url
        return {"ok": True, "url": url, "dir": self.web_dir}

    def stop(self):
        """Stop the running server (and the relay)."""
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._relay_loop is not None:
            try:
                self._relay_loop.call_soon_threadsafe(self._relay_loop.stop)
            except Exception:
                pass
            self._relay_loop = None
        print("freewash stopped")
        return {"ok": True}

    # -- gateway registration (modc2.com/freewash) -------------------------
    def register(self, app_url: str = None, owner: str = None,
                 gateway: str = "https://modc2.com"):
        """Register with the mod gateway so it routes ``/freewash`` → this app.

        Uses the ``server.namespace`` registry the same way every other module
        does (this is deploy plumbing — the game itself stays dependency-free).
        Returns the public URL on success.
        """
        try:
            import mod as m
        except Exception as e:
            return {"ok": False, "error": f"mod framework not importable: {e}"}

        url = app_url or f"http://{self.host}:{self.port}"
        try:
            ns = m.mod("server.namespace")()
            ns.reg("freewash", url)
            ns.reg_app("freewash", url, owner=owner, api_url=url)
            public = f"{gateway.rstrip('/')}/freewash"
            print(f"freewash registered → {public}  (app: {url})")
            return {"ok": True, "gateway": public, "app": url}
        except Exception as e:
            print(f"freewash: gateway registration failed: {e}")
            return {"ok": False, "error": str(e), "app": url}

    def deploy(self, port: int = None, host: str = "0.0.0.0", owner: str = None,
               gateway: str = "https://modc2.com"):
        """Serve (bound for LAN/multiplayer) and register with the gateway."""
        self.serve(port=port, host=host, open=False, background=True, multiplayer=True)
        # advertise the loopback url to the gateway; the gateway proxies it
        reg = self.register(app_url=f"http://127.0.0.1:{self.port}", owner=owner, gateway=gateway)
        return {"ok": reg.get("ok"), "serving": self.url(), "relay_port": self._relay_port,
                "registration": reg}

    # -- helpers -----------------------------------------------------------
    def _open(self):
        try:
            webbrowser.open(self.url())
        except Exception as e:
            print(f"(could not auto-open browser: {e}) — visit {self.url()}")

    def url(self):
        return f"http://{self.host}:{self.port}/index.html"

    def path(self):
        return self.web_dir

    def test(self):
        """Smoke test: the game file exists and is non-trivial."""
        idx = os.path.join(self.web_dir, "index.html")
        assert os.path.exists(idx), f"missing {idx}"
        size = os.path.getsize(idx)
        assert size > 5000, f"index.html looks too small ({size} bytes)"
        with open(idx) as f:
            head = f.read()
        assert "three" in head and "importmap" in head, "expected a three.js WebGL game"
        print(f"ok — {size} bytes, WebGL/three.js game present")
        return {"ok": True, "bytes": size}


if __name__ == "__main__":
    Mod().play(block=True)
