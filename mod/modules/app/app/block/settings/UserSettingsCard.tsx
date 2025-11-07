import React from 'react';
import { Settings } from 'lucide-react';

interface UserSettingsCardProps {
  settings: {
    theme?: string;
    notifications?: boolean;
    language?: string;
    privacy?: string;
  };
  onUpdate?: (settings: any) => void;
}

export const UserSettingsCard: React.FC<UserSettingsCardProps> = ({ settings, onUpdate }) => {
  return (
    <div className="w-full max-w-[280px] space-y-1.5 p-2.5 rounded-lg bg-gradient-to-br from-blue-500/5 to-transparent border border-blue-500/20">
      <div className="flex items-center gap-1.5 text-blue-500/70 text-[10px] font-mono uppercase mb-1.5">
        <Settings size={11} />
        <span>USER SETTINGS</span>
      </div>
      
      <div className="space-y-0.5">
        <label className="text-[9px] text-blue-500/60 font-mono uppercase">Theme</label>
        <select
          value={settings.theme || 'dark'}
          className="w-full bg-black/50 border border-blue-500/30 rounded px-1.5 py-0.5 text-blue-400 font-mono text-[10px] focus:outline-none focus:border-blue-500"
        >
          <option value="light">light</option>
          <option value="dark">dark</option>
        </select>
      </div>
      
      <div className="space-y-0.5">
        <label className="text-[9px] text-blue-500/60 font-mono uppercase">Notifications</label>
        <input
          type="checkbox"
          checked={settings.notifications || false}
          className="w-3 h-3 bg-black/50 border border-blue-500/30 rounded text-blue-500 focus:ring-blue-500"
        />
      </div>
      
      <div className="space-y-0.5">
        <label className="text-[9px] text-blue-500/60 font-mono uppercase">Language</label>
        <select
          value={settings.language || 'en'}
          className="w-full bg-black/50 border border-blue-500/30 rounded px-1.5 py-0.5 text-blue-400 font-mono text-[10px] focus:outline-none focus:border-blue-500"
        >
          <option value="en">en</option>
          <option value="es">es</option>
          <option value="fr">fr</option>
          <option value="de">de</option>
        </select>
      </div>
      
      <div className="space-y-0.5">
        <label className="text-[9px] text-blue-500/60 font-mono uppercase">Privacy</label>
        <select
          value={settings.privacy || 'public'}
          className="w-full bg-black/50 border border-blue-500/30 rounded px-1.5 py-0.5 text-blue-400 font-mono text-[10px] focus:outline-none focus:border-blue-500"
        >
          <option value="public">public</option>
          <option value="private">private</option>
        </select>
      </div>
    </div>
  );
};