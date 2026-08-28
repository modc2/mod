"""
The compute inventory — everything Cathedral will actually sell you, in one list.

The upstream catalog answers "what profiles exist", and buries the hardware
inside them. Read literally it shows two profiles, `attest.v1` and `custom.v1`,
and hides the rest: the confidential GPU is not a profile at all but an entry in
every profile's `hardware_classes`, the hybrid-GPU preview lives in the same
place, and the four sealed-worker sizes are down inside `custom.v1.resources`.
Somebody skimming it sees two things and misses a 96 GiB GPU and five buyable
shapes.

`build()` flattens that into one hardware class per row — what it is, what
shapes you can order on it, what each costs, which endpoint orders it, and,
where a class is shut, exactly which gate is holding it shut. Nothing is
invented: every field came out of the catalog, and what the catalog does not say
is None rather than a guess.

Pure and offline — it takes the catalog dict (and optionally the cc_gpu gate
verdict) and returns a dict. Both the CLI (`m cathedral/inventory`) and the BYOK
gateway (`GET /inventory`) render this same answer.
"""

LIVE_AVAILABILITY = ("available", "live_testing")

# The catalog names a hardware class twice: `id` inside the profile, and the
# `execution_class` the API is ordered against. Keyed by execution class here,
# because that is the thing you buy.
CLASS_ID_TO_EXECUTION = {"tdx_cpu": "tdx_cpu", "confidential_gpu": "cc_gpu",
                         "hybrid_gpu": "hybrid_gpu_preview"}

NAMES = {
    "tdx_cpu": "Intel TDX CPU",
    "cc_gpu": "Confidential GPU",
    "hybrid_gpu_preview": "Hybrid GPU",
}
WHAT = {
    "tdx_cpu": "Sealed Intel TDX machine. Evidence covers the machine, the "
               "workload and the result; egress defaults to none.",
    "cc_gpu": "AMD SEV host with a confidential NVIDIA GPU, one execution at a "
              "time, on spot capacity with no auto-restart.",
    "hybrid_gpu_preview": "TDX controller driving a remote GPU. Transport is "
                          "encrypted, but inputs are plaintext to the GPU host "
                          "— GPU memory is not confidential.",
}
# Which endpoint actually orders a shape on this profile.
ORDERS = {"attest.v1": "run", "custom.v1": "rent"}
UNITS = {"completed_receipt": "execution", "verified_execution": "execution",
         "worker_hour": "hour"}


def _unit(pricing):
    unit = (pricing or {}).get("unit")
    return UNITS.get(unit, unit)


def _evidence(hw):
    """Split the class's *_status fields into what is proven and what is not."""
    checks = {k: v for k, v in hw.items()
              if k.endswith("_status") and isinstance(v, str)}
    return {
        "checks": checks,
        "pass": sorted(k for k, v in checks.items() if v == "PASS"),
        "unproven": sorted(k for k, v in checks.items() if v != "PASS"),
        "attests": hw.get("evidence"),
        "scope": hw.get("evidence_scope"),
        "captured_at": hw.get("evidence_captured_at"),
        "live_digest": hw.get("live_evidence_digest"),
    }


def _hardware(hw):
    """The physical facts, only where the catalog states them."""
    spec = {
        "provider": hw.get("provider"),
        "machine_type": hw.get("machine_type"),
        "cpu_tee": hw.get("cpu_tee"),
        "gpu_type": hw.get("gpu_type"),
        "gpu_count": hw.get("gpu_count"),
        "gpu_memory_gib": hw.get("gpu_memory_gib"),
        "gpu_memory_confidential": hw.get("gpu_memory_confidential"),
        "provisioning": hw.get("provisioning_models"),
        "network_egress": hw.get("network_egress"),
        "capacity": hw.get("capacity"),
        "runtime_image": (hw.get("runtime") or {}).get("image"),
        "supported_binaries": (hw.get("runtime") or {}).get("supported_binaries"),
        "requires_scope": hw.get("requires_scope"),
    }
    return {k: v for k, v in spec.items() if v is not None}


