"use client";

import { useState } from "react";
import { X, Cpu, HardDrive, Zap } from "lucide-react";
import type { Offer } from "./OfferCard";

interface Props {
  offer: Offer | null;
  onClose: () => void;
  onSubmit: (data: any) => void;
}

export function RentModal({ offer, onClose, onSubmit }: Props) {
  const [name, setName] = useState("");
  const [deposit, setDeposit] = useState("");

  if (!offer) return null;

  const submit = () => {
    onSubmit({
      name,
      host: offer.provider,
      offer: offer.name,
      deposit: deposit ? parseFloat(deposit) : undefined,
    });
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div
        className="w-full max-w-md rounded-lg border p-6 flex flex-col gap-4"
        style={{ backgroundColor: "var(--bg-secondary)", borderColor: "var(--border)" }}
      >
        <div className="flex items-center justify-between">
          <span className="text-sm font-semibold">Rent: {offer.name}</span>
          <button onClick={onClose}>
            <X size={16} style={{ color: "var(--text-muted)" }} />
          </button>
        </div>

        {/* offer summary */}
        <div
          className="rounded p-3 flex flex-col gap-2 text-xs"
          style={{ backgroundColor: "var(--bg-primary)", color: "var(--text-secondary)" }}
        >
          <div className="flex justify-between">
            <span className="text-indigo-400 font-medium">{offer.rate} tok/hr</span>
            <span>{offer.image}</span>
          </div>
          <div className="flex gap-4">
            <span className="flex items-center gap-1"><Cpu size={11} /> {offer.cpus ?? "--"} vCPU</span>
            <span className="flex items-center gap-1"><HardDrive size={11} /> {offer.memory ?? "--"}</span>
            {offer.gpu && <span className="flex items-center gap-1 text-amber-400"><Zap size={11} /> GPU</span>}
          </div>
        </div>

        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <label className="text-[10px] uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>
              Instance Name
            </label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="my-worker-1"
              className="rounded border px-3 py-1.5 text-xs outline-none focus:border-indigo-500/50"
              style={{ backgroundColor: "var(--bg-primary)", borderColor: "var(--border)", color: "var(--text-primary)" }}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>
              Deposit (optional, min 1 hr = {offer.rate} tok)
            </label>
            <input
              value={deposit}
              onChange={(e) => setDeposit(e.target.value)}
              placeholder={String(offer.rate)}
              className="rounded border px-3 py-1.5 text-xs outline-none focus:border-indigo-500/50"
              style={{ backgroundColor: "var(--bg-primary)", borderColor: "var(--border)", color: "var(--text-primary)" }}
            />
          </div>
        </div>

        <button
          onClick={submit}
          disabled={!name}
          className="w-full py-2 rounded text-sm font-medium transition-colors disabled:opacity-30 bg-indigo-600 hover:bg-indigo-500 text-white"
        >
          Rent Instance
        </button>
      </div>
    </div>
  );
}
