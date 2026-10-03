import type { Metadata } from 'next'
import CopyLine from './CopyLine'
import ToolTable from './ToolTable'
import type { McpSurface } from '@/lib/api'

export const metadata: Metadata = {
  title: 'NYC Atlas — MCP server & API docs',
  description:
    'Connect any MCP client to New York City open data: 16 read-only tools over '
    + 'housing prices, transit, parks and the whole NYC/NYS open-data portal.',
}

// The tool registry changes when someone adds a tool, not when someone loads
// the page. Rendering on the server every hour keeps the page static and fast
// while never going stale enough to lie about what the server exposes.
export const revalidate = 3600

/**
 * Server-side base for the build/render pass. The browser talks to the gateway
 * path (`/nyc/api`), but a server render has no origin to resolve that against,
 * so it goes straight to the API port.
 */
const SSR_BASE = process.env.NYC_API_ORIGIN || 'http://localhost:50310'

async function loadSurface(): Promise<McpSurface | null> {
  try {
    const res = await fetch(`${SSR_BASE}/tools`, { next: { revalidate } })
    if (!res.ok) return null
    return (await res.json()) as McpSurface
  } catch {
    // The docs are worth serving even when the API is down — everything except
    // the generated tool table is prose that does not depend on it.
    return null
  }
}

