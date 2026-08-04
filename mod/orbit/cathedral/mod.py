"""
cathedral — rent confidential compute from https://cathedral.computer.

Cathedral runs your workload inside attested hardware (Intel TDX CPU, or a
confidential NVIDIA GPU) and hands back the result together with a signed
receipt binding the evidence, the workload, the result, the charge, and the
teardown. This mod is the mod-protocol front door to that service: catalog,
credits, workers, receipts.

WHO PAYS
    Every paid call is billed to the *caller's own* Cathedral account, always.
    This mod holds no house key and never fronts anybody's compute:

      * locally, the key comes from `CATHEDRAL_API_KEY` or from this machine's
        off-tree vault at ~/.mod/cathedral/keys.json (0600, never committed),
        keyed per account so two people sharing a box never share credits;
      * over HTTP (api/api.py), the caller sends their own `cat_sk_*` on every
        request and the server refuses to fall back to any ambient key.

    So a run costs the person who ran it, out of the prepaid USD credits sitting
    in their own Cathedral account. Top-ups go through Cathedral's own Stripe
    checkout, in their name — `topup` only hands you the payment URL.

SPEND GUARD
    Anything that can cost more than $0.20 needs `yes=True` (Cathedral's own
    agent contract draws the line in the same place). Every accepted job is
    written to a local ledger keyed by API-key fingerprint, so you can always
    answer "what did this key spend, and on what".

CLI:
    m cathedral                                  # account, credits, prices
    m cathedral/login cat_sk_...                 # store a key (this machine only)
    m cathedral/credits                          # your prepaid USD balance
    m cathedral/packs                            # credit packs
    m cathedral/topup pack_10                    # -> hosted Stripe URL (your card)
    m cathedral/profiles                         # live catalog + gates
    m cathedral/free_test                        # one free restricted minute
    m cathedral/run image=python:3.12-slim command='print(6*7)'
    m cathedral/rent image=nginx:1.27-alpine minutes=60 yes=1
    m cathedral/workers                          # your workers
    m cathedral/stop wrk_...                     # tear one down
    m cathedral/receipt rcpt_...                 # the signed evidence
    m cathedral/ledger                           # what this key has spent here
    m cathedral/serve                            # api on /api/cathedral, console on /cathedral
    m cathedral/app                              # where the console lives

CONSOLE
    app/ is a zero-dependency web console for the same API — catalog and gates,
    the spend guard quoted before you submit, workers, receipts, credits. It
    keeps the key in the browser tab and sends it to one place: /cathedral/_api,
    which this box forwards to the BYOK gateway. Nothing about the key is stored
    on the app server.
"""
import json
import os
import time
import uuid
from pathlib import Path
from typing import Optional

import requests

DIR = Path(__file__).resolve().parent
BASE = 'https://cathedral.computer/v1'
# The protocol's two halves: {host}/api/cathedral → PORT, {host}/cathedral → APP_PORT.
PORT = 50390
APP_PORT = 50391
STORE = Path(os.path.expanduser('~/.mod/cathedral'))
# Off-tree, 0600, never committed: {"<account>": {"key": "cat_sk_...", ...}}
KEYS_PATH = STORE / 'keys.json'
LEDGER_PATH = STORE / 'ledger.json'

# Spend above this needs an explicit yes= — same line Cathedral's own agent
# contract draws ("ask first if the run will cost more than $0.20").
CONFIRM_USD = 0.20

# Fallback price sheet (https://cathedral.computer/docs/#pricing) — the live
# catalog is authoritative and overrides these whenever it is reachable.
# One-shot profiles are per execution, not metered by elapsed time.
PRICES = {
    'free_test': {'usd': 0.0, 'unit': 'run', 'note': 'one restricted minute, once per verified account'},
    'attest.v1': {'usd': 0.20, 'unit': 'completed execution', 'note': 'Intel TDX CPU one-shot; failed CPU runs are refunded'},
    'cc_gpu': {'usd': 3.00, 'unit': 'verified execution', 'note': 'confidential GPU, up to 10 workload minutes; nonzero exits and timeouts are still billable'},
}
# Persistent ("keep alive") workers are the only hourly-metered shape.
HOURLY = {
    '4x16': 0.40, '8x32': 0.80, '22x88': 2.20, '44x176': 4.40,
}

