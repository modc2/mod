import json
import os
import time
import signal
import subprocess
import threading
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, Dict, Any, Union, List
import mod as m


class Mod:
    """
    Claude module — programmable AI developer interface.

    Operates in two modes:
      - Server mode: delegates to Rust job server (port 8820) + Next.js app (8821)
      - Standalone mode: runs Claude CLI directly, local bg jobs, IPFS versioning

    All code-writing operations require owner permission when an owner is set.
    Read-only operations (analyze, debug, modules, health) are always open.
    """

    description = "Claude AI interface — jobs, code ops, IPFS versioning, and module management."
    endpoints = [
        'forward', 'ask', 'submit', 'jobs', 'job', 'cancel', 'tail',
        'create_module', 'edit_module', 'analyze_code', 'generate_code',
        'refactor', 'debug', 'edit_file', 'run_task', 'batch_process',
        'bg', 'bg_status', 'bg_list', 'snapshot', 'changelog',
        'get_version', 'restore_version', 'health', 'modules',
        'folders', 'suggest_folders',
        'set_owner', 'get_owner', 'is_owner', 'is_bloctime_owner',
        'register_onchain', 'permissions', 'ensure_env',
        'install', 'setup', 'serve',
        'kill', 'kill_port', 'status', 'logs', 'scan', 'fix',
    ]

    def __init__(self, api_url: str = None, app_url: str = None,
                 key=None, default_path: str = None,
                 idle_timeout: int = None, **kwargs):
        self.key = m.key(key)
        self.auth = m.mod('auth.base')()
        cfg = self._load_config()
        servers = cfg.get('servers', {})
        name = cfg.get('name', 'claude')
        self.api_url = api_url or servers.get(f'{name}-api', 'http://localhost:8820')
        self.app_url = app_url or servers.get(f'{name}-app', 'http://localhost:8821')
        self.config = cfg
        self.default_path = default_path or cfg.get('default_path', os.path.expanduser('~/mod'))
        self._owner = cfg.get('owner') or self.key.address.lower()
        self._history_dir = Path(self._module_dir()) / '.history'
        # dynamic API lifecycle
        self._idle_timeout = idle_timeout or cfg.get('idle_timeout', 300)  # seconds
        self._last_activity = 0.0
        self._api_managed = False  # True if we started the API ourselves
        self._idle_lock = threading.Lock()
        self._idle_thread = None
        # on-chain integration (chain mod Registry + BlocTime ownership)
        self._chain_mod = None
        self._chain_network = cfg.get('chain_network', 'testnet')
        self._bloctime_gate = cfg.get('bloctime_gate', True)
        self._bloctime_cache = {}          # addr -> (is_holder, ts)
        self._bloctime_ttl = cfg.get('bloctime_ttl', 60)  # seconds

    # ── config ────────────────────────────────────────────────────

    def _module_dir(self) -> str:
        return str(Path(__file__).parent.parent)

    def _load_config(self) -> dict:
        config_path = os.path.join(Path(__file__).parent.parent, 'config.json')
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                return json.load(f)
        return {}

    def _save_config(self, cfg: dict):
        config_path = os.path.join(self._module_dir(), 'config.json')
        with open(config_path, 'w') as f:
            json.dump(cfg, f, indent=2)
        self.config = cfg

    # ── permissions ───────────────────────────────────────────────

    def get_owner(self) -> Optional[str]:
        """Get the owner address, or None if unset."""
        return self._owner

    def set_owner(self, address: str):
        """Set the owner address. Persists to config.json."""
        addr = address.lower() if address else None
        self._owner = addr
        cfg = self._load_config()
        cfg['owner'] = addr
        self._save_config(cfg)

    def _resolve_address(self, key=None) -> str:
        """Resolve a key/address/token to a verified address string.

        Accepts:
          - None           → uses self.key (the owner by default)
          - Key object     → extracts .address
          - str (0x...)    → used directly
          - str (token)    → decoded & verified via auth, returns token's key address
        """
        if key is None:
            return self.key.address
        if hasattr(key, 'address'):
            return key.address
        key_str = str(key)
        # Looks like a hex address
        if key_str.startswith('0x') and len(key_str) in (42, 66):
            return key_str
        # Otherwise treat as a signed token — verify it
        try:
            verified = self.auth.verify(key_str)
            return verified['key']
        except Exception:
            return key_str

    def token(self, data: dict = None, key=None) -> str:
        """Generate a signed auth token for the given key (default: owner)."""
        key = key or self.key
        return self.auth.token(data=data or {}, key=key)

    def verify(self, token: str) -> dict:
        """Verify a signed auth token. Returns decoded headers with 'key' address."""
        return self.auth.verify(token)

    # ── on-chain integration (chain mod) ──────────────────────────

    def _chain(self):
        """Lazy chain orchestrator (BlocTime + Registry live on-chain)."""
        if self._chain_mod is None:
            self._chain_mod = m.mod('chain')(network=self._chain_network)
        return self._chain_mod

    def is_bloctime_owner(self, address=None) -> bool:
        """Check whether an address holds BlocTime (staked) on-chain.

        BlocTime holders are granted owner-level access to claude. Results are
        cached briefly and every chain failure degrades to False so the auth
        path never blocks on RPC issues.
        """
        if not self._bloctime_gate:
            return False
        addr = self._resolve_address(address) if address is not None else self.key.address
        if not addr:
            return False
        addr = addr.lower()
        now = time.time()
        cached = self._bloctime_cache.get(addr)
        if cached and now - cached[1] < self._bloctime_ttl:
            return cached[0]
        try:
            holder = bool(self._chain().is_bloctime_holder(addr))
        except Exception:
            holder = False
        self._bloctime_cache[addr] = (holder, now)
        return holder

    def register_onchain(self, network: str = None, data: str = None,
                         force: bool = False) -> dict:
        """Register this claude module in the on-chain Registry (chain mod).

        Permissionless per-creator registration. Skips the transaction when the
        module is already registered with identical data (unless force=True).
        """
        chain = self._chain() if network is None else m.mod('chain')(network=network)
        data = data or self.api_url
        try:
            if not force and chain.mod_exists('claude'):
                existing = chain.get_mod(chain.name2id('claude'))
                if existing.get('data') == data:
                    return {'status': 'already_registered', 'name': 'claude', 'data': data}
        except Exception:
            pass
        receipt = chain.reg('claude', data)
        tx = receipt.transactionHash.hex() if hasattr(receipt, 'transactionHash') else str(receipt)
        return {'status': 'registered', 'name': 'claude', 'data': data, 'tx': tx}

    def is_owner(self, key=None) -> bool:
        """Check if key/address/token belongs to the owner.

        Authorized if: no owner is set, the caller matches the config owner, OR
        the caller holds BlocTime on-chain (BlocTime owners get owner access).
        """
        if not self._owner:
            return True
        addr = self._resolve_address(key)
        if not addr:
            return False
        if addr.lower() == self._owner.lower():
            return True
        return self.is_bloctime_owner(addr)

    def _format_address(self, address) -> str:
        """Extract a clean hex address from a string, Key object, or repr."""
        addr = str(address)
        if hasattr(address, 'address'):
            addr = str(address.address)
        elif addr.startswith('Key('):
            inner = addr[4:].rstrip(')')
            addr = inner.split(',')[0].strip()
        return addr[:10] + '...' + addr[-4:] if len(addr) > 16 else addr

    def require_owner(self, key=None, operation: str = "this operation"):
        """Raise PermissionError if key/address/token is not the owner.

        Accepts None (defaults to self.key), Key objects, hex addresses, or signed tokens.
        """
        if not self.is_owner(key):
            caller = self._format_address(self._resolve_address(key))
            owner = self._format_address(self._owner) if self._owner else 'none'
            raise PermissionError(
                f"Permission denied: '{operation}' is owner-only.\n"
                f"  caller: {caller}\n"
                f"  owner:  {owner}"
            )

    def _portal_dir(self, address: str) -> str:
        """Return the portal directory for a given address."""
        return os.path.join(os.path.expanduser('~/mod/mod/orbit/portal'), address.lower())

    def _user_allowed_modules(self, address: str) -> List[str]:
        """List module names a non-owner user is allowed to edit (from portal/{address}/)."""
        portal = self._portal_dir(address)
        if not os.path.isdir(portal):
            return []
        return [d for d in os.listdir(portal)
                if os.path.isdir(os.path.join(portal, d)) and not d.startswith('.')]

    def _path_in_portal(self, path: str, address: str) -> bool:
        """Check if a path is within the user's portal directory."""
        try:
            real_path = os.path.realpath(os.path.expanduser(path))
            real_portal = os.path.realpath(self._portal_dir(address))
            return real_path.startswith(real_portal + os.sep) or real_path == real_portal
        except Exception:
            return False

    def require_permission(self, key=None, module: str = None, path: str = None,
                           operation: str = "this operation"):
        """Check if the caller has permission for the target module/path.

        Owner: can edit anything (orbit, core, portal, etc.)
        Non-owner: can only edit modules within portal/{their_address}/.

        Args:
            key: caller identity (address, Key, token, or None for self.key)
            module: module name being edited (e.g. 'bridge', 'agent')
            path: working directory path
            operation: description for error messages
        """
        # owner can do anything
        if self.is_owner(key):
            return

        caller_addr = self._resolve_address(key)

        # check if path is within caller's portal
        if path and self._path_in_portal(path, caller_addr):
            return

        # check if module name matches a portal module
        if module:
            allowed = self._user_allowed_modules(caller_addr)
            # allow portal/{addr}/{module} style names
            normalized = module.lower().replace('portal/', '').lstrip('/')
            # strip the address prefix if present
            addr_prefix = caller_addr.lower() + '/'
            if normalized.startswith(addr_prefix):
                normalized = normalized[len(addr_prefix):]
            if normalized in [a.lower() for a in allowed]:
                return

        # no match — deny
        caller_fmt = self._format_address(caller_addr)
        portal_path = self._portal_dir(caller_addr)
        allowed = self._user_allowed_modules(caller_addr)
        allowed_str = ', '.join(allowed[:5]) if allowed else '(none)'
        raise PermissionError(
            f"Permission denied: '{operation}'\n"
            f"  caller: {caller_fmt}\n"
            f"  allowed modules: {allowed_str}\n"
            f"  portal: {portal_path}\n"
            f"  hint: non-owners can only edit modules in their portal directory"
        )

    def permissions(self, key=None) -> dict:
        """Check what the caller is allowed to edit.

        Returns role ('owner' or 'user'), address, and allowed modules.
        Owner can edit all modules. Non-owners can only edit portal/{address}/ modules.
        """
        addr = self._resolve_address(key)
        if self.is_owner(key):
            is_config_owner = bool(self._owner) and addr and addr.lower() == self._owner.lower()
            return {
                'role': 'owner' if (is_config_owner or not self._owner) else 'bloctime_owner',
                'address': addr,
                'can_edit': 'all',
                'scope': ['orbit/*', 'core/*', 'portal/*'],
                'via': 'config' if is_config_owner else ('open' if not self._owner else 'bloctime'),
            }
        allowed = self._user_allowed_modules(addr)
        return {
            'role': 'user',
            'address': addr,
            'can_edit': 'portal_only',
            'portal': self._portal_dir(addr),
            'modules': allowed,
        }

    # ── HTTP helpers ──────────────────────────────────────────────

    def _request(self, method: str, path: str, data: dict = None, timeout: int = 30) -> dict:
        """Make a request to the API server. Auto-starts API if needed."""
        self._ensure_api()
        url = f"{self.api_url}{path}"
        body = json.dumps(data).encode() if data else None
        headers = {'Content-Type': 'application/json'} if data else {}
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                self._touch_activity()
                return json.loads(resp.read().decode())
        except urllib.error.URLError as e:
            raise ConnectionError(
                f"API server not reachable at {self.api_url} — "
                f"start it with: cd mod/orbit/claude/api && cargo run\n{e}"
            )

    def _stream(self, path: str) -> None:
        """Stream SSE from the API server, printing lines as they arrive."""
        url = f"{self.api_url}{path}"
        req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", errors="replace").rstrip()
                    if line.startswith("data:"):
                        text = line[5:]
                        if text.strip() == "[DONE]":
                            break
                        print(text, end="", flush=True)
        except KeyboardInterrupt:
            print("\n--- detached (job still running) ---")

    def _server_available(self) -> bool:
        """Check if the API server is reachable."""
        try:
            url = f"{self.api_url}/health"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=2) as resp:
                json.loads(resp.read().decode())
            return True
        except Exception:
            return False

    # ── dynamic API lifecycle ─────────────────────────────────────

    def _api_port(self) -> int:
        """Extract port from api_url."""
        try:
            return int(self.api_url.rsplit(':', 1)[-1].rstrip('/'))
        except (ValueError, IndexError):
            return 8820

    def _start_api(self) -> bool:
        """Start the Rust API server. Returns True if started successfully."""
        api_dir = os.path.join(self._module_dir(), 'src', 'api')
        binary = os.path.join(api_dir, 'target', 'release', 'claude-jobs')
        if not os.path.exists(binary):
            start_sh = os.path.join(api_dir, 'start.sh')
            if os.path.exists(start_sh):
                subprocess.Popen(
                    ['bash', start_sh, str(self._api_port())],
                    cwd=api_dir, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, start_new_session=True
                )
            else:
                return False
        else:
            port = self._api_port()
            env = os.environ.copy()
            env['CLAUDE_JOBS_LOCAL'] = '1'
            subprocess.Popen(
                [binary, str(port)], cwd=api_dir, env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True
            )
        # wait for it to come up
        for _ in range(30):
            time.sleep(0.5)
            if self._server_available():
                self._api_managed = True
                self._touch_activity()
                self._start_idle_monitor()
                return True
        return False

    def _stop_api(self):
        """Stop the API server if we started it."""
        port = self._api_port()
        try:
            result = subprocess.run(
                ['lsof', '-ti', f':{port}'],
                capture_output=True, text=True
            )
            pids = result.stdout.strip().split('\n')
            for pid in pids:
                if pid.strip():
                    try:
                        os.kill(int(pid.strip()), signal.SIGTERM)
                    except (ProcessLookupError, ValueError):
                        pass
        except Exception:
            pass
        self._api_managed = False

    def _touch_activity(self):
        """Record the current time as last API activity."""
        with self._idle_lock:
            self._last_activity = time.time()

    def _start_idle_monitor(self):
        """Start a daemon thread that shuts down the API after idle_timeout."""
        if self._idle_thread and self._idle_thread.is_alive():
            return
        def _monitor():
            while self._api_managed:
                time.sleep(10)
                with self._idle_lock:
                    idle = time.time() - self._last_activity
                if idle >= self._idle_timeout:
                    print(f"[claude] API idle for {int(idle)}s — shutting down")
                    self._stop_api()
                    return
        self._idle_thread = threading.Thread(target=_monitor, daemon=True)
        self._idle_thread.start()

    def _ensure_api(self):
        """Ensure the API server is running. Auto-starts if needed."""
        self._touch_activity()
        if self._server_available():
            return True
        print("[claude] API not running — starting automatically...")
        if self._start_api():
            print(f"[claude] API started on port {self._api_port()} (idle timeout: {self._idle_timeout}s)")
            return True
        raise ConnectionError(
            f"Failed to auto-start API on port {self._api_port()}. "
            f"Start manually: cd {self._module_dir()}/src/api && bash start.sh"
        )

    # ── core: forward ─────────────────────────────────────────────

    def forward(self, query: str, *extra_query, mod: str = None,
                model: str = "sonnet", stream: bool = False,
                key: str = None, **kwargs) -> Dict[str, Any]:
        """
        Edit a module with the Claude agent.

        Args:
            query:  what to do (edit instructions)
            mod:    orbit module name to edit (default: resolves from kwargs or cwd)
            model:  sonnet | opus | haiku
            stream: if True, auto-tail the job output
            key:    caller address for permission check
        """
        query = query + " " + " ".join(extra_query)
        module_name = mod or kwargs.pop('module_name', None)
        path = m.dp(module_name) if module_name else kwargs.pop('path', self.default_path)
        self.require_permission(key, module=module_name, path=path,
                                operation=f"forward({query[:40]}...)")

        data = {
            "prompt": query,
            "model": model,
            "work_dir": path,
            "user_address": self._resolve_address(key),
            "creation_mode": "edit",
        }
        if module_name:
            data["module_name"] = module_name
        for k in ('github_url', 'anchor_dir', 'images', 'agent_type', 'system_prompt'):
            if k in kwargs:
                data[k] = kwargs[k]

        result = self._request("POST", "/jobs", data)
        if stream and result.get('id'):
            self.tail(result['id'])
        return result

    # ── CLI (no server needed) ────────────────────────────────────

    def _find_claude(self) -> str:
        mod_dir = self._module_dir()
        # prefer local install in module node_modules
        for candidate in [
            os.path.join(mod_dir, 'node_modules', '.bin', 'claude'),
            os.path.join(mod_dir, 'node_modules', '@anthropic-ai', 'claude-code', 'cli.js'),
        ]:
            if os.path.isfile(candidate):
                return candidate
        # fall back to global
        result = subprocess.run(["which", "claude"], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError("claude CLI not found — install with: m claude/install")
        return result.stdout.strip()

    def _run_cli(self, query: str, path: str = None, model: str = "sonnet",
                 output_format: str = "json", stream_output: bool = True, **kwargs) -> Union[str, Dict]:
        """Run Claude CLI directly (blocking). Used when background=False."""
        claude = self._find_claude()
        work_dir = path or self.default_path
        base = ['node', claude] if claude.endswith('.js') else [claude]
        cmd = base + ["--print", "--model", model, "--output-format", output_format,
                      "--dangerously-skip-permissions", query]
                      
        env = os.environ.copy()

        if stream_output:
            proc = subprocess.Popen(cmd, cwd=work_dir, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, env=env, bufsize=1)
            lines = []
            while True:
                retcode = proc.poll()
                if proc.stdout:
                    line = proc.stdout.readline()
                    if line:
                        print(line, end='', flush=True)
                        lines.append(line)
                if retcode is not None:
                    rest = proc.stdout.read() if proc.stdout else ''
                    if rest:
                        print(rest, end='', flush=True)
                        lines.append(rest)
                    break
                time.sleep(0.01)
            stdout = ''.join(lines)
            if retcode != 0:
                err = proc.stderr.read() if proc.stderr else ''
                raise RuntimeError(f"Claude CLI error: {err}")
        else:
            r = subprocess.run(cmd, cwd=work_dir, capture_output=True, text=True, env=env, timeout=300)
            if r.returncode != 0:
                raise RuntimeError(f"Claude CLI error: {r.stderr}")
            stdout = r.stdout

        if output_format == "json":
            try:
                return json.loads(stdout)
            except json.JSONDecodeError:
                return {"raw_output": stdout}
        return stdout

    # ── code operations ───────────────────────────────────────────

    def analyze_code(self, path: str = None, query: str = None,
                     focus: str = None, model: str = "sonnet", **kwargs) -> str:
        """Analyze code at a path. Read-only, no owner required."""
        work_dir = path or self.default_path
        prompt = query or f"Analyze the code in this directory. Focus on: {focus or 'overall quality and structure'}"
        return self._run_cli(prompt, path=work_dir, model=model,
                             output_format="text", stream_output=False)

    def generate_code(self, description: str, language: str = "python",
                      path: str = None, model: str = "sonnet",
                      key: str = None, **kwargs) -> str:
        """Generate code from description. Write operation, requires permission."""
        work_dir = path or self.default_path
        self.require_permission(key, path=work_dir, operation="generate_code")
        prompt = f"Generate {language} code: {description}"
        return self._run_cli(prompt, path=work_dir, model=model,
                             output_format="text", stream_output=False)

    def refactor(self, path: str, instructions: str = None,
                 model: str = "sonnet", key: str = None, **kwargs) -> str:
        """Refactor code at path. Write operation, requires permission."""
        self.require_permission(key, path=path, operation="refactor")
        prompt = f"Refactor the code. {instructions or 'Improve structure and readability.'}"
        return self._run_cli(prompt, path=path, model=model,
                             output_format="text", stream_output=False)

    def debug(self, path: str, error: str = None,
              model: str = "sonnet", **kwargs) -> str:
        """Debug code at path. Read-only, no owner required."""
        prompt = f"Debug the code in this directory."
        if error:
            prompt += f" The error is: {error}"
        return self._run_cli(prompt, path=path, model=model,
                             output_format="text", stream_output=False)

    def edit_file(self, file_path: str, instructions: str,
                  path: str = None, model: str = "sonnet",
                  key: str = None, **kwargs) -> str:
        """Edit a file with instructions. Write operation, requires permission."""
        work_dir = path or self.default_path
        self.require_permission(key, path=work_dir, operation="edit_file")
        prompt = f"Edit the file {file_path}: {instructions}"
        return self._run_cli(prompt, path=work_dir, model=model,
                             output_format="text", stream_output=False)

    def run_task(self, task: str, path: str = None,
                 model: str = "sonnet", **kwargs) -> str:
        """Run an arbitrary task with Claude CLI."""
        work_dir = path or self.default_path
        return self._run_cli(task, path=work_dir, model=model,
                             output_format="text", stream_output=False)

    def batch_process(self, items: List[str], instruction: str,
                      model: str = "sonnet", **kwargs) -> List[Dict]:
        """Process a batch of items with the same instruction."""
        results = []
        for item in items:
            prompt = f"{instruction}\n\nItem: {item}"
            try:
                out = self._run_cli(prompt, model=model,
                                    output_format="text", stream_output=False)
                results.append({"item": item, "result": out, "status": "ok"})
            except Exception as e:
                results.append({"item": item, "error": str(e), "status": "error"})
        return results

    # ── job management (server) ───────────────────────────────────

    def submit(self, prompt: str, model: str = "sonnet", work_dir: str = None,
               module_name: str = None, creation_mode: str = None,
               github_url: str = None, anchor_dir: str = None,
               key: str = None, **kwargs) -> dict:
        """Submit a background job to the Rust server."""
        data = {"prompt": prompt, "model": model}
        data["user_address"] = self._resolve_address(key)
        if work_dir:
            data["work_dir"] = work_dir
        if module_name:
            data["module_name"] = module_name
        if creation_mode:
            data["creation_mode"] = creation_mode
        if github_url:
            data["github_url"] = github_url
        if anchor_dir:
            data["anchor_dir"] = anchor_dir
        return self._request("POST", "/jobs", data)

    def jobs(self) -> list:
        """List all background jobs."""
        return self._request("GET", "/jobs").get("jobs", [])

    def job(self, job_id: str) -> dict:
        """Get job details."""
        return self._request("GET", f"/jobs/{job_id}")

    def cancel(self, job_id: str) -> dict:
        """Cancel a running job."""
        return self._request("POST", f"/jobs/{job_id}/cancel")

    def kill(self, pid: int = None, port: int = None, sig: str = "SIGKILL") -> dict:
        """Kill a process by PID, port, or both claude services (API+App) if no args given. Owner-only."""
        self.require_owner(operation="kill")

        # No args → kill both claude services by port
        if pid is None and port is None:
            killed = []
            api_port = self.config.get('port', 8820)
            app_port = self.config.get('app_port', 8821)
            for svc_port in [api_port, app_port]:
                try:
                    result = subprocess.run(
                        ['lsof', '-ti', f':{svc_port}'],
                        capture_output=True, text=True,
                    )
                    for p in result.stdout.strip().split('\n'):
                        if p.strip():
                            os.kill(int(p), signal.SIGTERM)
                            killed.append(p.strip())
                except Exception:
                    pass
            self._api_managed = False
            return {'status': 'killed', 'killed': killed}

        # Specific PID or port → delegate to API server
        data = {"signal": sig}
        if pid is not None:
            data["pid"] = pid
        elif port is not None:
            data["port"] = port
        return self._request("POST", "/kill", data)

    def kill_port(self, port: int, sig: str = "SIGKILL") -> dict:
        """Kill all processes on a port. Owner-only."""
        return self.kill(port=port, sig=sig)

    def delete_job(self, job_id: str) -> dict:
        """Delete a job."""
        return self._request("DELETE", f"/jobs/{job_id}")

    def logs(self, job_id: str) -> str:
        """Get job output text."""
        return self._request("GET", f"/jobs/{job_id}").get("output", "")

    def tail(self, job_id: str) -> None:
        """Live-stream job output via SSE. Ctrl-C to detach."""
        print(f"Streaming job {job_id[:8]}...")
        self._stream(f"/jobs/{job_id}/stream")

    # ── local background jobs (no server) ─────────────────────────

    def bg(self, prompt: str, path: str = None, mod: str = None,
           model: str = "sonnet", log_dir: str = None, **kwargs) -> dict:
        """Run a background job locally with nohup. Returns {pid, log_file}."""
        claude = self._find_claude()
        work_dir = path or (m.dp(mod) if mod else self.default_path)
        log_dir = log_dir or os.path.join(self._module_dir(), '.logs')
        os.makedirs(log_dir, exist_ok=True)

        ts = int(time.time())
        log_file = os.path.join(log_dir, f"job_{ts}.log")
        base = ['node', claude] if claude.endswith('.js') else [claude]
        cmd = base + ["--print", "--model", model, "--output-format", "text",
                      "--dangerously-skip-permissions", prompt]

        with open(log_file, 'w') as lf:
            proc = subprocess.Popen(
                cmd, cwd=work_dir, stdout=lf, stderr=lf,
                env=os.environ.copy(), start_new_session=True
            )

        return {"pid": proc.pid, "log_file": log_file, "model": model, "prompt": prompt[:80]}

    def bg_status(self, pid: int) -> str:
        """Check if a background process is still running."""
        try:
            os.kill(pid, 0)
            return "running"
        except OSError:
            return "completed"

    def bg_list(self, log_dir: str = None) -> list:
        """List local background job logs."""
        log_dir = log_dir or os.path.join(self._module_dir(), '.logs')
        if not os.path.isdir(log_dir):
            return []
        logs = []
        for f in sorted(Path(log_dir).glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True):
            logs.append({
                "file": str(f),
                "name": f.name,
                "size": f.stat().st_size,
                "modified": time.ctime(f.stat().st_mtime),
            })
        return logs

    # ── module ops ────────────────────────────────────────────────

    def create_module(self, module_name: str, prompt: str,
                      model: str = "sonnet", anchor_dir: str = None,
                      key: str = None) -> dict:
        """Create a new orbit module via background job. Requires permission."""
        self.require_permission(key, module=module_name, operation="create_module")
        return self.submit(prompt=prompt, model=model, module_name=module_name,
                           creation_mode="new", anchor_dir=anchor_dir, key=key)

    def edit_module(self, module_name: str, prompt: str,
                    model: str = "sonnet", key: str = None) -> dict:
        """Edit an existing module via background job. Requires permission."""
        self.require_permission(key, module=module_name, operation="edit_module")
        return self.submit(prompt=prompt, model=model, module_name=module_name,
                           creation_mode="edit", key=key)

    # ── read-only helpers (API endpoints) ─────────────────────────

    def health(self) -> dict:
        return self._request("GET", "/health")

    def modules(self, search: str = None) -> list:
        """List orbit/core modules. Falls back to m.mods() if server is down."""
        try:
            q = f"?q={search}" if search else ""
            return self._request("GET", f"/modules{q}").get("modules", [])
        except ConnectionError:
            all_mods = m.mods(search)
            return all_mods if isinstance(all_mods, list) else list(all_mods)

    def folders(self, search: str = None, path: str = None, depth: int = 2) -> list:
        """List folders under a path. Defaults to ~/mod. Returns [{name, path, has_config}]."""
        base = os.path.expanduser(path or self.default_path)
        results = []
        for root, dirs, files in os.walk(base):
            rel = os.path.relpath(root, base)
            level = 0 if rel == '.' else rel.count(os.sep) + 1
            if level > depth:
                dirs.clear()
                continue
            # skip hidden/build dirs
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in
                       ('node_modules', '__pycache__', 'target', 'build', 'dist', '.next', 'venv', '.venv')]
            for d in dirs:
                full = os.path.join(root, d)
                name = os.path.relpath(full, base)
                if search and search.lower() not in name.lower():
                    continue
                has_config = os.path.exists(os.path.join(full, 'config.json'))
                has_mod = os.path.exists(os.path.join(full, 'mod.py'))
                results.append({
                    'name': name,
                    'path': full,
                    'has_config': has_config,
                    'has_mod': has_mod,
                })
        results.sort(key=lambda x: x['name'])
        return results

    def suggest_folders(self, query: str, path: str = None, top_k: int = 10) -> list:
        """Suggest folders using embedcode similarity search.

        Embeds the codebase (if not already) and returns folders ranked by
        semantic similarity to the query.

        Returns: [{name, path, score, preview}]
        """
        try:
            ec = m.mod('embedcode')()
        except Exception:
            return []
        base = os.path.expanduser(path or self.default_path)
        # ensure the codebase is embedded
        collections = ec.collections()
        col_name = ec._collection_name(base)
        if col_name not in collections.get('collections', []):
            ec.embed(base)
        # search
        results = ec.search(query=query, path=base, top_k=top_k * 5)
        # group by folder — pick best score per folder
        folder_scores = {}
        for r in results:
            folder = os.path.dirname(r['path'])
            rel = os.path.relpath(folder, base)
            if rel not in folder_scores or r['score'] > folder_scores[rel]['score']:
                folder_scores[rel] = {
                    'name': rel,
                    'path': folder,
                    'score': r['score'],
                    'preview': r.get('preview', '')[:120],
                }
        ranked = sorted(folder_scores.values(), key=lambda x: x['score'], reverse=True)
        return ranked[:top_k]

    def repos(self, search: str = None) -> list:
        q = f"?q={search}" if search else ""
        return self._request("GET", f"/repos{q}").get("repos", [])

    def files(self, path: str = "~/mod", depth: int = 3) -> list:
        return self._request("GET", f"/files/tree?path={path}&depth={depth}").get("tree", [])

    def owner(self) -> dict:
        """Get owner info. Works offline too."""
        return {"owner": self._owner, "has_owner": bool(self._owner)}

    # ── IPFS versioning ──────────────────────────────────────────

    def _history_path(self) -> Path:
        return self._history_dir / 'cid_history.json'

    def _load_history(self) -> list:
        hp = self._history_path()
        if hp.exists():
            with open(hp, 'r') as f:
                return json.load(f)
        return []

    def _save_history(self, history: list):
        self._history_dir.mkdir(parents=True, exist_ok=True)
        with open(self._history_path(), 'w') as f:
            json.dump(history, f, indent=2)

    def _add_to_history(self, cid: str, description: str, version: str = None):
        history = self._load_history()
        entry = {
            "cid": cid,
            "description": description,
            "timestamp": time.time(),
            "date": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if version:
            entry["version"] = version
        history.append(entry)
        self._save_history(history)

    def get_history(self, limit: int = None) -> list:
        """Get version history, newest first."""
        history = self._load_history()
        history.reverse()
        if limit:
            history = history[:limit]
        return history

    def get_latest_cid(self) -> Optional[str]:
        """Get the most recent CID from history."""
        history = self._load_history()
        return history[-1]["cid"] if history else None

    def snapshot(self, description: str = None, version: str = None, **kwargs) -> dict:
        """Take an IPFS snapshot of the module's source files."""
        try:
            ipfs = m.mod('ipfs')()
        except Exception as e:
            return {"error": f"IPFS not available: {e}"}

        # collect source files
        mod_dir = self._module_dir()
        content = {}
        for ext in ('py', 'json', 'toml', 'rs', 'ts', 'tsx', 'js', 'css'):
            for fp in Path(mod_dir).rglob(f"*.{ext}"):
                rel = str(fp.relative_to(mod_dir))
                if 'node_modules' in rel or 'target' in rel or '.history' in rel:
                    continue
                try:
                    content[rel] = fp.read_text()
                except Exception:
                    pass

        cid = ipfs.put(json.dumps(content))
        desc = description or f"Snapshot {version or time.strftime('%Y%m%d-%H%M%S')}"
        self._add_to_history(cid, desc, version=version)

        return {
            "cid": cid,
            "version": version,
            "description": desc,
            "files": len(content),
            "gateway": f"https://ipfs.io/ipfs/{cid}",
        }

    def changelog(self, limit: int = None) -> list:
        """Get the version changelog (newest first)."""
        return self.get_history(limit=limit)

    def show_changelog(self, limit: int = 20):
        """Display changelog in terminal."""
        history = self.get_history(limit=limit)
        if not history:
            print("No history entries found.")
            return
        print(f"\n{'='*60}")
        print(f"  IPFS CID HISTORY  ({len(history)} entries)")
        print(f"{'='*60}")
        for i, entry in enumerate(history):
            ver = entry.get('version', '')
            ver_str = f"  [{ver}]" if ver else ""
            print(f"\n  {i+1}. {entry['cid'][:16]}...{ver_str}")
            print(f"     {entry.get('date', 'unknown date')}")
            print(f"     {entry.get('description', '')}")
        print(f"\n{'='*60}\n")

    def get_version(self, version: str = None, cid: str = None) -> Optional[dict]:
        """Retrieve a version entry by version label or CID."""
        history = self._load_history()
        for entry in reversed(history):
            if version and entry.get('version') == version:
                return entry
            if cid and entry.get('cid') == cid:
                return entry
        return None

    def restore_version(self, version: str = None, cid: str = None,
                        dry_run: bool = True) -> dict:
        """Restore the module to a previous version from IPFS."""
        entry = self.get_version(version=version, cid=cid)
        if not entry:
            return {"error": f"Version not found: {version or cid}"}

        try:
            ipfs = m.mod('ipfs')()
            data = json.loads(ipfs.get(entry['cid']))
        except Exception as e:
            return {"error": f"Failed to fetch from IPFS: {e}"}

        if dry_run:
            return {
                "dry_run": True,
                "cid": entry['cid'],
                "files": list(data.keys()) if isinstance(data, dict) else [],
                "description": entry.get('description'),
            }

        mod_dir = self._module_dir()
        restored = []
        for rel_path, content in data.items():
            fp = Path(mod_dir) / rel_path
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content)
            restored.append(rel_path)

        return {
            "restored": restored,
            "cid": entry['cid'],
            "count": len(restored),
        }

    # ── conversational (OpenRouter) ───────────────────────────────

    def ask(self, message: str, model: str = 'anthropic/claude-opus-4',
            stream: bool = False, **kwargs) -> str:
        """Send a message via OpenRouter API."""
        router = m.mod('model.openrouter')()
        return router.forward(message, model=model, stream=stream, **kwargs)

    def models(self, search: str = 'claude') -> list:
        """List models available via OpenRouter."""
        router = m.mod('model.openrouter')()
        return router.models(search=search)

    # ── remote setup ─────────────────────────────────────────────

    def _ssh_cmd(self, host: str, key_path: str = None) -> list:
        """Build base SSH command list."""
        cmd = ['ssh', '-o', 'StrictHostKeyChecking=no',
               '-o', 'ConnectTimeout=10']
        if key_path:
            cmd += ['-i', os.path.expanduser(key_path)]
        cmd.append(host)
        return cmd

    def _ssh(self, host: str, cmd: str, key_path: str = None,
             timeout: int = 60) -> tuple:
        """Run a command over SSH. Returns (ok, stdout, stderr)."""
        r = subprocess.run(
            self._ssh_cmd(host, key_path) + [cmd],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode == 0, r.stdout.strip(), r.stderr.strip()

    def _ssh_source_nvm(self, cmd: str) -> str:
        """Wrap a command so nvm-installed binaries are on PATH."""
        return f'export NVM_DIR="$HOME/.nvm" && [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"; {cmd}'

    def install(self, host: str = None, key_path: str = None, **kwargs) -> dict:
        """Install Claude CLI locally or on a remote host.

        Without args: installs via npm and prompts for auth token.
        With host: installs on remote host over SSH.

        Usage:
            m claude/install              # local install + auth setup
            m claude/install user@myserver # remote install
        """
        if not host:
            return self._install_local(**kwargs)

        print(f'[install] installing claude on {host}...')
        ok, out, err = self._ssh(host, 'npm install -g @anthropic-ai/claude-code',
                                 key_path=key_path, timeout=120)
        if not ok:
            # retry with nvm sourced
            ok, out, err = self._ssh(
                host, self._ssh_source_nvm('npm install -g @anthropic-ai/claude-code'),
                key_path=key_path, timeout=120,
            )
        if not ok:
            print(f'[install] failed: {err}')
            return {'ok': False, 'error': err, 'host': host}

        # verify
        ok, ver, _ = self._ssh(host, self._ssh_source_nvm('claude --version'),
                               key_path=key_path)
        print(f'[install] done — claude {ver}' if ok else '[install] installed but version check failed')
        return {'ok': True, 'host': host, 'version': ver if ok else None}

    def setup(self, host: str = None, key_path: str = None, **kwargs) -> dict:
        """Ensure Claude CLI is installed on a remote host via SSH.

        Checks connectivity, node/npm, installs claude if missing,
        checks for ANTHROPIC_API_KEY.

        Args:
            host: SSH target (e.g. 'user@1.2.3.4' or 'myserver')
            key_path: path to SSH key (optional, uses default if omitted)

        Usage:
            m claude/setup user@myserver
            m claude/setup user@myserver key_path=~/.ssh/id_ed25519
        """
        if not host:
            return {'error': 'host required — e.g. m claude/setup user@myserver'}

        results = {'host': host, 'steps': []}

        # 1. connectivity
        print(f'[setup] connecting to {host}...')
        ok, _, err = self._ssh(host, 'echo ok', key_path=key_path)
        if not ok:
            return {'error': f'SSH connection failed: {err}', 'host': host}
        results['steps'].append({'name': 'ssh_connect', 'ok': True})
        print(f'[setup] connected')

        # 2. node
        ok, node_ver, _ = self._ssh(host, self._ssh_source_nvm('node --version'), key_path=key_path)
        if not ok:
            print(f'[setup] node not found — installing via nvm...')
            install_cmd = (
                'curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash '
                '&& export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh" '
                '&& nvm install --lts'
            )
            ok, _, err = self._ssh(host, install_cmd, key_path=key_path, timeout=120)
            if not ok:
                return {'error': f'Failed to install node: {err}', 'host': host, 'steps': results['steps']}
            results['steps'].append({'name': 'node_install', 'ok': True})
            print(f'[setup] node installed')
        else:
            results['steps'].append({'name': 'node', 'ok': True, 'version': node_ver})
            print(f'[setup] node {node_ver}')

        # 3. claude — check, install if missing
        ok, ver, _ = self._ssh(host, self._ssh_source_nvm('claude --version'), key_path=key_path)
        if ok:
            results['steps'].append({'name': 'claude', 'ok': True, 'version': ver})
            print(f'[setup] claude {ver}')
        else:
            r = self.install(host=host, key_path=key_path)
            results['steps'].append({'name': 'claude_install', 'ok': r.get('ok', False),
                                     'version': r.get('version')})
            if not r.get('ok'):
                return {'error': r.get('error'), 'host': host, 'steps': results['steps']}

        # 4. ANTHROPIC_API_KEY
        ok, has_key, _ = self._ssh(host, 'test -n "$ANTHROPIC_API_KEY" && echo yes || echo no',
                                   key_path=key_path)
        if has_key == 'yes':
            results['steps'].append({'name': 'api_key', 'ok': True})
            print(f'[setup] ANTHROPIC_API_KEY set')
        else:
            results['steps'].append({'name': 'api_key', 'ok': False,
                                     'warning': 'ANTHROPIC_API_KEY not set'})
            print(f'[setup] warning: ANTHROPIC_API_KEY not set on remote')

        # 5. final verify
        ok, ver, _ = self._ssh(host, self._ssh_source_nvm('claude --version'), key_path=key_path)
        results['claude_version'] = ver if ok else None
        results['ready'] = ok
        print(f'[setup] ready — claude {ver} on {host}' if ok else '[setup] not ready')

        return results

    def _install_local(self, **kwargs) -> dict:
        """Install Claude Code locally into the module's node_modules."""
        results = {'steps': []}
        mod_dir = self._module_dir()

        # 1. check if already installed locally
        local_bin = os.path.join(mod_dir, 'node_modules', '.bin', 'claude')
        if os.path.isfile(local_bin) and os.access(local_bin, os.X_OK):
            r = subprocess.run([local_bin, '--version'], capture_output=True, text=True, timeout=10)
            ver = r.stdout.strip() if r.returncode == 0 else 'unknown'
            print(f'[install] claude already installed locally: {ver}')
            results['steps'].append({'name': 'check', 'ok': True, 'version': ver})
        else:
            print('[install] installing @anthropic-ai/claude-code locally...')
            r = subprocess.run(
                ['npm', 'install', '@anthropic-ai/claude-code'],
                cwd=mod_dir, capture_output=True, text=True, timeout=120,
            )
            if r.returncode != 0:
                print(f'[install] npm install failed: {r.stderr[-300:]}')
                return {'ok': False, 'error': r.stderr}
            # verify — find the actual binary (npx fallback if .bin symlink missing)
            ver = 'unknown'
            if os.path.isfile(local_bin):
                r2 = subprocess.run([local_bin, '--version'], capture_output=True, text=True, timeout=10)
                ver = r2.stdout.strip() if r2.returncode == 0 else 'unknown'
            else:
                r2 = subprocess.run(['npx', 'claude', '--version'],
                                    cwd=mod_dir, capture_output=True, text=True, timeout=10)
                ver = r2.stdout.strip() if r2.returncode == 0 else 'installed'
            print(f'[install] installed claude {ver} → {mod_dir}/node_modules/')
            results['steps'].append({'name': 'npm_install', 'ok': True, 'version': ver})

        # 2. check for existing auth
        token = self._read_keychain_token()
        api_key = os.environ.get('ANTHROPIC_API_KEY')
        if token:
            print(f'[install] auth token found in keychain ({token[:8]}...{token[-4:]})')
            results['steps'].append({'name': 'auth', 'ok': True, 'exists': True})
        elif api_key:
            print(f'[install] ANTHROPIC_API_KEY already set in env ({api_key[:8]}...)')
            results['steps'].append({'name': 'auth', 'ok': True, 'env_key': True})
        else:
            print('')
            print('[install] no credentials found. pick one:')
            print('')
            print('  1) paste auth token  — from a machine already logged in:')
            print('     run `m claude/_authtoken` on that machine, copy the token')
            print('')
            print('  2) set API key       — paste your ANTHROPIC_API_KEY')
            print('     (get one at https://console.anthropic.com/settings/keys)')
            print('')
            print('  3) skip              — press Enter, run `claude login` later')
            print('')
            try:
                choice = input('[install] enter token, API key, or press Enter to skip:\n> ').strip()
            except (EOFError, KeyboardInterrupt):
                choice = ''
            if choice:
                # detect API key (sk-ant-...) vs auth token
                if choice.startswith('sk-ant-'):
                    # write to shell profile
                    profile = self._detect_shell_profile()
                    export_line = f'\nexport ANTHROPIC_API_KEY="{choice}"\n'
                    with open(profile, 'a') as f:
                        f.write(export_line)
                    os.environ['ANTHROPIC_API_KEY'] = choice
                    print(f'[install] API key saved to {profile}')
                    print(f'[install] run `source {profile}` or open a new terminal')
                    results['steps'].append({'name': 'auth', 'ok': True, 'method': 'api_key'})
                else:
                    ok = self._write_keychain_token(choice)
                    if ok:
                        print('[install] auth token saved to keychain')
                        results['steps'].append({'name': 'auth', 'ok': True, 'method': 'token'})
                    else:
                        print('[install] failed to save token — run `claude login` to authenticate')
                        results['steps'].append({'name': 'auth', 'ok': False})
            else:
                print('[install] skipped — run `claude login` to authenticate')
                results['steps'].append({'name': 'auth', 'ok': False, 'skipped': True})

        results['ok'] = True
        return results

    def _authtoken(self, token: str = None, **kwargs) -> dict:
        """Get or set the Claude Code auth token (macOS Keychain).

        Without args: reads and displays the current token for transfer.
        With token arg: writes the token to keychain.

        Usage:
            m claude/_authtoken               # show current token
            m claude/_authtoken <token>       # set token from another machine
        """
        if token:
            ok = self._write_keychain_token(token)
            if ok:
                print(f'[authtoken] saved to keychain ({token[:8]}...{token[-4:]})')
                return {'ok': True, 'action': 'set'}
            return {'ok': False, 'error': 'failed to write to keychain'}

        current = self._read_keychain_token()
        if current:
            print(f'[authtoken] {current}')
            return {'ok': True, 'token': current}
        print('[authtoken] no token found — run `claude login` first')
        return {'ok': False, 'error': 'no token in keychain'}

    def _detect_shell_profile(self) -> str:
        """Return the user's shell profile path."""
        shell = os.environ.get('SHELL', '/bin/zsh')
        home = os.path.expanduser('~')
        if 'zsh' in shell:
            return os.path.join(home, '.zshrc')
        return os.path.join(home, '.bashrc')

    def _read_keychain_token(self) -> Optional[str]:
        """Read Claude Code credentials from macOS Keychain."""
        try:
            r = subprocess.run(
                ['security', 'find-generic-password',
                 '-s', 'Claude Code-credentials', '-w'],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except Exception:
            pass
        return None

    def _write_keychain_token(self, token: str) -> bool:
        """Write Claude Code credentials to macOS Keychain."""
        try:
            # delete existing entry first (ignore errors if not found)
            subprocess.run(
                ['security', 'delete-generic-password',
                 '-s', 'Claude Code-credentials'],
                capture_output=True, text=True, timeout=10,
            )
            # add new entry
            r = subprocess.run(
                ['security', 'add-generic-password',
                 '-s', 'Claude Code-credentials',
                 '-a', os.environ.get('USER', 'claude'),
                 '-w', token],
                capture_output=True, text=True, timeout=10,
            )
            return r.returncode == 0
        except Exception:
            return False

    # ── environment ──────────────────────────────────────────────

    def _install_rustup(self) -> Optional[str]:
        """Install rustup non-interactively and return path to cargo, or None on failure."""
        cargo_bin = os.path.expanduser('~/.cargo/bin/cargo')
        if os.path.exists(cargo_bin):
            return cargo_bin
        print('[claude] cargo missing — installing rustup (this may take a minute)')
        try:
            r = subprocess.run(
                ['sh', '-c', 'curl --proto "=https" --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable --profile minimal'],
                capture_output=True, text=True, timeout=600,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            print(f'[claude] rustup install failed: {e}')
            return None
        if r.returncode != 0 or not os.path.exists(cargo_bin):
            print(f'[claude] rustup install failed: {r.stderr[-200:]}')
            return None
        print('[claude] rustup installed')
        return cargo_bin

    def ensure_env(self, app_dir: str = None, api_dir: str = None):
        """Ensure runtime dependencies are installed for app and API.

        Checks node_modules for Next.js app and Rust binary for API.
        Installs/builds if missing. Called automatically by serve().
        """
        results = {}
        mod_dir = Path(self._module_dir())
        app_dir = Path(app_dir) if app_dir else mod_dir / 'src' / 'app'
        api_dir = Path(api_dir) if api_dir else mod_dir / 'src' / 'api'

        # ── App: node_modules ──
        if (app_dir / 'package.json').exists():
            if not (app_dir / 'node_modules').is_dir():
                print('[claude] node_modules missing — running npm install')
                try:
                    r = subprocess.run(
                        ['npm', 'install'], cwd=str(app_dir),
                        capture_output=True, text=True, timeout=120,
                    )
                except FileNotFoundError:
                    results['app_install'] = {'ok': False, 'error': 'npm not found in PATH'}
                    print('[claude] npm install failed: npm not found in PATH')
                else:
                    if r.returncode != 0:
                        results['app_install'] = {'ok': False, 'error': r.stderr[-500:]}
                        print(f'[claude] npm install failed: {r.stderr[-200:]}')
                    else:
                        results['app_install'] = {'ok': True}
                        print('[claude] npm install done')
            else:
                results['app_install'] = {'ok': True, 'cached': True}

        # ── API: Rust binary ──
        binary = api_dir / 'target' / 'release' / 'claude-jobs'
        if (api_dir / 'Cargo.toml').exists() and not binary.exists():
            import shutil
            cargo = shutil.which('cargo') or (
                os.path.expanduser('~/.cargo/bin/cargo')
                if os.path.exists(os.path.expanduser('~/.cargo/bin/cargo')) else None
            )
            if not cargo:
                cargo = self._install_rustup()
            if not cargo:
                results['api_build'] = {'ok': False, 'error': 'cargo not found and rustup install failed'}
            else:
                print('[claude] API binary missing — running cargo build --release')
                try:
                    r = subprocess.run(
                        [cargo, 'build', '--release'], cwd=str(api_dir),
                        capture_output=True, text=True, timeout=600,
                    )
                except FileNotFoundError:
                    results['api_build'] = {'ok': False, 'error': 'cargo not found in PATH'}
                    print('[claude] cargo build failed: cargo not found in PATH')
                else:
                    if r.returncode != 0:
                        results['api_build'] = {'ok': False, 'error': r.stderr[-500:]}
                        print(f'[claude] cargo build failed: {r.stderr[-200:]}')
                    else:
                        results['api_build'] = {'ok': True}
                        print('[claude] cargo build done')
        else:
            results['api_build'] = {'ok': True, 'cached': True}

        return results

    def _check_service(self, url: str, retries: int = 20, interval: float = 0.5) -> bool:
        """Poll a URL until it responds or retries are exhausted."""
        for _ in range(retries):
            try:
                req = urllib.request.Request(url, method='GET')
                with urllib.request.urlopen(req, timeout=2) as resp:
                    if resp.status < 500:
                        return True
            except Exception:
                pass
            time.sleep(interval)
        return False

    def _tail_log(self, log_path: str, lines: int = 30) -> str:
        """Read the last N lines of a log file."""
        try:
            r = subprocess.run(
                ['tail', '-n', str(lines), log_path],
                capture_output=True, text=True,
            )
            return r.stdout
        except Exception:
            return ''

    # ── serve ─────────────────────────────────────────────────────

    def _serve_pm2(self, api_port, app_port, dev, results) -> bool:
        """Launch claude-api + claude-app under pm2 (the root deployment path).

        Returns True if pm2 is available and the start command succeeded, in
        which case serve() skips the Popen fallback. Running under pm2 as root is
        what gives the API filesystem access to edit sibling modules; every
        cross-module mutation still demands a per-op sudo signature (sudo.rs).
        """
        import shutil
        if not shutil.which('pm2'):
            return False
        module_dir = Path(self._module_dir())
        eco = module_dir / 'ecosystem.config.js'
        api_bin = module_dir / 'src' / 'api' / 'target' / 'release' / 'claude-jobs'
        if not eco.exists():
            return False
        # Ensure the API binary exists (pm2 runs it directly — no build-on-start).
        if not api_bin.exists():
            print('[claude] building API binary for pm2…')
            build = subprocess.run(
                ['cargo', 'build', '--release'],
                cwd=str(module_dir / 'src' / 'api'),
                capture_output=True, text=True,
            )
            if build.returncode != 0 or not api_bin.exists():
                print('[claude] API build failed — falling back to start.sh')
                return False
        env = os.environ.copy()
        env['CLAUDE_MODE'] = 'dev' if dev else 'prod'
        env['CLAUDE_API_PORT'] = str(api_port)
        env['CLAUDE_APP_PORT'] = str(app_port)
        # NOTE: deliberately NOT setting CLAUDE_JOBS_LOCAL — as root we must keep
        # auth + the sudo gate on.
        subprocess.run(['pm2', 'delete', 'claude-api', 'claude-app'],
                       capture_output=True, text=True)
        proc = subprocess.run(['pm2', 'start', str(eco)], cwd=str(module_dir),
                              env=env, capture_output=True, text=True)
        if proc.returncode != 0:
            print(f'[claude] pm2 start failed: {proc.stderr[-300:]}')
            return False
        subprocess.run(['pm2', 'save'], capture_output=True, text=True)
        results['api'] = f'http://localhost:{api_port}'
        results['app'] = f'http://localhost:{app_port}'
        results['pm2'] = True
        print(f'[claude] pm2: claude-api :{api_port}  claude-app :{app_port}')
        return True

    def serve(self, api_port=None, app_port=None, dev=True):
        """Start the claude API (Rust job server) and Next.js app.

        Ensures dependencies are installed, starts both services,
        verifies they come up, and registers in the namespace.
        """
        api_port = int(api_port or self.config.get('port', 8820))
        app_port = int(app_port or self.config.get('app_port', 8821))
        log_dir = Path('/tmp/claude')
        log_dir.mkdir(parents=True, exist_ok=True)
        results = {}

        self.kill()

        api_url = f'http://localhost:{api_port}'
        app_url = f'http://localhost:{app_port}'
        app_dir = Path(self._module_dir()) / 'src' / 'app'
        api_dir = Path(self._module_dir()) / 'src' / 'api'

        # ── Ensure environment before starting ──
        env_result = self.ensure_env(app_dir=str(app_dir), api_dir=str(api_dir))
        results['env'] = env_result

        # App env reused by the pm2 launch and the Popen fallback/retry below.
        app_env = os.environ.copy()
        # Frontend talks to the core gateway at /api/claude, not the API directly.
        # NEXT_PUBLIC_API_PORT is only used for in-app start/stop service mgmt.
        app_env['NEXT_PUBLIC_BASE_PATH'] = '/claude'
        app_env['NEXT_PUBLIC_API_PORT'] = str(api_port)
        app_env['PORT'] = str(app_port)
        app_cmd = ['npx', 'next', 'dev' if dev else 'start', '-p', str(app_port)]

        # ── Preferred path: pm2 as root (gives the API filesystem access to edit
        #    sibling modules). Cross-module writes still require a per-op sudo
        #    signature, enforced in the Rust API regardless of how it's launched.
        launched_pm2 = self._serve_pm2(api_port, app_port, dev, results)

        # ── API (Rust job server) — Popen fallback when pm2 is unavailable ──
        start_sh = api_dir / 'start.sh'
        if not launched_pm2 and start_sh.exists():
            api_log = open(log_dir / 'api.log', 'w')
            subprocess.Popen(
                ['bash', str(start_sh), str(api_port)],
                cwd=str(api_dir), stdout=api_log, stderr=subprocess.STDOUT,
            )
            results['api'] = api_url
            results['api_log'] = str(log_dir / 'api.log')

        # ── App (Next.js) — Popen fallback when pm2 is unavailable ──
        if not launched_pm2 and (app_dir / 'package.json').exists():
            app_log = open(log_dir / 'app.log', 'w')
            subprocess.Popen(
                app_cmd, cwd=str(app_dir), env=app_env,
                stdout=app_log, stderr=subprocess.STDOUT,
            )
            results['app'] = app_url
            results['app_log'] = str(log_dir / 'app.log')

        # ── Verify services come up ──
        checks = {}
        if 'api' in results:
            api_live = self._check_service(f'{api_url}/health')
            checks['api'] = {'live': api_live}
            if not api_live:
                tail = self._tail_log(str(log_dir / 'api.log'))
                checks['api']['error'] = tail
                print(f'[claude] API failed to start on :{api_port}')
                if tail:
                    print(tail[-300:])
            else:
                print(f'[claude] API live on :{api_port}')

        if 'app' in results:
            app_live = self._check_service(f'{app_url}/claude')
            checks['app'] = {'live': app_live}
            if not app_live:
                tail = self._tail_log(str(log_dir / 'app.log'))
                checks['app']['error'] = tail
                print(f'[claude] App failed to start on :{app_port}')
                # ── Auto-recover: reinstall deps and retry once ──
                if not env_result.get('app_install', {}).get('cached'):
                    pass  # already freshly installed, don't retry
                else:
                    print('[claude] retrying — reinstalling node_modules')
                    import shutil
                    nm = app_dir / 'node_modules'
                    if nm.is_dir():
                        shutil.rmtree(str(nm), ignore_errors=True)
                    self.ensure_env(app_dir=str(app_dir))
                    # kill stale process on the port and restart
                    subprocess.run(['lsof', '-ti', f':{app_port}'],
                                   capture_output=True, text=True)
                    app_log2 = open(log_dir / 'app.log', 'w')
                    subprocess.Popen(
                        app_cmd, cwd=str(app_dir), env=app_env,
                        stdout=app_log2, stderr=subprocess.STDOUT,
                    )
                    app_live = self._check_service(f'{app_url}/claude')
                    checks['app']['retry'] = True
                    checks['app']['live'] = app_live
                    if app_live:
                        print(f'[claude] App recovered on :{app_port}')
                    else:
                        tail = self._tail_log(str(log_dir / 'app.log'))
                        checks['app']['error'] = tail
                        print(f'[claude] App still failing after retry')
            else:
                print(f'[claude] App live on :{app_port}')

        results['checks'] = checks

        # ── Register in app namespace so gateway routes /claude → here ──
        try:
            registry = m.mod('server.namespace')()
            registry.reg('claude', api_url)
            registry.reg_app('claude', app_url,
                             owner=self.key.address, api_url=api_url)
        except Exception as e:
            print(f'[claude] namespace registration failed: {e}')

        # ── Register in on-chain Registry (chain mod) ──
        if self.config.get('onchain_registry', False):
            try:
                res = self.register_onchain(data=api_url)
                results['onchain'] = res
                print(f"[claude] on-chain registry: {res.get('status')}")
            except Exception as e:
                print(f'[claude] on-chain registration failed: {e}')

        # ── Snapshot + update CID in registry ──
        all_live = all(c.get('live') for c in checks.values())
        if all_live:
            try:
                snap = self.snapshot(description='deploy')
                cid = snap.get('cid')
                if cid:
                    reg = m.mod('registry')()
                    reg.register('claude', {'schema': cid, 'urls': {'api': api_url, 'app': app_url}},
                                 storage='ipfs')
                    results['cid'] = cid
                    print(f'[claude] CID registered: {cid[:16]}...')
            except Exception as e:
                print(f'[claude] CID registration skipped: {e}')

        # ── Save urls to config ──
        try:
            cfg = self._load_config()
            cfg.setdefault('urls', {})
            cfg['urls']['api'] = api_url
            cfg['urls']['app'] = app_url
            if results.get('cid'):
                cfg['schema'] = results['cid']
            self._save_config(cfg)
        except Exception:
            pass

        results['logs'] = str(log_dir)
        return results

    def logs(self, service='both', lines=100):
        """View logs for API/app servers or background tasks.

        Usage: c claude/logs              — show server logs
               c claude/logs fix_1234     — show background task log
               c claude/logs api          — show API server log only

        Args:
            service: 'api', 'app', 'both', or a task_id (e.g. 'fix_1234567890')
            lines: Number of lines to show (default 100)
        """
        # check if service is a background task ID
        bg_log_dir = os.path.join(self._module_dir(), '.logs')
        task_log = os.path.join(bg_log_dir, f"{service}.log")
        if os.path.exists(task_log):
            pid_file = os.path.join(bg_log_dir, f"{service}.pid")
            status = "unknown"
            pid = None
            if os.path.exists(pid_file):
                with open(pid_file) as pf:
                    pid = int(pf.read().strip())
                status = self.bg_status(pid)

            tail = subprocess.run(
                ['tail', '-n', str(lines), task_log],
                capture_output=True, text=True,
            )
            return {
                'task_id': service,
                'status': status,
                'pid': pid,
                'log_file': task_log,
                'output': tail.stdout,
            }

        # server logs
        log_dir = Path('/tmp/claude')
        results = {}
        for svc in (['api', 'app'] if service == 'both' else [service]):
            log_file = log_dir / f'{svc}.log'
            if log_file.exists():
                tail = subprocess.run(
                    ['tail', '-n', str(lines), str(log_file)],
                    capture_output=True, text=True,
                )
                results[svc] = tail.stdout
            else:
                results[svc] = f'No log file at {log_file}'
        return results

    # ── status ─────────────────────────────────────────────────────

    def status(self) -> dict:
        """Check if claude services (API + App) are running.

        Returns dict with per-service status and overall 'online' bool.
        """
        api_port = self.config.get('port', 8820)
        app_port = self.config.get('app_port', 8821)
        result = {'online': False, 'services': {}}

        for svc, port in [('api', api_port), ('app', app_port)]:
            info = {'port': port, 'reachable': False}
            try:
                url = f'http://localhost:{port}' + ('/health' if svc == 'api' else '/')
                req = urllib.request.Request(url, method='GET')
                with urllib.request.urlopen(req, timeout=2):
                    info['reachable'] = True
            except Exception:
                pass
            result['services'][svc] = info

        result['online'] = any(s['reachable'] for s in result['services'].values())
        return result

    # ── test ──────────────────────────────────────────────────────

    def _run_test(self, results, name, fn):
        """Run a single test, append to results."""
        try:
            fn()
            results["passed"] += 1
            results["tests"].append({"name": name, "status": "ok"})
        except Exception as e:
            results["failed"] += 1
            results["tests"].append({"name": name, "status": "fail", "error": str(e)})

    def test(self) -> dict:
        """Run self-tests. Returns {passed, failed, total}."""
        results = {"passed": 0, "failed": 0, "tests": []}

        # 1: init
        def t_init():
            assert self.config is not None
            assert self.default_path is not None
            assert self.default_path.rstrip('/').endswith('/mod'), \
                f"default_path should end with /mod, got {self.default_path}"
        self._run_test(results, "init", t_init)

        # 2: owner + token auth
        def t_auth():
            assert self._owner is not None, "owner should always be set"
            tok = self.token()
            assert isinstance(tok, str) and len(tok) > 0
            verified = self.verify(tok)
            assert verified['key'] == self.key.address
        self._run_test(results, "owner_auth", t_auth)

        # 3: history
        def t_history():
            h = self.get_history()
            assert isinstance(h, list)
        self._run_test(results, "history", t_history)

        # 4: modules fallback
        def t_modules():
            mods = self.modules()
            assert isinstance(mods, list)
        self._run_test(results, "modules", t_modules)

        # 5: bg_list
        def t_bg_list():
            bl = self.bg_list()
            assert isinstance(bl, list)
        self._run_test(results, "bg_list", t_bg_list)

        # 6: API auto-start (ensure_api)
        def t_ensure_api():
            self._ensure_api()
            assert self._server_available(), "API should be reachable after _ensure_api"
        self._run_test(results, "ensure_api", t_ensure_api)

        # 7: health endpoint
        def t_health():
            h = self.health()
            assert isinstance(h, dict), "health should return a dict"
        self._run_test(results, "health", t_health)

        # 8: submit + fetch job (real integration test)
        def t_job_lifecycle():
            job = self.submit(
                prompt="echo hello from test — respond with just 'test_ok'",
                model="haiku",
                work_dir=os.path.expanduser('~/mod')
            )
            assert 'id' in job, f"submit should return job id, got {job}"
            job_id = job['id']
            # fetch it back
            detail = self.job(job_id)
            assert detail.get('id') == job_id, "job fetch should return same id"
            assert detail.get('status') in ('pending', 'running', 'completed', 'failed')
            # list jobs should include it
            all_jobs = self.jobs()
            ids = [j['id'] for j in all_jobs]
            assert job_id in ids, f"job {job_id} should appear in jobs list"
            # clean up — cancel then delete
            if detail.get('status') in ('pending', 'running'):
                self.cancel(job_id)
            self.delete_job(job_id)
        self._run_test(results, "job_lifecycle", t_job_lifecycle)

        # 9: idle timeout config
        def t_idle_timeout():
            assert self._idle_timeout > 0, "idle_timeout should be positive"
            assert self._last_activity > 0, "last_activity should be set after API use"
        self._run_test(results, "idle_timeout", t_idle_timeout)

        results["total"] = results["passed"] + results["failed"]
        return results

    # ── security scan ──────────────────────────────────────────────

    _scan_goal = """
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
1. First understand the repo structure and file types
2. Search for patterns: passwords, secrets, keys, tokens, eval, exec, subprocess
3. Read critical files: configs, env files, auth modules, API routes, contracts
4. Analyze each finding for actual exploitability (not just pattern matches)

OUTPUT:
You MUST output ONLY a JSON object (no markdown, no explanation) with this exact structure:
{"findings": [{"severity": "critical|high|medium|low|info", "category": "secrets|injection|xss|access_control|config|contract|dependency|crypto|info_leak|other", "title": "short description", "description": "detailed explanation with exploitation scenario", "file": "relative/path", "line": null, "recommendation": "how to fix"}]}
"""

    def scan(self, mod=None, path=None, key=None, model='sonnet', steps=None, **kwargs):
        """
        Run a security scan on a module or repo using Claude CLI.

        Usage: m claude/scan bridge
               m claude/scan path=/some/repo

        Args:
            mod: module name to scan (e.g. 'bridge', 'agent')
            path: repo path to scan (defaults to ~/mod/)
            key: key name for reviewer identity
            model: claude model (sonnet, opus, haiku)
        """
        if mod:
            path = m.dp(mod)
        path = path or os.path.expanduser('~/mod')
        path = os.path.abspath(os.path.expanduser(path))
        key = key or 'test'

        if not os.path.isdir(path):
            return {'error': f'path not found: {path}'}

        wallet = self._scan_resolve_wallet(key)
        print(f'[scan] reviewer: {wallet}')
        print(f'[scan] scanning: {path}')
        print(f'[scan] model: {model}')

        context = self._scan_gather_context(path)
        prompt = self._scan_build_prompt(context)

        ts = time.time()
        raw = self._run_cli(prompt, path=path, model=model,
                            output_format="text", stream_output=True)
        elapsed = round(time.time() - ts, 1)

        findings = self._scan_parse_output(raw)
        print(f'[scan] found {len(findings)} findings in {elapsed}s')

        metadata = {
            'timestamp': int(time.time()),
            'reviewer': wallet,
            'key': key,
            'model': model,
            'repo': path,
            'elapsed_seconds': elapsed,
            'stats': self._scan_compute_stats(findings),
        }

        # sign report with reviewer key
        sig = self._scan_sign_report(key, metadata, findings)
        if sig:
            metadata['signature'] = sig

        report_dir = self._scan_write_report(path, wallet, findings, metadata)

        return {
            'reviewer': wallet,
            'repo': path,
            'findings': findings,
            'stats': metadata['stats'],
            'report_dir': str(report_dir),
            'elapsed': elapsed,
            'signature': sig,
        }

    def _scan_resolve_wallet(self, key):
        try:
            return m.key(key).address
        except Exception:
            return key

    def _scan_sign_report(self, key, metadata, findings):
        """Sign the report hash with the reviewer's key."""
        import hashlib
        try:
            k = m.key(key)
            payload = json.dumps({
                'timestamp': metadata['timestamp'],
                'reviewer': metadata['reviewer'],
                'repo': metadata['repo'],
                'stats': metadata['stats'],
            }, sort_keys=True)
            report_hash = hashlib.sha256(payload.encode()).hexdigest()
            sig = k.sign(report_hash.encode())
            return sig.hex() if isinstance(sig, bytes) else str(sig)
        except Exception as e:
            print(f'[scan] could not sign report: {e}')
            return None

    def _scan_gather_context(self, path):
        context = {'path': path, 'file_types': {}, 'total_files': 0, 'structure': []}
        skip_dirs = {'.git', 'node_modules', '__pycache__', '.next', 'venv',
                     'env', '.env', 'dist', 'build', '.security', 'target'}
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

    def _scan_build_prompt(self, context):
        parts = [
            self._scan_goal,
            f"\nRepository path: {context['path']}",
            f"Total files: {context['total_files']}",
            f"File types: {json.dumps(context['file_types'], indent=2)}",
            f"\nSample structure (first 100 files):",
        ]
        for f in context['structure']:
            parts.append(f"  {f}")
        parts.append(
            "\nPerform a thorough security scan. Focus on high-impact vulnerabilities first. "
            "Output ONLY the JSON findings object, nothing else."
        )
        return '\n'.join(parts)

    def _scan_parse_output(self, raw):
        if not raw:
            return []
        text = raw if isinstance(raw, str) else json.dumps(raw)
        # extract JSON from output (may have surrounding text)
        start = text.find('{"findings"')
        if start == -1:
            start = text.find('"findings"')
            if start != -1:
                start = text.rfind('{', 0, start)
        if start == -1:
            return []
        depth = 0
        end = start
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        try:
            data = json.loads(text[start:end])
            findings = data.get('findings', [])
            return findings if isinstance(findings, list) else [findings]
        except (json.JSONDecodeError, ValueError):
            return []

    def _scan_compute_stats(self, findings):
        stats = {'total': len(findings), 'by_severity': {}, 'by_category': {}}
        for f in findings:
            if not isinstance(f, dict):
                continue
            sev = f.get('severity', 'unknown')
            cat = f.get('category', 'other')
            stats['by_severity'][sev] = stats['by_severity'].get(sev, 0) + 1
            stats['by_category'][cat] = stats['by_category'].get(cat, 0) + 1
        return stats

    def _scan_write_report(self, path, wallet, findings, metadata):
        security_dir = Path(path) / '.security' / wallet
        security_dir.mkdir(parents=True, exist_ok=True)

        report = {**metadata, 'findings': findings}
        report_path = security_dir / 'report.json'
        with open(report_path, 'w') as f:
            json.dump(report, f, indent=2, default=str)
        print(f'[scan] wrote {report_path}')

        summary = self._scan_build_summary(findings, metadata)
        summary_path = security_dir / 'summary.md'
        with open(summary_path, 'w') as f:
            f.write(summary)
        print(f'[scan] wrote {summary_path}')

        return security_dir

    def _scan_build_summary(self, findings, metadata):
        lines = [
            "# Security Scan Report",
            "",
            f"**Repo:** `{metadata['repo']}`",
            f"**Reviewer:** `{metadata['reviewer']}`",
            f"**Model:** `{metadata['model']}`",
            f"**Date:** {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(metadata['timestamp']))}",
            f"**Duration:** {metadata['elapsed_seconds']}s",
        ]
        if metadata.get('signature'):
            lines.append(f"**Signature:** `{metadata['signature'][:32]}...`")
        lines += [
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

    # ── security fix ──────────────────────────────────────────────

    _fix_goal = """
You are a senior security engineer. Fix the security vulnerability described below.

RULES:
- Make the MINIMAL change needed to fix the issue
- Do NOT refactor surrounding code or add unrelated improvements
- Do NOT add comments explaining the fix unless the logic is non-obvious
- Preserve existing code style and patterns
- If the fix requires a new dependency, mention it but still apply the code change

FINDING:
Severity: {severity}
Category: {category}
Title: {title}
File: {file}
Line: {line}
Description: {description}
Recommendation: {recommendation}

Apply the fix now. Edit only the affected file(s).
"""

    def fix(self, mod=None, path=None, key=None, model='sonnet',
            severity=None, index=None, bg=True, **kwargs):
        """
        Fix security findings from a previous scan. Runs scan first if needed.
        Runs in background by default — returns task_id and log path.

        Usage: c claude/fix bridge
               c claude/fix path=/some/repo
               c claude/fix bridge severity=high
               c claude/fix bridge index=0
               c claude/fix bridge bg=false   # run in foreground

        View logs: c claude/logs <task_id>

        Args:
            mod: module name to fix (e.g. 'bridge', 'agent')
            path: repo path (defaults to ~/mod/)
            key: key name for reviewer identity
            model: claude model (sonnet, opus, haiku)
            severity: only fix findings of this severity (critical, high, medium, low)
            index: fix a single finding by index (0-based)
            bg: run in background (default True)
        """
        # background mode — fork and return immediately
        if bg:
            return self._fix_bg(mod=mod, path=path, key=key, model=model,
                                severity=severity, index=index, **kwargs)

        if mod:
            path = m.dp(mod)
        path = path or os.path.expanduser('~/mod')
        path = os.path.abspath(os.path.expanduser(path))
        key = key or 'test'

        if not os.path.isdir(path):
            return {'error': f'path not found: {path}'}

        wallet = self._scan_resolve_wallet(key)

        # load existing scan or run one
        findings = self._fix_load_findings(path, wallet)
        if findings is None:
            print('[fix] no scan data found, running scan first...')
            result = self.scan(mod=mod, path=path, key=key, model=model, **kwargs)
            if 'error' in result:
                return result
            findings = result.get('findings', [])

        if not findings:
            return {'fixed': 0, 'message': 'no findings to fix'}

        # filter by severity
        if severity:
            severity = severity.lower()
            findings = [f for f in findings if f.get('severity', '').lower() == severity]
            if not findings:
                return {'fixed': 0, 'message': f'no {severity} findings to fix'}
            print(f'[fix] filtered to {len(findings)} {severity} findings')

        # filter by index
        if index is not None:
            index = int(index)
            if index < 0 or index >= len(findings):
                return {'error': f'index {index} out of range (0-{len(findings)-1})'}
            findings = [findings[index]]
            print(f'[fix] fixing finding #{index}')

        print(f'[fix] fixing {len(findings)} findings in {path}')
        print(f'[fix] model: {model}')

        fixed = []
        failed = []
        for i, finding in enumerate(findings):
            if not isinstance(finding, dict):
                continue
            title = finding.get('title', 'untitled')
            sev = finding.get('severity', 'unknown')
            print(f'\n[fix] ({i+1}/{len(findings)}) [{sev}] {title}')

            prompt = self._fix_goal.format(
                severity=sev,
                category=finding.get('category', 'other'),
                title=title,
                file=finding.get('file', 'unknown'),
                line=finding.get('line') or 'unknown',
                description=finding.get('description', ''),
                recommendation=finding.get('recommendation', ''),
            )

            try:
                self._run_cli(prompt, path=path, model=model,
                              output_format="text", stream_output=True)
                fixed.append(finding)
                print(f'[fix] done: {title}')
            except Exception as e:
                failed.append({'finding': title, 'error': str(e)})
                print(f'[fix] failed: {title} — {e}')

        print(f'\n[fix] complete: {len(fixed)} fixed, {len(failed)} failed')
        return {
            'fixed': len(fixed),
            'failed': len(failed),
            'details': {
                'fixed': [f.get('title') for f in fixed],
                'failed': failed,
            },
        }

    def _fix_bg(self, mod=None, path=None, key=None, model='sonnet',
                severity=None, index=None, **kwargs):
        """Fork fix to a background process. Returns task_id + log path."""
        log_dir = os.path.join(self._module_dir(), '.logs')
        os.makedirs(log_dir, exist_ok=True)

        ts = int(time.time())
        task_id = f"fix_{ts}"
        log_file = os.path.join(log_dir, f"{task_id}.log")

        # build CLI args
        cmd_parts = ['c', 'claude/fix']
        if mod:
            cmd_parts.append(str(mod))
        cmd_parts.append('bg=false')
        if path:
            cmd_parts.append(f'path={path}')
        if key:
            cmd_parts.append(f'key={key}')
        if model and model != 'sonnet':
            cmd_parts.append(f'model={model}')
        if severity:
            cmd_parts.append(f'severity={severity}')
        if index is not None:
            cmd_parts.append(f'index={index}')

        with open(log_file, 'w') as lf:
            lf.write(f'[fix] task {task_id} started at {time.ctime()}\n')
            lf.write(f'[fix] cmd: {" ".join(cmd_parts)}\n\n')
            lf.flush()
            proc = subprocess.Popen(
                cmd_parts, stdout=lf, stderr=lf,
                env=os.environ.copy(), start_new_session=True
            )

        # write pid file for status tracking
        pid_file = os.path.join(log_dir, f"{task_id}.pid")
        with open(pid_file, 'w') as pf:
            pf.write(str(proc.pid))

        print(f'[fix] background task started: {task_id}')
        print(f'[fix] pid: {proc.pid}')
        print(f'[fix] view logs:  c claude/logs {task_id}')
        print(f'[fix] tail live:  tail -f {log_file}')

        return {"task_id": task_id, "pid": proc.pid, "log_file": log_file}

    def _fix_load_findings(self, path, wallet):
        """Load findings from existing scan report, or return None."""
        report_path = Path(path) / '.security' / wallet / 'report.json'
        if not report_path.exists():
            return None
        try:
            with open(report_path) as f:
                data = json.load(f)
            findings = data.get('findings', [])
            print(f'[fix] loaded {len(findings)} findings from {report_path}')
            return findings
        except (json.JSONDecodeError, IOError):
            return None

    # ── Mod protocol integration ──────────────────────────────────────────
    # These methods route through the mod core/api sidecar so claude doesn't
    # duplicate registry/version/meter logic. URL resolves through caddy
    # (`/api/mod`) when running in-container; falls back to direct port 8000
    # for host/dev. Override via env: MOD_API_URL=http://host:8000.

    @property
    def mod_api_url(self) -> str:
        url = os.environ.get('MOD_API_URL')
        if url:
            return url.rstrip('/')
        # Inside the claude container, the caddy gateway is on host.docker.internal:3000
        # In host/dev mode, the api binds directly to :8000.
        for candidate in ('http://mod-api:8000', 'http://host.docker.internal:8000', 'http://localhost:8000'):
            try:
                with urllib.request.urlopen(f'{candidate}/health', timeout=1) as r:
                    if r.status == 200:
                        return candidate
            except Exception:
                continue
        return 'http://mod-api:8000'  # last-resort default

    def _mod_api(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        """Call the mod core/api sidecar. Path is "/reg", "/versions/foo", etc."""
        url = f"{self.mod_api_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={'Content-Type': 'application/json'} if data else {},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return {'error': f'HTTP {e.code}', 'detail': e.read().decode(errors='replace')}
        except Exception as e:
            return {'error': str(e)}

    def mod_register(self, mod: str, comment: str = '') -> dict:
        """Register/update a module through mod core/api. Replaces the inline
        api/reg POST that claude-jobs makes today — same on-the-wire shape."""
        return self._mod_api('POST', '/reg', {'mod': mod, 'comment': comment})

    def mod_versions(self, mod: str, n: int = 100) -> list:
        """Get a module's version history via mod core/api (single source of truth)."""
        result = self._mod_api('GET', f'/versions?mod={mod}&n={n}')
        return result.get('result', []) if isinstance(result, dict) else []

    def mod_meter(self, fn: str, duration: float, user: str = '', status: str = 'success') -> dict:
        """Log a usage event to mod's meter. Called from job completion paths
        so per-user/per-module stats accumulate in one place."""
        return self._mod_api('POST', '/meter/record', {
            'fn': fn, 'duration': duration, 'user': user, 'status': status,
        })

    # ── Container orchestration (DooD) ────────────────────────────────────
    # When the host docker socket is bind-mounted, claude becomes a thin
    # orchestrator. These methods shell to the docker CLI for one-off ops
    # and route module-shape ones through m.up/m.down which handle compose
    # files, port allocation, and the registry side-effect.

    def container_list(self) -> list:
        """List all sibling containers visible through the host socket."""
        try:
            out = subprocess.check_output(
                ['docker', 'ps', '-a', '--format', '{{json .}}'],
                stderr=subprocess.STDOUT, timeout=10,
            ).decode().strip()
            return [json.loads(line) for line in out.splitlines() if line]
        except Exception as e:
            return [{'error': str(e)}]

    def container_up(self, mod_name: str) -> dict:
        """Start a module via mod core's pm.docker pipeline. Side effect:
        the module also lands in the local registry through the existing
        config-scanner loop (core/api/mod.py:_scan_and_sync_configs)."""
        try:
            return {'result': m.up(mod_name)}
        except Exception as e:
            return {'error': str(e)}

    def container_down(self, mod_name: str) -> dict:
        try:
            return {'result': m.down(mod_name)}
        except Exception as e:
            return {'error': str(e)}

    def __repr__(self):
        server = "connected" if self._server_available() else "offline"
        return f"<Claude api={self.api_url} server={server} owner={self._owner or 'none'}>"
