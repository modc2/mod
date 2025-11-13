'use client'

import { KeyIcon } from '@heroicons/react/24/outline'

export function LoginHeader() {
  return (
    <div className="flex flex-col items-center gap-8 mb-8 mt-12">
      <div 
        className="p-8 rounded-2xl border-4 bg-black/90 backdrop-blur-xl animate-pulse"
        style={{
          borderColor: '#00ff00',
          boxShadow: '0 0 40px #00ff0060, inset 0 0 30px #00ff0030',
        }}
      >
        <KeyIcon 
          className="w-24 h-24" 
          style={{ 
            color: '#00ff00',
            filter: 'drop-shadow(0 0 15px #00ff00)',
            strokeWidth: 3
          }} 
        />
      </div>
      <h1 
        className="text-7xl font-black tracking-widest uppercase"
        style={{
          color: '#00ff00',
          textShadow: '0 0 25px #00ff00, 0 0 50px #00ff0090, 0 4px 0 #008800',
          fontFamily: '"Press Start 2P", "Courier New", monospace',
          letterSpacing: '0.3em',
          imageRendering: 'pixelated',
          WebkitFontSmoothing: 'none',
          MozOsxFontSmoothing: 'grayscale'
        }}
      >
        LOGIN
      </h1>
    </div>
  )
}

export default LoginHeader