/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        ink: "var(--ink)",
        panel: "var(--panel)",
        edge: "var(--edge)",
        fg: "var(--fg)",
        muted: "var(--muted)",
        accent: "var(--accent)",
        up: "var(--up)",
        down: "var(--down)",
        warn: "var(--warn)",
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};
