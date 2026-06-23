import Link from "next/link";

export function Nav() {
  return (
    <nav className="nav">
      <div className="wrap nav-inner">
        <Link href="/" className="brand">
          <span className="glyph">m</span>
          <span>mod</span>
        </Link>
        <div className="nav-links">
          <Link href="/" className="hide-sm">
            Ecosystem
          </Link>
          <a
            href="https://github.com/modc2/mod"
            target="_blank"
            rel="noreferrer"
            className="hide-sm"
          >
            Docs
          </a>
          <a
            href="https://github.com/modc2/mod"
            target="_blank"
            rel="noreferrer"
            className="nav-cta"
          >
            GitHub ↗
          </a>
        </div>
      </div>
    </nav>
  );
}

export function Footer({ version }: { version?: string }) {
  return (
    <footer className="footer wrap">
      <span>
        © {new Date().getFullYear()} mod protocol · built modular, on Base
      </span>
      <span className="mono">
        {version ? `mod-api v${version} · ` : ""}modc2.com/web
      </span>
    </footer>
  );
}
