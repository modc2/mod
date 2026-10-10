-- special.lean
--
-- Constants and special functions.
--
-- These are Defs rather than primitives on purpose. Binding `norm_cdf` to
-- scipy in Python and to some crate in Rust would give two different
-- algorithms wearing one name, and the parity check would be measuring the
-- tolerance instead of the code. Written here, the approximation is visible,
-- cited, bounded, and identical in every target.

/-- π. -/
@[convention]
def pi : Real :=
  3.141592653589793

/-- Standard normal probability density. -/
@[source as1964, eq 26.2.1]
def norm_pdf (x : Real) : Real :=
  exp (-(x ^ 2) / 2) / sqrt (2 * pi)

#example norm_pdf 0 ≈ 0.3989422804014327 tol 1e-15

/-- Standard normal CDF, Zelen & Severo's rational approximation.
    Absolute error < 7.5e-8 — good enough to price with, not to integrate with. -/
@[source as1964, eq 26.2.17]
def norm_cdf (x : Real) : Real :=
  let z := abs x
  let t := 1 / (1 + 0.2316419 * z)
  let poly := t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))))
  let tail := norm_pdf z * poly
  if x ≥ 0 then 1 - tail else tail

#example norm_cdf 0 ≈ 0.5 tol 1e-7
#example norm_cdf 1.96 ≈ 0.9750021048517795 tol 1e-7
#example norm_cdf (-1.96) ≈ 0.0249978951482205 tol 1e-7