def _profile_shapes(prof):
    """The shapes a profile's own `resources` block sells.

    One-shot profiles state a single shape as a dict; `custom.v1` states a list
    of named sizes, each with its own hourly rate.
    """
    res = prof.get("resources")
    if isinstance(res, dict):
        res = [res]
    if not isinstance(res, list):
        return []
    pricing = prof.get("pricing") or {}
    out = []
    for r in res:
        if not isinstance(r, dict):
            continue
        hourly = r.get("price_usd_per_hour")
        mode = (r.get("gpu") or {}).get("mode")
        out.append({
            "name": r.get("size") or prof.get("name") or prof.get("id"),
            "profile": prof.get("id"),
            "class_id": r.get("hardware_class"),
            "cpu": r.get("cpu"),
            "memory_gib": r.get("memory_gib"),
            "gpu": None if mode in (None, "none") else mode,
            "price_usd": hourly if hourly is not None else pricing.get("amount_usd"),
            "unit": "hour" if hourly is not None else _unit(pricing),
            "minutes_included": pricing.get("runtime_minutes_included"),
            "lifetimes": prof.get("lifetimes") or [],
            "order": ORDERS.get(prof.get("id")),
        })
    return out


def _class_shape(hw, profiles):
    """The shape sold by a hardware class that no profile's `resources` names —
    the confidential GPU and the hybrid preview are only described here."""
    pricing = hw.get("pricing") or {}
    execution = CLASS_ID_TO_EXECUTION.get(hw.get("id"), hw.get("execution_class"))
    gpu = hw.get("gpu_type")
    return {
        "name": (gpu.replace("_", " ") if gpu else NAMES.get(execution, hw.get("id"))),
        "profile": hw.get("profile_id") or (profiles[0] if profiles else None),
        "class_id": hw.get("id"),
        "cpu": None,
        "memory_gib": None,
        "gpu": hw.get("gpu_count") or (1 if gpu else None),
        "price_usd": pricing.get("amount_usd"),
        "unit": _unit(pricing) or ("hour" if execution == "hybrid_gpu_preview" else None),
        "minutes_included": pricing.get("runtime_minutes_included"),
        "lifetimes": ["one_shot"] if execution == "cc_gpu" else ["bounded_service"],
        "order": "gpu" if execution == "cc_gpu" else "rent",
        # The hybrid preview has no published rate: Cathedral quotes it before it
        # reserves anything, and `rent` sends that quote ceiling explicitly.
        "quoted": hw.get("billing") == "full_rate_prequoted_before_reservation" or None,
    }


def _say(value):
    """Blockers are read by people; print JSON words, not Python ones."""
    return {True: "true", False: "false", None: "null"}.get(value, value)


def _status(execution, hw, gates):
    """live / preview / unavailable, plus why — never a cheerier answer than the
    catalog supports."""
    availability = hw.get("availability")
    blockers = []
    if execution == "cc_gpu":
        # The GPU has a real gate verdict; defer to it rather than re-deriving.
        if gates is not None:
            if gates.get("ready"):
                return "live", []
            for k, v in (gates.get("gates") or {}).items():
                if k == "operations":
                    blockers += [f"operations.{op}={val}" for op, val in (v or {}).items() if not val]
                elif k == "availability" and v not in LIVE_AVAILABILITY:
                    blockers.append(f"availability={_say(v)}")
                elif k == "live_evidence_digest" and not v:
                    blockers.append("no live_evidence_digest")
                elif k not in ("availability", "live_evidence_digest") and v not in (True, "PASS"):
                    blockers.append(f"{k}={_say(v)}")
            if gates.get("reason"):
                blockers.append(gates["reason"])
            return "unavailable", blockers
    if hw.get("customer_enabled") is False:
        blockers.append("customer_enabled=false")
    if availability in LIVE_AVAILABILITY and not blockers:
        return "live", []
    if availability == "preview":
        return "preview", blockers
    blockers.insert(0, f"availability={_say(availability)}")
    return "unavailable", blockers


