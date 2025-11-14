'use client'
import React, { createContext, useContext, useEffect, useState } from 'react'
import { Key } from '@/app/block/key'
import { cryptoWaitReady } from '@polkadot/util-crypto'
import { Client } from '@/app/block/client/client'

interface UserContextType {
  user: { address: string; crypto_type: string; balance?: number; mods?: any[] } | null
  signIn: () => Promise<void>
  signOut: () => void
  authLoading: boolean
  client: Client | null
  localKey: Key | null
}

const UserContext = createContext<UserContextType | undefined>(undefined)

export const UserProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<{ address: string; crypto_type: string; balance?: number; mods?: any[] } | null>(null)
  const [password, setPassword] = useState('')
  const [authLoading, setAuthLoading] = useState(true)
  const [client, setClient] = useState<Client | null>(null)
  const [localKey, setLocalKey] = useState<Key | null>(null)

  // Initialize from localStorage on mount
  useEffect(() => {
    const initializeAuth = async () => {
      try {
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

  const signIn = async () => {
    try {
      await cryptoWaitReady()
  
      const wallet_password = localStorage.getItem('wallet_password') || '42069'
      const key = new Key(wallet_password)
      setLocalKey(key)

      const wallet_mode = localStorage.getItem('wallet_mode') || 'local'
      const user_address = wallet_mode === 'local' ? key.address : localStorage.getItem('wallet_address') || key.address
      const userData = {
        address: user_address,
        crypto_type: key.crypto_type,
        wallet_mode: wallet_mode
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
    <UserContext.Provider value={{ user, signIn, signOut, authLoading, client, localKey }}>
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