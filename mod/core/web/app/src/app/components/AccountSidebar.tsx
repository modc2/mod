"use client";

// The account sidebar the header pill opens: a full-height panel docked to the
// right edge with two tabs.
//
//   AGENT  — ask anything about mod. Matching modules land instantly (catalog
//            search); a grounded written answer follows from the docs module.
//   WALLET — who you are, which network, what you hold and have staked, and
//            the one-click fix when the wallet is on the wrong chain.
//
// Rendered through a portal: the nav has backdrop-filter, which would turn a
// fixed child into one positioned against the nav instead of the viewport.

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { gatewayUrl } from "@/lib/api";
import { askDocs, findModules, loadChat, saveChat, type ChatMsg } from "@/lib/agent";
import { fmt, lockDuration, type Overview } from "@/lib/bloctime";
import { DEFAULT_CHAIN, useWallet } from "@/lib/wallet";
import { Icon } from "./Icon";

export type SideTab = "agent" | "wallet";

const SUGGESTIONS = [
  "What is a module?",
  "How does BlocTime staking work?",
  "How do I build my own mod?",
  "Which apps can I trade with?",
];

/** A stable two-tone gradient per address — a face without an image. */
export function addrGradient(addr: string): string {
  let h = 0;
  for (let i = 2; i < addr.length; i++) h = (h * 33 + addr.charCodeAt(i)) % 3600;
  const a = h % 360;
  return `linear-gradient(135deg, hsl(${a} 75% 62%), hsl(${(a + 70) % 360} 70% 48%))`;
}

function uid() {
  return Math.random().toString(36).slice(2, 10);
}

export default function AccountSidebar({
  open,
  tab,
  onTab,
  onClose,
  overview,
}: {
  open: boolean;
  tab: SideTab;
  onTab: (t: SideTab) => void;
  onClose: () => void;
  overview: Overview | null;
}) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  // A closed panel is off-screen but still in the DOM: inert keeps Tab and
  // screen readers out of it. (React 18 has no typed `inert` prop.)
  const asideRef = useRef<HTMLElement>(null);
  useEffect(() => {
    asideRef.current?.toggleAttribute("inert", !open);
  }, [open, mounted]);

  // Escape closes. Focus moves into the panel on open so keyboard users land
  // somewhere useful (the composer on AGENT).
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!mounted) return null;

  return createPortal(
    <>
      <div
        className={`side-scrim${open ? " open" : ""}`}
        onClick={onClose}
        aria-hidden
      />
      <aside
        className={`side${open ? " open" : ""}`}
        aria-label="account and agent"
        aria-hidden={!open}
        ref={asideRef}
      >
        <SideHead onClose={onClose} overview={overview} />
        <div className="side-tabs" role="tablist">
          {(["agent", "wallet"] as SideTab[]).map((t) => (
            <button
              key={t}
              role="tab"
              aria-selected={tab === t}
              className={`side-tab${tab === t ? " on" : ""}`}
              onClick={() => onTab(t)}
            >
              <Icon name={t === "agent" ? "spark" : "wallet"} />
              {t === "agent" ? "Agent" : "Wallet"}
            </button>
          ))}
          <span className={`side-slider at-${tab}`} aria-hidden />
        </div>
        <div className="side-body">
          {tab === "agent" ? (
            <AgentPane active={open && tab === "agent"} />
          ) : (
            <WalletPane overview={overview} onClose={onClose} />
          )}
        </div>
      </aside>
    </>,
    document.body,
  );
}

// ─── header: identity + network ───────────────────────────────────────────

