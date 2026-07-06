"use client";

import { useState, useEffect, useCallback } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:50100";

type Info = {
  height: number;
  validators: number;
  quorum: number;
  keys: string[];
  pending: number;
  circuits: string[];
};

type ValidatorInfo = {
  name: string;
  pub_key: string;
  sigs_used: number;
  sigs_capacity: number;
};

type ProveResult = {
  ok: boolean;
  outputs?: Record<string, string>;
  proof?: Record<string, unknown>;
  error?: string;
};

const EXAMPLES: Record<string, object> = {
  double: {
    name: "double",
    inputs: ["x"],
    outputs: ["result"],
    gates: [
      { op: "Const", wire: "two", value: "2" },
      { op: "Mul", a: "x", b: "two", output: "result" },
    ],
  },
  hash_preimage: {
    name: "hash_preimage",
    inputs: ["secret"],
    outputs: ["digest"],
    gates: [{ op: "Hash", input: "secret", output: "digest" }],
  },
  pythagorean: {
    name: "pythagorean",
    inputs: ["a", "b", "c"],
    outputs: ["sum", "c_sq"],
    gates: [
      { op: "Mul", a: "a", b: "a", output: "a_sq" },
      { op: "Mul", a: "b", b: "b", output: "b_sq" },
      { op: "Mul", a: "c", b: "c", output: "c_sq" },
      { op: "Add", a: "a_sq", b: "b_sq", output: "sum" },
      { op: "Eq", a: "sum", b: "c_sq" },
    ],
  },
};

const INPUT = "w-full bg-zinc-900 border border-zinc-700 rounded px-3 py-2 outline-none focus:border-zinc-500";
const BTN = "bg-white text-black font-bold py-2 px-4 rounded hover:bg-zinc-200 transition-colors";

