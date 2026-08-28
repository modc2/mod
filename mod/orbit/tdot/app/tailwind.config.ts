import type { Config } from 'tailwindcss'

/**
 * The colour names below are the theme tokens from globals.css, not literals —
 * `text-ink`, `border-line`, `bg-accent` resolve to whatever the active
 * `data-theme` block sets. They carry their own alpha, so use the token that
 * means what you want (`fill-hover`, `line-strong`) rather than an opacity
 * modifier like `/10`, which can't apply to a `var()` colour.
 */
const config: Config = {
  content: ['./src/**/*.{js,ts,jsx,tsx,mdx}'],
  theme: {
    extend: {
      colors: {
        bg: 'var(--bg)',
        surface: 'var(--surface)',
        'surface-solid': 'var(--surface-solid)',
        fill: 'var(--fill)',
        'fill-hover': 'var(--fill-hover)',
        'fill-strong': 'var(--fill-strong)',
        inset: 'var(--inset)',
        ink: 'var(--ink)',
        'ink-2': 'var(--ink-2)',
        muted: 'var(--muted)',
        line: 'var(--line)',
        'line-strong': 'var(--line-strong)',
        accent: 'var(--accent)',
        'accent-ink': 'var(--accent-ink)',
        'accent-soft': 'var(--accent-soft)',
        good: 'var(--good)',
        bad: 'var(--bad)',
      },
      borderRadius: {
        panel: 'var(--radius)',
        ctl: 'var(--radius-sm)',
      },
    },
  },
  plugins: [],
}
export default config
