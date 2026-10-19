# App (Next.js, port 50131)

basePath `/openhouse`; `/api/*` is rewritten to the API using `config.json` `urls.api`, read at server start (restart `openhouse.app` after changing it).

Routes: `/` hero and directory, `/manifesto`, `/split` (fee slider), `/simulator`, `/invest`, `/paper` and `/paper/<slug>`, `/landscape`, `/code`, `/proofs`. The site map is the `ROUTES` array in `src/components/chrome.tsx`; adding an entry updates nav, footer, home directory and prev/next cards.

Key files under `src/app/src/`: `components/chrome.tsx` (shell), `motion.tsx`, `vibe.tsx` (picker), `simchart.tsx`, `lib/api.ts` (`api()`, `useResource()`, types), `lib/currency.tsx` (`useCurrency()`, Intl formatting), `lib/whitepaper.ts` (paper text, `LAUNCH` stage copy), `app/paper/` (server-rendered whitepaper with figures).

Vibes: eight skins set by `data-mode`, `data-skin`, `data-vibe` on `<html>`; the id list exists in `vibe.tsx` and in the pre-paint boot script in `layout.tsx` and must stay in step.

Docker: `docker-entrypoint.sh` runs uvicorn, `next start` and a Caddy gateway. Build with the root `Dockerfile` (it copies `src/api` and `src/app`).
