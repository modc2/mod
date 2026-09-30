
export default function NotFound() {
  return (
    <section className="page narrow" style={{ textAlign: 'center' }}>
      <h2 style={{ justifyContent: 'center' }}>Warp pipe to nowhere.</h2>
      <p className="lead">This room is not in WORLD τ-1.</p>
      <a className="pill primary" href={process.env.NEXT_PUBLIC_BASE || '/'}>Back to the start</a>
    </section>
  );
}
