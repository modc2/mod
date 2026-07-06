"use client"

import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { ethers } from 'ethers'
import { userContext } from '@/context/UserContext'
import { useMetaMask } from '@/wallet/MetaMaskProvider'
import { getFreshToken } from '@/utils/tokenUtils'
import { toast } from 'react-toastify'
import { Game, CoPlayTab, CoPlayConfig } from './types'
import { COPLAY_HUB_ABI } from './abi'
import GameCard from './GameCard'
import GameDetail from './GameDetail'
import CreateGameForm from './CreateGameForm'
import ServerPanel from '@/components/ServerPanel'

const TABS: { key: CoPlayTab; label: string }[] = [
  { key: 'games', label: 'GAMES' },
  { key: 'my_games', label: 'MY GAMES' },
  { key: 'create', label: 'CREATE' },
  { key: 'admin', label: 'ADMIN' },
]

export default function CoPlayPage() {
  const { client, user } = userContext()
  const { signer, provider: mmProvider } = useMetaMask()

  const [activeTab, setActiveTab] = useState<CoPlayTab>('games')
  const [games, setGames] = useState<Game[]>([])
  const [myOrganized, setMyOrganized] = useState<Game[]>([])
  const [myJoined, setMyJoined] = useState<Game[]>([])
  const [selectedGame, setSelectedGame] = useState<Game | null>(null)
  const [loading, setLoading] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [config, setConfig] = useState<CoPlayConfig>({ contract_address: null, chain_id: 84532 })
  const searchInputRef = useRef<HTMLInputElement>(null)

  // Admin state
  const [adminFeeBps, setAdminFeeBps] = useState<number | null>(null)
  const [requireApproval, setRequireApproval] = useState(false)
  const [accumulatedFees, setAccumulatedFees] = useState('0')
  const [isContractAdmin, setIsContractAdmin] = useState(false)
  const [pendingGames, setPendingGames] = useState<Game[]>([])
  const [newFeeBps, setNewFeeBps] = useState('')

  // ── Data fetching ──────────────────────────────────────────────

  const fetchConfig = useCallback(async () => {
    if (!client) return
    try {
      const result = await client.call('coplay/get_config', {})
      if (result) setConfig(result)
    } catch (e) {
      console.error('Failed to fetch config:', e)
    }
  }, [client])

  const fetchGames = useCallback(async () => {
    if (!client) return
    setLoading(true)
    try {
      const result = await client.call('coplay/games', {})
      setGames(Array.isArray(result) ? result : [])
    } catch (e) {
      console.error('Failed to fetch games:', e)
    }
    setLoading(false)
  }, [client])

  const fetchMyGames = useCallback(async () => {
    if (!client || !user?.key) return
    try {
      const freshToken = await getFreshToken(user.key, user.wallet_mode)
      if (!freshToken) return
      const result = await client.call('coplay/my_games', { token: freshToken })
      setMyOrganized(result?.organized || [])
      setMyJoined(result?.joined || [])
    } catch (e) {
      console.error('Failed to fetch my games:', e)
    }
  }, [client, user?.key, user?.wallet_mode])

  const fetchContractState = useCallback(async () => {
    if (!config.contract_address || !mmProvider) return
    try {
      const contract = new ethers.Contract(config.contract_address, COPLAY_HUB_ABI, mmProvider)
      const [fee, approval, fees, contractAdmin] = await Promise.all([
        contract.adminFeeBps(),
        contract.requireApproval(),
        contract.accumulatedFees(),
        contract.admin(),
      ])
      setAdminFeeBps(Number(fee))
      setRequireApproval(approval)
      setAccumulatedFees(ethers.formatEther(fees))
      setIsContractAdmin(
        user?.key ? contractAdmin.toLowerCase() === user.key.toLowerCase() : false
      )
    } catch (e) {
      console.error('Failed to read contract:', e)
    }
  }, [config.contract_address, mmProvider, user?.key])

  useEffect(() => { fetchConfig() }, [fetchConfig])
  useEffect(() => { fetchGames() }, [fetchGames])
  useEffect(() => {
    if (activeTab === 'my_games' && user?.key) fetchMyGames()
  }, [activeTab, fetchMyGames, user?.key])
  useEffect(() => {
    if (activeTab === 'admin') fetchContractState()
  }, [activeTab, fetchContractState])

  // ── Handlers ───────────────────────────────────────────────────

  const handleCreateGame = async (data: {
    title: string; description: string; game_type: string; location: string;
    date: string; time: string; max_players: number; entry_fee: string
  }) => {
    if (!user?.key) { toast.error('Please sign in to create a game'); return }
    setLoading(true)
    try {
      const freshToken = await getFreshToken(user.key, user.wallet_mode)
      if (!freshToken) { toast.error('Auth failed'); setLoading(false); return }

      let chainGameId: number | null = null

      // Create on-chain game if contract is configured
      if (config.contract_address && signer) {
        try {
          const contract = new ethers.Contract(config.contract_address, COPLAY_HUB_ABI, signer)
          const entryFeeWei = ethers.parseEther(data.entry_fee || '0')
          const tx = await contract.createGame(entryFeeWei, data.max_players)
          const receipt = await tx.wait()

          // Parse GameCreated event to get game ID
          const iface = new ethers.Interface(COPLAY_HUB_ABI)
          for (const log of receipt.logs) {
            try {
              const parsed = iface.parseLog({ topics: log.topics as string[], data: log.data })
              if (parsed?.name === 'GameCreated') {
                chainGameId = Number(parsed.args[0])
                break
              }
            } catch { /* skip non-matching logs */ }
          }
        } catch (err: any) {
          if (err.code === 4001 || err.code === 'ACTION_REJECTED') {
            toast.error('Transaction rejected')
            setLoading(false)
            return
          }
          console.warn('On-chain creation failed, creating off-chain only:', err.message)
        }
      }

      // Create off-chain metadata
      const result = await client!.call('coplay/create_game', {
        token: freshToken,
        title: data.title,
        description: data.description,
        game_type: data.game_type,
        location: data.location,
        date: data.date,
        time_str: data.time,
        max_players: data.max_players,
        entry_fee: data.entry_fee,
        chain_game_id: chainGameId,
      })

      if (result?.id) {
        toast.success('Game created!')
        setActiveTab('games')
        fetchGames()
      }
    } catch (e: any) {
      toast.error(`Failed: ${e.message || e}`)
    }
    setLoading(false)
  }

  const handleJoined = async () => {
    if (!selectedGame || !user?.key) return
    try {
      const freshToken = await getFreshToken(user.key, user.wallet_mode)
      if (!freshToken) return
      await client!.call('coplay/join_game', {
        token: freshToken,
        game_id: selectedGame.id,
      })
      toast.success('Joined!')
      fetchGames()
      setSelectedGame(null)
    } catch (e: any) {
      toast.error(`Failed to record join: ${e.message}`)
    }
  }

  const handleComplete = async () => {
    if (!selectedGame || !user?.key) return
    setLoading(true)
    try {
      // Complete on-chain
      if (config.contract_address && signer && selectedGame.chain_game_id !== null) {
        const contract = new ethers.Contract(config.contract_address, COPLAY_HUB_ABI, signer)
        const tx = await contract.completeGame(selectedGame.chain_game_id)
        await tx.wait()
      }

      const freshToken = await getFreshToken(user.key, user.wallet_mode)
      if (!freshToken) { setLoading(false); return }
      await client!.call('coplay/complete_game', {
        token: freshToken,
        game_id: selectedGame.id,
      })
      toast.success('Game completed! Funds released.')
      fetchGames()
      setSelectedGame(null)
    } catch (e: any) {
      if (e.code === 4001 || e.code === 'ACTION_REJECTED') toast.error('Transaction rejected')
      else toast.error(`Failed: ${e.message}`)
    }
    setLoading(false)
  }

  const handleCancel = async () => {
    if (!selectedGame || !user?.key) return
    setLoading(true)
    try {
      if (config.contract_address && signer && selectedGame.chain_game_id !== null) {
        const contract = new ethers.Contract(config.contract_address, COPLAY_HUB_ABI, signer)
        const tx = await contract.cancelGame(selectedGame.chain_game_id)
        await tx.wait()
      }

      const freshToken = await getFreshToken(user.key, user.wallet_mode)
      if (!freshToken) { setLoading(false); return }
      await client!.call('coplay/cancel_game', {
        token: freshToken,
        game_id: selectedGame.id,
      })
      toast.success('Game cancelled.')
      fetchGames()
      setSelectedGame(null)
    } catch (e: any) {
      if (e.code === 4001 || e.code === 'ACTION_REJECTED') toast.error('Transaction rejected')
      else toast.error(`Failed: ${e.message}`)
    }
    setLoading(false)
  }

  // Admin handlers
  const handleSetFee = async () => {
    if (!signer || !config.contract_address) return
    const bps = parseInt(newFeeBps)
    if (isNaN(bps) || bps < 0 || bps > 1000) {
      toast.error('Fee must be 0-1000 basis points (0-10%)')
      return
    }
    try {
      const contract = new ethers.Contract(config.contract_address, COPLAY_HUB_ABI, signer)
      const tx = await contract.setAdminFee(bps)
      await tx.wait()
      toast.success(`Admin fee set to ${(bps / 100).toFixed(1)}%`)
      setNewFeeBps('')
      fetchContractState()
    } catch (e: any) {
      toast.error(e.reason || e.message || 'Failed')
    }
  }

  const handleToggleApproval = async () => {
    if (!signer || !config.contract_address) return
    try {
      const contract = new ethers.Contract(config.contract_address, COPLAY_HUB_ABI, signer)
      const tx = await contract.setRequireApproval(!requireApproval)
      await tx.wait()
      toast.success(`Approval ${!requireApproval ? 'enabled' : 'disabled'}`)
      fetchContractState()
    } catch (e: any) {
      toast.error(e.reason || e.message || 'Failed')
    }
  }

  const handleWithdrawFees = async () => {
    if (!signer || !config.contract_address) return
    try {
      const contract = new ethers.Contract(config.contract_address, COPLAY_HUB_ABI, signer)
      const tx = await contract.withdrawFees()
      await tx.wait()
      toast.success('Fees withdrawn!')
      fetchContractState()
    } catch (e: any) {
      toast.error(e.reason || e.message || 'Failed')
    }
  }

  const handleApproveGame = async (gameId: number) => {
    if (!signer || !config.contract_address) return
    try {
      const contract = new ethers.Contract(config.contract_address, COPLAY_HUB_ABI, signer)
      const tx = await contract.approveGame(gameId)
      await tx.wait()
      toast.success('Game approved')
      fetchGames()
      fetchContractState()
    } catch (e: any) {
      toast.error(e.reason || e.message || 'Failed')
    }
  }

  const handleRejectGame = async (gameId: number) => {
    if (!signer || !config.contract_address) return
    try {
      const contract = new ethers.Contract(config.contract_address, COPLAY_HUB_ABI, signer)
      const tx = await contract.rejectGame(gameId)
      await tx.wait()
      toast.success('Game rejected')
      fetchGames()
    } catch (e: any) {
      toast.error(e.reason || e.message || 'Failed')
    }
  }

  // ── Filtering ──────────────────────────────────────────────────

  const filteredGames = useMemo(() => {
    if (!searchQuery.trim()) return games
    const q = searchQuery.toLowerCase()
    return games.filter(g =>
      g.title.toLowerCase().includes(q) ||
      g.game_type.toLowerCase().includes(q) ||
      g.location.toLowerCase().includes(q) ||
      g.description.toLowerCase().includes(q)
    )
  }, [games, searchQuery])

  // ── Render helpers ─────────────────────────────────────────────

  const renderEmpty = (msg: string) => (
    <div className="flex flex-col items-center justify-center py-20 font-mono border-4"
      style={{ backgroundColor: 'var(--bg-primary)', borderColor: 'var(--border-color)' }}>
      <span className="text-[15px] mb-2 font-extrabold" style={{ color: 'rgb(245 158 11 / 0.5)' }}>[EMPTY]</span>
      <p className="text-[15px] font-bold" style={{ color: 'var(--text-secondary)' }}>{msg}</p>
    </div>
  )

  const renderLoading = () => (
    <div className="flex items-center justify-center py-20 font-mono">
      <div className="flex items-center gap-3">
        <span className="animate-pulse text-lg" style={{ color: 'rgb(245 158 11)' }}>_</span>
        <span className="text-[15px] font-extrabold" style={{ color: 'var(--text-secondary)' }}>LOADING...</span>
      </div>
    </div>
  )

  // If a game is selected, show detail view
  if (selectedGame) {
    return (
      <div className="min-h-screen relative overflow-hidden font-mono" style={{ backgroundColor: 'var(--bg-surface)' }}>
        <div className="relative max-w-5xl mx-auto px-6 pt-6 pb-8">
          <GameDetail
            game={selectedGame}
            userKey={user?.key}
            contractAddress={config.contract_address}
            onBack={() => setSelectedGame(null)}
            onJoined={handleJoined}
            onCompleted={handleComplete}
            onCancelled={handleCancel}
          />
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen relative overflow-hidden font-mono" style={{ backgroundColor: 'var(--bg-surface)' }}>
      <div className="relative max-w-5xl mx-auto px-6 pt-6 pb-8">

        {/* Server status */}
        <ServerPanel moduleName="coplay" apiUrl="http://localhost:50118" appUrl="http://localhost:3118" color="#f97316" />

        {/* Header + Tabs */}
        <div className="mb-6">
          <div className="flex items-end gap-5 pb-0" style={{ borderBottom: '1px solid var(--border-color)' }}>
            <button
              onClick={() => {
                setActiveTab('games')
                setSearchQuery('')
                setTimeout(() => searchInputRef.current?.focus(), 0)
              }}
              className="flex items-center gap-2.5 shrink-0 pb-3 cursor-pointer bg-transparent border-none outline-none"
            >
              <span className="text-[16px] font-extrabold select-none" style={{ color: 'rgb(245 158 11 / 0.6)' }}>&gt;_</span>
              <h1 className="text-[48px] font-extrabold tracking-tight uppercase leading-none"
                style={{ color: 'var(--text-primary)', textShadow: '0 0 20px rgba(245, 158, 11, 0.2)' }}>COPLAY</h1>
            </button>
            <div className="flex items-center gap-0 overflow-x-auto scrollbar-none flex-1">
              {TABS.filter(t => t.key !== 'create' && (t.key !== 'admin' || isContractAdmin)).map(tab => (
                <button
                  key={tab.key}
                  onClick={() => { setActiveTab(tab.key); setSearchQuery('') }}
                  className="relative px-4 py-3.5 text-[14px] font-extrabold tracking-wider transition-all whitespace-nowrap shrink-0 uppercase -mb-px"
                  style={{
                    color: activeTab === tab.key ? 'rgb(245 158 11)' : 'var(--text-secondary)',
                    borderBottom: activeTab === tab.key ? '2px solid rgb(245 158 11)' : '2px solid transparent',
                    backgroundColor: activeTab === tab.key ? 'rgb(245 158 11 / 0.06)' : 'transparent',
                  }}
                >
                  {tab.label}
                </button>
              ))}
            </div>
            <button
              onClick={() => { setActiveTab('create'); setSearchQuery('') }}
              className="shrink-0 px-6 py-2.5 mb-1.5 text-[14px] font-extrabold uppercase tracking-widest transition-all border-4"
              style={{
                backgroundColor: activeTab === 'create' ? 'rgb(245 158 11)' : 'rgb(245 158 11 / 0.15)',
                color: activeTab === 'create' ? '#000' : 'rgb(245 158 11)',
                borderColor: activeTab === 'create' ? 'rgb(245 158 11)' : 'rgb(245 158 11 / 0.5)',
                boxShadow: activeTab === 'create' ? '0 0 20px rgba(245,158,11,0.3)' : 'none',
              }}
            >
              + CREATE GAME
            </button>
          </div>
        </div>

        {/* Contract info bar */}
        {adminFeeBps !== null && (
          <div className="mb-4 flex items-center gap-4 px-4 py-2 border-4 text-[11px] font-extrabold uppercase tracking-wider"
            style={{ borderColor: 'var(--border-color)', color: 'var(--text-tertiary)' }}>
            <span>FEE: <span style={{ color: 'rgb(245 158 11)' }}>{(adminFeeBps / 100).toFixed(1)}%</span></span>
            <span>APPROVAL: <span style={{ color: requireApproval ? 'rgb(245 158 11)' : 'rgb(16 185 129)' }}>{requireApproval ? 'REQUIRED' : 'OFF'}</span></span>
            {config.contract_address && (
              <span className="ml-auto font-mono">
                CONTRACT: {config.contract_address.slice(0, 6)}...{config.contract_address.slice(-4)}
              </span>
            )}
          </div>
        )}

        {/* Search */}
        {activeTab === 'games' && (
          <div className="mb-5">
            <div className="relative">
              <span className="absolute left-4 top-1/2 -translate-y-1/2 text-[15px] font-extrabold" style={{ color: 'var(--text-secondary)' }}>&gt;</span>
              <input
                ref={searchInputRef}
                type="text"
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
                placeholder="search games..."
                className="w-full pl-9 pr-4 py-3.5 border-4 text-[15px] font-mono font-bold transition-colors focus:outline-none"
                style={{ backgroundColor: 'var(--bg-input)', borderColor: 'var(--border-color)', color: 'var(--text-primary)' }}
              />
              {searchQuery && (
                <button onClick={() => setSearchQuery('')}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-[13px] font-extrabold"
                  style={{ color: 'var(--text-secondary)' }}>[x]</button>
              )}
            </div>
          </div>
        )}

        {/* ── Games Tab ─────────────────────────────────────────── */}
        {activeTab === 'games' && (
          loading ? renderLoading() :
          filteredGames.length === 0 ? renderEmpty(searchQuery ? 'No games match your search.' : 'No games yet. Create one!') : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {filteredGames.map(game => (
                <GameCard key={game.id} game={game} userKey={user?.key} onSelect={setSelectedGame} />
              ))}
            </div>
          )
        )}

        {/* ── My Games Tab ──────────────────────────────────────── */}
        {activeTab === 'my_games' && (
          !user?.key ? renderEmpty('Sign in to view your games.') : (
            <div className="space-y-6">
              <div>
                <h3 className="text-[13px] font-extrabold uppercase tracking-[0.2em] mb-3" style={{ color: 'var(--text-secondary)' }}>
                  ORGANIZED ({myOrganized.length})
                </h3>
                {myOrganized.length === 0 ? renderEmpty('No games organized yet.') : (
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {myOrganized.map(game => (
                      <GameCard key={game.id} game={game} userKey={user?.key} onSelect={setSelectedGame} />
                    ))}
                  </div>
                )}
              </div>
              <div>
                <h3 className="text-[13px] font-extrabold uppercase tracking-[0.2em] mb-3" style={{ color: 'var(--text-secondary)' }}>
                  JOINED ({myJoined.length})
                </h3>
                {myJoined.length === 0 ? renderEmpty('No games joined yet.') : (
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {myJoined.map(game => (
                      <GameCard key={game.id} game={game} userKey={user?.key} onSelect={setSelectedGame} />
                    ))}
                  </div>
                )}
              </div>
            </div>
          )
        )}

        {/* ── Create Tab ────────────────────────────────────────── */}
        {activeTab === 'create' && (
          !user?.key ? renderEmpty('Sign in to create a game.') : (
            <CreateGameForm loading={loading} onSubmit={handleCreateGame} />
          )
        )}

        {/* ── Admin Tab ─────────────────────────────────────────── */}
        {activeTab === 'admin' && isContractAdmin && (
          <div className="space-y-4 max-w-2xl">
            {/* Fee control */}
            <div className="border-4 p-5" style={{ backgroundColor: 'var(--bg-primary)', borderColor: 'var(--border-color)' }}>
              <div className="text-[12px] font-extrabold uppercase tracking-[0.2em] mb-3" style={{ color: 'var(--text-secondary)' }}>
                ADMIN FEE
              </div>
              <div className="flex items-center gap-3">
                <div className="text-[24px] font-extrabold" style={{ color: 'rgb(245 158 11)' }}>
                  {adminFeeBps !== null ? `${(adminFeeBps / 100).toFixed(1)}%` : '—'}
                </div>
                <div className="flex-1" />
                <input
                  type="number"
                  min="0"
                  max="1000"
                  value={newFeeBps}
                  onChange={e => setNewFeeBps(e.target.value)}
                  className="w-24 px-3 py-2 text-[14px] font-mono font-bold focus:outline-none border-4"
                  style={{ backgroundColor: 'var(--bg-input)', borderColor: 'var(--border-color)', color: 'var(--text-primary)' }}
                  placeholder="BPS"
                />
                <button onClick={handleSetFee}
                  className="px-4 py-2 text-[12px] font-extrabold uppercase tracking-wider"
                  style={{ backgroundColor: 'rgb(245 158 11)', color: '#000' }}>
                  SET
                </button>
              </div>
              <p className="text-[10px] mt-2 font-bold" style={{ color: 'var(--text-tertiary)' }}>
                Basis points (0-1000). 100 = 1%, 1000 = 10% max.
              </p>
            </div>

            {/* Approval toggle */}
            <div className="border-4 p-5" style={{ backgroundColor: 'var(--bg-primary)', borderColor: 'var(--border-color)' }}>
              <div className="flex items-center gap-3">
                <div>
                  <div className="text-[12px] font-extrabold uppercase tracking-[0.2em]" style={{ color: 'var(--text-secondary)' }}>
                    REQUIRE GAME APPROVAL
                  </div>
                  <p className="text-[11px] mt-1 font-bold" style={{ color: 'var(--text-tertiary)' }}>
                    When enabled, new games need admin approval before going live.
                  </p>
                </div>
                <button onClick={handleToggleApproval}
                  className="ml-auto px-4 py-2 text-[12px] font-extrabold uppercase tracking-wider border-4"
                  style={{
                    backgroundColor: requireApproval ? 'rgb(245 158 11 / 0.15)' : 'transparent',
                    color: requireApproval ? 'rgb(245 158 11)' : 'var(--text-secondary)',
                    borderColor: requireApproval ? 'rgb(245 158 11 / 0.4)' : 'var(--border-color)',
                  }}>
                  {requireApproval ? 'ON' : 'OFF'}
                </button>
              </div>
            </div>

            {/* Accumulated fees */}
            <div className="border-4 p-5" style={{ backgroundColor: 'var(--bg-primary)', borderColor: 'var(--border-color)' }}>
              <div className="flex items-center gap-3">
                <div>
                  <div className="text-[12px] font-extrabold uppercase tracking-[0.2em]" style={{ color: 'var(--text-secondary)' }}>
                    ACCUMULATED FEES
                  </div>
                  <div className="text-[20px] font-extrabold mt-1" style={{ color: 'rgb(16 185 129)' }}>
                    {accumulatedFees} ETH
                  </div>
                </div>
                <button onClick={handleWithdrawFees}
                  className="ml-auto px-4 py-2 text-[12px] font-extrabold uppercase tracking-wider"
                  style={{ backgroundColor: 'rgb(16 185 129)', color: '#000' }}>
                  WITHDRAW
                </button>
              </div>
            </div>

            {/* Pending games */}
            {requireApproval && pendingGames.length > 0 && (
              <div className="border-4 p-5" style={{ backgroundColor: 'var(--bg-primary)', borderColor: 'var(--border-color)' }}>
                <div className="text-[12px] font-extrabold uppercase tracking-[0.2em] mb-3" style={{ color: 'var(--text-secondary)' }}>
                  PENDING APPROVAL ({pendingGames.length})
                </div>
                <div className="space-y-2">
                  {pendingGames.map(game => (
                    <div key={game.id} className="flex items-center gap-3 p-3 border-4" style={{ borderColor: 'var(--border-color)' }}>
                      <div className="flex-1">
                        <div className="text-[13px] font-extrabold uppercase" style={{ color: 'var(--text-primary)' }}>{game.title}</div>
                        <div className="text-[11px] font-bold" style={{ color: 'var(--text-tertiary)' }}>{game.game_type} — {game.location}</div>
                      </div>
                      {game.chain_game_id !== null && (
                        <>
                          <button onClick={() => handleApproveGame(game.chain_game_id!)}
                            className="px-3 py-1.5 text-[11px] font-extrabold uppercase"
                            style={{ backgroundColor: 'rgb(16 185 129)', color: '#000' }}>APPROVE</button>
                          <button onClick={() => handleRejectGame(game.chain_game_id!)}
                            className="px-3 py-1.5 text-[11px] font-extrabold uppercase border-4"
                            style={{ color: 'rgb(239 68 68)', borderColor: 'rgb(239 68 68 / 0.4)' }}>REJECT</button>
                        </>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
