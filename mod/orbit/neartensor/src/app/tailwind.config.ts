import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        nt: {
          bg: "#0a0a0f",
          panel: "#111118",
          border: "#1e1e2e",
          accent: "#00d4aa",
          accent2: "#7c3aed",
          muted: "#6b7280",
          text: "#e5e7eb",
          green: "#10b981",
          red: "#ef4444",
          yellow: "#f59e0b",
        },
      },
      fontFamily: {
        mono: ["IBM Plex Mono", "monospace"],
      },
    },
  },
  plugins: [],
};
export default config;
