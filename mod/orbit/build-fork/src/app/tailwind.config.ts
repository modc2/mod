import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        pixel: ['"Inter"', '-apple-system', 'BlinkMacSystemFont', 'sans-serif'],
        code: ['"JetBrains Mono"', '"SF Mono"', 'Monaco', '"Cascadia Code"', '"Fira Code"', '"Courier New"', "monospace"],
      },
      colors: {
        crt: {
          green: "#10b981",
          amber: "#f59e0b",
          blue: "#3b82f6",
          red: "#ef4444",
          dark: "#f8fafc",
          darker: "#f1f5f9",
          gray: "#e2e8f0",
        },
      },
      borderRadius: {
        'sm': '8px',
        'md': '12px',
        'lg': '16px',
      },
      animation: {
        blink: "blink 1s step-end infinite",
      },
      keyframes: {
        blink: {
          "50%": { opacity: "0" },
        },
      },
    },
  },
  plugins: [],
};
export default config;
