"use client";

import React from "react";

type BentoProps = {
  children: React.ReactNode;
  className?: string;
  accent?: boolean;
  interactive?: boolean;
  span?: 1 | 2 | 3;
  rowSpan?: 1 | 2;
  title?: string;
  action?: React.ReactNode;
  onClick?: () => void;
};

export function Bento({
  children,
  className = "",
  accent,
  interactive,
  span = 1,
  rowSpan = 1,
  title,
  action,
  onClick,
}: BentoProps) {
  const classes = [
    "bento",
    accent ? "accent" : "",
    interactive ? "interactive" : "",
    span === 2 ? "col-2" : span === 3 ? "col-3" : "",
    rowSpan === 2 ? "row-2" : "",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={classes} onClick={onClick}>
      {(title || action) && (
        <div className="bento-header">
          {title && <span className="bento-title">{title}</span>}
          {action && <div>{action}</div>}
        </div>
      )}
      {children}
    </div>
  );
}

export function BentoGrid({
  children,
  dense,
  className = "",
}: {
  children: React.ReactNode;
  dense?: boolean;
  className?: string;
}) {
  return (
    <div className={["bento-grid", dense ? "dense" : "", className].filter(Boolean).join(" ")}>
      {children}
    </div>
  );
}

export function CidChip({ cid, onClick }: { cid: string; onClick?: () => void }) {
  const short = cid.length > 14 ? `${cid.slice(0, 8)}…${cid.slice(-4)}` : cid;
  return (
    <span
      className="cid-chip"
      title={cid}
      onClick={(e) => {
        e.stopPropagation();
        if (onClick) onClick();
        else navigator.clipboard?.writeText(cid).catch(() => {});
      }}
    >
      <span style={{ opacity: 0.6 }}>cid</span>
      {short}
    </span>
  );
}

export function GlassButton({
  children,
  variant = "default",
  onClick,
  disabled,
  type = "button",
  title,
}: {
  children: React.ReactNode;
  variant?: "default" | "primary" | "ghost";
  onClick?: () => void;
  disabled?: boolean;
  type?: "button" | "submit";
  title?: string;
}) {
  return (
    <button
      type={type}
      className={`glass-btn ${variant === "primary" ? "primary" : variant === "ghost" ? "ghost" : ""}`}
      onClick={onClick}
      disabled={disabled}
      title={title}
    >
      {children}
    </button>
  );
}
