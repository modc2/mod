/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0b0d10",
        panel: "#13161b",
        panel2: "#1a1e25",
        border: "#2a2f38",
        text: "#e6e8eb",
        muted: "#8a93a3",
        accent: "#6ee7ff",
        critical: "#ff5470",
        high: "#ff9f43",
        medium: "#ffd166",
        low: "#7ad8a3",
        info: "#6ee7ff",
      },
      fontFamily: {
        mono: ['"JetBrains Mono"', '"IBM Plex Mono"', "ui-monospace", "monospace"],
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
