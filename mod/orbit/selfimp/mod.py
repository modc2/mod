"""selfimp — coder/reviewer rounds over the agent protocol.

    m selfimp                                   # info()
    m selfimp/roster                            # agents you can pick from
    m selfimp/run query="add a --json flag to cli.py" path=/some/dir rounds=3
    m selfimp/run query=... reviewer='{"agent":"claude","model":"opus"}'
    m selfimp/list ; m selfimp/get id=<id>
    m selfimp/serve                             # agent-protocol server on :51450

Round 1: the coder does the task. The reviewer assesses the result and replies
with feedback ending in a verdict line, `VERDICT: APPROVED` or
`VERDICT: CHANGES`. On CHANGES the feedback goes back to the coder for the
next round. Stops on APPROVED or at the round limit.

Coder and reviewer are each {agent, url, model, prompt}: any server speaking
the agent protocol (GET /agents, POST /run) can fill either seat.
"""

import json
import os
import re
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = json.load(open(os.path.join(HERE, "config.json")))
DEFAULTS = CONFIG["defaults"]
PORT = CONFIG["port"]
STORE = os.environ.get("SELFIMP_DIR") or os.path.expanduser("~/.mod/selfimp/runs")

REVIEWER_PROMPT = (
    "You are a strict code reviewer. Assess the work the coder just did against the task: "
    "correctness, edge cases, tests, clarity. Read the actual files; do not edit them. "
    "Give concrete, numbered, actionable feedback. End with exactly one line: "
    "`VERDICT: APPROVED` if nothing needs to change, else `VERDICT: CHANGES`."
)
CODER_PROMPT = "You are the coder. Do the task by editing files. Address reviewer feedback point by point."
VERDICT_RE = re.compile(r"VERDICT:\s*(APPROVED|CHANGES)", re.I)


