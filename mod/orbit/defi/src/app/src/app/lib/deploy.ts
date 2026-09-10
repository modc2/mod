"use client";

import type { Plan, PlanArg, PlanStep } from "./types";

export type StepState = {
  index: number;
  status: "pending" | "running" | "done" | "failed";
  label: string;
  address?: string;
  tx?: string;
  error?: string;
};

/// Execute a deployment plan with the browser wallet.
///
/// The server never signs anything — it hands over bytecode, an ABI, and args
/// where "the address of node n2" is still a placeholder. Substituting those
/// placeholders is this function's whole job, and it happens after each deploy
/// confirms, so a plan can wire contracts that did not exist when it was built.
export async function runPlan(
  plan: Plan,
  onUpdate: (steps: StepState[]) => void
): Promise<{ addresses: Record<string, string>; txs: string[]; failed: boolean }> {
  const { BrowserProvider, ContractFactory, Contract } = await import("ethers");
  const ethereum = (window as any).ethereum;
  if (!ethereum) throw new Error("no browser wallet found");

  const provider = new BrowserProvider(ethereum);
  await provider.send("eth_requestAccounts", []);
  const signer = await provider.getSigner();
  const owner = await signer.getAddress();

  const addresses: Record<string, string> = {};
  const txs: string[] = [];
  const steps: StepState[] = plan.steps.map((step, index) => ({
    index,
    status: "pending",
    label: describe(step),
  }));
  const push = () => onUpdate(steps.map((s) => ({ ...s })));
  push();

  const resolve = (arg: PlanArg): any => {
    if (arg.kind === "owner") return owner;
    if (arg.kind === "ref") {
      const address = addresses[arg.node];
      if (!address) throw new Error(`node ${arg.node} has no address yet`);
      return address;
    }
    return coerce(arg.value, arg.type, owner);
  };

  for (let i = 0; i < plan.steps.length; i++) {
    const step = plan.steps[i];
    steps[i].status = "running";
    push();
    try {
      if (step.kind === "deploy") {
        const factory = new ContractFactory(step.abi, step.bytecode, signer);
        const contract = await factory.deploy(...step.args.map(resolve));
        const tx = contract.deploymentTransaction();
        if (tx?.hash) {
          steps[i].tx = tx.hash;
          txs.push(tx.hash);
        }
        await contract.waitForDeployment();
        const address = await contract.getAddress();
        addresses[step.node] = address;
        steps[i].address = address;
      } else {
        const target = resolve(step.target);
        const contract = new Contract(target, [`function ${step.method}`], signer);
        const name = step.method.slice(0, step.method.indexOf("("));
        const tx = await contract[name](...step.args.map(resolve));
        steps[i].tx = tx.hash;
        txs.push(tx.hash);
        await tx.wait();
      }
      steps[i].status = "done";
    } catch (e: any) {
      // Wallet rejections and reverts both land here; keep the reason, drop the
      // 4kb of RPC noise that usually comes with it.
      steps[i].status = "failed";
      steps[i].error = (e?.shortMessage || e?.reason || e?.message || "failed").slice(0, 240);
      push();
      return { addresses, txs, failed: true };
    }
    push();
  }
  return { addresses, txs, failed: false };
}

function describe(step: PlanStep): string {
  return step.kind === "deploy"
    ? `deploy ${step.label} (${step.contract})`
    : `wire ${step.note}`;
}

/// Solidity types arrive as JSON. Numbers must become BigInt (a uint256 does not
/// survive a double), booleans must not arrive as the string "false", and the
/// "$owner" placeholder in address params means the connected wallet.
function coerce(value: any, type: string, owner: string): any {
  if (value === "$owner") return owner;
  if (type === "bool") return value === true || value === "true";
  if (type === "address") return String(value);
  if (type === "string") return String(value ?? "");
  if (type.startsWith("uint") || type.startsWith("int")) {
    if (typeof value === "bigint") return value;
    const text = String(value ?? "0").trim();
    if (!/^\d+$/.test(text)) {
      throw new Error(`"${text}" is not a whole number (${type})`);
    }
    return BigInt(text);
  }
  return value;
}
