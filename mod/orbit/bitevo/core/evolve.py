"""
Evolutionary forecasting engine — Bitevo Track II.

A population of predictor genomes evolves to fit MULTIVARIATE predictions of
the FUTURE: each genome is a small nonlinear vector-autoregressive model
(every output variable reads lagged values of every variable), and fitness is
walk-forward error on a held-out future window — never on the training past.
Selection therefore optimizes genuine forecasting, not curve-fitting.

Stdlib only (math, random, json): local-first, no numpy, no services.
Weight allocation over the population reuses the subnet's own softmax
(core.scoring.normalize_scores), so evolved genomes are first-class miners.
"""

import json
import math
import os
import random
import time
import hashlib
from typing import Dict, List, Optional

from core.scoring import normalize_scores

Z_CLAMP = 8.0          # clamp recursive predictions in z-space so rollouts can't blow up
PARSIMONY = 0.004      # fitness penalty per unit of lag — pressure toward simpler genomes
FITNESS_SCALE = 20.0   # scale −RMSE before the shared T=2.0 softmax (effective T = 0.1 in RMSE units)


# ── Datasets ──────────────────────────────────────────────────────
# Built-in synthetic multivariate series (dict name → list[float], aligned).
# Each is genuinely cross-coupled so multivariate genomes beat univariate ones.

def _dataset_coupled(n: int, rng: random.Random) -> Dict[str, List[float]]:
    """driver: trend+cycle+noise; follower: responds to driver with lag;
    stress: mean-reverting, excited by driver shocks."""
    driver, follower, stress = [], [], []
    d, f, s = 0.0, 0.0, 1.0
    for t in range(n):
        shock = rng.gauss(0, 0.35)
        d = 0.2 * t / n + 2.0 * math.sin(2 * math.pi * t / 48) + 0.75 * d + shock
        driver.append(d)
        lagged = driver[max(0, t - 6)]
        f = 0.7 * f + 0.5 * lagged + rng.gauss(0, 0.25)
        follower.append(f)
        s = 0.9 * s + 0.6 * abs(shock) + rng.gauss(0, 0.08)
        stress.append(s)
    return {"driver": driver, "follower": follower, "stress": stress}


def _dataset_harmonics(n: int, rng: random.Random) -> Dict[str, List[float]]:
    """Three phase-shifted harmonic mixes — strongly predictable; good for demos."""
    out = {}
    for k, name in enumerate(["alpha", "beta", "gamma"]):
        phase = k * 2.1
        out[name] = [
            math.sin(2 * math.pi * t / 36 + phase)
            + 0.5 * math.sin(2 * math.pi * t / 11 + phase * 1.7)
            + rng.gauss(0, 0.07)
            for t in range(n)
        ]
    return out


def _dataset_regimes(n: int, rng: random.Random) -> Dict[str, List[float]]:
    """lead flips drift regimes; follow tracks lead with lag; spread mean-reverts
    to their gap — a harder, regime-switching problem."""
    lead, follow, spread = [], [], []
    lv, fv, drift = 0.0, 0.0, 0.05
    for t in range(n):
        if rng.random() < 0.02:
            drift = -drift
        lv += drift + rng.gauss(0, 0.12)
        lead.append(lv)
        fv += 0.35 * (lead[max(0, t - 4)] - fv) + rng.gauss(0, 0.08)
        follow.append(fv)
        spread.append(lv - fv + rng.gauss(0, 0.05))
    return {"lead": lead, "follow": follow, "spread": spread}


DATASETS = {
    "coupled": {"fn": _dataset_coupled, "docs": "trend/cycle driver, lagged follower, shock-excited stress"},
    "harmonics": {"fn": _dataset_harmonics, "docs": "phase-shifted harmonic mixes — strongly predictable"},
    "regimes": {"fn": _dataset_regimes, "docs": "regime-switching drift, lagged follower, mean-reverting spread"},
}


def make_dataset(name: str = "coupled", n: int = 360, seed: int = 7) -> Dict[str, List[float]]:
    if name not in DATASETS:
        raise ValueError(f"unknown dataset '{name}' — options: {list(DATASETS)} or csv=<path>")
    return DATASETS[name]["fn"](n, random.Random(seed))


