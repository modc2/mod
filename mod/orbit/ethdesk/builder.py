"""
The agent door: hand a contract project to the **build module** and get the
edit back as a new version — verified by the same tests everything else here
runs.

Three ideas, in the order they matter:

    a folder of code    a project normally lives in the store as a JSON bundle
                        (a CID per version). An agent does not edit JSON
                        bundles; it edits files. So a project can be
                        *materialized* as a mod-shaped folder — config.json,
                        contracts/*.sol, tests/*.json, a CLAUDE.md that
                        explains the rules — exactly the way the chain module
                        lays its agent workspaces out. The folder is a
                        *checkout*, never the source of truth: syncing it back
                        writes a new version through projects.py, so the CID
                        history keeps recording what the agent did.

    the build module    orbit/build is the fleet's dev console — it runs
                        Claude against a working directory, streams the steps,
                        bills the caller, and sandboxes non-owners. This file
                        does not spawn any agent itself. It submits a job to
                        build with the CALLER'S own token (the store_link
                        pattern: no credentials owned here), pointing at the
                        materialized folder, and polls the job like anybody
                        else would. Build's whitelist, credits and sandbox
                        apply to the person asking, not to this box.

    tests are the gate  a suite in tests/ is not documentation — harness.py
                        deploys the contract to a chain and runs every case
                        for real. The agent is told, in its system prompt and
                        in the folder's CLAUDE.md, that changed logic without
                        a covering case is an unfinished job. After sync the
                        module compiles the result itself (free, local), and
                        `verify` runs the suites on a testnet with the
                        caller's account — the same wall every human edit
                        goes through.

The workspace root defaults to /var/tmp rather than ~/.mod for one concrete
reason: build runs a non-owner's job under an unprivileged uid, and /root is
0700 — a workspace the sandboxed agent cannot even traverse into would make
this an owner-only feature by accident.
"""
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

import compiler
import ledger
import projects
from projects import ProjectError

DIR = Path(__file__).resolve().parent
DEFAULT_BUILD = 'http://127.0.0.1:8890'
DEFAULT_ACTIVATOR = 'http://127.0.0.1:9000'
BUILD_MODULE = os.environ.get('ETH_BUILD_MODULE', 'build')
TIMEOUT = float(os.environ.get('ETH_BUILD_TIMEOUT', 30))
WAKE_TIMEOUT = float(os.environ.get('ETH_WAKE_TIMEOUT', 45))
DEFAULT_MODEL = os.environ.get('ETH_BUILD_MODEL', 'claude-sonnet-5')

WORKROOT = Path(os.environ.get('ETH_BUILD_WORKROOT',
                               '/var/tmp/ethdesk/build/mods'))

MAX_SUITE_FILES = 20
MAX_SUITE_BYTES = 256_000
OUTPUT_TAIL = 6000

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner TEXT NOT NULL,
  project_id INTEGER,
  slug TEXT,
  prompt TEXT NOT NULL,
  model TEXT,
  job_id TEXT NOT NULL,
  workspace TEXT NOT NULL,
  status TEXT NOT NULL,
  synced INTEGER NOT NULL DEFAULT 0,
  result TEXT,
  created INTEGER NOT NULL,
  updated INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS agent_runs_owner ON agent_runs(owner, created DESC);
"""

# What a brand-new agent project starts from when no template is named: the
# smallest thing that compiles, so the first job's diff is all signal.
SEED = """// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// Replace this with the contract the prompt asks for.
contract Draft {
}
"""


class BuildError(Exception):
    """The build module said no (or did not answer). Carries its status."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status
        self.message = message


def connect():
    conn = ledger.connect()
    conn.executescript(SCHEMA)
    return conn


def build_url() -> str:
    env = os.environ.get('ETH_BUILD_URL')
    if env:
        return env.rstrip('/')
    return DEFAULT_BUILD


def api_url() -> str:
    """This module's own API, as a process on this box reaches it."""
    try:
        cfg = json.loads((DIR / 'config.json').read_text())
        return f"http://127.0.0.1:{cfg.get('port', 50750)}"
    except Exception:
        return 'http://127.0.0.1:50750'


