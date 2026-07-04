"use client";
import { useAuth } from "../context/AuthContext";
import { shortAddress } from "../lib/wallet";

export default function WalletChip() {
  const { auth, hasWallet, loading, error, signIn, signOut } = useAuth();

  if (!auth.gated) {
    return (
      <span className="text-[10px] text-white/30" title="No owner wallet configured — every endpoint is open">
        open
      </span>
    );
  }

  if (auth.connected && auth.address) {
    return (
      <div className="flex items-center gap-2">
        <span
          className={`text-[11px] mono-num ${auth.isOwner ? "text-emerald-300" : "text-amber-400"}`}
          title={
            auth.isOwner
              ? "signed in as owner"
              : "signed in — gated actions succeed if this wallet is the owner or holds BlocTime on-chain"
          }
        >
          {auth.isOwner ? "● " : "△ "}
          {shortAddress(auth.address)}
        </span>
        <button className="btn text-[10px]" onClick={signOut}>
          sign out
        </button>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <button
        className="btn-primary text-[11px]"
        onClick={signIn}
        disabled={!hasWallet || loading}
        title={hasWallet ? "Connect MetaMask to authorize training/inference" : "Install a wallet extension"}
      >
        {loading ? "..." : hasWallet ? "connect wallet" : "no wallet"}
      </button>
      {error && <span className="text-[10px] text-red-400">{error}</span>}
    </div>
  );
}
