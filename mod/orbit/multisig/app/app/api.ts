import { API_URL } from './config'
import type { MultisigWallet, Transaction, Chain, ApiResponse } from './types'

async function request<T = any>(path: string, opts?: RequestInit): Promise<ApiResponse<T>> {
  try {
    const res = await fetch(`${API_URL}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...opts,
    })
    return await res.json()
  } catch (e: any) {
    return { ok: false, error: e.message }
  }
}

// --- Common ---

export const api = {
  health: () => request('/api/health'),

  listMultisigs: (chain?: Chain) =>
    request<MultisigWallet[]>(`/api/multisigs${chain ? `?chain=${chain}` : ''}`),

  getMultisig: (id: string) =>
    request<{ multisig: MultisigWallet; transactions: Transaction[] }>(`/api/multisigs/${id}`),

  deleteMultisig: (id: string) =>
    request(`/api/multisigs/${id}`, { method: 'DELETE' }),

  getTx: (id: string) => request<Transaction>(`/api/tx/${id}`),

  listTxs: (multisigId: string) =>
    request<Transaction[]>(`/api/multisigs/${multisigId}/txs`),

  // --- EVM (Base) ---

  evmCreateMultisig: (data: {
    name: string; owners: string[]; threshold: number; address?: string
  }) =>
    request<MultisigWallet>('/api/evm/multisig', {
      method: 'POST',
      body: JSON.stringify({ ...data, chain: 'base' }),
    }),

  evmPropose: (data: {
    multisig_id: string; to: string; value: string; data?: string;
    description?: string; proposer: string; signature?: string
  }) =>
    request<Transaction>('/api/evm/tx/propose', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  evmGetSignData: (txId: string) =>
    request(`/api/evm/tx/${txId}/sign-data`),

  evmApprove: (txId: string, owner: string, signature: string) =>
    request<Transaction>(`/api/evm/tx/${txId}/approve`, {
      method: 'POST',
      body: JSON.stringify({ owner, signature }),
    }),

  evmExecute: (txId: string, executor: string, txHash?: string) =>
    request(`/api/evm/tx/${txId}/execute`, {
      method: 'POST',
      body: JSON.stringify({ executor, tx_hash: txHash }),
    }),

  evmBalance: (address: string) =>
    request(`/api/evm/balance/${address}`),

  evmNonce: (address: string) =>
    request(`/api/evm/nonce/${address}`),

  // --- Substrate (TAO) ---

  substrateCreateMultisig: (data: {
    name: string; owners: string[]; threshold: number; address?: string
  }) =>
    request<MultisigWallet>('/api/substrate/multisig', {
      method: 'POST',
      body: JSON.stringify({ ...data, chain: 'tao' }),
    }),

  substrateDeriveAddress: (signatories: string[], threshold: number) =>
    request('/api/substrate/derive', {
      method: 'POST',
      body: JSON.stringify({ signatories, threshold }),
    }),

  substratePropose: (data: {
    multisig_id: string; to: string; value: string; data?: string;
    description?: string; proposer: string; signature?: string
  }) =>
    request<Transaction>('/api/substrate/tx/propose', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  substrateApprove: (txId: string, owner: string, signature: string) =>
    request<Transaction>(`/api/substrate/tx/${txId}/approve`, {
      method: 'POST',
      body: JSON.stringify({ owner, signature }),
    }),

  substrateExecute: (txId: string, executor: string, txHash?: string) =>
    request(`/api/substrate/tx/${txId}/execute`, {
      method: 'POST',
      body: JSON.stringify({ executor, tx_hash: txHash }),
    }),

  substrateBalance: (address: string) =>
    request(`/api/substrate/balance/${address}`),

  // --- Solana ---

  solanaCreateMultisig: (data: {
    name: string; owners: string[]; threshold: number; address?: string
  }) =>
    request<MultisigWallet>('/api/solana/multisig', {
      method: 'POST',
      body: JSON.stringify({ ...data, chain: 'solana' }),
    }),

  solanaPropose: (data: {
    multisig_id: string; to: string; value: string; data?: string;
    description?: string; proposer: string; signature?: string
  }) =>
    request<Transaction>('/api/solana/tx/propose', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  solanaApprove: (txId: string, owner: string, signature: string) =>
    request<Transaction>(`/api/solana/tx/${txId}/approve`, {
      method: 'POST',
      body: JSON.stringify({ owner, signature }),
    }),

  solanaExecute: (txId: string, executor: string, txHash?: string) =>
    request(`/api/solana/tx/${txId}/execute`, {
      method: 'POST',
      body: JSON.stringify({ executor, tx_hash: txHash }),
    }),

  solanaBalance: (address: string) =>
    request(`/api/solana/balance/${address}`),
}
