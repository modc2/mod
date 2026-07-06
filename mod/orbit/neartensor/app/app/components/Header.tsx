"use client";

interface HeaderProps {
  activeTab: string;
  onTabChange: (tab: string) => void;
}

const tabs = [
  { id: "subnets", label: "Subnets" },
  { id: "validators", label: "Validators" },
  { id: "staking", label: "Staking" },
  { id: "registry", label: "Registry" },
];

export default function Header({ activeTab, onTabChange }: HeaderProps) {
  return (
    <header className="border-b border-nt-border bg-nt-panel/50 backdrop-blur-sm sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-4 py-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h1 className="text-lg font-bold text-nt-accent">NearTensor</h1>
            <span className="text-xs text-nt-muted">subnet protocol</span>
          </div>
          <nav className="flex gap-1">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => onTabChange(tab.id)}
                className={`px-3 py-1.5 text-xs rounded transition-colors ${
                  activeTab === tab.id
                    ? "bg-nt-accent/10 text-nt-accent border border-nt-accent/30"
                    : "text-nt-muted hover:text-nt-text hover:bg-nt-border/50"
                }`}
              >
                {tab.label}
              </button>
            ))}
          </nav>
        </div>
      </div>
    </header>
  );
}
