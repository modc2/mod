"""Server for one of the build console's background agents, as its own app.

The agent itself lives inside orbit/build (its loop, its state, its gates).
This process only serves the console for it and says where build is — the
page talks to build's API directly with the browser's build session, so
every write is gated by build exactly as it is in the AGENTS tab. Nothing
here holds a credential or proxies one.

    /<name>/*    → the console (prefix kept by the gateway; stripped here)
    /info        → what this is + where build is (also /<name>/info)
    /health      → liveness

    python3 serve.py [--port N] [--host 0.0.0.0]
"""

import argparse
import collections
import datetime
import json
import os
import queue
import threading
import time
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(MODULE_DIR, 'web')
BUILD_CONFIG = os.path.join(MODULE_DIR, '..', 'build', 'config.json')
HISTORY_FILE = os.path.expanduser('~/.mod/appwatch/history.jsonl')


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


CFG = _load(os.path.join(MODULE_DIR, 'config.json'))
NAME = CFG.get('name') or os.path.basename(MODULE_DIR)
PREFIX = '/' + NAME

_history = collections.deque(maxlen=500)

_sse_clients: set = set()
_sse_lock = threading.Lock()


def _load_history():
    try:
        with open(HISTORY_FILE) as f:
            lines = f.readlines()
        for line in lines[-500:]:
            try:
                _history.append(json.loads(line))
            except Exception:
                pass
    except FileNotFoundError:
        pass
    except Exception:
        pass


def _append_history_entry(entry):
    try:
        os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
        with open(HISTORY_FILE, 'a') as f:
            f.write(json.dumps(entry) + '\n')
    except Exception:
        pass


def _trim_history_file():
    try:
        entries = list(_history)
        with open(HISTORY_FILE, 'w') as f:
            for e in entries:
                f.write(json.dumps(e) + '\n')
    except Exception:
        pass


def _poll_status():
    last_c = None
    count = 0
    while True:
        try:
            t0 = time.time()
            with urllib.request.urlopen('http://127.0.0.1:8890/appwatch/status', timeout=4) as r:
                data = json.loads(r.read())
            ms = int((time.time() - t0) * 1000)
            c = data.get('last_check')
            if c != last_c:
                last_c = c
                entry = {'t': int(time.time()), 'up': data.get('up'), 'c': c, 'ms': ms}
                _history.append(entry)
                _append_history_entry(entry)
                count += 1
                if count % 100 == 0:
                    _trim_history_file()
                payload = b'data: ' + json.dumps({'status': data, 'entry': entry}).encode() + b'\n\n'
                with _sse_lock:
                    dead = set()
                    for q in _sse_clients:
                        try:
                            q.put_nowait(payload)
                        except queue.Full:
                            dead.add(q)
                    _sse_clients -= dead
        except Exception:
            pass
        time.sleep(5)


def _daily_stats():
    days = {}
    try:
        with open(HISTORY_FILE) as f:
            for line in f:
                try:
                    e = json.loads(line)
                    t = e.get('t')
                    if t is None:
                        continue
                    d = datetime.datetime.utcfromtimestamp(t)
                    key = d.strftime('%Y-%m-%d')
                    label = d.strftime('%b') + ' ' + str(d.day)
                    if key not in days:
                        days[key] = {'label': label, 'total': 0, 'up_count': 0, 'ms_vals': []}
                    days[key]['total'] += 1
                    if e.get('up'):
                        days[key]['up_count'] += 1
                    ms = e.get('ms')
                    if ms is not None:
                        days[key]['ms_vals'].append(ms)
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    except Exception:
        pass
    result = []
    for key in sorted(days.keys(), reverse=True)[:7]:
        v = days[key]
        ms_vals = sorted(v['ms_vals'])
        n = len(ms_vals)
        avg_ms = round(sum(ms_vals) / n) if n else None
        p95_ms = ms_vals[int(n * 0.95)] if n >= 20 else None
        uptime_pct = round(v['up_count'] / v['total'] * 100, 2) if v['total'] else 0.0
        result.append({
            'date': v['label'],
            'total': v['total'],
            'up_count': v['up_count'],
            'uptime_pct': uptime_pct,
            'avg_ms': avg_ms,
            'p95_ms': p95_ms,
        })
    return result


