'use client'
import { UserType } from '@/app/types'
import ModCard from '@/app/mod/explore/ModCard'

export function UserModules({ userData }: { userData: UserType }) {
  const { mods } = userData
  return (
    <div className="grid grid-cols-1  gap-6">
      {mods.map((mod) => (
        <ModCard mod={mod} key={mod.key} />
      ))}
    </div>
  )
}