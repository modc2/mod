"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { backing, type ModBacking } from "@/lib/backing";
import { chain } from "@/lib/chain";
import { fmt } from "@/lib/bloctime";
import { useWallet } from "@/lib/wallet";

// The identity used by the server-key fallback below (a key name held on the
// host). Shared with RegisterPanel so signing once is enough. Quota-safe:
// modc2.com modules share one localStorage origin.
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

/**
 * Back one module with BlocTime.
 *
 * Staking here allocates BLOC the address already holds on-chain — it is a
 * curation signal ("this module is worth your weight"), backed 1:1 by the live
 * balance and withdrawable any time. The primary path is the visitor's own
 * wallet: they sign a plain-English message, the API recovers the signer and
 * checks the balance with the bloctime module. No gas, no custody.
 *
 * To MINT BLOC in the first place you lock NAT in the BlocTime protocol — the
 * /stake console does that, and this panel links there when you're empty.
 */
export default function StakePanel({ name }: { name: string }) {
  const w = useWallet();
  const [info, setInfo] = useState<ModBacking | null>(null);
  const [amount, setAmount] = useState("");
  const [busy, setBusy] = useState<"stake" | "unstake" | "load" | null>(null);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  // Operator fallback: stake with a key the host holds instead of a wallet.
  const [keyMode, setKeyMode] = useState(false);
  const [who, setWho] = useState("");

  const load = useCallback(
    async (address?: string) => {
      setBusy("load");
      try {
        setInfo(await backing.module(name, address || undefined));
      } catch (e) {
        setMsg({ kind: "err", text: String(e instanceof Error ? e.message : e) });
      } finally {
        setBusy(null);
      }
    },
    [name],
  );

  useEffect(() => {
    setWho(chainWho.get());
    load(w.address);
  }, [load, w.address]);

  const mine = Number(info?.my_stake_bloc ?? 0);
  const avail = Number(info?.available_bloc ?? 0);
  const network = info?.network || "testnet";

  // Wallet-signed stake/unstake.
  const act = async (kind: "stake" | "unstake") => {
    const amt = amount.trim();
    if (kind === "stake" && !(parseFloat(amt) > 0)) {
      setMsg({ kind: "err", text: "enter an amount of BLOC to stake" });
      return;
    }
    setBusy(kind);
    setMsg(null);
    try {
      const res = await backing.submit(
        {
          name,
          action: kind,
          amount: kind === "unstake" && !(parseFloat(amt) > 0) ? "all" : amt,
          address: w.address,
          network,
        },
        w.signMessage,
      );
      setMsg({
        kind: "ok",
        text:
          kind === "stake"
            ? `staked ✓ — ${fmt(res.my_stake_bloc)} BLOC of yours now backs ${name}`
            : `unstaked ✓ — ${fmt(res.my_stake_bloc)} BLOC of yours still backs ${name}`,
      });
      setAmount("");
      await load(w.address);
    } catch (e) {
      const text = String(e instanceof Error ? e.message : e);
      setMsg({
        kind: "err",
        text: /user rejected|ACTION_REJECTED|4001/i.test(text)
          ? "cancelled in your wallet"
          : text,
      });
    } finally {
      setBusy(null);
    }
  };

  // Fallback for the host operator: the chain module signs with a named key.
  const actWithKey = async (kind: "stake" | "unstake") => {
    const amt = parseFloat(amount);
    if (kind === "stake" && !(amt > 0)) {
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
              amount: amt > 0 ? amt : undefined,
              key: who.trim(),
            });
      setMsg({
        kind: "ok",
        text: `${kind === "stake" ? "staked" : "unstaked"} ✓ — key ${short(
          res.address,
        )} now backs ${name} with ${fmt(res.my_stake)} BLOC`,
      });
      setAmount("");
      await load(w.address);
    } catch (e) {
      setMsg({ kind: "err", text: String(e instanceof Error ? e.message : e) });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="panel chain-card stake-panel">
      <div className="chain-card-head">
        <h3>stake bloctime</h3>
        <span className="stake-total" title="Total BlocTime backing this module">
          ⧗ {fmt(info?.total_bloc)} BLOC
          {info && info.stakers.length > 0 && (
            <i>
              {" "}
              · {info.stakers.length} staker{info.stakers.length > 1 ? "s" : ""}
            </i>
          )}
        </span>
      </div>
      <p className="reg-note">
        Back <code>{name}</code> with BlocTime you hold — staked BLOC ranks
        modules in the catalog. Backed 1:1 by your on-chain balance,
        withdrawable any time, and signed by your wallet: no gas, no custody.
      </p>

      {!w.address && !keyMode && (
        <div className="stake-connect">
          <button
            className="btn btn-primary"
            onClick={() => w.connect().catch(() => {})}
            disabled={w.connecting}
          >
            {w.connecting ? "connecting…" : "connect wallet to stake"}
          </button>
          <button className="linkish" onClick={() => setKeyMode(true)}>
            use a server-held key instead
          </button>
          {w.error && <div className="reg-err">{w.error}</div>}
        </div>
      )}

      {w.address && (
        <>
          <div className="stake-stats">
            <div className="stake-stat">
              <span className="k">your stake</span>
              <span className="v">{fmt(mine)}</span>
            </div>
            <div className="stake-stat">
              <span className="k">free to stake</span>
              <span className="v">{info?.balance_available ? fmt(avail) : "—"}</span>
            </div>
            <div className="stake-stat">
              <span className="k">BLOC held</span>
              <span className="v">
                {info?.balance_available ? fmt(info?.bloc_balance_bloc) : "—"}
              </span>
            </div>
          </div>

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
              disabled={busy !== null || avail <= 0}
              onClick={() => act("stake")}
              title={
                avail > 0
                  ? "sign a message to back this module"
                  : "no free BlocTime — stake NAT in the protocol first"
              }
            >
              {busy === "stake" ? "signing…" : "⧗ Stake"}
            </button>
            <button
              className="btn btn-ghost"
              disabled={busy !== null || mine <= 0}
              onClick={() => act("unstake")}
              title="Unstake the amount above (empty = withdraw everything)"
            >
              {busy === "unstake" ? "…" : "Unstake"}
            </button>
          </div>

          {info && !info.balance_available && (
            <div className="reg-fineprint">
              couldn&apos;t read your BLOC balance — the bloctime module may be
              waking up. Reload in a moment.
            </div>
          )}
          {info?.balance_available && avail <= 0 && mine <= 0 && (
            <div className="reg-fineprint">
              no free BlocTime on <code>{short(w.address)}</code> —{" "}
              <Link href="/stake">lock NAT in the BlocTime protocol</Link> to
              mint some first.
            </div>
          )}
        </>
      )}

      {keyMode && (
        <label className="reg-field">
          <span>server-held key</span>
          <div className="stake-who">
            <input
              value={who}
              onChange={(e) => setWho(e.target.value)}
              placeholder="key name on this host"
              spellCheck={false}
              autoComplete="off"
            />
            <button
              className="chip-toggle"
              disabled={busy !== null || !who.trim()}
              onClick={() => actWithKey("stake")}
            >
              {busy === "stake" ? "…" : "stake"}
            </button>
            <button
              className="chip-toggle"
              disabled={busy !== null || !who.trim()}
              onClick={() => actWithKey("unstake")}
            >
              unstake
            </button>
          </div>
        </label>
      )}

      {msg && (
        <div className={msg.kind === "ok" ? "stake-ok" : "reg-err"}>{msg.text}</div>
      )}

      {info && info.stakers.length > 0 && (
        <div className="stake-book">
          {info.stakers.slice(0, 5).map((s) => (
            <div
              className={`stake-book-row${
                s.address === w.address ? " mine" : ""
              }`}
              key={`${s.via}-${s.address}`}
              title={`${s.address} · ${s.via === "wallet" ? "wallet-signed" : "server key"}`}
            >
              <span className="mono">{short(s.address)}</span>
              <span className="stake-book-bar">
                <i
                  style={{
                    width: `${Math.max(
                      4,
                      (s.bloc / (info.total_bloc || 1)) * 100,
                    )}%`,
                  }}
                />
              </span>
              <b>{fmt(s.bloc)}</b>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
