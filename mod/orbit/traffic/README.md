# traffic

How many people are using each app on the router right now, and over time.

- Page: `https://modc2.com/traffic/` (local `http://localhost:51540/traffic/`)
- API: `/traffic/api/live`, `/traffic/api/series?range=1h|6h|24h|7d|30d&top=8&apps=a,b`, `/traffic/api/sparks?range=`

**Source.** The caddy module writes a JSON access log for every site block
(`/var/log/caddy/access.log`, 50MiB x 5 rolls; path = `access_log` in
`~/.mod/caddy/settings.json`, empty disables). `collector.py` tails it (handles rotation)
and attributes each request to the app whose route it hit: `/<app>/…`, `/<app>/api/…`,
`/api/<app>/…`, aliases resolved, `/` → build. Unknown paths are dropped.

**Who counts.** One person = sha256(salt | Cf-Connecting-Ip | User-Agent)[:16]. Raw IPs are
never stored. Bot/CLI user agents and internal requests (private/loopback IP with no Cloudflare
header) are dropped. "Now" = seen in the last 5 minutes. An open console that polls its API
counts as using that app.

**State.** `~/.mod/traffic/traffic.db`: `seen(app, minute, vid, hits)`, kept 35 days.
History starts when the access log was turned on (2026-10-21); there is nothing from before.

Runs under pm2 as `traffic` with `always_on` so the activator never sleeps the collector.
`m traffic/test` runs the offline tests.
