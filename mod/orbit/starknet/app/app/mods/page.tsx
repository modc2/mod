'use client';
import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { Loading } from '@/components/ui';

/** The old MODS tab: the sibling mods are apps now. */
export default function Mods() {
  const router = useRouter();
  useEffect(() => {
    router.replace(`/apps${window.location.hash || ''}`);
  }, [router]);
  return <Loading what="Mods moved to Apps" />;
}
