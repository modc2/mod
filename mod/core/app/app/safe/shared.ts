import { getChainConfig } from '@/network/chainConfig'

// ABI imports
import TreasuryABI from '@/contracts/treasury/Treasury.sol/Treasury.json'
import MarketABI from '@/contracts/market/Market.sol/Market.json'
import DebitABI from '@/contracts/market/debit/Debit.sol/Debit.json'
import RegistryABI from '@/contracts/registry/Registry.sol/Registry.json'
import ManualPriceOracleABI from '@/contracts/oracles/ManualPriceOracle.sol/ManualPriceOracle.json'
import TokenGateABI from '@/contracts/tokengate/TokenGate.sol/TokenGate.json'
import BlocTimeABI from '@/contracts/bloctime/BlocTime.sol/BlocTime.json'
import TokenABI from '@/contracts/token/Token.sol/Token.json'

export const ACCENT = '#10b981'

export const CONTRACT_ABIS: Record<string, any[]> = {
  Treasury: TreasuryABI.abi,
  Market: MarketABI.abi,
  Debit: DebitABI.abi,
  Registry: RegistryABI.abi,
  ManualPriceOracle: ManualPriceOracleABI.abi,
  TokenGate: TokenGateABI.abi,
  BlocTime: BlocTimeABI.abi,
  NativeToken: TokenABI.abi,
  USDC: TokenABI.abi,
  USDT: TokenABI.abi,
}

export function getWriteFunctions(abi: any[]): { name: string; inputs: any[]; outputs?: any[] }[] {
  return abi
    .filter(
      (item: any) =>
        item.type === 'function' &&
        item.stateMutability !== 'view' &&
        item.stateMutability !== 'pure'
    )
    .map((item: any) => ({
      name: item.name,
      inputs: item.inputs || [],
      outputs: item.outputs || [],
    }))
    .sort((a: any, b: any) => a.name.localeCompare(b.name))
}

export function getReadFunctions(abi: any[]): { name: string; inputs: any[]; outputs: any[] }[] {
  return abi
    .filter(
      (item: any) =>
        item.type === 'function' &&
        (item.stateMutability === 'view' || item.stateMutability === 'pure')
    )
    .map((item: any) => ({
      name: item.name,
      inputs: item.inputs || [],
      outputs: item.outputs || [],
    }))
    .sort((a: any, b: any) => a.name.localeCompare(b.name))
}

export function getContracts(): { name: string; address: string }[] {
  const chainConfig = getChainConfig()
  if (!chainConfig?.contracts) return []
  const entries = Object.entries(chainConfig.contracts) as [string, any][]
  const treasury = entries.find(([name]) => name === 'Treasury')
  const rest = entries
    .filter(([name]) => name !== 'Treasury' && name !== 'Safe')
    .sort(([a], [b]) => a.localeCompare(b))
  const ordered = treasury ? [treasury, ...rest] : rest
  return ordered.map(([name, val]) => ({ name, address: val.address }))
}

export function getSafeDeployment(): { singleton: string; factory: string } {
  const chainConfig = getChainConfig()
  const safe = chainConfig?.contracts?.Safe
  return {
    singleton: safe?.singleton || '',
    factory: safe?.factory || '',
  }
}
