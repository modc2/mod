-- stats.lean
--
-- The handful of sample statistics that show up in every empirical paper, and
-- the one place to be explicit about the convention (population, not sample:
-- divide by n).

/-- Population variance of a sample. Divides by n. -/
@[convention]
def variance (v : Vec Real) : Real :=
  let m := mean v
  let n := len v
  (sum i in 0..n - 1, (v[i] - m) ^ 2) / n

#example variance [2, 4, 4, 4, 5, 5, 7, 9] = 4

/-- Population standard deviation. -/
@[convention]
def stdev (v : Vec Real) : Real :=
  sqrt (variance v)

#example stdev [2, 4, 4, 4, 5, 5, 7, 9] = 2

/-- How many standard deviations `x` sits from the sample's mean. -/
@[convention]
def zscore (v : Vec Real) (x : Real) : Real :=
  (x - mean v) / stdev v

#example zscore [2, 4, 4, 4, 5, 5, 7, 9] 9 = 2

/-- Sharpe ratio of a per-period return series against a per-period risk-free
    rate, annualised by `periods` periods per year. -/
@[source sharpe1994, eq 8]
def sharpe (r : Vec Real) (rf : Real) (periods : Real) : Real :=
  ((mean r - rf) / stdev r) * sqrt periods

#example sharpe [0.01, 0.02, -0.01, 0.03, 0.0] 0 1 ≈ 0.7071067811865476 tol 1e-12
