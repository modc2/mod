"""
Cathedral API — BYOK (bring-your-own-key) gateway to https://cathedral.computer.

Confidential compute over HTTP: attested TDX CPU jobs, sealed persistent
workers, confidential GPU executions, each returning a signed receipt.

WHOSE CREDITS
    Every caller sends their own `cat_sk_*` key, and every upstream call is made
    with exactly that key — so the compute is rented, and the prepaid USD
    credits are drawn down, on behalf of the person who made the request.

    This server holds no house key and will not fall back to one. If the
    process happens to have CATHEDRAL_API_KEY in its environment it is refused
    for caller requests (see _reject_ambient_key), because serving one person's
    job from another person's balance is the single failure this gateway exists
    to prevent. Keys are never persisted, logged, or echoed back; only a
    sha256 fingerprint ever leaves this process.

Headers (BYOK):
    Authorization: Bearer cat_sk_...    (or  x-cathedral-key: cat_sk_... )

Get a key at https://cathedral.computer/account/ — key issuance is free, paid
execution needs prepaid credits on that same account.
"""

import hashlib
import json
import os
import uuid
from typing import Any, Optional

import requests
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

UPSTREAM = os.environ.get("CATHEDRAL_BASE", "https://cathedral.computer/v1").rstrip("/")
CC_GPU_PROFILE = "gcp-g4-rtx-pro-6000-sev-v1"
# The catalog says "live_testing" for shipping profiles and "available" in the
# docs' prose; either counts as live. The evidence gates do the real
# gatekeeping, and they fail closed.
LIVE_AVAILABILITY = ("available", "live_testing")
CONFIRM_USD = 0.20

PRICES = {
    "free_test": 0.0,
    "attest.v1": 0.20,
    "cc_gpu": 3.00,
}
HOURLY = {"4x16": 0.40, "8x32": 0.80, "22x88": 2.20, "44x176": 4.40}

ERRORS = {
    400: "malformed request or invalid verification code",
    401: "missing, revoked, or invalid API key",
    402: "insufficient prepaid credits — top up your own Cathedral account",
    409: "capability unavailable, or conflicts with worker state",
    422: "valid JSON that violates the selected profile contract",
    425: "receipt is not publishable yet",
    503: "capacity, email, billing, or verification service temporarily unavailable",
}

app = FastAPI(
    title="Cathedral BYOK API",
    description="Rent confidential compute from cathedral.computer with your own key and your own credits.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── BYOK credential dependency ───────────────────────────────────────────

def fingerprint(key: str) -> str:
    return "cat:" + hashlib.sha256(key.encode()).hexdigest()[:12]


def _reject_ambient_key(key: str):
    """Refuse to serve a request with the server's own ambient credential.

    A deployment may legitimately have CATHEDRAL_API_KEY set for admin CLI use.
    It must never end up paying for a caller's job, so if a caller somehow
    presents it we treat that as a configuration error, not a valid session.
    """
    house = os.environ.get("CATHEDRAL_API_KEY")
    if house and key == house:
        raise HTTPException(
            403,
            detail={
                "error": "server key refused",
                "why": "this gateway never rents compute on its own credits — bring your own cat_sk_* key",
                "get_a_key": "https://cathedral.computer/account/",
            },
        )


def caller_key(
    authorization: Optional[str] = Header(None),
    x_cathedral_key: Optional[str] = Header(None),
) -> str:
    raw = x_cathedral_key or ""
    if not raw and authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:]
    raw = raw.strip()
    if not raw:
        raise HTTPException(
            401,
            detail={
                "error": "missing credentials",
                "how": "send Authorization: Bearer cat_sk_... (BYOK — your key, your credits)",
                "get_a_key": "https://cathedral.computer/account/",
            },
        )
    _reject_ambient_key(raw)
    return raw


def upstream(method: str, path: str, key: str, body: Any = None,
             idempotency_key: str = None, timeout: int = 60):
    """One call to Cathedral, signed with the caller's key and nobody else's."""
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    try:
        r = requests.request(method, UPSTREAM + path, headers=headers,
                             data=json.dumps(body) if body is not None else None,
                             timeout=timeout)
    except requests.RequestException as e:
        raise HTTPException(502, detail={"error": "cathedral unreachable", "message": str(e)})
    try:
        payload = r.json()
    except ValueError:
        payload = {"raw": r.text[:2000]}
    if r.status_code >= 400:
        raise HTTPException(
            r.status_code if 400 <= r.status_code < 600 else 502,
            detail={"error": ERRORS.get(r.status_code, "cathedral rejected the request"),
                    "upstream_status": r.status_code, "detail": payload},
        )
    return payload


