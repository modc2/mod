"use client";

// The BlocTime protocol console.
//
// This is the real thing, not a ledger entry: lock NAT in the BlocTime
// contract on Base Sepolia for a number of blocks and it mints BLOC at a
// multiplier that grows with the lock (1× at zero blocks up to 3× at the
// maximum). BLOC is what backs modules in the catalog and what the weekly
// reward pot is split by.
//
// Reads come from the bloctime module (it holds the RPC and the ABI); every
// write is a transaction signed by the visitor's own wallet — approve, then
// stake, then unstake once the lock has run out.

import Link from "next/link";
import { parseUnits } from "ethers";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  bloctime,
  fmt,
  fromWei,
  lockDuration,
  type Contracts,
  type Overview,
  type Params,
  type ProtocolStats,
} from "@/lib/bloctime";
import {
  BLOCTIME_ABI,
  DEFAULT_CHAIN,
  ERC20_ABI,
  contractAt,
  useWallet,
} from "@/lib/wallet";
import { gatewayUrl } from "@/lib/api";
import { Nav, Footer } from "../components/Chrome";

type Msg = { kind: "ok" | "err" | "wait"; text: string; tx?: string } | null;

/** Lock presets, in blocks (Base produces one roughly every 2 seconds). */
const PRESETS = [
  { label: "no lock", blocks: 0 },
  { label: "~1 day", blocks: 43200 },
  { label: "~2 days", blocks: 86400 },
  { label: "max", blocks: -1 },
];