def load_csv(path: str) -> Dict[str, List[float]]:
    """Load any local CSV (header row of names; non-numeric columns like
    timestamps are dropped). Local-first: your own data never leaves disk."""
    path = os.path.expanduser(path)
    with open(path) as f:
        rows = [line.strip().split(",") for line in f if line.strip()]
    header, body = rows[0], rows[1:]
    cols: Dict[str, List[float]] = {}
    for i, name in enumerate(header):
        try:
            cols[name.strip()] = [float(r[i]) for r in body]
        except (ValueError, IndexError):
            continue  # non-numeric column (timestamps, labels)
    if not cols:
        raise ValueError(f"no numeric columns in {path}")
    return cols


# ── Genome ────────────────────────────────────────────────────────

class Genome:
    """One multivariate predictor: for each output variable i,
        pred_i = bias_i + Σ_{j,l} w_lin[i][j,l]·z_j[t−l] + amp_i·tanh(Σ_{j,l} w_nl[i][j,l]·z_j[t−l])
    over standardized (z-scored) series. lag is part of the genome."""

    def __init__(self, n_vars: int, lag: int, w_lin=None, w_nl=None, bias=None, amp=None,
                 rng: Optional[random.Random] = None, persistence=False):
        rng = rng or random
        self.n_vars = n_vars
        self.lag = lag
        k = n_vars * lag
        if w_lin is None:
            w_lin = [[rng.gauss(0, 0.12) for _ in range(k)] for _ in range(n_vars)]
            if persistence:  # seed genome: pred_i ≈ last value of var i
                for i in range(n_vars):
                    w_lin[i] = [0.0] * k
                    w_lin[i][i * lag] = 1.0
        self.w_lin = w_lin
        self.w_nl = w_nl if w_nl is not None else [[rng.gauss(0, 0.08) for _ in range(k)] for _ in range(n_vars)]
        self.bias = bias if bias is not None else [0.0] * n_vars
        self.amp = amp if amp is not None else [rng.gauss(0, 0.05) for _ in range(n_vars)]
        self.fitness: Optional[float] = None
        self.age = 0

    @property
    def id(self) -> str:
        raw = json.dumps([self.lag, self.bias, self.amp, self.w_lin, self.w_nl])
        return hashlib.sha256(raw.encode()).hexdigest()[:8]

    def predict(self, window: List[List[float]]) -> List[float]:
        """window: per-variable z-history lists, each ≥ lag long, most recent LAST."""
        V, L = self.n_vars, self.lag
        out = []
        for i in range(V):
            wl, wn = self.w_lin[i], self.w_nl[i]
            s_lin, s_nl = self.bias[i], 0.0
            for j in range(V):
                zj = window[j]
                base = j * L
                nz = len(zj)
                for l in range(L):
                    v = zj[nz - 1 - l]
                    s_lin += wl[base + l] * v
                    s_nl += wn[base + l] * v
            p = s_lin + self.amp[i] * math.tanh(s_nl)
            out.append(max(-Z_CLAMP, min(Z_CLAMP, p)))
        return out

    # — serialization —
    def to_dict(self) -> Dict:
        return {"n_vars": self.n_vars, "lag": self.lag, "w_lin": self.w_lin,
                "w_nl": self.w_nl, "bias": self.bias, "amp": self.amp,
                "fitness": self.fitness, "age": self.age}

    @classmethod
    def from_dict(cls, d: Dict) -> "Genome":
        g = cls(d["n_vars"], d["lag"], w_lin=d["w_lin"], w_nl=d["w_nl"],
                bias=d["bias"], amp=d["amp"])
        g.fitness = d.get("fitness")
        g.age = d.get("age", 0)
        return g


# ── Variation operators ───────────────────────────────────────────

def _remap_lag(row: List[float], n_vars: int, old_lag: int, new_lag: int) -> List[float]:
    out = [0.0] * (n_vars * new_lag)
    for j in range(n_vars):
        for l in range(min(old_lag, new_lag)):
            out[j * new_lag + l] = row[j * old_lag + l]
    return out


def crossover(a: Genome, b: Genome, rng: random.Random) -> Genome:
    """Row-level uniform crossover: the child inherits each output variable's
    whole predictor row (linear + nonlinear + bias + amp) from one parent."""
    lag = rng.choice([a.lag, b.lag])
    V = a.n_vars
    w_lin, w_nl, bias, amp = [], [], [], []
    for i in range(V):
        p = a if rng.random() < 0.5 else b
        w_lin.append(_remap_lag(p.w_lin[i], V, p.lag, lag))
        w_nl.append(_remap_lag(p.w_nl[i], V, p.lag, lag))
        bias.append(p.bias[i])
        amp.append(p.amp[i])
    return Genome(V, lag, w_lin=w_lin, w_nl=w_nl, bias=bias, amp=amp)