def confirm(price_usd: float, confirmed: bool, key: str):
    """Anything above $0.20 needs explicit consent from the person paying."""
    if price_usd > CONFIRM_USD and not confirmed:
        raise HTTPException(
            402,
            detail={"error": "confirmation required", "price_usd": price_usd,
                    "threshold_usd": CONFIRM_USD, "pays": fingerprint(key),
                    "how": "resend with confirm=true to authorize this spend against your own credits"},
        )


# ── public: no key, no charge ────────────────────────────────────────────

@app.get("/")
def info():
    return {
        "name": "cathedral",
        "version": "0.1.0",
        "description": "BYOK gateway to Cathedral confidential compute — attested runs with signed receipts.",
        "upstream": UPSTREAM,
        "billing": "every run is charged to the caller's own prepaid Cathedral credits; this gateway holds no house key",
        "byok": {
            "headers": ["Authorization: Bearer cat_sk_...", "x-cathedral-key: cat_sk_... (alternative)"],
            "get_a_key": "https://cathedral.computer/account/",
        },
        "endpoints": {
            "GET /health": "liveness",
            "GET /prices": "published price sheet (public)",
            "GET /profiles": "live catalog + runtime gates (public)",
            "GET /gpu/ready": "are the confidential-GPU gates PASS right now (public)",
            "GET /credits": "your prepaid balance",
            "GET /credits/packs": "buyable packs (public)",
            "POST /credits/checkout": "hosted Stripe URL for your own top-up",
            "POST /credits/verify": "settle the redirect race after paying",
            "POST /free-test": "your one free restricted minute",
            "POST /run": "one verified TDX CPU job (attest.v1, $0.20)",
            "POST /rent": "keep a sealed worker alive (custom.v1, hourly)",
            "POST /gpu": "one confidential-GPU execution (cc_gpu, $3.00, catalog-gated)",
            "GET /workers": "your workers",
            "GET /workers/{id}": "one worker",
            "DELETE /workers/{id}": "tear down",
            "GET /receipts/{id}": "the signed receipt (425 while pending)",
        },
        "docs": "https://cathedral.computer/docs/",
    }


@app.get("/health")
def health():
    return {"ok": True, "upstream": UPSTREAM}


def live_prices() -> dict:
    """Today's prices from the catalog, falling back to the built-in table.

    The spend guard quotes from this, never from the static table alone — a
    stale price would ask the payer to confirm the wrong number.
    """
    sheet = {"per_execution_usd": dict(PRICES), "hourly_keep_alive_usd": dict(HOURLY),
             "source": "built-in table"}
    try:
        catalog = profiles()
    except HTTPException:
        return sheet
    for prof in catalog.get("profiles") or []:
        pricing = prof.get("pricing") or {}
        if prof.get("id") == "attest.v1" and pricing.get("amount_usd") is not None:
            sheet["per_execution_usd"]["attest.v1"] = pricing["amount_usd"]
        if prof.get("id") == "custom.v1":
            shapes = {f"{r.get('cpu')}x{r.get('memory_gib')}": r["price_usd_per_hour"]
                      for r in prof.get("resources") or []
                      if isinstance(r, dict) and r.get("price_usd_per_hour") is not None}
            if shapes:
                sheet["hourly_keep_alive_usd"] = shapes
    gpu = _cc_gpu_entry(catalog)
    if gpu and (gpu.get("pricing") or {}).get("amount_usd") is not None:
        sheet["per_execution_usd"]["cc_gpu"] = gpu["pricing"]["amount_usd"]
    sheet["source"] = UPSTREAM + "/profiles"
    return sheet


@app.get("/prices")
def prices():
    sheet = live_prices()
    sheet["confirm_threshold_usd"] = CONFIRM_USD
    sheet["note"] = ("one-shot profiles bill per execution, not by elapsed time; "
                     "storage and egress are not included unless quoted")
    return sheet


@app.get("/profiles")
def profiles():
    """Public catalog. Its fields are runtime gates — read them before submitting."""
    try:
        r = requests.get(UPSTREAM + "/profiles", timeout=30)
        return r.json()
    except Exception as e:
        raise HTTPException(502, detail={"error": "cathedral unreachable", "message": str(e)})


@app.get("/credits/packs")
def packs():
    try:
        r = requests.get(UPSTREAM + "/credits/packs", timeout=30)
        return r.json()
    except Exception as e:
        raise HTTPException(502, detail={"error": "cathedral unreachable", "message": str(e)})


def _cc_gpu_entry(catalog: dict) -> Optional[dict]:
    """The confidential-GPU entry is not a top-level profile — it hangs off each
    profile's `hardware_classes` as the entry with execution_class == 'cc_gpu'."""
    for prof in catalog.get("profiles") or []:
        if not isinstance(prof, dict):
            continue
        for hw in prof.get("hardware_classes") or []:
            if isinstance(hw, dict) and hw.get("execution_class") == "cc_gpu":
                return hw
        if CC_GPU_PROFILE in (prof.get("profile_id"), prof.get("id")):
            return prof
    return None


