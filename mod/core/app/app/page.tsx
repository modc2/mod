import { redirect } from 'next/navigation'

export const dynamic = 'force-dynamic'

// The mod app's front door is the build console (orbit/build, :8893 behind
// the gateway). Server-side redirect so `/` never flashes an empty page.
export default function Home() {
  redirect('/build')
}