function SideHead({ onClose, overview }: { onClose: () => void; overview: Overview | null }) {
  const w = useWallet();
  const [copied, setCopied] = useState(false);

  const copy = () => {
    navigator.clipboard?.writeText(w.address).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    });
  };

  return (
    <div className="side-head">
      <div className="side-id">
        {w.address ? (
          <span className="side-avatar" style={{ background: addrGradient(w.address) }} />
        ) : (
          <span className="side-avatar ghost">
            <Icon name="wallet" />
          </span>
        )}
        <div className="side-id-text">
          {w.address ? (
            <>
              <button className="side-addr" onClick={copy} title={`${w.address} — click to copy`}>
                <span className="mono">{w.short}</span>
                <Icon name={copied ? "check" : "copy"} />
              </button>
              <span className={`side-net${w.wrongNetwork ? " warn" : ""}`}>
                <span className="dot" />
                {w.wrongNetwork ? `wrong network · chain ${w.chainId ?? "?"}` : DEFAULT_CHAIN.name}
                {!w.wrongNetwork && overview && (
                  <span className="side-net-bal"> · {fmt(overview.blocBalance)} BLOC</span>
                )}
              </span>
            </>
          ) : (
            <>
              <span className="side-addr guest">Not connected</span>
              <span className="side-net">Ask the agent anything — no wallet needed</span>
            </>
          )}
        </div>
        <button className="side-x" onClick={onClose} aria-label="close sidebar" title="Close (Esc)">
          <Icon name="x" />
        </button>
      </div>

      {w.address && w.wrongNetwork && (
        <div className="side-warn">
          <span>
            Your wallet is on another chain. Staking and backing live on{" "}
            <b>{DEFAULT_CHAIN.name}</b>.
          </span>
          <button className="side-btn primary" onClick={() => w.switchNetwork().catch(() => {})}>
            Switch
          </button>
        </div>
      )}
      {!w.address && (
        <button
          className="side-btn primary wide"
          onClick={() => w.connect().catch(() => {})}
          disabled={w.connecting}
          title={w.hasWallet ? undefined : "Install MetaMask (or any EIP-1193 wallet)"}
        >
          <Icon name="wallet" />
          {w.connecting ? "Connecting…" : w.hasWallet ? "Connect wallet" : "No wallet found — install MetaMask"}
        </button>
      )}
      {w.error && <div className="side-err">{w.error}</div>}
    </div>
  );
}

// ─── AGENT ────────────────────────────────────────────────────────────────

function AgentPane({ active }: { active: boolean }) {
  const [msgs, setMsgs] = useState<ChatMsg[]>([]);
  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const aborts = useRef<AbortController[]>([]);

  useEffect(() => setMsgs(loadChat()), []);
  useEffect(() => saveChat(msgs), [msgs]);
  useEffect(() => () => aborts.current.forEach((a) => a.abort()), []);

  useEffect(() => {
    if (active) setTimeout(() => inputRef.current?.focus(), 220);
  }, [active]);

  // Pin to the newest message. scrollTop on the scroller itself — never
  // scrollIntoView, which also scrolls every ancestor (the whole page).
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [msgs]);

  const patch = useCallback((id: string, p: Partial<ChatMsg>) => {
    setMsgs((cur) => cur.map((m) => (m.id === id ? ({ ...m, ...p } as ChatMsg) : m)));
  }, []);

  const busy = msgs.some((m) => m.role === "agent" && m.pending);

  const send = (text: string) => {
    const q = text.trim();
    if (!q || busy) return;
    const aid = uid();
    setMsgs((cur) => [
      ...cur,
      { id: uid(), role: "user", text: q, at: Date.now() },
      { id: aid, role: "agent", at: Date.now(), pending: true },
    ]);
    setDraft("");

    findModules(q)
      .then((mods) => patch(aid, { mods }))
      .catch(() => {});

    const ac = new AbortController();
    aborts.current.push(ac);
    askDocs(q, ac.signal)
      .then((r) =>
        patch(aid, {
          pending: false,
          answer: r.answer,
          sources: r.sources,
          model: r.model,
          error: r.ok ? undefined : "The docs model is offline — here's where to look instead.",
        }),
      )
      .catch((e) => {
        if (ac.signal.aborted) return;
        patch(aid, { pending: false, error: e?.message || "agent unreachable" });
      });
  };

  const docs = gatewayUrl("docs");

  return (
    <div className="agent">
      <div className="agent-scroll" ref={scrollRef}>
        {msgs.length === 0 ? (
          <div className="agent-empty">
            <span className="agent-orb">
              <Icon name="spark" />
            </span>
            <h3>Ask mod</h3>
            <p>
              Find an app, learn how something works, or figure out what to build next. Answers
              come from the protocol docs and the live module catalog.
            </p>
            <div className="agent-sugs">
              {SUGGESTIONS.map((s) => (
                <button key={s} className="agent-sug" onClick={() => send(s)}>
                  {s}
                  <Icon name="arrow-right" />
                </button>
              ))}
            </div>
          </div>
        ) : (
          msgs.map((m) =>
            m.role === "user" ? (
              <div key={m.id} className="msg user">
                {m.text}
              </div>
            ) : (
              <div key={m.id} className="msg agent">
                {m.mods && m.mods.length > 0 && (
                  <div className="msg-mods">
                    {m.mods.map((mod) => (
                      <Link key={mod.name} href={`/mods/${mod.name}`} className="msg-mod">
                        <span
                          className="msg-mod-ic"
                          style={{ background: mod.color || addrGradient("0x" + mod.name) }}
                        >
                          {(mod.icon || mod.name[0]).slice(0, 1)}
                        </span>
                        <span className="msg-mod-txt">
                          <b>{mod.name}</b>
                          <span>{mod.description}</span>
                        </span>
                        {mod.on_web && <span className="msg-mod-live">live</span>}
                      </Link>
                    ))}
                  </div>
                )}
                {m.answer && <div className="msg-text">{m.answer}</div>}
                {m.pending && (
                  <div className="msg-thinking">
                    <span className="dots">
                      <i />
                      <i />
                      <i />
                    </span>
                    reading the docs — this can take a minute
                  </div>
                )}
                {m.error && <div className="msg-err">{m.error}</div>}
                {m.sources && m.sources.length > 0 && (
                  <div className="msg-src">
                    {m.sources.map((s) => (
                      <a key={s} href={`${docs}/${s}`} target="_blank" rel="noreferrer">
                        <Icon name="book" />
                        {s}
                      </a>
                    ))}
                  </div>
                )}
              </div>
            ),
          )
        )}
      </div>

      <form
        className="agent-compose"
        onSubmit={(e) => {
          e.preventDefault();
          send(draft);
        }}
      >
        <textarea
          ref={inputRef}
          value={draft}
          rows={1}
          placeholder={busy ? "Waiting on the answer…" : "Ask about any app or how mod works…"}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send(draft);
            }
          }}
        />
        <button type="submit" className="agent-send" disabled={!draft.trim() || busy} aria-label="send">
          <Icon name="send" />
        </button>
      </form>
      {msgs.length > 0 && (
        <button
          className="agent-clear"
          onClick={() => {
            aborts.current.forEach((a) => a.abort());
            aborts.current = [];
            setMsgs([]);
          }}
        >
          new chat
        </button>
      )}
    </div>
  );
}

