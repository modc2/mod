export type Chain = 'base' | 'tao' | 'solana'

export interface MultisigWallet {
  id: string
  chain: Chain
  name: string
  owners: string[]
  threshold: number
  address: string | null
  safe_version: string | null
  created_at: string
}

export interface Transaction {
  id: string
  multisig_id: string
  chain: Chain
  to: string
  value: string
  data: string
  description: string
  nonce: number
  call_hash: string | null
  approvals: Approval[]
  status: 'pending' | 'approved' | 'executed' | 'failed' | 'cancelled'
  tx_hash: string | null
  created_at: string
}

export interface Approval {
  owner: string
  signature: string
  approved_at: string
}

export interface ApiResponse<T = any> {
  ok: boolean
  data?: T
  error?: string
}
