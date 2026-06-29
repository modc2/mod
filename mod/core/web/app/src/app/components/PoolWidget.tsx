"use client";

import { useEffect, useState } from "react";
import { chain, type Claimable, type Pool } from "@/lib/chain";

// The weekly reward pool: funded by $1 MOD mints from non-BlocTime registrants,
// distributed to BlocTime holders by their share. Shows the pool size and lets
// a holder check + claim their portion by key.
export default function PoolWidget() {
  const [pool, setPool] = useState<Pool | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [key, setKey] = useState("");
  const [claim, setClaim] = useState<Claimable | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    chain
      .pool()
      .then((p) => alive && setPool(p))
      .catch((e) => alive && setErr(String(e)));
    return () => {
      alive = false;
    };
  }, []);

  const check = async () => {
    if (!key.trim()) return;
    setBusy(true);
    setMsg(null);
    try {
      // Resolve the key's address via a register dry-run? Simpler: the claimable
      // endpoint accepts an address OR a key-name; pass the key name through.
      const c = await chain.claimable(key.trim());
      setClaim(c);
    } catch (e) {
      setMsg(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  };

  const doClaim = async () => {
    setBusy(true);
    setMsg(null);
    try {
      await chain.claim({ key: key.trim() || undefined });
      setMsg("claimed ✓");
      const [p, c] = await Promise.all([chain.pool(), chain.claimable(key.trim())]);
      setPool(p);
      setClaim(c);
    } catch (e) {
      setMsg(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  };

  if (err) return null; // chain down — hide the widget rather than show an error

  return (
    <div className="pool-widget">
      <div className="pool-main">
        <div className="pool-eyebrow">⛓ weekly reward pool</div>
        <div className="pool-size">
          {pool ? `$${pool.pool_usd.toFixed(2)}` : "—"}
        </div>
        <div className="pool-sub">
          funded by $1 MOD mints · distributed to BlocTime holders
          {pool ? ` · ${(pool.total_bloctime / 1e18).toFixed(0)} BLOC staked` : ""}
        </div>
      </div>

      <div className="pool-claim">
        <div className="pool-claim-row">
          <input
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder="your key / address"
            spellCheck={false}
            autoComplete="off"
          />
          <button className="chip-toggle" disabled={busy || !key.trim()} onClick={check}>
            check
          </button>
        </div>
        {claim && (
          <div className="pool-claim-info">
            <span>
              {(claim.bloctime / 1e18).toFixed(2)} BLOC →{" "}
              <b>${claim.claimable_usd.toFixed(4)}</b> claimable
            </span>
            <button
              className="btn btn-primary pool-claim-btn"
              disabled={busy || claim.claimable_usd <= 0}
              onClick={doClaim}
            >
              {busy ? "…" : "claim"}
            </button>
          </div>
        )}
        {msg && <div className="pool-msg">{msg}</div>}
      </div>
    </div>
  );
}
