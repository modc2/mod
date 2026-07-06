"use client"

import { Game } from './types'
import PaymentFlow from './PaymentFlow'
import { useState } from 'react'

interface GameDetailProps {
  game: Game
  userKey?: string
  contractAddress: string | null
  onBack: () => void
  onJoined: () => void
  onCompleted: () => void
  onCancelled: () => void
}

export default function GameDetail({
  game, userKey, contractAddress, onBack, onJoined, onCompleted, onCancelled
}: GameDetailProps) {
  const [showPayment, setShowPayment] = useState(false)
  const isOrganizer = userKey && game.organizer === userKey
  const isJoined = userKey && game.players.includes(userKey)
  const isOpen = game.status === 'open'
  const canJoin = isOpen && !isOrganizer && !isJoined && userKey
  const canComplete = (game.status === 'open' || game.status === 'full') && isOrganizer
  const canCancel = (game.status === 'open' || game.status === 'full') && (isOrganizer)
  const feeNum = parseFloat(game.entry_fee || '0')

  return (
    <div className="font-mono">
      {/* Back button */}
      <button
        onClick={onBack}
        className="flex items-center gap-2 mb-4 text-[13px] font-extrabold uppercase tracking-wider transition-colors hover:opacity-70"
        style={{ color: 'var(--text-secondary)' }}
      >
        <span>&lt;</span> BACK TO GAMES
      </button>

      <div className="border-4" style={{ backgroundColor: 'var(--bg-primary)', borderColor: 'var(--border-color)' }}>
        {/* Header */}
        <div className="px-6 py-4 flex items-center gap-3" style={{ borderBottom: '1px solid var(--border-color)' }}>
          {game.game_type && (
            <span className="px-2.5 py-1 text-[11px] font-extrabold uppercase tracking-wider"
              style={{ backgroundColor: 'rgb(99 102 241 / 0.15)', color: 'rgb(99 102 241)' }}>
              {game.game_type}
            </span>
          )}
          <h2 className="text-[20px] font-extrabold uppercase" style={{ color: 'var(--text-primary)' }}>
            {game.title}
          </h2>
          <span className="ml-auto px-2.5 py-1 text-[11px] font-extrabold uppercase tracking-wider"
            style={{
              backgroundColor: game.status === 'open' ? 'rgb(16 185 129 / 0.15)' : game.status === 'completed' ? 'rgb(99 102 241 / 0.15)' : 'rgb(239 68 68 / 0.15)',
              color: game.status === 'open' ? 'rgb(16 185 129)' : game.status === 'completed' ? 'rgb(99 102 241)' : 'rgb(239 68 68)',
            }}>
            {game.status.toUpperCase().replace('_', ' ')}
          </span>
        </div>

        {/* Body */}
        <div className="px-6 py-5 space-y-5">
          {game.description && (
            <p className="text-[14px] leading-relaxed font-medium" style={{ color: 'var(--text-secondary)' }}>
              {game.description}
            </p>
          )}

          {/* Info grid */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {[
              { label: 'LOCATION', value: game.location || '—' },
              { label: 'DATE', value: game.date ? `${game.date}${game.time ? ` @ ${game.time}` : ''}` : '—' },
              { label: 'PLAYERS', value: `${game.players.length}${game.max_players > 0 ? `/${game.max_players}` : ''}` },
              { label: 'ENTRY FEE', value: feeNum > 0 ? `${game.entry_fee} ETH` : 'FREE' },
            ].map(item => (
              <div key={item.label} className="p-3 border-4" style={{ borderColor: 'var(--border-color)' }}>
                <div className="text-[10px] font-extrabold uppercase tracking-[0.2em] mb-1" style={{ color: 'var(--text-tertiary)' }}>
                  {item.label}
                </div>
                <div className="text-[14px] font-extrabold" style={{ color: 'var(--text-primary)' }}>
                  {item.value}
                </div>
              </div>
            ))}
          </div>

          {/* Organizer */}
          <div className="p-3 border-4" style={{ borderColor: 'var(--border-color)' }}>
            <div className="text-[10px] font-extrabold uppercase tracking-[0.2em] mb-1" style={{ color: 'var(--text-tertiary)' }}>
              ORGANIZER
            </div>
            <div className="text-[13px] font-bold font-mono" style={{ color: 'var(--text-primary)' }}>
              {game.organizer.slice(0, 10)}...{game.organizer.slice(-8)}
              {isOrganizer && <span className="ml-2 text-[10px]" style={{ color: 'rgb(245 158 11)' }}>(YOU)</span>}
            </div>
          </div>

          {/* Player list */}
          {game.players.length > 0 && (
            <div>
              <div className="text-[12px] font-extrabold uppercase tracking-[0.2em] mb-2" style={{ color: 'var(--text-secondary)' }}>
                PLAYERS ({game.players.length})
              </div>
              <div className="space-y-1">
                {game.players.map((player, i) => (
                  <div key={player} className="flex items-center gap-2 px-3 py-2 border-4" style={{ borderColor: 'var(--border-color)' }}>
                    <span className="text-[11px] font-extrabold" style={{ color: 'rgb(16 185 129)' }}>
                      {String(i + 1).padStart(2, '0')}
                    </span>
                    <span className="text-[13px] font-bold font-mono" style={{ color: 'var(--text-primary)' }}>
                      {player.slice(0, 10)}...{player.slice(-8)}
                    </span>
                    {player === userKey && (
                      <span className="text-[9px] font-extrabold ml-auto" style={{ color: 'rgb(99 102 241)' }}>YOU</span>
                    )}
                    {game.tx_hashes?.[player] && (
                      <span className="text-[9px] font-bold ml-auto" style={{ color: 'rgb(16 185 129)' }}>PAID</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-3 pt-2">
            {canJoin && (
              <button
                onClick={() => setShowPayment(true)}
                className="px-6 py-3 text-[14px] font-extrabold uppercase tracking-wider transition-all hover:brightness-110"
                style={{ backgroundColor: 'rgb(16 185 129)', color: '#000' }}
              >
                {feeNum > 0 ? `JOIN — ${game.entry_fee} ETH` : 'JOIN GAME'}
              </button>
            )}
            {canComplete && (
              <button
                onClick={onCompleted}
                className="px-6 py-3 text-[14px] font-extrabold uppercase tracking-wider transition-all hover:brightness-110"
                style={{ backgroundColor: 'rgb(99 102 241)', color: '#fff' }}
              >
                COMPLETE GAME
              </button>
            )}
            {canCancel && (
              <button
                onClick={onCancelled}
                className="px-6 py-3 text-[14px] font-extrabold uppercase tracking-wider transition-all hover:brightness-110 border-4"
                style={{ backgroundColor: 'transparent', color: 'rgb(239 68 68)', borderColor: 'rgb(239 68 68 / 0.4)' }}
              >
                CANCEL
              </button>
            )}
            {isJoined && !isOrganizer && (
              <span className="px-4 py-3 text-[13px] font-extrabold uppercase tracking-wider"
                style={{ color: 'rgb(99 102 241)' }}>
                YOU&apos;RE IN
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Payment Modal */}
      {showPayment && (
        <PaymentFlow
          game={game}
          contractAddress={contractAddress}
          onSuccess={() => {
            setShowPayment(false)
            onJoined()
          }}
          onCancel={() => setShowPayment(false)}
        />
      )}
    </div>
  )
}
