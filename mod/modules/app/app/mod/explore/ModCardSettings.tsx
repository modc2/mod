import React from 'react';
import { ModSettingsCard } from '@/app/block/settings/ModSettingsCard';

export const ModCardSettings = () => {
  const defaultSettings = {
    visibility: 'public',
    autoApprove: false,
    maxUsers: 100,
    rateLimit: 100,
    permissions: ['read']
  };

  return <ModSettingsCard settings={defaultSettings} />;
};