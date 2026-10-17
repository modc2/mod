"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import * as api from "../lib/api";
import { money, pct } from "./Modules";

/// The ASK desk, in the hub's rail: one sentence in, a ranked plan out.
///
/// The plan is the API's (POST /ask) — deterministic, free, the same for
/// everyone. The EXPLAIN button is the only thing that talks to a model, and
/// it only phrases the plan it is handed.

type Props = {
  say: (text: string, bad?: boolean) => void;
  address: string | null;
  /// Open a pick in the MODULES room — by hub id, and the exact module id.
  onPick: (id: string, moduleId?: string | null) => void;
};

const RISKS = [
  { id: "low", label: "SAFE", hint: "core names only, ranked by the floor of their record" },
  { id: "medium", label: "BALANCED", hint: "core and established names with a solid record" },
  { id: "high", label: "BOLD", hint: "everything with a quoted rate — thin histories shown, not filtered" },
];
const HORIZONS = [
  { months: 1, label: "1 mo" },
  { months: 3, label: "3 mo" },
  { months: 6, label: "6 mo" },
  { months: 12, label: "1 yr" },
];
const EXAMPLES = [
  "I have $5,000 for six months, keep it safe",
  "2k USDC on Base for a year",
  "what pays most for 1000 over 3 months",
  "10k on solana, balanced, from this desk",
];

const dollars = (v: number | null | undefined) =>
  v === null || v === undefined || !isFinite(Number(v))
    ? "—"
    : `$${Number(v).toLocaleString(undefined, { maximumFractionDigits: Math.abs(Number(v)) < 100 ? 2 : 0 })}`;

const horizonWord = (months: number) =>
  months >= 12 && months % 12 === 0 ? `${months / 12} year${months === 12 ? "" : "s"}` : months === 1 ? "1 month" : `${Number(months.toFixed(1))} months`;

