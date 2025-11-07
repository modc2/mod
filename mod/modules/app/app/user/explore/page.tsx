"use client"

import { useEffect, useState } from 'react'
import { Loading } from '@/app/block/Loading'
import { Footer } from '@/app/block/footer/Footer'
import { useSearchContext } from '@/app/block/context/SearchContext'
import { useUserContext } from '@/app/block/context/UserContext'
import { UsersState, UserType } from '@/app/types'
import { UserCard } from './UserCard'
import { UserSettings } from '@/app/block/settings/UserSettings'
import { Users as UsersIcon, AlertCircle, Sparkles, ChevronDown, ChevronUp, LayoutGrid , Zap, Coins} from 'lucide-react'

type SortKey = 'mods' | 'balance'

export default function Users() {
  const { keyInstance, client } = useUserContext()
  const { searchFilters } = useSearchContext()
  const [columns, setColumns] = useState(3)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [sort, setSort] = useState<SortKey>('mods')
  const [userSettings, setUserSettings] = useState({ theme: 'dark', notifications: true, language: 'en', privacy: 'public' })

  const sortUsers = (list: UserType[]) => {
    switch (sort) {
      case 'mods':
        return [...list].sort((a, b) => (b.mods?.length || 0) - (a.mods?.length || 0))
      case 'balance':
        return [...list].sort((a, b) => (b.balance || 0) - (a.balance || 0))
    }
  }

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
      let usersData: UserType[] = await client.call('users', params)
      setState({ users: sortUsers(usersData), n: usersData.length, loading: false, error: null })
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
  }, [searchFilters.searchTerm, page, pageSize, client, sort])

  const gridColsClass = {
    1: 'grid-cols-1',
    2: 'grid-cols-1 md:grid-cols-2',
    3: 'grid-cols-1 md:grid-cols-2 lg:grid-cols-3',
    4: 'grid-cols-1 md:grid-cols-2 lg:grid-cols-4'
  }[columns] || 'grid-cols-1 md:grid-cols-2 lg:grid-cols-3'

  return (
    <div className="min-h-screen bg-gradient-to-br from-black via-gray-900 to-black text-white flex flex-col">
      <main className="flex-1 px-6 py-8">
        <div className="max-w-7xl mx-auto space-y-6">

          <div className="bg-gradient-to-r from-purple-500/10 via-pink-500/10 to-blue-500/10 border-2 border-purple-500/30 rounded-2xl shadow-2xl shadow-purple-500/20 overflow-hidden backdrop-blur-xl">
            <button
              onClick={() => setAdvancedOpen(!advancedOpen)}
              className="w-full flex items-center justify-between p-6 hover:bg-purple-500/5 transition-all group"
            >
              <div className="flex items-center gap-4">
                <div className="p-3 bg-gradient-to-br from-purple-500/30 to-pink-500/30 border-2 border-purple-400/50 rounded-xl group-hover:scale-110 group-hover:rotate-3 transition-all duration-300 shadow-lg shadow-purple-500/30">
                  <Sparkles className="text-purple-300" size={28} strokeWidth={2.5} />
                </div>
                <div className="text-left">
                  <div className="text-purple-300 font-black text-2xl uppercase tracking-wider drop-shadow-lg">SETTINGS</div>
                </div>
              </div>
              <div className="p-3 bg-gradient-to-br from-purple-500/30 to-pink-500/30 border-2 border-purple-400/50 rounded-xl group-hover:rotate-180 transition-all duration-500 shadow-lg shadow-purple-500/30">
                {advancedOpen ? <ChevronUp className="text-purple-300" size={28} strokeWidth={2.5} /> : <ChevronDown className="text-purple-300" size={28} strokeWidth={2.5} />}
              </div>
            </button>

            {advancedOpen && (
              <div className="border-t-2 border-purple-500/30 p-6 bg-black/40 backdrop-blur-sm animate-in slide-in-from-top duration-300">
                <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-8">
                  <div className="flex-1 w-full">
                    <div className="text-purple-300 font-black text-sm uppercase tracking-wider mb-4 flex items-center gap-2">
                      <Zap className="text-purple-400" size={18} strokeWidth={2.5} />
                      SORT BY
                    </div>
                    <div className="flex flex-wrap gap-3">
                      {(['mods', 'balance'] as SortKey[]).map((s) => (
                        <button
                          key={s}
                          onClick={() => setSort(s)}
                          className={`px-6 py-3 rounded-xl font-black text-base uppercase transition-all duration-300 flex items-center gap-2 ${
                            sort === s
                              ? 'bg-gradient-to-r from-purple-500 to-pink-500 text-white border-2 border-purple-300 shadow-2xl shadow-purple-500/50 scale-110'
                              : 'bg-purple-500/20 text-purple-300 border-2 border-purple-500/40 hover:bg-purple-500/30 hover:scale-105 hover:border-purple-400/60'
                          }`}
                        >
                          {s === 'balance' && <Coins size={18} strokeWidth={2.5} />}
                          {s}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div className="flex-1 w-full">
                    <div className="text-blue-300 font-black text-sm uppercase tracking-wider mb-4 flex items-center gap-2">
                      <Zap className="text-blue-400" size={18} strokeWidth={2.5} />
                      COLUMNS
                    </div>
                    <div className="flex flex-wrap gap-3">
                      {[1, 2, 3, 4].map((num) => (
                        <button
                          key={num}
                          onClick={() => setColumns(num)}
                          className={`w-14 h-14 rounded-xl font-black text-2xl transition-all duration-300 ${
                            columns === num
                              ? 'bg-gradient-to-br from-blue-500 to-cyan-500 text-white border-2 border-blue-300 shadow-2xl shadow-blue-500/50 scale-110'
                              : 'bg-blue-500/20 text-blue-300 border-2 border-blue-500/40 hover:bg-blue-500/30 hover:scale-105 hover:border-blue-400/60'
                          }`}
                        >
                          {num}
                        </button>
                      ))}
                    </div>
                  </div>

                </div>
              </div>
            )}
          </div>

          {state.error && (
            <div className="flex items-start gap-4 p-5 rounded-xl border-2 border-rose-500/50 bg-gradient-to-br from-rose-500/20 to-rose-600/15 backdrop-blur-xl shadow-lg shadow-rose-500/20">
              <AlertCircle className="w-8 h-8 text-rose-400 flex-shrink-0 mt-1" />
              <div className="flex-1 min-w-0">
                <h3 className="text-2xl font-bold text-rose-300 mb-1">ERROR LOADING USERS</h3>
                <p className="text-xl text-rose-200/90">{state.error}</p>
              </div>
            </div>
          )}

          {state.loading ? (
            <div className="py-24 flex flex-col items-center justify-center gap-4">
              <Loading />
              <p className="text-white/60 text-2xl font-bold">LOADING USERS...</p>
            </div>
          ) : state.users.length === 0 ? (
            <div className="py-24 flex flex-col items-center justify-center gap-4">
              <div className="w-20 h-20 rounded-2xl bg-gradient-to-br from-white/10 to-white/5 border border-white/20 flex items-center justify-center shadow-lg">
                <UsersIcon className="w-10 h-10 text-white/30" />
              </div>
              <div className="text-center space-y-2">
                <h3 className="text-3xl font-bold text-white/70">
                  {searchFilters.searchTerm ? 'NO MATCHING USERS' : 'NO USERS FOUND'}
                </h3>
                <p className="text-xl text-white/50">
                  {searchFilters.searchTerm 
                    ? 'TRY ADJUSTING YOUR SEARCH FILTERS' 
                    : 'USERS WILL APPEAR HERE ONCE THEY REGISTER'}
                </p>
              </div>
            </div>
          ) : (
            <div className={`grid ${gridColsClass} gap-6`}>
              {state.users.map((u) => (
                <div
                  key={u.key}
                  className="transform hover:scale-[1.03] transition-all duration-300 ease-out"
                >
                  <UserCard user={u} />
                </div>
              ))}
            </div>
          )}
        </div>
      </main>

      <Footer />
    </div>
  )
}