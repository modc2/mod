import type { Config } from 'tailwindcss'

// Every colour here is an `r g b` channel triple read from a CSS var, so a
// `[data-theme="…"]` block in globals.css repaints the whole console without
// touching a single className. Channel form (not `var(--x)`) is what keeps
// Tailwind's opacity modifiers — `text-mute/60`, `bg-accent/15` — working.
//
// The names are roles, not hues: `accent` is whatever this skin uses to point
// at things, `up`/`down` are the two signs of a number, `gold` is value at
// rest (staked, pot), `iris` is the fifth voice. Nothing in the app names a
// hue directly, which is why ten skins can be this different.
const tok = (name: string) => `rgb(var(--${name}-rgb) / <alpha-value>)`

const config: Config = {
  content: [
    './src/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        base: tok('base'),         // page field
        raise: tok('raise'),       // popovers, menus — floats above the field
        panel: tok('panel'),       // card interior
        panel2: tok('panel2'),     // nested block inside a card
        field: tok('field'),       // input / button fill
        fieldhi: tok('fieldhi'),   // that fill, hovered
        scrim: tok('scrim'),       // modal backdrop

        // Four steps of type, tuned per skin instead of alpha-faded — 25%
        // black on paper is unreadable, so light skins pick real values.
        ink: tok('ink'),
        ink2: tok('ink2'),
        mute: tok('mute'),
        faint: tok('faint'),

        hair: tok('hair'),         // the quietest divider
        line: tok('line'),         // standard border
        line2: tok('line2'),       // focused / emphasised border

        accent: tok('accent'),
        up: tok('up'),
        gold: tok('gold'),
        iris: tok('iris'),
        down: tok('down'),
      },
      fontFamily: {
        // Skins swap these two vars; a pixel cabinet and a paper ledger are
        // the same components wearing different type.
        ui: ['var(--font-ui)'],
        mono: ['var(--font-num)'],
      },
      borderRadius: {
        // Driven by the skin so PIXEL can square every corner in the tree
        // without a JSX edit.
        none: '0px',
        sm: 'var(--r-sm)',
        DEFAULT: 'var(--r-md)',
        md: 'var(--r-md)',
        lg: 'var(--r-lg)',
        xl: 'var(--r-xl)',
        '2xl': 'var(--r-xl)',
        full: 'var(--r-full)',
      },
      keyframes: {
        'fade-up': {
          '0%': { opacity: '0', transform: 'translateY(4px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'sweep': {
          '0%': { transform: 'translateX(-100%)' },
          '100%': { transform: 'translateX(100%)' },
        },
        'pulse-dot': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.35' },
        },
      },
      animation: {
        'fade-up': 'fade-up .22s var(--ease) both',
        'sweep': 'sweep 2.4s var(--ease) infinite',
        'pulse-dot': 'pulse-dot 2s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}
export default config
