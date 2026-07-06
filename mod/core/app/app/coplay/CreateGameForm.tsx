"use client"

import { useState } from 'react'

interface CreateGameFormProps {
  loading: boolean
  onSubmit: (data: {
    title: string
    description: string
    game_type: string
    location: string
    date: string
    time: string
    max_players: number
    entry_fee: string
  }) => Promise<void>
}

export default function CreateGameForm({ loading, onSubmit }: CreateGameFormProps) {
  const [form, setForm] = useState({
    title: '',
    description: '',
    game_type: '',
    location: '',
    date: '',
    time: '',
    max_players: '',
    entry_fee: '',
  })

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!form.title.trim()) return
    await onSubmit({
      title: form.title.trim(),
      description: form.description.trim(),
      game_type: form.game_type.trim(),
      location: form.location.trim(),
      date: form.date,
      time: form.time,
      max_players: parseInt(form.max_players) || 0,
      entry_fee: form.entry_fee || '0',
    })
    setForm({ title: '', description: '', game_type: '', location: '', date: '', time: '', max_players: '', entry_fee: '' })
  }

  const inputStyle = {
    backgroundColor: 'var(--bg-input)',
    border: '4px solid var(--border-color)',
    color: 'var(--text-primary)',
  }

  return (
    <div className="max-w-2xl mx-auto font-mono">
      <form onSubmit={handleSubmit} style={{ backgroundColor: 'var(--bg-primary)', border: '4px solid var(--border-color)' }}>
        {/* Header */}
        <div className="px-6 py-4 border-b flex items-center gap-2" style={{ borderColor: 'var(--border-color)' }}>
          <span className="text-[14px] font-extrabold uppercase tracking-[0.15em]" style={{ color: 'rgb(245 158 11)' }}>&gt;_</span>
          <span className="text-[14px] font-extrabold uppercase tracking-[0.15em]" style={{ color: 'var(--text-primary)' }}>New Game</span>
          <span className="text-[12px] ml-auto font-bold" style={{ color: 'var(--text-tertiary)' }}>FORM.CREATE</span>
        </div>

        <div className="px-6 py-6 space-y-5">
          {/* Title */}
          <div>
            <label className="block text-[12px] font-extrabold uppercase tracking-[0.2em] mb-2" style={{ color: 'var(--text-secondary)' }}>
              TITLE
            </label>
            <input
              type="text"
              value={form.title}
              onChange={e => setForm({ ...form, title: e.target.value })}
              className="w-full px-4 py-3 text-[15px] focus:outline-none transition-colors font-mono font-bold"
              style={inputStyle}
              placeholder="Pickup Basketball @ The Park"
              required
            />
          </div>

          {/* Description */}
          <div>
            <label className="block text-[12px] font-extrabold uppercase tracking-[0.2em] mb-2" style={{ color: 'var(--text-secondary)' }}>
              DESCRIPTION
            </label>
            <textarea
              value={form.description}
              onChange={e => setForm({ ...form, description: e.target.value })}
              className="w-full px-4 py-3 text-[15px] focus:outline-none transition-colors resize-none h-24 font-mono font-medium"
              style={inputStyle}
              placeholder="5v5 full court, bring your own water..."
            />
          </div>

          {/* Game Type + Location */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-[12px] font-extrabold uppercase tracking-[0.2em] mb-2" style={{ color: 'var(--text-secondary)' }}>
                GAME TYPE
              </label>
              <input
                type="text"
                value={form.game_type}
                onChange={e => setForm({ ...form, game_type: e.target.value })}
                className="w-full px-4 py-3 text-[15px] focus:outline-none transition-colors font-mono font-medium"
                style={inputStyle}
                placeholder="basketball, poker, soccer..."
              />
            </div>
            <div>
              <label className="block text-[12px] font-extrabold uppercase tracking-[0.2em] mb-2" style={{ color: 'var(--text-secondary)' }}>
                LOCATION
              </label>
              <input
                type="text"
                value={form.location}
                onChange={e => setForm({ ...form, location: e.target.value })}
                className="w-full px-4 py-3 text-[15px] focus:outline-none transition-colors font-mono font-medium"
                style={inputStyle}
                placeholder="Central Park Court 3"
              />
            </div>
          </div>

          {/* Date + Time */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-[12px] font-extrabold uppercase tracking-[0.2em] mb-2" style={{ color: 'var(--text-secondary)' }}>
                DATE
              </label>
              <input
                type="date"
                value={form.date}
                onChange={e => setForm({ ...form, date: e.target.value })}
                className="w-full px-4 py-3 text-[15px] focus:outline-none transition-colors font-mono font-bold"
                style={inputStyle}
              />
            </div>
            <div>
              <label className="block text-[12px] font-extrabold uppercase tracking-[0.2em] mb-2" style={{ color: 'var(--text-secondary)' }}>
                TIME
              </label>
              <input
                type="time"
                value={form.time}
                onChange={e => setForm({ ...form, time: e.target.value })}
                className="w-full px-4 py-3 text-[15px] focus:outline-none transition-colors font-mono font-bold"
                style={inputStyle}
              />
            </div>
          </div>

          {/* Max Players + Entry Fee */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-[12px] font-extrabold uppercase tracking-[0.2em] mb-2" style={{ color: 'var(--text-secondary)' }}>
                MAX PLAYERS <span style={{ color: 'var(--text-tertiary)' }}>(0 = NO LIMIT)</span>
              </label>
              <input
                type="number"
                min="0"
                value={form.max_players}
                onChange={e => setForm({ ...form, max_players: e.target.value })}
                className="w-full px-4 py-3 text-[15px] focus:outline-none transition-colors font-mono font-extrabold"
                style={inputStyle}
                placeholder="10"
              />
            </div>
            <div>
              <label className="block text-[12px] font-extrabold uppercase tracking-[0.2em] mb-2" style={{ color: 'var(--text-secondary)' }}>
                ENTRY FEE (ETH)
              </label>
              <input
                type="text"
                value={form.entry_fee}
                onChange={e => setForm({ ...form, entry_fee: e.target.value })}
                className="w-full px-4 py-3 text-[15px] focus:outline-none transition-colors font-mono font-extrabold"
                style={{ ...inputStyle, color: 'rgb(245 158 11)' }}
                placeholder="0.005"
              />
              <p className="text-[11px] mt-1.5 font-bold" style={{ color: 'var(--text-tertiary)' }}>
                Set to 0 for free games
              </p>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t flex justify-end" style={{ borderColor: 'var(--border-color)' }}>
          <button
            type="submit"
            disabled={loading || !form.title.trim()}
            className="px-6 py-2.5 text-[14px] font-extrabold uppercase tracking-wider transition-colors"
            style={{
              backgroundColor: 'rgb(245 158 11)',
              color: '#000',
              opacity: loading || !form.title.trim() ? 0.4 : 1,
              cursor: loading || !form.title.trim() ? 'not-allowed' : 'pointer',
            }}
          >
            {loading ? 'CREATING...' : 'CREATE GAME >'}
          </button>
        </div>
      </form>
    </div>
  )
}
