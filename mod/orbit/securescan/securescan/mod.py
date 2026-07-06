"""
securescan — agent-powered security scanner for any GitHub repository.

Layout
    securescan/
      securescan/mod.py   ← this file (anchor, framework entry point)
      src/
        api/              ← FastAPI server (api.py)
        app/              ← Next.js scanner UI

Usage
    import mod as m
    s = m.mod('securescan')()
    s.scan(mod='bridge')                          # local module
    s.scan(path='/some/repo')                     # local path
    s.scan_github('https://github.com/user/repo') # remote repo
    s.serve()                                     # start API + UI
"""

import os
import re
import json
import time
import shutil
import hashlib
import tempfile
import subprocess
from pathlib import Path
from typing import Dict, List, Any, Optional

import mod as m
print = m.print


class Mod:
    description = """
    Agent-powered security scanner. Scans any GitHub repository (or local path)
    for vulnerabilities, secrets, and misconfigurations using an LLM agent.
    Reports are written to .security/{reviewer_wallet}/ in the scanned repo,
    and to ~/.securescan/scans/{scan_id}/ when the API is used.
    """

    goal = """
        You are a senior security auditor performing a comprehensive code review.
        Systematically scan the repository for security vulnerabilities.

        CHECK FOR:
        - Hardcoded secrets, API keys, private keys, passwords, tokens
        - Command injection, SQL injection, XSS, path traversal
        - Unsafe deserialization, insecure file operations
        - Smart contract vulnerabilities (reentrancy, overflow, access control)
        - Insecure configurations (CORS, debug mode, permissive permissions)
        - Missing authentication or authorization checks
        - Exposed sensitive endpoints or debug routes
        - Dependency vulnerabilities (known CVEs in imports)
        - Unsafe use of eval, exec, subprocess with user input
        - Information leakage (stack traces, verbose errors, .env files)

        METHODOLOGY:
        1. First use glob to understand the repo structure and file types
        2. Use grep to search for patterns: passwords, secrets, keys, tokens, eval, exec, subprocess
        3. Read critical files: configs, env files, auth modules, API routes, contracts
        4. Analyze each finding for actual exploitability (not just pattern matches)

        OUTPUT:
        When finished, use the finish tool. In params, include a "findings" list.
        Each finding must be a dict with:
        - severity: "critical" | "high" | "medium" | "low" | "info"
        - category: "secrets" | "injection" | "xss" | "access_control" | "config" | "contract" | "dependency" | "crypto" | "info_leak" | "other"
        - title: short description
        - description: detailed explanation with exploitation scenario
        - file: relative file path
        - line: line number (if applicable, else null)
        - recommendation: how to fix
    """

    skills = ['read', 'grep', 'glob', 'bash', 'think']

    name = 'securescan'
    api_port = 50092
    app_port = 3092

    def __init__(self, key='test', provider='openrouter', model=None, **kwargs):
        self._agent = None
        self.key_name = key
        self.provider = provider
        self.model = model
        self._mod_dir = Path(__file__).parent                  # securescan/securescan/
        self._dir = self._mod_dir.parent                       # securescan/
        self._src_dir = self._dir / 'src'
        self._api_dir = self._src_dir / 'api'
        self._app_dir = self._src_dir / 'app'
        self.store_dir = Path(os.path.expanduser('~/.securescan'))
        self.scans_dir = self.store_dir / 'scans'
        self.repos_dir = self.store_dir / 'repos'
        for d in (self.scans_dir, self.repos_dir):
            d.mkdir(parents=True, exist_ok=True)

    @property
    def agent(self):
        if self._agent is None:
            self._agent = m.mod('agent')(
                goal=self.goal,
                skills=self.skills,
                model=self.provider,
            )
        return self._agent

    # ── Scan entry points ─────────────────────────────────────────

    def forward(self, mod=None, path=None, repo=None, subdir=None, key=None, reviewer=None,
                provider=None, model=None, steps=15, **kwargs):
        """Default entry — dispatch to local scan or github scan based on args."""
        if repo:
            return self.scan_github(repo, subdir=subdir, key=key, reviewer=reviewer,
                                    provider=provider, model=model, steps=steps, **kwargs)
        return self.scan(mod=mod, path=path, key=key, reviewer=reviewer,
                         provider=provider, model=model, steps=steps, **kwargs)

    def scan_github(self, repo: str, branch: str = None, subdir: str = None, scan_id: str = None,
                    key=None, reviewer=None, provider=None, model=None, steps=15, **kwargs):
        """
        Clone a GitHub repository and run a security scan against it.

        Args:
            repo: GitHub URL or "owner/name" shorthand
            branch: optional branch/ref to check out
            subdir: optional path inside the repo to scan (e.g. "mod/orbit/bridge").
                    Useful for monorepos where you want to audit a single module.
            scan_id: optional pre-allocated scan id (used by API for status polling)
            key, provider, model, steps: passed through to scan()

        Returns:
            Dict with scan results, including report paths.
        """
        repo_url = self._normalize_github_url(repo)
        if not repo_url:
            return {'error': f'invalid github repo: {repo}'}

        scan_id = scan_id or self._make_scan_id(repo_url, subdir=subdir, branch=branch)
        scan_dir = self.scans_dir / scan_id
        scan_dir.mkdir(parents=True, exist_ok=True)
        clone_dir = self.repos_dir / scan_id

        self._write_status(scan_dir, {
            'scan_id': scan_id,
            'repo': repo_url,
            'branch': branch,
            'subdir': subdir,
            'status': 'cloning',
            'started_at': int(time.time()),
        })

        # Clone (shallow)
        try:
            self._clone(repo_url, clone_dir, branch=branch)
        except subprocess.CalledProcessError as e:
            err = (e.stderr or b'').decode('utf-8', 'ignore') or str(e)
            self._write_status(scan_dir, {
                'scan_id': scan_id,
                'repo': repo_url,
                'status': 'error',
                'error': f'clone failed: {err.strip()}',
                'finished_at': int(time.time()),
            }, merge=True)
            return {'scan_id': scan_id, 'error': f'clone failed: {err.strip()}'}

        # Resolve the scan target: full clone or a subdir within it.
        scan_path = clone_dir
        if subdir:
            sub = self._resolve_subdir(clone_dir, subdir)
            if isinstance(sub, dict) and 'error' in sub:
                self._write_status(scan_dir, {
                    'status': 'error',
                    'error': sub['error'],
                    'finished_at': int(time.time()),
                }, merge=True)
                return {'scan_id': scan_id, 'error': sub['error']}
            scan_path = sub

        self._write_status(scan_dir, {'status': 'scanning'}, merge=True)

        try:
            result = self.scan(
                path=str(scan_path),
                key=key,
                reviewer=reviewer,
                provider=provider,
                model=model,
                steps=steps,
                **kwargs,
            )
        except Exception as e:
            self._write_status(scan_dir, {
                'status': 'error',
                'error': str(e),
                'finished_at': int(time.time()),
            }, merge=True)
            raise

        # Persist findings/report into the central scan dir as well
        report = {
            'scan_id': scan_id,
            'repo': repo_url,
            'branch': branch,
            'subdir': subdir,
            'reviewer': result.get('reviewer'),
            'stats': result.get('stats', {}),
            'findings': result.get('findings', []),
            'elapsed_seconds': result.get('elapsed'),
            'finished_at': int(time.time()),
            'status': 'done',
        }
        with open(scan_dir / 'report.json', 'w') as f:
            json.dump(report, f, indent=2, default=str)
        self._write_status(scan_dir, report, merge=False)

        # Optionally clean up the clone — keep it for now so the user can inspect.
        return {**result, 'scan_id': scan_id, 'repo': repo_url, 'subdir': subdir}

    def _resolve_subdir(self, clone_dir: Path, subdir: str):
        """Resolve a subdir argument to an absolute path inside clone_dir.

        Rejects path traversal (`..`, absolute paths, symlinks pointing outside
        the clone) so a malicious `subdir` can't trick us into scanning host
        filesystem locations.
        """
        if not isinstance(subdir, str) or not subdir.strip():
            return {'error': 'subdir must be a non-empty string'}
        sub = subdir.strip().lstrip('/')
        if '..' in Path(sub).parts:
            return {'error': f'subdir must not contain ".." (got: {subdir!r})'}
        candidate = (clone_dir / sub).resolve()
        clone_real = clone_dir.resolve()
        try:
            candidate.relative_to(clone_real)
        except ValueError:
            return {'error': f'subdir resolves outside the cloned repo: {subdir!r}'}
        if not candidate.exists():
            return {'error': f'subdir does not exist in repo: {subdir!r}'}
        if not candidate.is_dir():
            return {'error': f'subdir is not a directory: {subdir!r}'}
        return candidate

    def scan(self, mod=None, path=None, key=None, reviewer=None,
             provider=None, model=None, steps=15, **kwargs):
        """Run a security scan on a local repository or module.

        If `reviewer` is supplied (e.g. an EVM address from a connected wallet),
        it is used directly as the reviewer identity. Otherwise the wallet
        derived from `key` is used.
        """
        if mod:
            path = m.dp(mod)
        path = path or os.path.expanduser('~/mod')
        path = os.path.abspath(os.path.expanduser(path))
        key = key or self.key_name
        provider = provider or self.provider
        model = model or self.model

        if not os.path.isdir(path):
            return {'error': f'path not found: {path}'}

        wallet = self._validate_reviewer(reviewer) if reviewer else self._resolve_wallet(key)
        print(f'[securescan] reviewer: {wallet}', color='cyan')
        print(f'[securescan] scanning: {path}', color='cyan')
        print(f'[securescan] provider: {provider}', color='cyan')
        if model:
            print(f'[securescan] model: {model}', color='cyan')

        context = self._gather_context(path)
        prompt = self._build_prompt(context)

        ts = time.time()
        result = self.agent.run(
            query=prompt,
            path=path,
            model=model,
            provider=provider,
            steps=steps,
        )
        elapsed = round(time.time() - ts, 1)

        findings = self._parse_findings(result)
        print(f'[securescan] found {len(findings)} findings in {elapsed}s', color='yellow')

        metadata = {
            'timestamp': int(time.time()),
            'reviewer': wallet,
            'reviewer_source': 'wallet' if reviewer else 'key',
            'key': None if reviewer else key,
            'model': model,
            'repo': path,
            'steps': steps,
            'elapsed_seconds': elapsed,
            'stats': self._compute_stats(findings),
        }

        report_dir = self.write_report(path, wallet, findings, metadata)

        return {
            'reviewer': wallet,
            'repo': path,
            'findings': findings,
            'stats': metadata['stats'],
            'report_dir': str(report_dir),
            'elapsed': elapsed,
        }

    # ── Scan listing / retrieval (used by the API) ────────────────

    def list_scans(self, limit: int = 100):
        """List recent scans (most recent first)."""
        out = []
        if not self.scans_dir.exists():
            return out
        entries = sorted(
            self.scans_dir.iterdir(),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for d in entries[:limit]:
            status = self._read_status(d)
            if status:
                out.append(status)
        return out

    def get_scan(self, scan_id: str):
        """Return the full status/report for a scan id."""
        scan_dir = self.scans_dir / scan_id
        return self._read_status(scan_dir)

    def delete_scan(self, scan_id: str):
        """Remove a scan's stored data."""
        scan_dir = self.scans_dir / scan_id
        clone_dir = self.repos_dir / scan_id
        for d in (scan_dir, clone_dir):
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
        return {'scan_id': scan_id, 'deleted': True}

    def write_report(self, path, wallet, findings, metadata):
        """Write scan results to {repo}/.security/{wallet}/."""
        security_dir = Path(path) / '.security' / wallet
        security_dir.mkdir(parents=True, exist_ok=True)

        report = {**metadata, 'findings': findings}
        report_path = security_dir / 'report.json'
        with open(report_path, 'w') as f:
            json.dump(report, f, indent=2, default=str)
        print(f'[securescan] wrote {report_path}', color='green')

        summary_path = security_dir / 'summary.md'
        with open(summary_path, 'w') as f:
            f.write(self._build_summary(findings, metadata))
        print(f'[securescan] wrote {summary_path}', color='green')

        return security_dir

    def health(self):
        return {'status': 'ok', 'module': 'securescan'}

    def status(self):
        return {
            'module': 'securescan',
            'provider': self.provider,
            'model': self.model,
            'skills': self.skills,
            'api_port': self.api_port,
            'app_port': self.app_port,
        }

    # ── Serve (API + Next.js app) ─────────────────────────────────

    def serve(self, api_port=None, app_port=None, dev=True, api_only=False, app_only=False,
              gateway=False, base_path=None):
        """
        Start the API server (FastAPI under src/api) and the scanner UI (Next.js under src/app).

        Args:
            api_port:  API server port (default 50092)
            app_port:  Next.js app port (default 3092)
            dev:       run in dev mode (default True)
            api_only:  only start the API
            app_only:  only start the UI
            gateway:   set True when running behind the mod gateway
            base_path: explicit basePath override (e.g. "/securescan")
        """
        api_port = api_port or self.api_port
        app_port = app_port or self.app_port
        if base_path is None:
            base_path = f'/{self.name}' if gateway else ''

        results = {}
        if not app_only:
            results['api'] = self._serve_api(api_port, dev=dev)
        if not api_only:
            results['app'] = self._serve_app(app_port, api_port=api_port, dev=dev, base_path=base_path)

        try:
            ns = m.mod('server.namespace')()
            ns.reg_app(
                self.name,
                f'http://localhost:{app_port}',
                api_url=f'http://localhost:{api_port}',
            )
        except Exception:
            pass
        return results

    def kill(self, service=None):
        """Stop running services. service='api'|'app'|None."""
        results = {}
        try:
            pm2 = m.mod('pm.pm2')()
            if service in (None, 'api') and pm2.exists('securescan-api'):
                pm2.kill('securescan-api')
                results['api'] = 'killed'
            if service in (None, 'app') and pm2.exists('securescan-app'):
                pm2.kill('securescan-app')
                results['app'] = 'killed'
        except Exception as e:
            results['error'] = str(e)
        if service is None:
            try:
                m.mod('server.namespace')().dereg_app(self.name)
            except Exception:
                pass
        return results

    def _serve_api(self, port, dev=True):
        """Build (if needed) and start the Rust API server (src/api)."""
        cwd = str(self._api_dir)
        binary = self._api_dir / 'target' / 'release' / 'securescan-api'
        if not binary.exists():
            print('[securescan] building Rust API (cargo build --release)…', color='cyan')
            r = subprocess.run(['cargo', 'build', '--release'], cwd=cwd,
                               capture_output=True, text=True)
            if r.returncode != 0:
                print(f'[securescan] cargo build failed:\n{r.stderr}', color='red')
                return {'status': 'error', 'error': 'cargo build failed', 'stderr': r.stderr}

        script = os.path.join(cwd, '_serve.sh')
        with open(script, 'w') as f:
            f.write(
                f'#!/bin/bash\ncd {cwd}\n'
                f'export PORT={port}\n'
                f'export RUST_LOG=${{RUST_LOG:-securescan_api=info,tower_http=info}}\n'
                f'exec {binary}\n'
            )
        os.chmod(script, 0o755)
        try:
            pm2 = m.mod('pm.pm2')()
            name = 'securescan-api'
            if pm2.exists(name):
                pm2.kill(name, remove_script=False)
            pm2.start_script(name=name, script_path=script, cwd=cwd, interpreter='bash')
            return {'status': 'running', 'port': port, 'manager': 'pm2', 'name': name, 'binary': str(binary)}
        except Exception:
            proc = subprocess.Popen(['bash', script], cwd=cwd)
            return {'status': 'running', 'port': port, 'manager': 'subprocess', 'pid': proc.pid, 'binary': str(binary)}

    def _serve_app(self, port, api_port, dev=True, base_path=''):
        cwd = str(self._app_dir)
        if not os.path.exists(os.path.join(cwd, 'node_modules')):
            print('[securescan] installing npm dependencies…', color='cyan')
            r = subprocess.run(['npm', 'install'], cwd=cwd, capture_output=True, text=True)
            if r.returncode != 0:
                print(f'[securescan] npm install failed: {r.stderr}', color='red')
        cmd = f'npm run {"dev" if dev else "start"} -- -p {port}'
        script = os.path.join(cwd, '_serve.sh')
        with open(script, 'w') as f:
            f.write(
                f'#!/bin/bash\ncd {cwd}\n'
                f'export NEXT_PUBLIC_API_URL=http://localhost:{api_port}\n'
                f'export NEXT_PUBLIC_BASE_PATH={base_path}\n'
                f'{cmd}\n'
            )
        os.chmod(script, 0o755)
        try:
            pm2 = m.mod('pm.pm2')()
            name = 'securescan-app'
            if pm2.exists(name):
                pm2.kill(name, remove_script=False)
            pm2.start_script(name=name, script_path=script, cwd=cwd, interpreter='bash')
            return {'status': 'running', 'port': port, 'manager': 'pm2', 'name': name}
        except Exception:
            proc = subprocess.Popen(['bash', script], cwd=cwd)
            return {'status': 'running', 'port': port, 'manager': 'subprocess', 'pid': proc.pid}

    # ── Internal helpers ──────────────────────────────────────────

    @staticmethod
    def _normalize_github_url(repo: str) -> Optional[str]:
        repo = (repo or '').strip().rstrip('/')
        if not repo:
            return None
        if re.fullmatch(r'[\w.-]+/[\w.-]+', repo):
            return f'https://github.com/{repo}.git'
        m_url = re.fullmatch(r'(https?://github\.com/[\w.-]+/[\w.-]+?)(\.git)?', repo)
        if m_url:
            return m_url.group(1) + '.git'
        if repo.startswith('git@github.com:'):
            return repo if repo.endswith('.git') else repo + '.git'
        return None

    @staticmethod
    def _make_scan_id(repo_url: str, subdir: str = None, branch: str = None) -> str:
        # Include subdir/branch so distinct scans of the same repo don't collide.
        seed = f'{repo_url}|{branch or ""}|{subdir or ""}|{time.time_ns()}'
        h = hashlib.sha1(seed.encode()).hexdigest()[:12]
        slug = re.sub(r'[^a-z0-9]+', '-', repo_url.lower()).strip('-')[-40:]
        if subdir:
            sub_slug = re.sub(r'[^a-z0-9]+', '-', subdir.lower()).strip('-')[:20]
            if sub_slug:
                slug = f'{slug}-{sub_slug}'
        return f'{slug}-{h}'

    def _clone(self, repo_url: str, dest: Path, branch: str = None):
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        cmd = ['git', 'clone', '--depth', '1']
        if branch:
            cmd += ['--branch', branch]
        cmd += [repo_url, str(dest)]
        subprocess.run(cmd, check=True, capture_output=True, timeout=180)

    @staticmethod
    def _write_status(scan_dir: Path, data: dict, merge: bool = False):
        scan_dir.mkdir(parents=True, exist_ok=True)
        path = scan_dir / 'status.json'
        if merge and path.exists():
            try:
                with open(path) as f:
                    existing = json.load(f)
                existing.update(data)
                data = existing
            except Exception:
                pass
        with open(path, 'w') as f:
            json.dump(data, f, indent=2, default=str)

    @staticmethod
    def _read_status(scan_dir: Path):
        if not scan_dir.exists():
            return None
        path = scan_dir / 'status.json'
        if not path.exists():
            return None
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return None

    def _resolve_wallet(self, key):
        try:
            return m.key(key).address
        except Exception:
            return key

    @staticmethod
    def _validate_reviewer(reviewer: str) -> str:
        """Accept a reviewer identity supplied externally (e.g. from a connected wallet).

        Currently expects an EVM address (`0x` + 40 hex). Normalised to lowercase.
        Raises ValueError on anything else so a junk value can't poison the report.
        """
        if not isinstance(reviewer, str):
            raise ValueError(f'reviewer must be a string (got {type(reviewer).__name__})')
        r = reviewer.strip()
        if not re.fullmatch(r'0x[0-9a-fA-F]{40}', r):
            raise ValueError(f'reviewer must be a 0x-prefixed 40-char hex address (got {reviewer!r})')
        return r.lower()

    def _gather_context(self, path):
        context = {'path': path, 'file_types': {}, 'total_files': 0, 'structure': []}
        skip_dirs = {'.git', 'node_modules', '__pycache__', '.next', 'venv',
                     'env', '.env', 'dist', 'build', '.security'}
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for fname in files:
                context['total_files'] += 1
                ext = Path(fname).suffix.lower()
                if ext:
                    context['file_types'][ext] = context['file_types'].get(ext, 0) + 1
                rel = os.path.relpath(os.path.join(root, fname), path)
                if len(context['structure']) < 100:
                    context['structure'].append(rel)
        return context

    def _build_prompt(self, context):
        parts = [
            f"Security audit of repository at: {context['path']}",
            f"\nTotal files: {context['total_files']}",
            f"File types: {json.dumps(context['file_types'], indent=2)}",
            f"\nSample structure (first 100 files):",
        ]
        for f in context['structure']:
            parts.append(f"  {f}")
        parts.append(
            "\nPerform a thorough security scan. Focus on high-impact vulnerabilities first. "
            "Use grep to search for secrets and dangerous patterns, then read suspicious files. "
            "When done, call finish with your findings list."
        )
        return '\n'.join(parts)

    def _parse_findings(self, result):
        findings = []
        if not result:
            return findings
        for step in result:
            if not isinstance(step, dict):
                continue
            if step.get('tool', '').lower() == 'finish':
                params = step.get('params', {})
                if isinstance(params, dict):
                    if 'findings' in params:
                        f = params['findings']
                        if isinstance(f, list):
                            findings.extend(f)
                        else:
                            findings.append(f)
                    elif params:
                        findings.append(params)
            if 'result' in step and isinstance(step['result'], dict):
                if 'findings' in step['result']:
                    r = step['result']['findings']
                    if isinstance(r, list):
                        findings.extend(r)
        return findings

    def _compute_stats(self, findings):
        stats = {'total': len(findings), 'by_severity': {}, 'by_category': {}}
        for f in findings:
            if not isinstance(f, dict):
                continue
            sev = f.get('severity', 'unknown')
            cat = f.get('category', 'other')
            stats['by_severity'][sev] = stats['by_severity'].get(sev, 0) + 1
            stats['by_category'][cat] = stats['by_category'].get(cat, 0) + 1
        return stats

    def _build_summary(self, findings, metadata):
        lines = [
            "# Security Scan Report",
            "",
            f"**Repo:** `{metadata['repo']}`",
            f"**Reviewer:** `{metadata['reviewer']}`",
            f"**Model:** `{metadata['model']}`",
            f"**Date:** {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(metadata['timestamp']))}",
            f"**Duration:** {metadata['elapsed_seconds']}s",
            "",
            "## Summary",
            "",
            f"Total findings: **{metadata['stats']['total']}**",
            "",
        ]
        if metadata['stats'].get('by_severity'):
            lines.append("| Severity | Count |")
            lines.append("|----------|-------|")
            for sev in ['critical', 'high', 'medium', 'low', 'info']:
                count = metadata['stats']['by_severity'].get(sev, 0)
                if count:
                    lines.append(f"| {sev} | {count} |")
            lines.append("")
        if findings:
            lines.append("## Findings")
            lines.append("")
            for i, f in enumerate(findings, 1):
                if not isinstance(f, dict):
                    continue
                sev = f.get('severity', 'unknown').upper()
                title = f.get('title', 'Untitled')
                lines.append(f"### {i}. [{sev}] {title}")
                lines.append("")
                if f.get('file'):
                    loc = f['file']
                    if f.get('line'):
                        loc += f":{f['line']}"
                    lines.append(f"**Location:** `{loc}`")
                if f.get('category'):
                    lines.append(f"**Category:** {f['category']}")
                lines.append("")
                if f.get('description'):
                    lines.append(f.get('description'))
                    lines.append("")
                if f.get('recommendation'):
                    lines.append(f"> **Fix:** {f['recommendation']}")
                    lines.append("")
                lines.append("---")
                lines.append("")
        else:
            lines.append("No findings detected.")
            lines.append("")
        return '\n'.join(lines)