CC_GPU_PROFILE = 'gcp-g4-rtx-pro-6000-sev-v1'
# The catalog says "live_testing" for shipping profiles and "available" in the
# docs' prose; either counts as live. The evidence gates below it do the real
# gatekeeping, and they fail closed.
LIVE_AVAILABILITY = ('available', 'live_testing')


class Mod:
    description = ('Rent confidential compute from cathedral.computer (attested TDX CPU / '
                   'confidential GPU) with signed receipts — every run billed to the '
                   "caller's own prepaid Cathedral credits, never a shared house key")

    fns = [
        'forward', 'info', 'prices', 'estimate',
        'login', 'logout', 'accounts', 'whoami',
        'credits', 'packs', 'topup', 'verify_payment',
        'profiles', 'gpu_ready',
        'free_test', 'run', 'rent', 'gpu',
        'workers', 'worker', 'wait', 'stop',
        'receipt', 'ledger',
        'app', 'serve', 'serve_api', 'serve_app', 'kill',
    ]

    def __init__(self, key: str = None, account: str = 'default', base: str = None, **kw):
        self.base = (base or os.environ.get('CATHEDRAL_BASE') or BASE).rstrip('/')
        self.account = account or 'default'
        self._key = key
        STORE.mkdir(parents=True, exist_ok=True)

    # ── the key: whose credits get spent ────────────────────────────────

    def _vault(self) -> dict:
        try:
            return json.loads(KEYS_PATH.read_text())
        except Exception:
            return {}

    def _save_vault(self, v: dict):
        KEYS_PATH.write_text(json.dumps(v, indent=2))
        os.chmod(KEYS_PATH, 0o600)

    def key(self) -> str:
        """The API key this call bills against.

        Explicit key > the named account's stored key > $CATHEDRAL_API_KEY. The
        env var only answers for the *default* account: naming an account and
        silently falling through to ambient credentials would charge the wrong
        person, which is the one thing this mod must never do.
        """
        if self._key:
            return self._key
        entry = self._vault().get(self.account) or {}
        if entry.get('key'):
            return entry['key']
        if self.account == 'default':
            env = os.environ.get('CATHEDRAL_API_KEY')
            if env:
                return env
        raise ValueError(
            f'no Cathedral API key for account {self.account!r} — '
            'run `m cathedral/login cat_sk_...` or set CATHEDRAL_API_KEY '
            '(get a key at https://cathedral.computer/account/)'
        )

    @staticmethod
    def fingerprint(key: str) -> str:
        """Stable, non-reversible handle for a key — safe to log, print, ledger."""
        import hashlib
        return 'cat:' + hashlib.sha256(key.encode()).hexdigest()[:12]

    def login(self, key: str, account: str = None):
        """Store a cat_sk_* key on this machine (0600, off-tree) and verify it."""
        key = (key or '').strip()
        if not key.startswith('cat_sk_'):
            return {'error': 'expected a cat_sk_* key from https://cathedral.computer/account/'}
        account = account or self.account
        probe = self._call('GET', '/credits', key=key)
        if 'error' in probe:
            return {'error': 'key rejected by Cathedral', 'detail': probe}
        vault = self._vault()
        vault[account] = {
            'key': key,
            'fingerprint': self.fingerprint(key),
            'added': int(time.time()),
        }
        self._save_vault(vault)
        return {'account': account, 'fingerprint': vault[account]['fingerprint'],
                'stored': str(KEYS_PATH), 'credits': probe}

    def logout(self, account: str = None):
        """Forget a stored key. Does not revoke it — do that in the dashboard."""
        account = account or self.account
        vault = self._vault()
        if account not in vault:
            return {'error': f'no stored key for {account!r}'}
        vault.pop(account)
        self._save_vault(vault)
        return {'account': account, 'removed': True,
                'revoke_at': 'https://cathedral.computer/account/'}

    def accounts(self):
        """Accounts with a key on this machine. Fingerprints only, never secrets."""
        vault = self._vault()
        out = [{'account': a, 'fingerprint': e.get('fingerprint'), 'added': e.get('added')}
               for a, e in vault.items()]
        if os.environ.get('CATHEDRAL_API_KEY') and 'default' not in vault:
            out.append({'account': 'default', 'fingerprint': self.fingerprint(os.environ['CATHEDRAL_API_KEY']),
                        'source': 'CATHEDRAL_API_KEY'})
        return {'accounts': out, 'store': str(KEYS_PATH)}

    def whoami(self):
        """Which key pays, and what it has left."""
        try:
            key = self.key()
        except ValueError as e:
            return {'error': str(e)}
        return {'account': self.account, 'fingerprint': self.fingerprint(key),
                'credits': self._call('GET', '/credits', key=key)}

    # ── HTTP ────────────────────────────────────────────────────────────

    def _pays(self) -> Optional[str]:
        """Payer fingerprint, or None when no key is configured yet."""
        try:
            return self.fingerprint(self.key())
        except ValueError:
            return None

    def _call(self, method: str, path: str, body: dict = None, key: str = None,
              idempotency_key: str = None, timeout: int = 60):
        if not key:
            try:
                key = self.key()
            except ValueError as e:
                # No key is the ordinary first-run state, not a crash.
                return {'error': 'no api key', 'detail': str(e),
                        'get_a_key': 'https://cathedral.computer/account/'}
        headers = {'Authorization': f'Bearer {key}', 'Accept': 'application/json'}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        if idempotency_key:
            headers['Idempotency-Key'] = idempotency_key
        url = self.base + path
        try:
            r = requests.request(method, url, headers=headers,
                                 data=json.dumps(body) if body is not None else None,
                                 timeout=timeout)
        except requests.RequestException as e:
            return {'error': 'unreachable', 'url': url, 'detail': str(e)}
        try:
            payload = r.json()
        except ValueError:
            payload = {'raw': r.text[:2000]}
        if r.status_code >= 400:
            return {'error': ERRORS.get(r.status_code, 'request failed'),
                    'status': r.status_code, 'detail': payload}
        if isinstance(payload, dict):
            payload.setdefault('status', r.status_code)
        return payload

    # ── credits — always the caller's own ───────────────────────────────

    def credits(self):
        """Prepaid USD credit balance for the paying key."""
        return self._call('GET', '/credits')

    def packs(self):
        """Buyable credit packs (public catalog — no key needed)."""
        try:
            r = requests.get(self.base + '/credits/packs', timeout=30)
            return r.json()
        except Exception as e:
            return {'error': 'unreachable', 'detail': str(e)}

    def topup(self, pack_id: str = 'pack_10'):
        """Open a Stripe checkout for MORE OF YOUR OWN CREDITS.

        Returns a hosted payment URL — the card, the charge, and the resulting
        credits all belong to whoever owns this key. This mod never pays for
        anyone else's compute. After paying, call `verify_payment(session_id)`.
        """
        out = self._call('POST', '/credits/checkout', {'pack_id': pack_id})
        if 'error' not in out:
            out['pays'] = self._pays()
            out['next'] = 'm cathedral/verify_payment <session_id> after paying'
        return out

    def verify_payment(self, session_id: str):
        """Settle the redirect race after checkout. Idempotent."""
        return self._call('POST', '/credits/verify', {'session_id': session_id})

    def _afford(self, usd: float, yes: bool):
        """Refuse to spend more than CONFIRM_USD without an explicit yes."""
        if usd > CONFIRM_USD and not yes:
            return {'error': 'confirmation required',
                    'price_usd': usd,
                    'threshold_usd': CONFIRM_USD,
                    'pays': self._pays(),
                    'how': 'pass yes=1 to authorize this spend against your own credits'}
        return None

    # ── catalog ─────────────────────────────────────────────────────────

    def profiles(self):
        """Live profile catalog. Public — no key, no charge.

        Its fields are runtime gates, not documentation: read them before every
        submission rather than trusting anything cached here.
        """
        try:
            r = requests.get(self.base + '/profiles', timeout=30)
            return r.json()
        except Exception as e:
            return {'error': 'unreachable', 'detail': str(e)}

    def gpu_ready(self):
        """Is the confidential-GPU profile safe to submit to right now?

        Cathedral's contract: submit no G4 job unless the profile is available,
        customer-enabled, reports PASS for both the overall evidence gate and
        verifier_log_digest_evidence_status, carries a canonical
        live_evidence_digest, and exposes create/cancel/retry/receipt.
        """
        cat = self.profiles()
        if 'error' in cat:
            return cat
        entry = _cc_gpu_entry(cat)
        if not entry:
            return {'ready': False, 'reason': f'{CC_GPU_PROFILE} not in catalog'}
        ops = entry.get('operations') or {}
        gates = {
            'availability': entry.get('availability'),
            'customer_enabled': entry.get('customer_enabled'),
            'cathedral_evidence_status': entry.get('cathedral_evidence_status'),
            'verifier_log_digest_evidence_status': entry.get('verifier_log_digest_evidence_status'),
            'live_evidence_digest': entry.get('live_evidence_digest'),
            'operations': {k: ops.get(k) for k in ('create', 'cancel', 'retry', 'receipt')},
        }
        ready = (gates['availability'] in LIVE_AVAILABILITY
                 and gates['customer_enabled'] is True
                 and gates['cathedral_evidence_status'] == 'PASS'
                 and gates['verifier_log_digest_evidence_status'] == 'PASS'
                 and bool(gates['live_evidence_digest'])
                 and all(gates['operations'].values()))
        return {'ready': ready, 'profile_id': entry.get('profile_id', CC_GPU_PROFILE),
                'gates': gates, 'runtime_image': (entry.get('runtime') or {}).get('image')}

    # ── running work ────────────────────────────────────────────────────

    def free_test(self):
        """The one free restricted minute every verified account gets. 409 if used."""
        out = self._call('POST', '/workers/free-test')
        if 'error' not in out:
            self._record(out, profile='free_test', reserved=0.0)
        return out

    def run(self, image: str = 'python:3.12-slim', command=None, minutes: int = 10,
            max_spend_usd: float = 0.20, egress: str = 'none', yes: bool = False,
            idempotency_key: str = None, wait: bool = False):
        """One verified TDX CPU job (attest.v1): run it, get the result, tear down.

        $0.20 per completed execution; a failed CPU run is refunded. Charged to
        your key's own credits.
        """
        price = self.estimate('attest.v1').get('usd', PRICES['attest.v1']['usd'])
        guard = self._afford(min(float(max_spend_usd), price), yes)
        if guard:
            return guard
        body = {
            'profile': 'attest.v1',
            'lifetime': {'mode': 'one_shot', 'reuse': 'forbidden', 'max_runtime_minutes': int(minutes)},
            'workload': {'image': image, 'command': _argv(command)},
            'network': {'egress': egress},
            'budget': {'max_spend_usd': float(max_spend_usd), 'auto_stop': True},
        }
        idem = idempotency_key or _idem('run')
        out = self._call('POST', '/workers', body, idempotency_key=idem)
        if 'error' not in out:
            self._record(out, profile='attest.v1', reserved=price, idem=idem)
            if wait:
                return self.wait(_worker_id(out))
        return out

    def rent(self, image: str = None, minutes: int = 60, ssh_public_key: str = None,
             name: str = 'sealed-worker', cpu: int = None, memory_gib: int = None,
             max_spend_usd: float = 1.0, yes: bool = False, gpu: str = None,
             accepted_max_hourly_usd: float = None, idempotency_key: str = None):
        """Keep a sealed TDX worker alive (custom.v1) — the hourly-metered shape.

        Billing opens only once the requested workload is ready and stops after
        provider cleanup; the rate is pinned at creation. Pass `gpu='H100'` for
        the provider-trusted hybrid preview (inputs are plaintext to the GPU
        host — not confidential GPU), which also needs the key to carry
        deploy:agent, compute:rent and workers:hybrid-gpu:rent.
        """
        guard = self._afford(float(max_spend_usd), yes)
        if guard:
            return guard
        body = {
            'profile': 'custom.v1',
            'name': name,
            'lifetime': {'mode': 'bounded_service', 'reuse': 'allowed', 'max_runtime_minutes': int(minutes)},
            'budget': {'max_spend_usd': float(max_spend_usd), 'auto_stop': True},
        }
        if image:
            body['workload'] = {'image': image}
        if ssh_public_key:
            body['ssh_public_key'] = ssh_public_key
        resources = {}
        if cpu or memory_gib:
            resources.update({'hardware_class': 'tdx_cpu'})
            if cpu:
                resources['cpu'] = int(cpu)
            if memory_gib:
                resources['memory_gib'] = int(memory_gib)
        if gpu:
            resources.setdefault('hardware_class', 'tdx_cpu')
            resources['gpu'] = {'mode': 'hybrid', 'type': gpu, 'provider': 'cloud',
                                'accepted_max_hourly_usd': float(accepted_max_hourly_usd or max_spend_usd)}
        if resources:
            body['resources'] = resources
        idem = idempotency_key or _idem('rent')
        out = self._call('POST', '/workers', body, idempotency_key=idem)
        if 'error' not in out:
            self._record(out, profile='custom.v1', reserved=float(max_spend_usd), idem=idem)
        return out

    def gpu(self, command=None, image: str = None, minutes: int = 10,
            max_spend_usd: float = 3.0, yes: bool = False, idempotency_key: str = None,
            checkpoint: str = 'checkpoint.json', max_attempts: int = 2):
        """One confidential-GPU execution (cc_gpu) — $3.00, gated on the catalog.

        Checks the live gates first and refuses to submit unless they all PASS.
        The image must be the catalog's immutable runtime digest, so it is read
        from the catalog rather than guessed.
        """
        gate = self.gpu_ready()
        if 'error' in gate:
            return gate
        if not gate.get('ready'):
            return {'error': 'confidential GPU not submittable right now', 'gates': gate.get('gates'),
                    'why': 'Cathedral requires available + customer_enabled + both evidence gates PASS'}
        price = self.estimate('cc_gpu').get('usd', PRICES['cc_gpu']['usd'])
        guard = self._afford(max(float(max_spend_usd), price), yes)
        if guard:
            return guard
        image = image or gate.get('runtime_image') or os.environ.get('CATHEDRAL_CC_GPU_RUNTIME_IMAGE')
        if not image:
            return {'error': 'no pinned runtime image in the catalog', 'gates': gate.get('gates')}
        body = {
            'profile': CC_GPU_PROFILE,
            'execution_class': 'cc_gpu',
            'lifetime': {'mode': 'one_shot', 'max_runtime_minutes': int(minutes), 'reuse': 'forbidden'},
            'resources': {
                'hardware_class': 'confidential_gpu', 'provider': 'gcp',
                'machine_type': 'g4-standard-48', 'cpu_tee': 'amd_sev',
                'provisioning_model': 'spot',
                'gpu': {'mode': 'confidential', 'provider': 'gcp',
                        'type': 'nvidia_rtx_pro_6000_96gb', 'count': 1},
            },
            'workload': {'image': image, 'command': _argv(command)},
            'network': {'egress': 'control_plane_only'},
            'budget': {'max_spend_usd': float(max_spend_usd), 'auto_stop': True},
            'retry': {'max_attempts': int(max_attempts),
                      'checkpoint': {'mode': 'on_preemption', 'artifact_path': checkpoint}},
            'protected_inputs': [],
        }
        idem = idempotency_key or _idem('gpu')
        out = self._call('POST', '/workers', body, idempotency_key=idem)
        if 'error' not in out:
            self._record(out, profile='cc_gpu', reserved=price, idem=idem)
        return out

    # ── worker lifecycle ────────────────────────────────────────────────

    def workers(self):
        """Your workers."""
        return self._call('GET', '/workers')

    def worker(self, worker_id: str):
        """One worker's state. A one-shot reaches `completed` only once fresh-
        environment deletion is confirmed."""
        return self._call('GET', f'/workers/{worker_id}')

    def wait(self, worker_id: str, timeout: int = 900, interval: int = 5):
        """Poll a worker to a terminal state, then keep the ledger honest."""
        deadline = time.time() + timeout
        last = {}
        while time.time() < deadline:
            last = self.worker(worker_id)
            if 'error' in last:
                return last
            state = (last.get('state') or last.get('status_name') or last.get('worker_status') or '')
            if str(state).lower() in ('completed', 'failed', 'cancelled', 'canceled', 'refunded', 'terminated'):
                self._settle(worker_id, str(state).lower(), last)
                return last
            time.sleep(interval)
        return {'error': 'timeout waiting for worker', 'worker_id': worker_id, 'last': last}

    def stop(self, worker_id: str):
        """Tear a worker down. Hourly billing stops after provider cleanup."""
        out = self._call('DELETE', f'/workers/{worker_id}')
        if 'error' not in out:
            self._settle(worker_id, 'stopped', out)
        return out

    def receipt(self, worker_id_or_receipt_id: str, save: str = None):
        """The signed receipt (cathedral_customer_receipt_v1).

        HTTP 425 means execution or cleanup is still pending — try again.
        Verify offline with the published trusted-keys file:
            curl https://cathedral.computer/customer-receipt-trusted-keys.json -o keys.json
            cathedral customer-receipt verify --receipt r.json --trusted-keys keys.json
        """
        out = self._call('GET', f'/receipts/{worker_id_or_receipt_id}')
        if out.get('status') == 425:
            out['hint'] = 'receipt not publishable yet — execution or teardown still pending'
        if save and 'error' not in out:
            p = Path(os.path.expanduser(save))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(out, indent=2))
            out['saved'] = str(p)
        return out

    # ── local spend ledger (fingerprints only, never keys) ───────────────

    def _record(self, response: dict, profile: str, reserved: float, idem: str = None):
        wid = _worker_id(response)
        entry = {
            'ts': int(time.time()),
            'account': self.account,
            'pays': self._pays(),
            'worker_id': wid,
            'profile': profile,
            'reserved_usd': reserved,
            'idempotency_key': idem,
            'state': 'accepted',
        }
        rows = _read_ledger()
        rows.append(entry)
        _write_ledger(rows)
        return entry

    def _settle(self, worker_id: str, state: str, response: dict):
        rows = _read_ledger()
        for row in rows:
            if row.get('worker_id') == worker_id:
                row['state'] = state
                row['settled_ts'] = int(time.time())
                for field in ('charge_usd', 'charged_usd', 'amount_usd'):
                    if isinstance(response, dict) and response.get(field) is not None:
                        row['charged_usd'] = response[field]
                        break
        _write_ledger(rows)

    def ledger(self, account: str = None, limit: int = 50):
        """Everything this machine has spent, and on whose key."""
        rows = _read_ledger()
        if account:
            rows = [r for r in rows if r.get('account') == account]
        reserved = sum(float(r.get('reserved_usd') or 0) for r in rows)
        charged = sum(float(r.get('charged_usd') or 0) for r in rows if r.get('charged_usd') is not None)
        return {'runs': len(rows), 'reserved_usd': round(reserved, 4),
                'charged_usd': round(charged, 4), 'entries': rows[-int(limit):],
                'note': 'local record of jobs launched from this machine; Cathedral is the source of truth'}

    # ── prices ──────────────────────────────────────────────────────────

    def prices(self, live: bool = True):
        """The price sheet — read from the live catalog, falling back to the
        published table if Cathedral is unreachable."""
        # Deep copy: live prices are written into this sheet, and the module-level
        # fallback table must survive as the offline answer.
        out = {'per_execution': {k: dict(v) for k, v in PRICES.items()},
               'hourly_keep_alive_usd': dict(HOURLY),
               'confirm_threshold_usd': CONFIRM_USD, 'source': 'built-in table',
               'note': ('one-shot profiles are billed per execution, not by elapsed time; '
                        'storage and egress are not included unless quoted')}
        if not live:
            return out
        cat = self.profiles()
        if 'error' in cat:
            out['live'] = cat
            return out
        for prof in cat.get('profiles') or []:
            pricing = prof.get('pricing') or {}
            if prof.get('id') == 'attest.v1' and pricing.get('amount_usd') is not None:
                out['per_execution']['attest.v1']['usd'] = pricing['amount_usd']
            if prof.get('id') == 'custom.v1':
                shapes = _shapes(prof)
                if shapes:
                    out['hourly_keep_alive_usd'] = shapes
        gpu = _cc_gpu_entry(cat)
        if gpu and (gpu.get('pricing') or {}).get('amount_usd') is not None:
            out['per_execution']['cc_gpu']['usd'] = gpu['pricing']['amount_usd']
        out['source'] = self.base + '/profiles'
        return out

    def estimate(self, profile: str = 'attest.v1', minutes: int = 10, shape: str = '4x16',
                 live: bool = True):
        """What a job would cost, before you run it. Quote this out loud."""
        sheet = self.prices(live=live)
        per_execution, hourly = sheet['per_execution'], sheet['hourly_keep_alive_usd']
        if profile in per_execution:
            p = per_execution[profile]
            return {'profile': profile, 'usd': p['usd'], 'unit': p['unit'],
                    'note': p['note'], 'source': sheet['source']}
        if profile in ('custom.v1', 'keep_alive'):
            rate = hourly.get(shape)
            if rate is None:
                return {'error': f'unknown shape {shape!r}', 'shapes': sorted(hourly)}
            return {'profile': 'custom.v1', 'shape': shape, 'hourly_usd': rate,
                    'minutes': minutes, 'usd': round(rate * minutes / 60.0, 4),
                    'source': sheet['source'],
                    'note': 'hourly metering starts when the workload is ready, stops after cleanup'}
        return {'error': f'unknown profile {profile!r}', 'profiles': ['attest.v1', 'custom.v1', 'cc_gpu']}

    # ── mod protocol ────────────────────────────────────────────────────

    def info(self):
        """Default view: who pays, what's in the account, what it costs."""
        out = {
            'name': 'cathedral',
            'description': self.description,
            'base': self.base,
            'billing': ("caller's own prepaid Cathedral credits — this mod holds no house key"),
            'app': f'http://localhost:{APP_PORT}/cathedral',
            'api': f'http://localhost:{PORT}',
            'get_a_key': 'https://cathedral.computer/account/',
            'docs': 'https://cathedral.computer/docs/',
            'prices': self.prices(),
            'accounts': self.accounts()['accounts'],
        }
        try:
            self.key()
        except ValueError as e:
            out['key'] = str(e)
            return out
        out['whoami'] = self.whoami()
        return out

    def forward(self, fn: str = 'info', *args, **kw):
        if fn not in self.fns:
            return {'error': f'unknown fn {fn!r}', 'fns': self.fns}
        return getattr(self, fn)(*args, **kw)

    def app(self, gateway: str = None):
        """Where the console lives, and what it will and won't do with a key."""
        base = f'{gateway.rstrip("/")}/cathedral' if gateway else f'http://localhost:{APP_PORT}/cathedral'
        return {
            'app': base,
            'api': f'{gateway.rstrip("/")}/api/cathedral' if gateway else f'http://localhost:{PORT}',
            'same_origin_api': base + '/_api',
            'path': str(DIR / 'app'),
            'byok': ('the console holds the key in the browser tab (sessionStorage) and sends it '
                     'as an Authorization header; no key is stored, logged, or proxied anywhere else'),
        }

    # pm2 is how the fleet actually runs these two processes, so `serve` starts
    # what pm2 will keep running rather than a shell-lived child. Without pm2 on
    # PATH it falls back to a plain subprocess, which dies with the shell.
    def _start(self, name: str, cmd: list, cwd: str = None, env: dict = None):
        import shutil
        import subprocess
        environ = {**os.environ, **(env or {})}
        if shutil.which('pm2'):
            subprocess.run(['pm2', 'delete', name], capture_output=True, text=True)
            pm2 = ['pm2', 'start', cmd[0], '--name', name]
            if cwd:
                pm2 += ['--cwd', cwd]
            pm2 += ['--'] + cmd[1:]
            r = subprocess.run(pm2, capture_output=True, text=True, env=environ)
            return {'pm2': name, 'ok': r.returncode == 0,
                    **({'error': r.stderr[-600:]} if r.returncode else {})}
        proc = subprocess.Popen(cmd, cwd=cwd, env=environ)
        return {'pid': proc.pid, 'ok': True, 'note': 'pm2 not found — child dies with this shell'}

    def serve_api(self, port: int = PORT, host: str = '0.0.0.0'):
        """Run the BYOK HTTP gateway (api/api.py)."""
        out = self._start('cathedral.api',
                          ['python3', '-m', 'uvicorn', 'api:app', '--host', host,
                           '--port', str(port), '--app-dir', str(DIR / 'api')])
        return {**out, 'api': f'http://localhost:{port}',
                'byok': 'callers send their own cat_sk_* per request'}

    def serve_app(self, port: int = APP_PORT, host: str = '0.0.0.0', api_port: int = PORT):
        """Run the console (app/serve.py) — static bundle plus the /_api alias."""
        out = self._start('cathedral.app',
                          ['python3', str(DIR / 'app' / 'serve.py'), '--port', str(port), '--host', host],
                          cwd=str(DIR / 'app'),
                          env={'CATHEDRAL_UPSTREAM': f'http://127.0.0.1:{api_port}'})
        return {**out, 'app': f'http://localhost:{port}/cathedral'}

    def serve(self, port: int = PORT, app_port: int = APP_PORT, host: str = '0.0.0.0'):
        """Start both halves of the route: the API on /api/cathedral, the app on /cathedral."""
        return {'api': self.serve_api(port=port, host=host),
                'app': self.serve_app(port=app_port, host=host, api_port=port)}

    def kill(self, port: int = PORT, app_port: int = APP_PORT):
        import shutil
        import subprocess
        if shutil.which('pm2'):
            for name in ('cathedral.api', 'cathedral.app'):
                subprocess.run(['pm2', 'delete', name], capture_output=True, text=True)
        else:
            subprocess.run(['pkill', '-f', f'uvicorn api:app --host 0.0.0.0 --port {port}'])
            subprocess.run(['pkill', '-f', f'app/serve.py --port {app_port}'])
        return {'killed': ['cathedral.api', 'cathedral.app'], 'ports': [port, app_port]}


