import type { Config } from 'tailwindcss'

const config: Config = {
  content: ['./src/**/*.{js,ts,jsx,tsx,mdx}'],
  theme: {
    extend: {
      colors: {
        bg: {
          DEFAULT: '#0a0a0f',
          panel: '#10101a',
          card: '#15151f',
        },
        accent: {
          DEFAULT: '#6ee7b7',
          dim: '#34d399',
        },
        muted: '#6b7280',
      },
      fontFamily: {
        mono: ['SF Mono', 'Fira Code', 'IBM Plex Mono', 'monospace'],
      },
    },
  },
  plugins: [],
}
export default config
