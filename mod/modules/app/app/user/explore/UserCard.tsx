import { UserType } from '@/app/types'
import { text2color } from '@/app/utils'
import { useRouter } from 'next/navigation'
import { UserIcon, CubeIcon } from '@heroicons/react/24/outline'

interface UserCardProps {
  user: UserType
}

export function UserCard({ user }: UserCardProps) {
  const router = useRouter()
  const userColor = text2color(user.key)
  const modCount = user.mods?.length || 0

  return (
    <div
      onClick={() => router.push(`/user/${user.key}`)}
      className="group relative overflow-hidden rounded-2xl border bg-gradient-to-br from-black via-zinc-950 to-black p-6 transition-all duration-300 hover:shadow-2xl cursor-pointer"
      style={{
        borderColor: `${userColor}30`,
        boxShadow: `0 0 20px ${userColor}20`,
      }}
    >
      <div
        className="absolute inset-0 opacity-0 transition-opacity duration-500 group-hover:opacity-100"
        style={{
          background: `radial-gradient(circle at top right, ${userColor}08, transparent 70%)`,
        }}
      />

      <div className="relative z-10 flex items-start gap-4">
        <div
          className="flex-shrink-0 rounded-xl p-3 transition-all duration-300 group-hover:scale-110"
          style={{
            backgroundColor: `${userColor}15`,
            boxShadow: `0 0 20px ${userColor}30`,
          }}
        >
          <UserIcon
            className="h-8 w-8"
            style={{ color: userColor }}
          />
        </div>

        <div className="flex-1 min-w-0">
          <h3
            className="text-xl font-bold mb-2 truncate"
            style={{ color: userColor }}
          >
            {user.key}
          </h3>

          <div className="flex flex-wrap gap-2 mb-3">
            <span
              className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold"
              style={{
                backgroundColor: `${userColor}20`,
                color: userColor,
              }}
            >
              <CubeIcon className="h-3.5 w-3.5" />
              {modCount} modules
            </span>
            {user.balance !== undefined && (
              <span
                className="px-3 py-1 rounded-full text-xs font-semibold"
                style={{
                  backgroundColor: `${userColor}15`,
                  color: `${userColor}cc`,
                }}
              >
                Balance: {user.balance.toFixed(2)}
              </span>
            )}
          </div>

          {user.address && (
            <div className="text-xs text-gray-500 font-mono truncate">
              {user.address}
            </div>
          )}
        </div>
      </div>

      <div
        className="absolute inset-0 rounded-2xl opacity-0 transition-opacity duration-500 group-hover:opacity-100 pointer-events-none"
        style={{
          boxShadow: `inset 0 0 0 1px ${userColor}30`,
        }}
      />
    </div>
  )
}
