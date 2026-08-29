"use client";

import { Cpu, HardDrive, Zap, DollarSign, ShoppingCart, Trash2 } from "lucide-react";

export interface Offer {
  name: string;
  rate: number;
  image: string;
  cpus?: number;
  memory?: string;
  gpu?: boolean;
  description?: string;
  provider: string;
}

interface Props {
  offer: Offer;
  onRent: (offer: Offer) => void;
  onRemove: (name: string) => void;
  isOwner: boolean;
}

export function OfferCard({ offer, onRent, onRemove, isOwner }: Props) {
  return (
    <div
      className="rounded-lg border p-4 flex flex-col gap-3 transition-colors hover:border-indigo-500/40"
      style={{ backgroundColor: "var(--bg-card)", borderColor: "var(--border)" }}
    >
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold">{offer.name}</span>
        <span className="text-xs font-mono text-indigo-400">
          {offer.rate} tok/hr
        </span>
      </div>

      {offer.description && (
        <p className="text-xs" style={{ color: "var(--text-secondary)" }}>
          {offer.description}
        </p>
      )}

      <div className="grid grid-cols-3 gap-2 text-xs" style={{ color: "var(--text-secondary)" }}>
        <div className="flex items-center gap-1">
          <Cpu size={12} /> {offer.cpus ?? "--"}
        </div>
        <div className="flex items-center gap-1">
          <HardDrive size={12} /> {offer.memory ?? "--"}
        </div>
        <div className="flex items-center gap-1">
          <Zap size={12} className={offer.gpu ? "text-amber-400" : ""} />
          {offer.gpu ? "GPU" : "CPU"}
        </div>
      </div>

      <div className="text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>
        {offer.image}
      </div>

      <div className="flex gap-2 pt-1">
        <button
          onClick={() => onRent(offer)}
          className="flex-1 flex items-center justify-center gap-1.5 text-xs py-1.5 rounded border transition-colors hover:bg-indigo-500/10 text-indigo-400"
          style={{ borderColor: "var(--border)" }}
        >
          <ShoppingCart size={12} /> Rent
        </button>
        {isOwner && (
          <button
            onClick={() => onRemove(offer.name)}
            className="flex items-center justify-center gap-1.5 text-xs py-1.5 px-3 rounded border transition-colors hover:bg-red-500/10 text-red-400"
            style={{ borderColor: "var(--border)" }}
          >
            <Trash2 size={12} />
          </button>
        )}
      </div>
    </div>
  );
}
