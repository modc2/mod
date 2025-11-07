import React from 'react';
import { Settings } from 'lucide-react';

interface ModSettingsCardProps {
  settings: {
    visibility?: string;
    autoApprove?: boolean;
    maxUsers?: number;
    permissions?: string[];
    rateLimit?: number;
  };
  onUpdate?: (settings: any) => void;
}

export const ModSettingsCard: React.FC<ModSettingsCardProps> = ({ settings, onUpdate }) => {
  return (
    <div className="w-full max-w-[280px] space-y-1.5 p-2.5 rounded-lg bg-gradient-to-br from-green-500/5 to-transparent border border-green-500/20">
      <div className="flex items-center gap-1.5 text-green-500/70 text-[10px] font-mono uppercase mb-1.5">
        <Settings size={11} />
        <span>MOD SETTINGS</span>
      </div>
      
      <div className="space-y-0.5">
        <label className="text-[9px] text-green-500/60 font-mono uppercase">Visibility</label>
        <select
          value={settings.visibility || 'public'}
          className="w-full bg-black/50 border border-green-500/30 rounded px-1.5 py-0.5 text-green-400 font-mono text-[10px] focus:outline-none focus:border-green-500"
        >
          <option value="public">public</option>
          <option value="private">private</option>
          <option value="unlisted">unlisted</option>
        </select>
      </div>
      
      <div className="space-y-0.5">
        <label className="text-[9px] text-green-500/60 font-mono uppercase">Auto Approve</label>
        <input
          type="checkbox"
          checked={settings.autoApprove || false}
          className="w-3 h-3 bg-black/50 border border-green-500/30 rounded text-green-500 focus:ring-green-500"
        />
      </div>
      
      <div className="space-y-0.5">
        <label className="text-[9px] text-green-500/60 font-mono uppercase">Max Users</label>
        <input
          type="number"
          value={settings.maxUsers || 100}
          min={1}
          max={10000}
          className="w-full bg-black/50 border border-green-500/30 rounded px-1.5 py-0.5 text-green-400 font-mono text-[10px] focus:outline-none focus:border-green-500"
        />
      </div>
      
      <div className="space-y-0.5">
        <label className="text-[9px] text-green-500/60 font-mono uppercase">Rate Limit</label>
        <input
          type="number"
          value={settings.rateLimit || 100}
          min={1}
          max={1000}
          className="w-full bg-black/50 border border-green-500/30 rounded px-1.5 py-0.5 text-green-400 font-mono text-[10px] focus:outline-none focus:border-green-500"
        />
      </div>
    </div>
  );
};