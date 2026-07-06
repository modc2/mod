// Polymarket on-chain contracts (Polygon, chainId 137). Sourced from
// docs.polymarket.com and the @polymarket/clob-client repo. Two exchange
// flavors:
//   - CTF Exchange     — binary / standard ConditionalTokens markets
//   - NegRisk Exchange — multi-outcome "negative risk" markets, plus its
//                        own Adapter contract that routes USDC for splits.
// Trading requires the relevant exchange (and the negRisk Adapter for those
// markets) to be approved to spend USDC.e and to manage CTF outcome shares.

export const POLYGON_CHAIN_ID = 137;

export const USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174";
export const CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045";

export const CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E";
export const NEG_RISK_CTF_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a";
export const NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296";

export interface ExchangeContracts {
  exchange: string;
  /** EIP-712 domain name used when signing orders against this exchange. */
  domainName: string;
}

export const STANDARD_EXCHANGE: ExchangeContracts = {
  exchange: CTF_EXCHANGE,
  domainName: "Polymarket CTF Exchange",
};

export const NEG_RISK_EXCHANGE: ExchangeContracts = {
  exchange: NEG_RISK_CTF_EXCHANGE,
  domainName: "Polymarket CTF Exchange",
};

export function exchangeFor(negRisk: boolean): ExchangeContracts {
  return negRisk ? NEG_RISK_EXCHANGE : STANDARD_EXCHANGE;
}

export const ERC20_ABI = [
  "function allowance(address owner, address spender) view returns (uint256)",
  "function approve(address spender, uint256 amount) returns (bool)",
];

export const CTF_ABI = [
  "function isApprovedForAll(address owner, address operator) view returns (bool)",
  "function setApprovalForAll(address operator, bool approved)",
];

// All addresses that need approval to operate on the user's USDC + CTF
// shares. The negRisk Adapter is what actually pulls USDC for splits/merges
// on multi-outcome markets, so it needs both USDC + CTF approval too.
export const SPENDERS_USDC = [CTF_EXCHANGE, NEG_RISK_CTF_EXCHANGE, NEG_RISK_ADAPTER];
export const SPENDERS_CTF = [CTF_EXCHANGE, NEG_RISK_CTF_EXCHANGE, NEG_RISK_ADAPTER];
