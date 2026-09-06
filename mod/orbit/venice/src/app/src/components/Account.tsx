"use client";

import { useEffect, useRef, useState } from "react";
import { MeResponse } from "@/lib/api";
import { shortAddress } from "@/lib/wallet";
import Ident from "./Ident";
import Pix from "./Pix";

export type IdKind = "wallet" | "local";
export type Mode = "byok" | "paid";

type Props = {
  address: string;
  idKind: IdKind | null;
  me: MeResponse | null;
  mode: Mode;
  setMode: (m: Mode) => void;
  keyInput: string;
  setKeyInput: (v: string) => void;
  onSaveKey: () => void;
  onRemoveKey: () => void;
  onSignOut: () => void;
  onForget: () => void;
  busy: string | null;
};

/**
 * Who you are, parked in the top-right corner: a sprite + your address, and a
 * menu holding everything that belongs to the account rather than to the
 * conversation — the address itself, how this turn gets paid for, and the two
 * ways to leave (sign out, or erase an anonymous identity outright).
 */
export default function Account({
  address,
  idKind,
  me,
  mode,
  setMode,
  keyInput,
  setKeyInput,
  onSaveKey,
  onRemoveKey,
  onSignOut,
  onForget,
  busy,
}: Props) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 1400);
    return () => clearTimeout(t);
  }, [copied]);

  const anon = idKind === "local";
  // Green only when this identity can actually send a turn — a key on file, or
  // the paid path standing by.
  const ready = !!me?.has_key || !!me?.paid_available;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(address);
      setCopied(true);
    } catch {
      /* clipboard blocked (insecure origin / permission) — the address is
         selectable in the menu either way */
    }
  };

  return (
    <div className="acct" ref={box}>
      <button
        className={`acct-btn${open ? " on" : ""}`}
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        title={`${anon ? "anonymous identity" : "wallet"} — ${address}`}
      >
        <Ident address={address} size={22} />
        <span className="acct-id">
          <span className="acct-addr">{shortAddress(address)}</span>
          <span className="acct-kind">{anon ? "anonymous" : "wallet"}</span>
        </span>
        <span className={`acct-dot${ready ? " ready" : ""}`} aria-hidden="true" />
        <span className="acct-caret" aria-hidden="true"><Pix name="down" size={9} /></span>
      </button>

      {open && (
        <div className="acct-pop" role="menu">
          <div className="acct-head">
            <Ident address={address} size={44} />
            <div className="acct-who">
              <span className={`pill ${anon ? "ok" : "strong"}`}>{anon ? "anonymous" : "wallet"}</span>
              <span className="acct-note">
                {anon ? "keypair minted in this browser" : "signed with your wallet"}
              </span>
            </div>
          </div>

          <button className="ghost acct-copy" onClick={copy} title="copy address">
            <span className="acct-full">{address}</span>
            <Pix name={copied ? "check" : "copy"} size={12} />
          </button>

          <div className="acct-sec">
            <div className="sec-title">Access</div>
            {me?.has_key ? (
              <div className="row" style={{ gap: 8 }}>
                <span className="pill ok"><Pix name="check" size={11} /> your key</span>
                <button className="ghost sm" onClick={onRemoveKey} disabled={!!busy}>Remove</button>
              </div>
            ) : (
              <div className="key-form">
                <input
                  type="password"
                  placeholder="Venice API key (vk-…)"
                  value={keyInput}
                  onChange={(e) => setKeyInput(e.target.value)}
                  disabled={!!busy}
                />
                <button className="primary sm" onClick={onSaveKey} disabled={!keyInput.trim() || !!busy}>
                  Save
                </button>
              </div>
            )}

            {/* Only a real choice is worth a control: with no paid path on this
                deployment the toggle was half-dead, and offering it read as a
                promise the backend can't keep. */}
            {me?.paid_available && (
              <>
                <div className="seg acct-seg">
                  <button
                    className={mode === "byok" ? "active" : ""}
                    onClick={() => setMode("byok")}
                    disabled={!me?.has_key}
                    title={me?.has_key ? "spend your own Venice key" : "add a key first"}
                  >
                    My key
                  </button>
                  <button
                    className={mode === "paid" ? "active" : ""}
                    onClick={() => setMode("paid")}
                    title="pay per turn in USDC"
                  >
                    Pay per turn
                  </button>
                </div>
                <div className="acct-note">
                  {`${me.price} ${me.currency} per turn on ${me.network}`}
                </div>
              </>
            )}
          </div>

          <div className="acct-foot">
            <button className="ghost" onClick={onSignOut}>Sign out</button>
            {anon && (
              <button
                className="ghost danger"
                onClick={onForget}
                disabled={!!busy}
                title="erase the browser-local private key and its conversations"
              >
                Forget identity
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