def _cc_gpu_gates() -> dict:
    entry = _cc_gpu_entry(profiles())
    if not entry:
        return {"ready": False, "reason": f"{CC_GPU_PROFILE} not in catalog"}
    ops = entry.get("operations") or {}
    gates = {
        "availability": entry.get("availability"),
        "customer_enabled": entry.get("customer_enabled"),
        "cathedral_evidence_status": entry.get("cathedral_evidence_status"),
        "verifier_log_digest_evidence_status": entry.get("verifier_log_digest_evidence_status"),
        "live_evidence_digest": entry.get("live_evidence_digest"),
        "operations": {k: ops.get(k) for k in ("create", "cancel", "retry", "receipt")},
    }
    ready = (gates["availability"] in LIVE_AVAILABILITY
             and gates["customer_enabled"] is True
             and gates["cathedral_evidence_status"] == "PASS"
             and gates["verifier_log_digest_evidence_status"] == "PASS"
             and bool(gates["live_evidence_digest"])
             and all(gates["operations"].values()))
    return {"ready": ready, "profile_id": entry.get("profile_id", CC_GPU_PROFILE),
            "gates": gates, "runtime_image": (entry.get("runtime") or {}).get("image")}


@app.get("/gpu/ready")
def gpu_ready():
    return _cc_gpu_gates()


# ── your account, your credits ───────────────────────────────────────────

@app.get("/me")
def me(key: str = Depends(caller_key)):
    """Who pays for this session, and what they have left."""
    return {"pays": fingerprint(key), "credits": upstream("GET", "/credits", key)}


@app.get("/credits")
def credits(key: str = Depends(caller_key)):
    return upstream("GET", "/credits", key)


class Checkout(BaseModel):
    pack_id: str = "pack_10"


@app.post("/credits/checkout")
def checkout(body: Checkout, key: str = Depends(caller_key)):
    """Hosted Stripe checkout for the CALLER's own credits — never the server's."""
    out = upstream("POST", "/credits/checkout", key, {"pack_id": body.pack_id})
    if isinstance(out, dict):
        out["pays"] = fingerprint(key)
        out["next"] = "POST /credits/verify with the returned session_id"
    return out


class VerifyPayment(BaseModel):
    session_id: str


@app.post("/credits/verify")
def verify_payment(body: VerifyPayment, key: str = Depends(caller_key)):
    return upstream("POST", "/credits/verify", key, {"session_id": body.session_id})


# ── running work ─────────────────────────────────────────────────────────

@app.post("/free-test")
def free_test(key: str = Depends(caller_key)):
    """One fixed restricted minute per verified account. 409 once it is used."""
    return upstream("POST", "/workers/free-test", key)


class RunRequest(BaseModel):
    image: str = "python:3.12-slim"
    command: list[str] = ["python", "-c", "print(6 * 7)"]
    minutes: int = 10
    max_spend_usd: float = 0.20
    egress: str = "none"
    confirm: bool = False
    idempotency_key: Optional[str] = None


@app.post("/run")
def run(body: RunRequest, key: str = Depends(caller_key)):
    """attest.v1 — one fresh, bounded TDX CPU job with a teardown-bound receipt."""
    price = live_prices()["per_execution_usd"]["attest.v1"]
    confirm(min(body.max_spend_usd, price), body.confirm, key)
    payload = {
        "profile": "attest.v1",
        "lifetime": {"mode": "one_shot", "reuse": "forbidden", "max_runtime_minutes": body.minutes},
        "workload": {"image": body.image, "command": body.command},
        "network": {"egress": body.egress},
        "budget": {"max_spend_usd": body.max_spend_usd, "auto_stop": True},
    }
    idem = body.idempotency_key or f"run-{uuid.uuid4().hex[:16]}"
    out = upstream("POST", "/workers", key, payload, idempotency_key=idem)
    if isinstance(out, dict):
        out["pays"] = fingerprint(key)
        out["idempotency_key"] = idem
    return out


class RentRequest(BaseModel):
    image: Optional[str] = None
    name: str = "sealed-worker"
    minutes: int = 60
    ssh_public_key: Optional[str] = None
    cpu: Optional[int] = None
    memory_gib: Optional[int] = None
    max_spend_usd: float = 1.0
    gpu: Optional[str] = None
    accepted_max_hourly_usd: Optional[float] = None
    confirm: bool = False
    idempotency_key: Optional[str] = None


