"""
Minimal NEAR JSON-RPC client. Keyless, dependency-light (requests only),
rotates across public endpoints so no single provider is load-bearing.
Used by both the miner (to answer) and the validator (to verify).
"""
import itertools
import requests


class NearRPCError(RuntimeError):
    pass


class NearClient:
    def __init__(self, rpcs, timeout=10.0):
        if isinstance(rpcs, str):
            rpcs = [rpcs]
        self.rpcs = list(rpcs)
        self.timeout = timeout
        self._rr = itertools.cycle(range(len(self.rpcs)))

    def call(self, method, params):
        last_err = None
        for _ in range(len(self.rpcs)):
            url = self.rpcs[next(self._rr)]
            try:
                r = requests.post(
                    url,
                    json={"jsonrpc": "2.0", "id": "nt", "method": method, "params": params},
                    timeout=self.timeout,
                )
                r.raise_for_status()
                body = r.json()
                if "error" in body:
                    raise NearRPCError(str(body["error"]))
                return body["result"]
            except NearRPCError:
                raise  # a real chain-level error, same on every endpoint
            except Exception as e:
                last_err = e
        raise NearRPCError(f"all NEAR RPCs failed: {last_err}")

    # ── Views used by the subnet tasks ──────────────────────────────────

    def final_block(self):
        return self.call("block", {"finality": "final"})

    def block_by_hash(self, block_hash):
        return self.call("block", {"block_id": block_hash})

    def gas_price(self, block_hash=None):
        return self.call("gas_price", [block_hash])

    def view_account(self, account_id, block_hash=None):
        params = {"request_type": "view_account", "account_id": account_id}
        if block_hash:
            params["block_id"] = block_hash
        else:
            params["finality"] = "final"
        return self.call("query", params)

    def final_height(self):
        return int(self.final_block()["header"]["height"])