ERRORS = {
    400: 'malformed request or invalid verification code',
    401: 'missing, revoked, or invalid API key',
    402: 'insufficient prepaid credits — top up your own account first',
    404: 'not found',
    409: 'capability unavailable, or conflicts with worker state (free test already used?)',
    422: 'valid JSON that violates the selected profile contract',
    425: 'receipt is not publishable yet',
    503: 'capacity, email, billing, or verification service temporarily unavailable',
}


def _cc_gpu_entry(catalog: dict) -> Optional[dict]:
    """Find the confidential-GPU entry in the live catalog.

    It is not a top-level profile: it hangs off each profile's
    `hardware_classes` as the entry with execution_class == 'cc_gpu'.
    """
    for prof in catalog.get('profiles') or []:
        if not isinstance(prof, dict):
            continue
        for hw in prof.get('hardware_classes') or []:
            if isinstance(hw, dict) and hw.get('execution_class') == 'cc_gpu':
                return hw
        if prof.get('profile_id') == CC_GPU_PROFILE or prof.get('id') == CC_GPU_PROFILE:
            return prof
    return None


def _shapes(profile: dict) -> dict:
    """custom.v1 sizes as {'<cpu>x<memory_gib>': usd_per_hour}."""
    out = {}
    for res in profile.get('resources') or []:
        if isinstance(res, dict) and res.get('price_usd_per_hour') is not None:
            out[f"{res.get('cpu')}x{res.get('memory_gib')}"] = res['price_usd_per_hour']
    return out


