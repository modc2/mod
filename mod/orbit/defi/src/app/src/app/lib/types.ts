export type PortType = { label: string; color: string; iface?: string };

export type InputPort = {
  id: string;
  label: string;
  type: string;
  required?: boolean;
};

export type ParamSpec = {
  name: string;
  type: string;
  label: string;
  default: any;
  scale?: number | string;
  help?: string;
  max?: number;
};

export type BlockSpec = {
  id: string;
  contract: string;
  file: string;
  name: string;
  category: string;
  icon: string;
  summary: string;
  docs: string;
  provides: string[];
  inputs: InputPort[];
  params: ParamSpec[];
  ctor: string[];
  wires?: { when: string; method: string; note?: string }[];
  source?: string;
};

export type Template = {
  id: string;
  name: string;
  summary: string;
  nodes: { id: string; block: string; x: number; y: number; params: Record<string, any> }[];
  edges: GraphEdge[];
};

export type Catalog = {
  version: string;
  protocol: string;
  portTypes: Record<string, PortType>;
  blocks: BlockSpec[];
  templates: Template[];
};

export type GraphNode = {
  id: string;
  block: string;
  x: number;
  y: number;
  label?: string;
  params: Record<string, any>;
};

export type GraphEdge = { from: string; to: string; port: string };

export type Graph = {
  name: string;
  description: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
};

export type Issue = {
  level: "error" | "warning";
  node: string | null;
  port: string | null;
  message: string;
};

export type Report = {
  ok: boolean;
  issues: Issue[];
  order: string[];
  stats: any;
};

export type PlanArg =
  | { kind: "value"; value: any; type: string }
  | { kind: "ref"; node: string }
  | { kind: "owner" };

export type PlanStep =
  | {
      kind: "deploy";
      node: string;
      block: string;
      contract: string;
      label: string;
      abi: any[];
      bytecode: string;
      args: PlanArg[];
    }
  | {
      kind: "call";
      node: string;
      target: PlanArg;
      method: string;
      args: PlanArg[];
      note: string;
    };

export type Plan = {
  name: string;
  order: string[];
  steps: PlanStep[];
  warnings: string[];
};

export type Prompt = {
  id: string;
  name: string;
  description: string;
  text: string;
  tags: string[];
  cid?: string | null;
  owner?: string | null;
  builtin?: boolean;
};

export type Protocol = {
  id: string;
  name: string;
  description: string;
  owner: string;
  created: number;
  updated: number;
  graph: Graph;
  cid?: string | null;
  deployments: {
    chainId: number;
    network: string;
    at: number;
    deployer: string;
    addresses: Record<string, string>;
    txs: string[];
  }[];
};

export const emptyGraph = (): Graph => ({
  name: "Untitled protocol",
  description: "",
  nodes: [],
  edges: [],
});
