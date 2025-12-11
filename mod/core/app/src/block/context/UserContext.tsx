'use client'
import React, { createContext, useContext, useEffect, useState } from 'react'
import { Key } from '@/block/key'
import { cryptoWaitReady } from '@polkadot/util-crypto'
import  Client from '@/block/client'
import { UserType } from '@/block/types'
import { Network } from '@/block/network/network'

interface UserContextType {
  user: UserType | null
  signIn: () => Promise<void>
  signOut: () => void
  network: Network | null
  authLoading: boolean
  client: Client | null
  connectClient: () => Promise<void>
  localKey: Key | null
}

const UserContext = createContext<UserContextType | undefined>(undefined)

export const UserProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<{ key: string; crypto_type: string; balance?: number; mods?: any[] } | null>(null)
  const [password, setPassword] = useState('')
  const [authLoading, setAuthLoading] = useState(true)
  const [client, setClient] = useState<Client | null>(null)
  const [network, setNetwork] = useState<Network| null>(null)
  const [localKey, setLocalKey] = useState<Key | null>(null)


  // Initialize from localStorage on mount
  useEffect(() => {
    const initializeAuth = async () => {
      try {
        setNetwork(new Network('test'))
        const storedUser = localStorage.getItem('user_data')
        const storedPassword = localStorage.getItem('user_password') || '420'
        console.log('Restoring auth session from localStorage:', { storedUser, storedPassword })
        await cryptoWaitReady()
        const key = new Key(storedPassword)
        setLocalKey(key)
        // localStorage.setItem('wallet_mode', 'local')
        if (storedUser) {
          setUser(JSON.parse(storedUser))
        }
        setClient(new Client(undefined, key))
      } catch (error) {
        console.error('Failed to restore auth session:', error)
        localStorage.removeItem('user_data')
      } finally {
        setAuthLoading(false)
      }
    }
    
    initializeAuth()
  }, [])

  const connectClient = async () => {
    if (localKey) {
      setClient(new Client(undefined, localKey))
    } else {
      await cryptoWaitReady()
      setClient(new Client( undefined, new Key('this_is_a_shitty_password_for_the_default_local_wallet')))
    }
  }
  const signIn = async () => {
    try {
      await cryptoWaitReady()
  
      const wallet_password = localStorage.getItem('wallet_password') || 'this_is_a_shitty_password_for_the_default_local_wallet'
      const key = new Key(wallet_password)
      setLocalKey(key)

      const wallet_mode = localStorage.getItem('wallet_mode') || 'local'
      const user_key = wallet_mode === 'local' ? key.address : localStorage.getItem('wallet_address') || key.address
      let balance = 0
      if (!network) {
        throw new Error('Network not initialized')
      }      
      const userData = {
        key: user_key,
        crypto_type: key.crypto_type,
        wallet_mode: wallet_mode,
        balance: balance,
        
      }
      setUser(userData)
      setClient(new Client(undefined, key))
      
      // Persist to localStorage
      localStorage.setItem('user_data', JSON.stringify(userData))
    } catch (error) {
      console.error('Failed to sign in:', error)
      throw error
    }
  }

  const signOut = () => {
    setUser(null)
    setPassword('')
    setClient(null)
    localStorage.removeItem('wallet_mode')
    localStorage.removeItem('wallet_password')
    localStorage.removeItem('wallet_address')
    localStorage.removeItem('user_data')
  }

  return (
    <UserContext.Provider value={{ user, signIn, signOut, authLoading, client, localKey , network, connectClient }}>
      {children}
    </UserContext.Provider>
  )
}

export const useUserContext = () => {
  const context = useContext(UserContext)
  if (context === undefined) {
    throw new Error('useUserContext must be used within an UserProvider')
  }
  return context
}