import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Deep space-black with a cold blue undertone — the canvas.
        bg: "#04060a",
        bg2: "#070a10",
        // Glass panels are layered translucent surfaces (see globals.css);
        // these solid mirrors stay for any opaque consumers.
        panel: "#0b0f16",
        panel2: "#10151f",
        border: "#1a2230",
        ink: "#eef4fb",
        muted: "#7d8a9c",
        dim: "#566273",
        // Hyperliquid mint-teal is the hero color; cyan is the cool second.
        accent: "#3ce0c8",
        accent2: "#5cc8ff",
        warn: "#ffcc4d",
        loss: "#ff5d73",
        win: "#3ce0c8",
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
        glow: "0 0 0 1px rgba(60,224,200,0.18), 0 0 22px -6px rgba(60,224,200,0.45)",
        "glow-lg": "0 0 0 1px rgba(60,224,200,0.22), 0 0 48px -8px rgba(60,224,200,0.55)",
        panel:
          "0 1px 0 rgba(255,255,255,0.04) inset, 0 18px 40px -22px rgba(0,0,0,0.9)",
        lift: "0 20px 50px -24px rgba(0,0,0,0.85), 0 0 0 1px rgba(255,255,255,0.04)",
      },
      backgroundImage: {
        "panel-grad":
          "linear-gradient(160deg, rgba(255,255,255,0.045), rgba(255,255,255,0.01) 38%, rgba(0,0,0,0.18))",
        "accent-grad": "linear-gradient(135deg, #3ce0c8 0%, #5cc8ff 100%)",
        "text-grad": "linear-gradient(120deg, #eef4fb 0%, #3ce0c8 55%, #5cc8ff 100%)",
        "grid-faint":
          "linear-gradient(rgba(255,255,255,0.02) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.02) 1px, transparent 1px)",
      },
      keyframes: {
        fadeUp: {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: { "100%": { transform: "translateX(100%)" } },
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
        floaty: "floaty 22s ease-in-out infinite",
        pulseGlow: "pulseGlow 2.4s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
export default config;