# ── the bridge to orbit/build ────────────────────────────────────────

class BuildLink:
    """Same shape as StoreLink, same rule: **owns no credentials.**

    Submitting a job forwards the caller's token verbatim, so build's own
    auth, credits and sandbox decide what that address may run. Reading a job
    back needs no token — build's task ledger is world-readable by design.
    """

    def __init__(self, url: Optional[str] = None, timeout: float = TIMEOUT,
                 activator: Optional[str] = None):
        self.url = (url or build_url()).rstrip('/')
        env = os.environ.get('ETH_ACTIVATOR_URL')
        self.activator = ((activator if activator is not None
                           else (env if env is not None else DEFAULT_ACTIVATOR))
                          .rstrip('/'))
        self.timeout = timeout

    @staticmethod
    def _bearer(token: Optional[str]) -> Dict[str, str]:
        token = (token or '').strip()
        if not token:
            raise BuildError('no protocol token: sign in first', 401)
        if token.lower().startswith('bearer '):
            token = token[7:].strip()
        return {'Authorization': f'Bearer {token}'}

    def _wake(self):
        """Knock on the activator so a slept build module comes back up."""
        if not self.activator:
            return False, 'no activator configured to start it'
        try:
            r = requests.get(f'{self.activator}/api/{BUILD_MODULE}/health',
                             timeout=WAKE_TIMEOUT)
        except requests.RequestException:
            return False, f'the activator at {self.activator} did not answer'
        if not r.ok:
            return False, f'the activator could not start it ({r.status_code})'
        return True, ''

    def _send(self, method: str, path: str, **kw):
        url = f'{self.url}{path}'
        try:
            return requests.request(method, url, **kw)
        except requests.ConnectionError:
            woken, why = self._wake()
            if not woken:
                raise BuildError(f'the build module is not running at '
                                 f'{self.url} — {why}', 503)
        try:
            return requests.request(method, url, **kw)
        except requests.RequestException as e:
            raise BuildError(f'the build module woke but is not answering at '
                             f'{self.url}: {e}', 503)

    @staticmethod
    def _detail(r) -> str:
        try:
            body = r.json()
            return str(body.get('error') or body.get('detail') or r.text[:400])
        except Exception:
            return r.text[:400]

    def _call(self, method: str, path: str, token: Optional[str] = None,
              **kw) -> Any:
        headers = self._bearer(token) if token else {}
        try:
            r = self._send(method, path, headers=headers,
                           timeout=kw.pop('timeout', self.timeout), **kw)
        except requests.RequestException as e:
            raise BuildError(f'build unreachable at {self.url}: {e}', 503)
        if r.status_code >= 400:
            raise BuildError(f'build {path} → {r.status_code}: '
                             f'{self._detail(r)}', r.status_code)
        try:
            return r.json()
        except ValueError:
            return {'raw': r.text[:2000]}

    def health(self) -> Dict[str, Any]:
        return self._call('GET', '/health')

    def submit(self, token: str, prompt: str, work_dir: str,
               model: Optional[str] = None,
               system_prompt: Optional[str] = None) -> Dict[str, Any]:
        return self._call('POST', '/jobs', token, json={
            'prompt': prompt,
            'model': model or DEFAULT_MODEL,
            'work_dir': work_dir,
            'system_prompt': system_prompt,
        }, timeout=max(self.timeout, 60))

    def job(self, job_id: str) -> Dict[str, Any]:
        return self._call('GET', f'/jobs/{job_id}')

    def cancel(self, token: str, job_id: str) -> Dict[str, Any]:
        return self._call('POST', f'/jobs/{job_id}/cancel', token)


LINK = BuildLink()


# ── the workspace: a project as a mod-shaped folder ──────────────────

def _suite_slug(suite: Dict[str, Any], index: int) -> str:
    base = projects.slugify(str(suite.get('name') or f'suite-{index + 1}'))
    return f'{index + 1:02d}-{base}.json'


def workspace_path(owner: str, slug: str) -> Path:
    owner = re.sub(r'[^0-9a-fx]', '', (owner or '').lower()) or 'anon'
    slug = projects.slugify(slug)
    return WORKROOT / owner / slug


