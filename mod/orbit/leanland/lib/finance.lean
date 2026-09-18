-- finance.lean
--
-- Sizing and pricing. Every def here cites the paper it came from; the numbers
-- in the #examples are the ones those papers state, not values this
-- implementation happened to produce.

/-- Fraction of bankroll to stake on a bet paying `b` to 1 with win probability `p`.
    Negative means the bet is against you. -/
@[source kelly1956, eq 1]
def kelly (p : Real) (b : Real) : Real :=
  (p * b - (1 - p)) / b

#example kelly 0.6 1 ≈ 0.2 tol 1e-12
#example kelly 0.5 1 = 0

/-- Kelly with a cap, which is what anyone actually sizes with: never short,
    never more than `cap` of the bankroll. -/
@[source thorp2006, section 7]
def kelly_capped (p : Real) (b : Real) (cap : Real) : Real :=
  max 0 (min cap (kelly p b))

#example kelly_capped 0.6 1 0.1 ≈ 0.1 tol 1e-12
#example kelly_capped 0.4 1 0.1 = 0

/-- Expected log growth per bet at stake fraction `f`. The quantity Kelly maximises. -/
@[source kelly1956, eq 5]
def log_growth (p : Real) (b : Real) (f : Real) : Real :=
  p * log (1 + b * f) + (1 - p) * log (1 - f)

#example log_growth 0.6 1 0.2 ≈ 0.020135513550688863 tol 1e-12

/-- Black–Scholes d1. -/
@[source blackscholes1973, eq 13]
def bs_d1 (s : Real) (k : Real) (r : Real) (sigma : Real) (t : Real) : Real :=
  (log (s / k) + (r + sigma ^ 2 / 2) * t) / (sigma * sqrt t)

/-- Black–Scholes d2. -/
@[source blackscholes1973, eq 13]
def bs_d2 (s : Real) (k : Real) (r : Real) (sigma : Real) (t : Real) : Real :=
  bs_d1 s k r sigma t - sigma * sqrt t

/-- European call price: spot `s`, strike `k`, rate `r`, vol `sigma`, years `t`. -/
@[source blackscholes1973, eq 13]
def bs_call (s : Real) (k : Real) (r : Real) (sigma : Real) (t : Real) : Real :=
  s * norm_cdf (bs_d1 s k r sigma t) - k * exp (-(r * t)) * norm_cdf (bs_d2 s k r sigma t)

-- the textbook figure, to the four decimals the textbook prints
#example bs_call 100 100 0.05 0.2 1 ≈ 10.4506 tol 1e-3

/-- European put, by put–call parity rather than a second formula — one place
    for the pricing, one place for the parity relation. -/
@[source blackscholes1973, eq 14]
def bs_put (s : Real) (k : Real) (r : Real) (sigma : Real) (t : Real) : Real :=
  bs_call s k r sigma t - s + k * exp (-(r * t))

#example bs_put 100 100 0.05 0.2 1 ≈ 5.5735 tol 1e-3
