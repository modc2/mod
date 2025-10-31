import React from 'react';
import Link from 'next/link';

interface UserCardProps {
  user: {
    key: string;
    name?: string;
    address?: string;
    balance?: string;
  };
}

export default function UserCard({ user }: UserCardProps) {
  return (
    <Link href={`/${user.key}/profile`}>
      <div className="border rounded-lg p-6 hover:shadow-lg transition-shadow cursor-pointer bg-white dark:bg-gray-800">
        <div className="flex items-center space-x-4">
          <div className="w-12 h-12 rounded-full bg-gradient-to-r from-blue-500 to-purple-500 flex items-center justify-center text-white font-bold">
            {user.name ? user.name.charAt(0).toUpperCase() : user.key.substring(0, 2).toUpperCase()}
          </div>
          <div className="flex-1">
            <h3 className="text-lg font-semibold text-gray-900 dark:text-white">
              {user.name || 'Anonymous User'}
            </h3>
            <p className="text-sm text-gray-500 dark:text-gray-400 truncate">
              {user.address || user.key}
            </p>
            {user.balance && (
              <p className="text-sm text-green-600 dark:text-green-400 mt-1">
                Balance: {user.balance}
              </p>
            )}
          </div>
        </div>
      </div>
    </Link>
  );
}