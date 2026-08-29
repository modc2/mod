"""
Compute — rent and manage compute instances with on-chain billing.

Hosts list compute offerings with hourly rates in Market tokens.
Clients rent by depositing tokens; the host bills per-hour via
chain.debit() through the Market contract.

Host (provider):
    compute = m.mod('compute')()
    compute.register_offer('gpu-a100', rate=2.0, cpus=8, memory='32g', gpu=True)
    compute.bill_all()          # charge every active renter for elapsed hours

Client (renter):
    compute = m.mod('compute')()
    compute.rent('worker-1', host='0xProvider...', offer='gpu-a100')
    compute.ssh('worker-1', 'nvidia-smi')
    compute.release('worker-1')
"""

import os
import json
import time
import threading
import logging

logger = logging.getLogger(__name__)


class Compute:
    """Provision and manage compute instances with on-chain hourly billing."""

    default_image = 'ubuntu:22.04'
    instances_dir = '~/.mod/compute-fork/instances'
    offers_dir = '~/.mod/compute-fork/offers'
    billing_interval = 3600  # bill every hour (seconds)

    def __init__(self, network='testnet', key=None, **kwargs):
        import mod
        self.m = mod.Mod()
        self.docker = self.m.mod('pm.docker')()
        self.chain = self.m.mod('chain')(network=network, key=key or 'test')
        self.instances_path = self.m.abspath(self.instances_dir)
        self.offers_path = self.m.abspath(self.offers_dir)
        os.makedirs(self.instances_path, exist_ok=True)
        os.makedirs(self.offers_path, exist_ok=True)
        self._billing_thread = None

    # ── Offers (host-side) ───────────────────────────────────────────────

    def register_offer(self, name, rate, image=None, cpus=None,
                       memory=None, gpu=False, description=None):
        """Register a compute offering with an hourly rate in Market tokens.

        Args:
            name:        Offer identifier (e.g. 'gpu-a100', '4cpu-8g')
            rate:        Hourly rate in Market tokens
            image:       Docker image for the instance
            cpus:        Number of CPUs
            memory:      Memory limit (e.g. '8g')
            gpu:         Whether GPU is included
            description: Human-readable description
        """
        offer = {
            'name': name,
            'rate': rate,
            'image': image or self.default_image,
            'cpus': cpus,
            'memory': memory,
            'gpu': gpu,
            'description': description or f'{name} @ {rate} tokens/hr',
            'provider': self.chain.account.address,
            'created': time.time(),
        }
        self._save_offer(name, offer)
        return offer

    def offers(self, provider=None):
        """List available compute offers, optionally filtered by provider."""
        all_offers = self._load_all_offers()
        if provider:
            all_offers = {k: v for k, v in all_offers.items()
                          if v.get('provider', '').lower() == provider.lower()}
        return all_offers

    def remove_offer(self, name):
        """Remove a compute offering."""
        path = os.path.join(self.offers_path, f'{name}.json')
        if os.path.exists(path):
            os.remove(path)
            return {'name': name, 'removed': True}
        return {'name': name, 'removed': False, 'error': 'not found'}

    # ── Rent (client-side) ───────────────────────────────────────────────

    def rent(self, name, host, offer, ports=None, volumes=None,
             env=None, cmd=None, network=None, deposit=None, **kwargs):
        """Rent a compute instance from a host.

        Verifies the client has enough Market token balance, then starts
        the container and begins tracking billable hours.

        Args:
            name:    Instance name
            host:    Provider address (0x...)
            offer:   Offer name to rent
            deposit: Optional upfront deposit in Market tokens
                     (must be >= 1 hour of the offer rate)
            ports:   Port mappings
            volumes: Volume mounts
            env:     Environment variables
            cmd:     Override command
        """
        offer_data = self._load_offer(offer)
        if not offer_data:
            raise ValueError(f'Offer "{offer}" not found')

        rate = offer_data['rate']
        client = self.chain.account.address

        # check client balance covers at least 1 hour
        balance = self.chain.credits(client)
        min_required = deposit or rate
        if balance < min_required:
            raise ValueError(
                f'Insufficient Market balance: {balance} < {min_required} '
                f'(need at least 1 hour @ {rate}/hr). '
                f'Run chain.credit() to top up.'
            )

        # if an upfront deposit was specified, debit it now
        if deposit and deposit > 0:
            tx = self.chain.debit(
                client=client,
                provider=host,
                amount=deposit,
            )
            logger.info(f'Deposit debit tx: {tx}')

        # build resource constraints from the offer
        resources = {}
        if offer_data.get('cpus'):
            resources['cpus'] = str(offer_data['cpus'])
        if offer_data.get('memory'):
            resources['memory'] = offer_data['memory']
        if offer_data.get('gpu'):
            resources['gpus'] = 'all'

        image = offer_data.get('image', self.default_image)
        run_cmd = cmd or 'sleep infinity'

        self.docker.run(
            name=name,
            image=image,
            cmd=run_cmd,
            volumes=volumes or {},
            resources=resources,
            network=network,
            port=ports or {},
            **kwargs,
        )

        instance = {
            'name': name,
            'offer': offer,
            'image': image,
            'host': host,
            'client': client,
            'rate': rate,
            'cpus': offer_data.get('cpus'),
            'memory': offer_data.get('memory'),
            'gpu': offer_data.get('gpu', False),
            'ports': ports or {},
            'volumes': volumes or {},
            'env': env or {},
            'deposit': deposit or 0,
            'total_billed': deposit or 0,
            'started': time.time(),
            'last_billed': time.time(),
            'status': 'running',
        }
        self._save_instance(name, instance)
        return instance

    def release(self, name):
        """Release a rented instance — bills remaining time and destroys it."""
        inst = self._load_instance(name)
        if not inst:
            raise ValueError(f'Instance "{name}" not found')

        # bill for any remaining unbilled time
        self._bill_instance(name, inst)

        # tear down the container
        self.docker.kill(name)
        inst['status'] = 'released'
        inst['released'] = time.time()
        self._save_instance(name, inst)
        return {
            'name': name,
            'status': 'released',
            'total_billed': inst['total_billed'],
        }

    # ── Billing (host-side) ──────────────────────────────────────────────

    def bill(self, name):
        """Bill a single instance for elapsed hours since last bill."""
        inst = self._load_instance(name)
        if not inst:
            raise ValueError(f'Instance "{name}" not found')
        if inst.get('status') != 'running':
            return {'name': name, 'billed': 0, 'reason': 'not running'}
        return self._bill_instance(name, inst)

    def bill_all(self):
        """Bill every running instance for elapsed time."""
        results = {}
        for name, inst in self._load_all_instances().items():
            if inst and inst.get('status') == 'running':
                results[name] = self._bill_instance(name, inst)
        return results

    def start_billing_loop(self, interval=None):
        """Start a background thread that bills all instances periodically."""
        interval = interval or self.billing_interval
        if self._billing_thread and self._billing_thread.is_alive():
            return {'status': 'already running'}

        def _loop():
            while True:
                try:
                    self.bill_all()
                except Exception as e:
                    logger.error(f'Billing loop error: {e}')
                time.sleep(interval)

        self._billing_thread = threading.Thread(
            target=_loop, daemon=True, name='compute-billing'
        )
        self._billing_thread.start()
        return {'status': 'started', 'interval': interval}

    def _bill_instance(self, name, inst):
        """Calculate and execute a debit for elapsed hours."""
        now = time.time()
        elapsed = now - inst['last_billed']
        hours = elapsed / 3600.0
        amount = round(hours * inst['rate'], 8)

        if amount <= 0:
            return {'name': name, 'billed': 0, 'hours': hours}

        client = inst['client']
        host = inst['host']

        # check the client still has balance
        balance = self.chain.credits(client)
        if balance < amount:
            logger.warning(
                f'Client {client} balance {balance} < {amount} for {name}, '
                f'stopping instance'
            )
            self.docker.stop(name)
            inst['status'] = 'suspended'
            inst['suspended_reason'] = 'insufficient_balance'
            self._save_instance(name, inst)
            return {
                'name': name,
                'billed': 0,
                'error': 'insufficient_balance',
                'balance': balance,
                'required': amount,
            }

        tx = self.chain.debit(
            client=client,
            provider=host,
            amount=amount,
        )

        inst['last_billed'] = now
        inst['total_billed'] = inst.get('total_billed', 0) + amount
        self._save_instance(name, inst)

        return {
            'name': name,
            'billed': amount,
            'hours': round(hours, 4),
            'tx': tx,
            'total_billed': inst['total_billed'],
        }

    # ── Instance Lifecycle ───────────────────────────────────────────────

    def create(self, name, image=None, cpus=None, memory=None,
               gpu=False, ports=None, volumes=None, env=None,
               cmd=None, network=None, shm_size=None, rate=0, **kwargs):
        """Create an instance directly (host-local, no billing if rate=0)."""
        image = image or self.default_image
        ports = ports or {}
        volumes = volumes or {}
        env = env or {}

        resources = {}
        if cpus:
            resources['cpus'] = str(cpus)
        if memory:
            resources['memory'] = memory
        if gpu:
            resources['gpus'] = 'all'

        run_cmd = cmd or 'sleep infinity'

        self.docker.run(
            name=name,
            image=image,
            cmd=run_cmd,
            volumes=volumes,
            resources=resources,
            shm_size=shm_size,
            network=network,
            port=ports,
            **kwargs,
        )

        instance = {
            'name': name,
            'image': image,
            'cpus': cpus,
            'memory': memory,
            'gpu': gpu,
            'ports': ports,
            'volumes': volumes,
            'env': env,
            'rate': rate,
            'host': self.chain.account.address,
            'client': self.chain.account.address,
            'total_billed': 0,
            'created': time.time(),
            'started': time.time(),
            'last_billed': time.time(),
            'status': 'running',
        }
        self._save_instance(name, instance)
        return instance

    def destroy(self, name):
        """Bill remaining time, then stop and remove an instance."""
        inst = self._load_instance(name)
        if inst and inst.get('status') == 'running' and inst.get('rate', 0) > 0:
            self._bill_instance(name, inst)
        self.docker.kill(name)
        self._remove_instance(name)
        return {'name': name, 'status': 'destroyed'}

    def stop(self, name):
        """Stop an instance — bills up to this point."""
        inst = self._load_instance(name)
        if inst and inst.get('status') == 'running' and inst.get('rate', 0) > 0:
            self._bill_instance(name, inst)
        self.docker.stop(name)
        self._update_field(name, 'status', 'stopped')
        return {'name': name, 'status': 'stopped'}

    def start(self, name):
        """Resume a stopped instance — resets billing clock."""
        self.docker.start(name, image=None)
        inst = self._load_instance(name)
        if inst:
            inst['status'] = 'running'
            inst['last_billed'] = time.time()
            self._save_instance(name, inst)
        return {'name': name, 'status': 'running'}

    def restart(self, name):
        """Restart an instance."""
        self.docker.restart(name)
        self._update_field(name, 'status', 'running')
        return {'name': name, 'status': 'running'}

    # ── Execution ────────────────────────────────────────────────────────

    def ssh(self, name, cmd):
        """Execute a command inside a compute instance."""
        return self.docker.exec(name, cmd)

    def exec(self, name, cmd):
        return self.ssh(name, cmd)

    # ── Info & Monitoring ────────────────────────────────────────────────

    def ps(self):
        """List all instances with live status and billing info."""
        instances = self._load_all_instances()
        running = {c.get('Names', ''): c for c in (self.docker.ps() or [])}
        for name, inst in instances.items():
            if inst:
                inst['live'] = name in running
                elapsed = time.time() - inst.get('last_billed', time.time())
                inst['unbilled_hours'] = round(elapsed / 3600.0, 4)
                inst['unbilled_amount'] = round(
                    inst['unbilled_hours'] * inst.get('rate', 0), 8
                )
        return instances

    def cost(self, name):
        """Get billing summary for an instance."""
        inst = self._load_instance(name)
        if not inst:
            return None
        elapsed_total = time.time() - inst.get('started', time.time())
        elapsed_unbilled = time.time() - inst.get('last_billed', time.time())
        return {
            'name': name,
            'rate': inst.get('rate', 0),
            'total_hours': round(elapsed_total / 3600.0, 4),
            'total_billed': inst.get('total_billed', 0),
            'unbilled_hours': round(elapsed_unbilled / 3600.0, 4),
            'unbilled_amount': round(
                (elapsed_unbilled / 3600.0) * inst.get('rate', 0), 8
            ),
        }

    def logs(self, name, tail=100, follow=False):
        return self.docker.logs(name, tail=tail, follow=follow)

    def stats(self, name=None):
        all_stats = self.docker.stats()
        if name and isinstance(all_stats, list):
            return [s for s in all_stats if s.get('Name') == name]
        return all_stats

    def inspect(self, name):
        return self.m.cmd(f'docker inspect {name}')

    # ── Persistence (instances) ──────────────────────────────────────────

    def _instance_path(self, name):
        return os.path.join(self.instances_path, f'{name}.json')

    def _save_instance(self, name, data):
        path = self._instance_path(name)
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)

    def _load_instance(self, name):
        path = self._instance_path(name)
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return json.load(f)

    def _load_all_instances(self):
        instances = {}
        for f in os.listdir(self.instances_path):
            if f.endswith('.json'):
                instances[f[:-5]] = self._load_instance(f[:-5])
        return instances

    def _remove_instance(self, name):
        path = self._instance_path(name)
        if os.path.exists(path):
            os.remove(path)

    def _update_field(self, name, field, value):
        inst = self._load_instance(name)
        if inst:
            inst[field] = value
            self._save_instance(name, inst)

    # ── Persistence (offers) ─────────────────────────────────────────────

    def _save_offer(self, name, data):
        path = os.path.join(self.offers_path, f'{name}.json')
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)

    def _load_offer(self, name):
        path = os.path.join(self.offers_path, f'{name}.json')
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return json.load(f)

    def _load_all_offers(self):
        offers = {}
        for f in os.listdir(self.offers_path):
            if f.endswith('.json'):
                offers[f[:-5]] = self._load_offer(f[:-5])
        return offers

    # ── Convenience ──────────────────────────────────────────────────────

    def serve(self, port=None, dev=True):
        """Start the compute module's Next.js app server."""
        import subprocess
        app_dir = os.path.join(os.path.dirname(__file__), 'app')
        config_path = os.path.join(app_dir, 'mod.json')
        config = {}
        if os.path.exists(config_path):
            with open(config_path) as f:
                config = json.load(f)
        port = port or config.get('port', 3100)

        # Register as an app server with owner
        owner = config.get('owner', self.m.owner())
        ns = self.m.mod('server.namespace')()
        ns.reg_app('compute', f'http://0.0.0.0:{port}', owner=owner)

        cmd = ['npm', 'run', 'dev' if dev else 'start', '--', '-p', str(port)]
        subprocess.run(cmd, cwd=app_dir)

    def forward(self, name=None, **kwargs):
        """Default entry — list instances or rent one."""
        if name is None:
            return self.ps()
        return self.rent(name, **kwargs) if 'host' in kwargs else self.create(name, **kwargs)
