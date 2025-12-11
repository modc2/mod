'use client'

import { useState, useEffect } from 'react'
import { useUserContext } from '@/bloc/context'
import { LoginHeader } from './LoginHeader'

export function LocalKeyManager() {
  const { signIn, authLoading } = useUserContext()
  const [mnemonic, setMnemonic] = useState('')
  const [error, setError] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    
    if (!mnemonic.trim()) {
      setError('Please enter a mnemonic phrase')
      return
    }

    try {
      await signIn(mnemonic.trim())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to sign in')
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4" style={{ backgroundColor: 'black' }}>
      <div className="w-full max-w-2xl">
        <LoginHeader />
        
        <form onSubmit={handleSubmit} className="space-y-6">
          <div>
            <label 
              htmlFor="mnemonic" 
              className="bloc text-xl font-bold mb-3 uppercase tracking-wider"
              style={{ color: '#00ff00', fontFamily: 'monospace' }}
            >
              MNEMONIC PHRASE
            </label>
            <textarea
              id="mnemonic"
              value={mnemonic}
              onChange={(e) => setMnemonic(e.target.value)}
              placeholder="Enter your 12 or 24 word mnemonic phrase..."
              rows={4}
              className="w-full px-6 py-4 rounded-xl border-4 bg-black/80 backdrop-blur-xl text-lg font-mono transition-all focus:outline-none focus:ring-4"
              style={{
                borderColor: '#00ff00',
                color: '#00ff00',
                boxShadow: '0 0 20px #00ff0030, inset 0 0 10px #00ff0010',
                caretColor: '#00ff00'
              }}
              disabled={authLoading}
            />
          </div>

          {error && (
            <div 
              className="p-4 rounded-xl border-4 font-bold text-center uppercase tracking-wider"
              style={{
                backgroundColor: '#ff000020',
                borderColor: '#ff0000',
                color: '#ff0000',
                boxShadow: '0 0 20px #ff000050'
              }}
            >
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={authLoading}
            className="w-full py-5 rounded-xl border-4 font-black text-2xl uppercase tracking-widest transition-all hover:scale-105 active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed"
            style={{
              backgroundColor: authLoading ? '#00ff0020' : '#00ff0040',
              borderColor: '#00ff00',
              color: '#00ff00',
              boxShadow: '0 0 30px #00ff0050, inset 0 0 20px #00ff0020',
              fontFamily: 'monospace'
            }}
          >
            {authLoading ? 'LOADING...' : 'CONNECT'}
          </button>
        </form>
      </div>
    </div>
  )
}

export default LocalKeyManager