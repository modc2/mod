'use client'

import { useNetwork, useSwitchNetwork } from 'wagmi'

export default function NetworkSelector() {
  const { chain } = useNetwork()
  const { chains, switchNetwork } = useSwitchNetwork()

  return (
    <div className="relative">
      <select
        value={chain?.id || ''}
        onChange={(e) => switchNetwork?.(Number(e.target.value))}
        className="bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 text-sm font-medium text-white hover:bg-gray-700 transition-colors cursor-pointer"
      >
        {chains.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
          </option>
        ))}
      </select>
    </div>
  )
}