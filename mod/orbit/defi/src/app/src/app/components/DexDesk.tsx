"use client";

import { useEffect, useMemo, useState } from "react";
import { dexQuote, dexSwap, getVenues } from "../lib/api";

type Props = { onClose: () => void; say: (text: string, bad?: boolean) => void };

/// The trading desk, in the console.
///
/// Nothing here talks to a chain. Every button is one call to this module's
/// /dex routes, which hand the work to the module that owns that chain — eth,
/// solana or bt — and those hold the keys. So the panel asks for the account
/// *name* the chain module knows, never a private key, and the token box is
/// that module's bearer, forwarded untouched.
export default function DexDesk({ onClose, say }: Props) {
  const [venues, setVenues] = useState<any[]>([]);
  const [modules, setModules] = useState<Record<string, any>>({});
  const [chain, setChain] = useState("base");
  const [sell, setSell] = useState("ETH");
  const [buy, setBuy] = useState("USDC");
  const [amount, setAmount] = useState("0.1");
  const [slippage, setSlippage] = useState("50");
  const [account, setAccount] = useState("");
  const [auth, setAuth] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [quote, setQuote] = useState<any>(null);
  const [trade, setTrade] = useState<any>(null);
  const [busy, setBusy] = useState<"" | "quote" | "trade">("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getVenues()
      .then((v) => {
        setVenues(v.venues ?? []);
        setModules(v.modules ?? {});
      })
      .catch((e) => setError(e.message));
  }, []);

  const venue = useMemo(() => venues.find((v) => v.chain === chain), [venues, chain]);
  const backing = venue ? modules[venue.module] : null;

  const run = async (what: "quote" | "trade") => {
    setBusy(what);
    setError(null);
    if (what === "quote") setTrade(null);
    const body: any = {
      chain,
      sell: sell.trim(),
      buy: buy.trim(),
      amount: amount.trim(),
      slippageBps: Number(slippage) || 50,
    };
    if (auth.trim()) body.auth = auth.trim();
    if (account.trim()) body.account = account.trim();
    try {
      if (what === "quote") {
        setQuote(await dexQuote(body));
      } else {
        const out = await dexSwap({ ...body, confirm });
        setTrade(out);
        if (out?.quote) setQuote(out.quote);
        say(
          out?.traded
            ? `traded on ${chain}`
            : out?.needs_confirm
              ? "tick confirm to send it"
              : "priced, not sent"
        );
      }
    } catch (e: any) {
      setError(e.message);
      say(e.message, true);
    } finally {
      setBusy("");
    }
  };

  const out = quote?.buy ?? null;
  const impact = quote?.price_impact_pct;
  const result = trade?.result ?? null;
  // Only ever a link the chain module actually handed back — a guessed
  // explorer URL for the wrong network is worse than no link.
  const link: string | null = result?.explorer ?? null;

  return (
    <div className="drawer">
      <div style={{ padding: "11px 12px", borderBottom: "1px solid var(--line)", display: "flex" }}>
        <span style={{ fontSize: 12, fontWeight: 600, flex: 1 }}>Trade on the DEXes</span>
        <button className="ghost" onClick={onClose} style={{ padding: "2px 8px" }}>
          ×
        </button>
      </div>

      <div className="scroll" style={{ flex: 1, padding: 12 }}>
        <div className="label">Venue</div>
        <select value={chain} onChange={(e) => setChain(e.target.value)} style={{ width: "100%" }}>
          {venues.map((v) => (
            <option key={v.chain} value={v.chain}>
              {v.label} — {v.venue}
            </option>
          ))}
        </select>
        <div style={{ display: "flex", gap: 6, marginTop: 7, alignItems: "center" }}>
          <span className={`pill ${backing?.reachable ? "ok" : "bad"}`}>
            <span className="dot" />
            {venue?.module ?? "…"} {backing?.reachable ? "up" : "down"}
          </span>
          {venue?.testnet && <span className="pill warn">testnet</span>}
          {venue && !venue.testnet && <span className="pill">real money</span>}
        </div>
        {venue && (
          <div className="mono-small" style={{ marginTop: 6, lineHeight: 1.6 }}>
            {venue.trades}
          </div>
        )}

        <div className="label" style={{ marginTop: 16 }}>
          Sell → buy
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          <input value={sell} onChange={(e) => setSell(e.target.value)} placeholder="ETH" />
          <input value={buy} onChange={(e) => setBuy(e.target.value)} placeholder="USDC" />
        </div>
        <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
          <input
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            placeholder="amount of sell"
          />
          <input
            value={slippage}
            onChange={(e) => setSlippage(e.target.value)}
            placeholder="slippage bps"
            style={{ width: 110 }}
          />
        </div>
        <button
          onClick={() => run("quote")}
          disabled={busy !== "" || !sell.trim() || !buy.trim() || !amount.trim()}
          style={{ width: "100%", marginTop: 7 }}
        >
          {busy === "quote" ? "pricing…" : "quote"}
        </button>

        {quote && (
          <div className="card" style={{ marginTop: 10 }}>
            <div style={{ display: "flex", gap: 7, alignItems: "baseline" }}>
              <span style={{ fontSize: 13, fontWeight: 600, flex: 1 }}>
                {out?.amount ?? "—"} {out?.symbol ?? ""}
              </span>
              <span className="pill">{quote.venue}</span>
            </div>
            <div className="mono-small" style={{ marginTop: 6, lineHeight: 1.7 }}>
              rate {quote.rate ?? "—"} · min after slippage {quote.min_received ?? "—"}
              {impact != null && ` · impact ${Number(impact).toFixed(4)}%`}
              <br />
              route{" "}
              {(quote.route ?? [])
                .map((r: any) => r.pool ?? r.amm ?? "—")
                .join(" → ") || "direct"}
              <br />
              priced by {quote.quoted_by} ({quote.module})
            </div>
          </div>
        )}

        <div className="label" style={{ marginTop: 18 }}>
          Who signs
        </div>
        <input
          value={account}
          onChange={(e) => setAccount(e.target.value)}
          placeholder={
            venue?.module === "eth"
              ? "eth account name (eth_accounts)"
              : venue?.module === "solana"
                ? "solana keystore wallet"
                : "bittensor coldkey"
          }
        />
        <input
          value={auth}
          onChange={(e) => setAuth(e.target.value)}
          type="password"
          placeholder={`bearer token for the ${venue?.module ?? "chain"} module`}
          style={{ marginTop: 6 }}
        />
        <label
          style={{
            display: "flex",
            gap: 7,
            alignItems: "center",
            margin: "10px 0",
            fontSize: 11,
            color: "var(--dim)",
          }}
        >
          <input
            type="checkbox"
            checked={confirm}
            onChange={(e) => setConfirm(e.target.checked)}
            style={{ width: "auto" }}
          />
          yes, trade real money
        </label>
        <button
          className="primary"
          onClick={() => run("trade")}
          disabled={busy !== "" || !sell.trim() || !buy.trim() || !amount.trim()}
          style={{ width: "100%" }}
        >
          {busy === "trade" ? "sending…" : confirm ? "TRADE" : "trade (dry until confirmed)"}
        </button>

        {trade && (
          <div className="card" style={{ marginTop: 10 }}>
            <div style={{ fontSize: 12, fontWeight: 600 }}>
              {trade.traded ? "sent" : trade.needs_confirm ? "not sent — needs confirm" : "not sent"}
            </div>
            <div className="mono-small" style={{ marginTop: 6, lineHeight: 1.7 }}>
              {trade.reason ?? trade.executed_by}
              {result?.hash && (
                <>
                  <br />
                  {result.hash}
                </>
              )}
              {result?.signature && (
                <>
                  <br />
                  {result.signature}
                </>
              )}
            </div>
            {link && (
              <a href={link} target="_blank" rel="noreferrer" className="mono-small">
                explorer ↗
              </a>
            )}
          </div>
        )}

        {error && (
          <div className="card" style={{ marginTop: 10, borderColor: "#3a2126" }}>
            <div className="mono-small" style={{ color: "var(--danger)", lineHeight: 1.6 }}>
              {error}
            </div>
          </div>
        )}

        <div className="mono-small" style={{ marginTop: 16, lineHeight: 1.6 }}>
          This module holds no keys. The chain module signs, and its own guard —
          confirm on mainnet, USD ceilings, a locked keystore — is the one that
          applies. Agents get the same desk as MCP tools: defi_dex_venues,
          defi_dex_quote, defi_dex_swap.
        </div>
      </div>
    </div>
  );
}
