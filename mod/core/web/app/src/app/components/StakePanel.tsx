"use client";

import { useCallback, useEffect, useState } from "react";
import { bloc, chain, type ModStakeInfo } from "@/lib/chain";

// The identity used for chain actions (a server-held key name, or a bare
// address for read-only views). Shared with RegisterPanel so signing once is
// enough. Quota-safe: modc2.com modules share one localStorage origin.
const WHO_KEY = "mod.web.chainkey";
export const chainWho = {
  get(): string {
    if (typeof window === "undefined") return "";
    try {
      return window.localStorage.getItem(WHO_KEY) || "";
    } catch {
      return "";
    }
  },
  set(v: string) {
    try {
      window.localStorage.setItem(WHO_KEY, v);
    } catch {
      /* storage full / blocked — non-fatal */
    }
  },
};

function short(a: string): string {
  return a.length > 12 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a;
}

// Stake BlocTime to one module. Stakes are backed 1:1 by the identity's
// on-chain BlocTime balance (the chain hub enforces balance ≥ total staked),
// so this is real skin-in-the-game curation, reversible any time.
export default function StakePanel({ name }: { name: string }) {
  const [info, setInfo] = useState<ModStakeInfo | null>(null);
  const [who, setWho] = useState("");
  const [amount, setAmount] = useState("");
  const [busy, setBusy] = useState<"stake" | "unstake" | "load" | null>(null);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  const load = useCallback(
    async (w: string) => {
      setBusy("load");
      try {
        setInfo(await chain.modStakeInfo(name, w.trim() || undefined));
        setMsg(null);
      } catch (e) {
        setMsg({ kind: "err", text: String(e instanceof Error ? e.message : e) });
      } finally {
        setBusy(null);
      }
    },
    [name],
  );

  useEffect(() => {
    const w = chainWho.get();
    setWho(w);
    load(w);
  }, [load]);

  const readOnly = who.trim().startsWith("0x");
  const canAct = !!who.trim() && !readOnly;

  const act = async (kind: "stake" | "unstake") => {
    const amt = parseFloat(amount);
    if (kind === "stake" && (!isFinite(amt) || amt <= 0)) {
      setMsg({ kind: "err", text: "enter an amount of BLOC to stake" });
      return;
    }
    setBusy(kind);
    setMsg(null);
    chainWho.set(who.trim());
    try {
      const res =
        kind === "stake"
          ? await chain.stakeMod({ name, amount: amt, key: who.trim() })
          : await chain.unstakeMod({
              name,
              amount: isFinite(amt) && amt > 0 ? amt : undefined,
              key: who.trim(),
            });
      setMsg({
        kind: "ok",
        text:
          kind === "stake"
            ? `staked ✓ — ${bloc(res.my_stake)} BLOC now backing ${name}`
            : `unstaked ✓ — ${bloc(res.my_stake)} BLOC still backing ${name}`,
      });
      setAmount("");
      await load(who);
    } catch (e) {
      setMsg({ kind: "err", text: String(e instanceof Error ? e.message : e) });
    } finally {
      setBusy(null);
    }
  };

  const mine = info?.my_stake ?? 0;
  const avail = info?.available ?? 0;

  return (
    <div className="panel chain-card stake-panel">
      <div className="chain-card-head">
        <h3>stake bloctime</h3>
        <span className="stake-total" title="Total BlocTime staked to this module">
          ⧗ {bloc(info?.total)} BLOC
          {info && info.stakers.length > 0 && (
            <i>
              {" "}
              · {info.stakers.length} staker{info.stakers.length > 1 ? "s" : ""}
            </i>
          )}
        </span>
      </div>
      <p className="reg-note">
        Back <code>{name}</code> with your on-chain BlocTime weight — staked
        BLOC ranks modules in the catalog. Backed 1:1 by your BlocTime balance,
        withdrawable any time.
      </p>

      <label className="reg-field">
        <span>key / address</span>
        <div className="stake-who">
          <input
            value={who}
            onChange={(e) => setWho(e.target.value)}
            placeholder="key name (server-held) or 0x… to view"
            spellCheck={false}
            autoComplete="off"
          />
          <button
            className="chip-toggle"
            disabled={busy !== null}
            onClick={() => {
              chainWho.set(who.trim());
              load(who);
            }}
          >
            {busy === "load" ? "…" : "load"}
          </button>
        </div>
      </label>

      {info?.address && (
        <div className="stake-stats">
          <div className="stake-stat">
            <span className="k">your stake</span>
            <span className="v">{bloc(mine)}</span>
          </div>
          <div className="stake-stat">
            <span className="k">available</span>
            <span className="v">{bloc(avail)}</span>
          </div>
          <div className="stake-stat">
            <span className="k">bloc balance</span>
            <span className="v">{bloc(info.bloctime)}</span>
          </div>
        </div>
      )}

      <div className="stake-row">
        <input
          className="stake-amount"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          placeholder="amount (BLOC)"
          inputMode="decimal"
          spellCheck={false}
          autoComplete="off"
        />
        <button
          className="btn btn-primary"
          disabled={busy !== null || !canAct}
          onClick={() => act("stake")}
        >
          {busy === "stake" ? "staking…" : "⧗ Stake"}
        </button>
        <button
          className="btn btn-ghost"
          disabled={busy !== null || !canAct || mine <= 0}
          onClick={() => act("unstake")}
          title="Unstake the amount above (empty = withdraw everything)"
        >
          {busy === "unstake" ? "…" : "Unstake"}
        </button>
      </div>
      {readOnly && (
        <div className="reg-fineprint">
          address view is read-only — staking signs with a server-held key name.
        </div>
      )}
      {info?.address && avail <= 0 && mine <= 0 && !readOnly && (
        <div className="reg-fineprint">
          no free BlocTime on <code>{short(info.address)}</code> — stake MOD via
          the chain module to earn BLOC first.
        </div>
      )}

      {msg && (
        <div className={msg.kind === "ok" ? "stake-ok" : "reg-err"}>{msg.text}</div>
      )}

      {info && info.stakers.length > 0 && (
        <div className="stake-book">
          {info.stakers.slice(0, 5).map((s) => (
            <div className="stake-book-row" key={s.address} title={s.address}>
              <span className="mono">{short(s.address)}</span>
              <span className="stake-book-bar">
                <i
                  style={{
                    width: `${Math.max(4, (s.amount / (info.total || 1)) * 100)}%`,
                  }}
                />
              </span>
              <b>{bloc(s.amount)}</b>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
