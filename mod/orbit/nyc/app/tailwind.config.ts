import type { Config } from 'tailwindcss'

/**
 * Chrome tokens only. Data colours live in src/lib/palette.ts, where they are
 * validated against the map surface — nothing that encodes a value should be
 * reachable as a Tailwind utility, or it will drift.
 */
const config: Config = {
  content: ['./src/**/*.{js,ts,jsx,tsx,mdx}'],
  // A touch screen holds :hover on the last thing tapped, so every `hover:`
  // utility would otherwise leave a highlight stuck behind the finger. This
  // compiles them all inside `@media (hover: hover)`.
  future: { hoverOnlyWhenSupported: true },
  theme: {
    extend: {
      colors: {
        // The `nes` namespace survives from the old 8-bit theme so every
        // component keeps its class names; the values are the night-console
        // palette. `coin` is the one warm accent (taxi gold).
        nes: {
          void: '#0a0d14',    // page
          panel: '#11151f',   // panel body
          raised: '#1b2130',  // inset controls, button rest state
          sky: '#60a5fa',
          red: '#f0564a',
          coin: '#e8b64c',
          green: '#3fb68b',
          brick: '#1b2130',
          ink: '#f2f5fa',
          ink2: '#c7cedb',
          ink3: '#8b94a7',
        },
      },
      fontFamily: {
        pixel: [
          'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI',
          'Roboto', 'sans-serif',
        ],
      },
    },
  },
  plugins: [],
}
export default config
