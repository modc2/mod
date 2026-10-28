;; PreFi paper pool — the distribution contract.
;;
;; This is the money-moving core of the paper pool as a WebAssembly module:
;; given every entry's weight (stake × accuracy) it decides, in pure integer
;; arithmetic, exactly how a pot splits. The module is standard WASM text —
;; run it under wasmtime, a browser, or the bundled interpreter (watvm.py)
;; and the same inputs produce the same payouts to the last unit. That
;; determinism is what lets the state live in a stored, replayable log
;; instead of on a live blockchain.
;;
;; Memory layout: one 24-byte record per entry, from address 0.
;;   24*i + 0   weight  (u64, in)   stake × score, pre-weighted by the host
;;   24*i + 8   payout  (u64, out)
;;   24*i + 16  scratch (s64)       flooring remainder, then -1 once served
;;
;; distribute(n, pot) -> leftover
;;   Largest-remainder split: floor every share, then hand the leftover
;;   units out one at a time to whoever was robbed hardest by the flooring
;;   (first index wins ties). Σ payouts + leftover == pot, always.
;;   leftover is only non-zero when nothing carries weight — the pot then
;;   belongs to the caller's reserve.
;;
;;   Weights are halved until their sum fits 31 bits, so r*w below never
;;   overflows i64. The reference implementation (paper_machine.split_
;;   weighted) mirrors this bit for bit; a test holds them equal.
(module
  (memory (export "mem") 1)

  (func (export "distribute") (param $n i32) (param $pot i64) (result i64)
    (local $i i32) (local $a i32)
    (local $total i64) (local $w i64)
    (local $q i64) (local $r i64)
    (local $floor i64) (local $given i64) (local $left i64)
    (local $best i32) (local $bestrem i64)

    ;; ── zero the output and scratch slots; sum the weights
    (local.set $i (i32.const 0))
    (block $sum_done
      (loop $sum
        (br_if $sum_done (i32.ge_u (local.get $i) (local.get $n)))
        (local.set $a (i32.mul (local.get $i) (i32.const 24)))
        (i64.store (i32.add (local.get $a) (i32.const 8)) (i64.const 0))
        (i64.store (i32.add (local.get $a) (i32.const 16)) (i64.const 0))
        (local.set $total (i64.add (local.get $total)
                                   (i64.load (local.get $a))))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $sum)))

    ;; ── nothing carries weight: nobody is paid, the whole pot is leftover
    (if (i64.eqz (local.get $total))
      (then (return (local.get $pot))))

    ;; ── halve every weight until the total fits 31 bits
    (block $scale_done
      (loop $scale
        (br_if $scale_done (i64.le_u (local.get $total)
                                     (i64.const 2147483647)))
        (local.set $total (i64.const 0))
        (local.set $i (i32.const 0))
        (block $h_done
          (loop $h
            (br_if $h_done (i32.ge_u (local.get $i) (local.get $n)))
            (local.set $a (i32.mul (local.get $i) (i32.const 24)))
            (local.set $w (i64.shr_u (i64.load (local.get $a)) (i64.const 1)))
            (i64.store (local.get $a) (local.get $w))
            (local.set $total (i64.add (local.get $total) (local.get $w)))
            (local.set $i (i32.add (local.get $i) (i32.const 1)))
            (br $h)))
        (br $scale)))
    (if (i64.eqz (local.get $total))
      (then (return (local.get $pot))))

    ;; ── floor shares: pot*w/total as q*w + (r*w)/total so nothing overflows
    (local.set $q (i64.div_u (local.get $pot) (local.get $total)))
    (local.set $r (i64.rem_u (local.get $pot) (local.get $total)))
    (local.set $i (i32.const 0))
    (block $f_done
      (loop $f
        (br_if $f_done (i32.ge_u (local.get $i) (local.get $n)))
        (local.set $a (i32.mul (local.get $i) (i32.const 24)))
        (local.set $w (i64.load (local.get $a)))
        (local.set $floor
          (i64.add (i64.mul (local.get $q) (local.get $w))
                   (i64.div_u (i64.mul (local.get $r) (local.get $w))
                              (local.get $total))))
        (i64.store (i32.add (local.get $a) (i32.const 8)) (local.get $floor))
        (i64.store (i32.add (local.get $a) (i32.const 16))
                   (i64.rem_u (i64.mul (local.get $r) (local.get $w))
                              (local.get $total)))
        (local.set $given (i64.add (local.get $given) (local.get $floor)))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $f)))
    (local.set $left (i64.sub (local.get $pot) (local.get $given)))

    ;; ── hand the dust to the largest remainders, first index wins ties
    (block $dust_done
      (loop $dust
        (br_if $dust_done (i64.le_s (local.get $left) (i64.const 0)))
        (local.set $best (i32.const -1))
        (local.set $bestrem (i64.const 0))
        (local.set $i (i32.const 0))
        (block $m_done
          (loop $m
            (br_if $m_done (i32.ge_u (local.get $i) (local.get $n)))
            (local.set $a (i32.add (i32.mul (local.get $i) (i32.const 24))
                                   (i32.const 16)))
            (if (i64.gt_s (i64.load (local.get $a)) (local.get $bestrem))
              (then
                (local.set $best (local.get $i))
                (local.set $bestrem (i64.load (local.get $a)))))
            (local.set $i (i32.add (local.get $i) (i32.const 1)))
            (br $m)))
        (br_if $dust_done (i32.lt_s (local.get $best) (i32.const 0)))
        (local.set $a (i32.mul (local.get $best) (i32.const 24)))
        (i64.store (i32.add (local.get $a) (i32.const 8))
          (i64.add (i64.load (i32.add (local.get $a) (i32.const 8)))
                   (i64.const 1)))
        (i64.store (i32.add (local.get $a) (i32.const 16)) (i64.const -1))
        (local.set $left (i64.sub (local.get $left) (i64.const 1)))
        (br $dust)))

    (local.get $left)))
