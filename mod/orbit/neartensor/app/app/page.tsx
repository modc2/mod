"use client";

import { useState, useEffect, useCallback } from "react";
import Header from "@/components/Header";
import SubnetList from "@/components/SubnetList";
import ValidatorTable from "@/components/ValidatorTable";
import ConsensusPanel from "@/components/ConsensusPanel";
import StakingPanel from "@/components/StakingPanel";
import RegistryPanel from "@/components/RegistryPanel";
import { api } from "@/lib/api";
import type { SubnetInfo, ValidatorEntry, ConsensusState } from "@/lib/types";

export default function Home() {
  const [tab, setTab] = useState("subnets");
  const [subnets, setSubnets] = useState<SubnetInfo[]>([]);
  const [selectedSubnet, setSelectedSubnet] = useState(0);
  const [validators, setValidators] = useState<ValidatorEntry[]>([]);
  const [consensus, setConsensus] = useState<ConsensusState | null>(null);
  const [registrationCost, setRegistrationCost] = useState("0");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);

      // Fetch subnets from registry
      const subs = await api("neartensor/subnets");
      if (subs && !subs.error) {
        setSubnets(Array.isArray(subs) ? subs : []);
      }

      // Fetch registration cost
      const cost = await api("neartensor/status");
      if (cost && cost.registration_cost) {
        setRegistrationCost(cost.registration_cost);
      }
    } catch (e: any) {
      setError(e.message);
    }
    setLoading(false);
  }, []);

  const refreshSubnet = useCallback(async () => {
    try {
      const [vals, state] = await Promise.all([
        api("neartensor/validators", { subnet_id: selectedSubnet }),
        api("neartensor/consensus_state", { subnet_id: selectedSubnet }),
      ]);

      if (vals && !vals.error) {
        setValidators(Array.isArray(vals) ? vals : []);
      }
      if (state && !state.error) {
        setConsensus(state);
      }
    } catch (e) {
      console.error("Failed to refresh subnet:", e);
    }
  }, [selectedSubnet]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    refreshSubnet();
  }, [refreshSubnet]);

  return (
    <div className="min-h-screen">
      <Header activeTab={tab} onTabChange={setTab} />

      <main className="max-w-7xl mx-auto px-4 py-6">
        {error && (
          <div className="mb-4 p-3 rounded border border-nt-red/30 bg-nt-red/5 text-nt-red text-xs">
            {error}
          </div>
        )}

        {loading && subnets.length === 0 ? (
          <div className="text-center py-20 text-nt-muted text-sm">
            Loading...
          </div>
        ) : (
          <div className="space-y-6">
            {/* Subnets Tab */}
            {tab === "subnets" && (
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <div className="lg:col-span-1">
                  <SubnetList
                    subnets={subnets}
                    selectedSubnet={selectedSubnet}
                    onSelect={(id) => setSelectedSubnet(id)}
                  />
                </div>
                <div className="lg:col-span-2">
                  <ConsensusPanel state={consensus} />
                </div>
              </div>
            )}

            {/* Validators Tab */}
            {tab === "validators" && (
              <ValidatorTable
                validators={validators}
                subnetId={selectedSubnet}
              />
            )}

            {/* Staking Tab */}
            {tab === "staking" && (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <StakingPanel
                  subnetId={selectedSubnet}
                  onRefresh={refreshSubnet}
                />
                <ConsensusPanel state={consensus} />
              </div>
            )}

            {/* Registry Tab */}
            {tab === "registry" && (
              <RegistryPanel
                subnetCount={subnets.length}
                registrationCost={registrationCost}
              />
            )}
          </div>
        )}
      </main>

      {/* Footer */}
      <footer className="border-t border-nt-border mt-12 py-4">
        <div className="max-w-7xl mx-auto px-4 flex items-center justify-between text-[10px] text-nt-muted">
          <span>NearTensor Protocol</span>
          <span>Bittensor-inspired subnets on NEAR</span>
        </div>
      </footer>
    </div>
  );
}
