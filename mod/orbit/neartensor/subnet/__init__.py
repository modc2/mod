"""
NearTensor dedicated Bittensor subnet.

A subnet whose single job is NEAR chain attestation: miners serve verifiable
answers about NEAR Protocol state (final block headers, gas price, account
state), validators re-verify every answer against their own NEAR RPC view at
the exact block hash the miner claims, score correctness / freshness / latency,
and set weights.

Local-first: with network="local" the whole subnet runs on one box against a
file-backed metagraph (LocalChain) — no wallet, no subtensor connection, no
external service beyond public NEAR RPC. Point network at "test" or "finney"
and the identical protocol code runs against real subtensor via the bittensor
SDK (SubtensorChain).
"""
from .config import SubnetConfig
from .chain import get_chain, LocalChain
from .protocol import TASKS, make_task, canon, answer_digest
from .reward import score_response, normalize_weights

__all__ = [
    "SubnetConfig", "get_chain", "LocalChain",
    "TASKS", "make_task", "canon", "answer_digest",
    "score_response", "normalize_weights",
]
