"use client";

import { useAuth } from "../context/AuthContext";
import { useSidebar } from "../context/SidebarContext";

/// Right-side button that expands/collapses the permanent account sidebar
/// (SidebarShell). The top bar already shows wallet/CLOB/token via dedicated
/// chips, so this trigger is purely the sidebar collapse toggle.
export default function ProfileMenu() {
  const { auth, localToken } = useAuth();
  const { collapsed, toggleCollapsed } = useSidebar();

  const triggerColor = !collapsed
    ? "border-pixel-white text-pixel-white bg-pixel-white/10"
    : auth.authenticated
    ? "border-green-400 text-green-400"
    : auth.connected
    ? "border-amber-400 text-amber-400"
    : localToken
    ? "border-green-400/60 text-green-400"
    : "border-pixel-border text-pixel-gray hover:text-pixel-white hover:border-pixel-white";

  return (
    <button
      onClick={toggleCollapsed}
      className={`pixel-btn text-[13px] px-2 py-1 transition-colors flex items-center gap-1.5 ${triggerColor}`}
      title="Profile / sign-in"
    >
      <div
        className={`w-1.5 h-1.5 ${
          auth.authenticated ? "bg-green-400" : auth.connected ? "bg-amber-400" : localToken ? "bg-green-400/70" : "bg-pixel-gray"
        }`}
      />
      <span className="font-mono">PANEL</span>
      <span className="text-[12px] opacity-60">{collapsed ? "◀" : "▶"}</span>
    </button>
  );
}
