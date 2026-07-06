"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { fetchSubnets } from "../../lib/api";
import type { SubnetInfo } from "../../lib/types";

export default function SubnetDetailPage() {
  const { netuid } = useParams<{ netuid: string }>();
  const [subnet, setSubnet] = useState<SubnetInfo | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    fetchSubnets()
      .then((all) => {
        const found = all.find((s) => String(s.netuid) === netuid);
        if (!found) setErr("subnet not found");
        setSubnet(found || null);
      })
      .catch((e) => setErr(e.message));
  }, [netuid]);

  if (err) return <p className="text-red-400 font-mono">{err}</p>;
  if (!subnet) return <p className="text-pixel-gray">loading…</p>;

  return (
    <div className="space-y-6">
      <Link href="/subnets" className="text-pixel-gray hover:text-pixel-white text-sm no-underline">
        ← all subnets
      </Link>
      <header className="pixel-panel p-6">
        <div className="flex items-baseline gap-4 mb-2">
          <h1 className="font-display text-3xl font-bold text-pixel-white">
            SN{subnet.netuid}
          </h1>
          <span className="font-mono text-pixel-gray-light">{subnet.name}</span>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-6 mt-6">
          <Stat label="alpha price" value={`${subnet.alpha_price_tao.toFixed(6)} τ/α`} />
          <Stat label="total stake" value={`${subnet.total_stake_tao.toFixed(2)} τ`} />
          <Stat label="tempo" value={String(subnet.tempo)} />
          <Stat label="emission" value={subnet.emission.toFixed(6)} />
        </div>
      </header>
      <div className="pixel-panel p-6 text-pixel-gray text-sm">
        Validator rankings + flow history for this subnet are coming soon.
        For now, use the <Link href="/leaderboard" className="text-green-400">leaderboard</Link>
        {" "}filtered by top-SN, or look up a specific validator from the search bar.
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[10px] tracking-[2px] uppercase text-pixel-gray">{label}</p>
      <p className="font-mono text-lg text-pixel-white tabular-nums">{value}</p>
    </div>
  );
}