def build(catalog, gates=None, source=None):
    """Flatten a `/profiles` payload into the compute you can actually buy."""
    if not isinstance(catalog, dict) or catalog.get("error"):
        return catalog if isinstance(catalog, dict) else {"error": "no catalog"}

    classes, order = {}, []
    profiles = [p for p in (catalog.get("profiles") or []) if isinstance(p, dict)]

    for prof in profiles:
        for hw in prof.get("hardware_classes") or []:
            if not isinstance(hw, dict):
                continue
            execution = hw.get("execution_class") or CLASS_ID_TO_EXECUTION.get(hw.get("id"))
            if not execution:
                continue
            entry = classes.get(execution)
            if entry is None:
                entry = classes[execution] = {
                    "execution_class": execution,
                    "id": hw.get("id"),
                    "name": NAMES.get(execution, execution),
                    "what": WHAT.get(execution),
                    "availability": hw.get("availability"),
                    "customer_enabled": hw.get("customer_enabled"),
                    "profiles": [], "shapes": [], "notes": [], "_raw": hw,
                }
                order.append(execution)
            # The same class appears under several profiles, sometimes tersely.
            # Keep the fullest description of it.
            if len(hw) > len(entry["_raw"]):
                entry["_raw"] = hw
                entry["availability"] = hw.get("availability")
                entry["customer_enabled"] = hw.get("customer_enabled")
            if prof.get("id") and prof["id"] not in entry["profiles"]:
                entry["profiles"].append(prof["id"])

        # Disclosures the profile makes about a class are part of the answer.
        trust = prof.get("trust") or {}
        if trust.get("hybrid_gpu_disclosure") and "hybrid_gpu_preview" in classes:
            note = trust["hybrid_gpu_disclosure"]
            if note not in classes["hybrid_gpu_preview"]["notes"]:
                classes["hybrid_gpu_preview"]["notes"].append(note)

    # Shapes named by a profile's resources block. A size that names no hardware
    # class belongs to the profile's base class — the first one it lists, which
    # is the CPU the profile runs on; the GPUs come after it as add-ons.
    for prof in profiles:
        listed = [hw.get("execution_class") or CLASS_ID_TO_EXECUTION.get(hw.get("id"))
                  for hw in prof.get("hardware_classes") or [] if isinstance(hw, dict)]
        base = next((e for e in listed if e), None)
        for shape in _profile_shapes(prof):
            execution = CLASS_ID_TO_EXECUTION.get(shape["class_id"], shape["class_id"]) or base
            if execution in classes:
                classes[execution]["shapes"].append(shape)

    # Shapes only the hardware class itself describes (both GPU paths).
    for execution, entry in classes.items():
        if not entry["shapes"]:
            entry["shapes"].append(_class_shape(entry["_raw"], entry["profiles"]))

    out = []
    for execution in order:
        entry = classes[execution]
        hw = entry.pop("_raw")
        entry["status"], entry["blockers"] = _status(execution, hw, gates)
        entry["hardware"] = _hardware(hw)
        entry["evidence"] = _evidence(hw)
        entry["operations"] = hw.get("operations")
        for shape in entry["shapes"]:
            shape["orderable"] = entry["status"] != "unavailable"
        out.append(entry)

    shapes = [s for c in out for s in c["shapes"]]
    priced = [s["price_usd"] for s in shapes if isinstance(s.get("price_usd"), (int, float))]
    return {
        "classes": out,
        "totals": {
            "hardware_classes": len(out),
            "shapes": len(shapes),
            "orderable_shapes": sum(1 for s in shapes if s["orderable"]),
            "live": sum(1 for c in out if c["status"] == "live"),
            "preview": sum(1 for c in out if c["status"] == "preview"),
            "unavailable": sum(1 for c in out if c["status"] == "unavailable"),
            "cheapest_usd": min(priced) if priced else None,
            "dearest_usd": max(priced) if priced else None,
        },
        "source": source,
    }