def _argv(command) -> list:
    """Accept a list, a shell-ish string, or a bare python snippet."""
    if command is None:
        return ['python', '-c', 'print(6 * 7)']
    if isinstance(command, (list, tuple)):
        return [str(c) for c in command]
    text = str(command)
    if text.strip().startswith(('[', '{')):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(c) for c in parsed]
        except ValueError:
            pass
    # A CLI convenience: `command='print(6*7)'` means "run this in python".
    if '(' in text and not text.split()[0].endswith(('sh', 'bash', 'python', 'python3')):
        return ['python', '-c', text]
    import shlex
    return shlex.split(text)


def _idem(prefix: str) -> str:
    return f'{prefix}-{uuid.uuid4().hex[:16]}'


def _worker_id(response: dict):
    if not isinstance(response, dict):
        return None
    for field in ('worker_id', 'id', 'workerId'):
        if response.get(field):
            return response[field]
    worker = response.get('worker')
    if isinstance(worker, dict):
        return worker.get('worker_id') or worker.get('id')
    return None


def _read_ledger() -> list:
    try:
        return json.loads(LEDGER_PATH.read_text())
    except Exception:
        return []


def _write_ledger(rows: list):
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    LEDGER_PATH.write_text(json.dumps(rows, indent=2))
    os.chmod(LEDGER_PATH, 0o600)
