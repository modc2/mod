"use client";

import ModulePage from '@/mod/ModulePage'

export default function ModulePageWrapper() {
  return (
    <div className="h-full" style={{ backgroundColor: 'var(--bg-primary)' }}>
      <ModulePage />
    </div>
  )
}
