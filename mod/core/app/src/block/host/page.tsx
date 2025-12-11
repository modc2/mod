'use client'

import { useState, useEffect } from 'react'
import { useUserContext } from '@/block/context/UserContext'
import ModCard from '@/block/mod/explore/ModCard'
import { ModuleType } from '@/block/types'

export default function NodeMarketplace() {
  const [apiModules, setApiModules] = useState<ModuleType[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const { client, connectClient } = useUserContext()

  useEffect(() => {
    fetchApiModules()
  }, [])

  const fetchApiModules = async () => {
    try {
      setLoading(true)
      setError(null)
      if (!client) {
        await connectClient()
      }
      const response = await client.call('mods', { search: 'api' })
      const modules = Array.isArray(response) ? response : []
      setApiModules(modules)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch API modules')
      console.error('Error fetching API modules:', err)
    } finally {
      setLoading(false)
    }
  }

  const getCurrentNodeUrl = () => {
    return localStorage.getItem('custom_node_url') || process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-900 via-black to-gray-900 p-8">
      <div className="max-w-7xl mx-auto">
        <div className="mb-8">
          <h1 className="text-4xl font-bold text-white mb-2">Nodes</h1>
          <p className="text-gray-400">Browse and connect to API backend modules</p>
          <div className="mt-4 p-4 bg-green-500/10 border border-green-500/30 rounded-lg">
            <p className="text-sm text-green-400">
              <span className="font-semibold">Current Backend:</span> {getCurrentNodeUrl()}
            </p>
          </div>
        </div>

        {loading && (
          <div className="flex items-center justify-center py-12">
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-green-500"></div>
          </div>
        )}

        {error && (
          <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-4 mb-6">
            <p className="text-red-400">{error}</p>
            <button
              onClick={fetchApiModules}
              className="mt-2 px-4 py-2 bg-red-500/20 hover:bg-red-500/30 text-red-400 rounded-md transition-colors"
            >
              Retry
            </button>
          </div>
        )}

        {!loading && !error && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {apiModules.length === 0 ? (
              <div className="col-span-full text-center py-12">
                <p className="text-gray-400">No API modules found</p>
              </div>
            ) : (
              apiModules.map((module) => (
                <div key={module.cid || module.name}>
                  <ModCard mod={module} card_enabled={false} />
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  )
}