export default async function DocsPage() {
  const surface = await loadSurface()

  return (
    <main className="min-h-[100dvh] bg-nes-void">
      <div className="mx-auto w-full max-w-[860px] px-4 py-8 md:px-6 md:py-14">
        <Header version={surface?.server.version} />

        <Section id="what" title="WHAT THIS IS">
          <p>
            <strong className="text-nes-ink">NYC Atlas</strong> is a browser GIS
            for New York City — housing prices from recorded deeds, subway and
            bike networks, parks, flood zones and traffic injuries, as map
            layers you can toggle.
          </p>
          <p>
            It is also an <strong className="text-nes-ink">MCP server</strong>.
            The same data engine behind the map is exposed as{' '}
            {surface?.count ?? 16} read-only tools, so an AI assistant can ask
            New York a question directly: what a neighborhood sells for, how
            that has moved since 2016, where the subway reaches, how many noise
            complaints a district filed last month.
          </p>
          <Callout>
            Every source is public, key-free city and state open data. There is
            no account to make and no key to paste — for the map or for the MCP
            server.
          </Callout>
        </Section>

        <Section id="connect" title="CONNECT AN MCP CLIENT">
          <p>
            The server speaks both MCP transports. Use HTTP for a hosted client
            and stdio for one that launches its own process.
          </p>

          <SubHead>Streamable HTTP — nothing to install</SubHead>
          <CopyLine
            label="CLAUDE CODE"
            cmd="claude mcp add --transport http nyc https://modc2.com/nyc/api/mcp"
          />
          <p className="mt-3">
            Or point any MCP client at the endpoint directly:
          </p>
          <CopyLine cmd="https://modc2.com/nyc/api/mcp" />

          <SubHead>stdio — running this module locally</SubHead>
          <CopyLine
            label="CLAUDE CODE"
            cmd="claude mcp add nyc -- python3 -m nycgis.mcp_server"
          />
          <p className="mt-3">
            Or as an <code className="code">mcpServers</code> entry, for a client
            that keeps its config in JSON:
          </p>
          <Pre>{`{
  "mcpServers": {
    "nyc": {
      "command": "python3",
      "args": ["-m", "nycgis.mcp_server"],
      "cwd": "/root/mod/mod/orbit/nyc"
    }
  }
}`}</Pre>

          <SubHead>Check it works</SubHead>
          <p>
            An <code className="code">initialize</code> handshake needs no
            session and no auth, so curl is enough to prove the endpoint is
            live:
          </p>
          <Pre>{`curl -s -X POST https://modc2.com/nyc/api/mcp \\
  -H 'Content-Type: application/json' \\
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
       "params":{"protocolVersion":"2025-06-18","capabilities":{},
                 "clientInfo":{"name":"curl","version":"1"}}}'`}</Pre>

          <dl className="mt-6 grid gap-px overflow-hidden border-[3px] border-black bg-black sm:grid-cols-2">
            <Fact k="Protocol" v={surface?.mcp.protocol ?? '2025-06-18'} />
            <Fact
              k="Also speaks"
              v={(surface?.mcp.supported ?? []).slice(1).join(', ') || '2025-03-26, 2024-11-05'}
            />
            <Fact k="Auth" v="none — public open data" />
            <Fact k="Writes" v="nothing; every tool is read-only" />
          </dl>
        </Section>

        <Section id="tools" title="TOOLS">
          {surface ? (
            <ToolTable surface={surface} />
          ) : (
            <Callout tone="warn">
              The tool list is generated from the live API, which is not
              answering right now. Start it with{' '}
              <code className="code">m nyc/serve_api</code>, or read the
              registry directly in{' '}
              <code className="code">nycgis/tools.py</code>.
            </Callout>
          )}
        </Section>

        {surface && surface.prompts.length > 0 && (
          <Section id="prompts" title="PROMPTS">
            <p>
              Ready-made investigations the server ships with. In a client that
              surfaces MCP prompts these appear as slash commands or menu items;
              each one is a recipe that tells the model which tools to reach for
              and in what order.
            </p>
            <div className="mt-5 space-y-3">
              {surface.prompts.map((p) => (
                <div key={p.name} className="blk px-4 py-3.5">
                  <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                    <code className="text-[13px] font-semibold text-nes-coin">
                      {p.name}
                    </code>
                    {p.arguments?.map((a) => (
                      <span key={a.name} className="text-[12px] text-nes-ink3">
                        {a.name}
                        {a.required && <span className="text-nes-red">*</span>}
                      </span>
                    ))}
                  </div>
                  <p className="mt-1.5 text-[13px] leading-relaxed text-nes-ink2">
                    {p.description}
                  </p>
                </div>
              ))}
            </div>
          </Section>
        )}

        {surface && surface.resources.length > 0 && (
          <Section id="resources" title="RESOURCES">
            <p>
              Reference documents a client can read without spending a tool
              call — the layer catalogue, the valid parameter values, and the
              data caveats below.
            </p>
            <div className="mt-5 space-y-3">
              {surface.resources.map((r) => (
                <div key={r.uri} className="blk px-4 py-3.5">
                  <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                    <code className="break-all text-[13px] font-semibold text-nes-coin">
                      {r.uri}
                    </code>
                    <span className="text-[11px] text-nes-ink3">{r.mimeType}</span>
                  </div>
                  <p className="mt-1.5 text-[13px] leading-relaxed text-nes-ink2">
                    {r.description}
                  </p>
                </div>
              ))}
            </div>
          </Section>
        )}

        <Section id="caveats" title="BEFORE YOU QUOTE A NUMBER">
          <p>
            Three decisions shape every housing price this server reports. They
            are the difference between a figure that is right and one that only
            looks reasonable.
          </p>
          <ol className="mt-5 space-y-4">
            <Caveat n={1} title="Sales under $50,000 are dropped">
              A large share of rows in the city&rsquo;s rolling-sales file are $0
              or nominal deed transfers — family transfers, LLC restructurings,
              estate filings. They are not market prices, and they would drag
              every median down.
            </Caveat>
            <Caveat n={2} title="$/ft² is filtered to $50–$5,000 per row">
              For condo and co-op units the file often reports the{' '}
              <em>whole building&rsquo;s</em> square footage rather than the
              unit&rsquo;s, so a $1M apartment in a 250,000 ft² tower computes to
              $4/ft². Rows are filtered one at a time, so the genuine ones still
              count; an area left with fewer than five usable rows reports no
              $/ft² at all rather than a noisy one.
            </Caveat>
            <Caveat n={3} title="Price change needs 5+ sales on both sides">
              Otherwise a single thin-volume ZIP swinging −79% stretches the
              colour scale until every other area reads as neutral.
            </Caveat>
          </ol>
          <Callout>
            Areas with no qualifying sales report <code className="code">null</code>,
            never zero, and are drawn in neutral grey rather than the ramp&rsquo;s
            lowest class. &ldquo;No data&rdquo; and &ldquo;cheapest&rdquo; are
            different answers and must not look the same.
          </Callout>
        </Section>

        <Section id="http" title="PLAIN HTTP API">
          <p>
            Everything the MCP server does is also a normal GET, returning
            GeoJSON or JSON. Responses are gzipped — the 29,679-segment bike
            network goes out at 568 KB instead of 6.4 MB.
          </p>
          <Routes />
          <p className="mt-5">
            And one call per tool, for a client that wants the tool registry
            without speaking MCP:
          </p>
          <CopyLine cmd="curl -s -X POST https://modc2.com/nyc/api/tools/nyc_prices -d '{}'" />
        </Section>

        <Section id="cli" title="COMMAND LINE">
          <p>
            The module is a mod-protocol module, so every function is callable
            from a shell:
          </p>
          <Pre>{`m nyc/layers                      # the layer catalogue
m nyc/layer subway_lines          # one layer as GeoJSON
m nyc/housing metric=median_ppsf geography=zip property_type=condo
m nyc/prices                      # citywide summary, top/bottom areas
m nyc/trend area=BK0101           # one neighborhood's yearly history
m nyc/where "Prospect Park"       # geocode
m nyc/tools                       # the tool registry
m nyc/warm                        # pre-fetch every layer (~19MB)
m nyc/serve                       # API + map app under pm2`}</Pre>
        </Section>

        <footer className="mt-14 border-t-[3px] border-black pt-6">
          <p className="text-[12.5px] leading-relaxed text-nes-ink3">
            Data © the City of New York, New York State / MTA, and OpenStreetMap
            contributors, used under their respective open-data terms. This
            module is a viewer and is not affiliated with or endorsed by any of
            them.
          </p>
          <a href="/nyc" className="btn pixel mt-5 inline-block px-3 py-2.5 text-[8px]">
            ← BACK TO THE MAP
          </a>
        </footer>
      </div>
    </main>
  )
}

/* ── pieces ─────────────────────────────────────────────────────────────── */

