'use client'

import { useState, useEffect } from 'react'
import { useUserContext } from '@/bloc/context'
import { ModuleType } from '@/bloc/types'
import { Clock, ArrowUpRight, ArrowDownLeft, Hash } from 'lucide-react'
import { CopyButton } from '@/bloc/ui/CopyButton'

interface ModTransactionsProps {
  mod: ModuleType
}

interface Transaction {
  hash: string
  from: string
  to: string
  amount: string
  timestamp: number
  type: 'in' | 'out'
}

const ui = {
  bg: '#0b0b0b',
  panel: '#121212',
  panelAlt: '#151515',
  border: '#2a2a2a',
  text: '#e7e7e7',
  textDim: '#a8a8a8',
  green: '#22c55e',
  red: '#ef4444',
}

export default function ModTransactions({ mod }: ModTransactionsProps) {
  const { client } = useUserContext()
  const [transactions, setTransactions] = useState<Transaction[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const fetchTransactions = async () => {
      if (!client || !mod.key) return
      setLoading(true)
      setError(null)
      try {
        const txs = await client.call('txs', { key: mod.key })
        setTransactions(txs || [])
      } catch (err: any) {
        console.error('Failed to fetch transactions:', err)
        setError(err?.message || 'Failed to load transactions')
      } finally {
        setLoading(false)
      }
    }
    fetchTransactions()
  }, [client, mod.key])

  if (loading) {
    return (
      <div className="flex items-center justify-center p-12" style={{ backgroundColor: ui.panel }}>
        <div className="animate-spin rounded-full h-8 w-8 border-2 border-t-transparent" style={{ borderColor: ui.green }} />
      </div>
    )
  }

  if (error) {
    return (
      <div className="p-6 rounded-xl border-2" style={{ backgroundColor: ui.panel, borderColor: ui.red }}>
        <p className="text-red-400 font-mono">{error}</p>
      </div>
    )
  }

  return (
    <div className="space-y-4" style={{ backgroundColor: ui.bg }}>
      <div className="p-4 rounded-xl border" style={{ backgroundColor: ui.panel, borderColor: ui.border }}>
        <h3 className="text-xl font-bold mb-4" style={{ color: ui.text }}>Transactions</h3>
        {transactions.length === 0 ? (
          <p className="text-center py-8" style={{ color: ui.textDim }}>No transactions found</p>
        ) : (
          <div className="space-y-2">
            {transactions.map((tx, idx) => (
              <div
                key={idx}
                className="p-4 rounded-lg border flex items-center justify-between hover:bg-opacity-80 transition-all"
                style={{ backgroundColor: ui.panelAlt, borderColor: ui.border }}
              >
                <div className="flex items-center gap-3">
                  {tx.type === 'in' ? (
                    <ArrowDownLeft className="w-5 h-5" style={{ color: ui.green }} />
                  ) : (
                    <ArrowUpRight className="w-5 h-5" style={{ color: ui.red }} />
                  )}
                  <div>
                    <div className="flex items-center gap-2">
                      <Hash className="w-4 h-4" style={{ color: ui.textDim }} />
                      <code className="text-sm font-mono" style={{ color: ui.text }}>
                        {tx.hash.slice(0, 16)}...{tx.hash.slice(-8)}
                      </code>
                      <CopyButton text={tx.hash} size="sm" />
                    </div>
                    <div className="flex items-center gap-2 mt-1">
                      <Clock className="w-3 h-3" style={{ color: ui.textDim }} />
                      <span className="text-xs" style={{ color: ui.textDim }}>
                        {new Date(tx.timestamp).toLocaleString()}
                      </span>
                    </div>
                  </div>
                </div>
                <div className="text-right">
                  <div className="font-bold" style={{ color: tx.type === 'in' ? ui.green : ui.red }}>
                    {tx.type === 'in' ? '+' : '-'}{tx.amount}
                  </div>
                  <div className="text-xs" style={{ color: ui.textDim }}>
                    {tx.type === 'in' ? 'From' : 'To'}: {tx.type === 'in' ? tx.from.slice(0, 8) : tx.to.slice(0, 8)}...
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
