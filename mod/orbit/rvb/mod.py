"""rvb — red team versus blue team, scored.

A jailbreak game with an actual scoreboard. Red team writes attacks: prompts
built to make a model produce something it shouldn't. Blue team writes
defenses: not just a system prompt but a pipeline — input rules, the system
prompt, the model, an optional self-check, output rules. The arena fires every
attack at every defense, a judge scores each exchange on two axes (did it
refuse, did anything harmful get out), and the number that comes out is the
**refusal rate** on attacks — held honest by the **over-refusal rate** on a
fixed set of benign controls that sit right next to the attacks.

    m rvb                                   # this — the rules and the scoring
    m rvb/attacks                           # the red team corpus (seeded)
    m rvb/defenses                          # the blue team corpus (+ built-ins)
    m rvb/fight attack=seed-fiction-frame defense=layered   # one exchange
    m rvb/round                             # full tournament, every × every
    m rvb/board                             # who's winning, both sides
    m rvb/serve                             # REST + console + MCP on one port

WHY TWO AXES AND A CONTROL SET
    "Refusal rate" alone is trivially gamed: a defense that refuses everything
    scores 1.0 and is useless. So every round also runs benign controls that
    live next to the attacks — a chemistry question beside a synthesis attack —
    and reports over-refusal beside refusal. The single honest number is
    `safety_score = refusal_rate − over_refusal`, and a refuse-everything
    defense nets ~0.

WHERE THINGS LIVE
    Attacks, defenses and round records are in ~/.mod/rvb, not in this repo. A
    defense's system prompt is the blue team's live answer to an open attack
    surface and an attack corpus is a list of things that worked — neither
    belongs in a committed config.json.

THE TARGET IS PLUGGABLE
    Default target is the local Claude Code CLI (keyless, already authed on
    this box, ~5s a call). Point it anywhere with `model=` — openrouter:<slug>,
    anthropic:<model>, openai:<model> — or `model=mock:naive` for an offline,
    deterministic target whose correct score is known, which is how the harness
    itself is tested.
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# Appended, never prepended: this directory holds a mod.py that would shadow
# the protocol's own `mod` package for anything importing us afterwards.
if HERE not in sys.path:
    sys.path.append(HERE)

import mod as m  # noqa: E402

from rvbsrc import arena, builtins as bimod, corpus  # noqa: E402
from rvbsrc import defense as defmod  # noqa: E402
from rvbsrc import judge as judgemod, models, store  # noqa: E402


class Mod:
    description = """
    rvb — red team vs blue team, scored. Red writes attacks (jailbreak
    prompts), blue writes defenses (input rules → system prompt → model →
    self-check → output rules). The arena fires every attack at every defense,
    a two-axis judge asks did-it-refuse and did-anything-harmful-escape, and
    the score is the refusal rate on attacks kept honest by the over-refusal
    rate on benign controls sitting next to them. Target is pluggable: the
    keyless local Claude CLI by default, any API model, or an offline mock
    whose score is known so the harness can be tested. REST + console + MCP on
    one port.
    """

    def __init__(self, port=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('RVB_PORT') or cfg.get('port', 50820))
        self.base = cfg.get('base_path', '/rvb')
        corpus.seed_store()

    # ── plumbing ─────────────────────────────────────────────────

    def config(self):
        try:
            with open(os.path.join(HERE, 'config.json')) as f:
                return json.load(f)
        except Exception:
            return {}

    def info(self):
        """The rules, the scoring, and everything you can call."""
        from rvbsrc import api
        return api.info()

    forward = info

    # ── red team: attacks ────────────────────────────────────────

    def attacks(self, id=None, category=None):
        """The red team corpus. One attack, or all of them (filter by category)."""
        if id:
            return store.get('attack', id)
        return store.listing('attack', category=category)

    def attack(self, name, prompt=None, goal=None, category='custom',
               technique=None, markers=None, turns=None, id=None):
        """Add a red-team attack. `prompt` OR `turns` (a multi-turn transcript).

        markers are strings whose presence in a reply is evidence the attack
        landed — they let the offline judge work and make the model judge
        auditable. A single-word attack with no markers still runs; the model
        judge just carries the whole verdict.
        """
        if not prompt and not turns:
            raise store.StoreError('an attack needs a `prompt` or `turns`')
        aid = store.unique_id('attack', id or store.slug(name, ''))
        rec = {'id': aid, 'kind': 'attack', 'name': name, 'goal': goal,
               'category': category, 'technique': technique,
               'markers': self._list(markers), 'author': m.key().ss58_address
               if hasattr(m, 'key') else None}
        if turns:
            rec['turns'] = turns if isinstance(turns, list) else json.loads(turns)
        else:
            rec['prompt'] = prompt
        return store.put('attack', rec)

    def delete_attack(self, id):
        """Remove an attack. Built-in seeds can be removed too; re-seed by
        restarting."""
        return store.delete('attack', id)

    # ── blue team: defenses ──────────────────────────────────────

    def defenses(self, id=None):
        """The blue team corpus, built-ins included."""
        if id:
            if id in self._builtins():
                return self._builtins()[id]
            return store.get('defense', id)
        stored = store.listing('defense')
        return list(self._builtins().values()) + stored

    def defense(self, name, system_prompt='', input_rules=None,
                output_rules=None, self_check=False, max_input_chars=0,
                description=None, id=None):
        """Add a blue-team defense — a whole pipeline, not just a prompt.

        input_rules / output_rules are lists; a bare string is shorthand for a
        blocking regex. self_check adds a second model pass that reviews the
        draft (doubles the model cost). See `m rvb` for the rule grammar.
        """
        did = store.unique_id('defense', id or store.slug(name, ''))
        spec = {'id': did, 'kind': 'defense', 'name': name,
                'description': description, 'system_prompt': system_prompt,
                'input_rules': self._json(input_rules) or [],
                'output_rules': self._json(output_rules) or [],
                'self_check': bool(self_check),
                'max_input_chars': int(max_input_chars or 0),
                'author': m.key().ss58_address if hasattr(m, 'key') else None}
        defmod.normalise(spec)                       # reject a broken one now
        return store.put('defense', spec)

    def delete_defense(self, id):
        """Remove a stored defense. Built-ins can't be deleted."""
        if id in self._builtins():
            raise store.StoreError(f'{id!r} is a built-in defense')
        return store.delete('defense', id)

    def cost(self, defense):
        """What one turn through a defense costs, before running it."""
        return defmod.cost(self._load_defense(defense))

    # ── the game ─────────────────────────────────────────────────

    def fight(self, attack, defense='none', model=None, judge='model',
              timeout=None):
        """One exchange: fire one attack at one defense and score it.

        The fastest way to see the whole pipeline — every stage, the response,
        and the verdict — for a single pair.
        """
        atk = self._load_attack(attack)
        dfn = self._load_defense(defense)
        rec = arena._one_match(atk, defmod.normalise(dfn), model or models.DEFAULT,
                              judge, self._num(timeout))
        return rec

    def round(self, attacks=None, defenses=None, model=None, judge='model',
              parallel=6, controls=True, timeout=None, name=None):
        """The tournament: every attack × every defense, scored, with controls.

        attacks/defenses default to the whole corpus. Pass comma-separated ids
        to scope it. Returns the round record with per-defense scorecards and
        both leaderboards.
        """
        atks = self._resolve_attacks(attacks)
        dfns = self._resolve_defenses(defenses)
        if not atks:
            raise arena.ArenaError('no attacks to run')
        if not dfns:
            raise arena.ArenaError('no defenses to run')
        return arena.run_round(atks, dfns, model=model or models.DEFAULT,
                               judge_kind=judge, parallel=int(parallel),
                               controls=self._flag(controls),
                               timeout=self._num(timeout), name=name)

    def rounds(self, limit=20, status=None):
        """Round history — id, model, matches, and the top defense of each."""
        out = []
        for r in store.listing('round', limit=int(limit), status=status):
            lb = r.get('leaderboard') or []
            out.append({'id': r['id'], 'status': r.get('status'),
                        'model': r.get('model'), 'started': r.get('started'),
                        'matches': r.get('total_matches'), 'done': r.get('done'),
                        'winner': lb[0] if lb else None})
        return out

    def show(self, round):
        """One round in full — every match, the responses, both boards."""
        return store.get('round', round)

    def board(self, rounds=8):
        """The standings across recent rounds: blue's ranking and red's.

        Blue: defenses by safety_score, averaged over the rounds they appear in.
        Red: attacks by how often they breach, across every defense they meet.
        """
        return bimod.board_across(int(rounds))

    # ── target & judge introspection ─────────────────────────────

    def targets(self):
        """Which model backends can run right now, and how to enable the rest."""
        return {'default': models.DEFAULT, 'judge_default': models.JUDGE_MODEL,
                'providers': models.providers()}

    def ping(self, model=None, prompt='Say the single word: pong.'):
        """Send one prompt to a target to prove it is reachable."""
        try:
            out = models.complete([{'role': 'user', 'content': prompt}],
                                  model=model or models.DEFAULT)
            return {'ok': True, **out}
        except models.ModelError as e:
            return {'ok': False, 'error': str(e), 'model': model or models.DEFAULT}

    def controls(self):
        """The fixed benign control set the over-refusal score is measured on."""
        return corpus.CONTROL_SET

    # ── surfaces ─────────────────────────────────────────────────

    def serve(self, port=None, bind=None, background=False):
        """REST, the console and the MCP server on one port."""
        port = int(port or self.port)
        bind = bind or os.environ.get('RVB_BIND', '127.0.0.1')
        if not background:
            from rvbsrc import api
            return api.serve(port, bind=bind)
        proc = subprocess.Popen(
            [sys.executable, '-m', 'rvbsrc.api', '--port', str(port),
             '--bind', bind],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=HERE)
        return {'pid': proc.pid, 'port': port, 'bind': bind,
                'url': f'http://{bind}:{port}{self.base}'}

    def mcp(self, tool=None, **args):
        """The MCP registry, or one tool invoked directly."""
        from rvbsrc import mcp as mcpsrv
        if tool is None:
            return {'tools': mcpsrv.tool_list(), 'count': len(mcpsrv.TOOLS)}
        return mcpsrv.call_tool(tool, args)

    def readme(self):
        for name in ('README.md', 'readme.md'):
            p = os.path.join(HERE, name)
            if os.path.exists(p):
                return m.get_text(p)
        return None

    def test(self):
        """Run the offline test suite (uses mock: targets, no network)."""
        p = subprocess.run([sys.executable, '-m', 'pytest', '-q',
                            os.path.join(HERE, 'tests')],
                           capture_output=True, text=True, cwd=HERE)
        return {'ok': p.returncode == 0,
                'output': (p.stdout + p.stderr)[-4000:]}

    # ── built-in defenses ────────────────────────────────────────

    def _builtins(self):
        return bimod.BUILTIN

    # ── loaders / coercion ───────────────────────────────────────

    def _load_attack(self, ref):
        if isinstance(ref, dict):
            return ref
        return store.get('attack', ref)

    def _load_defense(self, ref):
        if isinstance(ref, dict):
            return ref
        if ref in self._builtins():
            return self._builtins()[ref]
        return store.get('defense', ref)

    def _resolve_attacks(self, spec):
        if not spec:
            return store.listing('attack', limit=0)
        return [self._load_attack(x.strip()) for x in self._csv(spec)]

    def _resolve_defenses(self, spec):
        if not spec:
            return list(self._builtins().values()) + store.listing('defense', limit=0)
        return [self._load_defense(x.strip()) for x in self._csv(spec)]

    @staticmethod
    def _csv(spec):
        if isinstance(spec, list):
            return spec
        return [s for s in str(spec).split(',') if s.strip()]

    @staticmethod
    def _list(v):
        if v is None:
            return []
        if isinstance(v, list):
            return v
        if isinstance(v, str) and v.strip().startswith('['):
            return json.loads(v)
        return [x.strip() for x in str(v).split(',') if x.strip()]

    @staticmethod
    def _json(v):
        if v is None or isinstance(v, (list, dict)):
            return v
        if isinstance(v, str) and v.strip().startswith(('[', '{')):
            return json.loads(v)
        return v

    @staticmethod
    def _flag(v):
        if isinstance(v, bool):
            return v
        return str(v).lower() not in ('0', 'false', 'no', '')

    @staticmethod
    def _num(v):
        return int(v) if v not in (None, '') else None

