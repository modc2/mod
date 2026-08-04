import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './src/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      // Every colour resolves to a CSS variable defined in globals.css, so the
      // DIGITAL mode block there repaints all of these at once. Nothing in the
      // app should carry a literal hex.
      colors: {
        // The page is written in `text-white/40`, `bg-white/[0.03]`, `border-white/10`.
        // On a pastel paper background those all need to be INK, not white — so `white`
        // is re-pointed at the ink token here instead of rewriting ~200 utility classes.
        white: 'rgb(var(--ink-rgb) / <alpha-value>)',
        ink: 'rgb(var(--ink-rgb) / <alpha-value>)',
        paper: 'rgb(var(--paper-rgb) / <alpha-value>)',
        // Type that sits on a bright accent fill — dark ink on paper, near-black
        // on phosphor. It can't follow --ink, because --ink inverts.
        onaccent: 'rgb(var(--on-accent-rgb) / <alpha-value>)',
        // `emerald` is the "owner income / done" green everywhere. Pastel surf mint.
        emerald: {
          300: 'rgb(var(--em-300-rgb) / <alpha-value>)',
          400: 'rgb(var(--em-400-rgb) / <alpha-value>)',
          500: 'rgb(var(--em-500-rgb) / <alpha-value>)',
          600: 'rgb(var(--em-600-rgb) / <alpha-value>)',
        },
        coral: 'rgb(var(--coral-rgb) / <alpha-value>)',
        peach: 'rgb(var(--peach-rgb) / <alpha-value>)',
        ember: 'rgb(var(--ember-rgb) / <alpha-value>)',
        pink: 'rgb(var(--pink-rgb) / <alpha-value>)',
        mint: 'rgb(var(--mint-rgb) / <alpha-value>)',
        sky: 'rgb(var(--sky-rgb) / <alpha-value>)',
        sun: 'rgb(var(--sun-rgb) / <alpha-value>)',
        lilac: 'rgb(var(--lilac-rgb) / <alpha-value>)',
      },
    },
  },
  plugins: [],
}
export default config
