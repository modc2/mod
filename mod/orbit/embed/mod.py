"""embed — small models, made smaller, with the cost written down.

The module surface, for the CLI and for other mods:

    m embed/info                         what this is
    m embed/models                       the model zoo, and what is built
    m embed/build name=bow-64            build one (a couple of seconds)
    m embed/search query="how fine a grind"
    m embed/classify text="the film was dull"
    m embed/compare                      one tensor, every compression method
    m embed/compress name=bow-64 method=int8
    m embed/sweep name=bow-64            every method, scored on the task
    m embed/check name=bow-64            our runtime vs onnxruntime
    m embed/examples                     the lessons, in order
    m embed/serve                        API :50620 + console :50621

Every function is the same code the API calls — the API is a transport, not a
second implementation.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


class Mod:
    description = ('Small models and what it costs to shrink them. Builds two '
                   'tiny ONNX models on the spot, reads and writes .onnx from '
                   'raw protobuf, runs them in numpy, quantizes them to '
                   'float16 / int8 / 4-bit, and measures what each step did to '
                   'the answers rather than only to the file size.')
    path = str(HERE)
    port = 50620
    app_port = 50621

    # ── what it is ───────────────────────────────────────────────────

    def forward(self, **kwargs):
        """The null call: the module's own card."""
        return self.info()

    def info(self) -> Dict[str, Any]:
        from src import compress, runtime, zoo
        return {
            'name': 'embed',
            'description': self.description,
            'models': [m['name'] for m in zoo.catalog()],
            'methods': list(compress.METHODS),
            'ops_implemented': runtime.implemented(),
            'dependencies': 'numpy — onnxruntime optional, only to cross-check',
            'state': str(zoo.HOME),
            'urls': {'api': f'http://localhost:{self.port}',
                     'app': f'http://localhost:{self.app_port}/embed'},
            'fns': [f for f in dir(self)
                    if not f.startswith('_') and callable(getattr(self, f))],
        }

    def readme(self) -> Optional[str]:
        target = HERE / 'README.md'
        return target.read_text() if target.exists() else None

    def health(self) -> Dict[str, Any]:
        from src import check, zoo
        return {'ok': True, 'models_built': [m['name'] for m in zoo.catalog()
                                             if m['present']],
                'cross_check': check.available()}

    # ── the zoo ──────────────────────────────────────────────────────

    def models(self) -> List[Dict[str, Any]]:
        """What is available, what is built, and what each one weighs."""
        from src import zoo
        return zoo.catalog()

    def build(self, name: str = 'bow-64', rebuild: bool = False) -> Dict[str, Any]:
        """Build a model. Deterministic — the same seed gives the same bytes."""
        from src import onnxfile, zoo
        target = zoo.path(name)
        if rebuild:
            target.unlink(missing_ok=True)
        file = zoo.ensure(name)
        return {'name': name, 'path': str(file), 'bytes': file.stat().st_size,
                'built': zoo.built().get(name),
                'summary': onnxfile.load(file).summary()}

    def pull(self, name: str = 'minilm', repo: str = None,
             file: str = None) -> Dict[str, Any]:
        """Download a real ONNX model to point the compressor at. Networked."""
        from src import zoo
        return zoo.pull(name, repo, file)

    def inspect(self, name: str = 'bow-64', path: str = None) -> Dict[str, Any]:
        """Everything an .onnx file says about itself — ops, shapes, weights."""
        from src import onnxfile, runtime, zoo
        model = onnxfile.load(path or zoo.ensure(name))
        missing = runtime.unsupported(model)
        return {**model.summary(),
                'nodes': [{'op': n.op_type, 'inputs': n.inputs, 'outputs': n.outputs}
                          for n in model.graph.nodes[:40]],
                'node_count': len(model.graph.nodes),
                'ops_this_runtime_lacks': missing,
                'runnable_here': not missing}

    # ── using the models ─────────────────────────────────────────────

    def embed(self, text: str, name: str = 'bow-64') -> Dict[str, Any]:
        """A sentence to a vector."""
        from src import evaluate, zoo
        vector = evaluate.embed(zoo.load(name), text)
        return {'text': text, 'model': name, 'dimensions': int(vector.size),
                'vector': [round(float(v), 5) for v in vector]}

    def search(self, query: str, name: str = 'bow-64', top: int = 5,
               docs: Any = None) -> Dict[str, Any]:
        """Rank sentences by cosine similarity. The built-in corpus by default."""
        from src import evaluate, zoo
        if isinstance(docs, str):
            docs = [d for d in docs.split('|') if d.strip()]
        return {'query': query, 'model': name,
                'results': evaluate.search(zoo.load(name), query, docs, int(top))}

    def classify(self, text: str, name: str = 'sent-mlp') -> Dict[str, Any]:
        """Positive or negative, and how close the call was."""
        from src import evaluate, zoo
        probs = evaluate.classify(zoo.load(name), [text])[0]
        return {'text': text, 'model': name,
                'label': ['negative', 'positive'][int(probs.argmax())],
                'probabilities': {'negative': round(float(probs[0]), 4),
                                  'positive': round(float(probs[1]), 4)},
                'margin': round(abs(float(probs[1] - probs[0])), 4)}

    def collisions(self, vocab: int = 8192) -> Dict[str, Any]:
        """What the hashing tokenizer costs at this vocabulary size."""
        from src import data, text as tokens
        corpus = [d for _, d in data.DOCS] + [q for q, _ in data.QUERIES]
        return tokens.collisions(corpus, int(vocab))

    # ── compression ──────────────────────────────────────────────────

    def compare(self, name: str = 'bow-64', tensor: str = None) -> Dict[str, Any]:
        """One weight tensor through every method — bytes and error, no graph."""
        from src import quantize, zoo
        model = zoo.load(name)
        weights = model.tensors()
        picked = tensor or max(weights, key=lambda k: weights[k].size)
        return {'model': name, 'tensor': picked, **quantize.compare(weights[picked])}

    def compress(self, name: str = 'bow-64', method: str = 'int8',
                 out: str = None, path: str = None) -> Dict[str, Any]:
        """Compress a model file. The result is a valid .onnx any runtime reads."""
        from src import compress as compressor
        from src import zoo
        source = Path(path) if path else zoo.ensure(name)
        target = Path(out) if out else source.with_name(f'{source.stem}.{method}.onnx')
        report = compressor.compress_file(source, target, method)
        report['tensors'] = [t for t in report['tensors'] if 'error' in t]
        return report

    def sweep(self, name: str = 'bow-64', keep: bool = False) -> Dict[str, Any]:
        """Every method, scored on the model's task. The headline table."""
        from src import evaluate
        return evaluate.sweep(name, keep=bool(keep))

    def check(self, name: str = 'bow-64', all: bool = False) -> Dict[str, Any]:
        """Our numpy runtime against onnxruntime on the same file."""
        from src import check as checker
        return checker.check_all() if all else checker.check(name)

    # ── the lessons ──────────────────────────────────────────────────

    def examples(self, run: str = None) -> Any:
        """List the example scripts, or run one and hand back its output."""
        folder = HERE / 'examples'
        scripts = sorted(p for p in folder.glob('*.py'))
        if run is None:
            return {'run': 'm embed/examples run=01', 'examples': [
                {'id': p.stem.split('_')[0], 'file': p.name,
                 'title': _title(p)} for p in scripts]}
        match = next((p for p in scripts if p.stem.startswith(str(run))
                      or p.name == run), None)
        if match is None:
            raise KeyError(f'no example {run!r} — {[p.stem for p in scripts]}')
        done = subprocess.run([sys.executable, str(match)], cwd=str(HERE),
                              capture_output=True, text=True)
        return {'example': match.name, 'ok': done.returncode == 0,
                'output': done.stdout + done.stderr}

    # ── running it ───────────────────────────────────────────────────

    def serve(self, port: int = None, app_port: int = None,
              background: bool = True) -> Dict[str, Any]:
        port = int(port or os.environ.get('EMBED_PORT', self.port))
        app_port = int(app_port or os.environ.get('EMBED_APP_PORT', self.app_port))
        api = subprocess.Popen([sys.executable, str(HERE / 'src/api.py'),
                                '--port', str(port)], cwd=str(HERE))
        app = subprocess.Popen([sys.executable, str(HERE / 'src/app.py'),
                                '--port', str(app_port),
                                '--api', f'http://127.0.0.1:{port}'], cwd=str(HERE))
        if not background:
            api.wait()
            app.wait()
        return {'api': f'http://localhost:{port}',
                'app': f'http://localhost:{app_port}/embed',
                'pids': [api.pid, app.pid]}

    def kill(self) -> Dict[str, Any]:
        killed = []
        for pattern in ('embed/src/api.py', 'embed/src/app.py'):
            done = subprocess.run(['pkill', '-f', pattern], capture_output=True)
            killed.append({'pattern': pattern, 'signalled': done.returncode == 0})
        return {'killed': killed}

    def test(self) -> Dict[str, Any]:
        done = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests'],
                              cwd=str(HERE), capture_output=True, text=True)
        return {'ok': done.returncode == 0,
                'output': (done.stdout or done.stderr)[-4000:]}


def _title(script: Path) -> str:
    """An example's first docstring line, which is its title."""
    for line in script.read_text().splitlines():
        if line.startswith('"""'):
            return line.strip('"').strip()
    return script.stem
