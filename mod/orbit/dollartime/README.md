# dollartime

**Will the price be up or down in N hours?** A prediction market anyone can use:
anyone opens a market on any USD-quoted asset for any window, anyone takes a
side, and it settles on the real price when the window ends.

## How a market works

1. **Open** — `create(asset, hours, side, amount)`. The strike ("the line") is
   the spot price at that moment. The creator backs their side with a stake.
2. **Bet** — anyone takes `up` or `down` for the first quarter of the window
   (override with `bet_minutes`). After that the market is **locked**.
3. **Settle** — at `open + hours` the close price is the Coinbase 1-minute
   candle containing that instant. `close > line` → up, `close < line` → down.
4. **Pay** — parimutuel: winners split the whole pot in proportion to their
   stakes. A flat close, a market with nobody on the winning side, or a price
   that can't be fetched within 24h (void) refunds everyone.

The payout multiple shown on each side (`pays`) is `total pot / that side's pool`
as of now — it moves as people bet.

## Players

Play money. `join(name)` returns a **token** (shown once) and 1000 points;
the token is what you bet with. `claim` adds 100 points once every 24h. No
wallets, no keys, nothing real at stake.

## Use it

```bash
m dollartime/serve                         # console + API on :51410
open http://localhost:51410/dollartime/

m dollartime/join name=alice               # → {token, balance: 1000}
m dollartime/create token=$T asset=BTC hours=4 side=up amount=50 note="ETF flows"
m dollartime/bet token=$T2 id=1 side=down amount=20
m dollartime/markets status=live           # open | locked | live | resolved | all
m dollartime/market id=1
m dollartime/me token=$T
m dollartime/leaderboard
m dollartime/test
```

HTTP: reads are `GET /{fn}?args`, writes are `POST /{fn}` with a JSON body —
see `config.json → endpoints`. The server settles due markets every 30s.

## Files

| file | what |
|---|---|
| `mod.py` | anchor — the `Mod` class the orbit loader instantiates |
| `market.py` | engine: players, markets, bets, settlement, price feed |
| `serve.py` | stdlib HTTP server: console + API, background resolver |
| `web/index.html` | the console |
| `tests/` | offline tests with a fake clock and price feed |

State: `~/.mod/dollartime/dollartime.db` (`DOLLARTIME_DIR` to move it).
Prices: Coinbase public API, keyless (`api.coinbase.com` spot,
`api.exchange.coinbase.com` candles).