def _open_perms(path: Path):
    """Let build's sandboxed (non-owner) jobs edit what root wrote.

    Contract source is not a secret — the bundle is one `share` away from
    public — and a workspace nobody's job can write into is a dead feature.
    """
    for base, dirs, files in os.walk(path):
        os.chmod(base, 0o777)
        for f in files:
            os.chmod(os.path.join(base, f), 0o666)
    os.chmod(path, 0o777)


def materialize(owner: str, row: Dict[str, Any]) -> Dict[str, Any]:
    """Write a project out as a folder of code, and say what was written.

    The folder is a mod: a config.json that names it, sources under
    contracts/, suites under tests/. Re-materializing an existing workspace
    replaces contracts/ and tests/ wholesale — the store bundle is the truth,
    and a stale leftover file would ride into the next sync as a ghost edit.
    """
    ws = workspace_path(owner, row['slug'])
    contracts = ws / 'contracts'
    tests = ws / 'tests'
    for sub in (contracts, tests):
        if sub.exists():
            for old in sub.rglob('*'):
                if old.is_file():
                    old.unlink()
        sub.mkdir(parents=True, exist_ok=True)

    files = row.get('files') or {}
    for path, text in files.items():
        target = contracts / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text or '')

    suites = row.get('tests') or []
    for i, suite in enumerate(suites):
        (tests / _suite_slug(suite, i)).write_text(
            json.dumps(suite, indent=2) + '\n')

    (ws / 'config.json').write_text(json.dumps({
        'name': row['slug'],
        'description': f"eth contract project — {row['name']}",
        'protocol': projects.KIND,
        'anchor': f"contracts/{row.get('entry') or next(iter(files), '')}",
        'entry': row.get('entry'),
        'project': {'owner': owner, 'id': row.get('id'),
                    'slug': row['slug'], 'cid': row.get('cid')},
        'ethdesk': {'api': api_url()},
        'written': int(time.time()),
    }, indent=2) + '\n')

    (ws / 'CLAUDE.md').write_text(_claude_md(row))
    _open_perms(ws)
    return {
        'workspace': str(ws),
        'files': sorted(f'contracts/{p}' for p in files),
        'suites': sorted(_suite_slug(s, i) for i, s in enumerate(suites)),
    }


def _claude_md(row: Dict[str, Any]) -> str:
    """The rules of the folder, written *into* the folder.

    The system prompt says what this run wants; this file says what any run
    must respect — it survives in the checkout, so a human opening the folder
    reads the same contract the agent did.
    """
    return f"""# {row['name']} — an ethdesk contract project

This folder is a **checkout** of an eth contract project, materialized so an
agent can edit it. The source of truth is the project bundle in the store
(a CID per version); when your run ends, ethdesk reads this folder back and
saves it as a new version. Nothing outside this folder is yours to touch.

## Layout
- `contracts/*.sol` — the Solidity sources. `config.json` names the entry file.
- `tests/*.json` — test suites. These are not examples: the ethdesk harness
  deploys the contract to a real chain and runs every case as a real call or
  a real signed transaction. **They are the verification of the contract
  logic.** Any behavior you add or change must be covered by a case, and a
  case must assert something (`expect`, `expect_gt`, `expect_event`,
  `expect_revert`) — a suite of bare calls verifies nothing.
- `config.json` — the project's identity. Do not edit the `project` block.

## A suite
```json
{{
  "name": "erc20 basics",
  "contract": "Token",
  "args": ["Test", "TST", 1000],
  "cases": [
    {{"name": "name is set",  "fn": "name",      "expect": "Test"}},
    {{"name": "minted to me", "fn": "balanceOf", "args": ["$deployer"], "expect_gt": 0}},
    {{"name": "transfer",     "fn": "transfer",  "args": ["$zero", 1], "expect_event": "Transfer"}},
    {{"name": "overspend",    "fn": "transfer",  "args": ["$zero", "10**60"], "expect_revert": true}}
  ]
}}
```
Whether a case is a free call or a signed transaction is read off the ABI —
never repeat it in the case. Placeholders: `$deployer`, `$contract`, `$zero`,
`$account:<name>`.

## Checking your work
Compiling is free and needs no account. From this folder:
```bash
python3 - <<'EOF'
import json, pathlib, urllib.request
src = {{p.name: p.read_text() for p in pathlib.Path('contracts').glob('**/*.sol')}}
req = urllib.request.Request('{api_url()}/compile',
    json.dumps({{'sources': src}}).encode(), {{'Content-Type': 'application/json'}})
print(urllib.request.urlopen(req).read().decode()[:2000])
EOF
```
Fix every compile error before you finish. You cannot deploy or run the
suites yourself — they need the owner's keys — so the compile check plus
honest, assertive test cases are how your work gets verified after you.
"""


