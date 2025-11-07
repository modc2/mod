import React from 'react';
import { Settings } from 'lucide-react';

interface SettingsField {
  key: string;
  label: string;
  type: 'select' | 'checkbox' | 'number' | 'multicheck';
  options?: string[] | { value: string; label: string }[];
  min?: number;
  max?: number;
}

interface SettingsBlockProps {
  title: string;
  settings: Record<string, any>;
  fields: SettingsField[];
  onUpdate?: (settings: any) => void;
}

export const SettingsBlock: React.FC<SettingsBlockProps> = ({ title, settings, fields, onUpdate }) => {
  return (
    <div className="w-full max-w-[280px] space-y-1.5 p-2.5 rounded-lg bg-gradient-to-br from-green-500/5 to-transparent border border-green-500/20">
      <div className="flex items-center gap-1.5 text-green-500/70 text-[10px] font-mono uppercase mb-1.5">
        <Settings size={11} />
        <span>{title}</span>
      </div>
      
      {fields.map((field) => (
        <div key={field.key} className="space-y-0.5">
          <label className="text-[9px] text-green-500/60 font-mono uppercase">{field.label}</label>
          
          {field.type === 'select' && (
            <select
              value={settings[field.key] || ''}
              className="w-full bg-black/50 border border-green-500/30 rounded px-1.5 py-0.5 text-green-400 font-mono text-[10px] focus:outline-none focus:border-green-500"
            >
              {field.options?.map((opt) => {
                const value = typeof opt === 'string' ? opt : opt.value;
                const label = typeof opt === 'string' ? opt : opt.label;
                return <option key={value} value={value}>{label}</option>;
              })}
            </select>
          )}
          
          {field.type === 'checkbox' && (
            <input
              type="checkbox"
              checked={settings[field.key] || false}
              className="w-3 h-3 bg-black/50 border border-green-500/30 rounded text-green-500 focus:ring-green-500"
            />
          )}
          
          {field.type === 'number' && (
            <input
              type="number"
              value={settings[field.key] || field.min || 0}
              min={field.min}
              max={field.max}
              className="w-full bg-black/50 border border-green-500/30 rounded px-1.5 py-0.5 text-green-400 font-mono text-[10px] focus:outline-none focus:border-green-500"
            />
          )}
          
          {field.type === 'multicheck' && (
            <div className="flex flex-wrap gap-1">
              {field.options?.map((opt) => {
                const value = typeof opt === 'string' ? opt : opt.value;
                const label = typeof opt === 'string' ? opt : opt.label;
                return (
                  <label key={value} className="flex items-center gap-0.5 text-[9px]">
                    <input
                      type="checkbox"
                      checked={settings[field.key]?.includes(value) || false}
                      className="w-2.5 h-2.5 bg-black/50 border border-green-500/30 rounded text-green-500"
                    />
                    <span className="text-green-400 font-mono">{label}</span>
                  </label>
                );
              })}
            </div>
          )}
        </div>
      ))}
    </div>
  );
};