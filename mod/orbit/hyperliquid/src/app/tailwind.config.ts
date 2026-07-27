import type { Config } from "tailwindcss";

// Every color routes through a CSS variable (space-separated RGB) declared in
// globals.css, so html.light / html.dark swap the entire palette at once.
const v = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;

const config: Config = {
  content: ["./app/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: v("c-bg"),
        bg2: v("c-bg2"),
        // Glass panels are layered translucent surfaces (see globals.css);
        // these solid mirrors stay for any opaque consumers.
        panel: v("c-panel"),
        panel2: v("c-panel2"),
        border: v("c-border"),
        ink: v("c-ink"),
        muted: v("c-muted"),
        dim: v("c-dim"),
        // Hyperliquid mint-teal is the hero color; cyan is the cool second.
        accent: v("c-accent"),
        accent2: v("c-accent2"),
        warn: v("c-warn"),
        loss: v("c-loss"),
        win: v("c-win"),
        // The glass trick: `white`/`black` alpha utilities (hairlines, fills,
        // tooltips) become "high/low contrast vs the surface" — dark hairlines
        // on light, light hairlines on dark — so the glasswork flips free.
        white: v("c-glass-hi"),
        black: v("c-glass-lo"),
      },
      fontFamily: {
        display: ["'Space Grotesk'", "ui-sans-serif", "system-ui", "sans-serif"],
        sans: ["'Inter'", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["'JetBrains Mono'", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      borderRadius: {
        sm: "7px",
        DEFAULT: "11px",
        md: "13px",
        lg: "17px",
        xl: "22px",
      },
      boxShadow: {
        glow: "var(--shadow-glow)",
        "glow-lg": "var(--shadow-glow-lg)",
        panel: "var(--shadow-panel)",
        lift: "var(--shadow-lift)",
      },
      backgroundImage: {
        "panel-grad": "var(--panel-grad)",
        "accent-grad": "var(--accent-grad)",
        "text-grad": "var(--text-grad)",
      },
      keyframes: {
        fadeUp: {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: { "100%": { transform: "translateX(100%)" } },
        ticker: {
          "0%": { transform: "translateX(0)" },
          "100%": { transform: "translateX(-50%)" },
        },
        floaty: {
          "0%,100%": { transform: "translate(0,0)" },
          "50%": { transform: "translate(3%,4%)" },
        },
        pulseGlow: {
          "0%,100%": { opacity: "0.55" },
          "50%": { opacity: "1" },
        },
      },
      animation: {
        fadeUp: "fadeUp 0.5s cubic-bezier(0.22,1,0.36,1) both",
        shimmer: "shimmer 1.6s infinite",
        ticker: "ticker 60s linear infinite",
        floaty: "floaty 22s ease-in-out infinite",
        pulseGlow: "pulseGlow 2.4s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
export default config;
