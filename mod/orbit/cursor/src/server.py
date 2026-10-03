"""
cursor job server — the process behind the contract's submit()/jobs()/health().

Stdlib only, local-first: no framework, no build step, state on disk.

  :{port}     (config.json "port", 8835)    protocol API
  :{app_port} (config.json "app_port", 8836) one-page console (same handlers
              answer /api/* so the page works both direct and behind the
              /cursor/ gateway prefix)

Routes
  GET  /health        {service, status, binary, jobs}
  GET  /info          contract info (name, icon, binary, default model, …)
  GET  /jobs          job list (newest first; stdout/stderr only for direct
                      loopback callers — proxied readers get metadata)
  GET  /jobs/<id>     one job, full record (direct loopback only)
  POST /jobs          {prompt, model?, work_dir?} → spawn cursor-agent -p

Submitting a job runs a CLI with write+shell access, so POST /jobs (and full
job records) are refused for proxied traffic: any X-Forwarded-* header means
the request came through a reverse proxy, not a local caller. Optionally set
CURSOR_SUBMIT_TOKEN to also require `Authorization: Bearer <token>`.

Jobs persist to ~/.mod/cursor/jobs.json; each job's output also lands in
~/.mod/cursor/jobs/<id>.log so a crash mid-run loses nothing.
"""
import importlib.util
import json
import os
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODULE_ROOT = HERE.parent
CONFIG = json.loads((MODULE_ROOT / "config.json").read_text())
NAME = CONFIG.get("name", "cursor")
API_PORT = int(CONFIG.get("port", 8835))
APP_PORT = int(CONFIG.get("app_port", 8836))
STATE_DIR = Path(os.path.expanduser(f"~/.mod/{NAME}"))
JOBS_PATH = STATE_DIR / "jobs.json"
JOB_LOG_DIR = STATE_DIR / "jobs"
MAX_JOBS_KEPT = 200
SUBMIT_TOKEN = os.environ.get("CURSOR_SUBMIT_TOKEN", "")

# Load our own mod.py by path — never `import mod`, which must stay free to
# resolve to the protocol package (see the fleet's import-shadowing trap).
_spec = importlib.util.spec_from_file_location("cursor_contract", HERE / "mod.py")
cursor_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cursor_mod)

CONTRACT = cursor_mod.Mod()

_jobs_lock = threading.Lock()


def _load_jobs():
    if JOBS_PATH.exists():
        try:
            return json.loads(JOBS_PATH.read_text())
        except json.JSONDecodeError:
            pass
    return []


def _save_jobs(jobs):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = JOBS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(jobs, indent=2))
    tmp.replace(JOBS_PATH)


JOBS = _load_jobs()


def _update_job(job_id, **fields):
    with _jobs_lock:
        for j in JOBS:
            if j["id"] == job_id:
                j.update(fields)
                break
        _save_jobs(JOBS)


def _binary():
    return CONTRACT.cli_path()


def _run_job(job_id, prompt, model, work_dir):
    binary = _binary()
    if not binary:
        _update_job(job_id, status="error", error="cursor-agent binary not installed",
                    finished_at=int(time.time()))
        return
    args = [binary] + CONTRACT.build_args(prompt, model, work_dir)
    JOB_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = JOB_LOG_DIR / f"{job_id}.log"
    env = dict(os.environ)
    env["PATH"] = env.get("PATH", "") + os.pathsep + os.path.expanduser("~/.local/bin")
    try:
        with log_path.open("w") as log:
            proc = subprocess.Popen(
                args, stdout=log, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, cwd=work_dir or str(MODULE_ROOT), env=env,
            )
            _update_job(job_id, status="running", pid=proc.pid)
            rc = proc.wait(timeout=3600)
        out = log_path.read_text()
        _update_job(job_id, status="done" if rc == 0 else "failed", returncode=rc,
                    output=out[-20000:], finished_at=int(time.time()), pid=None)
    except subprocess.TimeoutExpired:
        proc.kill()
        _update_job(job_id, status="timeout", finished_at=int(time.time()), pid=None)
    except Exception as e:
        _update_job(job_id, status="error", error=str(e),
                    finished_at=int(time.time()), pid=None)


def _submit(body):
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return None, "prompt required"
    model = body.get("model") or ""
    work_dir = body.get("work_dir") or ""
    if work_dir and not os.path.isdir(work_dir):
        return None, f"work_dir does not exist: {work_dir}"
    job = {
        "id": uuid.uuid4().hex[:12],
        "prompt": prompt,
        "model": model,
        "work_dir": work_dir,
        "status": "queued",
        "submitted_at": int(time.time()),
    }
    with _jobs_lock:
        JOBS.insert(0, job)
        del JOBS[MAX_JOBS_KEPT:]
        _save_jobs(JOBS)
    threading.Thread(target=_run_job, daemon=True,
                     args=(job["id"], prompt, model, work_dir)).start()
    return job, None


