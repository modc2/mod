'use client'

import React, { useEffect, useState } from 'react'
import { Loading } from '@/app/block/ui/Loading'
import { UserCard } from './UserCard'
import { UserCardSettings } from './UserCardSettings'
import { UserType } from '@/app/types'
import { useUserContext } from '@/app/context'
import { X, RotateCcw, Users } from 'lucide-react'

type SortKey = 'recent' | 'name' | 'balance' | 'modules'

export default function UsersPage() {
  const { client } = useUserContext()
  const [users, setUsers] = useState<UserType[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [sort, setSort] = useState<SortKey>(() => {
    if (typeof window !== 'undefined') {
      return (localStorage.getItem('user_explorer_sort') as SortKey) || 'recent'
    }
    return 'recent'
  })
  const [columns, setColumns] = useState<number>(() => {
    if (typeof window !== 'undefined') {
      return parseInt(localStorage.getItem('user_explorer_columns') || '2')
    }
    return 2
  })

  useEffect(() => {
    if (typeof window !== 'undefined') {
      localStorage.setItem('user_explorer_sort', sort)
    }
  }, [sort])

  useEffect(() => {
    if (typeof window !== 'undefined') {
      localStorage.setItem('user_explorer_columns', columns.toString())
    }
  }, [columns])

  const sortUsers = (list: UserType[]) => {
    switch (sort) {
      case 'balance':
        return [...list].sort((a, b) => (b.balance || 0) - (a.balance || 0))
      case 'modules':
        return [...list].sort((a, b) => (b.mods?.length || 0) - (a.mods?.length || 0))
      default:
        return [...list].sort((a, b) => (b.balance || 0) - (a.balance || 0))
    }
  }

  const fetchAll = async () => {
    setLoading(true)
    setError(null)
    try {
      if (!client) throw new Error('Client not initialized')
      const raw = (await client.call('users', {})) as UserType[]
      const allUsers = Array.isArray(raw) ? raw : []
      const sorted = sortUsers(allUsers)
      setUsers(sorted)
    } catch (err: any) {
      console.error('Error fetching users:', err)
      setError(err?.message || 'Failed to load users')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchAll()
  }, [client, sort])

  const gridColsClass = {
    1: 'grid-cols-1',
    2: 'grid-cols-1 md:grid-cols-2',
    3: 'grid-cols-1 md:grid-cols-2 lg:grid-cols-3',
    4: 'grid-cols-1 md:grid-cols-2 lg:grid-cols-4'
  }[columns] || 'grid-cols-1 md:grid-cols-2'

  return (
    <div className="min-h-screen bg-gradient-to-br from-black via-gray-900 to-black text-white">
      <main className="flex-1 px-6 pt-0 pb-0" role="main">
        <div className="mx-auto max-w-7xl mb-4">
          <UserCardSettings
            sort={sort}
            onSortChange={setSort}
            columns={columns}
            onColumnsChange={setColumns}
          />
        </div>

        {error && (
          <div className="mx-auto max-w-7xl mb-4">
            <div className="p-4 border-2 border-red-500/60 bg-gradient-to-r from-red-500/20 to-pink-500/20 rounded-xl flex items-start justify-between backdrop-blur-xl shadow-lg">
              <div className="flex-1">
                <div className="text-red-300 font-bold mb-1 text-lg uppercase tracking-wide">ERROR</div>
                <div className="text-red-200/90 text-sm font-medium">{error}</div>
              </div>
              <div className="flex gap-2 ml-4">
                <button
                  onClick={fetchAll}
                  className="flex items-center gap-2 px-4 py-2 border border-red-400/60 rounded-lg text-red-300 hover:bg-red-500/30 transition-all font-bold text-sm uppercase"
                >
                  <RotateCcw size={16} strokeWidth={2.5} />
                  RETRY
                </button>
                <button
                  onClick={() => setError(null)}
                  className="p-2 border border-red-400/60 rounded-lg text-red-300 hover:bg-red-500/30 transition-all"
                >
                  <X size={16} strokeWidth={2.5} />
                </button>
              </div>
            </div>
          </div>
        )}

        {!loading && users.length === 0 && !error && (
          <div className="mx-auto max-w-4xl text-center py-12">
            <div className="mb-6 inline-block p-6 bg-gradient-to-br from-purple-500/20 via-pink-500/20 to-blue-500/20 rounded-2xl border-2 border-purple-500/40 shadow-xl backdrop-blur-xl">
              <Users className="w-16 h-16 text-purple-300" strokeWidth={2} />
            </div>
            <div className="text-purple-300 text-3xl mb-6 font-black uppercase tracking-wide">
              NO USERS YET
            </div>
          </div>
        )}

        {loading && (
          <div className="flex justify-center py-12">
            <Loading />
          </div>
        )}

        <div className={`mx-auto max-w-7xl grid ${gridColsClass} gap-6`}>
          {users.map((user) => (
            <div
              key={user.key}
              className="transform hover:scale-[1.02] transition-all duration-300 ease-out"
            >
              <UserCard user={user} />
            </div>
          ))}
        </div>
      </main>

    </div>
  )
}