# mandamistan

How Mamdani fixes the New York housing crisis — with receipts.

A research-backed explainer packaged as a mod-orbit module: the crisis in
sourced numbers, the **Block by Block** plan pillar by pillar, a
promised-to-delivered timeline, the strongest critiques stated fairly with
their counters, and a transparent simulator where you set the assumptions
and watch the math. Static data + Python stdlib only; every figure cites
its source.

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

Also served under `/mandamistan/api/{fn}` and `/api/mandamistan/{fn}` for
the gateway.

## The simulator

`simulate()` is a deliberately small, transparent model — every assumption
is an input, the status-quo baseline is the pre-plan pace (~7k subsidized
completions/yr against ~10k/yr attrition), and the output says so:
*"a transparent toy, not a forecast — change the inputs and see what has to
be true for the plan to work."* The console mirrors the same arithmetic in
JS so the sliders respond instantly.

## Files

    mod.py        anchor — the orbit loader instantiates Mod
    housing.py    the data core: CRISIS, PLAN, TIMELINE, CRITIQUES, SOURCES, simulate()
    serve.py      static console + JSON API on one port (stdlib http.server)
    web/          the console (one self-contained index.html, hand-rolled SVG charts)
    tests/        offline tests

No state, no keys, no network calls at runtime. Data updated 2026-10-07.
