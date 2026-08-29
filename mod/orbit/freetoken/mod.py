"""freetoken — the fleet's handle on a FreeToken engine, local or not.

    m freetoken/info                              what this is, and what it can reach
    m freetoken/preflight                         can THIS machine host the engine
    m freetoken/install                           ft, into a venv of its own
    m freetoken/boxes                             every engine, and which one is up
    m freetoken/add_box name=gpu url=host:1919    an engine on another machine
    m freetoken/models                            known-good checkpoints + what's on disk
    m freetoken/start model=Qwen/Qwen3.6-35B-A3B  load it — daemon if there is one
    m freetoken/ask "what is a MoE model"         one turn against the default box
    m freetoken/stats                             throughput, latency, VRAM, pools
    m freetoken/resize moe=2000 kv=200k           live pool resize, no restart
    m freetoken/serve                             API :50660 + console :50661

FreeToken (github.com/FlashML-org/FreeToken, Apache-2.0) is an edge-native MoE
serving engine: frontier open-weight models on one consumer GPU, experts held in
host RAM and streamed or computed as the bandwidth of the machine dictates. It
needs Linux, an NVIDIA GPU on r580+, and CUDA 13. This module needs none of
those — its whole client half is stdlib over HTTP, which is the point: the GPU
box and the box you drive it from are usually not the same box.

Every function here is the code the API calls. The API is a transport.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

PORT = int(os.environ.get('FREETOKEN_PORT', 50660))
APP_PORT = int(os.environ.get('FREETOKEN_APP_PORT', 50661))


class Mod:
    description = ('A handle on a FreeToken engine — the edge-native MoE server '
                   'that runs 290B-class open-weight models on one consumer GPU. '
                   'Checks whether this machine qualifies, installs ft into a venv '
                   'of its own, starts and switches models through the control '
                   'daemon or as a supervised process, and drives any number of '
                   'engines over HTTP from a machine with no GPU at all. Re-serves '
                   'the whole fleet an OpenAI- and Anthropic-shaped endpoint on '
                   ':50660, so anything that takes a base_url can point at a '
                   'gaming PC instead of a vendor.')
    path = str(HERE)
    port = PORT
    app_port = APP_PORT
    upstream = 'https://github.com/FlashML-org/FreeToken'
    paper = 'https://arxiv.org/abs/2608.16157'

    # ── what this is ─────────────────────────────────────────────────

    def forward(self, **kwargs: Any) -> Dict[str, Any]:
        """The null call: the module's own card."""
        return self.info()

    def info(self) -> Dict[str, Any]:
        from src import boxes, catalog, install, preflight, state
        gate = preflight.report()
        return {
            'name': 'freetoken',
            'description': self.description,
            'wraps': {'project': 'FreeToken', 'repo': self.upstream,
                      'paper': self.paper, 'license': 'Apache-2.0'},
            'this_machine': {'can_serve_here': gate['can_serve_here'],
                             'verdict': gate['verdict'],
                             'blocking': gate['blocking'],
                             'gpus': gate['gpus']},
            'ft': install.status(),
            'boxes': boxes.listing(),
            'known_good_families': [m['family'] for m in catalog.known()],
            'moe_backends': list(catalog.MOE_BACKENDS),
            'urls': {'api': f'http://localhost:{self.port}',
                     'app': f'http://localhost:{self.app_port}/freetoken',
                     'openai_base': f'http://localhost:{self.port}/v1'},
            'state': str(state.home()),
            'fns': [f for f in dir(self)
                    if not f.startswith('_') and callable(getattr(self, f))],
        }

    def readme(self) -> Optional[str]:
        target = HERE / 'README.md'
        return target.read_text() if target.exists() else None

    def health(self) -> Dict[str, Any]:
        """This module's own health, plus one line per engine it knows about."""
        from src import boxes, client, install
        cards = [client.probe(b, timeout=2.0) for b in boxes.all()]
        return {'ok': True, 'ft_installed': bool(install.ft_bin()),
                'boxes_up': [c['name'] for c in cards if c['up']],
                'boxes_down': [c['name'] for c in cards if not c['up']],
                'engines': cards}

    # ── this machine ─────────────────────────────────────────────────

    def preflight(self) -> Dict[str, Any]:
        """Linux, x86_64, python, GPU, driver r580+, CUDA 13 — found vs wanted."""
        from src import preflight
        return preflight.report()

    def install(self, source: bool = False, accel: bool = True,
                upgrade: bool = False, ref: str = None, dry: bool = False) -> Dict[str, Any]:
        """`uv pip install freetoken[accel]` into ~/.mod/freetoken/venv, detached.

        source=1 clones the repo and installs it editable instead. dry=1 prints
        the commands without running them.
        """
        from src import engine, install
        return install.install(source=engine.truthy(source), accel=engine.truthy(accel),
                               upgrade=engine.truthy(upgrade), ref=ref,
                               dry=engine.truthy(dry))

    def install_log(self, lines: int = 60) -> Dict[str, Any]:
        from src import install
        return install.log(lines)

    def ft(self, *args: Any, timeout: float = 300.0) -> Dict[str, Any]:
        """Any `ft` subcommand, captured. `m freetoken/ft ctl health`."""
        from src import install
        return install.run(list(args), timeout=float(timeout))

    # ── the engines ──────────────────────────────────────────────────

    def boxes(self) -> Dict[str, Any]:
        """Every registered engine, probed. Tokens stay in the state directory."""
        from src import boxes, client
        listing = boxes.listing()
        listing['engines'] = [client.probe(b, timeout=2.5) for b in boxes.all()]
        return listing

    def add_box(self, name: str, url: str = None, daemon: str = None,
                token: str = None, note: str = '', use: bool = False) -> Dict[str, Any]:
        """Register an engine. url is the serve port (:1919), daemon the control one (:1900)."""
        from src import boxes, client, engine
        added = boxes.add(name, url=url, daemon=daemon, token=token, note=note,
                          use=engine.truthy(use))
        return {'added': added, 'probe': client.probe(boxes.get(name), timeout=4.0)}

    def drop_box(self, name: str) -> Dict[str, Any]:
        from src import boxes
        return boxes.drop(name)

    def use_box(self, name: str) -> Dict[str, Any]:
        """Which engine every other call talks to when none is named."""
        from src import boxes
        return boxes.use(name)

    def probe(self, box: str = None) -> Dict[str, Any]:
        """Is it up, what is it serving, can it be steered."""
        from src import boxes, client
        return client.probe(boxes.resolve(box), timeout=6.0)

    # ── models ───────────────────────────────────────────────────────

    def models(self, box: str = None, size: bool = True) -> Dict[str, Any]:
        """The known-good table, what is on this disk, and what a box is serving."""
        from src import boxes, catalog, client, engine
        out = catalog.catalog(size=engine.truthy(size))
        try:
            target = boxes.resolve(box)
            out['served'] = client.models(target)
            out['box'] = target['name']
        except (client.Unreachable, client.Refused, KeyError) as exc:
            out['served'] = None
            out['why_not'] = str(exc)
        return out

    def start(self, model: str, box: str = None, port: int = None,
              force: bool = False, **flags: Any) -> Dict[str, Any]:
        """Load a model. Through the box's daemon if it has one, else here.

        Flags are `ft serve` flags with dashes as underscores — moe_backend,
        moe_cache_auto, memory_ratio, attn, graph, page_size … `--model` is the
        only one that is required; the rest resolve from the checkpoint and GPU.
        """
        from src import boxes, client, engine
        target = boxes.resolve(box)
        argv = engine.serve_argv(model, **({'port': port} if port else {}), **flags)
        passthrough = argv[3:]                       # drop 'serve --model <model>'
        if target.get('daemon'):
            try:
                client.daemon_self(target, timeout=3.0)
            except (client.Unreachable, client.Refused):
                pass
            else:
                result = client.engine_start(target, model, port=port, args=passthrough)
                return {'via': 'daemon', 'box': target['name'],
                        'daemon': target['daemon'], 'model': model,
                        'args': passthrough, 'result': result}
        if _is_local(target):
            return {'via': 'local process', 'box': target['name'],
                    **engine.start(model, force=force, **({'port': port} if port else {}),
                                   **flags)}
        return {'ok': False, 'box': target['name'],
                'why': f'{target["name"]} is remote and has no reachable daemon — '
                       'run `ft daemon` on that machine and register it with '
                       f'daemon=http://host:{boxes.DAEMON_PORT}, or start the model there by hand'}

    def switch(self, model: str, box: str = None, port: int = None,
               **flags: Any) -> Dict[str, Any]:
        """Swap the resident model. Needs the daemon — that is what it is for."""
        from src import boxes, client, engine
        target = boxes.resolve(box)
        argv = engine.serve_argv(model, **flags)
        return {'via': 'daemon', 'box': target['name'],
                'result': client.engine_switch(target, model, port=port, args=argv[3:])}

    def stop(self, box: str = None, force: bool = False) -> Dict[str, Any]:
        """Stop the engine — the daemon's stop if there is one, else this module's."""
        from src import boxes, client, engine
        target = boxes.resolve(box)
        if target.get('daemon'):
            try:
                return {'via': 'daemon', 'result': client.engine_stop(
                    target, force=engine.truthy(force))}
            except (client.Unreachable, client.Refused) as exc:
                if not _is_local(target):
                    return {'ok': False, 'why': str(exc)}
        return {'via': 'local process', **engine.stop(force=force)}

    def server(self, box: str = None) -> Dict[str, Any]:
        """The engine's own account of itself: health, status, footprint."""
        from src import boxes, client, engine
        target = boxes.resolve(box)
        card = client.probe(target, timeout=6.0)
        if _is_local(target):
            card['local_process'] = engine.status()
        return card

    def logs(self, lines: int = 60, box: str = None) -> Dict[str, Any]:
        """The local serve log. Remote engines log where they run."""
        from src import boxes, engine
        target = boxes.resolve(box)
        if not _is_local(target):
            return {'ok': False, 'box': target['name'],
                    'why': 'logs are read from the machine the engine runs on; '
                           'use the daemon SSE stream (`ft daemon logs`) there'}
        return engine.logs(lines)

    # ── using it ─────────────────────────────────────────────────────

    def ask(self, prompt: str, box: str = None, model: str = None,
            max_tokens: int = 512, system: str = None,
            text_only: bool = True) -> Any:
        """One turn. The shortest path from this module to a token."""
        messages = ([{'role': 'system', 'content': system}] if system else [])
        messages.append({'role': 'user', 'content': prompt})
        answer = self.chat(messages, box=box, model=model, max_tokens=max_tokens)
        if not text_only:
            return answer
        try:
            return answer['choices'][0]['message']['content']
        except (KeyError, IndexError, TypeError):
            return answer

    def chat(self, messages: List[Dict[str, str]], box: str = None, model: str = None,
             max_tokens: int = 512, temperature: float = None, **extra: Any) -> Any:
        """OpenAI chat completions against a box. The engine speaks it natively."""
        from src import boxes, client
        return client.chat(boxes.resolve(box), messages, model=model,
                           max_tokens=max_tokens, temperature=temperature, **extra)

    def generate(self, prompt: str = 'Hello', box: str = None,
                 max_tokens: int = 32) -> Any:
        """The raw completion smoke test — no chat template, like `ft ctl generate`."""
        from src import boxes, client
        return client.generate(boxes.resolve(box), prompt, max_tokens=max_tokens)

    def count_tokens(self, prompt: str, box: str = None, model: str = None) -> Any:
        """Anthropic's /v1/messages/count_tokens, served by the engine itself."""
        from src import boxes, client
        return client.count_tokens(boxes.resolve(box),
                                   [{'role': 'user', 'content': prompt}], model=model)

    # ── watching it ──────────────────────────────────────────────────

    def stats(self, box: str = None) -> Any:
        """Throughput, latency, VRAM, pool occupancy."""
        from src import boxes, client
        return client.stats(boxes.resolve(box))

    def cache(self, box: str = None) -> Any:
        """The cache pool table — MoE expert slots, KV, mamba, SWA."""
        from src import boxes, client
        return client.cache_status(boxes.resolve(box))

    def resize(self, moe: Any = None, kv: Any = None, mamba: Any = None,
               swa: Any = None, wait: int = 300, box: str = None) -> Any:
        """Move VRAM between the expert cache and KV, live. No restart, no reload.

        moe/mamba are slots, kv/swa are tokens, and k/m suffixes work:
        `m freetoken/resize moe=3000 kv=200k`.
        """
        from src import boxes, client
        return client.cache_rebuild(boxes.resolve(box), moe=moe, kv=kv, mamba=mamba,
                                    swa=swa, wait=int(wait))

    def requests(self, since: int = 0, limit: int = 50, box: str = None) -> Any:
        """The recent request ring — what has been asked of this engine."""
        from src import boxes, client
        return client.requests(boxes.resolve(box), since=since, limit=limit)

    def bench(self, dtype: str = None, threshold: float = None,
              timeout: float = 900.0) -> Dict[str, Any]:
        """`ft bench bw` — host RAM vs PCIe, once per machine. Picks hybrid or offload.

        The profile is keyed on expert format and GPU name, so one taken on
        other hardware is ignored rather than misapplied.
        """
        from src import install
        argv = ['bench', 'bw']
        if dtype:
            argv += ['--dtype', str(dtype)]
        if threshold is not None:
            argv += ['--threshold', str(threshold)]
        return install.run(argv, timeout=float(timeout))

    def profile(self, box: str = None) -> Any:
        """The cached bandwidth profile a box is deciding its MoE backend from."""
        from src import boxes, client
        return client.bench_profile(boxes.resolve(box))

    def checkpoint(self, model: str, out: str, dtype: str = None,
                   moe_backend: str = None, timeout: float = 7200.0) -> Dict[str, Any]:
        """Convert an HF checkpoint to FTW so the next load skips the conversion."""
        from src import install
        argv = ['checkpoint', '--model', str(model), '--out', str(out)]
        if dtype:
            argv += ['--dtype', str(dtype)]
        if moe_backend:
            argv += ['--moe-backend', str(moe_backend)]
        return install.run(argv, timeout=float(timeout))

    def launch(self, agent: str = 'claude', box: str = None,
               dry: bool = True) -> Dict[str, Any]:
        """Point a coding agent at an engine. Defaults to a dry run — it edits config.

        `ft launch` writes the agent's provider config and clears cloud API keys
        from the child environment so it cannot fall back to a paid endpoint.
        """
        from src import boxes, engine, install
        target = boxes.resolve(box)
        argv = ['launch', str(agent), '--server', target['url']]
        if engine.truthy(dry):
            argv.append('--dry-run')
        else:
            argv.append('--yes')
        return {'box': target['name'], **install.run(argv, timeout=900)}

    # ── running this module ──────────────────────────────────────────

    def serve(self, port: int = None, app_port: int = None,
              background: bool = True) -> Dict[str, Any]:
        """This module's API and console — not the engine. `m freetoken/start` is that."""
        from src import engine
        port = int(port or PORT)
        app_port = int(app_port or APP_PORT)
        api = subprocess.Popen([sys.executable, str(HERE / 'src/api.py'),
                                '--port', str(port)], cwd=str(HERE))
        app = subprocess.Popen([sys.executable, str(HERE / 'src/app.py'),
                                '--port', str(app_port),
                                '--api', f'http://127.0.0.1:{port}'], cwd=str(HERE))
        if not engine.truthy(background):
            api.wait()
            app.wait()
        return {'api': f'http://localhost:{port}',
                'app': f'http://localhost:{app_port}/freetoken',
                'openai_base': f'http://localhost:{port}/v1',
                'pids': [api.pid, app.pid]}

    def kill(self) -> Dict[str, Any]:
        """Stop this module's API and console. Leaves any engine alone."""
        killed = []
        for pattern in ('freetoken/src/api.py', 'freetoken/src/app.py'):
            done = subprocess.run(['pkill', '-f', pattern], capture_output=True)
            killed.append({'pattern': pattern, 'signalled': done.returncode == 0})
        return {'killed': killed}

    def test(self) -> Dict[str, Any]:
        done = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests'],
                              cwd=str(HERE), capture_output=True, text=True)
        return {'ok': done.returncode == 0,
                'output': (done.stdout or done.stderr)[-4000:]}


def _is_local(box: Dict[str, Any]) -> bool:
    url = (box.get('url') or '')
    return any(h in url for h in ('127.0.0.1', 'localhost', '::1', '0.0.0.0'))