def mutate(g: Genome, rng: random.Random, max_lag: int,
           p_weight=0.15, sigma=0.12, p_zero=0.02, p_lag=0.10) -> Genome:
    V, lag = g.n_vars, g.lag
    if rng.random() < p_lag:
        new_lag = max(1, min(max_lag, lag + rng.choice([-1, 1])))
        if new_lag != lag:
            g = Genome(V, new_lag,
                       w_lin=[_remap_lag(r, V, lag, new_lag) for r in g.w_lin],
                       w_nl=[_remap_lag(r, V, lag, new_lag) for r in g.w_nl],
                       bias=list(g.bias), amp=list(g.amp))
            lag = new_lag
    for mat in (g.w_lin, g.w_nl):
        for row in mat:
            for k in range(len(row)):
                r = rng.random()
                if r < p_zero:
                    row[k] = 0.0
                elif r < p_zero + p_weight:
                    row[k] += rng.gauss(0, sigma)
    for i in range(V):
        if rng.random() < p_weight:
            g.bias[i] += rng.gauss(0, sigma * 0.5)
        if rng.random() < p_weight:
            g.amp[i] += rng.gauss(0, sigma * 0.5)
    return g


# ── Engine ────────────────────────────────────────────────────────

class EvolutionEngine:
    """Population manager: evaluate on the future window, select, vary, repeat."""

    def __init__(self, series: Dict[str, List[float]], dataset: str = "custom",
                 population: int = 40, max_lag: int = 6, horizon: int = 10,
                 train_frac: float = 0.8, seed: Optional[int] = None):
        if len(series) < 2:
            raise ValueError("need ≥ 2 variables for a multivariate problem")
        n = min(len(v) for v in series.values())
        if n < 60:
            raise ValueError(f"series too short ({n} points, need ≥ 60)")
        self.names = list(series.keys())
        self.series = {k: list(v[:n]) for k, v in series.items()}
        self.dataset = dataset
        self.pop_size = int(population)
        self.max_lag = int(max_lag)
        self.horizon = int(horizon)
        self.train_frac = train_frac
        self.rng = random.Random(seed)
        self.generation = 0
        self.history: List[Dict] = []

        # standardize with TRAIN-region stats only — the future window stays untouched
        self.split = int(n * train_frac)
        self.mu, self.sd = {}, {}
        for k in self.names:
            train = self.series[k][:self.split]
            m = sum(train) / len(train)
            var = sum((x - m) ** 2 for x in train) / max(1, len(train) - 1)
            self.mu[k], self.sd[k] = m, math.sqrt(var) or 1.0
        self.z = [[(x - self.mu[k]) / self.sd[k] for x in self.series[k]] for k in self.names]

        V = len(self.names)
        self.population: List[Genome] = []
        for i in range(self.pop_size):
            lag = self.rng.randint(2, self.max_lag)
            # a quarter of the founding population are persistence priors —
            # evolution starts from "tomorrow = today" and must beat it
            self.population.append(Genome(V, lag, rng=self.rng, persistence=(i % 4 == 0)))

    # — fitness: walk-forward multivariate error on the FUTURE window —

    def _evaluate(self, g: Genome) -> float:
        V, L, h = len(self.names), g.lag, self.horizon
        n = len(self.z[0])
        anchors = list(range(self.split, n - h, max(1, (n - h - self.split) // 12)))
        if not anchors:
            anchors = [self.split]
        se, cnt = 0.0, 0
        for t in anchors:
            window = [self.z[j][t - L:t] for j in range(V)]
            for step in range(h):
                pred = g.predict(window)
                actual_t = t + step
                for j in range(V):
                    se += (pred[j] - self.z[j][actual_t]) ** 2
                    cnt += 1
                    window[j] = window[j][1:] + [pred[j]]  # recursive rollout
        rmse = math.sqrt(se / cnt)
        return -rmse - PARSIMONY * g.lag

    def _tournament(self, k: int = 3) -> Genome:
        k = min(k, len(self.population))
        return max(self.rng.sample(self.population, k), key=lambda g: g.fitness)

    def run(self, generations: int = 25) -> Dict:
        t0 = time.time()
        for _ in range(int(generations)):
            for g in self.population:
                if g.fitness is None:
                    g.fitness = self._evaluate(g)
            self.population.sort(key=lambda g: g.fitness, reverse=True)
            fits = [g.fitness for g in self.population]
            self.generation += 1
            self.history.append({
                "gen": self.generation,
                "best": round(fits[0], 5),
                "mean": round(sum(fits) / len(fits), 5),
            })
            elites = self.population[:2]
            for e in elites:
                e.age += 1
            children = []
            while len(children) < self.pop_size - len(elites):
                child = crossover(self._tournament(), self._tournament(), self.rng)
                children.append(mutate(child, self.rng, self.max_lag))
            self.population = elites + children
        # final evaluation so reported fitness covers the last brood
        for g in self.population:
            if g.fitness is None:
                g.fitness = self._evaluate(g)
        self.population.sort(key=lambda g: g.fitness, reverse=True)
        return {
            "generations_run": int(generations),
            "generation": self.generation,
            "best": self.leaderboard(top=1)[0],
            "elapsed": round(time.time() - t0, 2),
        }

    # — outputs —

    def leaderboard(self, top: int = 20) -> List[Dict]:
        ranked = sorted(self.population, key=lambda g: g.fitness or -1e9, reverse=True)[:top]
        ws = self.weights(top=top)
        return [{
            "rank": i + 1, "id": g.id, "lag": g.lag, "age": g.age,
            "fitness": round(g.fitness, 5) if g.fitness is not None else None,
            "future_rmse": round(-(g.fitness + PARSIMONY * g.lag), 5) if g.fitness is not None else None,
            "weight": ws.get(g.id, 0.0),
        } for i, g in enumerate(ranked)]

    def weights(self, top: int = 20) -> Dict[str, float]:
        """Emission weights over genomes — the subnet's own softmax, reused."""
        ranked = sorted(self.population, key=lambda g: g.fitness or -1e9, reverse=True)[:top]
        scored = [(g.id, g.fitness) for g in ranked if g.fitness is not None]
        if not scored:
            return {}
        normed = normalize_scores([f * FITNESS_SCALE for _, f in scored])
        return {gid: w for (gid, _), w in zip(scored, normed)}

    def forecast(self, horizon: Optional[int] = None, tail: int = 60) -> Dict:
        """Best genome predicts the next `horizon` steps for every variable
        (recursive rollout from the end of the series), denormalized."""
        h = int(horizon or self.horizon)
        best = max(self.population, key=lambda g: g.fitness if g.fitness is not None else -1e9)
        if best.fitness is None:
            best.fitness = self._evaluate(best)
        V, L = len(self.names), best.lag
        window = [z[-L:] for z in self.z]
        preds = [[] for _ in range(V)]
        for _ in range(h):
            p = best.predict(window)
            for j in range(V):
                preds[j].append(p[j])
                window[j] = window[j][1:] + [p[j]]
        out = {}
        for j, k in enumerate(self.names):
            out[k] = {
                "history": [round(x, 5) for x in self.series[k][-tail:]],
                "forecast": [round(p * self.sd[k] + self.mu[k], 5) for p in preds[j]],
            }
        return {
            "genome": best.id, "lag": best.lag,
            "fitness": round(best.fitness, 5),
            "horizon": h, "dataset": self.dataset,
            "variables": out,
        }

    def status(self) -> Dict:
        best = self.history[-1]["best"] if self.history else None
        return {
            "dataset": self.dataset, "variables": self.names,
            "points": len(self.z[0]), "train_split": self.split,
            "population": self.pop_size, "generation": self.generation,
            "horizon": self.horizon, "max_lag": self.max_lag,
            "best_fitness": best,
            "history": self.history,
        }

    # — persistence (local-first: one JSON file, resumes across restarts) —

    def state_dict(self) -> Dict:
        return {
            "dataset": self.dataset, "names": self.names, "series": self.series,
            "population_size": self.pop_size, "max_lag": self.max_lag,
            "horizon": self.horizon, "train_frac": self.train_frac,
            "generation": self.generation, "history": self.history,
            "population": [g.to_dict() for g in self.population],
        }

    def save(self, path: str):
        path = os.path.expanduser(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.state_dict(), f)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: str) -> Optional["EvolutionEngine"]:
        path = os.path.expanduser(path)
        if not os.path.exists(path):
            return None
        with open(path) as f:
            s = json.load(f)
        eng = cls({k: s["series"][k] for k in s["names"]}, dataset=s["dataset"],
                  population=s["population_size"], max_lag=s["max_lag"],
                  horizon=s["horizon"], train_frac=s["train_frac"])
        eng.generation = s["generation"]
        eng.history = s["history"]
        eng.population = [Genome.from_dict(d) for d in s["population"]]
        eng.pop_size = len(eng.population) or eng.pop_size
        return eng
