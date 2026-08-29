"use client";

import { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Server,
  ShoppingBag,
  Receipt,
  RefreshCw,
  Plus,
  AlertCircle,
} from "lucide-react";
import { InstanceCard, type Instance } from "./components/InstanceCard";
import { OfferCard, type Offer } from "./components/OfferCard";
import { CreateOfferModal } from "./components/CreateOfferModal";
import { RentModal } from "./components/RentModal";

export const dynamic = "force-dynamic";

type Tab = "instances" | "offers" | "billing";

export default function ComputePage() {
  const [tab, setTab] = useState<Tab>("instances");
  const [instances, setInstances] = useState<Record<string, Instance>>({});
  const [offers, setOffers] = useState<Record<string, Offer>>({});
  const [showNewOffer, setShowNewOffer] = useState(false);
  const [rentTarget, setRentTarget] = useState<Offer | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [billingLog, setBillingLog] = useState<any[]>([]);

  // ── fetchers ──────────────────────────────────────────────────────

  const fetchInstances = useCallback(async () => {
    try {
      const res = await fetch("/api/instances");
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      setInstances(data);
    } catch (e: any) {
      setError(e.message);
    }
  }, []);

  const fetchOffers = useCallback(async () => {
    try {
      const res = await fetch("/api/offers");
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      setOffers(data);
    } catch (e: any) {
      setError(e.message);
    }
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    await Promise.all([fetchInstances(), fetchOffers()]);
    setLoading(false);
  }, [fetchInstances, fetchOffers]);

  useEffect(() => {
    refresh();
    const iv = setInterval(fetchInstances, 15000);
    return () => clearInterval(iv);
  }, [refresh, fetchInstances]);

  // ── actions ───────────────────────────────────────────────────────

  async function instanceAction(action: string, name: string) {
    setError(null);
    try {
      const res = await fetch("/api/instances", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, name }),
      });
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      if (action === "bill") {
        setBillingLog((prev) => [{ ...data, time: new Date().toISOString() }, ...prev].slice(0, 50));
      }
      await fetchInstances();
    } catch (e: any) {
      setError(e.message);
    }
  }

  async function submitOffer(data: any) {
    setError(null);
    try {
      const res = await fetch("/api/offers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "register", ...data }),
      });
      const result = await res.json();
      if (result.error) throw new Error(result.error);
      await fetchOffers();
    } catch (e: any) {
      setError(e.message);
    }
  }

  async function removeOffer(name: string) {
    setError(null);
    try {
      const res = await fetch("/api/offers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "remove", name }),
      });
      const result = await res.json();
      if (result.error) throw new Error(result.error);
      await fetchOffers();
    } catch (e: any) {
      setError(e.message);
    }
  }

  async function submitRent(data: any) {
    setError(null);
    try {
      const res = await fetch("/api/instances", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "rent", ...data }),
      });
      const result = await res.json();
      if (result.error) throw new Error(result.error);
      await fetchInstances();
      setTab("instances");
    } catch (e: any) {
      setError(e.message);
    }
  }

  async function billAll() {
    setError(null);
    try {
      const res = await fetch("/api/billing", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "bill_all" }),
      });
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      setBillingLog((prev) => [
        { ...data, time: new Date().toISOString(), type: "bill_all" },
        ...prev,
      ].slice(0, 50));
      await fetchInstances();
    } catch (e: any) {
      setError(e.message);
    }
  }

  // ── derived ───────────────────────────────────────────────────────

  const instanceList = Object.values(instances).filter(Boolean) as Instance[];
  const offerList = Object.values(offers).filter(Boolean) as Offer[];
  const runningCount = instanceList.filter((i) => i.status === "running").length;
  const totalRate = instanceList
    .filter((i) => i.status === "running")
    .reduce((s, i) => s + (i.rate || 0), 0);
  const totalBilled = instanceList.reduce((s, i) => s + (i.total_billed || 0), 0);

  // ── tabs ──────────────────────────────────────────────────────────

  const tabs: { key: Tab; label: string; icon: any }[] = [
    { key: "instances", label: "Instances", icon: Server },
    { key: "offers", label: "Offers", icon: ShoppingBag },
    { key: "billing", label: "Billing", icon: Receipt },
  ];

  return (
    <div className="min-h-screen flex flex-col" style={{ backgroundColor: "var(--bg-primary)" }}>
      {/* header */}
      <header
        className="flex items-center justify-between px-6 py-4 border-b"
        style={{ borderColor: "var(--border)" }}
      >
        <div className="flex items-center gap-3">
          <Server size={20} className="text-indigo-400" />
          <span className="text-base font-semibold tracking-tight">Compute</span>
        </div>
        <button
          onClick={refresh}
          disabled={loading}
          className="p-2 rounded hover:bg-white/5 transition-colors"
        >
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} style={{ color: "var(--text-muted)" }} />
        </button>
      </header>

      {/* stats bar */}
      <div
        className="grid grid-cols-3 gap-px text-center text-xs py-3 border-b"
        style={{ backgroundColor: "var(--bg-secondary)", borderColor: "var(--border)" }}
      >
        <div>
          <span style={{ color: "var(--text-muted)" }}>Running </span>
          <span className="text-emerald-400 font-medium">{runningCount}</span>
        </div>
        <div>
          <span style={{ color: "var(--text-muted)" }}>Rate </span>
          <span className="text-indigo-400 font-medium">{totalRate.toFixed(2)} tok/hr</span>
        </div>
        <div>
          <span style={{ color: "var(--text-muted)" }}>Billed </span>
          <span className="text-emerald-400 font-medium">{totalBilled.toFixed(4)}</span>
        </div>
      </div>

      {/* error */}
      <AnimatePresence>
        {error && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="flex items-center gap-2 px-6 py-2 text-xs text-red-400 border-b overflow-hidden"
            style={{ backgroundColor: "var(--bg-secondary)", borderColor: "var(--border)" }}
          >
            <AlertCircle size={12} />
            {error}
            <button onClick={() => setError(null)} className="ml-auto hover:text-red-300">dismiss</button>
          </motion.div>
        )}
      </AnimatePresence>

      {/* tab bar */}
      <nav
        className="flex border-b"
        style={{ borderColor: "var(--border)" }}
      >
        {tabs.map((t) => {
          const active = tab === t.key;
          return (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className="flex-1 flex items-center justify-center gap-1.5 py-3 text-xs transition-colors relative"
              style={{ color: active ? "var(--text-primary)" : "var(--text-muted)" }}
            >
              <t.icon size={13} />
              {t.label}
              {active && (
                <motion.div
                  layoutId="tab-underline"
                  className="absolute bottom-0 left-0 right-0 h-0.5 bg-indigo-500"
                />
              )}
            </button>
          );
        })}
      </nav>

      {/* content */}
      <main className="flex-1 overflow-auto p-6">
        {tab === "instances" && (
          <div className="flex flex-col gap-4">
            {instanceList.length === 0 ? (
              <Empty message="No instances yet. Rent one from the Offers tab." />
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {instanceList.map((inst) => (
                  <InstanceCard key={inst.name} instance={inst} onAction={instanceAction} />
                ))}
              </div>
            )}
          </div>
        )}

        {tab === "offers" && (
          <div className="flex flex-col gap-4">
            <div className="flex justify-end">
              <button
                onClick={() => setShowNewOffer(true)}
                className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded border transition-colors hover:bg-indigo-500/10 text-indigo-400"
                style={{ borderColor: "var(--border)" }}
              >
                <Plus size={12} /> New Offer
              </button>
            </div>
            {offerList.length === 0 ? (
              <Empty message='No offers listed. Click "New Offer" to create one.' />
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {offerList.map((o) => (
                  <OfferCard
                    key={o.name}
                    offer={o}
                    onRent={(offer) => setRentTarget(offer)}
                    onRemove={removeOffer}
                    isOwner={true}
                  />
                ))}
              </div>
            )}
          </div>
        )}

        {tab === "billing" && (
          <div className="flex flex-col gap-4">
            <div className="flex justify-end">
              <button
                onClick={billAll}
                className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded border transition-colors hover:bg-emerald-500/10 text-emerald-400"
                style={{ borderColor: "var(--border)" }}
              >
                <Receipt size={12} /> Bill All Now
              </button>
            </div>

            {/* per-instance billing */}
            <div
              className="rounded-lg border overflow-hidden"
              style={{ borderColor: "var(--border)" }}
            >
              <table className="w-full text-xs">
                <thead>
                  <tr style={{ backgroundColor: "var(--bg-secondary)", color: "var(--text-muted)" }}>
                    <th className="text-left px-4 py-2 font-medium">Instance</th>
                    <th className="text-right px-4 py-2 font-medium">Rate</th>
                    <th className="text-right px-4 py-2 font-medium">Total Billed</th>
                    <th className="text-right px-4 py-2 font-medium">Pending</th>
                    <th className="text-right px-4 py-2 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {instanceList.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="text-center py-8" style={{ color: "var(--text-muted)" }}>
                        No instances to bill
                      </td>
                    </tr>
                  ) : (
                    instanceList.map((inst) => (
                      <tr
                        key={inst.name}
                        className="border-t"
                        style={{ borderColor: "var(--border)" }}
                      >
                        <td className="px-4 py-2 font-medium">{inst.name}</td>
                        <td className="px-4 py-2 text-right text-indigo-400">{inst.rate} tok/hr</td>
                        <td className="px-4 py-2 text-right text-emerald-400">{inst.total_billed.toFixed(4)}</td>
                        <td className="px-4 py-2 text-right text-amber-400">
                          {(inst.unbilled_amount ?? 0).toFixed(4)}
                        </td>
                        <td className="px-4 py-2 text-right">{inst.status}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>

            {/* billing log */}
            {billingLog.length > 0 && (
              <div className="flex flex-col gap-2">
                <span className="text-xs font-medium" style={{ color: "var(--text-muted)" }}>
                  Recent transactions
                </span>
                <div
                  className="rounded-lg border divide-y max-h-64 overflow-auto"
                  style={{ borderColor: "var(--border)" }}
                >
                  {billingLog.map((entry, i) => (
                    <div
                      key={i}
                      className="flex items-center justify-between px-4 py-2 text-xs"
                      style={{ backgroundColor: "var(--bg-card)" }}
                    >
                      <span style={{ color: "var(--text-secondary)" }}>
                        {entry.name || "all"}
                      </span>
                      <span className="text-emerald-400 font-mono">
                        {typeof entry.billed === "number"
                          ? `${entry.billed.toFixed(4)} tok`
                          : JSON.stringify(entry).slice(0, 60)}
                      </span>
                      {entry.tx && (
                        <span className="font-mono" style={{ color: "var(--text-muted)" }}>
                          {String(entry.tx).slice(0, 10)}...
                        </span>
                      )}
                      <span style={{ color: "var(--text-muted)" }}>
                        {entry.time?.split("T")[1]?.slice(0, 8)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </main>

      {/* modals */}
      <CreateOfferModal
        open={showNewOffer}
        onClose={() => setShowNewOffer(false)}
        onSubmit={submitOffer}
      />
      <RentModal
        offer={rentTarget}
        onClose={() => setRentTarget(null)}
        onSubmit={submitRent}
      />
    </div>
  );
}

function Empty({ message }: { message: string }) {
  return (
    <div
      className="flex flex-col items-center justify-center py-20 text-sm"
      style={{ color: "var(--text-muted)" }}
    >
      <Server size={32} className="mb-3 opacity-30" />
      {message}
    </div>
  );
}
