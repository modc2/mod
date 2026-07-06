"use client";

import { useState } from "react";

// Fixed bottom-right badge showing the content hash of this build.
// `NEXT_PUBLIC_BUILD_CID` is set by docker-entrypoint.sh (or left as "dev"
// in local dev mode).
export default function BuildBadge() {
  const [copied, setCopied] = useState(false);

  const cid = process.env.NEXT_PUBLIC_BUILD_CID || "dev";
  const builtAt = process.env.NEXT_PUBLIC_BUILD_TIME || "";

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

  const tooltip = [
    "Content hash of the served bundle.",
    `Full: ${cid}`,
    builtAt && `Built: ${builtAt}`,
    "Verify: m.mod('localfs')().cid(m.content('copytensor', ignore_folders=['node_modules','.next','__pycache__','data']))",
  ].filter(Boolean).join("\n");

  const btnCls =
    "pixel-btn text-[12px] px-2 py-1 font-mono border-pixel-border " +
    "text-pixel-gray bg-pixel-black/80 hover:text-green-400 " +
    "hover:border-green-400 backdrop-blur-sm";

  return (
    <div className="fixed bottom-2 right-2 z-40 flex flex-col items-end gap-1">
      <button onClick={handleCopy} title={tooltip} className={btnCls}>
        {copied ? "COPIED" : display}
      </button>
    </div>
  );
}