export default function StakeConsole() {
  const w = useWallet();
  const [contracts, setContracts] = useState<Contracts | null>(null);
  const [params, setParams] = useState<Params | null>(null);
  const [stats, setStats] = useState<ProtocolStats | null>(null);
  const [me, setMe] = useState<Overview | null>(null);
  const [nat, setNat] = useState<string>("0");
  const [allowance, setAllowance] = useState<string>("0");
  const [amount, setAmount] = useState("");
  const [lock, setLock] = useState(0);
  const [mult, setMult] = useState<number | null>(1);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<Msg>(null);
  const [err, setErr] = useState("");

  const bt = useMemo(
    () => contracts?.contracts.find((c) => c.name === "BlocTime"),
    [contracts],
  );
  const natC = useMemo(
    () => contracts?.contracts.find((c) => c.name === "NativeToken"),
    [contracts],
  );
  const maxLock = params?.maxLockBlocks ?? 100000;

  // Protocol-level facts: contracts, curve params, live totals.
  useEffect(() => {
    let alive = true;
    bloctime.contracts().then((c) => alive && setContracts(c)).catch((e) => alive && setErr(String(e)));
    bloctime.params().then((p) => alive && setParams(p)).catch(() => {});
    bloctime.stats().then((s) => alive && setStats(s)).catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  // The connected address's position, NAT balance and current allowance.
  const refresh = useCallback(async () => {
    if (!w.address) {
      setMe(null);
      setNat("0");
      setAllowance("0");
      return;
    }
    const [ov, bal, allow] = await Promise.allSettled([
      bloctime.overview(w.address),
      bloctime.read("nativeToken", "balanceOf", [w.address]),
      bt
        ? bloctime.read("nativeToken", "allowance", [w.address, bt.address])
        : Promise.reject(new Error("no contract")),
    ]);
    if (ov.status === "fulfilled") setMe(ov.value);
    if (bal.status === "fulfilled") setNat(bal.value.output);
    if (allow.status === "fulfilled") setAllowance(allow.value.output);
  }, [w.address, bt]);

  useEffect(() => {
    refresh().catch(() => {});
  }, [refresh]);

  // The multiplier is read from the contract itself so the projection can't
  // drift from what the stake will actually mint.
  useEffect(() => {
    let alive = true;
    bloctime
      .multiplier(lock)
      .then((m) => alive && setMult(m.multiplierX))
      .catch(() => alive && setMult(null));
    return () => {
      alive = false;
    };
  }, [lock]);

  const amountNum = parseFloat(amount);
  const validAmount = isFinite(amountNum) && amountNum > 0;
  const natHuman = fromWei(nat);
  const needsApproval = validAmount && fromWei(allowance) < amountNum;
  const projected = validAmount && mult ? amountNum * mult : 0;

  const explorerTx = (hash: string) => `${DEFAULT_CHAIN.explorer}/tx/${hash}`;

  /** Guard rails shared by every write: wallet, network, contract. */
  const ready = async () => {
    if (!w.address) {
      await w.connect();
    }
    if (w.wrongNetwork) {
      await w.switchNetwork();
      throw new Error(`switch to ${DEFAULT_CHAIN.name} and try again`);
    }
    if (!bt || !natC) throw new Error("bloctime contracts are not loaded");
    return w.getSigner();
  };

  const approve = async () => {
    setBusy("approve");
    setMsg({ kind: "wait", text: "confirm the approval in your wallet…" });
    try {
      const signer = await ready();
      const token = contractAt(natC!.address, ERC20_ABI, signer);
      const wei = parseUnits(amount.trim(), 18);
      const tx = await token.approve(bt!.address, wei);
      setMsg({ kind: "wait", text: "approval sent — waiting for a block…", tx: tx.hash });
      await tx.wait();
      setMsg({ kind: "ok", text: `approved ${fmt(amountNum)} NAT`, tx: tx.hash });
      await refresh();
    } catch (e) {
      setMsg({ kind: "err", text: reason(e) });
    } finally {
      setBusy(null);
    }
  };

  const stake = async () => {
    if (!validAmount) {
      setMsg({ kind: "err", text: "enter how much NAT to lock" });
      return;
    }
    if (amountNum > natHuman) {
      setMsg({ kind: "err", text: `you hold ${fmt(natHuman)} NAT` });
      return;
    }
    setBusy("stake");
    setMsg({ kind: "wait", text: "confirm the stake in your wallet…" });
    try {
      const signer = await ready();
      const c = contractAt(bt!.address, BLOCTIME_ABI, signer);
      const wei = parseUnits(amount.trim(), 18);
      const tx = await c.stake(wei, lock);
      setMsg({ kind: "wait", text: "stake sent — waiting for a block…", tx: tx.hash });
      await tx.wait();
      setMsg({
        kind: "ok",
        text: `staked ${fmt(amountNum)} NAT for ${lock.toLocaleString()} blocks → ${fmt(
          projected,
        )} BLOC minted`,
        tx: tx.hash,
      });
      setAmount("");
      await refresh();
    } catch (e) {
      setMsg({ kind: "err", text: reason(e) });
    } finally {
      setBusy(null);
    }
  };

  const unstake = async (stakeId: number) => {
    setBusy(`unstake-${stakeId}`);
    setMsg({ kind: "wait", text: "confirm the unstake in your wallet…" });
    try {
      const signer = await ready();
      const c = contractAt(bt!.address, BLOCTIME_ABI, signer);
      const tx = await c.unstake(stakeId);
      setMsg({ kind: "wait", text: "unstake sent — waiting for a block…", tx: tx.hash });
      await tx.wait();
      setMsg({ kind: "ok", text: `position #${stakeId} withdrawn`, tx: tx.hash });
      await refresh();
    } catch (e) {
      setMsg({ kind: "err", text: reason(e) });
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <Nav />
      <main className="wrap">
        <Link href="/" className="back">
          ← all modules
        </Link>

        <div className="stake-hero">
          <div>
            <h1>⧗ BlocTime</h1>
            <p>
              Lock NAT for a stretch of blocks and mint BLOC — time-weighted
              stake, up to 3× for the longest lock. BLOC is the weight that
              backs modules in this catalog and the share you're paid by when
              the weekly pot pays out.
            </p>
          </div>
          <div className="stake-hero-stats">
            <div className="stat">
              <span className="k">BLOC in existence</span>
              <span className="v">{fmt(stats?.totalSupply)}</span>
            </div>
            <div className="stat">
              <span className="k">positions</span>
              <span className="v">{stats?.totalStakes ?? "—"}</span>
            </div>
            <div className="stat">
              <span className="k">network</span>
              <span className="v">{contracts?.network ?? stats?.network ?? "—"}</span>
            </div>
          </div>
        </div>

        {err && <div className="chain-note">bloctime module unreachable — {err}</div>}

        {!w.address && (
          <div className="panel connect-card">
            <h3>connect a wallet to stake</h3>
            <p className="reg-note">
              Staking is signed by your wallet on {DEFAULT_CHAIN.name}; nothing
              is custodied here and no key of yours ever reaches this server.
            </p>
            <button
              className="btn btn-primary"
              onClick={() => w.connect().catch(() => {})}
              disabled={w.connecting}
            >
              {w.connecting ? "connecting…" : "connect wallet"}
            </button>
            {!w.hasWallet && (
              <div className="reg-fineprint">
                no wallet detected — install MetaMask, or any EIP-1193 wallet,
                and reload.
              </div>
            )}
            {w.error && <div className="reg-err">{w.error}</div>}
          </div>
        )}

        {w.address && (
          <>
            {w.wrongNetwork && (
              <div className="chain-note warn">
                your wallet is on chain {w.chainId} — the BlocTime contracts
                live on {DEFAULT_CHAIN.name}.{" "}
                <button className="chip-toggle" onClick={() => w.switchNetwork()}>
                  switch network
                </button>
              </div>
            )}

            <div className="stake-grid">
              <div className="panel chain-card">
                <div className="chain-card-head">
                  <h3>stake NAT</h3>
                  <span className="stake-total">
                    balance {fmt(nat)} NAT
                  </span>
                </div>

                <label className="reg-field">
                  <span>amount</span>
                  <div className="stake-who">
                    <input
                      value={amount}
                      onChange={(e) => setAmount(e.target.value)}
                      placeholder="0.0"
                      inputMode="decimal"
                      spellCheck={false}
                      autoComplete="off"
                    />
                    <button
                      className="chip-toggle"
                      onClick={() => setAmount(String(natHuman))}
                      disabled={!natHuman}
                    >
                      max
                    </button>
                  </div>
                </label>

                <label className="reg-field">
                  <span>
                    lock · {lock.toLocaleString()} blocks
                    <i className="lock-dur"> ({lockDuration(lock)})</i>
                  </span>
                  <input
                    type="range"
                    className="lock-range"
                    min={0}
                    max={maxLock}
                    step={Math.max(1, Math.round(maxLock / 200))}
                    value={lock}
                    onChange={(e) => setLock(Number(e.target.value))}
                  />
                </label>

                <div className="preset-row">
                  {PRESETS.map((p) => {
                    const blocks = p.blocks < 0 ? maxLock : Math.min(p.blocks, maxLock);
                    return (
                      <button
                        key={p.label}
                        className={`chip-toggle${lock === blocks ? " active" : ""}`}
                        onClick={() => setLock(blocks)}
                      >
                        {p.label}
                      </button>
                    );
                  })}
                </div>

                <div className="stake-stats">
                  <div className="stake-stat">
                    <span className="k">multiplier</span>
                    <span className="v">{mult === null ? "—" : `${mult}×`}</span>
                  </div>
                  <div className="stake-stat">
                    <span className="k">you'd mint</span>
                    <span className="v">{fmt(projected)} BLOC</span>
                  </div>
                  <div className="stake-stat">
                    <span className="k">approved</span>
                    <span className="v">{fmt(allowance)} NAT</span>
                  </div>
                </div>

                <div className="stake-row">
                  {needsApproval && (
                    <button
                      className="btn btn-ghost"
                      onClick={approve}
                      disabled={busy !== null}
                    >
                      {busy === "approve" ? "approving…" : "1 · Approve NAT"}
                    </button>
                  )}
                  <button
                    className="btn btn-primary"
                    onClick={stake}
                    disabled={busy !== null || !validAmount || needsApproval}
                    title={
                      needsApproval
                        ? "approve the contract to move that much NAT first"
                        : "lock NAT and mint BLOC"
                    }
                  >
                    {busy === "stake"
                      ? "staking…"
                      : needsApproval
                        ? "2 · Stake"
                        : "⧗ Stake"}
                  </button>
                </div>

                {msg && (
                  <div
                    className={
                      msg.kind === "ok"
                        ? "stake-ok"
                        : msg.kind === "err"
                          ? "reg-err"
                          : "stake-wait"
                    }
                  >
                    {msg.text}
                    {msg.tx && (
                      <>
                        {" "}
                        <a href={explorerTx(msg.tx)} target="_blank" rel="noreferrer">
                          tx ↗
                        </a>
                      </>
                    )}
                  </div>
                )}

                <div className="reg-fineprint">
                  Staking is two transactions the first time: an ERC-20 approval
                  so the contract can pull your NAT, then the stake itself. NAT
                  comes back when the lock expires — the BLOC it minted stays
                  until you unstake.
                </div>
              </div>

              <div className="panel chain-card">
                <div className="chain-card-head">
                  <h3>your positions</h3>
                  <span className="stake-total">
                    ⧗ {fmt(me?.blocBalance)} BLOC
                  </span>
                </div>

                {!me || me.positions.length === 0 ? (
                  <p className="reg-note">
                    nothing staked yet — lock NAT on the left and this fills in.
                  </p>
                ) : (
                  <div className="pos-list">
                    {me.positions.map((p) => (
                      <div className="pos-row" key={p.stakeId}>
                        <div className="pos-main">
                          <b>{fmt(p.amount)} NAT</b>
                          <span className="pos-meta">
                            #{p.stakeId} · {p.lockBlocks.toLocaleString()} blocks ·{" "}
                            {fmt(p.blocTimeBalance)} BLOC
                          </span>
                        </div>
                        <div className="pos-actions">
                          <span
                            className={`pos-lock${p.blocksRemaining > 0 ? " locked" : ""}`}
                          >
                            {p.blocksRemaining > 0
                              ? `${p.blocksRemaining.toLocaleString()} blocks left`
                              : "unlocked"}
                          </span>
                          <button
                            className="btn btn-ghost"
                            disabled={busy !== null || p.blocksRemaining > 0}
                            onClick={() => unstake(p.stakeId)}
                            title={
                              p.blocksRemaining > 0
                                ? "still locked — the contract will revert"
                                : "withdraw the NAT and burn its BLOC"
                            }
                          >
                            {busy === `unstake-${p.stakeId}` ? "…" : "Unstake"}
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                <div className="stake-stats">
                  <div className="stake-stat">
                    <span className="k">NAT locked</span>
                    <span className="v">{fmt(me?.totalStaked)}</span>
                  </div>
                  <div className="stake-stat">
                    <span className="k">BLOC weight</span>
                    <span className="v">{fmt(me?.totalBlocTime)}</span>
                  </div>
                  <div className="stake-stat">
                    <span className="k">positions</span>
                    <span className="v">{me?.stakeCount ?? 0}</span>
                  </div>
                </div>

                <div className="reg-fineprint">
                  Holding BLOC is what lets you back modules: open any module and
                  stake your weight behind it in the ⛓ on-chain panel.
                </div>
              </div>
            </div>

            {bt && (
              <div className="panel">
                <h3>contracts</h3>
                <div className="fn-list">
                  {contracts?.contracts
                    .filter((c) => c.name === "BlocTime" || c.name === "NativeToken")
                    .map((c) => (
                      <a
                        className="fn-chip"
                        key={c.address}
                        href={`${DEFAULT_CHAIN.explorer}/address/${c.address}`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        {c.name} · {c.address.slice(0, 8)}… ↗
                      </a>
                    ))}
                  <a
                    className="fn-chip"
                    href={gatewayUrl("bloctime")}
                    target="_blank"
                    rel="noreferrer"
                  >
                    bloctime module ↗
                  </a>
                </div>
              </div>
            )}
          </>
        )}
      </main>
      <Footer />
    </>
  );
}

/** Wallet errors are nested and noisy — dig out the sentence worth showing. */
function reason(e: unknown): string {
  const err = e as {
    code?: number | string;
    shortMessage?: string;
    reason?: string;
    info?: { error?: { message?: string } };
    message?: string;
  };
  if (err?.code === 4001 || err?.code === "ACTION_REJECTED") return "cancelled in your wallet";
  return (
    err?.reason ||
    err?.shortMessage ||
    err?.info?.error?.message ||
    err?.message ||
    String(e)
  );
}
