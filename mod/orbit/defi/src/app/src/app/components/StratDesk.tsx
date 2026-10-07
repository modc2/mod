"use client";

import { useEffect, useMemo, useState } from "react";
import * as api from "../lib/api";
import { pct } from "./Modules";

type Props = { say: (text: string, bad?: boolean) => void };

/// The STRATS room — one board for every trading strategy on the fleet,
/// across every venue, all on the ONE canonical Strat protocol owned by the
/// strat module. The strats shipped inside polymarket, hyperliquid and
/// copytensor (bittensor) appear here unchanged as <module>.<strat>; the
/// builtins and orbit strat mods ride the same contract.
///
/// Everything on this desk is a read or pure data. BACKTEST replays history;
/// PLAN returns the exact config that venue module's OWN live engine would
/// consume — starting it stays with that module and its gates.

const VENUE_FILTERS = [
  { id: "", label: "ALL VENUES" },
  { id: "polymarket", label: "POLYMARKET" },
  { id: "hyperliquid", label: "HYPERLIQUID" },
  { id: "bittensor", label: "BITTENSOR" },
  { id: "raydium", label: "RAYDIUM" },
  { id: "uniswap", label: "UNISWAP" },
];

const SOURCE_WORD: Record<string, string> = {
  polymarket: "polymarket",
  hyperliquid: "hyperliquid",
  copytensor: "bittensor (bt/copytensor)",
};

function perfCell(perf: any) {
  if (!perf || typeof perf !== "object") return <span className="dim">—</span>;
  if (typeof perf.roi_pct === "number") {
    const cls = perf.roi_pct >= 0 ? "ok" : "bad";
    return (
      <span>
        <span className={`tag ${cls}`}>{pct(perf.roi_pct)}</span>{" "}
        <span className="dim">
          {perf.currency ?? ""} · {perf.days ?? "?"}d{perf.stale ? " · stale" : ""}
        </span>
      </span>
    );
  }
  return <span className="dim">{perf.note ?? perf.error ?? "—"}</span>;
}

