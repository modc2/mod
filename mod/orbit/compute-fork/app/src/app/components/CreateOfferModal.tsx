"use client";

import { useState } from "react";
import { X } from "lucide-react";

interface Props {
  open: boolean;
  onClose: () => void;
  onSubmit: (data: any) => void;
}

export function CreateOfferModal({ open, onClose, onSubmit }: Props) {
  const [form, setForm] = useState({
    name: "",
    rate: "",
    image: "ubuntu:22.04",
    cpus: "",
    memory: "",
    gpu: false,
    description: "",
  });

  if (!open) return null;

  const set = (k: string, v: any) => setForm((p) => ({ ...p, [k]: v }));

  const submit = () => {
    onSubmit({
      name: form.name,
      rate: parseFloat(form.rate) || 1,
      image: form.image || undefined,
      cpus: form.cpus ? parseInt(form.cpus) : undefined,
      memory: form.memory || undefined,
      gpu: form.gpu,
      description: form.description || undefined,
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
          <span className="text-sm font-semibold">New Compute Offer</span>
          <button onClick={onClose}>
            <X size={16} style={{ color: "var(--text-muted)" }} />
          </button>
        </div>

        <div className="flex flex-col gap-3">
          <Field label="Name" value={form.name} onChange={(v) => set("name", v)} placeholder="gpu-a100" />
          <Field label="Rate (tok/hr)" value={form.rate} onChange={(v) => set("rate", v)} placeholder="2.0" />
          <Field label="Image" value={form.image} onChange={(v) => set("image", v)} placeholder="ubuntu:22.04" />
          <div className="grid grid-cols-2 gap-3">
            <Field label="CPUs" value={form.cpus} onChange={(v) => set("cpus", v)} placeholder="4" />
            <Field label="Memory" value={form.memory} onChange={(v) => set("memory", v)} placeholder="8g" />
          </div>
          <Field label="Description" value={form.description} onChange={(v) => set("description", v)} placeholder="High-performance GPU instance" />
          <label className="flex items-center gap-2 text-xs cursor-pointer" style={{ color: "var(--text-secondary)" }}>
            <input type="checkbox" checked={form.gpu} onChange={(e) => set("gpu", e.target.checked)} className="accent-indigo-500" />
            GPU enabled
          </label>
        </div>

        <button
          onClick={submit}
          disabled={!form.name || !form.rate}
          className="w-full py-2 rounded text-sm font-medium transition-colors disabled:opacity-30 bg-indigo-600 hover:bg-indigo-500 text-white"
        >
          Register Offer
        </button>
      </div>
    </div>
  );
}

function Field({
  label, value, onChange, placeholder,
}: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-[10px] uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>
        {label}
      </label>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="rounded border px-3 py-1.5 text-xs outline-none transition-colors focus:border-indigo-500/50"
        style={{
          backgroundColor: "var(--bg-primary)",
          borderColor: "var(--border)",
          color: "var(--text-primary)",
        }}
      />
    </div>
  );
}