def _public_view(job):
    """Job metadata safe for proxied readers — no prompt/output contents."""
    return {k: job.get(k) for k in
            ("id", "status", "model", "submitted_at", "finished_at", "returncode")}


APP_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>cursor</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {{ background:#0d0d12; color:#e6e6ec; font:14px/1.5 ui-monospace,monospace;
         max-width:720px; margin:2rem auto; padding:0 1rem; }}
  h1 {{ color:{color}; font-size:1.2rem; }}
  .row {{ display:flex; gap:.6rem; padding:.35rem 0; border-bottom:1px solid #22222c; }}
  .muted {{ color:#8a8a99; }}
  .ok {{ color:#4ade80; }} .bad {{ color:#f87171; }}
  code {{ background:#1a1a24; padding:.1rem .35rem; border-radius:4px; }}
</style></head><body>
<h1>{icon} {name}</h1>
<p class="muted">{description}</p>
<div id="health" class="muted">checking…</div>
<h1>jobs</h1>
<div id="jobs" class="muted">loading…</div>
<script>
  // Relative paths so this works at :{app_port}/ and behind /{name}/ alike.
  fetch('api/health').then(r=>r.json()).then(h=>{{
    document.getElementById('health').innerHTML =
      'status <span class="'+(h.status==='ok'?'ok':'bad')+'">'+h.status+'</span>'
      + ' · binary ' + (h.binary ? '<code>'+h.binary+'</code>'
                                 : '<span class="bad">not installed</span>');
  }}).catch(()=>document.getElementById('health').textContent='api unreachable');
  fetch('api/jobs').then(r=>r.json()).then(jobs=>{{
    const el=document.getElementById('jobs');
    if(!jobs.length){{ el.textContent='none yet — POST /jobs on the api port'; return; }}
    el.innerHTML=jobs.map(j=>'<div class="row"><span>'+j.id+'</span>'
      +'<span class="'+(j.status==='done'?'ok':j.status==='failed'?'bad':'muted')+'">'
      +j.status+'</span><span class="muted">'
      +new Date(j.submitted_at*1000).toISOString().slice(0,16)+'</span></div>').join('');
  }}).catch(()=>{{}});
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_role = "api"

    def _is_direct(self):
        # Any forwarding header means a reverse proxy relayed this from the
        # outside world; job submission and full job records are local-only.
        for h in self.headers:
            if h.lower().startswith("x-forwarded") or h.lower() == "forwarded":
                return False
        return True

    def _authed(self):
        if not SUBMIT_TOKEN:
            return True
        return self.headers.get("Authorization", "") == f"Bearer {SUBMIT_TOKEN}"

    def _send(self, code, payload, content_type="application/json"):
        body = (json.dumps(payload) if content_type == "application/json"
                else payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path.startswith("/api/"):
            path = path[4:] or "/"
        elif self.server_role == "app":
            page = APP_PAGE.format(name=NAME, icon=CONTRACT.ICON, color=CONTRACT.COLOR,
                                   description=CONTRACT.DESCRIPTION, app_port=APP_PORT)
            return self._send(200, page, "text/html; charset=utf-8")
        if path == "/health":
            binary = _binary()
            return self._send(200, {
                "service": NAME, "status": "ok" if binary else "degraded",
                "binary": binary, "jobs": len(JOBS),
            })
        if path == "/info":
            return self._send(200, CONTRACT.info())
        if path == "/jobs":
            with _jobs_lock:
                jobs = list(JOBS)
            if not self._is_direct():
                jobs = [_public_view(j) for j in jobs]
            return self._send(200, jobs)
        if path.startswith("/jobs/"):
            job_id = path.split("/")[2]
            with _jobs_lock:
                job = next((j for j in JOBS if j["id"] == job_id), None)
            if not job:
                return self._send(404, {"error": "no such job"})
            return self._send(200, job if self._is_direct() else _public_view(job))
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/")
        if path.startswith("/api/"):
            path = path[4:]
        if path != "/jobs":
            return self._send(404, {"error": "not found"})
        if not self._is_direct():
            return self._send(403, {"error": "job submission is local-only"})
        if not self._authed():
            return self._send(401, {"error": "bad or missing bearer token"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, {"error": "invalid JSON body"})
        job, err = _submit(body)
        if err:
            return self._send(400, {"error": err})
        return self._send(200, job)


class AppHandler(Handler):
    server_role = "app"


def main():
    api = ThreadingHTTPServer(("127.0.0.1", API_PORT), Handler)
    app = ThreadingHTTPServer(("127.0.0.1", APP_PORT), AppHandler)
    threading.Thread(target=app.serve_forever, daemon=True).start()
    print(f"{NAME}: api :{API_PORT}, app :{APP_PORT}, binary={_binary()}")
    api.serve_forever()


if __name__ == "__main__":
    main()
