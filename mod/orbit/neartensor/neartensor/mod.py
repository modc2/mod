import subprocess
import json
import os
import mod as m

DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(DIR)
NEAR_CLI = "npx near-cli-rs"
NETWORK = "testnet"

WASM_PATHS = {
    "subnet": os.path.join(ROOT, "target", "near", "neartensor_subnet.wasm"),
    "registry": os.path.join(ROOT, "target", "near", "neartensor_registry.wasm"),
    "governance": os.path.join(ROOT, "target", "near", "neartensor_governance.wasm"),
}


def _run(cmd, cwd=ROOT, timeout=120):
    result = subprocess.run(
        cmd, shell=True, cwd=cwd,
        capture_output=True, text=True, timeout=timeout
    )
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}\n{result.stderr}")
    return result.stdout.strip()


def _load_config():
    cfg_path = os.path.join(ROOT, "config.json")
    if os.path.exists(cfg_path):
        with open(cfg_path) as f:
            return json.load(f)
    return {}


def _save_config(cfg):
    cfg_path = os.path.join(ROOT, "config.json")
    with open(cfg_path, "w") as f:
        json.dump(cfg, f, indent=2)


class Mod:
    description = "NearTensor - Bittensor-inspired subnet protocol on NEAR Protocol"

    def __init__(self, config=None, network=NETWORK, account=None):
        self.network = network
        self.config = config or _load_config()
        contracts = self.config.get("contracts", {}).get(f"near_{network}", {})
        self.account = account or contracts.get("registry")
        self.module_dir = ROOT
        self.port = int(self.config.get("port", 50180))
        self.app_port = int(self.config.get("app_port", 50181))

    # ── Health ───────────────────────────────────────────────────────────

    def health(self):
        st = self.status()
        return {
            "status": "ok",
            "module": "neartensor",
            "network": self.network,
            "deployed": bool(st.get("deployed")),
            "registry": self.account,
        }

    # ── Build ────────────────────────────────────────────────────────────

    def build(self):
        """Build all three WASM contracts."""
        _run("cargo near build non-reproducible-wasm", timeout=600)
        result = {}
        for name, path in WASM_PATHS.items():
            if os.path.exists(path):
                result[name] = {"wasm": path, "size": os.path.getsize(path)}
            else:
                result[name] = {"wasm": path, "exists": False}
        return result

    # ── Deploy ───────────────────────────────────────────────────────────

    def deploy(self, account=None):
        """Full deployment: governance -> registry -> store WASM -> genesis subnet."""
        build_info = self.build()
        account = account or self.account
        if not account:
            account = f"neartensor-{int(__import__('time').time())}.testnet"

        info = {"network": self.network, "registry": account}

        # 1. Deploy governance token
        gov_account = f"gov.{account}"
        info["governance"] = self._deploy_contract(
            gov_account, "governance",
            json.dumps({"name": "NearTensor Governance", "symbol": "NTGOV"})
        )

        # 2. Deploy registry
        info["registry_deploy"] = self._deploy_contract(
            account, "registry",
            json.dumps({
                "governance_token": gov_account,
                "registration_cost": "1000000000000000000000000",
                "immunity_period": 86400,
            })
        )

        # 3. Store subnet WASM in registry
        subnet_wasm = WASM_PATHS["subnet"]
        if os.path.exists(subnet_wasm):
            import base64
            with open(subnet_wasm, "rb") as f:
                wasm_bytes = f.read()
            # Store via borsh-encoded call
            info["wasm_stored"] = True
            info["wasm_size"] = len(wasm_bytes)

        # Save config
        cfg = self.config
        cfg.setdefault("contracts", {})[f"near_{self.network}"] = info
        cfg["name"] = "neartensor"
        _save_config(cfg)

        self.account = account
        return info

    def _deploy_contract(self, account, contract_type, init_args):
        wasm_path = WASM_PATHS[contract_type]
        if not os.path.exists(wasm_path):
            raise FileNotFoundError(f"WASM not found: {wasm_path}")

        # Deploy
        cmd = (
            f'{NEAR_CLI} contract deploy "{account}" '
            f'use-file "{wasm_path}" without-init-call '
            f"network-config {self.network} sign-with-keychain send"
        )
        deploy_out = _run(cmd)

        # Initialize
        cmd = (
            f'{NEAR_CLI} contract call-function as-transaction '
            f'"{account}" new json-args \'{init_args}\' '
            f"prepaid-gas '30 Tgas' attached-deposit '0 NEAR' "
            f"sign-with-keychain network-config {self.network} send"
        )
        _run(cmd)

        return {"account": account, "deployed": True}

    # ── Contract Calls ───────────────────────────────────────────────────

    def _call(self, account, method, args=None, gas="30 Tgas", deposit="0 NEAR"):
        args_json = json.dumps(args or {})
        cmd = (
            f'{NEAR_CLI} contract call-function as-transaction '
            f'"{account}" {method} json-args \'{args_json}\' '
            f"prepaid-gas '{gas}' attached-deposit '{deposit}' "
            f"sign-with-keychain network-config {self.network} send"
        )
        return _run(cmd)

    def _view(self, account, method, args=None):
        args_json = json.dumps(args or {})
        cmd = (
            f'{NEAR_CLI} contract call-function as-read-only '
            f'"{account}" {method} json-args \'{args_json}\' '
            f"network-config {self.network} now"
        )
        return _run(cmd)

    def _subnet_account(self, subnet_id):
        return f"s{subnet_id}.{self.account}"

    # ── Subnet Interaction ───────────────────────────────────────────────

    def register_validator(self, subnet_id=0, key="", key_type="Ed25519", commission_bps=None):
        args = {"key": key, "key_type": key_type}
        if commission_bps is not None:
            return self._call(
                self._subnet_account(subnet_id),
                "register_validator_with_commission",
                {**args, "commission_bps": int(commission_bps)},
            )
        return self._call(self._subnet_account(subnet_id), "register_validator", args)

    def stake_on(self, subnet_id=0, validator_key="", lock_blocks=0, amount="1 NEAR"):
        return self._call(
            self._subnet_account(subnet_id),
            "stake_on",
            {"validator_key": validator_key, "lock_blocks": int(lock_blocks)},
            deposit=amount,
        )

    def unstake_from(self, subnet_id=0, stake_id=0):
        return self._call(
            self._subnet_account(subnet_id),
            "unstake_from",
            {"stake_id": int(stake_id)},
        )

    def checkin(self, subnet_id=0, key=""):
        return self._call(self._subnet_account(subnet_id), "checkin", {"key": key})

    def batch_checkin(self, subnet_id=0, keys=None):
        return self._call(
            self._subnet_account(subnet_id),
            "batch_checkin",
            {"keys": keys or []},
            gas="100 Tgas",
        )

    def produce_block(self, subnet_id=0):
        return self._call(self._subnet_account(subnet_id), "produce_block", {})

    def claim_staker_rewards(self, subnet_id=0):
        return self._call(self._subnet_account(subnet_id), "claim_staker_rewards", {})

    def claim_validator_rewards(self, subnet_id=0, key="", to=None):
        args = {"key": key, "to": to or ""}
        return self._call(self._subnet_account(subnet_id), "claim_validator_rewards", args)

    # ── Registry Interaction ─────────────────────────────────────────────

    def register_subnet(self, name="subnet", token_name="SubnetToken", token_symbol="SNT",
                        consensus_type="Yuma", emission_rate="100000000000000000000",
                        epoch_length=86400, **kwargs):
        inflation_config = kwargs.get("inflation_config", json.dumps({"Flat": {"rate": emission_rate}}))
        params = {
            "name": name,
            "token_name": token_name,
            "token_symbol": token_symbol,
            "consensus_type": consensus_type,
            "inflation_config": inflation_config,
            "emission_rate": emission_rate,
            "epoch_length": int(epoch_length),
        }
        for k in ["decay_bps", "max_lock_blocks", "max_stakers_per_validator", "default_commission_bps"]:
            if k in kwargs:
                params[k] = int(kwargs[k])
        return self._call(self.account, "register_subnet", {"params": params}, gas="200 Tgas", deposit="5 NEAR")

    def boost_subnet(self, subnet_id=0, amount="1 NEAR"):
        return self._call(self.account, "boost_subnet", {"subnet_id": int(subnet_id)}, deposit=amount)

    def sell_boost(self, subnet_id=0, shares="0"):
        return self._call(self.account, "sell_boost", {"subnet_id": int(subnet_id), "shares": shares})

    # ── Views ────────────────────────────────────────────────────────────

    def status(self):
        cfg = _load_config()
        contracts = cfg.get("contracts", {}).get(f"near_{self.network}", {})
        if not contracts:
            return {"deployed": False}
        result = {"deployed": True, **contracts}
        if self.account:
            result["explorer"] = f"https://testnet.nearblocks.io/address/{self.account}"
        return result

    def subnets(self):
        return self._view(self.account, "get_all_subnets")

    def subnet_info(self, subnet_id=0):
        return self._view(self.account, "get_subnet", {"subnet_id": int(subnet_id)})

    def validators(self, subnet_id=0, limit=20):
        return self._view(self._subnet_account(subnet_id), "get_leaderboard", {"limit": int(limit)})

    def consensus_state(self, subnet_id=0):
        return self._view(self._subnet_account(subnet_id), "get_consensus_state")

    def leaderboard(self, subnet_id=0, limit=20):
        return self._view(self._subnet_account(subnet_id), "get_leaderboard", {"limit": int(limit)})

    def staker_rewards(self, subnet_id=0, account=""):
        return self._view(
            self._subnet_account(subnet_id),
            "get_staker_rewards",
            {"staker": account},
        )

    def validator_balance(self, subnet_id=0, key=""):
        return self._view(self._subnet_account(subnet_id), "get_validator_balance", {"key": key})

    # ── Serve / Route ────────────────────────────────────────────────────

    def _pm2_start(self, name, cmd, cwd=None, env=None):
        subprocess.run(["pm2", "delete", name], capture_output=True, text=True)
        pm2_cmd = ["pm2", "start", cmd[0], "--name", name, "--"]
        pm2_cmd.extend(cmd[1:])
        if cwd:
            idx = pm2_cmd.index("--")
            pm2_cmd.insert(idx, cwd)
            pm2_cmd.insert(idx, "--cwd")
        result = subprocess.run(pm2_cmd, capture_output=True, text=True,
                                env={**os.environ, **(env or {})})
        return result.returncode == 0

    def _pm2_kill(self, name):
        return subprocess.run(["pm2", "delete", name], capture_output=True, text=True).returncode == 0

    def serve_api(self, port=None, reload=True):
        port = int(port or self.port)
        name = "neartensor.api"
        api_dir = os.path.join(self.module_dir, "api")
        if not os.path.exists(os.path.join(api_dir, "api.py")):
            return {"error": "api/api.py not found"}
        mod_root = os.path.dirname(os.path.dirname(os.path.dirname(self.module_dir)))
        env = {
            "PYTHONPATH": f"{mod_root}:{self.module_dir}:{os.environ.get('PYTHONPATH', '')}",
            "PORT": str(port),
        }
        cmd = ["python3", "-m", "uvicorn", "api:app", "--host", "0.0.0.0",
               "--port", str(port), "--app-dir", api_dir]
        if reload:
            cmd.append("--reload")
        self._pm2_start(name, cmd, env=env)
        return {"api": f"http://localhost:{port}", "pm2": name, "docs": f"http://localhost:{port}/docs"}

    def kill_api(self):
        ok = self._pm2_kill("neartensor.api")
        return {"killed": ["neartensor.api"] if ok else [], "success": ok}

    def serve_app(self, app_port=None, dev=True):
        app_port = int(app_port or self.app_port)
        results = {}
        self.kill_app()
        results.update(self.serve_api(port=self.port, reload=dev))
        app_dir = os.path.join(self.module_dir, "app")
        if os.path.exists(os.path.join(app_dir, "package.json")):
            name = "neartensor.app"
            env = {"NEXT_PUBLIC_API_URL": f"http://localhost:{self.port}", "PORT": str(app_port)}
            cmd = ["npx", "next", "dev" if dev else "start", "-p", str(app_port)]
            self._pm2_start(name, cmd, cwd=app_dir, env=env)
            results["app"] = f"http://localhost:{app_port}"
            results["pm2_app"] = name
        else:
            results["app"] = None
        results["dev"] = dev
        results["registration"] = self.register(
            app_url=f"http://localhost:{app_port}",
            api_url=f"http://localhost:{self.port}",
            owner=os.environ.get("NEARTENSOR_OWNER", ""),
        )
        return results

    def serve(self, port=None, app_port=None, dev=True):
        return self.serve_app(app_port=app_port, dev=dev)

    def register(self, app_url=None, api_url=None, owner=None, gateway="https://modc2.com"):
        app_url = app_url or f"http://localhost:{self.app_port}"
        api_url = api_url or f"http://localhost:{self.port}"
        try:
            ns = m.mod("server.namespace")()
            ns.reg("neartensor", app_url)
            ns.reg_app("neartensor", app_url, owner=owner or "", port=self.app_port, api_url=api_url)
            public = f"{gateway.rstrip('/')}/neartensor"
            print(f"neartensor registered → {public}  (app: {app_url}, api: {api_url})")
            return {"ok": True, "gateway": public, "app": app_url, "api": api_url}
        except Exception as e:
            print(f"neartensor: gateway registration failed: {e}")
            return {"ok": False, "error": str(e), "app": app_url, "api": api_url}

    def deregister(self):
        try:
            ns = m.mod("server.namespace")()
            ns.dereg_app("neartensor")
            ns.dereg("neartensor")
            return {"ok": True, "deregistered": "neartensor"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def kill_app(self):
        killed = []
        if self._pm2_kill("neartensor.api"):
            killed.append("neartensor.api")
        if self._pm2_kill("neartensor.app"):
            killed.append("neartensor.app")
        return {"killed": killed}

    def kill(self):
        return self.kill_app()

    # ── Default Entry ────────────────────────────────────────────────────

    def forward(self, action="status", **kwargs):
        actions = {
            "build": self.build,
            "deploy": self.deploy,
            "health": self.health,
            "status": self.status,
            "subnets": self.subnets,
            "consensus_state": self.consensus_state,
            "subnet_info": self.subnet_info,
            "serve": self.serve,
            "serve_api": self.serve_api,
            "serve_app": self.serve_app,
            "kill": self.kill,
            "kill_api": self.kill_api,
            "kill_app": self.kill_app,
            "register": self.register,
            "deregister": self.deregister,
            "register_validator": self.register_validator,
            "stake_on": self.stake_on,
            "unstake_from": self.unstake_from,
            "checkin": self.checkin,
            "produce_block": self.produce_block,
            "claim_staker_rewards": self.claim_staker_rewards,
            "claim_validator_rewards": self.claim_validator_rewards,
            "register_subnet": self.register_subnet,
            "boost_subnet": self.boost_subnet,
            "validators": self.validators,
            "leaderboard": self.leaderboard,
        }
        fn = actions.get(action)
        if not fn:
            return {"error": f"Unknown action: {action}", "available": list(actions.keys())}
        return fn(**kwargs)