export default function Ask({ say, address, onPick }: Props) {
  const [q, setQ] = useState("");
  // Appetite and horizon chips are OVERRIDES — unset, the sentence decides.
  const [risk, setRisk] = useState<string>("");
  const [months, setMonths] = useState<number | null>(null);
  const [plan, setPlan] = useState<any>(null);
  const [busy, setBusy] = useState<"" | "plan" | "explain">("");
  const [explain, setExplain] = useState<any>(null);
  const [agent, setAgent] = useState<boolean | null>(null);

  useEffect(() => {
    api.askStatus().then((s) => setAgent(!!s?.explain?.reachable)).catch(() => setAgent(false));
  }, []);

  const run = async (text?: string) => {
    const question = (text ?? q).trim();
    if (text !== undefined) setQ(text);
    setBusy("plan");
    setExplain(null);
    try {
      const body: any = { question };
      if (risk) body.risk = risk;
      if (months) body.months = months;
      setPlan(await api.ask(body));
    } catch (e: any) {
      say(e.message, true);
    } finally {
      setBusy("");
    }
  };

  const runExplain = async () => {
    if (!plan) return;
    setBusy("explain");
    try {
      const body: any = { question: plan.brief?.question ?? q, risk: plan.brief?.risk, amount: plan.brief?.amount, months: plan.brief?.months };
      const out = await api.askExplain(body);
      setExplain(out.explain);
    } catch (e: any) {
      say(e.message, true);
    } finally {
      setBusy("");
    }
  };

  const brief = plan?.brief;
  const picks: any[] = plan?.picks ?? [];

  // A chip changed after a plan exists: re-plan with the override, no extra click.
  const planned = useRef(false);
  useEffect(() => {
    if (!planned.current) return;
    run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [risk, months]);
  useEffect(() => {
    if (plan) planned.current = true;
  }, [plan]);

  return (
    <div className="ask">
      <div className="ask-title">
        <span style={{ color: "var(--accent)" }}>✦</span> Ask the desk
      </div>
      <div className="ask-sub">Say what you have, for how long, and how brave you feel. The answer is built from the vetted list&apos;s own numbers.</div>
      <form
        className="ask-form"
        onSubmit={(e) => {
          e.preventDefault();
          run();
        }}
      >
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="I have $5,000 for six months, keep it safe" />
        <button className="primary" type="submit" disabled={busy !== ""}>
          {busy === "plan" ? "…" : "plan"}
        </button>
      </form>
      <div className="ask-chips">
        {RISKS.map((r) => (
          <button
            key={r.id}
            className={`chip ${risk === r.id || (!risk && brief?.risk === r.id) ? "active" : ""}`}
            title={r.hint}
            onClick={() => setRisk(risk === r.id ? "" : r.id)}
          >
            {r.label}
          </button>
        ))}
        <span className="ask-gap" />
        {HORIZONS.map((h) => (
          <button
            key={h.months}
            className={`chip ${months === h.months ? "active" : ""}`}
            title="horizon — or just say it in the question"
            onClick={() => setMonths(months === h.months ? null : h.months)}
          >
            {h.label}
          </button>
        ))}
      </div>
      {!plan && (
        <div className="ask-examples">
          {EXAMPLES.map((ex) => (
            <button key={ex} className="ghost" onClick={() => run(ex)} disabled={busy !== ""}>
              {ex}
            </button>
          ))}
        </div>
      )}

      {plan && (
        <div className="ask-out">
          <div className="ask-brief">
            I read: <b>{dollars(brief.amount)}</b> · <b>{horizonWord(brief.months)}</b> · <b>{brief.risk} appetite</b>
            {brief.chain ? <> · <b>{brief.chain}</b></> : null}
            {brief.from_desk ? <> · <b>from this desk</b></> : null}
          </div>
          {picks.length === 0 && (
            <div className="empty" style={{ padding: "12px 0" }}>
              nothing on the vetted list fits that — loosen the appetite, drop the chain, or say &quot;balanced&quot;
            </div>
          )}
          {picks.map((p) => (
            <div key={p.id} className="ask-pick">
              <div className="ask-pick-head">
                <span className="ask-rank">{p.rank}</span>
                <span className="hub-name">{p.name}</span>
                <span className={`tag ${p.tier === "core" ? "ok" : p.tier === "frontier" ? "bad" : ""}`}>{p.tier}</span>
                <span className="dim" style={{ marginLeft: "auto" }}>
                  {p.symbol} on {p.chain}
                </span>
              </div>
              <div className="ask-earn">
                <div className="ask-earn-main">
                  <span className="apy-big" style={{ fontSize: 19 }}>
                    +{dollars(p.earn?.typical)}
                  </span>
                  <span className="dim">
                    {p.thin_record ? "at today's headline" : "typically"}, over {horizonWord(brief.months)} · {pct(p.apy_typical, 1)} a year
                  </span>
                  {p.thin_record && <span className="tag bad">thin record</span>}
                </div>
                <div className="ask-earn-band">
                  <span>
                    floor <b>+{dollars(p.earn?.floor)}</b>
                  </span>
                  <span>
                    ceiling <b>+{dollars(p.earn?.ceiling)}</b>
                  </span>
                  <span className="dim">{money(p.pool_tvl_usd)} deep</span>
                </div>
              </div>
              {p.risks?.[0] && (
                <div className="cond risk" style={{ marginTop: 6 }}>
                  <span className="cond-dot" />
                  <span>{p.risks[0]}</span>
                </div>
              )}
              <div className="ask-pick-foot">
                <span className="mono-small" style={{ flex: 1, minWidth: 0 }}>
                  {p.way_in}
                </span>
                <button className={p.enterable ? "primary" : ""} onClick={() => onPick(p.id, p.module_id)}>
                  {p.enterable ? "put USD in →" : "open →"}
                </button>
              </div>
            </div>
          ))}
          {picks.length > 0 && (
            <div className="mono-small" style={{ marginTop: 8, lineHeight: 1.6 }}>
              {plan.rule}. Floor and ceiling are the observed APR band&apos;s bounds; typical is today&apos;s rate capped by the 30-day mean.
              Nothing here is advice.
            </div>
          )}
          {picks.length > 0 && (
            <div style={{ marginTop: 10 }}>
              {explain ? (
                <div className="card ask-explain">
                  <div className="label">the agent&apos;s read</div>
                  <div style={{ marginTop: 6, lineHeight: 1.65, fontSize: 11.5, whiteSpace: "pre-wrap" }}>{explain.answer ?? explain.error}</div>
                  {explain.model && (
                    <div className="mono-small" style={{ marginTop: 6 }}>
                      {explain.model}
                      {explain.cost_usd != null ? ` · $${Number(explain.cost_usd).toFixed(3)}` : ""} · phrases the plan, never picks
                    </div>
                  )}
                </div>
              ) : (
                <button
                  className="ghost"
                  style={{ width: "100%" }}
                  onClick={runExplain}
                  disabled={busy !== "" || !address || agent === false}
                  title={
                    !address
                      ? "connect your wallet — the agent's read spends model credits"
                      : agent === false
                        ? "the agent contract is not reachable right now"
                        : "a short plain-English read of this exact plan"
                  }
                >
                  {busy === "explain" ? "the agent is reading…" : !address ? "connect wallet for the agent's read" : "✦ explain this in plain English"}
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/// "What $X becomes" — the same projection the ASK desk uses, for one
/// protocol the user already has open. Floor and ceiling come from its
/// observed APR band; today's rate is the headline.
export function Becomes({ apyNow, band, unit = "USD" }: { apyNow: number | null | undefined; band: any; unit?: string }) {
  const [amount, setAmount] = useState("1000");
  const [months, setMonths] = useState(6);
  const n = Number(amount.replace(/[^0-9.]/g, "")) || 0;
  const earn = (apr: number | null | undefined) =>
    apr === null || apr === undefined || !isFinite(Number(apr)) ? null : n * (Math.pow(1 + Number(apr) / 100, months / 12) - 1);
  const rows = useMemo(
    () =>
      [
        { k: "floor", apr: band ? Math.max(0, Number(band.apr_lower)) : null, hint: "the lowest the record supports" },
        { k: "today", apr: apyNow ?? null, hint: "the headline rate right now" },
        { k: "ceiling", apr: band ? Number(band.apr_upper) : null, hint: "the highest the record supports" },
      ].filter((r) => r.apr !== null),
    [band, apyNow, n, months],
  );
  if (rows.length === 0) return null;
  return (
    <div className="card becomes">
      <div className="becomes-head">
        <span className="label" style={{ margin: 0 }}>What {unit} becomes</span>
        <div className="becomes-ctl">
          <input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" />
          {HORIZONS.map((h) => (
            <button key={h.months} className={`chip ${months === h.months ? "active" : ""}`} onClick={() => setMonths(h.months)}>
              {h.label}
            </button>
          ))}
        </div>
      </div>
      <div className="becomes-rows">
        {rows.map((r) => (
          <div key={r.k} className="becomes-row" title={r.hint}>
            <span className="dim">{r.k}</span>
            <span>{pct(r.apr, 1)}</span>
            <b>+{dollars(earn(r.apr))}</b>
          </div>
        ))}
      </div>
      <div className="mono-small" style={{ marginTop: 6 }}>compounded from the quoted APY over {horizonWord(months)} · a bound, not a promise</div>
    </div>
  );
}
