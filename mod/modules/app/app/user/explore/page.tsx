"use client"

import { useEffect, useState } from 'react'
import { Loading } from '@/app/block/Loading'
import { Footer } from '@/app/block/Footer'
import { useSearchContext } from '@/app/block/context/SearchContext'
import { useUserContext } from '@/app/block/context/UserContext'
import { UsersState, UserType } from '@/app/types'
import { UserCard } from './UserCard'
import { Users as UsersIcon, AlertCircle } from 'lucide-react'

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
      if (!client) throw new Error('No client available')
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
    if (client) fetchUsers()
  }, [searchFilters.searchTerm, page, pageSize, client])

  return (
    <div className="min-h-screen bg-gradient-to-b from-black via-zinc-950 to-black text-white flex flex-col">


      {/* Error message */}
      {state.error && (
        <div className="max-w-4xl mx-auto w-full px-6 mt-8">
          <div className="flex items-start gap-4 p-6 rounded-2xl border border-rose-500/25 bg-gradient-to-br from-rose-500/15 to-rose-600/10 backdrop-blur-xl">
            <AlertCircle className="w-6 h-6 text-rose-400 flex-shrink-0 mt-0.5" />
            <div className="flex-1 min-w-0">
              <h3 className="text-lg font-semibold text-rose-300 mb-1">Error Loading Users</h3>
              <p className="text-sm text-rose-200/80">{state.error}</p>
            </div>
          </div>
        </div>
      )}

      {/* Main content */}
      <main className="flex-1 px-6 py-12">
        <div className="max-w-7xl mx-auto space-y-6">
          {state.loading ? (
            <div className="py-32 flex flex-col items-center justify-center gap-4">
              <Loading />
              <p className="text-white/50 text-sm">Loading users...</p>
            </div>
          ) : state.users.length === 0 ? (
            <div className="py-32 flex flex-col items-center justify-center gap-4">
              <div className="w-16 h-16 rounded-2xl bg-white/5 flex items-center justify-center">
                <UsersIcon className="w-8 h-8 text-white/20" />
              </div>
              <div className="text-center">
                <h3 className="text-xl font-semibold text-white/60 mb-2">
                  {searchFilters.searchTerm ? 'No matching users' : 'No users found'}
                </h3>
                <p className="text-sm text-white/40">
                  {searchFilters.searchTerm 
                    ? 'Try adjusting your search filters' 
                    : 'Users will appear here once they register'}
                </p>
              </div>
            </div>
          ) : (
            state.users.map((u) => <UserCard key={u.key} user={u} />)
          )}
        </div>
      </main>

      <Footer />
    </div>
  )
}