def collect(ws: Path) -> Dict[str, Any]:
    """Read a workspace folder back into a bundle's parts, validated hard.

    Everything in the folder was written by an agent (or a person) this
    module did not watch, so it gets the same scrutiny as bytes off the wire:
    the file caps from projects.py, suites that must at least be JSON objects
    with cases, and a name/entry read from config.json only if still sane.
    """
    ws = Path(ws)
    contracts = ws / 'contracts'
    if not contracts.is_dir():
        raise ProjectError(f'{ws} has no contracts/ directory — '
                           'not a project workspace')
    files: Dict[str, str] = {}
    for path in sorted(contracts.rglob('*.sol')):
        rel = str(path.relative_to(contracts))
        files[rel] = path.read_text()
    files = projects._clean_files(files)

    suites: List[dict] = []
    suite_files = sorted((ws / 'tests').glob('*.json')) if (ws / 'tests').is_dir() else []
    if len(suite_files) > MAX_SUITE_FILES:
        raise ProjectError(f'{len(suite_files)} suite files is more than a '
                           f'project holds ({MAX_SUITE_FILES})')
    for path in suite_files:
        raw = path.read_text()
        if len(raw.encode()) > MAX_SUITE_BYTES:
            raise ProjectError(f'tests/{path.name} is larger than '
                               f'{MAX_SUITE_BYTES} bytes')
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ProjectError(f'tests/{path.name} is not valid JSON: {e}')
        for suite in (parsed if isinstance(parsed, list) else [parsed]):
            if not isinstance(suite, dict) or not isinstance(
                    suite.get('cases'), list):
                raise ProjectError(f'tests/{path.name} is not a suite '
                                   '(an object with a `cases` list)')
            suites.append(suite)

    entry, name = None, None
    try:
        cfg = json.loads((ws / 'config.json').read_text())
        entry = cfg.get('entry')
        name = str(cfg.get('name') or '')[:120] or None
    except Exception:
        pass
    if entry not in files:
        entry = None
    return {'files': files, 'tests': suites, 'entry': entry, 'name': name}


# ── runs: submit, poll, verify ───────────────────────────────────────

def _seed_project(owner: str, token: Optional[str], name: Optional[str],
                  template: Optional[str]) -> Dict[str, Any]:
    if template:
        import catalog
        try:
            got = catalog.describe(template)
            text = catalog.source(template)
        except FileNotFoundError as e:
            raise ProjectError(str(e))
        files = {f"{got.get('contract') or got['name']}.sol": text}
        name = name or got.get('contract') or got['name']
    else:
        files = {'Draft.sol': SEED}
    return projects.save(owner, token, name=name or 'agent contract',
                         files=files, note='seeded for an agent run')


def _system_note(ws: str, row: Dict[str, Any]) -> str:
    return (f"You are editing the ethdesk contract project "
            f"'{row['name']}' checked out at {ws}. Read {ws}/CLAUDE.md first "
            f"and follow it: sources in contracts/, JSON test suites in "
            f"tests/ (they run against a real chain and are the verification "
            f"of the contract logic — cover what you change, assert on every "
            f"case), never edit the project block of config.json, never "
            f"touch files outside this folder. Compile-check your Solidity "
            f"with the API named in CLAUDE.md before finishing.")


