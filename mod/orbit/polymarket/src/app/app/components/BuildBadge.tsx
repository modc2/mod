"use client";

import { useEffect, useState } from "react";
import { useEmbedded } from "../lib/embedded";
import { useAuth } from "../context/AuthContext";
import { ensureChain, NETWORKS } from "../lib/networks";

const POLYGON = NETWORKS.find((n) => n.id === "polygon")!;
const STORAGE_KEY = "polymarket:build_tx";
const CID_TAG = "mod-cid:";

type StoredPin = { cid: string; tx: string; from: string; at: number };

function toHex(s: string): string {
  let out = "0x";
  for (let i = 0; i < s.length; i++) {
    out += s.charCodeAt(i).toString(16).padStart(2, "0");
  }
  return out;
}

/// Fixed bottom-right badge showing the content hash of this build, with a
/// browser-side "publish onchain" path that prompts the connected wallet to
/// send a Polygon self-tx whose calldata = `mod-cid:<CID>`. Once published,
/// the tx hash is cached in localStorage and surfaced as a 🔗 link to
/// Polygonscan.
///
/// `NEXT_PUBLIC_BUILD_CID` is set by `docker-entrypoint.dev.sh`. Preference
/// order: localfs/IPFS CID via `m polymarket/build_cid` (Qm…), otherwise a
/// deterministic `sha256:<64-hex>` manifest. `NEXT_PUBLIC_BUILD_TX` (set
/// from `config.json#build_onchain.tx_hash`) takes precedence over the
/// per-browser localStorage record.
export default function BuildBadge() {
  const [copied, setCopied] = useState(false);
  const [pin, setPin] = useState<StoredPin | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const embedded = useEmbedded();
  const { auth } = useAuth();

  const cid = process.env.NEXT_PUBLIC_BUILD_CID || "dev";
  const builtAt = process.env.NEXT_PUBLIC_BUILD_TIME || "";
  const envTx = process.env.NEXT_PUBLIC_BUILD_TX || "";
  const envScan = process.env.NEXT_PUBLIC_BUILD_SCAN || "";

  // Hydrate cached publish for this exact CID. Stale pins (different CID)
  // are silently dropped so the "publish" button reappears after a rebuild.
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw) as StoredPin;
      if (parsed.cid === cid) setPin(parsed);
    } catch {}
  }, [cid]);

  if (embedded) return null;

  const tx = envTx || pin?.tx || "";
  const scan = envScan || (tx ? `https://polygonscan.com/tx/${tx}` : "");
  const isLocalfsCid = cid.startsWith("Qm") || cid.startsWith("bafk") || cid.startsWith("bafy");

  const display = cid.startsWith("sha256:")
    ? `sha256:${cid.slice(7, 7 + 12)}...`
    : cid.length > 20
      ? `${cid.slice(0, 12)}...${cid.slice(-6)}`
      : cid;

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(cid);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {}
  };

  const handlePublish = async () => {
    setStatus(null);
    if (typeof window === "undefined" || !window.ethereum) {
      setStatus("NO WALLET");
      return;
    }
    if (!auth.address) {
      setStatus("CONNECT WALLET FIRST");
      return;
    }
    if (cid === "dev" || !cid) {
      setStatus("NO CID TO PIN");
      return;
    }
    setBusy(true);
    try {
      setStatus("SWITCHING TO POLYGON...");
      await ensureChain(window.ethereum as never, POLYGON);
      const ethereum = window.ethereum as {
        request: (a: { method: string; params?: unknown[] }) => Promise<unknown>;
      };
      setStatus("CONFIRM IN WALLET...");
      const txHash = (await ethereum.request({
        method: "eth_sendTransaction",
        params: [{
          from: auth.address,
          to: auth.address,
          value: "0x0",
          data: toHex(CID_TAG + cid),
        }],
      })) as string;
      const record: StoredPin = {
        cid,
        tx: txHash,
        from: auth.address,
        at: Date.now(),
      };
      try {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(record));
      } catch {}
      setPin(record);
      setStatus(`PINNED ${txHash.slice(0, 10)}...`);
      setTimeout(() => setStatus(null), 4000);
    } catch (e: unknown) {
      // Wallet RPC errors are plain objects with {code, message, data}, not
      // Error instances — String(e) on them yields "[object Object]". Pull
      // the message out of whatever shape we got.
      const errObj = e as { message?: string; data?: { message?: string }; reason?: string };
      const msg =
        e instanceof Error ? e.message :
        errObj?.data?.message ||
        errObj?.reason ||
        errObj?.message ||
        (typeof e === "string" ? e : JSON.stringify(e).slice(0, 200));
      setStatus(`ERROR: ${msg.slice(0, 120)}`);
    } finally {
      setBusy(false);
    }
  };

  const tooltip = [
    "Content hash of the served bundle.",
    `Full: ${cid}`,
    builtAt && `Built: ${builtAt}`,
    tx && `Onchain (Polygon): ${tx}`,
    pin && !envTx && `Pinned by ${pin.from.slice(0, 8)}... at ${new Date(pin.at).toISOString()}`,
    "",
    isLocalfsCid
      ? "Verify: m.mod('localfs')().cid(m.content('polymarket', ignore_folders=[...])) — see polymarket/src/mod.py:build_cid."
      : cid.startsWith("sha256:")
        ? "Verify: clone the repo, run the same sha256-over-manifest script in docker-entrypoint.dev.sh."
        : "Verify: re-pin the build output to IPFS and confirm the CID matches.",
    tx
      ? "Click 🔗 to view the onchain pin on Polygonscan."
      : "Click 📌 to pin this CID onchain (sends a 0-value Polygon tx; ~0.01 MATIC gas).",
  ]
    .filter(Boolean)
    .join("\n");

  const btnCls = "pixel-btn text-[12px] px-2 py-1 font-mono border-pixel-border text-pixel-gray bg-pixel-black/80 hover:text-green-400 hover:border-green-400 backdrop-blur-sm";

  return (
    <div className="fixed bottom-2 right-2 z-40 flex flex-col items-end gap-1">
      {status && (
        <div className="pixel-btn text-[12px] px-2 py-0.5 font-mono border-pixel-border text-green-400 bg-pixel-black/80 backdrop-blur-sm">
          {status}
        </div>
      )}
      <div className="flex gap-1">
        <button onClick={handleCopy} title={tooltip} className={btnCls}>
          {copied ? "COPIED" : display}
        </button>
        {tx ? (
          <a href={scan} target="_blank" rel="noopener noreferrer" title={`Onchain pin on Polygon\n${tx}`} className={btnCls}>
            🔗
          </a>
        ) : (
          <button onClick={handlePublish} disabled={busy} title={tooltip} className={btnCls + (busy ? " opacity-50 cursor-wait" : "")}>
            {busy ? "..." : "📌"}
          </button>
        )}
      </div>
    </div>
  );
}