@app.post("/rent")
def rent(body: RentRequest, key: str = Depends(caller_key)):
    """custom.v1 — a sealed worker kept alive, the only hourly-metered shape.

    `gpu` opts into the provider-trusted hybrid preview: transport is encrypted
    but inputs are plaintext to the GPU host. It is not confidential GPU.
    """
    confirm(body.max_spend_usd, body.confirm, key)
    payload: dict = {
        "profile": "custom.v1",
        "name": body.name,
        "lifetime": {"mode": "bounded_service", "reuse": "allowed", "max_runtime_minutes": body.minutes},
        "budget": {"max_spend_usd": body.max_spend_usd, "auto_stop": True},
    }
    if body.image:
        payload["workload"] = {"image": body.image}
    if body.ssh_public_key:
        payload["ssh_public_key"] = body.ssh_public_key
    resources: dict = {}
    if body.cpu or body.memory_gib or body.gpu:
        resources["hardware_class"] = "tdx_cpu"
    if body.cpu:
        resources["cpu"] = body.cpu
    if body.memory_gib:
        resources["memory_gib"] = body.memory_gib
    if body.gpu:
        resources["gpu"] = {"mode": "hybrid", "type": body.gpu, "provider": "cloud",
                            "accepted_max_hourly_usd": body.accepted_max_hourly_usd or body.max_spend_usd}
    if resources:
        payload["resources"] = resources
    idem = body.idempotency_key or f"rent-{uuid.uuid4().hex[:16]}"
    out = upstream("POST", "/workers", key, payload, idempotency_key=idem)
    if isinstance(out, dict):
        out["pays"] = fingerprint(key)
        out["idempotency_key"] = idem
    return out


class GpuRequest(BaseModel):
    command: list[str]
    image: Optional[str] = None
    minutes: int = 10
    max_spend_usd: float = 3.0
    max_attempts: int = 2
    checkpoint: str = "checkpoint.json"
    confirm: bool = False
    idempotency_key: Optional[str] = None


@app.post("/gpu")
def gpu(body: GpuRequest, key: str = Depends(caller_key)):
    """cc_gpu — one confidential-GPU execution, refused unless the gates PASS."""
    gate = _cc_gpu_gates()
    if not gate.get("ready"):
        raise HTTPException(409, detail={"error": "confidential GPU not submittable right now",
                                         "gates": gate.get("gates"), "reason": gate.get("reason")})
    confirm(max(body.max_spend_usd, live_prices()["per_execution_usd"]["cc_gpu"]), body.confirm, key)
    image = body.image or gate.get("runtime_image")
    if not image:
        raise HTTPException(409, detail={"error": "no pinned runtime image in the catalog"})
    payload = {
        "profile": CC_GPU_PROFILE,
        "execution_class": "cc_gpu",
        "lifetime": {"mode": "one_shot", "max_runtime_minutes": body.minutes, "reuse": "forbidden"},
        "resources": {
            "hardware_class": "confidential_gpu", "provider": "gcp",
            "machine_type": "g4-standard-48", "cpu_tee": "amd_sev",
            "provisioning_model": "spot",
            "gpu": {"mode": "confidential", "provider": "gcp",
                    "type": "nvidia_rtx_pro_6000_96gb", "count": 1},
        },
        "workload": {"image": image, "command": body.command},
        "network": {"egress": "control_plane_only"},
        "budget": {"max_spend_usd": body.max_spend_usd, "auto_stop": True},
        "retry": {"max_attempts": body.max_attempts,
                  "checkpoint": {"mode": "on_preemption", "artifact_path": body.checkpoint}},
        "protected_inputs": [],
    }
    idem = body.idempotency_key or f"gpu-{uuid.uuid4().hex[:16]}"
    out = upstream("POST", "/workers", key, payload, idempotency_key=idem)
    if isinstance(out, dict):
        out["pays"] = fingerprint(key)
        out["idempotency_key"] = idem
    return out


# ── worker lifecycle + evidence ──────────────────────────────────────────

@app.get("/workers")
def workers(key: str = Depends(caller_key)):
    return upstream("GET", "/workers", key)


@app.get("/workers/{worker_id}")
def worker(worker_id: str, key: str = Depends(caller_key)):
    """A one-shot reaches `completed` only after fresh-environment deletion."""
    return upstream("GET", f"/workers/{worker_id}", key)


@app.delete("/workers/{worker_id}")
def stop(worker_id: str, key: str = Depends(caller_key)):
    return upstream("DELETE", f"/workers/{worker_id}", key)


@app.get("/receipts/{receipt_id}")
def receipt(receipt_id: str, key: str = Depends(caller_key)):
    """The signed cathedral_customer_receipt_v1 bytes. 425 while still pending.

    Verify offline against https://cathedral.computer/customer-receipt-trusted-keys.json
    — pin that key file through an independent channel first.
    """
    return upstream("GET", f"/receipts/{receipt_id}", key)