function Header({ version }: { version?: string }) {
  return (
    <header className="mb-12">
      <a
        href="/nyc"
        className="pixel inline-block text-[7.5px] leading-[2] text-nes-ink3 hover:text-nes-coin"
      >
        ← NYC ATLAS
      </a>
      <h1 className="pixel pixel-shadow mt-4 text-[19px] leading-[1.6] text-nes-coin md:text-[26px]">
        MCP SERVER
      </h1>
      <p className="mt-5 max-w-[62ch] text-[15px] leading-relaxed text-nes-ink2">
        New York City&rsquo;s open data, as tools an AI assistant can call. Housing
        prices from every recorded deed since 2016, the transit and bike
        networks, parks and flood zones — plus SoQL access to every other
        dataset the city and state publish.
      </p>
      <p className="pixel mt-5 flex flex-wrap items-center gap-x-2.5 gap-y-2 text-[7px] leading-none text-nes-ink3">
        <span>V{version ?? '2.0.0'}</span>
        <Dot />
        <span>NO AUTH</span>
        <Dot />
        <span>READ ONLY</span>
        <Dot />
        <span>OPEN DATA</span>
      </p>
    </header>
  )
}

/** Press Start 2P has no ·, so the separator is drawn rather than typed. */
function Dot() {
  return <span className="h-[3px] w-[3px] shrink-0 bg-nes-ink3" aria-hidden />
}

function Section({
  id, title, children,
}: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section id={id} className="mb-14 scroll-mt-6">
      {/* Brick as an edge course, never under the type — the same call the
          rail's section headers make. Pixel type is already low-contrast at
          this size; laying it over mortar lines finishes the job. */}
      <h2 className="blk pixel relative mb-6 py-3 pl-6 pr-3 text-[10px] leading-none text-nes-coin">
        <span className="brick brick-strip absolute inset-y-0 left-0 w-2.5" aria-hidden />
        {title}
      </h2>
      <div className="space-y-4 text-[14.5px] leading-relaxed text-nes-ink2 [&_p]:max-w-[68ch]">
        {children}
      </div>
    </section>
  )
}

function SubHead({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="pixel !mt-8 mb-3 text-[8.5px] leading-[2] text-nes-sky">
      {children}
    </h3>
  )
}

function Pre({ children }: { children: string }) {
  return (
    <pre className="overflow-x-auto border-[3px] border-black bg-black px-3 py-2.5
                    text-[12.5px] leading-relaxed text-nes-coin">
      {children}
    </pre>
  )
}

function Callout({
  children, tone = 'info',
}: { children: React.ReactNode; tone?: 'info' | 'warn' }) {
  return (
    <div
      className={`!mt-6 border-l-[6px] bg-nes-panel px-4 py-3 text-[13.5px] leading-relaxed
                  ${tone === 'warn' ? 'border-nes-red' : 'border-nes-green'}`}
    >
      {children}
    </div>
  )
}

function Fact({ k, v }: { k: string; v: string }) {
  return (
    <div className="bg-nes-panel px-4 py-3">
      <dt className="pixel text-[7px] leading-[2] text-nes-ink3">{k.toUpperCase()}</dt>
      <dd className="mt-1 text-[13px] text-nes-ink">{v}</dd>
    </div>
  )
}

function Caveat({
  n, title, children,
}: { n: number; title: string; children: React.ReactNode }) {
  return (
    <li className="blk flex gap-3.5 px-4 py-3.5">
      <span className="pixel shrink-0 text-[13px] leading-none text-nes-coin">{n}</span>
      <div>
        <h4 className="text-[14px] font-semibold text-nes-ink">{title}</h4>
        <p className="mt-1.5 text-[13.5px] leading-relaxed text-nes-ink2">{children}</p>
      </div>
    </li>
  )
}

const ROUTES: [string, string][] = [
  ['GET /layers', 'the layer catalogue that drives the map'],
  ['GET /layers/{id}', 'any layer as a GeoJSON FeatureCollection'],
  ['GET /layers/housing_prices', 'choropleth + quantile class breaks'],
  ['GET /layers/sales', 'individual recorded sales as points'],
  ['GET /prices', 'citywide summary: totals, extremes, movers'],
  ['GET /trend', 'yearly median price and $/ft², citywide or per area'],
  ['GET /rents', 'what affordable homes rent for'],
  ['GET /homes', 'affordable rentals matching a budget'],
  ['GET /where', 'geocode an address or place'],
  ['GET /tools', 'the whole MCP surface as JSON'],
  ['POST /mcp', 'MCP streamable HTTP'],
]

function Routes() {
  return (
    <div className="mt-5 grid gap-px overflow-hidden border-[3px] border-black bg-black">
      {ROUTES.map(([route, what]) => (
        <div
          key={route}
          className="flex flex-col gap-1 bg-nes-panel px-4 py-2.5 sm:flex-row sm:items-baseline sm:gap-4"
        >
          <code className="shrink-0 text-[12.5px] text-nes-coin sm:w-[15.5rem]">
            {route}
          </code>
          <span className="text-[13px] text-nes-ink2">{what}</span>
        </div>
      ))}
    </div>
  )
}
