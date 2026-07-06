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
          green: "#ffffff",
          "green-dim": "#cccccc",
          lime: "#eeeeee",
          cyan: "#ffffff",
          "cyan-dim": "#cccccc",
          magenta: "#ffffff",
          purple: "#cccccc",
          red: "#000000",
          "red-dim": "#222222",
          amber: "#ffffff",
          "amber-dim": "#cccccc",
          orange: "#ffffff",
          blue: "#cccccc",
          "blue-bright": "#ffffff",
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
        scanline: {
          "0%": { transform: "translateY(-100%)" },
          "100%": { transform: "translateY(100vh)" },
        },
        blink: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0" },
        },
        glow: {
          "0%, 100%": { textShadow: "2px 2px 0 #000000" },
          "50%": { textShadow: "3px 3px 0 #333333" },
        },
        "pixel-pulse": {
          "0%, 100%": { boxShadow: "inset 3px 3px 0 #333333, inset -3px -3px 0 #000000" },
          "50%": { boxShadow: "inset 3px 3px 0 #666666, inset -3px -3px 0 #000000" },
        },
        float: {
          "0%, 100%": { transform: "translateY(0)" },
          "50%": { transform: "translateY(-4px)" },
        },
        "mario-jump": {
          "0%, 100%": { transform: "translateY(0) scaleY(1)" },
          "40%": { transform: "translateY(-8px) scaleY(1.1)" },
          "60%": { transform: "translateY(-8px) scaleY(0.9)" },
        },
        "coin-spin": {
          "0%": { transform: "rotateY(0deg)" },
          "100%": { transform: "rotateY(360deg)" },
        },
      },
      animation: {
        scanline: "scanline 8s linear infinite",
        blink: "blink 1s step-end infinite",
        glow: "glow 2s ease-in-out infinite",
        "pixel-pulse": "pixel-pulse 3s ease-in-out infinite",
        float: "float 2s ease-in-out infinite",
        "mario-jump": "mario-jump 0.6s ease-in-out infinite",
        "coin-spin": "coin-spin 1s linear infinite",
      },
    },
  },
  plugins: [],
};
