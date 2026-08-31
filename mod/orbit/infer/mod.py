"""infer — prove a model said it, and make it cheaper to run.

Two halves that need each other.

**The board.** A claim here is not a screenshot. It is the canonical request, the
exact output, three sha256 hashes over their own content, a mod-protocol
signature and a core/store CID — and the board only takes runs with the sampler
switched off, temperature 0 and top_p 1, because a sampled receipt makes every
disagreement on the board unreadable.

    m infer/run gpt-4o prompt="name three primes" repeat=3
    m infer/board sort=divergent          # models that will not hold still
    m infer/replicate <question>          # ask it again, file the answer
    m infer/verify <receipt> rerun=true   # recheck every hash from content
    m infer/leaderboard                   # who is reproducible, measured

Receipts sharing a request hash are one question, and the question carries the
verdict: unreplicated, self-reproduced, reproduced, or divergent. Temperature 0
is greedy, not deterministic — batching, expert routing and float reduction
order are not in the request — so `divergent` on a hosted model is a finding,
not a bug.

**The optimizer**, which is where the bit-exact receipts come from. Inference
optimization that does not care what architecture you trained. A
CNN, an LSTM, a transformer, a gradient-boosted forest: by the time it is here
it is a graph, and the passes read the graph. One standard binary carries it —
**ONNX** — because that is the format both runtimes execute without a second
conversion:

    locally   onnxruntime          `m infer/bench`
    browser   onnxruntime-web      the console, on the same bytes

    m infer/examples                          # three models to work on
    m infer/inspect mlp                       # what it is: ops, params, shapes
    m infer/plan mlp target=web               # what to try, and why
    m infer/optimize mlp slim,extended        # do it, measured both ways
    m infer/compare cnn                       # every pass, side by side
    m infer/bench cnn runs=100 threads=1      # p50/p90/p99, warmed up
    m infer/parity mlp mlp+slim+extended      # did the answers survive
    m infer/portable cnn+slim+extended        # will it run in a browser
    m infer/export torchvision:resnet18       # torch → the standard binary
    m infer/run mlp runtime=onnx repeat=3     # a receipt anyone can re-execute
    m infer/serve

Nothing is asserted without being measured. `optimize` benchmarks the model it
replaced, on the same inputs, in the same process, then compares the outputs
and re-checks portability — because the fastest local graph is very often the
one that quietly stopped running anywhere else.
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# Appended, never prepended: this directory holds mod.py, which would shadow
# the protocol's own `mod` package for anything that imports it after us.
if HERE not in sys.path:
    sys.path.append(HERE)


class Mod:
    description = """
    infer — a board where model output at temperature 0 is a signed,
    content-addressed, re-runnable claim, and the ONNX optimizer that produces
    the bit-exact half of it. Post what a model said, hash it, publish it to
    core/store, and let anyone ask the same question again and file their answer
    beside yours: same bytes from two independent signers is `reproduced`, two
    different answers to one greedy question is `divergent`, and the board says
    which character they parted on. The optimizer half fuses, folds and
    quantizes any architecture on one standard binary, running unchanged in
    onnxruntime here and onnxruntime-web in a browser tab. Twenty-eight MCP
    tools, a REST API and a console on one port.
    """

    def __init__(self, port=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 50820))
        self.base = cfg.get('base_path', '/infer')

    # ── plumbing ─────────────────────────────────────────────────

    def config(self):
        try:
            with open(os.path.join(HERE, 'config.json')) as f:
                return json.load(f)
        except Exception:
            return {}

    def info(self):
        """What this module is, and every route it serves."""
        import api
        return api.info()

    forward = info

    def health(self):
        """Runtime versions, execution providers, and which passes work here."""
        import engine
        return engine.health()

    # ── the board ────────────────────────────────────────────────

    def board(self, model=None, provider=None, runtime=None, verdict=None,
              by=None, q=None, limit=50, sort='recent'):
        """Every question posted, newest first, with the verdict it earned."""
        import proofs
        return proofs.board(model=model, provider=provider, runtime=runtime,
                            verdict=verdict, by=by, q=q, limit=limit, sort=sort)

    def run(self, model, prompt=None, provider=None, runtime=None, repeat=1,
            system=None, messages=None, max_tokens=512, seed=None, stop=None,
            publish=True, sign=True, api_key=None, batch=1, shapes=None):
        """Ask it at temperature 0, then hash, sign and publish the answer."""
        import proofs
        return proofs.run(model, provider=provider, runtime=runtime, sign=sign,
                          publish=publish, repeat=repeat, prompt=prompt,
                          system=system, messages=messages,
                          max_tokens=max_tokens, seed=seed, stop=stop,
                          api_key=api_key, batch=batch, shapes=shapes)

    ask = run

    def post(self, model=None, output=None, prompt=None, provider=None,
             claim=None, attestation=None, publish=True, sign=True, **kw):
        """File a run you did somewhere else — the hashes are recomputed here."""
        import proofs
        return proofs.post(claim=claim, sign=sign, publish=publish,
                           attestation=attestation, model=model, output=output,
                           prompt=prompt, provider=provider, **kw)

    def replicate(self, question=None, receipt=None, provider=None,
                  publish=True, sign=True, api_key=None):
        """Ask the same question again and say whether the answer held."""
        import proofs
        return proofs.replicate(question_id=question, receipt=receipt,
                                provider=provider, publish=publish, sign=sign,
                                api_key=api_key)

    def verify(self, receipt, rerun=False, fetch=True):
        """Recheck every hash, the signature and the stored bytes from content."""
        import proofs
        return proofs.verify(receipt, rerun=rerun, fetch=fetch)

    def question(self, question, full=False):
        """One question: its receipts, its variants, and where they parted."""
        import proofs
        return proofs.question(question, full=full)

    def receipt(self, receipt):
        """One receipt, exactly as it was published."""
        import proofs
        return proofs.receipt(receipt)

    def diff(self, a, b):
        """Two answers side by side, and the character they disagree on."""
        import proofs
        return proofs.diff(a, b)

    def leaderboard(self, runtime=None, min_receipts=2):
        """Which models hold still at temperature 0, by the receipts here."""
        import proofs
        return proofs.leaderboard(runtime=runtime, min_receipts=min_receipts)

    def canon(self, model, prompt=None, runtime='llm', **kw):
        """The canonical request bytes and their hash, without running it."""
        import proofs
        return proofs.canonical(runtime=runtime, model=model, prompt=prompt, **kw)

    def providers(self):
        """Which endpoints are reachable from here, and which have a key."""
        import proofs
        return proofs.providers()

    def set_key(self, provider, key=None):
        """Give a provider a key — 0600, off-tree, never inside a receipt."""
        import proofs
        return proofs.set_key(provider, key)

    def add_provider(self, name, base, style='openai', note=None):
        """Register any other openai- or anthropic-shaped endpoint."""
        import proofs
        return proofs.add_provider(name, base, style=style, note=note)

    def import_receipt(self, cid):
        """Pull in a receipt published from another box, by its store CID."""
        import proofs
        return proofs.fetch(cid)

    def status(self):
        """The board at a glance: receipts, verdicts, signer, store health."""
        import proofs
        return proofs.status()

    def forget(self, receipt):
        """Drop a receipt from this board. Its CID still resolves — that is the point."""
        import proofs
        return proofs.delete(receipt)

    # ── the store ────────────────────────────────────────────────

    def models(self, limit=200):
        """Every model held, newest first."""
        import engine
        return engine.models(limit=limit)

    ls = models

    def add(self, path=None, url=None, data=None, name=None, note=None):
        """Take an .onnx in — from a path, a URL, or base64 bytes."""
        import engine
        return engine.add(data=data, path=path, url=url, name=name, note=note)

    def rm(self, model):
        """Forget a model, and its bytes if nothing else points at them."""
        import engine
        return engine.delete(model)

    delete = rm

    def examples(self):
        """Plant an MLP, a CNN and a transformer block to experiment on."""
        import engine
        return engine.examples()

    def export(self, source, name=None, opset=17, shape=None, weights=None):
        """torchvision:<name>, a .py defining `model`, or a .pt → ONNX."""
        import engine
        return engine.export(source, name=name, opset=opset, shape=shape,
                             weights=weights)

    # ── reading ──────────────────────────────────────────────────

    def inspect(self, model):
        """What a model IS: opset, every op, params, inputs, outputs, arch."""
        import engine
        return engine.inspect(model)

    what = inspect

    def passes(self):
        """The catalog: what each pass does, what it costs, whether it is here."""
        import engine
        return engine.passes()

    def plan(self, model, target='local'):
        """What is worth trying on this one, and why. target=web keeps it portable."""
        import engine
        return engine.plan(model, target=target)

    def portable(self, model):
        """Will these exact bytes run in a browser? Which ops say otherwise."""
        import engine
        return engine.portable(model)

    # ── the work ─────────────────────────────────────────────────

    def optimize(self, model, passes=None, name=None, check=True, runs=None,
                 batch=1, samples=4, tol=1e-3, shapes=None, threads=None):
        """Run passes, and report what each one bought. Read `verdict` first."""
        import engine
        return engine.optimize(model, passes_=passes, name=name, check=check,
                               runs=runs or engine.DEFAULT_RUNS, batch=batch,
                               samples=samples, tol=tol, shapes=shapes,
                               threads=threads)

    opt = optimize

    def compare(self, model, passes=None, runs=20, batch=1, shapes=None):
        """Every pass applied on its own, side by side, ranked."""
        import mcp
        return mcp.call_tool('infer_compare', {
            'model': model, 'passes': passes, 'runs': runs, 'batch': batch,
            'shapes': shapes})

    def bench(self, model, runs=None, warmup=None, batch=1, threads=None,
              provider=None, shapes=None):
        """Time it: p50, p90, p99, mean, stdev, throughput — warmed up."""
        import engine
        return engine.bench(model, runs=runs or engine.DEFAULT_RUNS,
                            warmup=engine.DEFAULT_WARMUP if warmup is None else warmup,
                            batch=batch, threads=threads, provider=provider,
                            shapes=shapes)

    def parity(self, a, b, samples=8, batch=1, tol=1e-3, shapes=None):
        """Same inputs into both: how far did the outputs move, does argmax hold."""
        import engine
        return engine.parity(a, b, samples=samples, batch=batch, tol=tol,
                             shapes=shapes)

    # ── surfaces ─────────────────────────────────────────────────

    def tools(self):
        """The MCP tool registry, as an agent sees it."""
        import mcp
        return {'tools': mcp.tool_list(), 'count': len(mcp.TOOLS),
                'instructions': mcp.INSTRUCTIONS}

    def mcp_call(self, tool, **args):
        """Invoke one MCP tool directly, without a transport in the way."""
        import mcp
        return mcp.call_tool(tool, args)

    def mcp_config(self, url=None):
        """Drop-in client config for anything that speaks MCP over HTTP."""
        return {'mcpServers': {'infer': {
            'type': 'http', 'url': url or f'http://localhost:{self.port}/mcp'}}}

    def serve(self, port=None, background=False):
        """Run the REST API, the console and the MCP server on one port."""
        port = int(port or self.port)
        if not background:
            import api
            return api.serve(port)
        proc = subprocess.Popen([sys.executable, os.path.join(HERE, 'api.py'),
                                 '--port', str(port)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                cwd=HERE)
        return {'pid': proc.pid, 'port': port,
                'api': f'http://localhost:{port}/',
                'app': f'http://localhost:{port}{self.base}',
                'mcp': f'http://localhost:{port}/mcp'}

    def kill(self, port=None):
        """Stop whatever is holding the port. Targets the port, never a name —
        this box runs ~100 services and a pattern kill takes the fleet down."""
        port = int(port or self.port)
        out = subprocess.run(['bash', '-c', f'lsof -ti tcp:{port} || true'],
                             capture_output=True, text=True).stdout.split()
        for pid in out:
            subprocess.run(['kill', pid], capture_output=True)
        return {'port': port, 'killed': out}

    def test(self):
        """Run the module's tests."""
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'test'],
                           cwd=HERE, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}

    def readme(self):
        """The project README."""
        for name in ('README.md', 'skill.md'):
            p = os.path.join(HERE, name)
            if os.path.exists(p):
                with open(p) as f:
                    return f.read()
        return None
