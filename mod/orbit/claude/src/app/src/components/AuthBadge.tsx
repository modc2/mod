"use client";

import { useEffect, useState } from "react";

const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "/claude";

// Small fixed-position pill showing Claude credential status, linking to the
// /auth page where credentials can be entered. Rendered app-wide via layout.
export default function AuthBadge() {
  const [ok, setOk] = useState<boolean | null>(null);
  const [label, setLabel] = useState("auth");

  useEffect(() => {
    let alive = true;
    const check = async () => {
      try {
        const r = await fetch(`${BASE_PATH}/api/credentials`, { cache: "no-store" });
        const d = await r.json();
        if (!alive) return;
        setOk(!!d.loggedIn);
        if (d.loggedIn && typeof d.remainingMinutes === "number") {
          const h = Math.floor(d.remainingMinutes / 60);
          setLabel(h > 0 ? `auth ${h}h` : `auth ${d.remainingMinutes}m`);
        } else {
          setLabel(d.loggedIn ? "auth ok" : "login");
        }
      } catch {
        if (alive) { setOk(false); setLabel("auth"); }
      }
    };
    check();
    const id = setInterval(check, 60_000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  // Hide entirely until first check resolves, to avoid a flash.
  if (ok === null) return null;

  return (
    <a
      href={`${BASE_PATH}/auth`}
      title={ok ? "Claude credentials valid — click to manage" : "Not logged in — click to enter credentials"}
      style={{
        position: "fixed",
        bottom: 14,
        right: 14,
        zIndex: 9999,
        display: "flex",
        alignItems: "center",
        gap: 7,
        padding: "5px 11px",
        borderRadius: 999,
        fontSize: 12,
        fontWeight: 500,
        textDecoration: "none",
        fontFamily: "ui-sans-serif, system-ui, sans-serif",
        color: ok ? "#9fb6a6" : "#f0c9c2",
        background: ok ? "rgba(20,32,24,0.85)" : "rgba(40,18,18,0.92)",
        border: `1px solid ${ok ? "#2e5d3f" : "#7a3a34"}`,
        backdropFilter: "blur(6px)",
        boxShadow: ok ? "none" : "0 0 14px rgba(224,86,75,0.35)",
      }}
    >
      <span
        style={{
          width: 7,
          height: 7,
          borderRadius: "50%",
          background: ok ? "#3ecf6b" : "#e0564b",
          boxShadow: ok ? "0 0 6px #3ecf6b" : "0 0 6px #e0564b",
        }}
      />
      {label}
    </a>
  );
}
