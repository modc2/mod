# mandamistan

How Mamdani fixes the New York housing crisis — with receipts, measured
against the real city.

A research-backed explainer packaged as a mod-orbit module: the crisis in
sourced numbers, the **Block by Block** plan pillar by pillar, a
promised-to-delivered timeline, the strongest critiques stated fairly with
their counters, and a transparent simulator where you set the assumptions
and watch the math. Python stdlib only; every figure cites its source.

**Depends on [`nyc`](../nyc/)** — the fleet's open-data GIS and MCP server
for New York City. Every live number here (rent burden, build pace,
violations, NYCHA, lotteries, listings, neighborhood stats) is read through
nyc's API rather than re-implemented; nyc already handles every NYC Open
Data trap. When nyc is away the console degrades to a baked snapshot and
says so.

## Reality check — what the dependency buys

Promises are only promises until a number moves. For each pillar the
module names the metric that will settle it and reads it live:

| pillar | metric (via nyc) | now |
|---|---|---|
| Freeze | renters paying 30%+ of income | 52.4% |
| Build 200k | all-housing completions/yr vs the 20k/yr affordable the plan needs | ~34k |
| Preserve 200k | class-C (immediately hazardous) violations per 1,000 renter households | ~262 |
| NYCHA | apartments / residents / avg rent | 183k / 360k / $593 |
| Zoning | new homes per 1k residents since 2020; wealthy neighborhoods that built nothing | city 24.4; UES 0.7, UWS 0.2 |
| Tenants | Housing Connect lotteries open right now, listings under $1,500 | live |

Plus:

- **Borough picker** — the simulator re-seeds itself with a borough's share
  of the plan (by renter households), its real median rent and scaled
  stabilized stock (`/borough?name=Bronx`).
- **Find a home** — income + household size → 2026 HPD AMI band → rent
  affordable at 30% of income → lotteries open to that band now + affordable
  listings under the number (`/find?income=52000&household=3&bedrooms=2`).
- **Who builds nothing** — neighborhoods with median income $120k+ ranked
  by new homes per 1k residents since 2020 (`/neighborhoods`).

## The short version

The crisis (all sourced, see `/sources`):

- **1.41%** rental vacancy — the lowest since 1968; a healthy market is ~5%.
  Under $1,100/month it is **0.39%**: affordable homes never come open.
- **51.6%** of renters pay 30%+ of income in rent; **28.8%** pay half or more.
- **100,437** people sleeping in shelters nightly (Jan 2026), including
  33,217 children.

The plan (*Block by Block: The Housing Plan for a New Era*, May 2026):

1. **Freeze the rent** — ~1,000,000 stabilized apartments. **Delivered:**
   the Rent Guidelines Board voted 7–1 in June 2026 to freeze one-year AND
   two-year leases (the first two-year freeze ever); in effect Oct 1, 2026
   → Sep 30, 2027.
2. **Build 200,000** permanently affordable, rent-stabilized, union-built
   homes over 10 years — tripling subsidized production, $22B city capital
   in the first five years.
3. **Preserve 200,000** existing affordable homes (financing, operating-cost
   relief, code enforcement, closing deregulation loopholes).
4. **Rebuild NYCHA** — one of the largest city capital commitments to public
   housing in recent history.
5. **Unlock the land** — rezonings, transit-oriented development, ADUs,
   faster timelines.
6. **Tenant power** — inspectors, anti-harassment, right-to-counsel,
   homelessness prevention.

The fight: the module carries the strongest objections too (the Step Two
debt-limit critique, the maintenance-starvation argument, the
market-supply-only argument) and the plan's answers, because a case that
hides the counterarguments isn't a case.

## Run it

    m mandamistan/serve          # visual console + API on :51240
    open http://localhost:51240/mandamistan/

Or standalone: `python3 serve.py --port 51240`.

## API

All reads, all GET:

    /crisis       the crisis in numbers, sourced
    /plan         Block by Block, pillar by pillar
    /pillar?id=freeze|build|preserve|nycha|zoning|tenants
    /timeline     promised → delivered, dated
    /critiques    the objections and the counters
    /simulate     the model (new_per_year, preserved_per_year, freeze_years,
                  rgb_hike, attrition_per_year, median_stabilized_rent, years)
    /sources      every citation
    /info /health /readme

Through the nyc dependency (each answer stamped `source: live|cache|stale|snapshot`):

    /live           the crisis tiles with today's numbers
    /scorecard      every pillar: promise → metric → now
    /borough        name=Bronx (+ any simulate arg): profile + re-seeded simulation
    /find           income, household (1-8), bedrooms, borough
    /neighborhoods  min_income, min_population, limit
    /nyc            is nyc reachable, what are we serving from

Also served under `/mandamistan/api/{fn}` and `/api/mandamistan/{fn}` for
the gateway.

## The simulator

`simulate()` is a deliberately small, transparent model — every assumption
is an input, the status-quo baseline is the pre-plan pace (~7k subsidized
completions/yr against ~10k/yr attrition), and the output says so:
*"a transparent toy, not a forecast — change the inputs and see what has to
be true for the plan to work."* The console mirrors the same arithmetic in
JS so the sliders respond instantly.

## How the dependency works

`nycbridge.py` is the one seam. It finds nyc's API port in `../nyc/config.json`
(`NYC_API` env overrides), reads over HTTP, and resolves in order: memory TTL
cache → live → disk cache at `~/.mod/mandamistan/cache` (any age, marked
stale) → `snapshot.json` (baked by `m mandamistan/snapshot`). It never
raises. HTTP rather than an in-process import on purpose: nyc pulls a GIS
stack, and every mod's `mod.py` shadows the protocol package when imported
from the wrong cwd. `serve.py` warms every scorecard read in a background
thread at startup so the first paint never waits on the 11M-row violations
file.

`reality.py` is pure reshaping of nyc answers plus the plan's targets, so
the tests run offline against the snapshot (`tests/test_reality.py` points
nyc at a dead port).

## Files

    mod.py        anchor — the orbit loader instantiates Mod
    housing.py    the plan's data core: CRISIS, PLAN, TIMELINE, CRITIQUES, SOURCES, simulate()
    nycbridge.py  the nyc dependency seam: fetch / cache / snapshot / warm
    reality.py    scorecard, live crisis, borough, find, neighborhoods
    snapshot.json baked nyc answers — offline floor and test fixture
    serve.py      static console + JSON API on one port (stdlib http.server)
    web/          the console (one self-contained index.html, hand-rolled SVG charts)
    tests/        offline tests (plan + reality)

No keys. The only runtime network call is to the nyc module on localhost.
Plan data updated 2026-10-07; live data is whatever nyc says today.
