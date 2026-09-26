"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createCopy } from "../lib/api";

/**
 * One trader, one sleeve. The τ you allocate here is the size of the
 * position: it joins whatever other copies are running and the server blends
 * them into one book, so adding a trader here doesn't disturb the others.
 */
export default function CopyForm({ defaultTarget }: { defaultTarget?: string }) {
  const router = useRouter();
  const [target, setTarget] = useState(defaultTarget || "");
  const [hotkey, setHotkey] = useState("");
  const [allocTao, setAllocTao] = useState("10");
  const [maxPerTx, setMaxPerTx] = useState("10");
  const [threshold, setThreshold] = useState("5");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      const alloc = parseFloat(allocTao);
      if (!(alloc > 0)) throw new Error("allocate some τ to this trader first");
      await createCopy({
        target_ss58: target,
        our_hotkey: hotkey,
        alloc_tao: alloc,
        max_tao_per_tx: parseFloat(maxPerTx),
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

      <Field
        label="allocate (τ)"
        hint="The money that follows this trader. Their book gives the shape; this gives the size."
      >
        <input
          required
          type="number" step="0.5" min="0.05"
          value={allocTao}
          onChange={(e) => setAllocTao(e.target.value)}
          className="pixel-input w-full font-mono"
        />
      </Field>

      <div className="grid grid-cols-2 gap-3">
        <Field label="max τ / tx">
          <input
            type="number" step="0.1" min="0"
            value={maxPerTx}
            onChange={(e) => setMaxPerTx(e.target.value)}
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