// ─── WALLET ───────────────────────────────────────────────────────────────

function WalletPane({ overview, onClose }: { overview: Overview | null; onClose: () => void }) {
  const w = useWallet();

  if (!w.address) {
    return (
      <div className="wpane">
        <div className="wpane-empty">
          Connect a browser wallet to see your BLOC, stake into BlocTime and back the modules you
          use. Your keys never leave the wallet — this site only asks it to sign.
        </div>
      </div>
    );
  }

  const stats: [string, string][] = [
    ["BLOC held", overview ? fmt(overview.blocBalance) : "—"],
    ["Staked", overview ? fmt(overview.totalStaked) : "—"],
    ["BlocTime", overview ? fmt(overview.totalBlocTime) : "—"],
    ["Rewards", overview ? fmt(overview.pendingRewards) : "—"],
  ];

  return (
    <div className="wpane">
      <div className="wstats">
        {stats.map(([k, v]) => (
          <div key={k} className="wstat">
            <span className="k">{k}</span>
            <span className="v mono">{v}</span>
          </div>
        ))}
      </div>

      {overview && overview.positions.length > 0 && (
        <div className="wsec">
          <div className="wsec-h">Positions · {overview.positions.length}</div>
          {overview.positions.slice(0, 6).map((p) => (
            <div key={p.stakeId} className="wpos">
              <span className="mono">#{p.stakeId}</span>
              <span className="mono">{fmt(p.amount)}</span>
              <span className="wpos-lock">
                {p.blocksRemaining > 0 ? `${lockDuration(p.blocksRemaining)} left` : "unlocked"}
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="wsec">
        <Link href="/stake" className="wlink" onClick={onClose}>
          <Icon name="clock" />
          <span>Stake in BlocTime</span>
          <Icon name="chevron-right" />
        </Link>
        <a
          href={`${DEFAULT_CHAIN.explorer}/address/${w.address}`}
          target="_blank"
          rel="noreferrer"
          className="wlink"
        >
          <Icon name="external" />
          <span>View on BaseScan</span>
          <Icon name="chevron-right" />
        </a>
        <button
          className="wlink danger"
          onClick={() => {
            w.disconnect();
          }}
        >
          <Icon name="x" />
          <span>Disconnect</span>
        </button>
      </div>
    </div>
  );
}
