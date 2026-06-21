"use client";

/// Sidebar wallet + token pairing panel.
///
/// Shows the connected wallet (CONNECTED · OWNER · address · COPY · SIGN OUT)
/// inline — matches the wallet-chip popover look but lives in the docked
/// sidebar so the user can see everything at once. Below the wallet block
/// we surface the local strat token in full (with COPY / REGEN) and a
/// sign-in URL the user can copy to a phone to share the same session.

import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { shortAddress } from "../lib/auth";
import { getProxyAddress } from "../lib/polymarketProxy";

interface CopyTickState {
  address: boolean;
  token: boolean;
}

export default function WalletTokenPanel() {
  const { auth, hasWallet, connect, disconnect, loading, localToken, generateToken } = useAuth();
  const [copied, setCopied] = useState<CopyTickState>({ address: false, token: false });
  const [revealToken, setRevealToken] = useState(false);
  const [proxy, setProxy] = useState<string | null>(null);
  // Collapsed by default — the header keeps the at-a-glance state (WALLET ·
  // CONNECTED · OWNER) visible while the address / token / pairing rows fold
  // away for a clean, sleek strat view. Persisted across reloads.
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    if (typeof window === "undefined") return true;
    try { return localStorage.getItem("poly_wallettoken_collapsed") !== "false"; } catch { return true; }
  });
  useEffect(() => {
    try { localStorage.setItem("poly_wallettoken_collapsed", String(collapsed)); } catch {}
  }, [collapsed]);

  // Resolve the user's Polymarket proxy address — used purely to confirm the
  // connected EOA owns it (drives the OWNER badge). Local computation, no
  // network calls — falls back to null on any error so the badge just hides.
  useEffect(() => {
    let cancelled = false;
    if (!auth.address) { setProxy(null); return; }
    void getProxyAddress(auth.address)
      .then((p) => { if (!cancelled) setProxy(p); })
      .catch(() => { if (!cancelled) setProxy(null); });
    return () => { cancelled = true; };
  }, [auth.address]);

  // Sign-in URL with the token in the fragment (fragments are never sent in
  // HTTP requests, so the token isn't logged by a proxy or server access log
  // when the phone opens the URL). The page reads `#token=...`, persists it,
  // and the phone shares the same local strat session as the desktop.
  const signinUrl = useMemo(() => {
    if (!localToken) return null;
    if (typeof window === "undefined") return null;
    return `${window.location.origin}/#token=${localToken.token}`;
  }, [localToken]);

  const tap = useCallback((k: keyof CopyTickState) => {
    setCopied((p) => ({ ...p, [k]: true }));
    setTimeout(() => setCopied((p) => ({ ...p, [k]: false })), 1200);
  }, []);

  const copyText = useCallback(async (text: string, key: keyof CopyTickState) => {
    try { await navigator.clipboard.writeText(text); tap(key); } catch {}
  }, [tap]);

  const isOwner = !!(auth.address && proxy);

  return (
    <div className="pixel-panel border-2 border-pixel-border">
      <div
        onClick={() => setCollapsed((c) => !c)}
        role="button"
        aria-expanded={!collapsed}
        title={collapsed ? "Expand wallet & session" : "Collapse"}
        className="px-3 py-1.5 border-b border-pixel-border/60 flex items-center gap-2 bg-pixel-black/40 cursor-pointer select-none hover:bg-pixel-black/60"
      >
        <span className="text-pixel-gray text-[10px] w-2 inline-block shrink-0">
          {collapsed ? "▸" : "▾"}
        </span>
        <div
          className={`w-1.5 h-1.5 shrink-0 ${
            auth.authenticated ? "bg-green-400" : auth.connected ? "bg-amber-400" : "bg-pixel-gray"
          }`}
        />
        <span className="text-[13px] text-pixel-white tracking-[0.18em]">WALLET</span>
        {auth.connected ? (
          <span className="text-[11px] font-mono px-1 py-0 border ml-1 border-green-400/60 text-green-400 bg-green-400/10">
            CONNECTED
          </span>
        ) : (
          <span className="text-[11px] font-mono px-1 py-0 border ml-1 border-pixel-gray/60 text-pixel-gray">
            DISCONNECTED
          </span>
        )}
        {isOwner && (
          <span
            className="text-[11px] font-mono px-1 py-0 border border-amber-400/60 text-amber-400 bg-amber-400/5"
            title={`Owner of Safe proxy ${proxy}`}
          >
            OWNER
          </span>
        )}
        {/* When collapsed, surface the short address on the right so the header
            still tells you who's signed in at a glance. */}
        {collapsed && auth.address && (
          <span className="ml-auto font-mono text-[11px] text-pixel-gray/70 truncate">
            {shortAddress(auth.address)}
          </span>
        )}
      </div>

      {!collapsed && (
      <div className="px-3 py-2 space-y-2 bg-pixel-black/20">
        {/* ── Address row ── */}
        {auth.address ? (
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-pixel-gray tracking-[0.15em] shrink-0">ADDR</span>
            <span
              className="font-mono text-[12px] text-pixel-white truncate flex-1"
              title={auth.address}
            >
              {auth.address}
            </span>
            <button
              onClick={() => { void copyText(auth.address!, "address"); }}
              className="text-[11px] tracking-[0.12em] text-pixel-gray hover:text-green-400 border border-pixel-border/60 hover:border-green-400/60 px-1.5 py-0.5 shrink-0"
              title="Copy full address"
            >
              {copied.address ? "✓" : "COPY"}
            </button>
            <button
              onClick={disconnect}
              className="text-[11px] tracking-[0.12em] text-red-400/80 hover:text-red-400 border border-red-400/40 hover:border-red-400/80 px-1.5 py-0.5 shrink-0"
              title="Disconnect wallet"
            >
              SIGN OUT
            </button>
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <span className="text-[12px] text-pixel-gray flex-1">
              {hasWallet ? "No wallet connected." : "No wallet extension detected."}
            </span>
            <button
              onClick={() => { void connect(); }}
              disabled={!hasWallet || loading}
              className="pixel-btn text-[12px] px-2 py-1 border-green-400 text-green-400 hover:bg-green-400/10 disabled:opacity-30 disabled:cursor-not-allowed"
              title={hasWallet ? "Connect MetaMask" : "Install a wallet extension"}
            >
              {loading ? "..." : "CONNECT"}
            </button>
          </div>
        )}

        {/* Short summary chip — mirrors the look of the dropdown popover so
            the at-a-glance state matches what the wallet chip shows. */}
        {auth.address && (
          <div className="flex items-center gap-2 text-[11px] text-pixel-gray/80 font-mono pl-0.5">
            <span className="opacity-60">{shortAddress(auth.address)}</span>
            <span className="opacity-30">·</span>
            <span className={auth.authenticated ? "text-green-400" : "text-amber-400"}>
              {auth.authenticated ? "CLOB ✓" : "CLOB pending"}
            </span>
            {auth.chainId !== null && (
              <>
                <span className="opacity-30">·</span>
                <span className="opacity-60">chain {auth.chainId}</span>
              </>
            )}
          </div>
        )}

        {/* ── Token row ── */}
        <div className="pt-2 border-t border-pixel-border/40 space-y-1.5">
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-pixel-gray tracking-[0.15em] shrink-0">TOKEN</span>
            {localToken ? (
              <>
                <span
                  className="font-mono text-[12px] text-green-400 truncate flex-1"
                  title={revealToken ? localToken.token : localToken.tokenPreview + "..."}
                >
                  {revealToken ? localToken.token : `${localToken.tokenPreview}…`}
                </span>
                <button
                  onClick={() => setRevealToken((r) => !r)}
                  className="text-[11px] tracking-[0.12em] text-pixel-gray hover:text-pixel-white border border-pixel-border/60 hover:border-pixel-white/60 px-1.5 py-0.5 shrink-0"
                  title={revealToken ? "Hide full token" : "Reveal full token"}
                >
                  {revealToken ? "HIDE" : "SHOW"}
                </button>
                <button
                  onClick={() => { void copyText(localToken.token, "token"); }}
                  className="text-[11px] tracking-[0.12em] text-pixel-gray hover:text-green-400 border border-pixel-border/60 hover:border-green-400/60 px-1.5 py-0.5 shrink-0"
                  title="Copy token to clipboard"
                >
                  {copied.token ? "✓" : "COPY"}
                </button>
                <button
                  onClick={generateToken}
                  className="text-[11px] tracking-[0.12em] text-amber-400 hover:bg-amber-400/10 border border-amber-400/60 px-1.5 py-0.5 shrink-0"
                  title="Generate a new token (invalidates the previous sign-in URL)"
                >
                  REGEN
                </button>
              </>
            ) : (
              <>
                <span className="text-[12px] text-pixel-gray flex-1">No token yet.</span>
                <button
                  onClick={generateToken}
                  className="pixel-btn text-[12px] px-2 py-1 border-green-400 text-green-400 hover:bg-green-400/10"
                  title="Generate a local strat token"
                >
                  GENERATE
                </button>
              </>
            )}
          </div>
          {localToken && (
            <div className="text-[10px] text-pixel-gray/60 font-mono pl-0.5">
              issued {new Date(localToken.issuedAt).toISOString().slice(0, 19).replace("T", " ")}Z
            </div>
          )}
        </div>

        {/* ── Sign-in URL ── */}
        {localToken && signinUrl && (
          <div className="pt-2 border-t border-pixel-border/40 space-y-1.5">
            <div className="flex items-center gap-2">
              <span className="text-[11px] text-pixel-gray tracking-[0.15em] shrink-0">PAIR</span>
              <span className="text-[11px] text-pixel-gray/80 flex-1 leading-snug">
                Open this URL on your phone to share the session. The token
                rides in the URL fragment so it never hits a server log.
              </span>
            </div>
            <div className="space-y-1.5">
              <div className="font-mono text-[10px] text-pixel-gray/80 break-all leading-snug border border-pixel-border/60 bg-pixel-black/50 p-1.5">
                {signinUrl}
              </div>
              <button
                onClick={() => { void copyText(signinUrl, "token"); }}
                className="text-[11px] tracking-[0.12em] text-pixel-gray hover:text-green-400 border border-pixel-border/60 hover:border-green-400/60 px-1.5 py-0.5"
                title="Copy sign-in URL"
              >
                COPY URL
              </button>
            </div>
          </div>
        )}
      </div>
      )}
    </div>
  );
}
