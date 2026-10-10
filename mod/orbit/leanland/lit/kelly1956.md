---
title: A New Interpretation of Information Rate
authors: J. L. Kelly Jr.
year: 1956
url: https://www.princeton.edu/~wbialek/rome/refs/kelly_56.pdf
tags:
- sizing
- information-theory
---

## What it says

Maximising the expected *logarithm* of bankroll — not its expectation —
maximises the long-run growth rate. For a bet paying `b` to 1 at win
probability `p`, the growth-optimal stake is `(pb - q)/b`.

## What I actually want from it

`kelly` (eq 1) and `log_growth` (eq 5). Everything else in the paper is the
information-rate framing, which is where the result comes from but not what
gets computed.

## Caveats worth keeping

- Assumes `p` is *known*. It never is; estimation error is the whole risk, and
  is why `kelly_capped` exists rather than `kelly` being used directly.
- Assumes repeated independent bets with full reinvestment.
