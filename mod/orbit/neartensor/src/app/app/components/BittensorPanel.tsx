"use client";

import { useState, useEffect, useCallback } from "react";
import { api, shortenKey } from "@/lib/api";

interface Neuron {
  uid: number;
  hotkey: string;
  role: string;
  stake: number;
  url: string | null;
}

interface Subnet {
  netuid: number;
  name: string;
  consensus: string;
  tasks: string[];
  n: number;
  epochs: number;
  network: string;
}

interface Rule {
  name: string;
  about: string;
}

const TASKS = ["block_header", "gas_price", "account_state"];

const box = "p-3 rounded border border-nt-border bg-nt-panel";
const btn =
  "whitespace-nowrap px-3 py-1.5 text-xs rounded border border-nt-accent/30 bg-nt-accent/10 text-nt-accent hover:bg-nt-accent/20 disabled:opacity-50";
const input = "w-full px-2 py-1.5 text-xs rounded border border-nt-border bg-nt-bg text-nt-text";

export default function BittensorPanel() {
  const [subnets, setSubnets] = useState<Subnet[]>([]);
  const [rules, setRules] = useState<Rule[]>([]);
  const [netuid, setNetuid] = useState(0);
  const [neurons, setNeurons] = useState<Neuron[]>([]);
  const [incentive, setIncentive] = useState<Record<string, number>>({});
  const [scores, setScores] = useState<Record<string, number>>({});
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  // create form
  const [name, setName] = useState("");
  const [rule, setRule] = useState("yuma");
  const [tasks, setTasks] = useState<string[]>(TASKS);

  const loadSubnets = useCallback(async () => {
    const [subs, rs] = await Promise.all([api("neartensor/sn_subnets"), api("neartensor/sn_consensus")]);
    if (Array.isArray(subs)) setSubnets(subs);
    if (Array.isArray(rs)) setRules(rs);
  }, []);

  const loadSubnet = useCallback(async () => {
    const [mg, st] = await Promise.all([
      api("neartensor/sn_metagraph", { netuid }),
      api("neartensor/sn_status", { netuid }),
    ]);
    if (mg && !mg.error) {
      setNeurons(Array.isArray(mg.neurons) ? mg.neurons : []);
      setIncentive(mg.incentive || {});
    }
    setScores(st?.validator?.scores || {});
  }, [netuid]);

  useEffect(() => { loadSubnets(); }, [loadSubnets]);
  useEffect(() => { loadSubnet(); }, [loadSubnet]);

  const run = async (label: string, fn: () => Promise<any>) => {
    setBusy(true);
    setNote(null);
    const res = await fn();
    setNote(res?.error ? `${label} failed: ${res.error}` : null);
    setBusy(false);
    await loadSubnets();
    await loadSubnet();
    return res;
  };

  const create = async () => {
    const r = await run("Create", () => api("neartensor/sn_create", { name, consensus: rule, tasks }));
    if (r && !r.error) {
      setName("");
      setNetuid(r.netuid);
      setNote(`Created subnet ${r.netuid} "${r.name}". This box's miner and validator joined it.`);
    }
  };

  const epoch = async () => {
    const r = await run("Epoch", () => api("neartensor/sn_epoch", { netuid }));
    if (r && !r.error) {
      const scored = Object.keys(r.weights || {}).length > 0;
      setNote(`Epoch done: ${r.miners} miner(s) checked. ${scored ? "Weights set." : "No miner scored yet."}`);
    }
  };

  const switchRule = (r: string) =>
    run("Switch consensus", () => api("neartensor/sn_set_consensus", { netuid, consensus: r }));

  const current = subnets.find((s) => s.netuid === netuid);
  const toggleTask = (t: string) =>
    setTasks((ts) => (ts.includes(t) ? ts.filter((x) => x !== t) : [...ts, t]));

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      {/* left: subnet list + create */}
      <div className="space-y-4">
        <h2 className="text-sm font-semibold">
          Subnets <span className="text-nt-muted">({subnets.length})</span>
        </h2>
        <div className="grid gap-2">
          {subnets.map((s) => (
            <button
              key={s.netuid}
              onClick={() => setNetuid(s.netuid)}
              className={`text-left p-3 rounded border transition-colors ${
                s.netuid === netuid ? "border-nt-accent/50 bg-nt-accent/5" : "border-nt-border bg-nt-panel hover:border-nt-muted"
              }`}
            >
              <div className="flex justify-between text-xs">
                <span className="font-semibold">
                  <span className="text-nt-muted">{s.netuid}</span> {s.name}
                </span>
                <span className="text-nt-accent">{s.consensus}</span>
              </div>
              <div className="text-[10px] text-nt-muted mt-1">
                {s.n} neurons · {s.epochs} epochs · {s.tasks.length} tasks
              </div>
            </button>
          ))}
        </div>

        <div className={`${box} space-y-3`}>
          <h3 className="text-xs font-semibold">Create subnet</h3>
          <input className={input} placeholder="name, e.g. fast-headers" value={name}
                 onChange={(e) => setName(e.target.value)} maxLength={32} />
          <div>
            <div className="text-[10px] text-nt-muted mb-1">Consensus</div>
            <select className={input} value={rule} onChange={(e) => setRule(e.target.value)}>
              {rules.map((r) => <option key={r.name} value={r.name}>{r.name}</option>)}
            </select>
            <div className="text-[10px] text-nt-muted mt-1">{rules.find((r) => r.name === rule)?.about}</div>
          </div>
          <div>
            <div className="text-[10px] text-nt-muted mb-1">Tasks miners must answer</div>
            {TASKS.map((t) => (
              <label key={t} className="flex items-center gap-2 text-xs py-0.5">
                <input type="checkbox" checked={tasks.includes(t)} onChange={() => toggleTask(t)} />
                {t}
              </label>
            ))}
          </div>
          <button className={btn} disabled={busy || !name.trim() || tasks.length === 0} onClick={create}>
            {busy ? "Working…" : "Create subnet"}
          </button>
        </div>
      </div>

      {/* right: selected subnet */}
      <div className="lg:col-span-2 space-y-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-sm font-semibold">
              {current ? `${current.netuid} · ${current.name}` : `netuid ${netuid}`}
            </h2>
            <p className="text-[11px] text-nt-muted mt-1">
              Miners attest to NEAR state; validators re-check every answer at the anchored block and set weights.
              {current && ` Tasks: ${current.tasks.join(", ")}.`}
            </p>
          </div>
          <button className={btn} disabled={busy} onClick={epoch}>
            {busy ? "Working…" : "Run epoch"}
          </button>
        </div>

        {note && <div className={`${box} text-xs`}>{note}</div>}

        <div className={box}>
          <div className="text-[10px] text-nt-muted mb-2">Consensus (takes effect next epoch)</div>
          <div className="flex flex-wrap gap-2">
            {rules.map((r) => (
              <button
                key={r.name}
                title={r.about}
                disabled={busy || current?.network !== "local"}
                onClick={() => switchRule(r.name)}
                className={`px-3 py-1 text-xs rounded border ${
                  current?.consensus === r.name
                    ? "border-nt-accent/50 bg-nt-accent/10 text-nt-accent"
                    : "border-nt-border text-nt-muted hover:text-nt-text"
                }`}
              >
                {r.name}
              </button>
            ))}
          </div>
          <div className="text-[10px] text-nt-muted mt-2">
            {rules.find((r) => r.name === current?.consensus)?.about}
          </div>
        </div>

        {neurons.length === 0 ? (
          <div className="text-center py-6 text-nt-muted text-xs">
            No neurons yet. Start them with <code>m neartensor sn_serve</code>
          </div>
        ) : (
          <div className="overflow-x-auto rounded border border-nt-border">
            <table className="w-full text-xs">
              <thead className="bg-nt-panel text-nt-muted">
                <tr>
                  <th className="text-left px-3 py-2 font-medium">UID</th>
                  <th className="text-left px-3 py-2 font-medium">Hotkey</th>
                  <th className="text-left px-3 py-2 font-medium">Role</th>
                  <th className="text-left px-3 py-2 font-medium">Endpoint</th>
                  <th className="text-right px-3 py-2 font-medium">Score</th>
                  <th className="text-right px-3 py-2 font-medium">Incentive</th>
                </tr>
              </thead>
              <tbody>
                {neurons.map((n) => (
                  <tr key={n.uid} className="border-t border-nt-border">
                    <td className="px-3 py-2">{n.uid}</td>
                    <td className="px-3 py-2 font-mono">{shortenKey(n.hotkey, 6)}</td>
                    <td className="px-3 py-2">
                      <span className={n.role === "validator" ? "text-nt-accent" : ""}>{n.role}</span>
                    </td>
                    <td className="px-3 py-2 text-nt-muted">{n.url || "not serving"}</td>
                    <td className="px-3 py-2 text-right">
                      {scores[n.hotkey] !== undefined ? scores[n.hotkey].toFixed(3) : "—"}
                    </td>
                    <td className="px-3 py-2 text-right">
                      {incentive[String(n.uid)] !== undefined ? incentive[String(n.uid)].toFixed(3) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
