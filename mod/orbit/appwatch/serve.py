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
PROBES_FILE = os.path.expanduser('~/.mod/appwatch/probes.json')


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

_probes: list = []
_probe_hist: dict = {}
_probes_lock = threading.Lock()


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
                entry = {'t': int(time.time()), 'up': data.get('up'), 'c': c, 'ms': ms if data.get('up') else None}
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


def _load_probes():
    global _probes
    try:
        with open(PROBES_FILE) as f:
            loaded = json.load(f)
        if isinstance(loaded, list):
            with _probes_lock:
                _probes = loaded
                for p in _probes:
                    if p.get('id') and p['id'] not in _probe_hist:
                        _probe_hist[p['id']] = collections.deque(maxlen=200)
    except FileNotFoundError:
        pass
    except Exception:
        pass


def _save_probes():
    try:
        os.makedirs(os.path.dirname(PROBES_FILE), exist_ok=True)
        with _probes_lock:
            data = list(_probes)
        with open(PROBES_FILE, 'w') as f:
            json.dump(data, f)
    except Exception:
        pass


def _poll_probes():
    while True:
        with _probes_lock:
            probes_snap = list(_probes)
        for p in probes_snap:
            pid = p.get('id')
            if not pid:
                continue
            try:
                t0 = time.time()
                with urllib.request.urlopen(p['url'], timeout=4) as r:
                    status = r.status
                ms = int((time.time() - t0) * 1000)
                up = status < 400
            except Exception:
                ms = None
                up = False
            entry = {'t': int(time.time()), 'up': up, 'ms': ms}
            with _probes_lock:
                if pid not in _probe_hist:
                    continue
                _probe_hist[pid].append(entry)
            payload = b'event: probe\ndata: ' + json.dumps({'id': pid, 'entry': entry}).encode() + b'\n\n'
            with _sse_lock:
                dead = set()
                for q in _sse_clients:
                    try:
                        q.put_nowait(payload)
                    except queue.Full:
                        dead.add(q)
                _sse_clients -= dead
        time.sleep(5)


def _daily_stats(days=90):
    by_day = {}
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
                    if key not in by_day:
                        by_day[key] = {'label': label, 'total': 0, 'up_count': 0, 'ms_vals': []}
                    by_day[key]['total'] += 1
                    if e.get('up'):
                        by_day[key]['up_count'] += 1
                    ms = e.get('ms')
                    if ms is not None:
                        by_day[key]['ms_vals'].append(ms)
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    except Exception:
        pass
    result = []
    for key in sorted(by_day.keys())[-days:]:
        v = by_day[key]
        ms_vals = sorted(v['ms_vals'])
        n = len(ms_vals)
        avg_ms = round(sum(ms_vals) / n) if n else None
        p95_ms = ms_vals[int(n * 0.95)] if n >= 20 else None
        uptime_pct = round(v['up_count'] / v['total'] * 100, 2) if v['total'] else 0.0
        result.append({
            'key': key,
            'date': v['label'],
            'total': v['total'],
            'up_count': v['up_count'],
            'uptime_pct': uptime_pct,
            'avg_ms': avg_ms,
            'p95_ms': p95_ms,
        })
    return result


