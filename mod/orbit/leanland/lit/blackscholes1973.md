---
title: The Pricing of Options and Corporate Liabilities
authors: Fischer Black, Myron Scholes
year: 1973
url: https://www.cs.princeton.edu/courses/archive/fall09/cos323/papers/black_scholes73.pdf
tags:
- options
- pricing
---

## What I actually want from it

Equation 13 (the call) and the parity relation behind equation 14. `bs_put` is
derived from `bs_call` by parity rather than restated, so there is one pricing
formula in the library and not two that can drift apart.

## Caveats

Constant volatility, no dividends, European exercise, frictionless. Every one
of those is false; the formula survives as a *quoting convention* (implied vol)
more than as a price.
