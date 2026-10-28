"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import type { PortfolioPlan } from "../lib/types";
import { portfolioPlan, portfolioSync, shortSs58, updateCopy } from "../lib/api";
import { useCurrency, fmtValue } from "../context/CurrencyContext";

/**
 * AllocationBook — every trader you copy, the τ behind each, and the single
 * book they blend into.
 *
 * Copies do not each own a portfolio. Each one contributes its trader's SHAPE
 * at its own SIZE, and the server diffs the blend against what we hold, once.
 * This panel is the receipt for that: change a sleeve here and you can see,
 * before anything is signed, exactly which subnets move and whose money moved
 * them.
 *
 * The plan shown is the same object the engine executes — GET /portfolio and
 * POST /portfolio/sync run the identical code — so the preview cannot drift
 * from the thing it previews.
 */
export default function AllocationBook() {
  const { currency, usdPerTao } = useCurrency();
  const [plan, setPlan] = useState<PortfolioPlan | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [busy, setBusy] = useState(false);
  // Sleeve edits are held locally until committed — re-planning on every
  // keystroke would fire a chain read per digit.
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [showBook, setShowBook] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setPlan(await portfolioPlan());
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function commit(copyId: string) {
    const raw = drafts[copyId];
    if (raw === undefined) return;
    const tao = Number(raw);
    if (!Number.isFinite(tao) || tao < 0) {
      setError("allocation must be a number ≥ 0");
      return;
    }
    setBusy(true);
    try {
      await updateCopy(copyId, { alloc_tao: tao });
      setDrafts((d) => { const n = { ...d }; delete n[copyId]; return n; });
      setInfo(`re-sized to ${tao}τ — the next pass rebalances to it`);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function apply() {
    if (!plan) return;
    if (
      plan.trades > 0 &&
      !confirm(
        `Send ${plan.trades} transaction(s) now?\n\n` +
          plan.rows
            .filter((r) => r.action !== "hold")
            .map((r) => `${r.action.toUpperCase()} ${r.amount_tao.toFixed(4)}τ  SN${r.netuid}`)
            .join("\n"),
      )
    )
      return;
    setBusy(true);
    try {
      const res = await portfolioSync(false);
      setPlan(res);
      const failed = res.results.filter((r) => r.status !== "confirmed").length;
      setInfo(
        `${res.results.length} trade(s) sent` +
          (failed ? ` · ${failed} failed` : ""),
      );
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const τ = (v: number) => fmtValue(v, currency, usdPerTao);

  if (loading && !plan)
    return <p className="text-pixel-gray text-sm">reading the book…</p>;

  const movers = plan?.rows.filter((r) => r.action !== "hold") ?? [];
  const nameOf = (copyId: string) => {
    const s = plan?.sleeves.find((x) => x.copy_id === copyId);
    return s ? s.label || shortSs58(s.target_ss58) : copyId.slice(0, 6);
  };

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-display text-lg font-bold">Allocation book</h2>
        <div className="flex gap-1">
          <button
            className="pixel-btn text-[10px] px-2 py-0.5"
            onClick={load}
            disabled={busy}
          >
            REFRESH
          </button>
          <button
            className="pixel-btn text-[10px] px-2 py-0.5 border-green-400 text-green-400 disabled:opacity-40"
            onClick={apply}
            disabled={busy || !plan || plan.trades === 0 || !!plan.blocked}
            title={
              plan?.blocked
                ? plan.blocked
                : plan?.trades
                  ? "Send the trades below now"
                  : "Nothing to do — the book already matches"
            }
          >
            {busy ? "WORKING…" : `APPLY NOW${plan?.trades ? ` (${plan.trades})` : ""}`}
          </button>
        </div>
      </div>

      {!plan || plan.sleeves.length === 0 ? (
        <div className="pixel-panel p-6">
          <p className="arcade-prose">
            No traders allocated yet. Build a basket in the STRAT MAKER, or
            start a single copy below — each one gets its own τ and they blend
            into one book.
          </p>
        </div>
      ) : (
        <>
          {/* What the wallet can actually back, versus what was asked for. */}
          <div className="pixel-panel p-3 grid grid-cols-2 md:grid-cols-4 gap-3 font-mono text-sm">
            <div>
              <div className="text-[10px] uppercase tracking-[2px] text-pixel-gray">
                Allocated
              </div>
              {τ(plan.requested_tao)}
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-[2px] text-pixel-gray">
                Wallet backs
              </div>
              <span className={plan.scale < 1 ? "text-amber-400" : ""}>
                {τ(plan.deployable_tao)}
              </span>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-[2px] text-pixel-gray">
                Staked · free
              </div>
              {τ(plan.staked_tao)} · {τ(plan.free_tao)}
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-[2px] text-pixel-gray">
                Pending moves
              </div>
              <span className={plan.trades ? "text-green-400" : "text-pixel-gray"}>
                {plan.trades} · ±{plan.band_tao.toFixed(3)}τ band
              </span>
            </div>
          </div>

          {plan.blocked && (
            <div className="pixel-panel-red px-3 py-2 text-[12px] text-amber-400 font-mono">
              {plan.blocked}
            </div>
          )}

          {/* The sleeves — this is where you decide the money. */}
          <div className="pixel-panel overflow-hidden">
            <table className="pixel-table">
              <thead className="sticky">
                <tr>
                  <th>Trader</th>
                  <th className="num">Allocated τ</th>
                  <th className="num">Effective</th>
                  <th className="num">Share</th>
                  <th className="num">Subnets</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {plan.sleeves.map((s) => {
                  const draft = drafts[s.copy_id];
                  const dirty = draft !== undefined && Number(draft) !== s.alloc_tao;
                  return (
                    <tr key={s.copy_id} className={s.stale ? "opacity-60" : ""}>
                      <td>
                        <Link
                          href={`/traders/${s.target_ss58}`}
                          className="font-mono text-pixel-white hover:text-green-400 no-underline"
                          title={s.target_ss58}
                        >
                          {s.label || shortSs58(s.target_ss58)}
                        </Link>
                        {s.note && (
                          <div className="text-[10px] text-amber-400 font-mono">
                            {s.note}
                          </div>
                        )}
                      </td>
                      <td className="num">
                        <input
                          type="number" min="0" step="0.5"
                          value={draft ?? s.alloc_tao}
                          onChange={(e) =>
                            setDrafts((d) => ({ ...d, [s.copy_id]: e.target.value }))
                          }
                          onKeyDown={(e) => {
                            if (e.key === "Enter") commit(s.copy_id);
                          }}
                          className="pixel-input-sm w-20 text-right font-mono"
                          title="The TAO behind this trader. Enter to apply."
                        />
                      </td>
                      <td
                        className="num font-mono text-pixel-gray-light"
                        title={
                          plan.scale < 1
                            ? `Scaled to ${(plan.scale * 100).toFixed(1)}% — the sleeves ask for more than the wallet holds`
                            : undefined
                        }
                      >
                        {τ(s.effective_tao)}
                      </td>
                      <td className="num font-mono">{s.pct_of_book.toFixed(1)}%</td>
                      <td className="num font-mono text-pixel-gray-light">
                        {s.subnets}
                      </td>
                      <td>
                        {dirty && (
                          <button
                            className="pixel-btn text-[10px] px-2 py-0.5 border-green-400 text-green-400"
                            onClick={() => commit(s.copy_id)}
                            disabled={busy}
                          >
                            SET
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* The blend, and the trades that would close the gap. */}
          <div>
            <button
              className="pixel-btn text-[10px] px-2 py-0.5 mb-2"
              onClick={() => setShowBook((v) => !v)}
            >
              {showBook ? "HIDE" : "SHOW"} TARGET BOOK ({plan.rows.length})
            </button>
            {showBook && plan.rows.length > 0 && (
              <div className="pixel-panel overflow-hidden">
                <table className="pixel-table">
                  <thead className="sticky">
                    <tr>
                      <th>Subnet</th>
                      <th className="num">Target</th>
                      <th className="num">Held</th>
                      <th className="num">Move</th>
                      <th>Who wants it</th>
                    </tr>
                  </thead>
                  <tbody>
                    {plan.rows.map((r) => (
                      <tr key={r.netuid} title={r.reason}>
                        <td className="font-mono">
                          SN{r.netuid}{" "}
                          <span className="text-pixel-gray">{r.subnet_name}</span>
                        </td>
                        <td className="num font-mono">{τ(r.desired_tao)}</td>
                        <td className="num font-mono text-pixel-gray-light">
                          {τ(r.current_tao)}
                        </td>
                        <td
                          className={`num font-mono ${
                            r.action === "stake"
                              ? "text-green-400"
                              : r.action === "unstake"
                                ? "text-red-400"
                                : "text-pixel-gray"
                          }`}
                        >
                          {r.action === "hold"
                            ? "—"
                            : `${r.action === "stake" ? "+" : "−"}${r.amount_tao.toFixed(4)}τ`}
                        </td>
                        <td className="text-[11px] font-mono text-pixel-gray-light">
                          {Object.entries(r.contributors).length === 0
                            ? "—"
                            : Object.entries(r.contributors)
                                .sort((a, b) => b[1] - a[1])
                                .map(([id, v]) => `${nameOf(id)} ${v.toFixed(2)}τ`)
                                .join(" · ")}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {plan.notes.length > 0 && (
            <ul className="text-[11px] font-mono text-pixel-gray-light space-y-0.5">
              {plan.notes.map((n, i) => (
                <li key={i}>· {n}</li>
              ))}
            </ul>
          )}

          {plan.executed && plan.results.length > 0 && (
            <div className="pixel-panel p-3 text-[11px] font-mono space-y-0.5">
              {plan.results.map((r, i) => (
                <div
                  key={i}
                  className={r.status === "confirmed" ? "text-green-400" : "text-red-400"}
                >
                  {r.action} {r.amount_tao.toFixed(4)}τ SN{r.netuid} — {r.status}
                  {r.error ? ` · ${r.error}` : ""}
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {movers.length > 0 && !plan?.blocked && (
        <p className="text-[11px] font-mono text-pixel-gray">
          The loop applies these on its own schedule; APPLY NOW just doesn't
          wait for it.
        </p>
      )}

      {error && (
        <div className="pixel-panel-red px-3 py-2 text-[12px] text-red-400 font-mono break-all">
          {error}
        </div>
      )}
      {info && (
        <div className="px-1 text-[12px] text-green-400 font-mono">{info}</div>
      )}
    </section>
  );
}
