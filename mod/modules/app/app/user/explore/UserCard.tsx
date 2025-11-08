'use client'

import { UserType } from '@/app/types'
import { CopyButton } from '@/app/block/CopyButton'
import Link from 'next/link'
import { User, Hash, Package, Wallet, KeyIcon, Shield } from 'lucide-react'
import { shorten, text2color } from "@/app/utils";

interface UserCardProps {
  user: UserType
}

export function UserCard({ user }: UserCardProps) {
  const keyColor = text2color(user.key)
  const modCount = user.mods?.length || 0
  const balance = user.balance || 0
  const walletType = user.crypto_type || 'sr25519'

  const hexToRgb = (hex: string) => {
    const result = /^#?([a-f\\d]{2})([a-f\\d]{2})([a-f\\d]{2})$/i.exec(hex)
    return result ? {
      r: parseInt(result[1], 16),
      g: parseInt(result[2], 16),
      b: parseInt(result[3], 16)
    } : { r: 139, g: 92, b: 246 }
  }
  
  const rgb = hexToRgb(keyColor)
  const borderColor = `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.4)`
  const glowColor = `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.2)`

  return (
    <div className="group relative border-2 rounded-xl p-4 hover:shadow-xl transition-all duration-300 backdrop-blur-sm overflow-hidden h-full flex flex-col hover:scale-[1.01] bg-black" style={{ borderColor: borderColor, boxShadow: `0 0 12px ${glowColor}` }}>
      <div className="absolute -inset-1 bg-gradient-to-r opacity-5 group-hover:opacity-10 blur-lg transition-all duration-500 rounded-xl" style={{ background: `linear-gradient(45deg, ${keyColor}, transparent, ${keyColor})` }} />
      
      <div className="relative z-10 space-y-2.5 flex-1 flex flex-col">
        <div className="flex items-start justify-between gap-2">
          <Link href={`/user/${user.key}`} className="flex items-center gap-2 group/link flex-1 min-w-0">
            <div className="flex-shrink-0 p-2 rounded-lg border group-hover/link:scale-110 transition-all duration-300" style={{ backgroundColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.1)`, borderColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.4)` }}>
              <User size={28} strokeWidth={2.5} style={{ color: keyColor }} />
            </div>
            <div className="flex-1 min-w-0">
              <div className="border px-2.5 py-1.5 rounded-lg backdrop-blur-sm" style={{ backgroundColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.08)`, borderColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.3)` }}>
                <div className="flex items-center gap-2">
                  <KeyIcon size={18} strokeWidth={2.5} style={{ color: keyColor }} />
                  <code className="text-2xl font-mono font-bold truncate block" style={{ color: keyColor }} title={user.key}>
                    {shorten(user.key, 8, 8)}
                  </code>
                  <CopyButton text={user.key} size="sm" />
                  <div className="ml-1 px-2 py-0.5 rounded border text-xs font-mono font-bold" style={{ backgroundColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.15)`, borderColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.5)`, color: keyColor }}>
                    {walletType}
                  </div>
                </div>
              </div>
            </div>
          </Link>
        </div>

        <Link href={`/user/${user.key}`} className="flex-1 min-w-0 space-y-2">

          <div className="flex gap-2">
            <div className="border px-2.5 py-1.5 rounded-lg backdrop-blur-sm flex-1" style={{ backgroundColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.08)`, borderColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.3)` }}>
              <div className="flex items-center gap-1.5 mb-0.5">
                <Package size={16} strokeWidth={2.5} style={{ color: keyColor }} />
                <span className="text-sm font-bold uppercase tracking-wide" style={{ color: keyColor }}>Modules</span>
              </div>
              <div className="flex items-center gap-1.5">
                <code className="text-base font-mono font-bold" style={{ color: keyColor }}>
                  {modCount}
                </code>
              </div>
            </div>

            {user.balance !== undefined && (
              <div className="border px-2.5 py-1.5 rounded-lg backdrop-blur-sm flex-1" style={{ backgroundColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.08)`, borderColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.3)` }}>
                <div className="flex items-center gap-1.5 mb-0.5">
                  <Wallet size={16} strokeWidth={2.5} style={{ color: keyColor }} />
                  <span className="text-sm font-bold uppercase tracking-wide" style={{ color: keyColor }}>Balance</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <code className="text-base font-mono font-bold" style={{ color: keyColor }}>
                    {balance.toFixed(2)}
                  </code>
                </div>
              </div>
            )}
          </div>

          {user.address && (
            <div className="border px-2.5 py-1.5 rounded-lg backdrop-blur-sm" style={{ backgroundColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.08)`, borderColor: `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.3)` }}>
              <div className="flex items-center gap-1.5 mb-0.5">
                <Hash size={16} strokeWidth={2.5} style={{ color: keyColor }} />
                <span className="text-sm font-bold uppercase tracking-wide" style={{ color: keyColor }}>Address</span>
              </div>
              <div className="flex items-center gap-1.5">
                <code className="text-base font-mono font-bold truncate flex-1" style={{ color: keyColor }} title={user.address}>
                  {shorten(user.address, 6, 6)} 
                </code>
                <CopyButton text={user.address} size="sm" />
              </div>
            </div>
          )}
        </Link>
      </div>
    </div>
  )
}
