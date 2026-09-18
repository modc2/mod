"use client";

import React from "react";

/* ── nav + action icons ───────────────────────────────────────────────────────
   Line icons rather than emoji: emoji pick up a color font that is missing on
   plenty of Linux desktops (and every headless screenshot), where they land as
   tofu boxes. These inherit currentColor, so every skin styles them for free. */

export const Ico = ({ children, s = 15 }: { children: React.ReactNode; s?: number }) => (
  <svg className="ico" width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="currentColor"
       strokeWidth={s < 13 ? 2.1 : 1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    {children}
  </svg>
);

export const MarketIcon = () => (
  <Ico><path d="M3 4h2.2l2 11.2a1.8 1.8 0 0 0 1.8 1.5h7.7a1.8 1.8 0 0 0 1.8-1.4L20 8H6.2" /><circle cx="9.5" cy="20" r="1.1" /><circle cx="17" cy="20" r="1.1" /></Ico>
);
export const StackIcon = () => (
  <Ico><path d="M12 3 21 7.5 12 12 3 7.5 12 3Z" /><path d="m3 12 9 4.5L21 12" /><path d="m3 16.5 9 4.5 9-4.5" /></Ico>
);
export const PlusIcon = () => (
  <Ico><path d="M12 5v14M5 12h14" /></Ico>
);
export const ShareIcon = () => (
  <Ico><circle cx="18" cy="5.5" r="2.4" /><circle cx="6" cy="12" r="2.4" /><circle cx="18" cy="18.5" r="2.4" /><path d="m8.1 10.8 7.8-4.1M8.1 13.2l7.8 4.1" /></Ico>
);
export const PinIcon = ({ s = 15 }: { s?: number }) => (
  <Ico s={s}><path d="M14.5 3.2 20.8 9.5l-2.6 1a4 4 0 0 0-2 1.5L13.4 15 9 10.6l3-2.8a4 4 0 0 0 1.5-2l1-2.6Z" /><path d="m9 15-5 5" /></Ico>
);
export const PoolIcon = () => (
  <Ico><ellipse cx="12" cy="6" rx="7.5" ry="3" /><path d="M4.5 6v6c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3V6" /><path d="M4.5 12v6c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3v-6" /></Ico>
);
export const GraphIcon = () => (
  <Ico><circle cx="6" cy="7" r="2.2" /><circle cx="18" cy="6" r="2.2" /><circle cx="12" cy="17.5" r="2.2" /><path d="M7.9 8.3 10.7 15.6M16.6 7.9 13.3 15.7M8.2 6.7l7.6-.6" /></Ico>
);
export const ServerIcon = () => (
  <Ico><rect x="3.2" y="4" width="17.6" height="6" rx="1.6" /><rect x="3.2" y="14" width="17.6" height="6" rx="1.6" /><path d="M7 7h.01M7 17h.01" /></Ico>
);
export const TagIcon = () => (
  <Ico><path d="M11.2 3H20a1 1 0 0 1 1 1v8.8a2 2 0 0 1-.6 1.4l-6.2 6.2a2 2 0 0 1-2.8 0l-7.8-7.8a2 2 0 0 1 0-2.8l6.2-6.2A2 2 0 0 1 11.2 3Z" /><circle cx="16.4" cy="7.6" r="1.3" /></Ico>
);
export const FreeIcon = () => (
  <Ico><circle cx="12" cy="12" r="8.4" /><path d="M12 7.4v9.2M14.4 9.4c-.5-.7-1.4-1.1-2.4-1.1-1.4 0-2.5.8-2.5 1.9 0 2.5 5 1.5 5 4 0 1.1-1.1 1.9-2.5 1.9-1.1 0-2-.4-2.5-1.1" /></Ico>
);

/* pill-scale marks — 11px so they sit inside a badge without shoving it open */
export const LockIcon = ({ s = 11 }: { s?: number }) => (
  <Ico s={s}><rect x="5" y="10.5" width="14" height="10" rx="2" /><path d="M8.4 10.5V7.8a3.6 3.6 0 0 1 7.2 0v2.7" /></Ico>
);
export const UnlockIcon = ({ s = 11 }: { s?: number }) => (
  <Ico s={s}><rect x="5" y="10.5" width="14" height="10" rx="2" /><path d="M8.4 10.5V7.8a3.6 3.6 0 0 1 6.9-1.4" /></Ico>
);
export const GlobeIcon = ({ s = 11 }: { s?: number }) => (
  <Ico s={s}><circle cx="12" cy="12" r="8.6" /><path d="M3.4 12h17.2M12 3.4c2.2 2.4 3.4 5.4 3.4 8.6S14.2 18.2 12 20.6c-2.2-2.4-3.4-5.4-3.4-8.6S9.8 5.8 12 3.4Z" /></Ico>
);
export const ClockIcon = ({ s = 11 }: { s?: number }) => (
  <Ico s={s}><circle cx="12" cy="12" r="8.6" /><path d="M12 7.2V12l3.2 2" /></Ico>
);
export const BoltIcon = ({ s = 13 }: { s?: number }) => (
  <Ico s={s}><path d="M13.2 2.5 4.8 13.4h6L10.2 21.5l8.6-11.1h-6.2l.6-7.9Z" /></Ico>
);
export const TrashIcon = () => (
  <Ico s={13}><path d="M4.5 6.6h15M9.6 6.6V4.8a1.4 1.4 0 0 1 1.4-1.4h2a1.4 1.4 0 0 1 1.4 1.4v1.8M6.6 6.6l.9 12.6a1.6 1.6 0 0 0 1.6 1.4h5.8a1.6 1.6 0 0 0 1.6-1.4l.9-12.6" /></Ico>
);
export const GavelIcon = () => (
  <Ico s={13}><path d="M3.6 20.4h8.8M13.6 3.6 9 8.2M17 7l-4.6 4.6M9.9 4.5l6.4 6.4M5.8 12.6l4.6-4.6 3.6 3.6-4.6 4.6a2.5 2.5 0 0 1-3.6-3.6Z" /></Ico>
);
export const SemIcon = ({ s = 12 }: { s?: number }) => (
  <Ico s={s}><circle cx="8" cy="7.5" r="2.6" /><circle cx="17" cy="10.5" r="2.6" /><circle cx="10" cy="17" r="2.6" /><path d="M10.3 8.8 14.6 10M9.4 10 9.8 14.4M12.4 15.5 15.6 12.7" /></Ico>
);
export const FileIcon = () => (
  <Ico><path d="M14 3.2H7.6a1.8 1.8 0 0 0-1.8 1.8v14a1.8 1.8 0 0 0 1.8 1.8h8.8a1.8 1.8 0 0 0 1.8-1.8V7.4L14 3.2Z" /><path d="M13.6 3.4v4.2h4.4" /></Ico>
);
export const TextIcon = () => (
  <Ico><path d="M5 6.4V4.6h14v1.8M12 4.8v14.4M9 19.4h6" /></Ico>
);
export const JsonIcon = () => (
  <Ico><path d="M9.4 3.6C6.8 3.6 7.6 9 5 12c2.6 3 1.8 8.4 4.4 8.4M14.6 3.6c2.6 0 1.8 5.4 4.4 8.4-2.6 3-1.8 8.4-4.4 8.4" /></Ico>
);
export const ImageIcon = () => (
  <Ico><rect x="3.4" y="4.6" width="17.2" height="14.8" rx="2" /><circle cx="8.6" cy="9.8" r="1.6" /><path d="m4 17 5.2-5 4 3.6 3-2.6 4.4 4" /></Ico>
);

export const CopyIcon = ({ s = 12 }: { s?: number }) => (
  <Ico s={s}><rect x="8.6" y="8.6" width="11.8" height="11.8" rx="2" /><path d="M15.4 5.4V5a1.6 1.6 0 0 0-1.6-1.6H5.2A1.6 1.6 0 0 0 3.6 5v8.6a1.6 1.6 0 0 0 1.6 1.6h.4" /></Ico>
);