export default function StratDesk({ say }: Props) {
  const [board, setBoard] = useState<any>(null);
  const [sources, setSources] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [venue, setVenue] = useState("");
  const [picked, setPicked] = useState<any>(null);
  const [card, setCard] = useState<any>(null);
  const [busy, setBusy] = useState<"" | "board" | "backtest" | "plan">("");

  // Backtest / plan controls.
  const [days, setDays] = useState("7");
  const [capital, setCapital] = useState("1000");
  const [eoa, setEoa] = useState("");
  const [hotkey, setHotkey] = useState("");
  const [result, setResult] = useState<any>(null);
  const [plan, setPlan] = useState<any>(null);

  const load = async (refresh = false) => {
    setBusy("board");
    try {
      const [b, s] = await Promise.all([
        api.getStratBoard(7, refresh),
        api.getStratSources(),
      ]);
      setBoard(b);
      setSources(s.sources ?? null);
      if (refresh) say("board re-backtested across the venues");
    } catch (e: any) {
      setError(e.message);
      say(e.message, true);
    } finally {
      setBusy("");
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const rows = useMemo(() => {
    const all = board?.strats ?? [];
    return venue ? all.filter((s: any) => (s.venues ?? []).includes(venue)) : all;
  }, [board, venue]);

  const open = async (s: any) => {
    setPicked(s);
    setCard(null);
    setResult(null);
    setPlan(null);
    try {
      setCard(await api.getStrat(s.name));
    } catch (e: any) {
      say(e.message, true);
    }
  };

  const runBacktest = async () => {
    if (!picked) return;
    setBusy("backtest");
    setResult(null);
    try {
      const r = await api.stratBacktest({
        name: picked.name,
        days: Number(days) || 7,
        capital: Number(capital) || 1000,
      });
      setResult(r);
      say(
        typeof r.roi_pct === "number"
          ? `${picked.name}: ${pct(r.roi_pct)} over ${r.days}d (${r.currency})`
          : r.note ?? "no history to replay"
      );
    } catch (e: any) {
      say(e.message, true);
    } finally {
      setBusy("");
    }
  };

  const runPlan = async () => {
    if (!picked) return;
    setBusy("plan");
    setPlan(null);
    const body: Record<string, any> = {
      name: picked.name,
      capital: Number(capital) || 100,
    };
    if (eoa.trim()) body.eoa = eoa.trim();
    if (hotkey.trim()) body.hotkey = hotkey.trim();
    try {
      setPlan(await api.stratPlan(body));
      say("plan built — pure data, nothing started");
    } catch (e: any) {
      say(e.message, true);
    } finally {
      setBusy("");
    }
  };

  const needsEoa = picked?.source === "hyperliquid";
  const needsHotkey = picked?.source === "copytensor";

  return (
    <div className="fin">
      <div className="fin-head">
        <div>
          <div className="hub-head">STRATS — every venue, one protocol</div>
          <div className="dim" style={{ maxWidth: 640, marginTop: 4 }}>
            The strategies shipped inside the polymarket, hyperliquid and bittensor
            (bt/copytensor) modules, bridged unchanged onto the canonical Strat
            protocol, next to the builtins. Backtests replay real venue history;
            PLAN hands back the config that venue&apos;s own live engine consumes —
            this desk never signs and never starts anything.
          </div>
        </div>
        <button className="ghost" disabled={busy === "board"} onClick={() => load(true)}>
          {busy === "board" ? "RE-RUNNING…" : "RE-BACKTEST BOARD"}
        </button>
      </div>

      {sources && (
        <div className="strat-sources">
          {Object.entries(sources).map(([mod, s]: [string, any]) => {
            const whole = s.available && (s.drift ?? []).length === 0;
            return (
              <span key={mod} className={`tag ${whole ? "ok" : "bad"}`} title={s.package}>
                {SOURCE_WORD[mod] ?? mod} · {(s.strats ?? []).length} strats ·{" "}
                {whole ? "under protocol" : s.available ? `drift: ${s.drift.join("; ")}` : "missing"}
              </span>
            );
          })}
        </div>
      )}

      <div className="chips">
        {VENUE_FILTERS.map((f) => (
          <button
            key={f.id}
            className={`pill ${venue === f.id ? "ok" : ""}`}
            onClick={() => setVenue(f.id)}
          >
            {f.label}
          </button>
        ))}
      </div>

      {error && <div className="empty">{error}</div>}
      {!error && !board && <div className="empty">reading the strat registry…</div>}

      <div className="fin-body">
        <div className="scroll fin-list">
          <div className="strat-head">
            <span>STRAT</span>
            <span>ORIGIN</span>
            <span>VENUES</span>
            <span>LEADERS</span>
            <span>BACKTEST</span>
          </div>
          {rows.map((s: any) => (
            <div
              key={s.name}
              className={`strat-row ${picked?.name === s.name ? "active" : ""}`}
              onClick={() => open(s)}
            >
              <span>
                <b>{s.name}</b>
                {!s.ok && <span className="tag bad" style={{ marginLeft: 6 }}>broken</span>}
                <div className="dim">{s.description}</div>
              </span>
              <span className="dim">{s.origin === "bridge" ? `bridged · ${s.source}` : s.origin}</span>
              <span className="dim">{(s.venues ?? []).join(", ")}</span>
              <span className="dim">{s.selects ? "picks its own" : "needs a list"}</span>
              <span>{perfCell(s.perf)}</span>
            </div>
          ))}
          {board && rows.length === 0 && <div className="empty">no strats trade {venue}</div>}
          {board && (
            <div className="dim" style={{ padding: "10px 12px" }}>
              TAO rows and USDC rows are different currencies AND different native
              backtest models — never compare raw numbers across venues.
            </div>
          )}
        </div>

        {picked && (
          <div className="rail scroll">
            <div className="rail-head">{picked.name}</div>
            <div className="dim" style={{ marginBottom: 8 }}>{picked.description}</div>
            {card?.verify && (
              <div style={{ marginBottom: 8 }}>
                <span className={`tag ${card.verify.ok ? "ok" : "bad"}`}>
                  {card.verify.ok ? "honors the protocol" : "contract issues"}
                </span>
                {!card.verify.ok &&
                  (card.verify.issues ?? []).map((i: string) => (
                    <div key={i} className="issue">{i}</div>
                  ))}
              </div>
            )}
            {picked.params && Object.keys(picked.params).length > 0 && (
              <div className="kv-grid">
                {Object.entries(picked.params).map(([k, v]) => (
                  <div key={k} className="stat">
                    <span className="stat-l">{k}</span>
                    <span className="mono-small">{v === null ? "required" : String(v)}</span>
                  </div>
                ))}
              </div>
            )}

            <div className="label" style={{ marginTop: 14 }}>BACKTEST — pure read</div>
            <div className="strat-form">
              <input value={days} onChange={(e) => setDays(e.target.value)} placeholder="days" />
              <input value={capital} onChange={(e) => setCapital(e.target.value)} placeholder="capital" />
              <button className="ghost" disabled={busy === "backtest"} onClick={runBacktest}>
                {busy === "backtest" ? "REPLAYING…" : "RUN"}
              </button>
            </div>
            {result && (
              <div className="stats" style={{ marginTop: 8 }}>
                <div className="stat">
                  <span className="stat-n">{typeof result.roi_pct === "number" ? pct(result.roi_pct) : "—"}</span>
                  <span className="stat-l">roi · {result.currency ?? ""}</span>
                </div>
                <div className="stat">
                  <span className="stat-n">{result.final_pnl ?? "—"}</span>
                  <span className="stat-l">pnl</span>
                </div>
                <div className="stat">
                  <span className="stat-n">{result.trades_simulated ?? 0}</span>
                  <span className="stat-l">trades</span>
                </div>
              </div>
            )}
            {result?.note && <div className="dim" style={{ marginTop: 6 }}>{result.note}</div>}
            {result?.notes?.map((n: string) => (
              <div key={n} className="mono-small" style={{ marginTop: 4 }}>{n}</div>
            ))}

            {picked.origin === "bridge" && (
              <>
                <div className="label" style={{ marginTop: 14 }}>
                  PLAN — the config {picked.source}&apos;s own live engine takes
                </div>
                <div className="strat-form">
                  {needsEoa && (
                    <input value={eoa} onChange={(e) => setEoa(e.target.value)} placeholder="your master wallet (eoa)" />
                  )}
                  {needsHotkey && (
                    <input value={hotkey} onChange={(e) => setHotkey(e.target.value)} placeholder="your hotkey ss58" />
                  )}
                  <button className="ghost" disabled={busy === "plan"} onClick={runPlan}>
                    {busy === "plan" ? "BUILDING…" : "BUILD PLAN"}
                  </button>
                </div>
                {plan && (
                  <>
                    <div className="dim" style={{ margin: "6px 0" }}>
                      {plan.endpoint} — pure data; starting it stays with the {plan.module} module and its gates.
                    </div>
                    <pre className="mono-small" style={{ whiteSpace: "pre-wrap" }}>
                      {JSON.stringify(plan.body ?? plan, null, 2)}
                    </pre>
                  </>
                )}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
