'use client'

import { motion } from 'framer-motion'
import { useEffect, useState } from 'react'
import Link from 'next/link'

const ASCII_PATTERNS = [
  '░▒▓█▓▒░',
  '▀▄▀▄▀▄',
  '◢◣◥◤',
  '▓▒░▒▓',
  '█▓▒░▒▓█',
  '╔═╗╚═╝',
  '┌─┐└─┘',
  '▲▼◄►',
]

const MATRIX_CHARS = '01ΞΨΩαβγδεζηθικλμνξοπρστυφχψω░▒▓█<>{}[]|/\\'

export default function Home() {
  const [matrixRain, setMatrixRain] = useState<string[][]>([])
  const [pattern, setPattern] = useState(0)
  const [glitchText, setGlitchText] = useState('MODCHAIN')

  useEffect(() => {
    const cols = Math.floor(window.innerWidth / 20)
    const rows = 30
    const rain = Array(cols).fill(0).map(() => 
      Array(rows).fill(0).map(() => MATRIX_CHARS[Math.floor(Math.random() * MATRIX_CHARS.length)])
    )
    setMatrixRain(rain)

    const interval = setInterval(() => {
      setMatrixRain(prev => prev.map(col => {
        const newCol = [...col]
        newCol.shift()
        newCol.push(MATRIX_CHARS[Math.floor(Math.random() * MATRIX_CHARS.length)])
        return newCol
      }))
    }, 100)

    const patternInterval = setInterval(() => {
      setPattern(p => (p + 1) % ASCII_PATTERNS.length)
    }, 2000)

    const glitchInterval = setInterval(() => {
      const glitchChars = 'MODCHAIN!@#$%^&*()_+-=[]{}|;:,.<>?/~`'
      setGlitchText(prev => 
        Math.random() > 0.7 ? 
        prev.split('').map(c => glitchChars[Math.floor(Math.random() * glitchChars.length)]).join('') :
        'MODCHAIN'
      )
    }, 150)

    return () => {
      clearInterval(interval)
      clearInterval(patternInterval)
      clearInterval(glitchInterval)
    }
  }, [])

  return (
    <div className="min-h-screen bg-black relative overflow-hidden font-mono">
      {/* Ising Model Background */}
      <div className="absolute inset-0 opacity-10 pointer-events-none">
        <div className="flex gap-1 text-green-500 text-xs">
          {matrixRain.map((col, i) => (
            <div key={i} className="flex flex-col">
              {col.map((char, j) => (
                <span key={j} className={j === col.length - 1 ? 'text-white' : ''}>
                  {char}
                </span>
              ))}
            </div>
          ))}
        </div>
      </div>

      {/* Scanlines */}
      <div className="absolute inset-0 pointer-events-none" style={{
        backgroundImage: 'repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,255,0,0.03) 2px, rgba(0,255,0,0.03) 4px)'
      }} />

      {/* Main Content */}
      <div className="relative z-10 flex flex-col items-center justify-center min-h-screen px-4">
        
        {/* ASCII Pattern Border */}
        <motion.div
          animate={{ opacity: [0.3, 0.7, 0.3] }}
          transition={{ duration: 2, repeat: Infinity }}
          className="text-green-500 text-2xl mb-8 tracking-widest"
        >
          {ASCII_PATTERNS[pattern].repeat(10)}
        </motion.div>

        {/* Glitch Title */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="mb-8 relative"
        >
          <div className="text-7xl font-bold text-white tracking-wider relative">
            <span className="absolute inset-0 text-cyan-400" style={{ clipPath: 'inset(0 0 50% 0)', transform: 'translateX(-3px)' }}>
              {glitchText}
            </span>
            <span className="absolute inset-0 text-purple-500" style={{ clipPath: 'inset(50% 0 0 0)', transform: 'translateX(3px)' }}>
              {glitchText}
            </span>
            <span className="relative">
              MODCHAIN
            </span>
          </div>
          <div className="text-center text-white text-lg mt-3 tracking-[0.5em]">
            ▓▒░ DECENTRALIZED MODULE REGISTRY ░▒▓
          </div>
        </motion.div>

        {/* Core Info Box */}
        <motion.div
          animate={{ 
            boxShadow: ['0 0 20px rgba(0,255,255,0.3)', '0 0 40px rgba(138,43,226,0.6)', '0 0 20px rgba(0,255,255,0.3)']
          }}
          transition={{ duration: 2, repeat: Infinity }}
          className="border-2 border-cyan-400 p-8 bg-black/95 backdrop-blur-sm mb-8 max-w-4xl rounded-lg"
        >
          <pre className="text-white text-sm leading-relaxed">
{`╔═══════════════════════════════════════════════════════════╗
║  MODCHAIN: BLOCKCHAIN-BASED MODULE REGISTRY               ║
╠═══════════════════════════════════════════════════════════╣
║                                                           ║
║  WHAT IS MODCHAIN?                                        ║
║  • Decentralized app store WITHOUT gatekeepers            ║
║  • Software modules stored on-chain (immutable)           ║
║  • Cryptographically verified & tamper-proof              ║
║  • Permissionless publishing & discovery                  ║
║                                                           ║
║  HOW IT WORKS:                                            ║
║  1. Developer creates MOD                                 ║
║  2. MOD cryptographically signed                          ║
║  3. Published to blockchain (permanent record)            ║
║  4. Users discover & verify MODS trustlessly              ║
║  5. Zero middlemen, zero censorship                       ║
║                                                           ║
║  WHY MODCHAIN?                                            ║
║  • Code is law, not corporations                          ║
║  • Cryptography protects freedom                          ║
║  • Decentralization prevents control                      ║
║  • Build & share without permission                       ║
║                                                           ║
║  TL;DR: npm + blockchain = unstoppable code distribution  ║
╚═══════════════════════════════════════════════════════════╝`}
          </pre>
        </motion.div>

        {/* CTA Buttons */}
        <div className="flex gap-6">
          <motion.div 
            whileHover={{ scale: 1.05, boxShadow: '0 0 30px rgba(0,255,255,0.6)' }} 
            whileTap={{ scale: 0.95 }}
          >
            <Link
              href="/mod/explore"
              className="px-8 py-4 border-2 border-cyan-400 bg-cyan-400/10 text-white font-bold hover:bg-cyan-400 hover:text-black transition-all relative overflow-hidden group block rounded-lg"
            >
              <span className="relative z-10">> EXPLORE_MODS</span>
            </Link>
          </motion.div>
          
          <motion.div 
            whileHover={{ scale: 1.05, boxShadow: '0 0 30px rgba(138,43,226,0.6)' }} 
            whileTap={{ scale: 0.95 }}
          >
            <Link
              href="/user/explore"
              className="px-8 py-4 border-2 border-purple-500 bg-purple-500/10 text-white font-bold hover:bg-purple-500 hover:text-black transition-all block rounded-lg"
            >
              > JOIN_NETWORK
            </Link>
          </motion.div>
        </div>

        {/* Bottom Pattern */}
        <motion.div
          animate={{ opacity: [0.3, 0.7, 0.3] }}
          transition={{ duration: 2, repeat: Infinity, delay: 1 }}
          className="text-cyan-400 text-2xl mt-8 tracking-widest"
        >
          {ASCII_PATTERNS[(pattern + 4) % ASCII_PATTERNS.length].repeat(10)}
        </motion.div>

        {/* Terminal Cursor */}
        <motion.div
          animate={{ opacity: [1, 0] }}
          transition={{ duration: 0.8, repeat: Infinity }}
          className="text-cyan-400 text-3xl mt-3"
        >
          █
        </motion.div>
      </div>
    </div>
  )
}
