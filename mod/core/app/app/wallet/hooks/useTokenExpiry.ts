"use client";

import { useState, useEffect, useRef, useCallback } from 'react'
import { Auth } from '@/client/auth'
import { toast } from 'react-toastify'

const TOKEN_DURATION = 86400
const DAILY_REFRESH_KEY = 'wallet_token_last_daily_refresh'

export function useTokenExpiry(userExists: boolean) {
  const [tokenExpiry, setTokenExpiry] = useState<string | null>(null)
  const [isTokenExpired, setIsTokenExpired] = useState(false)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const autoRefreshAttempted = useRef(false)

  const getTokenExpiry = () => {
    try {
      const token = localStorage.getItem('wallet_token')
      if (!token) return 'No token'
      const auth = new Auth()
      const authData = auth.token2data(token)
      const tokenTime = parseFloat(authData.time)
      const expiryTime = tokenTime + TOKEN_DURATION
      const now = Date.now() / 1000
      const timeLeft = expiryTime - now
      if (timeLeft <= 0) return 'Expired'
      const minutes = Math.floor(timeLeft / 60)
      const seconds = Math.floor(timeLeft % 60)
      return `${minutes}m ${seconds}s`
    } catch {
      return 'Invalid token'
    }
  }

  const handleRefreshToken = useCallback(async () => {
    setIsRefreshing(true)
    try {
      const wallet_mode = localStorage.getItem('wallet_mode') || 'local'
      const wallet_address = localStorage.getItem('wallet_address')
      const auth = new Auth()
      const newToken = await auth.token('', wallet_address, wallet_mode)
      localStorage.setItem('wallet_token', newToken)
      // Update per-account cache too
      if (wallet_address) {
        localStorage.setItem(`wallet_token_${wallet_address.toLowerCase()}`, newToken)
      }
      setTokenExpiry(getTokenExpiry())
      setIsTokenExpired(false)
      autoRefreshAttempted.current = false
      if (typeof window !== 'undefined') {
        (window as any).__tokenExpired = false
      }
      // Update last daily refresh timestamp
      localStorage.setItem(DAILY_REFRESH_KEY, Date.now().toString())
      return true
    } catch (error) {
      console.error('Failed to refresh token:', error)
      return false
    } finally {
      setIsRefreshing(false)
    }
  }, [])

  // Auto-refresh when token expires
  const autoRefreshToken = useCallback(async () => {
    if (autoRefreshAttempted.current || isRefreshing) return
    autoRefreshAttempted.current = true

    const success = await handleRefreshToken()
    if (!success) {
      // Auto-refresh failed — prompt user
      toast.warning('Token expired — tap the refresh button to renew', {
        toastId: 'token-auto-refresh-failed',
        position: 'top-center',
        autoClose: 8000,
        closeOnClick: true,
        closeButton: true,
      })
    }
  }, [handleRefreshToken, isRefreshing])

  // Check if daily refresh is needed (once per day)
  const checkDailyRefresh = useCallback(async () => {
    const lastRefresh = localStorage.getItem(DAILY_REFRESH_KEY)
    const now = Date.now()
    const oneDayMs = 24 * 60 * 60 * 1000

    if (!lastRefresh || (now - parseInt(lastRefresh, 10)) >= oneDayMs) {
      await handleRefreshToken()
    }
  }, [handleRefreshToken])

  useEffect(() => {
    if (!userExists) return

    // Check daily refresh on mount
    checkDailyRefresh()

    const interval = setInterval(() => {
      const expiry = getTokenExpiry()
      setTokenExpiry(expiry)
      if (expiry === 'Expired' || expiry === 'Invalid token') {
        setIsTokenExpired(true)
        // Auto-refresh instead of just flagging expired
        autoRefreshToken()
      } else {
        setIsTokenExpired(false)
        autoRefreshAttempted.current = false
        if (typeof window !== 'undefined') {
          (window as any).__tokenExpired = false
        }
      }
    }, 1000)

    // Also set up daily refresh interval (check every hour if daily refresh is due)
    const dailyInterval = setInterval(() => {
      checkDailyRefresh()
    }, 60 * 60 * 1000)

    return () => {
      clearInterval(interval)
      clearInterval(dailyInterval)
    }
  }, [userExists, autoRefreshToken, checkDailyRefresh])

  return { tokenExpiry, isTokenExpired, isRefreshing, setIsRefreshing, getTokenExpiry, handleRefreshToken }
}
