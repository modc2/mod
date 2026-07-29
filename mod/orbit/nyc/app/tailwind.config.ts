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
        nes: {
          void: '#0a0a18',    // page / underground
          panel: '#0e1330',   // panel body
          raised: '#1a2258',  // inset controls, button rest state
          sky: '#5c94fc',
          red: '#e52521',
          coin: '#fbd000',
          green: '#43b047',
          brick: '#c1440e',
          ink: '#ffffff',
          ink2: '#ccd3f2',
          ink3: '#8f98c8',
        },
      },
      fontFamily: {
        pixel: ['var(--font-pixel)', 'ui-monospace', 'monospace'],
      },
    },
  },
  plugins: [],
}
export default config
