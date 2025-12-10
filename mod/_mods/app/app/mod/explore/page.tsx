import dynamic from 'next/dynamic';

const ModulePage = dynamic(
  () => import('./ModExplorePage'),
  { 
    ssr: false,
    loading: () => (
      <div className="flex items-center justify-center min-h-screen bg-black">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-emerald-500"></div>
      </div>
    )
  }
);

export default function Page() { 
  return <ModulePage />;