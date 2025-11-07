import React from 'react';
import { UserSettingsCard } from '@/app/block/settings/UserSettingsCard';

export const UserCardSettings = () => {
  const defaultSettings = {
    theme: 'dark',
    notifications: true,
    language: 'en',
    privacy: 'public'
  };

  return <UserSettingsCard settings={defaultSettings} />;
};