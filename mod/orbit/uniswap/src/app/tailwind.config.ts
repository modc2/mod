import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        uni: {
          pink: "#ff007a",
          purple: "#7b3fe4",
          dark: "#0d0d0d",
          card: "#1a1a2e",
          border: "#2a2a4a",
          muted: "#8888aa",
          green: "#4ade80",
          red: "#f87171",
        },
      },
      fontFamily: {
        mono: ["JetBrains Mono", "Fira Code", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