def run(owner: str, token: Optional[str], prompt: str,
        project: Optional[str] = None, name: Optional[str] = None,
        template: Optional[str] = None, model: Optional[str] = None,
        link: Optional[BuildLink] = None) -> Dict[str, Any]:
    """Materialize, submit to build, remember the run.

    With `project` this edits an existing one; without, it seeds a new
    project first (from a template or a compilable stub) so the agent always
    works on something that exists — a run that fails still has a project to
    show for it, and the version history starts before the agent's first
    keystroke.
    """
    owner = (owner or '').lower()
    prompt = (prompt or '').strip()
    if not prompt:
        raise ProjectError('what should the agent do? `prompt` is empty')

    if project:
        row = projects.get(owner, project)
    else:
        row = _seed_project(owner, token, name, template)

    ws = materialize(owner, row)
    job = (link or LINK).submit(token, prompt, ws['workspace'], model=model,
                                system_prompt=_system_note(ws['workspace'], row))
    now = int(time.time())
    with connect() as conn:
        cursor = conn.execute(
            'INSERT INTO agent_runs (owner, project_id, slug, prompt, model, '
            'job_id, workspace, status, created, updated) '
            'VALUES (?,?,?,?,?,?,?,?,?,?)',
            (owner, row['id'], row['slug'], prompt,
             job.get('model') or model or DEFAULT_MODEL,
             job['id'], ws['workspace'], 'running', now, now))
        run_id = cursor.lastrowid
    return {'run_id': run_id, 'job_id': job['id'], 'status': 'running',
            'project': {'id': row['id'], 'slug': row['slug'],
                        'name': row['name']},
            'workspace': ws}


def _get_run(owner: str, run_id: int) -> Dict[str, Any]:
    with connect() as conn:
        row = conn.execute('SELECT * FROM agent_runs WHERE id=? AND owner=?',
                           (int(run_id), (owner or '').lower())).fetchone()
    if row is None:
        raise ProjectError(f'no agent run {run_id} of yours')
    out = dict(row)
    try:
        out['result'] = json.loads(out['result']) if out.get('result') else None
    except json.JSONDecodeError:
        out['result'] = None
    return out


def _update_run(run_id: int, status: str, synced: Optional[bool] = None,
                result: Optional[dict] = None):
    with connect() as conn:
        sets, vals = ['status=?', 'updated=?'], [status, int(time.time())]
        if synced is not None:
            sets.append('synced=?')
            vals.append(int(synced))
        if result is not None:
            sets.append('result=?')
            vals.append(json.dumps(result))
        vals.append(int(run_id))
        conn.execute(f'UPDATE agent_runs SET {", ".join(sets)} WHERE id=?',
                     vals)


def _sync_back(owner: str, token: Optional[str], run: Dict[str, Any],
               job: Dict[str, Any]) -> Dict[str, Any]:
    """Folder → new project version, plus the free half of verification.

    Runs even when the job failed or was cancelled — the chain module learned
    this the hard way: an agent that timed out after writing something useful
    should not have its work thrown away with its exit code.
    """
    result: Dict[str, Any] = {'job_status': job.get('status'),
                              'cost_usd': job.get('cost_usd')}
    try:
        got = collect(Path(run['workspace']))
    except ProjectError as e:
        result['sync'] = {'ok': False, 'error': str(e)}
        return result

    current = projects.find(owner, str(run['project_id'])) or {}
    unchanged = (got['files'] == (current.get('files') or {})
                 and got['tests'] == (current.get('tests') or []))
    if unchanged:
        result['sync'] = {'ok': True, 'changed': False}
    else:
        saved = projects.save(
            owner, token, project=str(run['project_id']),
            name=got.get('name'), files=got['files'], entry=got.get('entry'),
            tests=got['tests'],
            note=f"agent: {run['prompt'][:100]}")
        result['sync'] = {'ok': True, 'changed': True,
                          'cid': saved.get('cid'),
                          'store': saved.get('store')}

    try:
        compiled = compiler.compile_sources(got['files'])
        deployable = [c['name'] for c in compiled['contracts']
                      if c.get('deployable')]
        result['compile'] = {'ok': True, 'contracts': deployable,
                             'warnings': len(compiled.get('warnings') or [])}
    except Exception as e:                    # CompileError or a missing solc
        result['compile'] = {'ok': False, 'error': str(e)[:2000]}

    result['tests'] = {'suites': len(got['tests']),
                       'cases': sum(len(s.get('cases') or [])
                                    for s in got['tests'])}
    return result


