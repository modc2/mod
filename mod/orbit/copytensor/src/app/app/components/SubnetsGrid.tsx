"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { fetchSubnets } from "../lib/api";
import type { SubnetInfo } from "../lib/types";
import { useFilters } from "../context/FiltersContext";

export default function SubnetsGrid() {
  const { search } = useFilters();
  const [subnets, setSubnets] = useState<SubnetInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  useEffect(() => {
    fetchSubnets()
      .then(setSubnets)
      .catch((e) => setErr(e.message))
      .finally(() => setLoading(false));
  }, []);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const r = q
      ? subnets.filter((s) =>
          s.name.toLowerCase().includes(q) || String(s.netuid) === q
        )
      : subnets;
    return [...r].sort((a, b) => b.total_stake_tao - a.total_stake_tao);
  }, [subnets, search]);

  if (loading) return <p className="text-pixel-gray">loading subnets…</p>;
  if (err) return <p className="text-red-400 font-mono text-sm">{err}</p>;

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
      {filtered.map((s) => (
        <Link
          key={s.netuid}
          href={`/subnets/${s.netuid}`}
          className="market-card group no-underline"
        >
          <div className="flex items-start justify-between mb-3">
            <div>
              <span className="text-pixel-gray text-[10px] tracking-[2px] uppercase">
                netuid
              </span>
              <h3 className="font-display text-lg font-bold text-pixel-white">
                SN{s.netuid}
              </h3>
            </div>
            <span className="pixel-badge border-green-400/30 text-green-400">
              live
            </span>
          </div>

          <p className="font-mono text-[13px] text-pixel-gray-light truncate mb-3">
            {s.name}
          </p>

          <div className="grid grid-cols-2 gap-2 text-[11px]">
            <div>
              <p className="text-pixel-gray tracking-[2px] uppercase">α/τ</p>
              <p className="font-mono text-pixel-white">
                {s.alpha_price_tao >= 1
                  ? s.alpha_price_tao.toFixed(3)
                  : s.alpha_price_tao.toFixed(6)}
              </p>
            </div>
            <div>
              <p className="text-pixel-gray tracking-[2px] uppercase">stake</p>
              <p className="font-mono text-pixel-white">
                {s.total_stake_tao >= 1000
                  ? `${(s.total_stake_tao / 1000).toFixed(2)}K`
                  : s.total_stake_tao.toFixed(2)}τ
              </p>
            </div>
            <div>
              <p className="text-pixel-gray tracking-[2px] uppercase">tempo</p>
              <p className="font-mono text-pixel-white">{s.tempo}</p>
            </div>
            <div>
              <p className="text-pixel-gray tracking-[2px] uppercase">emission</p>
              <p className="font-mono text-pixel-white">{s.emission.toFixed(4)}</p>
            </div>
          </div>
        </Link>
      ))}
    </div>
  );
}
