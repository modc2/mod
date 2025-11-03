"use client"

import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import { Loading } from '@/app/block/Loading'
import { Footer } from '@/app/block/Footer'
import { User } from '@/app/block/user'
import { useUserContext } from '@/app/block/context/UserContext'
import { UserType } from '@/app/types'
import { AlertCircle } from 'lucide-react'

export default function UserProfile() {
  const params = useParams()
  const userKey = params?.key as string
  const { client, keyInstance } = useUserContext()
  const [user, setUser] = useState<UserType | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const fetchUser = async () => {
      if (!userKey || !client || !keyInstance) {
        setLoading(false)
        return
      }

      setLoading(true)
      setError(null)
      
      try {
        const userData = await client.call('user', { key: userKey })
        setUser(userData)
      } catch (err: any) {
        console.error('Failed to fetch user:', err)
        setError(err.message || 'Failed to load user profile')
      } finally {
        setLoading(false)
      }
    }

    fetchUser()
  }, [userKey, client, keyInstance])

  return (
    <div className="min-h-screen bg-gradient-to-b from-black via-zinc-950 to-black text-white flex flex-col">
      {error && (
        <div className="max-w-4xl mx-auto w-full px-6 mt-8">
          <div className="flex items-start gap-4 p-6 rounded-2xl border border-rose-500/25 bg-gradient-to-br from-rose-500/15 to-rose-600/10 backdrop-blur-xl">
            <AlertCircle className="w-6 h-6 text-rose-400 flex-shrink-0 mt-0.5" />
            <div className="flex-1 min-w-0">
              <h3 className="text-lg font-semibold text-rose-300 mb-1">Error Loading Profile</h3>
              <p className="text-sm text-rose-200/80">{error}</p>
            </div>
          </div>
        </div>
      )}

      <main className="flex-1 px-6 py-12">
        <div className="max-w-4xl mx-auto">
          {loading ? (
            <div className="py-32 flex flex-col items-center justify-center gap-4">
              <Loading />
              <p className="text-white/50 text-sm">Loading user profile...</p>
            </div>
          ) : user ? (
            <User user={user} />
          ) : (
            <div className="py-32 flex flex-col items-center justify-center gap-4">
              <div className="text-center">
                <h3 className="text-xl font-semibold text-white/60 mb-2">User not found</h3>
                <p className="text-sm text-white/40">The requested user profile could not be found</p>
              </div>
            </div>
          )}
        </div>
      </main>

      <Footer />
    </div>
  )
}
