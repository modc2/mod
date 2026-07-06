/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: "class",
  content: ["./app/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // CSS variables flip between light and dark in globals.css.
        modblue: "rgb(var(--modblue) / <alpha-value>)",
        modcyan: "rgb(var(--modcyan) / <alpha-value>)",
        paper:   "rgb(var(--paper) / <alpha-value>)",
        panel:   "rgb(var(--panel) / <alpha-value>)",
        rule:    "rgb(var(--rule) / <alpha-value>)",
        ink:     "rgb(var(--ink) / <alpha-value>)",
        muted:   "rgb(var(--muted) / <alpha-value>)",
      },
      fontFamily: {
        serif: ["Charter", "Georgia", "ui-serif", "serif"],
        mono: ["JetBrains Mono", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};