def _http(method, url, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def _text(result):
    """Flatten an agent-protocol result (list of steps or string) to text."""
    if isinstance(result, str):
        return result
    out = []
    for s in result or []:
        if isinstance(s, dict):
            p = s.get("params") if isinstance(s.get("params"), dict) else {}
            t = s.get("text") or s.get("content") or p.get("text") or p.get("content") or p.get("result")
            if t and s.get("type", s.get("fn", "")) not in ("tool_use", "tool"):
                out.append(str(t))
        elif s:
            out.append(str(s))
    return "\n".join(out).strip()


def _save(run):
    os.makedirs(STORE, exist_ok=True)
    with open(os.path.join(STORE, run["id"] + ".json"), "w") as f:
        json.dump(run, f, indent=1)


class Mod:
    def info(self):
        return {"name": "selfimp", "version": CONFIG["version"], "defaults": DEFAULTS,
                "runs": len(os.listdir(STORE)) if os.path.isdir(STORE) else 0}

    def agents(self):
        return {"agents": [{"name": "selfimp", "label": "Self-improve (coder + reviewer rounds)",
                            "description": CONFIG["description"], "module": "selfimp",
                            "available": True, "harness": False}]}

    def roster(self, url=None):
        """Agents reachable at an agent-protocol server (default: claudecode)."""
        base = (url or DEFAULTS["agent_url"]).rstrip("/")
        try:
            return {"url": base, **_http("GET", base + "/agents", timeout=5)}
        except Exception as e:
            return {"url": base, "agents": [], "error": str(e)}

    def _seat(self, seat, default_prompt):
        seat = dict(seat or {})
        seat.setdefault("url", DEFAULTS["agent_url"])
        seat.setdefault("agent", "claude")
        seat.setdefault("prompt", default_prompt)
        return seat

    def _call(self, seat, query, path, timeout):
        body = {"query": query, "prompt": seat["prompt"], "agent": seat["agent"],
                "timeout": timeout}
        if seat.get("model"):
            body["model"] = seat["model"]
        if path:
            body["path"] = path
        res = _http("POST", seat["url"].rstrip("/") + "/run", body, timeout=timeout + 30)
        if res.get("error"):
            raise RuntimeError(res["error"])
        return _text(res.get("result"))

    def run(self, query=None, coder=None, reviewer=None, rounds=None, path=None,
            timeout=None, **_):
        if not query:
            return {"error": "query is required"}
        if isinstance(coder, str): coder = json.loads(coder)
        if isinstance(reviewer, str): reviewer = json.loads(reviewer)
        coder = self._seat(coder or DEFAULTS["coder"], CODER_PROMPT)
        reviewer = self._seat(reviewer or DEFAULTS["reviewer"], REVIEWER_PROMPT)
        rounds = max(1, min(int(rounds or DEFAULTS["rounds"]), 10))
        timeout = int(timeout or DEFAULTS["timeout"])
        run = {"id": uuid.uuid4().hex[:12], "query": query, "path": path, "coder": coder,
               "reviewer": reviewer, "max_rounds": rounds, "rounds": [],
               "status": "running", "started_at": time.time()}
        feedback = None
        try:
            for n in range(1, rounds + 1):
                prompt = query if feedback is None else (
                    f"Original task:\n{query}\n\nThe reviewer's feedback on round {n - 1}:\n"
                    f"{feedback}\n\nRevise the work to address every point.")
                work = self._call(coder, prompt, path, timeout)
                review = self._call(reviewer, (
                    f"Task given to the coder:\n{query}\n\nCoder's report (round {n}):\n{work}\n\n"
                    "Assess the code in the working directory."), path, timeout)
                m = VERDICT_RE.findall(review)
                verdict = m[-1].upper() if m else "CHANGES"
                run["rounds"].append({"round": n, "coder": work, "review": review, "verdict": verdict})
                _save(run)
                if verdict == "APPROVED":
                    run["status"] = "approved"
                    break
                feedback = review
            else:
                run["status"] = "round_limit"
        except Exception as e:
            run["status"], run["error"] = "error", str(e)
        run["finished_at"] = time.time()
        _save(run)
        return run

    def get(self, id=None):
        p = os.path.join(STORE, os.path.basename(str(id)) + ".json")
        return json.load(open(p)) if id and os.path.isfile(p) else {"error": "no such run"}

    def list(self, limit=50):
        if not os.path.isdir(STORE):
            return {"runs": []}
        rows = []
        for f in os.listdir(STORE):
            try:
                r = json.load(open(os.path.join(STORE, f)))
                rows.append({k: r.get(k) for k in ("id", "query", "status", "started_at")}
                            | {"rounds": len(r.get("rounds", []))})
            except Exception:
                pass
        rows.sort(key=lambda r: r["started_at"] or 0, reverse=True)
        return {"runs": rows[:int(limit)]}

    def serve(self, port=None, host="127.0.0.1"):
        mod = self

        class H(BaseHTTPRequestHandler):
            def _send(self, obj, code=200):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                from urllib.parse import urlparse, parse_qs
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                fn = {"/agents": mod.agents, "/roster": mod.roster, "/list": mod.list,
                      "/get": mod.get, "/info": mod.info}.get(u.path)
                self._send(fn(**q) if fn else {"error": "not found"}, 200 if fn else 404)

            def do_POST(self):
                if self.path != "/run":
                    return self._send({"error": "not found"}, 404)
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                res = mod.run(**body)
                if res.get("error") and not res.get("rounds"):
                    return self._send(res, 400)
                self._send({"result": res["rounds"], "run": res["id"], "status": res["status"]})

            def log_message(self, *a):
                pass

        ThreadingHTTPServer((host, int(port or PORT)), H).serve_forever()

    def test(self):
        import subprocess, sys
        return subprocess.run([sys.executable, os.path.join(HERE, "tests", "test_selfimp.py")],
                              capture_output=True, text=True).stdout[-2000:]

    def kill(self):
        return {"error": "managed by pm2; stop the selfimp process there"}
