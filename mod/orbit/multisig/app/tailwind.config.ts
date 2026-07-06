import type { Config } from 'tailwindcss'

const config: Config = {
  content: ['./app/**/*.{js,ts,jsx,tsx,mdx}'],
  theme: {
    extend: {
      colors: {
        base: { DEFAULT: '#0052FF', dark: '#003ACC' },
        tao: { DEFAULT: '#1A1A2E', dark: '#0F0F1A' },
        sol: { DEFAULT: '#9945FF', dark: '#7B2FE0' },
      },
    },
  },
  plugins: [],
}
export default config
