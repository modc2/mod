'use client'
import React, { createContext, useContext, useEffect, useState } from 'react'
import { Key } from '@/app/block/key'
import { cryptoWaitReady } from '@polkadot/util-crypto'
import { Client } from '@/app/block/client/client'

interface UserContextType {
  keyInstance: Key | null
  setKeyInstance: (key: Key | null) => void
  user: { address: string; crypto_type: string; balance?: number; mods?: any[] } | null
  password: string
  signIn: (password: string) => Promise<void>
  signOut: () => void
  authLoading: boolean
  client: Client | null
}

const UserContext = createContext<UserContextType | undefined>(undefined)

export const UserProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [keyInstance, setKeyInstance] = useState<Key | null>(null)
  const [user, setUser] = useState<{ address: string; crypto_type: string; balance?: number; mods?: any[] } | null>(null)
  const [password, setPassword] = useState('')
  const [authLoading, setAuthLoading] = useState(true)
  const [client, setClient] = useState<Client | null>(null)

  // Initialize from localStorage on mount
  useEffect(() => {
    const initializeAuth = async () => {
      try {
        const storedUser = localStorage.getItem('user_data')
        const storedPassword = localStorage.getItem('user_password')
        
        if (storedUser && storedPassword) {
          await cryptoWaitReady()
          const userData = JSON.parse(storedUser)
          const key = new Key(storedPassword)
          setUser(userData)
          setKeyInstance(key)
          setPassword(storedPassword)
          setClient(new Client(undefined, key))
        }
      } catch (error) {
        console.error('Failed to restore auth session:', error)
        localStorage.removeItem('user_data')
        localStorage.removeItem('user_password')
      } finally {
        setAuthLoading(false)
      }
    }
    
    initializeAuth()
  }, [])

  const signIn = async (newPassword: string) => {
    try {
      await cryptoWaitReady()
      const key = new Key(newPassword)
      const wallet_mode = localStorage.getItem('wallet_mode') || 'local'
      const user_address = wallet_mode === 'local' ? key.address : localStorage.getItem('wallet_address') || key.address
      const userData = {
        address: user_address,
        crypto_type: key.crypto_type,
        wallet_mode: wallet_mode
      }
      
      setKeyInstance(key)
      setUser(userData)
      setPassword(newPassword)
      setClient(new Client(undefined, key))
      
      // Persist to localStorage
      localStorage.setItem('user_data', JSON.stringify(userData))
      localStorage.setItem('user_password', newPassword)
    } catch (error) {
      console.error('Failed to sign in:', error)
      throw error
    }
  }

  const signOut = () => {
    setKeyInstance(null)
    setUser(null)
    setPassword('')
    setClient(null)
    localStorage.removeItem('user_data')
    localStorage.removeItem('user_password')
  }

  return (
    <UserContext.Provider value={{ keyInstance, setKeyInstance, user, password, signIn, signOut, authLoading, client }}>
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