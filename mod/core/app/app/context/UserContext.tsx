"use client";

import React, { createContext, useContext, useEffect, useState } from 'react'
import { cryptoWaitReady } from '@polkadot/util-crypto'
import Client from '@/client'
import { Auth } from '@/client/auth'
import { UserType } from '@/types'
import { Network } from '@/network/network'
import { TokenExpiryHandler } from '@/client/tokenExpiry'

interface UserContextType {
  user: UserType | null
  signIn: () => Promise<void>
  signOut: () => void
  switchWallet: (address: string, mode: string, type: string) => Promise<void>
  network: Network | null
  authLoading: boolean
  client: Client | null
  connectClient: () => Promise<void>
  balances: (token?: string) => Promise<Record<string, number>>
}

// Token cache structure for storing tokens per wallet address
interface TokenCacheEntry {
  token: string
  timestamp: number
  mode: string
  type: string
}

const UserContext = createContext<UserContextType | null>(null)

export const UserProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<UserType | null>(null)
  const [client, setClient] = useState<Client | null>(null)
  const [network, setNetwork] = useState<Network | null>(null)
  const [authLoading, setAuthLoading] = useState(false)
  const [expiryHandler, setExpiryHandler] = useState<TokenExpiryHandler | null>(null)

  // Token cache: Maps wallet address (lowercase) to token cache entry
  const tokenCacheRef = React.useRef<Map<string, TokenCacheEntry>>(new Map())

  // Token validity duration in seconds (1 hour minus buffer for safety)
  const TOKEN_VALIDITY_DURATION = 3600 - 60 // 59 minutes

  /**
   * Helper function to get or generate token with caching
   */
  const getOrGenerateToken = async (
    address: string,
    mode: string,
    type: string
  ): Promise<string> => {
    const cacheKey = address.toLowerCase()
    const cached = tokenCacheRef.current.get(cacheKey)

    // Check if cached token is still valid
    if (cached) {
      const age = (Date.now() / 1000) - cached.timestamp
      if (
        age < TOKEN_VALIDITY_DURATION &&
        cached.mode === mode &&
        cached.type === type
      ) {
        console.log(`Using cached token for ${address}`)
        return cached.token
      }
    }

    // Generate new token
    console.log(`Generating new token for ${address}`)
    const auth = new Auth()
    const token = await auth.token('', address, mode)

    // Cache the token
    tokenCacheRef.current.set(cacheKey, {
      token,
      timestamp: Date.now() / 1000,
      mode,
      type,
    })

    return token
  }

  /**
   * Client-only initialization
   */
  useEffect(() => {
    let cancelled = false

    const init = async () => {
      try {
        setAuthLoading(true)

        // Guard: browser only
        if (typeof window === 'undefined') return

        await cryptoWaitReady()
        if (cancelled) return

        // setNetwork(new Network('test')) // Disabled to prevent WebSocket connection errors

        const storedUser = localStorage.getItem('user_data')
        const walletMode = localStorage.getItem('wallet_mode')
        const walletAddress = localStorage.getItem('wallet_address')
        const walletType = localStorage.getItem('wallet_type') || 'ecdsa'
        let walletToken = localStorage.getItem('wallet_token')

        if (storedUser && walletMode && walletAddress) {
          const parsedUser = JSON.parse(storedUser) as UserType
          setUser(parsedUser)

          // Validate stored token expiry before using it
          let tokenValid = false
          if (walletToken) {
            try {
              const auth = new Auth()
              const tokenData = auth.token2data(walletToken)
              const age = Math.abs(Date.now() / 1000 - parseFloat(tokenData.time))
              tokenValid = age < TOKEN_VALIDITY_DURATION
            } catch {
              tokenValid = false
            }
          }

          if (!tokenValid) {
            walletToken = await getOrGenerateToken(walletAddress, walletMode, walletType)
            localStorage.setItem('wallet_token', walletToken)
          } else {
            // Cache the still-valid token
            tokenCacheRef.current.set(walletAddress.toLowerCase(), {
              token: walletToken!,
              timestamp: Date.now() / 1000,
              mode: walletMode,
              type: walletType,
            })
          }

          if (!cancelled) {
            setClient(new Client(undefined, walletToken || undefined))
          }
        }
      } catch (err) {
        console.error('Auth restore failed:', err)
        localStorage.removeItem('user_data')
        localStorage.removeItem('wallet_token')
      } finally {
        if (!cancelled) setAuthLoading(false)
      }
    }

    init()
    return () => {
      cancelled = true
    }
  }, [])

  /**
   * Token expiry monitoring - starts when user is authenticated
   */
  useEffect(() => {
    if (!user || !client) {
      // Stop monitoring if user logs out
      if (expiryHandler) {
        expiryHandler.stopMonitoring()
        setExpiryHandler(null)
      }
      return
    }

    // Expose client globally for token refresh handler to update
    if (typeof window !== 'undefined') {
      (window as any).__userContextClient = client
    }

    // Initialize token expiry handler
    const auth = new Auth()
    const handler = new TokenExpiryHandler(
      auth,
      () => {
        // Callback when token expires and auto-refresh fails
        // No longer showing modal - user gets notification instead
        console.log('Token expiry detected - notification shown to user')
      },
      60000 // Check every minute
    )

    handler.startMonitoring()
    setExpiryHandler(handler)

    return () => {
      handler.stopMonitoring()
      if (typeof window !== 'undefined') {
        delete (window as any).__userContextClient
      }
    }
  }, [user, client])

  const connectClient = async () => {
    if (typeof window === 'undefined') return

    await cryptoWaitReady()

    const walletAddress = localStorage.getItem('wallet_address')
    const walletMode = localStorage.getItem('wallet_mode') || 'local'
    const walletType = localStorage.getItem('wallet_type') || 'ecdsa'

    if (!walletAddress) {
      throw new Error('Wallet address not found')
    }

    const token = await getOrGenerateToken(walletAddress, walletMode, walletType)

    localStorage.setItem('wallet_token', token)
    setClient(new Client(undefined, token))
  }

  const signIn = async () => {
    if (typeof window === 'undefined') return

    await cryptoWaitReady()

    const walletMode = localStorage.getItem('wallet_mode') || 'local'
    const walletAddress = localStorage.getItem('wallet_address')
    const walletType = localStorage.getItem('wallet_type') || 'ecdsa'

    if (!walletAddress) {
      throw new Error('Wallet address missing')
    }

    const token = await getOrGenerateToken(walletAddress, walletMode, walletType)

    localStorage.setItem('wallet_token', token)

    const userData: UserType = {
      key: walletAddress,
      crypto_type: walletType,
      wallet_mode: walletMode,
      token,
      balance: 0,
    }

    setUser(userData)
    setClient(new Client(undefined, token))

    localStorage.setItem('user_data', JSON.stringify(userData))

    // Save password per-wallet for local mode switching
    if (walletMode === 'local') {
      const pw = localStorage.getItem('wallet_password')
      if (pw) localStorage.setItem(`wallet_pw_${walletAddress}`, pw)
    }

    // Save to wallet history
    try {
      const history: { address: string; mode: string; type: string; lastUsed: number }[] = JSON.parse(localStorage.getItem('wallet_history') || '[]')
      const existing = history.findIndex(w => w.address.toLowerCase() === walletAddress.toLowerCase())
      const entry = { address: walletAddress, mode: walletMode, type: walletType, lastUsed: Date.now() }
      if (existing >= 0) {
        history[existing] = entry
      } else {
        history.unshift(entry)
      }
      // Keep max 10
      localStorage.setItem('wallet_history', JSON.stringify(history.slice(0, 10)))
    } catch {}
  }

  const switchWallet = async (address: string, mode: string, type: string) => {
    if (typeof window === 'undefined') return

    await cryptoWaitReady()

    localStorage.setItem('wallet_mode', mode)
    localStorage.setItem('wallet_address', address)
    localStorage.setItem('wallet_type', type)

    // For local mode, we need the password — if not available, throw
    if (mode === 'local') {
      // Try to get password from history storage
      const savedPw = localStorage.getItem(`wallet_pw_${address}`)
      if (savedPw) {
        localStorage.setItem('wallet_password', savedPw)
      }
    }

    // Use cached token if available, otherwise generate new one
    const token = await getOrGenerateToken(address, mode, type)
    localStorage.setItem('wallet_token', token)

    const userData: UserType = {
      key: address,
      crypto_type: type,
      wallet_mode: mode,
      token,
      balance: 0,
    }

    setUser(userData)
    setClient(new Client(undefined, token))
    localStorage.setItem('user_data', JSON.stringify(userData))

    // Update history lastUsed
    try {
      const history: { address: string; mode: string; type: string; lastUsed: number }[] = JSON.parse(localStorage.getItem('wallet_history') || '[]')
      const idx = history.findIndex(w => w.address.toLowerCase() === address.toLowerCase())
      if (idx >= 0) {
        history[idx].lastUsed = Date.now()
        localStorage.setItem('wallet_history', JSON.stringify(history))
      }
    } catch {}
  }

  const signOut = () => {
    setUser(null)
    setClient(null)

    // Clear token cache
    tokenCacheRef.current.clear()

    if (typeof window !== 'undefined') {
      localStorage.removeItem('wallet_mode')
      localStorage.removeItem('wallet_password')
      localStorage.removeItem('wallet_address')
      localStorage.removeItem('wallet_token')
      localStorage.removeItem('user_data')
    }
  }

  const balances = async (token: string = 'market'): Promise<Record<string, number>> => {
    if (!client) {
      throw new Error('Client not initialized')
    }

    try {
      console.log(`Fetching all balances for token: ${token}`)

      // Call backend API to get all balances for the token
      const result = await client.call('balances', { token })

      if (result && typeof result === 'object') {
        return result as Record<string, number>
      }

      throw new Error('Invalid response format from balances API')
    } catch (err) {
      console.error(`Failed to get balances for token ${token}:`, err)
      throw err
    }
  }

  return (
    <UserContext.Provider
      value={{
        user,
        signIn,
        signOut,
        switchWallet,
        authLoading,
        client,
        network,
        connectClient,
        balances,
      }}
    >
      {children}
    </UserContext.Provider>
  )
}

export const userContext = () => {
  const ctx = useContext(UserContext)
  if (!ctx) {
    throw new Error('userContext must be used within UserProvider')
  }
  return ctx
}
