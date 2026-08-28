"use client";

import { useCallback, useEffect, useState } from "react";

import AccountPanel from "./components/AccountPanel";
import BetPanel from "./components/BetPanel";
import EdgePanel from "./components/EdgePanel";
import InfoPanel from "./components/InfoPanel";
import MarketPanel from "./components/MarketPanel";
import TokensPanel from "./components/TokensPanel";
import VerifyPanel from "./components/VerifyPanel";
import WalletBar from "./components/WalletBar";
import { api, countdown, Round, Wallet } from "./lib/api";
import { PhaseBadge } from "./components/ui";

const TABS = ["market", "bet", "edge", "tokens", "verify", "account", "info"] as const;
type Tab = (typeof TABS)[number];

export default function Page() {
  const [tab, setTab] = useState<Tab>("market");
  const [wallet, setWallet] = useState<Wallet | null>(null);
  const [round, setRound] = useState<Round | null>(null);
  const [status, setStatus] = useState<any>(null);
  const [account, setAccount] = useState<any>(null);
  const [now, setNow] = useState(() => Math.floor(Date.now() / 1000));
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [s, r] = await Promise.all([
        api("/status"),
        api<Round>("/round").catch(() => null),
      ]);
      setStatus(s);
      setRound(r);
      setError(null);
    } catch (e: any) {
      setError(e.message);
    }
  }, []);

  const refreshAccount = useCallback(async () => {
    if (!wallet) {
      setAccount(null);
      return;
    }
    try {
      setAccount(await api(`/account/${wallet.address}`));
    } catch {
      /* an address with no history is not an error */
    }
  }, [wallet]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh]);

  useEffect(() => {
    refreshAccount();
  }, [refreshAccount, round?.phase]);

  // The phase boundaries are timestamps, so the countdown is local and does
  // not need the server to tick for it.
  useEffect(() => {
    const t = setInterval(() => setNow(Math.floor(Date.now() / 1000)), 1000);
    return () => clearInterval(t);
  }, []);

  const reload = useCallback(async () => {
    await refresh();
    await refreshAccount();
  }, [refresh, refreshAccount]);

  const next = round
    ? round.phase === "open"
      ? { label: "bets close in", at: round.reveal_at }
      : round.phase === "reveal"
        ? { label: "reveals close in", at: round.seal_at }
        : round.phase === "sealed"
          ? { label: "settles in", at: round.settle_at }
          : null
    : null;

  return (
    <main className="min-h-screen">
      <div className="max-w-5xl mx-auto px-5 py-6">
        <header className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <div className="text-xl">
              <span className="text-accent">pre</span>rank
            </div>
            <div className="text-[11px] text-muted mt-1 max-w-xl leading-relaxed">
              one round a day on which model finishes first. bets are sealed until
              the reveal, and the house&apos;s margin on your early usage comes back
              to you as a position in the model you used.
            </div>
          </div>
          <WalletBar wallet={wallet} setWallet={setWallet} account={account} status={status} />
        </header>

        <nav className="flex gap-6 mt-6 border-b border-edge overflow-x-auto">
          {TABS.map((t) => (
            <div key={t} className="tab uppercase" data-active={tab === t} onClick={() => setTab(t)}>
              {t}
            </div>
          ))}
        </nav>

        {round && (
          <div className="flex items-center gap-3 mt-4 text-[12px] flex-wrap">
            <span className="text-muted">round</span>
            <span>{round.id}</span>
            <PhaseBadge phase={round.phase} />
            {next && (
              <span className="text-muted">
                {next.label} <span className="text-fg">{countdown(next.at, now)}</span>
              </span>
            )}
            <span className="text-muted">
              {round.commitments} sealed bet{round.commitments === 1 ? "" : "s"}
            </span>
            {status?.head && (
              <span className="mono-dim ml-auto" title="the head of the hash chain">
                head {status.head.slice(0, 12)}…
              </span>
            )}
          </div>
        )}

        {error && (
          <div className="mt-4 text-[12px] text-down">
            the api is not answering: {error}
          </div>
        )}

        <div className="mt-5 pb-16">
          {tab === "market" && <MarketPanel round={round} status={status} now={now} />}
          {tab === "bet" && (
            <BetPanel round={round} wallet={wallet} account={account} now={now} reload={reload} />
          )}
          {tab === "edge" && <EdgePanel account={account} status={status} />}
          {tab === "tokens" && (
            <TokensPanel round={round} wallet={wallet} account={account} reload={reload} />
          )}
          {tab === "verify" && <VerifyPanel round={round} />}
          {tab === "account" && (
            <AccountPanel wallet={wallet} account={account} status={status} round={round} reload={reload} />
          )}
          {tab === "info" && <InfoPanel status={status} />}
        </div>
      </div>
    </main>
  );
}
