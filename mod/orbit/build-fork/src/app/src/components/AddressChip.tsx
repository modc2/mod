"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ethers } from "ethers";
import { copyToClipboard } from "../utils/wallet";

/** EIP-55 checksum casing, so one address reads the same everywhere it shows
    up — and so a parent's `uppercase` class can't render it as "0XD779". */
export function checksumAddress(addr: string): string {
  try {
    return ethers.getAddress(addr);
  } catch {
    return addr;
  }
}

/** Middle-truncated form: 0xD779…996F. */
export function shortAddress(addr: string, head = 6, tail = 4): string {
  const a = checksumAddress(addr);
  if (a.length <= head + tail + 1) return a;
  return `${a.slice(0, head)}…${a.slice(-tail)}`;
}

interface AddressChipProps {
  address: string;
  /** Leading / trailing hex characters kept when truncating. */
  head?: number;
  tail?: number;
  /** Render the whole address instead of the truncated form. */
  full?: boolean;
  /** Font size in px — the chip's padding and icon scale off it. */
  size?: number;
  /** Accent for text and border. */
  color?: string;
  /** No border/background — for chips sitting inside a sentence. */
  bare?: boolean;
  /** Stretch to fill the row as a field (address left, copy affordance right)
      instead of hugging its text as a pill. */
  fill?: boolean;
  /** Fixed chip height in px, for toolbar rows where the chip has to line up
      with sibling buttons instead of hugging its own line-height. */
  height?: number;
  /** Explorer URL; renders a ↗ that opens it without copying. */
  explorerUrl?: string;
  /** Prepended to the copy tooltip, e.g. "Deposit address". */
  label?: string;
  className?: string;
  style?: React.CSSProperties;
}

/**
 * One click-to-copy address, used everywhere an address is shown.
 *
 * The whole chip is the copy target (not a 13px icon next to it), the tooltip
 * carries the full address for the times you need to read it, and the copied
 * state shows up as a ✓ badge so the address itself never moves.
 */
export default function AddressChip({
  address,
  head = 6,
  tail = 4,
  full = false,
  size = 11,
  color = "var(--crt-green)",
  bare = false,
  fill = false,
  height,
  explorerUrl,
  label,
  className = "",
  style,
}: AddressChipProps) {
  const [copied, setCopied] = useState(false);
  const [hover, setHover] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  const value = checksumAddress(address || "");

  const copy = useCallback(async (e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    const ok = await copyToClipboard(value);
    if (!ok) return;
    setCopied(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), 1600);
  }, [value]);

  if (!address) return null;

  const active = copied ? "var(--crt-green)" : color;
  const lit = hover || copied;

  return (
    <span
      className={`inline-flex items-center min-w-0 ${fill ? "flex-1 w-full" : ""} ${className}`}
      style={{ gap: Math.round(size * 0.45), ...style }}
    >
      <button
        type="button"
        onClick={copy}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        title={`${label ? `${label} — ` : ""}${value}\nClick to copy`}
        aria-label={`Copy address ${value}`}
        className={`inline-flex items-center min-w-0 focus-ring transition-all ${fill ? "flex-1 w-full justify-between" : ""}`}
        style={{
          gap: Math.round(size * 0.5),
          cursor: "copy",
          fontFamily: "'JetBrains Mono', 'SF Mono', Monaco, monospace",
          fontSize: size,
          fontWeight: 700,
          letterSpacing: "0.04em",
          // The chip carries its own casing — parents that uppercase their
          // header text must not turn "0x" into "0X".
          textTransform: "none",
          lineHeight: 1.1,
          color: active,
          height,
          padding: bare
            ? 0
            : `${height ? 0 : Math.round(size * (fill ? 0.5 : 0.28))}px ${Math.round(size * 0.7)}px`,
          borderRadius: bare ? 0 : fill ? 8 : 999,
          border: bare
            ? "none"
            : `1px solid color-mix(in srgb, ${active} ${lit ? 45 : 20}%, var(--border-color))`,
          background: bare
            ? "transparent"
            : `color-mix(in srgb, ${active} ${lit ? 13 : 6}%, transparent)`,
          textDecoration: bare && hover ? "underline" : "none",
          textUnderlineOffset: 2,
        }}
      >
        <span className="truncate tabular-nums">
          {full ? value : shortAddress(value, head, tail)}
        </span>
        {copied ? (
          <span
            className="shrink-0"
            style={{ fontSize: Math.max(8, size - 3), letterSpacing: "0.1em", opacity: 0.95 }}
          >
            ✓ COPIED
          </span>
        ) : (
          <svg
            viewBox="0 0 24 24"
            width={size}
            height={size}
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="shrink-0"
            style={{ opacity: hover ? 0.9 : 0.45 }}
            aria-hidden
          >
            <rect x="9" y="9" width="11" height="11" rx="2" />
            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
          </svg>
        )}
      </button>
      {explorerUrl && (
        // A real square hit target rather than a floating ↗ glyph — an icon on
        // a text baseline never lines up with the chip it sits beside.
        <a
          href={explorerUrl}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(e) => e.stopPropagation()}
          onMouseEnter={() => setHover(true)}
          onMouseLeave={() => setHover(false)}
          title="Open in block explorer"
          aria-label="Open address in block explorer"
          className="shrink-0 inline-flex items-center justify-center focus-ring transition-all"
          style={{
            width: height || Math.round(size * 2),
            height: height || Math.round(size * 2),
            borderRadius: bare ? 0 : fill || height ? 8 : 999,
            color: hover ? active : "var(--text-tertiary)",
            border: bare ? "none" : `1px solid color-mix(in srgb, ${active} ${hover ? 30 : 12}%, var(--border-color))`,
            background: bare ? "transparent" : `color-mix(in srgb, ${active} ${hover ? 8 : 0}%, transparent)`,
            textDecoration: "none",
          }}
        >
          <svg
            viewBox="0 0 24 24"
            width={size}
            height={size}
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden
          >
            <path d="M14 4h6v6M20 4l-8.5 8.5" />
            <path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />
          </svg>
        </a>
      )}
    </span>
  );
}
