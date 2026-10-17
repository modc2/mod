"use client";

import { useState } from "react";
import { api } from "@/lib/api";

interface Props {
  subnetId: number;
  onRefresh: () => void;
}

export default function StakingPanel({ subnetId, onRefresh }: Props) {
  const [validatorKey, setValidatorKey] = useState("");
  const [lockBlocks, setLockBlocks] = useState("0");
  const [amount, setAmount] = useState("1");
  const [action, setAction] = useState<"stake" | "register" | "checkin">("stake");
  const [result, setResult] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async () => {
    setLoading(true);
    setResult(null);
    try {
      let res;
      switch (action) {
        case "register":
          res = await api("neartensor/register_validator", {
            subnet_id: subnetId,
            key: validatorKey,
            key_type: "Ed25519",
          });
          break;
        case "stake":
          res = await api("neartensor/stake_on", {
            subnet_id: subnetId,
            validator_key: validatorKey,
            lock_blocks: parseInt(lockBlocks),
            amount: `${amount} NEAR`,
          });
          break;
        case "checkin":
          res = await api("neartensor/checkin", {
            subnet_id: subnetId,
            key: validatorKey,
          });
          break;
      }
      setResult(res);
      onRefresh();
    } catch (e: any) {
      setResult({ error: e.message });
    }
    setLoading(false);
  };

  return (
    <div className="space-y-3">
      <h2 className="text-sm font-semibold text-nt-text">
        Interact <span className="text-nt-muted">(Subnet #{subnetId})</span>
      </h2>

      <div className="p-4 rounded border border-nt-border bg-nt-panel space-y-3">
        {/* Action selector */}
        <div className="flex gap-1">
          {(["register", "stake", "checkin"] as const).map((a) => (
            <button
              key={a}
              onClick={() => setAction(a)}
              className={`px-3 py-1 text-xs rounded transition-colors ${
                action === a
                  ? "bg-nt-accent/10 text-nt-accent border border-nt-accent/30"
                  : "text-nt-muted hover:text-nt-text bg-nt-bg border border-nt-border"
              }`}
            >
              {a === "register" ? "Register Validator" : a === "stake" ? "Stake" : "Checkin"}
            </button>
          ))}
        </div>

        {/* Validator key input */}
        <div>
          <label className="text-[10px] text-nt-muted block mb-1">Validator Key</label>
          <input
            type="text"
            value={validatorKey}
            onChange={(e) => setValidatorKey(e.target.value)}
            placeholder="ed25519 public key..."
            className="w-full px-3 py-2 text-xs bg-nt-bg border border-nt-border rounded focus:border-nt-accent/50 focus:outline-none text-nt-text"
          />
        </div>

        {/* Stake-specific fields */}
        {action === "stake" && (
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-[10px] text-nt-muted block mb-1">Amount (NEAR)</label>
              <input
                type="text"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                className="w-full px-3 py-2 text-xs bg-nt-bg border border-nt-border rounded focus:border-nt-accent/50 focus:outline-none text-nt-text"
              />
            </div>
            <div>
              <label className="text-[10px] text-nt-muted block mb-1">Lock Blocks</label>
              <input
                type="text"
                value={lockBlocks}
                onChange={(e) => setLockBlocks(e.target.value)}
                className="w-full px-3 py-2 text-xs bg-nt-bg border border-nt-border rounded focus:border-nt-accent/50 focus:outline-none text-nt-text"
              />
            </div>
          </div>
        )}

        <button
          onClick={handleSubmit}
          disabled={loading || !validatorKey}
          className="w-full py-2 text-xs font-medium rounded bg-nt-accent/10 text-nt-accent border border-nt-accent/30 hover:bg-nt-accent/20 disabled:opacity-50 transition-colors"
        >
          {loading ? "Processing..." : action === "register" ? "Register" : action === "stake" ? "Stake" : "Checkin"}
        </button>

        {/* Result display */}
        {result && (
          <pre className="text-[10px] p-2 rounded bg-nt-bg border border-nt-border overflow-x-auto max-h-32">
            {JSON.stringify(result, null, 2)}
          </pre>
        )}
      </div>
    </div>
  );
}
