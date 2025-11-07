import React from 'react';
import { SettingsBlock } from './SettingsBlock';

interface ModSettingsProps {
  settings: {
    visibility?: string;
    autoApprove?: boolean;
    maxUsers?: number;
    permissions?: string[];
    rateLimit?: number;
  };
  onUpdate?: (settings: any) => void;
}

const MOD_FIELDS = [
  { key: 'visibility', label: 'Visibility', type: 'select' as const, options: ['public', 'private', 'unlisted'] },
  { key: 'autoApprove', label: 'Auto Approve', type: 'checkbox' as const },
  { key: 'maxUsers', label: 'Max Users', type: 'number' as const, min: 1, max: 10000 },
  { key: 'rateLimit', label: 'Rate Limit', type: 'number' as const, min: 1, max: 1000 },
  { key: 'permissions', label: 'Permissions', type: 'multicheck' as const, options: ['read', 'write', 'delete', 'admin'] }
];

export const ModSettings: React.FC<ModSettingsProps> = ({ settings, onUpdate }) => {
  return <SettingsBlock title="MOD SETTINGS" settings={settings} fields={MOD_FIELDS} onUpdate={onUpdate} />;
};