def info():
    b = _load(BUILD_CONFIG)
    return {
        'name': NAME,
        'title': CFG.get('title', NAME),
        'description': CFG.get('description', ''),
        'agent': CFG.get('agent', {}),
        'build': {
            'owner': b.get('owner', ''),
            'port': b.get('port', 8890),
            'app': '/build/',
            'api': '/api/build',
        },
    }


def _build_badge():
    if not _history:
        right = "checking"
        color = "#9f9f9f"
    else:
        last = list(_history)[-1]
        if last.get('up'):
            ms = last.get('ms')
            right = f"✓ up · {ms}ms" if ms is not None else "✓ up"
            color = "#5ee08a"
        else:
            right = "✗ down"
            color = "#dc3c3c"
    left = "appwatch"
    lw = 72
    rw = max(52, len(right) * 7 + 16)
    tw = lw + rw
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{tw}" height="20">'
        f'<rect width="{lw}" height="20" fill="#555"/>'
        f'<rect x="{lw}" width="{rw}" height="20" fill="{color}"/>'
        f'<g fill="#fff" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">'
        f'<text x="{lw // 2}" y="15" text-anchor="middle">{left}</text>'
        f'<text x="{lw + rw // 2}" y="15" text-anchor="middle">{right}</text>'
        f'</g>'
        f'</svg>'
    ).encode()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB_DIR, **kw)

    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def _sse(self):
        q: queue.Queue = queue.Queue(maxsize=20)
        with _sse_lock:
            _sse_clients.add(q)
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.send_header('Access-Control-Allow-Origin', '*')
            SimpleHTTPRequestHandler.end_headers(self)
            while True:
                try:
                    msg = q.get(timeout=25)
                except queue.Empty:
                    msg = b': keepalive\n\n'
                self.wfile.write(msg)
                self.wfile.flush()
        except Exception:
            pass
        finally:
            with _sse_lock:
                _sse_clients.discard(q)

    def _svg(self, body, max_age=0):
        self.send_response(200)
        self.send_header('Content-Type', 'image/svg+xml')
        self.send_header('Content-Length', str(len(body)))
        cache = f'max-age={max_age}' if max_age else 'no-cache'
        self.send_header('Cache-Control', cache)
        self.send_header('Access-Control-Allow-Origin', '*')
        # bypass end_headers override to avoid a second Cache-Control: no-cache
        SimpleHTTPRequestHandler.end_headers(self)
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == PREFIX:
            self.send_response(308)
            self.send_header('Location', PREFIX + '/')
            self.end_headers()
            return
        if path.startswith(PREFIX + '/'):
            path = path[len(PREFIX):]
        elif path.startswith('/api' + PREFIX):
            path = path[len('/api' + PREFIX):] or '/'
        if path in ('/info', '/api/info'):
            return self._json(info())
        if path in ('/health', '/api/health'):
            return self._json({'ok': True, 'name': NAME})
        if path in ('/history', '/api/history'):
            return self._json(list(_history))
        if path in ('/daily', '/api/daily'):
            return self._json(_daily_stats())
        if path == '/badge.svg':
            return self._svg(_build_badge(), max_age=8)
        if path in ('/stream', '/api/stream'):
            if self.command == 'HEAD':
                return self._json({'ok': True})
            return self._sse()
        self.path = path
        return super().do_GET()

    do_HEAD = do_GET


def serve(port=None, host='0.0.0.0'):
    port = int(port or CFG.get('port'))
    _load_history()
    t = threading.Thread(target=_poll_status, daemon=True)
    t.start()
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f'{NAME} on http://{host}:{port}{PREFIX}/', flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=None)
    ap.add_argument('--host', default='0.0.0.0')
    a = ap.parse_args()
    serve(a.port, a.host)
