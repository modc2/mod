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
| `median` | live | the middle rating — one loud rater can't move it |
| `trimmed` | live | mean after dropping `⌊n·trim⌋` from each end (`trim` 0–0.45, default 0.2) |
| `consensus` | live | `c` = median; `w_i = 1/(1 + k·|score_i − c|)` — agree with the crowd, count more (`k` default 1) |
| `recent` | live | `w_i = ½^(age_days/half_life)` (`half_life_days` default 30) |
| `market` | live | the consensus of aggregators — see below |
| `weighted` | live | `Σ w_i·score_i / Σ w_i` — `w_i` from a **published** table: `by_rater[addr]`, else `by_kind[kind]`, else `default` |
| `staketime` | soon | `Σ st_i·score_i / Σ st_i` — `st_i` is the rater's stake-time (bloctime). Interface wired; set `REVIEWS_STAKE_URL` to a provider answering `GET ?address= → {weight}` |

An aggregator that cannot run (no provider, every weight 0) still answers:
it falls back to `mean` and says so in `fallback`.

## The market of aggregators

Aggregators are subjects too. `agg:<id>` gets rated 1–5 like anything else,
so the aggregators compete:

- **standing** = `(Σ stars + 3·2) / (n + 2)` over ratings on `agg:<id>`. An
  unrated aggregator stands at 3; it takes real ratings to move it.
- **publish** a new one with no code: an id, a base (`mean median trimmed
  consensus recent weighted`) and that base's params. Anyone can publish;
  nobody can take over an id; `retract` is loopback-only. Published ones
  can't point at a URL (so no `staketime` clones).
- **`market`** asks every live aggregator for its score and blends them by
  `m = standing − 1`, so a 1★ aggregator drops out. A published aggregator
  joins the blend after 3 ratings. The breakdown is still per rater
  (`w_i = Σ m_a·share_ai / Σ m_a`), plus a `blend` table showing each
  aggregator's score and share.

Ratings are unauthenticated (the caller names the rater), so standings are
only as trustworthy as that. Once `staketime` has a provider it's the natural
way to rate the aggregators.

```bash
m reviews/market
m reviews/rate subject=agg:consensus rater=0xabc score=5
m reviews/publish id=harsh base=consensus params='{"k":3}' author=0xabc
m reviews/score subject=build:polymarket aggregator=market
```

**Adding a built-in** is a decorator in `reviews.py`:

```python
@aggregator('loudest', 'loudest', 'w_i = |score_i − 3|')
def _agg_loudest(ratings, ctx):
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
