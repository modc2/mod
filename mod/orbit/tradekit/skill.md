# tradekit skill

Use this skill for any work that adds a trading venue or changes the fn
surface of hyperliquid, polymarket or dydx. Run `m tradekit/board` before
and after the change; every row must stay `ok`. To add a venue, run
`m tradekit/scaffold name=<venue>` and then fill in
`orbit/<venue>/src/api/src/venue.rs`.
