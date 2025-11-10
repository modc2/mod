'use client'
import { useState, useEffect } from 'react'
import { useUserContext } from '@/app/block/context/UserContext'
import { cryptoWaitReady } from '@polkadot/util-crypto'
import { web3Accounts, web3Enable, web3FromAddress } from '@polkadot/extension-dapp'
import { KeyIcon, WalletIcon } from '@heroicons/react/24/outline'

type AuthMode = 'local' | 'subwallet'

export default function WalletAuthButton() {
  const { user, signIn, signOut, authLoading } = useUserContext()
  const [showAuthModal, setShowAuthModal] = useState(false)
  const [authMode, setAuthMode] = useState<AuthMode>('local')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [accounts, setAccounts] = useState<any[]>([])
  const [selectedAccount, setSelectedAccount] = useState<string>('')

  useEffect(() => {
    const checkWallet = async () => {
      const extensions = await web3Enable('MOD')
      if (extensions.length > 0) {
        const allAccounts = await web3Accounts()
        setAccounts(allAccounts)
      }
    }
    checkWallet()
  }, [])

  const handleLocalSignIn = async () => {
    if (!password) {
      setError('Password is required')
      return
    }
    
    setLoading(true)
    setError('')
    
    try {

      localStorage.setItem('wallet_mode', 'local')
      localStorage.setItem('wallet_password', password)
      await signIn()
      setShowAuthModal(false)
      setPassword('')
    } catch (err: any) {
      setError(err.message || 'Failed to sign in')
    } finally {
      setLoading(false)
    }
  }

  const handleSubwalletSignIn = async () => {
    if (!selectedAccount) {
      setError('Please select an account')
      return
    }

    setLoading(true)
    setError('')

    try {
      await cryptoWaitReady()
      
      const account = accounts.find(acc => acc.address === selectedAccount)
      if (!account) throw new Error('Account not found')

      const extensions = await web3Enable('MOD')
      if (extensions.length === 0) throw new Error('No extension found')
      
      const derivationPath = `//mod//client`
      const derivedSeed = `${selectedAccount}${derivationPath}`
          
      localStorage.setItem('wallet_mode', 'subwallet')
      localStorage.setItem('wallet_address', selectedAccount)
      localStorage.setItem('wallet_type', account.type || 'sr25519')
      await signIn()
      setShowAuthModal(false)
    } catch (err: any) {
      setError(err.message || 'Failed to connect wallet')
    } finally {
      setLoading(false)
    }
  }
  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (authMode === 'local') {
      handleLocalSignIn()
    } else {
      handleSubwalletSignIn()
    }
  }

  if (authLoading) {
    return (
      <div className="px-6 py-4 bg-black border-2 border-white/30 text-white rounded-xl backdrop-blur-md" style={{height: '60px'}}>
        <div className="flex items-center gap-2">
          <div className="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
          <span className="font-bold text-lg">Loading...</span>
        </div>
      </div>
    )
  }

  if (user) {
    return null
  }

  return (
    <>
      <button
        onClick={() => setShowAuthModal(true)}
        className="px-8 py-4 bg-black hover:bg-white/5 border-2 border-white/40 hover:border-white/60 text-white rounded-xl font-bold text-xl uppercase tracking-wider transition-all hover:scale-105 active:scale-95"
        style={{height: '60px'}}
      >
        <div className="flex items-center gap-3">
          <KeyIcon className="w-6 h-6" />
          <span>SIGN IN</span>
        </div>
      </button>

      {showAuthModal && (
        <div className="fixed inset-0 bg-black/90 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-black border-2 border-white/40 rounded-xl p-8 max-w-lg w-full">
            <h2 className="text-3xl font-bold text-white mb-6 uppercase tracking-wider text-center">AUTHENTICATE</h2>
            
            <div className="flex gap-3 mb-6">
              <button
                onClick={() => setAuthMode('local')}
                className={`flex-1 px-6 py-4 rounded-xl font-bold text-lg uppercase tracking-wider transition-all border-2 ${
                  authMode === 'local'
                    ? 'bg-white/10 text-white border-white/60'
                    : 'bg-black text-white/60 border-white/30 hover:bg-white/5 hover:border-white/40'
                }`}
              >
                <div className="flex items-center justify-center gap-2">
                  <KeyIcon className="w-5 h-5" />
                  <span>LOCAL KEY</span>
                </div>
              </button>
              <button
                onClick={() => setAuthMode('subwallet')}
                className={`flex-1 px-6 py-4 rounded-xl font-bold text-lg uppercase tracking-wider transition-all border-2 ${
                  authMode === 'subwallet'
                    ? 'bg-white/10 text-white border-white/60'
                    : 'bg-black text-white/60 border-white/30 hover:bg-white/5 hover:border-white/40'
                } ${accounts.length === 0 ? 'opacity-50 cursor-not-allowed' : ''}`}
                disabled={accounts.length === 0}
              >
                <div className="flex items-center justify-center gap-2">
                  <WalletIcon className="w-5 h-5" />
                  <span>SUBWALLET</span>
                </div>
              </button>
            </div>

            <form onSubmit={handleSubmit} className="space-y-6">
              {authMode === 'local' ? (
                <div>
                  <label className="block text-white/70 mb-3 font-bold text-base uppercase tracking-wider">PASSWORD</label>
                  <input
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="w-full px-5 py-4 bg-black border-2 border-white/40 text-white rounded-xl font-mono text-base focus:outline-none focus:border-white/60 transition-all"
                    placeholder="Enter your password"
                    autoFocus
                  />
                </div>
              ) : (
                <div>
                  <label className="block text-white/70 mb-3 font-bold text-base uppercase tracking-wider">SELECT WALLET</label>
                  {accounts.length === 0 ? (
                    <div className="p-5 bg-yellow-900/20 border-2 border-yellow-500/40 rounded-xl">
                      <p className="text-yellow-400 font-bold text-sm">⚠️ No wallet extension detected. Please install SubWallet or Polkadot.js extension.</p>
                    </div>
                  ) : (
                    <>
                      <div className="max-h-80 overflow-y-auto bg-black border-2 border-white/40 rounded-xl">
                        {accounts.map((account) => (
                          <button
                            key={account.address}
                            type="button"
                            onClick={() => setSelectedAccount(account.address)}
                            className={`w-full text-left px-5 py-4 font-mono text-sm transition-all border-b border-white/20 last:border-b-0 hover:bg-white/5 ${
                              selectedAccount === account.address
                                ? 'bg-white/10 text-white font-bold'
                                : 'text-white/70'
                            }`}
                          >
                            <div className="flex items-center justify-between">
                              <div className="flex-1 min-w-0">
                                <div className="font-bold text-base truncate">{account.meta.name}</div>
                                <div className="text-xs text-white/50 mt-1 truncate">
                                  {account.address.slice(0, 12)}...{account.address.slice(-12)}
                                </div>
                              </div>
                              {selectedAccount === account.address && (
                                <div className="w-5 h-5 bg-white rounded-full flex items-center justify-center flex-shrink-0 ml-3">
                                  <span className="text-black font-bold text-sm">✓</span>
                                </div>
                              )}
                            </div>
                          </button>
                        ))}
                      </div>
                      <p className="text-xs text-white/50 mt-3 font-mono leading-relaxed bg-white/5 p-3 rounded-lg border border-white/20">
                        💡 A derived key will be created for client operations. You won't need to sign every request.
                      </p>
                    </>
                  )}
                </div>
              )}

              {error && (
                <div className="text-red-400 font-bold text-sm border-2 border-red-500/40 bg-red-900/20 p-4 rounded-xl">
                  ❌ {error}
                </div>
              )}

              <div className="flex gap-3 pt-2">
                <button
                  type="submit"
                  disabled={loading || (authMode === 'subwallet' && accounts.length === 0)}
                  className="flex-1 px-6 py-4 bg-white/10 text-white hover:bg-white/20 border-2 border-white/40 hover:border-white/60 rounded-xl font-bold text-lg uppercase tracking-wider disabled:opacity-50 disabled:cursor-not-allowed transition-all hover:scale-105 active:scale-95"
                >
                  {loading ? '⏳ LOADING...' : '🚀 SIGN IN'}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setShowAuthModal(false)
                    setPassword('')
                    setError('')
                  }}
                  className="px-6 py-4 bg-black text-white/70 border-2 border-white/30 hover:bg-white/5 hover:border-white/40 rounded-xl font-bold text-lg uppercase tracking-wider transition-all hover:scale-105 active:scale-95"
                >
                  CANCEL
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </>
  )
}
