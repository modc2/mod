"use client";

// The sign-in sheet — three doors, one sentence to sign.
//
// The doors are ordered by how much they ask of the visitor: a browser key
// wants nothing, MetaMask wants a click, a Bittensor wallet wants an extension
// and an account picked out of it. Whichever you take, the flow underneath is
// identical, and the panel says so rather than pretending they're different
// products.

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "../context/AuthContext";
import {
  connectBittensor,
  connectBrowser,
  connectEvm,
  findSubstrateExtensions,
  forgetDevice,
  shortAddress,
  substrateAccounts,
  type SubstrateAccount,
} from "../lib/wallets";
import type { OwnerState } from "../lib/types";
import { fetchOwner } from "../lib/api";

const DOORS = [
  {
    kind: "browser" as const,
    glyph: "▣",
    title: "BROWSER",
    sub: "a key this tab makes and keeps",
    note: "No extension, no chain, no install. The key never leaves this device — clear the browser and it's gone.",
    tone: "text-cyan-400 border-cyan-400",
  },
  {
    kind: "evm" as const,
    glyph: "◈",
    title: "METAMASK",
    sub: "sign with your Ethereum account",
    note: "A plain personal_sign. No transaction, no gas, no approval — the address is recovered from the signature.",
    tone: "text-amber-400 border-amber-400",
  },
  {
    kind: "bittensor" as const,
    glyph: "τ",
    title: "BITTENSOR",
    sub: "Talisman · SubWallet · Polkadot{.js}",
    note: "sr25519 over raw bytes, the same key that holds your TAO. Nothing is submitted to a chain.",
    tone: "text-purple-400 border-purple-400",
  },
];

export default function SignIn({ onClose }: { onClose: () => void }) {
  const { session, signIn, signOut, busy, error, clearError } = useAuth();
  const [owner, setOwner] = useState<OwnerState | null>(null);
  const [open, setOpen] = useState<string | null>(null);   // which door expanded
  const [extensions, setExtensions] = useState<string[] | null>(null);
  const [extension, setExtension] = useState("");
  const [accounts, setAccounts] = useState<SubstrateAccount[]>([]);
  const [local, setLocal] = useState<string | null>(null);

  useEffect(() => { fetchOwner().then(setOwner).catch(() => {}); }, [session]);

  // Escape closes — a modal you can only leave with the mouse is a modal that
  // traps whoever opened it by accident.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const fail = (e: unknown) => setLocal(String(e instanceof Error ? e.message : e));

  const go = useCallback(async (connect: () => Promise<any>) => {
    setLocal(null);
    clearError();
    try {
      await signIn(await connect());
      onClose();
    } catch (e) { fail(e); }
  }, [signIn, onClose, clearError]);

  const openBittensor = useCallback(async () => {
    setOpen("bittensor");
    setLocal(null);
    const names = await findSubstrateExtensions();
    setExtensions(names);
    if (!names.length) return;
    const first = names[0];
    setExtension(first);
    try {
      setAccounts(await substrateAccounts(first));
    } catch (e) { fail(e); }
  }, []);

  const why = local || error;

  return (
    <div className="lq-scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="lq-sheet" role="dialog" aria-modal="true" aria-label="sign in">
        <div className="lq-sheet-head">
          <h2 className="font-display text-sm">{session ? "ACCOUNT" : "SIGN IN"}</h2>
          <button onClick={onClose} className="pixel-btn topbar-ctl px-2.5 ml-auto" aria-label="close">
            ✕
          </button>
        </div>

        <div className="lq-sheet-body">
          {session ? (
            <SignedIn owner={owner} onOut={() => { signOut(); onClose(); }} />
          ) : (
            <>
              <p className="font-mono text-sm text-pixel-gray-light leading-snug">
                Reading the catalog needs nothing. Signing in is what lets you spend
                this box&apos;s compute — a run on <b className="text-pixel-white">SERVER</b> or{" "}
                <b className="text-pixel-white">CLOUD</b>, and entries in the arena.
                Runs in your own tab never ask.
              </p>

              {DOORS.map((d) => (
                <div key={d.kind} className="lq-door">
                  <button
                    className="lq-door-head"
                    disabled={busy}
                    onClick={() => {
                      if (d.kind === "browser") go(connectBrowser);
                      else if (d.kind === "evm") go(connectEvm);
                      else openBittensor();
                    }}
                  >
                    <span className={`lq-door-glyph ${d.tone}`}>{d.glyph}</span>
                    <span className="min-w-0">
                      <span className="lq-door-title">{d.title}</span>
                      <span className="lq-door-sub">{d.sub}</span>
                    </span>
                    <span className="font-mono text-pixel-gray ml-auto shrink-0">
                      {busy ? "…" : "▸"}
                    </span>
                  </button>
                  <p className="lq-door-note">{d.note}</p>

                  {d.kind === "bittensor" && open === "bittensor" && (
                    <BittensorPicker
                      extensions={extensions}
                      extension={extension}
                      accounts={accounts}
                      busy={busy}
                      onExtension={async (name) => {
                        setExtension(name);
                        setAccounts([]);
                        try { setAccounts(await substrateAccounts(name)); } catch (e) { fail(e); }
                      }}
                      onPick={(address) => go(() => connectBittensor(extension, address))}
                    />
                  )}
                </div>
              ))}

              {owner && !owner.claimed && (
                <p className="font-mono text-xs text-amber-400 leading-snug">
                  ⚑ nobody has claimed this box yet — the first account to sign in
                  becomes its owner and gets the weights and the key vault.
                </p>
              )}
            </>
          )}

          {why && (
            <p className="font-mono text-sm text-red-400 break-words">{why}</p>
          )}
        </div>
      </div>
    </div>
  );
}

