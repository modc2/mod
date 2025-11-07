import React from 'react';
import { SettingsBlock } from './SettingsBlock';

interface UserSettingsProps {
  settings: {
    theme?: string;
    notifications?: boolean;
    language?: string;
    privacy?: string;
  };
  onUpdate?: (settings: any) => void;
}

const USER_FIELDS = [
  { key: 'theme', label: 'Theme', type: 'select' as const, options: ['light', 'dark'] },
  { key: 'notifications', label: 'Notifications', type: 'checkbox' as const },
  { key: 'language', label: 'Language', type: 'select' as const, options: ['en', 'es', 'fr', 'de'] },
  { key: 'privacy', label: 'Privacy', type: 'select' as const, options: ['public', 'private'] }
];

export const UserSettings: React.FC<UserSettingsProps> = ({ settings, onUpdate }) => {
  return <SettingsBlock title="USER SETTINGS" settings={settings} fields={USER_FIELDS} onUpdate={onUpdate} />;
};