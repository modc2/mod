# reviews

A modular rating layer. Any module hands it ratings — a subject, a rater, a
1–5 score, `human` or `agent` — and asks for **one number back, with its
working**: which aggregator produced it, every rater's weight and why, and the
formula. No score is ever a black box.

Local-first: one SQLite file at `~/.mod/reviews/reviews.db` (`REVIEWS_DIR`
moves it), Python stdlib only, no accounts, no keys.

## Aggregators

| id | status | formula |
|---|---|---|
| `mean` (default) | live | `Σ score_i / n` — every rating counts once, agent or human |
| `weighted` | live | `Σ w_i·score_i / Σ w_i` — `w_i` from a **published** table: `by_rater[addr]`, else `by_kind[kind]`, else `default` |
| `staketime` | soon | `Σ st_i·score_i / Σ st_i` — `st_i` is the rater's stake-time (bloctime). Interface wired; set `REVIEWS_STAKE_URL` to a provider answering `GET ?address= → {weight}` |

An aggregator that cannot run (no provider, every weight 0) still answers:
it falls back to `mean` and says so in `fallback`.

**Adding one** is a decorator in `reviews.py`:

```python
@aggregator('recency', 'recent first', 'w_i = 0.5 ** (age_days / 30)')
def _agg_recency(ratings, ctx):
    rows = [{**r, 'weight': ..., 'why': '...'} for r in ratings]
    return rows, {}
```

It receives the ratings and returns a weight and a reason per rater. That is
the whole contract; the registry, the catalog, the fallback and the console
pick it up.

## Use it

```bash
m reviews/serve                                   # console + API on :51440 → /reviews/
m reviews/aggregators
m reviews/rate subject=build:polymarket rater=0xabc score=4 kind=agent ref=sg_123
m reviews/score subject=build:polymarket          # mean
m reviews/score subject=build:polymarket aggregator=weighted
m reviews/set_weights subject=build:polymarket weights='{"by_kind":{"agent":0.5}}'
m reviews/set_aggregator subject=build:polymarket aggregator=weighted
m reviews/test
```

`POST /aggregate` is the stateless twin: pass `ratings` inline and get the
same transparent answer without storing anything.

## Who uses it

`orbit/build` — the REVIEWS tab. Every review carries its author's rating of
the module (agents that write reviews rate too), build mirrors each one here
as subject `build:<module>` / ref `<review id>`, and the module's score strip
asks this module to aggregate with the aggregator the reader picks.
