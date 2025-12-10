'use client'
import { useState, useEffect } from 'react'
import { Github, Database, HardDrive, Cloud, Coins } from 'lucide-react'

export type UrlType = 'git' | 'ipfs' | 'arweave' | 's3' | 'filecoin'

interface UrlTypeSelectorProps {
  value: string
  onChange: (value: string, type: UrlType) => void
  selectedType: UrlType
  onTypeChange: (type: UrlType) => void
}

const URL_PATTERNS = {
  git: /^(https?:\/\/)?(github\.com|gitlab\.com|bitbucket\.org)/i,
  ipfs: /^(ipfs:\/\/|Qm[a-zA-Z0-9]{44}|baf[a-zA-Z0-9]+)/i,
  arweave: /^(ar:\/\/|[a-zA-Z0-9_-]{43})/,
  s3: /^(s3:\/\/|https?:\/\/.*\.s3\..*\.amazonaws\.com)/i,
  filecoin: /^(fil:\/\/|f[0-9][a-z0-9]+)/i
}

const URL_TYPES: { id: UrlType; label: string; icon: any; placeholder: string }[] = [
  { id: 'git', label: 'Git', icon: Github, placeholder: 'https://github.com/username/repo' },
  { id: 'ipfs', label: 'IPFS', icon: Database, placeholder: 'ipfs://Qm... or Qm...' },
  { id: 'arweave', label: 'Arweave', icon: HardDrive, placeholder: 'ar://... or arweave hash' },
  { id: 's3', label: 'S3', icon: Cloud, placeholder: 's3://bucket/key or https://...' },
  { id: 'filecoin', label: 'Filecoin', icon: Coins, placeholder: 'fil://... or filecoin CID' }
]

const inferUrlType = (url: string): UrlType => {
  for (const [type, pattern] of Object.entries(URL_PATTERNS)) {
    if (pattern.test(url)) {
      return type as UrlType
    }
  }
  return 'git'
}

export function UrlTypeSelector({ value, onChange, selectedType, onTypeChange }: UrlTypeSelectorProps) {
  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newValue = e.target.value
    const inferredType = inferUrlType(newValue)
    onChange(newValue, inferredType)
    if (newValue.trim()) {
      onTypeChange(inferredType)
    }
  }

  const currentType = URL_TYPES.find(t => t.id === selectedType) || URL_TYPES[0]
  const Icon = currentType.icon

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        {URL_TYPES.map((type) => {
          const TypeIcon = type.icon
          return (
            <button
              key={type.id}
              type="button"
              onClick={() => onTypeChange(type.id)}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg font-bold text-sm uppercase transition-all ${
                selectedType === type.id
                  ? 'bg-white text-black border-2 border-white shadow-[0_0_20px_rgba(255,255,255,0.6)]'
                  : 'bg-black/60 text-white/70 border-2 border-white/30 hover:border-white/60 hover:text-white'
              }`}
            >
              <TypeIcon size={18} />
              <span>{type.label}</span>
            </button>
          )
        })}
      </div>

      <div className="relative">
        <input
          type="text"
          value={value}
          onChange={handleInputChange}
          placeholder={currentType.placeholder}
          className="w-full bg-black/90 border-2 border-white/60 rounded-lg px-4 py-5 pl-12 text-white font-mono text-xl placeholder-white/40 focus:outline-none focus:border-white focus:shadow-[0_0_30px_rgba(255,255,255,0.5)] transition-all"
        />
        <div className="absolute left-4 top-1/2 -translate-y-1/2 text-white/70">
          <Icon size={26} />
        </div>
      </div>
    </div>
  )
}
