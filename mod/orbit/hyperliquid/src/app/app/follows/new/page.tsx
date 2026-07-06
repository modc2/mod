"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { createFollow } from "../../lib/api";
import { useWallet } from "../../lib/wallet";

export default function NewFollowPage() {
  return <Suspense fallback={<div className="text-xs text-muted">loading…</div>}><Inner /></Suspense>;
}

function Inner() {
  const router = useRouter();
  const sp = useSearchParams();
  const leader = sp.get("leader") || "";
  const { address } = useWallet();
  const [follower, setFollower] = useState(address ?? "");
  const [sizePct, setSizePct] = useState(10);
  const [maxPerTrade, setMaxPerTrade] = useState(0);
  const [allow, setAllow] = useState("");
  const [deny, setDeny] = useState("");
  const [vault, setVault] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { if (address && !follower) setFollower(address); }, [address, follower]);

  const submit = async () => {
    if (!leader) { setErr("missing leader"); return; }
    if (!/^0x[a-fA-F0-9]{40}$/.test(follower.trim())) { setErr("invalid follower address"); return; }
    setBusy(true); setErr(null);
    try {
      await createFollow({
        follower: follower.trim().toLowerCase(),
        leader: leader.toLowerCase(),
        size_pct: sizePct,
        max_per_trade_usd: maxPerTrade,
        coins_allow: allow.split(",").map((s) => s.trim()).filter(Boolean),
        coins_deny: deny.split(",").map((s) => s.trim()).filter(Boolean),
        vault_address: vault.trim() || undefined,
      });
      router.push("/follows");
    } catch (e: any) { setErr(e.message ?? String(e)); }
    finally { setBusy(false); }
  };

  return (
    <div className="max-w-xl space-y-5">
      <div>
        <h1 className="text-xl text-ink">copy a trader</h1>
        <p className="text-xs text-muted mt-1">
          The engine watches the leader's fills and emits scaled-down signals for your wallet.
          Signals are surfaced in <span className="text-ink">/signals</span>; you sign and submit.
        </p>
      </div>

      <div className="panel p-5 space-y-4">
        <Field label="leader (trader you copy)">
          <input className="input w-full num" value={leader} readOnly />
        </Field>
        <Field label="your wallet (follower)">
          <input className="input w-full num" placeholder="0x…"
            value={follower} onChange={(e) => setFollower(e.target.value)} />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="size (% of leader fill)">
            <input className="input w-full num" type="number" min={0} max={100} step={0.5}
              value={sizePct} onChange={(e) => setSizePct(Number(e.target.value))} />
          </Field>
          <Field label="max per trade (USD)  0=∞">
            <input className="input w-full num" type="number" min={0} step={50}
              value={maxPerTrade} onChange={(e) => setMaxPerTrade(Number(e.target.value))} />
          </Field>
        </div>
        <Field label="allow only coins (comma-separated)">
          <input className="input w-full" placeholder="BTC, ETH, SOL"
            value={allow} onChange={(e) => setAllow(e.target.value)} />
        </Field>
        <Field label="deny coins">
          <input className="input w-full" placeholder=""
            value={deny} onChange={(e) => setDeny(e.target.value)} />
        </Field>
        <Field label="route through vault (optional)">
          <input className="input w-full num" placeholder="0xvault…"
            value={vault} onChange={(e) => setVault(e.target.value)} />
        </Field>
        {err && <div className="text-loss text-xs">{err}</div>}
        <div className="flex gap-2">
          <button className="btn-primary" onClick={submit} disabled={busy}>
            {busy ? "saving…" : "start copying"}
          </button>
          <button className="btn" onClick={() => router.back()}>cancel</button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="label">{label}</div>
      {children}
    </div>
  );
}
