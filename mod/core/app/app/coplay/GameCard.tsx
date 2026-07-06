"use client"

import { Game } from './types'

interface GameCardProps {
  game: Game
  userKey?: string
  onSelect: (game: Game) => void
}

const STATUS_COLORS: Record<string, { bg: string; text: string; label: string }> = {
  open: { bg: 'rgb(16 185 129 / 0.15)', text: 'rgb(16 185 129)', label: 'OPEN' },
  full: { bg: 'rgb(245 158 11 / 0.15)', text: 'rgb(245 158 11)', label: 'FULL' },
  completed: { bg: 'rgb(99 102 241 / 0.15)', text: 'rgb(99 102 241)', label: 'DONE' },
  cancelled: { bg: 'rgb(239 68 68 / 0.15)', text: 'rgb(239 68 68)', label: 'CANCELLED' },
  pending_approval: { bg: 'rgb(245 158 11 / 0.15)', text: 'rgb(245 158 11)', label: 'PENDING' },
  removed: { bg: 'rgb(239 68 68 / 0.15)', text: 'rgb(239 68 68)', label: 'REMOVED' },
}

export default function GameCard({ game, userKey, onSelect }: GameCardProps) {
  const status = STATUS_COLORS[game.status] || STATUS_COLORS.open
  const isOrganizer = userKey && game.organizer === userKey
  const isJoined = userKey && game.players.includes(userKey)
  const playerCount = game.players.length
  const maxStr = game.max_players > 0 ? `/${game.max_players}` : ''
  const feeNum = parseFloat(game.entry_fee || '0')

  return (
    <button
      onClick={() => onSelect(game)}
      className="w-full text-left border-4 transition-all hover:brightness-110 font-mono"
      style={{
        backgroundColor: 'var(--bg-primary)',
        borderColor: isJoined ? 'rgb(99 102 241 / 0.4)' : isOrganizer ? 'rgb(245 158 11 / 0.4)' : 'var(--border-color)',
      }}
    >
      {/* Header */}
      <div className="px-4 py-3 flex items-center gap-2" style={{ borderBottom: '1px solid var(--border-color)' }}>
        {game.game_type && (
          <span
            className="px-2 py-0.5 text-[10px] font-extrabold uppercase tracking-wider"
            style={{ backgroundColor: 'rgb(99 102 241 / 0.15)', color: 'rgb(99 102 241)' }}
          >
            {game.game_type}
          </span>
        )}
        <span className="text-[14px] font-extrabold uppercase truncate" style={{ color: 'var(--text-primary)' }}>
          {game.title}
        </span>
        <div className="ml-auto flex items-center gap-2 shrink-0">
          {isOrganizer && (
            <span className="px-1.5 py-0.5 text-[9px] font-extrabold uppercase tracking-wider"
              style={{ backgroundColor: 'rgb(245 158 11 / 0.15)', color: 'rgb(245 158 11)' }}>HOST</span>
          )}
          {isJoined && !isOrganizer && (
            <span className="px-1.5 py-0.5 text-[9px] font-extrabold uppercase tracking-wider"
              style={{ backgroundColor: 'rgb(99 102 241 / 0.15)', color: 'rgb(99 102 241)' }}>JOINED</span>
          )}
          <span
            className="px-2 py-0.5 text-[10px] font-extrabold uppercase tracking-wider"
            style={{ backgroundColor: status.bg, color: status.text }}
          >
            {status.label}
          </span>
        </div>
      </div>

      {/* Body */}
      <div className="px-4 py-3 space-y-2">
        {game.description && (
          <p className="text-[13px] font-medium line-clamp-2" style={{ color: 'var(--text-secondary)' }}>
            {game.description}
          </p>
        )}

        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] font-bold" style={{ color: 'var(--text-tertiary)' }}>
          {game.location && (
            <span className="flex items-center gap-1">
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M15 10.5a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 10.5c0 7.142-7.5 11.25-7.5 11.25S4.5 17.642 4.5 10.5a7.5 7.5 0 1 1 15 0Z" />
              </svg>
              {game.location}
            </span>
          )}
          {game.date && (
            <span className="flex items-center gap-1">
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 0 1 2.25-2.25h13.5A2.25 2.25 0 0 1 21 7.5v11.25m-18 0A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75m-18 0v-7.5A2.25 2.25 0 0 1 5.25 9h13.5A2.25 2.25 0 0 1 21 11.25v7.5" />
              </svg>
              {game.date}{game.time ? ` @ ${game.time}` : ''}
            </span>
          )}
        </div>

        {/* Footer stats */}
        <div className="flex items-center gap-4 pt-1">
          <span className="text-[12px] font-extrabold" style={{ color: 'var(--text-secondary)' }}>
            <span style={{ color: 'rgb(16 185 129)' }}>{playerCount}{maxStr}</span> PLAYERS
          </span>
          {feeNum > 0 && (
            <span className="text-[12px] font-extrabold" style={{ color: 'var(--text-secondary)' }}>
              <span style={{ color: 'rgb(245 158 11)' }}>{game.entry_fee}</span> ETH
            </span>
          )}
          {feeNum === 0 && (
            <span className="text-[12px] font-extrabold" style={{ color: 'rgb(16 185 129)' }}>FREE</span>
          )}
        </div>
      </div>
    </button>
  )
}
