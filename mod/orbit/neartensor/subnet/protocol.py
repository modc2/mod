"""
Wire protocol for the NearTensor subnet — the dedicated task set.

Every task asks a miner a question about NEAR chain state. The miner must
anchor its answer to a concrete `block_hash`, which is what makes answers
deterministically re-verifiable: the validator queries its OWN NEAR RPC at
that exact block hash and demands byte-equal canonical answers.

Transport-neutral: tasks and responses are plain dicts over HTTP POST
/synapse, so the same protocol runs against LocalChain or real subtensor.
Responses are signed by the miner's hotkey (sr25519 via bittensor_wallet
when available) over the answer digest.
"""
import hashlib
import json
import secrets
import time

SPEC_VERSION = 1

# Task registry: name -> which canonical fields the answer must carry.
TASKS = {
    # Latest finalized block header attestation.
    "block_header": ["height", "block_hash", "prev_hash", "epoch_id", "timestamp_ns"],
    # Gas price at the anchored block.
    "gas_price": ["block_hash", "gas_price"],
    # Account state (balance + storage) at the anchored block.
    "account_state": ["block_hash", "account_id", "amount", "locked", "storage_usage"],
}

# Well-known NEAR accounts a validator may probe with account_state.
PROBE_ACCOUNTS = {
    "testnet": ["testnet", "aurora", "guest-book.testnet", "wrap.testnet"],
    "mainnet": ["near", "aurora", "wrap.near", "usdt.tether-token.near"],
}


def make_task(name, near_network="testnet", account_id=None):
    """Build one task request. `nonce` binds the response to this query."""
    if name not in TASKS:
        raise ValueError(f"unknown task: {name}")
    params = {}
    if name == "account_state":
        params["account_id"] = account_id or secrets.choice(PROBE_ACCOUNTS[near_network])
    return {
        "spec": SPEC_VERSION,
        "task": name,
        "params": params,
        "nonce": secrets.token_hex(8),
        "sent_at": time.time(),
    }


def canon(obj):
    """Canonical JSON — the single serialization both sides hash and sign."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def answer_digest(task, nonce, answer):
    """Digest a miner signs and a validator verifies: binds answer to query."""
    return hashlib.sha256(canon({"task": task, "nonce": nonce, "answer": answer}).encode()).hexdigest()


# ── Answering (miner side) ──────────────────────────────────────────────

def solve(task_req, near):
    """Answer a task using a NearClient. Returns the canonical answer dict."""
    name = task_req["task"]
    params = task_req.get("params", {})
    if name == "block_header":
        h = near.final_block()["header"]
        return {
            "height": int(h["height"]),
            "block_hash": h["hash"],
            "prev_hash": h["prev_hash"],
            "epoch_id": h["epoch_id"],
            "timestamp_ns": int(h["timestamp_nanosec"]),
        }
    if name == "gas_price":
        anchor = near.final_block()["header"]["hash"]
        gp = near.gas_price(anchor)
        return {"block_hash": anchor, "gas_price": str(gp["gas_price"])}
    if name == "account_state":
        account_id = params["account_id"]
        anchor = near.final_block()["header"]["hash"]
        acct = near.view_account(account_id, block_hash=anchor)
        return {
            "block_hash": anchor,
            "account_id": account_id,
            "amount": str(acct["amount"]),
            "locked": str(acct["locked"]),
            "storage_usage": int(acct["storage_usage"]),
        }
    raise ValueError(f"unknown task: {name}")


# ── Ground truth (validator side) ───────────────────────────────────────

def ground_truth(task_req, answer, near):
    """
    Recompute the canonical answer at the miner's anchored block using the
    validator's own RPC view. Returns (truth_answer, validator_final_height).
    Raises on RPC failure (scored as unverifiable, not as miner failure).
    """
    name = task_req["task"]
    final_height = near.final_height()
    anchor = answer.get("block_hash")
    if not anchor:
        return None, final_height
    if name == "block_header":
        h = near.block_by_hash(anchor)["header"]
        truth = {
            "height": int(h["height"]),
            "block_hash": h["hash"],
            "prev_hash": h["prev_hash"],
            "epoch_id": h["epoch_id"],
            "timestamp_ns": int(h["timestamp_nanosec"]),
        }
    elif name == "gas_price":
        gp = near.gas_price(anchor)
        truth = {"block_hash": anchor, "gas_price": str(gp["gas_price"])}
    elif name == "account_state":
        account_id = answer.get("account_id", "")
        acct = near.view_account(account_id, block_hash=anchor)
        truth = {
            "block_hash": anchor,
            "account_id": account_id,
            "amount": str(acct["amount"]),
            "locked": str(acct["locked"]),
            "storage_usage": int(acct["storage_usage"]),
        }
    else:
        return None, final_height
    return truth, final_height


def anchored_height(answer, near):
    """Height of the block the answer is anchored to (for freshness scoring)."""
    if "height" in answer:
        return int(answer["height"])
    return int(near.block_by_hash(answer["block_hash"])["header"]["height"])


# ── Signing ─────────────────────────────────────────────────────────────

def sign_digest(keypair, digest_hex):
    if keypair is None:
        return ""
    return keypair.sign(digest_hex.encode()).hex()


def verify_signature(hotkey_ss58, digest_hex, signature_hex):
    """Verify an sr25519 signature. True/False; False also when unverifiable."""
    if not signature_hex or not hotkey_ss58:
        return False
    try:
        from bittensor_wallet import Keypair
        kp = Keypair(ss58_address=hotkey_ss58)
        return kp.verify(digest_hex.encode(), bytes.fromhex(signature_hex))
    except Exception:
        return False