def _hourly_stats():
    now = int(time.time())
    cutoff = now - 86400
    hours = {}
    try:
        with open(HISTORY_FILE) as f:
            for line in f:
                try:
                    e = json.loads(line)
                    t = e.get('t')
                    if t is None or t < cutoff:
                        continue
                    h = (t // 3600) * 3600
                    if h not in hours:
                        dt = datetime.datetime.utcfromtimestamp(h)
                        hours[h] = {'hour': h, 'label': dt.strftime('%H:%M'), 'total': 0, 'up_count': 0, 'ms_vals': []}
                    hours[h]['total'] += 1
                    if e.get('up'):
                        hours[h]['up_count'] += 1
                    ms = e.get('ms')
                    if ms is not None:
                        hours[h]['ms_vals'].append(ms)
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    except Exception:
        pass
    result = []
    for h in sorted(hours.keys())[-24:]:
        v = hours[h]
        ms_vals = v['ms_vals']
        avg_ms = round(sum(ms_vals) / len(ms_vals)) if ms_vals else None
        uptime_pct = round(v['up_count'] / v['total'] * 100, 2) if v['total'] else 0.0
        result.append({
            'hour': v['hour'],
            'label': v['label'],
            'total': v['total'],
            'uptime_pct': uptime_pct,
            'avg_ms': avg_ms,
        })
    return result


def _compute_incidents(tolerance_s=30):
    spans = []
    cur = None
    all_ms = []
    try:
        with open(HISTORY_FILE) as f:
            for line in f:
                try:
                    e = json.loads(line)
                    t = e.get('t')
                    if t is None:
                        continue
                    ms = e.get('ms')
                    if ms is not None:
                        all_ms.append(ms)
                    if e.get('up') is False:
                        if cur is None:
                            cur = {'start': t, '_last': t}
                        elif t - cur['_last'] > tolerance_s:
                            spans.append({'start': cur['start'], 'end': cur['_last'], 'duration_s': cur['_last'] - cur['start']})
                            cur = {'start': t, '_last': t}
                        else:
                            cur['_last'] = t
                    else:
                        if cur is not None:
                            spans.append({'start': cur['start'], 'end': t, 'duration_s': t - cur['start']})
                            cur = None
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    except Exception:
        pass
    if cur is not None:
        now = int(time.time())
        spans.append({'start': cur['start'], 'end': None, 'duration_s': now - cur['start']})
    total_down_s = sum(s['duration_s'] for s in spans)
    resolved = [s for s in spans if s['end'] is not None]
    mttr_s = round(sum(s['duration_s'] for s in resolved) / len(resolved)) if resolved else None
    all_ms_sorted = sorted(all_ms)
    n = len(all_ms_sorted)
    p95_ms = all_ms_sorted[int(n * 0.95)] if n >= 20 else None
    return {
        'incidents': spans,
        'total_incidents': len(spans),
        'total_down_s': total_down_s,
        'mttr_s': mttr_s,
        'p95_ms': p95_ms,
    }


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
            qs = urlparse(self.path).query
            days_n = 90
            for part in qs.split('&'):
                if part.startswith('days='):
                    try:
                        days_n = max(1, min(365, int(part[5:])))
                    except ValueError:
                        pass
            return self._json(_daily_stats(days_n))
        if path in ('/hourly', '/api/hourly'):
            return self._json(_hourly_stats())
        if path in ('/incidents', '/api/incidents'):
            return self._json(_compute_incidents())
        if path == '/badge.svg':
            return self._svg(_build_badge(), max_age=8)
        if path in ('/public',):
            self.path = '/public.html'
            return super().do_GET()
        if path in ('/stream', '/api/stream'):
            if self.command == 'HEAD':
                return self._json({'ok': True})
            return self._sse()
        if path in ('/probes', '/api/probes'):
            with _probes_lock:
                result = []
                for p in _probes:
                    h = list(_probe_hist.get(p['id'], []))
                    result.append({**p, 'last': h[-1] if h else None})
            return self._json(result)
        self.path = path
        return super().do_GET()

    do_HEAD = do_GET

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path
        if path.startswith(PREFIX + '/'):
            path = path[len(PREFIX):]
        elif path.startswith('/api' + PREFIX):
            path = path[len('/api' + PREFIX):] or '/'
        if path in ('/probes', '/api/probes'):
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
            except Exception:
                return self._json({'error': 'invalid json'}, 400)
            url = (data.get('url') or '').strip()
            label = (data.get('label') or '').strip()
            if not url:
                return self._json({'error': 'url required'}, 400)
            pid = f'p{int(time.time() * 1000)}'
            probe = {'id': pid, 'url': url, 'label': label or url}
            with _probes_lock:
                _probes.append(probe)
                _probe_hist[pid] = collections.deque(maxlen=200)
            _save_probes()
            return self._json(probe)
        return self._json({'error': 'not found'}, 404)

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path.startswith(PREFIX + '/'):
            path = path[len(PREFIX):]
        elif path.startswith('/api' + PREFIX):
            path = path[len('/api' + PREFIX):] or '/'
        if path.startswith('/probes/'):
            pid = path[len('/probes/'):]
            if pid:
                removed = False
                with _probes_lock:
                    before = len(_probes)
                    _probes[:] = [p for p in _probes if p.get('id') != pid]
                    _probe_hist.pop(pid, None)
                    removed = len(_probes) < before
                if removed:
                    _save_probes()
                    return self._json({'ok': True})
        return self._json({'error': 'not found'}, 404)


def serve(port=None, host='0.0.0.0'):
    port = int(port or CFG.get('port'))
    _load_history()
    _load_probes()
    t = threading.Thread(target=_poll_status, daemon=True)
    t.start()
    tp = threading.Thread(target=_poll_probes, daemon=True)
    tp.start()
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f'{NAME} on http://{host}:{port}{PREFIX}/', flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=None)
    ap.add_argument('--host', default='0.0.0.0')
    a = ap.parse_args()
    serve(a.port, a.host)
