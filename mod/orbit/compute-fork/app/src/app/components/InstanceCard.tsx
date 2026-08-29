"use client";

import { Cpu, HardDrive, Clock, DollarSign, Play, Square, Trash2, Receipt } from "lucide-react";
import { StatusBadge } from "./StatusBadge";

export interface Instance {
  name: string;
  offer?: string;
  image: string;
  host: string;
  client: string;
  rate: number;
  cpus?: number;
  memory?: string;
  gpu?: boolean;
  status: string;
  started?: number;
  total_billed: number;
  unbilled_hours?: number;
  unbilled_amount?: number;
  live?: boolean;
}

interface Props {
  instance: Instance;
  onAction: (action: string, name: string) => void;
}

function elapsed(ts?: number): string {
  if (!ts) return "--";
  const h = (Date.now() / 1000 - ts) / 3600;
  if (h < 1) return `${Math.round(h * 60)}m`;
  if (h < 24) return `${h.toFixed(1)}h`;
  return `${(h / 24).toFixed(1)}d`;
}

function truncAddr(a: string): string {
  if (!a || a.length < 12) return a || "--";
  return `${a.slice(0, 6)}...${a.slice(-4)}`;
}

export function InstanceCard({ instance: inst, onAction }: Props) {
  const isRunning = inst.status === "running";

  return (
    <div
      className="rounded-lg border p-4 flex flex-col gap-3 transition-colors"
      style={{
        backgroundColor: "var(--bg-card)",
        borderColor: inst.live ? "var(--green)" : "var(--border)",
      }}
    >
      {/* header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold">{inst.name}</span>
          <StatusBadge status={inst.status} />
        </div>
        {inst.live && (
          <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
        )}
      </div>

      {/* specs */}
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs" style={{ color: "var(--text-secondary)" }}>
        <div className="flex items-center gap-1.5">
          <Cpu size={12} />
          <span>{inst.cpus ?? "--"} vCPU</span>
        </div>
        <div className="flex items-center gap-1.5">
          <HardDrive size={12} />
          <span>{inst.memory ?? "--"}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <Clock size={12} />
          <span>{elapsed(inst.started)}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <DollarSign size={12} />
          <span>{inst.rate} tok/hr</span>
        </div>
      </div>

      {/* billing */}
      <div
        className="rounded px-3 py-2 flex justify-between text-xs"
        style={{ backgroundColor: "var(--bg-secondary)" }}
      >
        <div>
          <span style={{ color: "var(--text-muted)" }}>Billed</span>{" "}
          <span className="text-emerald-400">{inst.total_billed.toFixed(4)}</span>
        </div>
        <div>
          <span style={{ color: "var(--text-muted)" }}>Pending</span>{" "}
          <span className="text-amber-400">
            {(inst.unbilled_amount ?? 0).toFixed(4)}
          </span>
        </div>
      </div>

      {/* addresses */}
      <div className="flex justify-between text-[10px]" style={{ color: "var(--text-muted)" }}>
        <span>host {truncAddr(inst.host)}</span>
        <span>client {truncAddr(inst.client)}</span>
      </div>

      {/* actions */}
      <div className="flex gap-2 pt-1">
        {isRunning ? (
          <button
            onClick={() => onAction("stop", inst.name)}
            className="flex-1 flex items-center justify-center gap-1.5 text-xs py-1.5 rounded border transition-colors hover:bg-amber-500/10"
            style={{ borderColor: "var(--border)" }}
          >
            <Square size={12} /> Stop
          </button>
        ) : inst.status === "stopped" || inst.status === "suspended" ? (
          <button
            onClick={() => onAction("start", inst.name)}
            className="flex-1 flex items-center justify-center gap-1.5 text-xs py-1.5 rounded border transition-colors hover:bg-emerald-500/10"
            style={{ borderColor: "var(--border)" }}
          >
            <Play size={12} /> Start
          </button>
        ) : null}
        {isRunning && (
          <button
            onClick={() => onAction("bill", inst.name)}
            className="flex-1 flex items-center justify-center gap-1.5 text-xs py-1.5 rounded border transition-colors hover:bg-indigo-500/10"
            style={{ borderColor: "var(--border)" }}
          >
            <Receipt size={12} /> Bill
          </button>
        )}
        <button
          onClick={() => onAction(inst.status === "running" ? "release" : "destroy", inst.name)}
          className="flex items-center justify-center gap-1.5 text-xs py-1.5 px-3 rounded border transition-colors hover:bg-red-500/10 text-red-400"
          style={{ borderColor: "var(--border)" }}
        >
          <Trash2 size={12} />
        </button>
      </div>
    </div>
  );
}
