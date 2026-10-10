"use client";

import { ValidatorEntry } from "@/lib/types";
import { formatToken, shortenKey } from "@/lib/api";

interface Props {
  validators: ValidatorEntry[];
  subnetId: number;
}

export default function ValidatorTable({ validators, subnetId }: Props) {
  return (
    <div className="space-y-3">
      <h2 className="text-sm font-semibold text-nt-text">
        Validators <span className="text-nt-muted">(Subnet #{subnetId})</span>
      </h2>

      {validators.length === 0 ? (
        <div className="text-center py-8 text-nt-muted text-xs">
          No validators registered
        </div>
      ) : (
        <div className="border border-nt-border rounded overflow-hidden">
          <table className="w-full text-xs">
            <thead>
              <tr className="bg-nt-panel text-nt-muted border-b border-nt-border">
                <th className="text-left px-3 py-2">#</th>
                <th className="text-left px-3 py-2">Key</th>
                <th className="text-right px-3 py-2">Score</th>
                <th className="text-right px-3 py-2">STT</th>
                <th className="text-right px-3 py-2">Commission</th>
                <th className="text-center px-3 py-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {validators.map((v, i) => (
                <tr
                  key={v.key}
                  className="border-b border-nt-border/50 hover:bg-nt-panel/50"
                >
                  <td className="px-3 py-2 text-nt-muted">{i + 1}</td>
                  <td className="px-3 py-2 font-mono text-nt-accent">
                    {shortenKey(v.key)}
                  </td>
                  <td className="px-3 py-2 text-right">
                    {formatToken(v.score)}
                  </td>
                  <td className="px-3 py-2 text-right">
                    {formatToken(v.total_stt)}
                  </td>
                  <td className="px-3 py-2 text-right">
                    {(v.commission_bps / 100).toFixed(1)}%
                  </td>
                  <td className="px-3 py-2 text-center">
                    <span
                      className={`text-[10px] px-1.5 py-0.5 rounded ${
                        v.active
                          ? "bg-nt-green/10 text-nt-green"
                          : "bg-nt-red/10 text-nt-red"
                      }`}
                    >
                      {v.active ? "ACTIVE" : "OFFLINE"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
