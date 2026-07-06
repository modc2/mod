"use client";

import { useTheme } from "../context/ThemeContext";

// Bubble-style theme toggle. Two rounded pills side-by-side (sun/moon),
// active state filled with the same accent glow used by AssetChip. Sits
// next to other top-bar chips and stays visually quiet until hovered.
export default function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  return (
    <div
      className="inline-flex items-center gap-0.5 rounded-full border p-0.5"
      style={{
        background: "var(--btn-bg)",
        borderColor: "var(--border-strong)",
      }}
      role="group"
      aria-label="Theme"
    >
      <ThemeBubble active={theme === "dark"} onClick={() => setTheme("dark")} title="Dark mode">
        ☾
      </ThemeBubble>
      <ThemeBubble active={theme === "light"} onClick={() => setTheme("light")} title="Light mode">
        ☀
      </ThemeBubble>
    </div>
  );
}

function ThemeBubble({
  active,
  onClick,
  title,
  children,
}: {
  active: boolean;
  onClick: () => void;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      aria-pressed={active}
      className="inline-flex items-center justify-center rounded-full w-[22px] h-[22px] text-[12px] leading-none transition-all"
      style={
        active
          ? {
              background: "var(--fg)",
              color: "var(--bg)",
              boxShadow: "0 0 8px var(--border-strong)",
            }
          : {
              background: "transparent",
              color: "var(--fg-muted)",
            }
      }
    >
      {children}
    </button>
  );
}
