"use client";

import { useState } from "react";
import { chain, type RegisterResult } from "@/lib/chain";

// On-chain registration for a module, gated on BlocTime. One "Register" click:
// if the key holds BlocTime it registers free; otherwise the API reports
// payment_required and we surface a "Mint $1 & register" confirm — the $1 funds
// the weekly pool paid out to BlocTime holders.
export default function RegisterPanel({
  name,
  schema,
  onRegistered,
}: {
  name: string;
  schema: string | null;
  onRegistered?: () => void;
}) {
  const [key, setKey] = useState("");
  const [data, setData] = useState(schema || "");
  const [busy, setBusy] = useState(false);
  const [quote, setQuote] = useState<Extract<RegisterResult, { status: "payment_required" }> | null>(null);
  const [done, setDone] = useState<Extract<RegisterResult, { status: "registered" }> | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const submit = async (pay: boolean) => {
    setBusy(true);
    setErr(null);
    try {
      const res = await chain.register({
        name,
        data: data.trim(),
        key: key.trim() || undefined,
        pay,
      });
      if (res.status === "payment_required") {
        setQuote(res);
      } else {
        setDone(res);
        setQuote(null);
        onRegistered?.();
      }
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    return (
      <div className="panel reg-panel">
        <h3>registered on-chain ✓</h3>
        <p className="reg-note">
          <code>{name}</code> is now in the Registry, signed by{" "}
          <code>{done.address.slice(0, 10)}…</code>
          {done.paid
            ? " — paid $1 into the weekly BlocTime pool."
            : ` — free (held ${(done.bloctime / 1e18).toFixed(2)} BLOC).`}
        </p>
      </div>
    );
  }

  return (
    <div className="panel reg-panel">
      <h3>register {name} on-chain</h3>
      <p className="reg-note">
        Registers this module in the on-chain Registry. Free if your key holds
        BlocTime; otherwise mint $1 of MOD — the $1 funds the weekly pool paid
        out to BlocTime holders.
      </p>

      <label className="reg-field">
        <span>signing key</span>
        <input
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder="key name (server-held identity)"
          spellCheck={false}
          autoComplete="off"
        />
      </label>
      <label className="reg-field">
        <span>data (CID / manifest)</span>
        <input
          value={data}
          onChange={(e) => setData(e.target.value)}
          placeholder="schema CID — required by the Registry"
          spellCheck={false}
          autoComplete="off"
        />
      </label>

      {err && <div className="reg-err">{err}</div>}

      {quote ? (
        <div className="reg-quote">
          <div className="reg-quote-head">
            No BlocTime on <code>{quote.address.slice(0, 10)}…</code> — pay $
            {quote.price_usd.toFixed(2)} to register?
          </div>
          <div className="reg-actions">
            <button className="btn btn-primary" disabled={busy} onClick={() => submit(true)}>
              {busy ? "minting…" : `⛓ Mint $${quote.price_usd.toFixed(2)} & register`}
            </button>
            <button className="btn btn-ghost" disabled={busy} onClick={() => setQuote(null)}>
              cancel
            </button>
          </div>
          <div className="reg-fineprint">$1 → weekly pool for BlocTime holders.</div>
        </div>
      ) : (
        <div className="reg-actions">
          <button
            className="btn btn-primary"
            disabled={busy || !data.trim()}
            onClick={() => submit(false)}
          >
            {busy ? "registering…" : "Register on-chain"}
          </button>
        </div>
      )}
    </div>
  );
}