def poll(owner: str, token: Optional[str], run_id: int,
         link: Optional[BuildLink] = None) -> Dict[str, Any]:
    """Where a run stands — and the sync, exactly once, when the job ends."""
    run = _get_run(owner, run_id)
    try:
        job = (link or LINK).job(run['job_id'])
    except BuildError as e:
        run['build_error'] = e.message
        return run

    terminal = job.get('status') in ('completed', 'failed', 'cancelled')
    if terminal and not run['synced']:
        result = _sync_back(owner, token, run, job)
        ok = (job.get('status') == 'completed'
              and result.get('sync', {}).get('ok', False))
        _update_run(run_id, 'done' if ok else 'failed', synced=True,
                    result=result)
        run = _get_run(owner, run_id)
    elif not terminal and run['status'] != 'running':
        _update_run(run_id, 'running')
        run['status'] = 'running'

    run['job'] = {
        'status': job.get('status'),
        'output': (job.get('output') or '')[-OUTPUT_TAIL:],
        'error': job.get('error'),
        'cost_usd': job.get('cost_usd'),
        'duration_ms': job.get('duration_ms'),
    }
    return run


def verify(owner: str, token: Optional[str], run_id: int, account: str,
           password: Optional[str] = None, network: Optional[str] = None,
           confirm: bool = False) -> Dict[str, Any]:
    """Run the project's suites on a chain — the paid half of verification.

    This is harness.run on the project the agent edited, nothing more: same
    testnet default, same mainnet confirm, same stored report. The summary is
    written onto the run so the run's story ends with what the chain said.
    """
    import harness
    run_row = _get_run(owner, run_id)
    report = harness.run(owner, account, network=network,
                         project=str(run_row['project_id']),
                         password=password, confirm=confirm, token=token)
    result = run_row.get('result') or {}
    result['verified'] = {'ok': report.get('ok'),
                          'passed': report.get('passed'),
                          'failed': report.get('failed'),
                          'total': report.get('total'),
                          'network': report.get('network'),
                          'cid': report.get('cid')}
    _update_run(run_id, run_row['status'], result=result)
    return report


def cancel(owner: str, token: Optional[str], run_id: int,
           link: Optional[BuildLink] = None) -> Dict[str, Any]:
    run = _get_run(owner, run_id)
    out = (link or LINK).cancel(token, run['job_id'])
    _update_run(run_id, 'cancelled')
    return {'run_id': run_id, 'job_id': run['job_id'], 'cancelled': True,
            'build': out}


def runs(owner: str, limit: int = 30,
         project: Optional[str] = None) -> List[Dict[str, Any]]:
    owner = (owner or '').lower()
    with connect() as conn:
        if project:
            rows = conn.execute(
                'SELECT * FROM agent_runs WHERE owner=? AND slug=? '
                'ORDER BY created DESC LIMIT ?',
                (owner, project, int(limit))).fetchall()
        else:
            rows = conn.execute(
                'SELECT * FROM agent_runs WHERE owner=? '
                'ORDER BY created DESC LIMIT ?',
                (owner, int(limit))).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        try:
            item['result'] = (json.loads(item['result'])
                              if item.get('result') else None)
        except json.JSONDecodeError:
            item['result'] = None
        item.pop('workspace', None)
        out.append(item)
    return out


def status(link: Optional[BuildLink] = None) -> Dict[str, Any]:
    """Is the agent door open: build's health, and where checkouts land."""
    out: Dict[str, Any] = {'build_url': build_url(),
                           'workroot': str(WORKROOT),
                           'model': DEFAULT_MODEL}
    try:
        health = (link or LINK).health()
        out['reachable'] = True
        out['build'] = {'service': health.get('service'),
                        'local': health.get('local')}
    except BuildError as e:
        out['reachable'] = False
        out['error'] = e.message
    return out
