---
title: The Sharpe Ratio
authors: William F. Sharpe
year: 1994
url: https://web.stanford.edu/~wfsharpe/art/sr/sr.htm
tags:
- performance
---

## What I actually want from it

Equation 8, the ex-post ratio, plus the point the paper is at pains to make:
the numerator is a *differential* return, so the risk-free rate belongs inside
the standard deviation, not just the mean. `sharpe` here takes a constant `rf`,
which is the common simplification and is wrong when the risk-free rate moves.

Annualisation by sqrt(periods) is convention, not in the paper, and assumes iid
returns.
