"""Minimal Ethereum JSON-RPC + ABI layer (eth_abi / eth_account, no web3).

Kept deliberately small: the module talks to exactly three contracts (the
upstream vault, the devnet price feed and Chainlink's mainnet feed) and needs
calls at explicit block tags, unlocked-account sends on anvil and locally
signed sends on live networks.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import eth_abi
from eth_utils import keccak, to_checksum_address

GWEI = 10**9


class RpcError(RuntimeError):
    def __init__(self, method: str, error: Any):
        self.method = method
        self.error = error
        message = error.get("message") if isinstance(error, dict) else str(error)
        data = error.get("data") if isinstance(error, dict) else None
        super().__init__(f"{method}: {message}" + (f" ({data})" if data else ""))


class Rpc:
    def __init__(self, url: str, timeout: float = 30.0):
        self.url = url
        self.timeout = timeout
        self._id = 0
        self._lock = threading.Lock()

    def call(self, method: str, params: list | None = None) -> Any:
        with self._lock:
            self._id += 1
            rid = self._id
        body = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or []}).encode()
        req = urllib.request.Request(self.url, data=body, headers={"content-type": "application/json",
                                                                   "user-agent": "zkapi-module/0.2"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                value = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise RpcError(method, f"HTTP {e.code}") from None
        except (urllib.error.URLError, OSError) as e:
            raise RpcError(method, f"unreachable: {e}") from None
        if value.get("error"):
            raise RpcError(method, value["error"])
        return value.get("result")

    # -- conveniences -------------------------------------------------------------
    def chain_id(self) -> int:
        return int(self.call("eth_chainId"), 16)

    def block_number(self) -> int:
        return int(self.call("eth_blockNumber"), 16)

    def block(self, tag: str | int = "latest") -> dict:
        ref = hex(tag) if isinstance(tag, int) else tag
        return self.call("eth_getBlockByNumber", [ref, False])

    def balance(self, address: str, tag: str = "latest") -> int:
        return int(self.call("eth_getBalance", [address, tag]), 16)

    def code(self, address: str) -> str:
        return self.call("eth_getCode", [address, "latest"]) or "0x"

    def eth_call(self, to: str, data: str, tag: str = "latest", sender: str | None = None) -> str:
        tx = {"to": to, "data": data}
        if sender:
            tx["from"] = sender
        return self.call("eth_call", [tx, tag])

    def accounts(self) -> list[str]:
        return self.call("eth_accounts") or []

    def receipt(self, tx_hash: str) -> dict | None:
        return self.call("eth_getTransactionReceipt", [tx_hash])

    def wait(self, tx_hash: str, timeout: float = 120.0) -> dict:
        end = time.time() + timeout
        while time.time() < end:
            rec = self.receipt(tx_hash)
            if rec:
                return rec
            time.sleep(0.25)
        raise TimeoutError(f"transaction {tx_hash} not mined within {timeout}s")


def selector(signature: str) -> bytes:
    return keccak(text=signature)[:4]


def _abi_type(param: dict) -> str:
    t = param["type"]
    if t.startswith("tuple"):
        inner = ",".join(_abi_type(c) for c in param["components"])
        return f"({inner}){t[5:]}"
    return t


def _normalize(value: Any, abi_type: str) -> Any:
    """Coerce JSON-friendly values (hex strings, decimal strings) to ABI values."""
    if abi_type.endswith("]"):
        base = abi_type[: abi_type.rindex("[")]
        return [_normalize(v, base) for v in value]
    if abi_type.startswith("("):
        parts = _split_tuple(abi_type[1:-1])
        return tuple(_normalize(v, t) for v, t in zip(value, parts))
    if abi_type.startswith(("uint", "int")):
        if isinstance(value, str):
            return int(value, 16) if value.startswith(("0x", "0X")) else int(value)
        return int(value)
    if abi_type == "address":
        return to_checksum_address(value)
    if abi_type == "bool":
        return bool(value) if not isinstance(value, str) else value.lower() in ("1", "true")
    if abi_type.startswith("bytes"):
        if isinstance(value, str):
            raw = bytes.fromhex(value[2:] if value.startswith("0x") else value)
            if abi_type != "bytes":
                size = int(abi_type[5:])
                raw = raw.rjust(size, b"\0") if len(raw) < size else raw
            return raw
        if isinstance(value, int):
            return value.to_bytes(int(abi_type[5:]), "big")
        return value
    return value


def _split_tuple(body: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in body:
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
            continue
        depth += ch == "("
        depth -= ch == ")"
        cur += ch
    if cur:
        out.append(cur)
    return out


def _jsonable(value: Any) -> Any:
    if isinstance(value, bytes):
        return "0x" + value.hex()
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


class Contract:
    def __init__(self, abi: list[dict], address: str | None = None):
        self.abi = abi
        self.address = to_checksum_address(address) if address else None
        self.functions = {f["name"]: f for f in abi if f.get("type") == "function"}
        self.events = {}
        for e in abi:
            if e.get("type") == "event":
                sig = f"{e['name']}({','.join(_abi_type(i) for i in e['inputs'])})"
                self.events["0x" + keccak(text=sig).hex()] = e
        self.errors = {}
        for e in abi:
            if e.get("type") == "error":
                sig = f"{e['name']}({','.join(_abi_type(i) for i in e['inputs'])})"
                self.errors["0x" + keccak(text=sig)[:4].hex()] = e["name"]

    @classmethod
    def from_artifact(cls, path: Path, address: str | None = None) -> "Contract":
        return cls(json.loads(Path(path).read_text())["abi"], address)

    def at(self, address: str) -> "Contract":
        return Contract(self.abi, address)

    def encode(self, name: str, *args: Any) -> str:
        fn = self.functions[name]
        types = [_abi_type(i) for i in fn["inputs"]]
        sig = f"{name}({','.join(types)})"
        values = [_normalize(a, t) for a, t in zip(args, types)]
        return "0x" + (selector(sig) + eth_abi.encode(types, values)).hex()

    def encode_constructor(self, bytecode: str, *args: Any) -> str:
        ctor = next((f for f in self.abi if f.get("type") == "constructor"), {"inputs": []})
        types = [_abi_type(i) for i in ctor["inputs"]]
        values = [_normalize(a, t) for a, t in zip(args, types)]
        return bytecode + eth_abi.encode(types, values).hex()

    def decode(self, name: str, data: str) -> Any:
        fn = self.functions[name]
        types = [_abi_type(o) for o in fn["outputs"]]
        raw = bytes.fromhex(data[2:]) if data and data != "0x" else b""
        out = eth_abi.decode(types, raw)
        out = [_jsonable(v) for v in out]
        return out[0] if len(out) == 1 else out

    def call(self, rpc: Rpc, name: str, *args: Any, tag: str = "latest") -> Any:
        return self.decode(name, rpc.eth_call(self.address, self.encode(name, *args), tag))

    def decode_logs(self, receipt: dict) -> list[dict]:
        out = []
        for log in receipt.get("logs", []):
            if not self.address or log["address"].lower() != self.address.lower():
                continue
            ev = self.events.get(log["topics"][0]) if log.get("topics") else None
            if not ev:
                continue
            indexed = [i for i in ev["inputs"] if i.get("indexed")]
            plain = [i for i in ev["inputs"] if not i.get("indexed")]
            args = {}
            for inp, topic in zip(indexed, log["topics"][1:]):
                args[inp["name"]] = _jsonable(eth_abi.decode([_abi_type(inp)], bytes.fromhex(topic[2:]))[0])
            values = eth_abi.decode([_abi_type(i) for i in plain], bytes.fromhex(log["data"][2:]))
            for inp, v in zip(plain, values):
                args[inp["name"]] = _jsonable(v)
            out.append({"event": ev["name"], "args": args, "block": int(log["blockNumber"], 16),
                        "tx": log["transactionHash"]})
        return out

    def revert_reason(self, error: Exception) -> str:
        """Best-effort decode of a custom-error revert from an RpcError."""
        data = getattr(error, "error", None)
        raw = data.get("data") if isinstance(data, dict) else None
        if isinstance(raw, dict):
            raw = raw.get("data")
        if isinstance(raw, str) and raw.startswith("0x") and len(raw) >= 10:
            name = self.errors.get(raw[:10])
            if name:
                return name
        return str(error)


def link_bytecode(artifact: dict, addresses: dict[str, str]) -> str:
    """Splice library addresses into an unlinked forge artifact (cf. upstream v2-acceptance.mjs)."""
    code = artifact["bytecode"]["object"]
    for source, libs in (artifact["bytecode"].get("linkReferences") or {}).items():
        for name, refs in libs.items():
            addr = addresses[f"{source}:{name}"]
            for ref in refs:
                off = 2 + ref["start"] * 2
                code = code[:off] + addr[2:].lower() + code[off + 40:]
    if "__$" in code:
        raise ValueError("unlinked Solidity library in bytecode")
    return code


class Sender:
    """Sends transactions either from an anvil-unlocked account or a local key."""

    def __init__(self, rpc: Rpc, address: str | None = None, private_key: str | None = None):
        self.rpc = rpc
        self.private_key = private_key
        if private_key:
            from eth_account import Account

            self.account = Account.from_key(private_key)
            self.address = self.account.address
        else:
            if not address:
                raise ValueError("sender needs an unlocked address or a private key")
            self.account = None
            self.address = to_checksum_address(address)
        self._lock = threading.Lock()

    def send(self, to: str | None, data: str = "0x", value: int = 0, gas: int | None = None,
             wait: bool = True) -> dict:
        tx: dict[str, Any] = {"from": self.address, "data": data, "value": hex(value)}
        if to:
            tx["to"] = to
        with self._lock:
            if gas is None:
                gas = int(int(self.rpc.call("eth_estimateGas", [tx]), 16) * 1.25) + 25_000
            tx["gas"] = hex(gas)
            if self.account is None:
                tx_hash = self.rpc.call("eth_sendTransaction", [tx])
            else:
                chain_id = self.rpc.chain_id()
                nonce = int(self.rpc.call("eth_getTransactionCount", [self.address, "pending"]), 16)
                base = int(self.rpc.block("latest").get("baseFeePerGas", "0x0"), 16)
                try:
                    tip = int(self.rpc.call("eth_maxPriorityFeePerGas"), 16)
                except RpcError:
                    tip = GWEI
                signed = self.account.sign_transaction({
                    "chainId": chain_id, "nonce": nonce, "to": to and to_checksum_address(to),
                    "value": value, "data": data, "gas": gas,
                    "maxFeePerGas": base * 2 + tip, "maxPriorityFeePerGas": tip, "type": 2,
                })
                raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
                tx_hash = self.rpc.call("eth_sendRawTransaction", ["0x" + bytes(raw).hex()])
        if not wait:
            return {"transactionHash": tx_hash}
        receipt = self.rpc.wait(tx_hash)
        if receipt.get("status") != "0x1":
            raise RuntimeError(f"transaction {tx_hash} reverted")
        return receipt
