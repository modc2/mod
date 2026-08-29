"""infer — make a model cheaper to run, and prove you did.

Inference optimization that does not care what architecture you trained. A
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
    infer — inference optimization for any model architecture, on one standard
    binary. ONNX in, a smaller and faster ONNX out, running unchanged in
    onnxruntime here and onnxruntime-web in a browser tab. Fuse and fold the
    graph, quantize the weights to int8 or fp16, and get back what each pass
    actually bought: nodes removed, bytes saved, p50 latency before and after,
    how far the outputs moved, and whether the result can still run in a
    browser. Fourteen MCP tools, a REST API and a console on one port.
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