export default function Home() {
  const [info, setInfo] = useState<Info | null>(null);
  const [validators, setValidators] = useState<ValidatorInfo[]>([]);
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");
  const [lookupKey, setLookupKey] = useState("");
  const [lookupResult, setLookupResult] = useState<string | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const [valiName, setValiName] = useState("");
  const [valiPass, setValiPass] = useState("");

  // circuit state
  const [circuitJson, setCircuitJson] = useState(JSON.stringify(EXAMPLES.double, null, 2));
  const [execName, setExecName] = useState("double");
  const [execInputs, setExecInputs] = useState('{"x": "21"}');
  const [proveResult, setProveResult] = useState<ProveResult | null>(null);
  const [verifyResult, setVerifyResult] = useState<boolean | null>(null);

  const addLog = (msg: string) => setLog((prev) => [...prev.slice(-29), msg]);

  const refresh = useCallback(async () => {
    try {
      const [infoRes, valiRes] = await Promise.all([
        fetch(`${API}/info`),
        fetch(`${API}/validators`),
      ]);
      setInfo(await infoRes.json());
      setValidators(await valiRes.json());
    } catch {
      /* api not up */
    }
  }, []);

  useEffect(() => {
    refresh();
    const iv = setInterval(refresh, 3000);
    return () => clearInterval(iv);
  }, [refresh]);

  const handlePut = async () => {
    if (!key || !value) return;
    const res = await fetch(`${API}/put`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key, value }),
    });
    const data = await res.json();
    if (data.ok) {
      addLog(`PUT ${key}=${value} → block #${data.block} (${data.hash})`);
      setKey("");
      setValue("");
    } else {
      addLog(`PUT failed: ${data.error}`);
    }
    refresh();
  };

  const handleGet = async () => {
    if (!lookupKey) return;
    const res = await fetch(`${API}/get/${encodeURIComponent(lookupKey)}`);
    const data = await res.json();
    setLookupResult(data.value ?? "(not found)");
    addLog(`GET ${lookupKey} → ${data.value ?? "null"}`);
  };

  const handleAddValidator = async () => {
    if (!valiName) return;
    const body: Record<string, string> = { name: valiName };
    if (valiPass) body.passphrase = valiPass;
    const res = await fetch(`${API}/validator`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    addLog(
      `validator ${data.name} pk:${data.pub_key.slice(0, 12)}... priv:${data.priv_key.slice(0, 12)}...`
    );
    setValiName("");
    setValiPass("");
    refresh();
  };

  // -- circuit handlers --

  const handleRegister = async () => {
    try {
      const parsed = JSON.parse(circuitJson);
      const res = await fetch(`${API}/circuits`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: circuitJson,
      });
      const data = await res.json();
      addLog(`circuit "${parsed.name}" registered (${data.circuit_hash?.slice(0, 12)}...)`);
      refresh();
    } catch (e) {
      addLog(`register failed: ${e}`);
    }
  };

  const handleProve = async () => {
    try {
      const inputs = JSON.parse(execInputs);
      const res = await fetch(`${API}/circuits/${encodeURIComponent(execName)}/prove`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ inputs }),
      });
      const data: ProveResult = await res.json();
      setProveResult(data);
      setVerifyResult(null);
      if (data.ok) {
        addLog(
          `prove "${execName}" → ${JSON.stringify(data.outputs)} (${data.proof?.openings ? (data.proof.openings as unknown[]).length : 0} openings)`
        );
      } else {
        addLog(`prove failed: ${data.error}`);
      }
    } catch (e) {
      addLog(`prove failed: ${e}`);
    }
  };

  const handleVerify = async () => {
    if (!proveResult?.proof) return;
    try {
      const res = await fetch(`${API}/verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: execName, proof: proveResult.proof }),
      });
      const data = await res.json();
      setVerifyResult(data.valid);
      addLog(`verify → ${data.valid ? "VALID" : "INVALID"}`);
    } catch (e) {
      addLog(`verify failed: ${e}`);
    }
  };

  return (
    <div className="max-w-4xl mx-auto p-8 font-mono">
      <h1 className="text-3xl font-bold mb-2">qbit</h1>
      <p className="text-zinc-500 mb-8">
        quantum-resistant key-value store + zk circuits
      </p>

      {/* stats */}
      <div className="grid grid-cols-5 gap-4 mb-8">
        {(
          [
            ["height", info?.height ?? 0],
            ["validators", info?.validators ?? 0],
            ["quorum", info?.quorum ?? 0],
            ["keys", info?.keys?.length ?? 0],
            ["circuits", info?.circuits?.length ?? 0],
          ] as const
        ).map(([label, val]) => (
          <div key={label} className="border border-zinc-800 rounded-lg p-4">
            <div className="text-2xl font-bold">{val}</div>
            <div className="text-zinc-500 text-sm">{label}</div>
          </div>
        ))}
      </div>

      {/* put / get */}
      <div className="grid grid-cols-2 gap-8 mb-8">
        <div className="border border-zinc-800 rounded-lg p-4">
          <h2 className="text-lg font-bold mb-4">put</h2>
          <input className={`${INPUT} w-full mb-2`} placeholder="key" value={key} onChange={(e) => setKey(e.target.value)} onKeyDown={(e) => e.key === "Enter" && handlePut()} />
          <input className={`${INPUT} w-full mb-3`} placeholder="value" value={value} onChange={(e) => setValue(e.target.value)} onKeyDown={(e) => e.key === "Enter" && handlePut()} />
          <button onClick={handlePut} className={`${BTN} w-full`}>put</button>
        </div>
        <div className="border border-zinc-800 rounded-lg p-4">
          <h2 className="text-lg font-bold mb-4">get</h2>
          <input className={`${INPUT} w-full mb-3`} placeholder="key" value={lookupKey} onChange={(e) => setLookupKey(e.target.value)} onKeyDown={(e) => e.key === "Enter" && handleGet()} />
          <button onClick={handleGet} className={`${BTN} w-full mb-3`}>get</button>
          {lookupResult !== null && (
            <div className="bg-zinc-900 rounded px-3 py-2 text-sm break-all text-zinc-300">{lookupResult}</div>
          )}
        </div>
      </div>

      {/* validators */}
      <div className="border border-zinc-800 rounded-lg p-4 mb-8">
        <h2 className="text-lg font-bold mb-4">validators</h2>
        <div className="flex gap-2 mb-4">
          <input className={`${INPUT} flex-1`} placeholder="name" value={valiName} onChange={(e) => setValiName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && handleAddValidator()} />
          <input className={`${INPUT} flex-1`} placeholder="passphrase (optional)" value={valiPass} onChange={(e) => setValiPass(e.target.value)} onKeyDown={(e) => e.key === "Enter" && handleAddValidator()} />
          <button onClick={handleAddValidator} className={BTN}>add</button>
        </div>
        {validators.length === 0 ? (
          <p className="text-zinc-600 text-sm">no validators yet</p>
        ) : (
          <div className="space-y-2">
            {validators.map((v) => (
              <div key={v.name} className="flex justify-between items-center text-sm bg-zinc-900 rounded px-3 py-2">
                <span className="font-bold">{v.name}</span>
                <span className="text-zinc-500">{v.pub_key.slice(0, 12)}... sigs {v.sigs_used}/{v.sigs_capacity}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* circuits */}
      <div className="border border-zinc-800 rounded-lg p-4 mb-8">
        <h2 className="text-lg font-bold mb-4">circuits</h2>

        <div className="flex gap-2 mb-3">
          {Object.keys(EXAMPLES).map((name) => (
            <button
              key={name}
              onClick={() => {
                setCircuitJson(JSON.stringify(EXAMPLES[name], null, 2));
                setExecName(name);
                if (name === "double") setExecInputs('{"x": "21"}');
                else if (name === "hash_preimage") setExecInputs('{"secret": "hello"}');
                else if (name === "pythagorean") setExecInputs('{"a": "3", "b": "4", "c": "5"}');
              }}
              className="text-sm px-3 py-1 bg-zinc-800 rounded hover:bg-zinc-700 transition-colors"
            >
              {name}
            </button>
          ))}
        </div>

        <textarea
          className={`${INPUT} w-full mb-3 font-mono text-xs`}
          rows={8}
          value={circuitJson}
          onChange={(e) => setCircuitJson(e.target.value)}
        />
        <button onClick={handleRegister} className={`${BTN} w-full mb-6`}>
          register circuit
        </button>

        <h3 className="font-bold mb-3">prove</h3>
        <div className="flex gap-2 mb-3">
          <input className={`${INPUT} w-40`} placeholder="circuit name" value={execName} onChange={(e) => setExecName(e.target.value)} />
          <input className={`${INPUT} flex-1`} placeholder='{"x": "21"}' value={execInputs} onChange={(e) => setExecInputs(e.target.value)} onKeyDown={(e) => e.key === "Enter" && handleProve()} />
          <button onClick={handleProve} className={BTN}>prove</button>
        </div>

        {proveResult?.ok && (
          <div className="space-y-3">
            <div className="bg-zinc-900 rounded px-3 py-2 text-sm">
              <span className="text-zinc-500">outputs: </span>
              <span className="text-green-400">{JSON.stringify(proveResult.outputs)}</span>
            </div>
            <details className="bg-zinc-900 rounded px-3 py-2 text-xs">
              <summary className="cursor-pointer text-zinc-500 text-sm">
                proof ({(proveResult.proof?.openings as unknown[])?.length ?? 0} openings, trace root: {String(proveResult.proof?.trace_root ?? "").slice(0, 16)}...)
              </summary>
              <pre className="mt-2 overflow-x-auto max-h-48 text-zinc-400">
                {JSON.stringify(proveResult.proof, null, 2)}
              </pre>
            </details>
            <div className="flex items-center gap-3">
              <button onClick={handleVerify} className={BTN}>
                verify proof
              </button>
              {verifyResult !== null && (
                <span className={verifyResult ? "text-green-400 font-bold" : "text-red-400 font-bold"}>
                  {verifyResult ? "VALID" : "INVALID"}
                </span>
              )}
            </div>
          </div>
        )}
        {proveResult && !proveResult.ok && (
          <div className="bg-red-900/30 text-red-400 rounded px-3 py-2 text-sm">
            {proveResult.error}
          </div>
        )}
      </div>

      {/* log */}
      <div className="border border-zinc-800 rounded-lg p-4">
        <h2 className="text-lg font-bold mb-4">log</h2>
        <div className="space-y-1 text-sm text-zinc-400 max-h-64 overflow-y-auto">
          {log.length === 0 ? (
            <p className="text-zinc-600">waiting for operations...</p>
          ) : (
            log.map((entry, i) => <div key={i}>{entry}</div>)
          )}
        </div>
      </div>
    </div>
  );
}
