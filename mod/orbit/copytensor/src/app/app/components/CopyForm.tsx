"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createCopy } from "../lib/api";

export default function CopyForm({ defaultTarget }: { defaultTarget?: string }) {
  const router = useRouter();
  const [target, setTarget] = useState(defaultTarget || "");
  const [hotkey, setHotkey] = useState("");
  const [maxPerTx, setMaxPerTx] = useState("10");
  const [dailyLimit, setDailyLimit] = useState("100");
  const [threshold, setThreshold] = useState("5");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      await createCopy({
        target_ss58: target,
        our_hotkey: hotkey,
        max_tao_per_tx: parseFloat(maxPerTx),
        daily_limit_tao: parseFloat(dailyLimit),
        rebalance_threshold_pct: parseFloat(threshold),
      });
      router.push("/strats");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  const Field = ({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) => (
    <div>
      <label className="block text-[10px] tracking-[2px] uppercase text-pixel-gray mb-1">
        {label}
      </label>
      {children}
      {hint && <p className="text-[10px] text-pixel-gray mt-1">{hint}</p>}
    </div>
  );

  return (
    <form onSubmit={handleSubmit} className="pixel-panel p-5 space-y-4 max-w-2xl">
      <Field label="target coldkey ss58" hint="The validator you want to mirror.">
        <input
          required
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          placeholder="5..."
          className="pixel-input w-full font-mono text-sm"
        />
      </Field>

      <Field label="your hotkey ss58" hint="The hotkey you'll stake through.">
        <input
          required
          value={hotkey}
          onChange={(e) => setHotkey(e.target.value)}
          placeholder="5..."
          className="pixel-input w-full font-mono text-sm"
        />
      </Field>

      <div className="grid grid-cols-3 gap-3">
        <Field label="max τ / tx">
          <input
            type="number" step="0.1" min="0"
            value={maxPerTx}
            onChange={(e) => setMaxPerTx(e.target.value)}
            className="pixel-input w-full font-mono"
          />
        </Field>
        <Field label="daily limit (τ)">
          <input
            type="number" step="1" min="0"
            value={dailyLimit}
            onChange={(e) => setDailyLimit(e.target.value)}
            className="pixel-input w-full font-mono"
          />
        </Field>
        <Field label="rebal threshold %">
          <input
            type="number" step="0.5" min="0"
            value={threshold}
            onChange={(e) => setThreshold(e.target.value)}
            className="pixel-input w-full font-mono"
          />
        </Field>
      </div>

      {error && (
        <div className="pixel-panel-red px-3 py-2 text-[12px] text-red-400 font-mono">
          {error}
        </div>
      )}

      <button
        type="submit"
        disabled={submitting}
        className="pixel-btn border-green-400 text-green-400 disabled:opacity-50"
      >
        {submitting ? "CREATING…" : "START COPY"}
      </button>
    </form>
  );
}
