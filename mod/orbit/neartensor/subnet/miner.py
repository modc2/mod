"""
Miner neuron — answers NEAR attestation tasks over plain HTTP.

POST /synapse  {spec, task, params, nonce}  →  signed canonical answer
GET  /health   who this miner is and what it serves

Run directly (uvicorn) or via `m neartensor sn_miner`. On startup it
registers on the chain (local file or subtensor) and advertises its URL.
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, HTTPException

from subnet.config import SubnetConfig
from subnet.chain import get_chain
from subnet.keys import get_keypair
from subnet.near_client import NearClient
from subnet import protocol


def create_app(cfg=None):
    cfg = cfg or SubnetConfig.load()
    near = NearClient(cfg.near_rpcs, timeout=cfg.query_timeout)
    keypair = get_keypair(cfg, "miner")
    hotkey = keypair.ss58_address if keypair else "unsigned-miner"

    app = FastAPI(title="NearTensor Miner",
                  description="Dedicated Bittensor subnet miner: NEAR chain attestation.")
    app.state.cfg, app.state.hotkey = cfg, hotkey

    @app.on_event("startup")
    def announce():
        try:
            chain = get_chain(cfg)
            chain.register(hotkey, role="miner")
            chain.serve(hotkey, cfg.miner_url())
        except Exception as e:
            print(f"miner: chain announce failed (serving anyway): {e}")

    @app.get("/health")
    def health():
        return {"status": "ok", "neuron": "miner", "hotkey": hotkey,
                "network": cfg.network, "netuid": cfg.netuid,
                "near_network": cfg.near_network, "tasks": list(protocol.TASKS)}

    @app.post("/synapse")
    def synapse(req: dict):
        task = req.get("task")
        if task not in protocol.TASKS:
            raise HTTPException(status_code=400, detail=f"unknown task: {task}")
        t0 = time.time()
        try:
            answer = protocol.solve(req, near)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"NEAR query failed: {e}")
        digest = protocol.answer_digest(task, req.get("nonce", ""), answer)
        return {
            "spec": protocol.SPEC_VERSION,
            "task": task,
            "nonce": req.get("nonce", ""),
            "answer": answer,
            "digest": digest,
            "hotkey": hotkey,
            "signature": protocol.sign_digest(keypair, digest),
            "elapsed": round(time.time() - t0, 4),
        }

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    cfg = app.state.cfg
    uvicorn.run(app, host=cfg.miner_host, port=int(os.getenv("PORT", cfg.miner_port)))