function BittensorPicker({ extensions, extension, accounts, busy, onExtension, onPick }: {
  extensions: string[] | null;
  extension: string;
  accounts: SubstrateAccount[];
  busy: boolean;
  onExtension: (name: string) => void;
  onPick: (address: string) => void;
}) {
  if (extensions === null) {
    return <p className="lq-door-note text-pixel-gray-light">looking for a wallet…</p>;
  }
  if (!extensions.length) {
    return (
      <p className="lq-door-note text-red-400">
        no Polkadot-family extension found. Install Talisman, SubWallet or
        Polkadot{"{.js}"} and reload — or take the browser key above.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-1.5 px-3 pb-3">
      {extensions.length > 1 && (
        <select
          value={extension}
          onChange={(e) => onExtension(e.target.value)}
          className="pixel-input-sm font-mono w-full"
          aria-label="extension"
        >
          {extensions.map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
      )}
      {accounts.map((a) => (
        <button
          key={a.address}
          disabled={busy}
          onClick={() => onPick(a.address)}
          className="pixel-btn topbar-ctl w-full !justify-start gap-2"
        >
          <span className="text-purple-400">τ</span>
          <span className="truncate">{a.name || "account"}</span>
          <span className="font-mono text-xs text-pixel-gray ml-auto">
            {shortAddress(a.address)}
          </span>
        </button>
      ))}
      {!accounts.length && (
        <p className="font-mono text-xs text-pixel-gray-light">
          approve the connection in {extension} to list accounts…
        </p>
      )}
    </div>
  );
}

function SignedIn({ owner, onOut }: { owner: OwnerState | null; onOut: () => void }) {
  const { session } = useAuth();
  if (!session) return null;
  const kindLabel = { browser: "browser key", evm: "MetaMask", bittensor: "Bittensor wallet",
                      cli: "shell on this box" }[session.kind] ?? session.kind;
  return (
    <>
      <div className="stat-tile stat-tile-accent">
        <span className="stat-tile-label">SIGNED IN · {kindLabel}</span>
        <div className="stat-tile-value !text-[22px]">{shortAddress(session.address, 10, 6)}</div>
        <span className="stat-tile-sub">
          {session.owner ? "owner — weights and keys are yours to change"
            : owner?.claimed ? `this box belongs to ${shortAddress(owner.address || "", 8, 4)}`
            : "no owner claimed"}
        </span>
      </div>

      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 font-mono text-sm">
        <dt className="text-pixel-gray">address</dt>
        <dd className="truncate text-right" title={session.address}>{session.address}</dd>
        <dt className="text-pixel-gray">session ends</dt>
        <dd className="text-right">
          {new Date(session.expires * 1000).toLocaleDateString()}
        </dd>
        {owner?.open && (
          <>
            <dt className="text-pixel-gray">gate</dt>
            <dd className="text-right text-amber-400">open (LIQUIDAI_OPEN)</dd>
          </>
        )}
      </dl>

      <div className="grid grid-cols-2 gap-1.5">
        <button onClick={onOut} className="pixel-btn topbar-ctl w-full">SIGN OUT</button>
        {session.kind === "browser" && (
          <button
            onClick={() => { forgetDevice(); onOut(); }}
            className="pixel-btn topbar-ctl w-full text-red-400"
            title="delete this device's key — the next sign-in is a new identity"
          >
            FORGET KEY
          </button>
        )}
      </div>
    </>
  );
}
