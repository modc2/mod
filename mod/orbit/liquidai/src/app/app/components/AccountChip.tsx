"use client";

// The cap on the right of the rail: who you are, or the way in. One button —
// a signed-out console that shows a whole login form in the header is a
// console that spends its widest row on its least-used control.

import { useState } from "react";
import { useAuth } from "../context/AuthContext";
import { shortAddress } from "../lib/wallets";
import SignIn from "./SignIn";

const GLYPH: Record<string, string> = {
  browser: "▣", evm: "◈", bittensor: "τ", cli: "❯",
};

export default function AccountChip() {
  const { session } = useAuth();
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className={`pixel-btn topbar-ctl px-3 gap-2 shrink-0 ${session ? "" : "nav-active"}`}
        title={session ? session.address : "sign in with a browser key, MetaMask or a Bittensor wallet"}
      >
        {session ? (
          <>
            <span className={session.owner ? "text-amber-400" : "text-cyan-400"}>
              {GLYPH[session.kind] ?? "▣"}
            </span>
            <span className="font-mono hidden sm:inline">
              {shortAddress(session.address, 4, 4)}
            </span>
            {session.owner && <span className="hidden md:inline text-amber-400">★</span>}
          </>
        ) : (
          <>
            <span aria-hidden>⌁</span>
            <span className="hidden sm:inline">SIGN IN</span>
          </>
        )}
      </button>

      {open && <SignIn onClose={() => setOpen(false)} />}
    </>
  );
}
