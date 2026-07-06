export default function Home() {
  const name = process.env.NEXT_PUBLIC_BASE_PATH?.replace('/', '') || 'basebuilder'
  return (
    <main style={{ padding: '2rem', maxWidth: 600 }}>
      <h1 style={{ fontSize: '1.5rem' }}>{name}</h1>
      <p style={{ color: '#888', marginTop: '0.5rem' }}>
        Module running. Fork basebuilder to build your own.
      </p>
    </main>
  )
}
