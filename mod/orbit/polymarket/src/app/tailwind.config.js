/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        // The `pixel.*` keys that flip between themes go through CSS vars
        // defined in globals.css (`--pixel-*-rgb` channels for dark in :root,
        // light values under [data-theme="light"]). Channel-style vars keep
        // Tailwind's opacity modifiers (`text-pixel-white/60`) working —
        // pure `var()` colors break the alpha-value placeholder substitution.
        // Accent keys (green/cyan/amber/etc.) stay fixed brand colors.
        pixel: {
          black: "rgb(var(--pixel-black-rgb) / <alpha-value>)",
          bg: "rgb(var(--pixel-bg-rgb) / <alpha-value>)",
          panel: "rgb(var(--pixel-panel-rgb) / <alpha-value>)",
          border: "rgb(var(--pixel-border-rgb) / <alpha-value>)",
          white: "rgb(var(--pixel-white-rgb) / <alpha-value>)",
          gray: "rgb(var(--pixel-gray-rgb) / <alpha-value>)",
          "gray-light": "rgb(var(--pixel-gray-light-rgb) / <alpha-value>)",
          // Aliases used across the wallet/portfolio panels — previously
          // undefined, so `text-pixel-muted` etc. compiled to nothing and
          // helper text rendered full-brightness with no hierarchy.
          fg: "rgb(var(--pixel-white-rgb) / <alpha-value>)",
          muted: "rgb(var(--pixel-gray-rgb) / <alpha-value>)",
          "border-light": "rgb(var(--pixel-border-rgb) / <alpha-value>)",
          // Semantic accents — wired to the real signature palette (globals.css
          // --accent/--accent-2/--accent-3/--danger/--warn) instead of the old
          // monochrome stub values, so `text-pixel-green` etc. render actual
          // brand color rather than white/black placeholders.
          green: "rgb(var(--accent) / <alpha-value>)",
          "green-dim": "rgb(var(--accent) / <alpha-value>)",
          lime: "rgb(var(--accent) / <alpha-value>)",
          cyan: "rgb(var(--accent-2) / <alpha-value>)",
          "cyan-dim": "rgb(var(--accent-2) / <alpha-value>)",
          magenta: "rgb(var(--accent-3) / <alpha-value>)",
          purple: "rgb(var(--accent-3) / <alpha-value>)",
          red: "rgb(var(--danger) / <alpha-value>)",
          "red-dim": "rgb(var(--danger) / <alpha-value>)",
          amber: "rgb(var(--warn) / <alpha-value>)",
          "amber-dim": "rgb(var(--warn) / <alpha-value>)",
          orange: "rgb(var(--warn) / <alpha-value>)",
          blue: "rgb(var(--accent-2) / <alpha-value>)",
          "blue-bright": "rgb(var(--accent-2) / <alpha-value>)",
        },
      },
      fontFamily: {
        // Vibe overhaul: `font-pixel` (used widely across components) now
        // resolves to Inter. `font-mono` is JetBrains Mono for numerics.
        // `font-display` is Space Grotesk for headlines.
        pixel: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SF Mono', 'monospace'],
        display: ['"Space Grotesk"', 'Inter', 'sans-serif'],
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'sans-serif'],
      },
      borderRadius: {
        // Roomy radii — overrides tailwind defaults to give the whole UI
        // a uniform vibey roundness.
        sm:  'var(--radius-sm)',
        DEFAULT: 'var(--radius)',
        md:  'var(--radius)',
        lg:  'var(--radius-lg)',
        xl:  'var(--radius-xl)',
      },
      keyframes: {
        float: {
          "0%, 100%": { transform: "translateY(0)" },
          "50%": { transform: "translateY(-4px)" },
        },
      },
      animation: {
        float: "float 2s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
