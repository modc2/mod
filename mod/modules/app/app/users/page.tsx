'use client'

import { useEffect, useState } from 'react'
import { Loading } from '@/app/block/Loading'
import { ModuleType } from '@/app/block/types/mod'
import { Footer } from '@/app/block/Footer'
import { useSearchContext } from '@/app/block/context/SearchContext'
import { useUserContext } from '@/app/block/context/UserContext'

interface UserType {
  key: string
  mods: ModuleType[]
  balance: number
}

interface UsersState {
  users: UserType[]
  n: number
  loading: boolean
  error: string | null
}

function UserCard({ user }: { user: UserType }) {
  return (
    <div className="group relative w-full max-w-3xl mx-auto overflow-hidden rounded-2xl border border-green-500/20 bg-gradient-to-b from-zinc-950 to-black p-6 shadow-[0_0_25px_rgba(0,255,120,0.08)] hover:border-green-400/40 hover:shadow-[0_0_35px_rgba(0,255,120,0.2)] transition-all duration-500">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-2xl font-semibold text-green-400 tracking-tight truncate">{user.key}</h2>
        <span className="text-sm font-medium text-green-300/80 bg-green-900/30 px-3 py-1 rounded-lg border border-green-800/40">
          {user.balance}Ξ
        </span>
      </div>

      <div className="border-t border-green-900/40 my-4" />

      <div className="space-y-3">
        {user.mods.length === 0 ? (
          <p className="text-green-700 text-sm italic">no mods</p>
        ) : (
          <ul className="flex flex-wrap gap-2">
            {user.mods.map((mod) => (
              <li
                key={mod.key}
                className="px-3 py-1 text-sm uppercase bg-green-900/30 text-green-300/90 rounded-lg border border-green-800/60 hover:border-green-400/70 hover:bg-green-800/40 hover:text-green-100 transition-all"
              >
                {mod.name}
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* glowing orb effect */}
      <div className="absolute -top-16 -right-16 w-48 h-48 bg-green-500/10 rounded-full blur-3xl opacity-0 group-hover:opacity-20 transition" />
    </div>
  )
}

export default function Users() {
  const { keyInstance, client } = useUserContext()
  const { searchFilters } = useSearchContext()

  const [state, setState] = useState<UsersState>({
    users: [],
    n: 0,
    loading: false,
    error: null,
  })

  const page = searchFilters.page || 1
  const pageSize = searchFilters.pageSize || 20

  const fetchUsers = async () => {
    setState((p) => ({ ...p, loading: true, error: null }))
    try {
      if (!keyInstance) throw new Error('No key instance available')
      const params: any = {
        page,
        page_size: pageSize,
        ...(searchFilters.searchTerm ? { search: searchFilters.searchTerm } : {}),
      }
      const usersData: UserType[] = await client.call('users', params)
      setState({ users: usersData, n: usersData.length, loading: false, error: null })
    } catch (err: any) {
      setState({
        users: [],
        n: 0,
        loading: false,
        error: err.message || 'Failed to fetch users',
      })
    }
  }

  useEffect(() => {
    if (keyInstance) fetchUsers()
  }, [searchFilters.searchTerm, page, pageSize, keyInstance])

  return (
    <div className="min-h-screen bg-gradient-to-b from-black via-zinc-950 to-black text-green-400 font-mono flex flex-col items-center">
      {/* error message */}
      {state.error && (
        <div className="w-full max-w-2xl m-4 p-3 border border-red-500 bg-red-900/20 text-red-400 text-center rounded-lg">
          ERROR: {state.error}
        </div>
      )}

      <main className="w-full flex-1 px-4 py-10 flex flex-col items-center space-y-6">
        {state.loading ? (
          <div className="py-20 text-center text-green-400">
            <Loading />
          </div>
        ) : state.users.length === 0 ? (
          <div className="py-20 text-center text-green-700 text-sm">
            {searchFilters.searchTerm ? 'NO USERS MATCH.' : 'NO USERS FOUND.'}
          </div>
        ) : (
          state.users.map((u) => <UserCard key={u.key} user={u} />)
        )}
      </main>

      <Footer />
    </div>
  )
}
