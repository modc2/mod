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
        // Accent keys are the fixed arcade-neon palette: five hues (lime,
        // cyan, magenta, amber, red) are the whole vocabulary, so nothing on
        // screen can drift into a sixth colour.
        pixel: {
          black: "rgb(var(--pixel-black-rgb) / <alpha-value>)",
          bg: "rgb(var(--pixel-bg-rgb) / <alpha-value>)",
          panel: "rgb(var(--pixel-panel-rgb) / <alpha-value>)",
          border: "rgb(var(--pixel-border-rgb) / <alpha-value>)",
          white: "rgb(var(--pixel-white-rgb) / <alpha-value>)",
          gray: "rgb(var(--pixel-gray-rgb) / <alpha-value>)",
          "gray-light": "rgb(var(--pixel-gray-light-rgb) / <alpha-value>)",
          green: "#2bff88",
          "green-dim": "#14b85e",
          lime: "#b6ff3c",
          cyan: "#22f0ff",
          "cyan-dim": "#0fa9bd",
          magenta: "#ff2fb9",
          purple: "#a05cff",
          red: "#ff3355",
          "red-dim": "#c01438",
          amber: "#ffcf28",
          "amber-dim": "#c99400",
          orange: "#ff8a1f",
          blue: "#3d8bff",
          "blue-bright": "#71b0ff",
        },
      },
      fontFamily: {
        // Three faces, three jobs — see globals.css. `font-pixel` is the body
        // default (Silkscreen: compact bitmap, holds up in dense tables),
        // `font-display` is the logo voice (Press Start 2P, used sparingly),
        // `font-mono` carries every number and address (VT323, terminal CRT).
        pixel: ['Silkscreen', '"Press Start 2P"', 'ui-monospace', 'monospace'],
        mono: ['VT323', 'Silkscreen', 'ui-monospace', 'monospace'],
        display: ['"Press Start 2P"', 'Silkscreen', 'ui-monospace', 'monospace'],
        sans: ['Silkscreen', 'ui-monospace', 'monospace'],
      },
      borderRadius: {
        // Nothing is round on an 8-bit screen. Overriding `full` too means
        // every `rounded-full` pill/dot already in the component tree snaps
        // to a square LED without touching a single JSX file.
        none: '0px',
        sm: '0px',
        DEFAULT: '0px',
        md: '0px',
        lg: '0px',
        xl: '0px',
        '2xl': '0px',
        '3xl': '0px',
        full: '0px',
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
          "0%, 100%": { textShadow: "0 0 6px currentColor" },
          "50%": { textShadow: "0 0 14px currentColor, 0 0 26px currentColor" },
        },
        "pixel-pulse": {
          "0%, 100%": { boxShadow: "0 0 0 0 rgba(34, 240, 255, 0.5)" },
          "50%": { boxShadow: "0 0 0 4px rgba(34, 240, 255, 0)" },
        },
        float: {
          "0%, 100%": { transform: "translateY(0)" },
          "50%": { transform: "translateY(-4px)" },
        },
        "mario-jump": {
          // Stepped, not eased — in-betweens are a 3D idea. 8-bit sprites snap.
          "0%, 100%": { transform: "translateY(0)" },
          "25%": { transform: "translateY(-4px)" },
          "50%": { transform: "translateY(-8px)" },
          "75%": { transform: "translateY(-4px)" },
        },
        "coin-spin": {
          "0%":   { transform: "scaleX(1)" },
          "25%":  { transform: "scaleX(0.35)" },
          "50%":  { transform: "scaleX(0.1)" },
          "75%":  { transform: "scaleX(0.35)" },
          "100%": { transform: "scaleX(1)" },
        },
      },
      animation: {
        // Every duration is stepped. Smooth easing is the one thing an 8-bit
        // console could never do, so it's the fastest way to break the spell.
        scanline: "scanline 8s linear infinite",
        blink: "blink 1s step-end infinite",
        glow: "glow 2s steps(8, end) infinite",
        "pixel-pulse": "pixel-pulse 2s steps(6, end) infinite",
        float: "float 2s steps(4, end) infinite",
        "mario-jump": "mario-jump 0.6s steps(4, end) infinite",
        "coin-spin": "coin-spin 1s steps(4, end) infinite",
      },
    },
  },
  plugins: [],
};
