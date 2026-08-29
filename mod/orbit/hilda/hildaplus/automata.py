"""
A cellular automaton for land use, calibrated on HILDA+.

The grid *is* the automaton. Every 0.5 degree cell holds six numbers — the
fraction of its area under urban, cropland, pasture, forest, grass/shrub and
sparse cover — and each annual step moves some of that mixture between classes
according to a rule that depends only on the cell and its eight neighbours.
That is a cellular automaton in the strict sense; it is also, less strictly,
the standard shape of a land use change model (CLUE-S, SLEUTH and friends),
because land use really does spread from where it already is.

The rule, per step, for the flow from class i to class j in a cell:

    flow[i,j] = S[i] * rate[i,j] * suit * pressure[j] * scenario[j]

    rate[i,j]     annual conversion probability, counted directly from the
                  HILDA+ transition layers — not fitted, observed
    suit          how change-prone this cell was during the training window,
                  relative to the average cell
    pressure[j]   1 - w + w * (neighbourhood mean of j / neighbourhood land),
                  the automaton part: conversion prefers cells whose
                  surroundings are already that class
    scenario[j]   1.0 unless you are asking a what-if question

Outflows are scaled down if they would take more of a class than the cell has,
so fractions stay non-negative and land area is conserved exactly.

The ``suit`` term is what makes this work at all. Land use change is not
spread evenly: applying the global average rate to every cell converts a
little bit of everything everywhere, which is a worse description of the world
than saying nothing changed. Real change is concentrated in frontiers, and the
model has to know where they are.

Which raises the obvious objection: if you learn where change happens from the
record and then predict that record, you have proved nothing. So the training
window is always disjoint from the run. ``run`` fits ``suit`` on the years
strictly before ``start`` and says so in ``out_of_sample``; a run that starts
at the beginning of the record has nothing to train on and is honestly
labelled in-sample.

``validate`` scores a run against what actually happened *and* against a
do-nothing baseline, on two separate axes, because the answer differs sharply
between them:

    area skill        the global trajectory. Strongly positive for the classes
                      whose regime holds — urban +93%, cropland +91% on a
                      1990-2019 hindcast trained on 1960-1990.
    allocation skill  cell-by-cell placement. Negative, around -12% on that
                      same run. The automaton does not beat "nothing moved".

Both numbers are reported everywhere, deliberately. The second one is the
unflattering one and it is the honest state of this model at 0.5 degrees:
change lands in weakly correlated places (+0.17 to +0.31 correlation with
observed change per class), and the misallocation costs more than the captured
change earns. Extrapolating each cell's training-window trend instead was
tried and is far worse — around -110% — which says where a class grew in
1960-1990 is simply not where it grows afterwards.

The aggregate area skill is also negative, and that is a property of the data
rather than the model: pasture and grassland reverse sign between the halves
of the record, so no stationary rate matrix can get both halves right.

A model you cannot embarrass is not a model.
"""

import json
import time
from typing import Dict, List, Optional

import numpy as np

from . import cube
from . import raster as R
from . import series
from . import sources as S

# Chosen by the sweep in ``calibrate``, which improves monotonically all the
# way to 1.0 — pure contagion, no spatially blind component. That the optimum
# sits on the boundary says the neighbourhood term is doing real work.
DEFAULT_NEIGHBOURHOOD_WEIGHT = 1.0


# ── neighbourhoods ───────────────────────────────────────────────────────

def moore_mean(a: np.ndarray) -> np.ndarray:
    """Mean of the 8 neighbours + self over a 3x3 Moore neighbourhood.

    Longitude wraps (the grid is a whole globe, so column 0 borders column
    719); latitude does not (there is nothing north of the north pole, so the
    edge row is repeated).
    """
    pad = np.pad(a, ((1, 1), (1, 1)), mode='edge')
    pad[:, 0] = np.pad(a[:, -1], (1, 1), mode='edge')
    pad[:, -1] = np.pad(a[:, 0], (1, 1), mode='edge')
    out = np.zeros_like(a, dtype=np.float32)
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            out += pad[dy:dy + a.shape[0], dx:dx + a.shape[1]]
    return out / 9.0


# ── calibration ──────────────────────────────────────────────────────────

def observed_rates(y0=None, y1=None, deg: float = S.DEFAULT_DEG) -> np.ndarray:
    """The (6, 6) annual transition probability matrix, straight from HILDA+.

    ``rate[i, j]`` is the share of class-i pixels that became class j in an
    average year of the span. Rows include their diagonal, so each row sums to
    one; the automaton only uses the off-diagonal terms.
    """
    doc = cube.require('transitions', deg)
    years = doc['years']
    y0 = years[0] if y0 is None else int(y0)
    y1 = years[-1] if y1 is None else int(y1)
    idx = [doc['index'][y] for y in years if y0 <= y <= y1]
    if not idx:
        raise ValueError(f'no transition years in {y0}-{y1}')
    m = doc['matrix'][idx].sum(axis=0).astype(np.float64)
    totals = m.sum(axis=1, keepdims=True)
    return np.divide(m, np.where(totals > 0, totals, 1.0)).astype(np.float32)


def fallback_rates(y0=None, y1=None, deg: float = S.DEFAULT_DEG) -> np.ndarray:
    """Transition rates inferred from state layers alone.

    Used only when the transition cube has not been ingested. Net change
    cannot tell a swap from a standstill, so this distributes each class's net
    loss across the classes that gained, proportionally. It is a weaker rule
    and ``rates_source`` says so in every response that relies on it.
    """
    doc = cube.require('states', deg)
    years = doc['years']
    y0 = years[0] if y0 is None else cube.nearest_year(y0, 'states', deg)
    y1 = years[-1] if y1 is None else cube.nearest_year(y1, 'states', deg)
    span = max(1, y1 - y0)
    a = cube.fractions(y0, deg)[:S.N_CLASSES]
    b = cube.fractions(y1, deg)[:S.N_CLASSES]
    area = R.cell_area_km2(deg)[:, None]
    ka = (a * area).sum(axis=(1, 2))
    kb = (b * area).sum(axis=(1, 2))
    delta = kb - ka
    losers = np.where(delta < 0, -delta, 0.0)
    gainers = np.where(delta > 0, delta, 0.0)
    rate = np.zeros((S.N_CLASSES, S.N_CLASSES), dtype=np.float64)
    if gainers.sum() > 0:
        share = gainers / gainers.sum()
        for i in range(S.N_CLASSES):
            if losers[i] <= 0 or ka[i] <= 0:
                continue
            rate[i] = share * (losers[i] / ka[i] / span)
    np.fill_diagonal(rate, 0.0)
    np.fill_diagonal(rate, 1.0 - rate.sum(axis=1))
    return rate.astype(np.float32)


def rates(y0=None, y1=None, deg: float = S.DEFAULT_DEG) -> tuple:
    """(matrix, provenance) — observed gross rates when we have them."""
    if cube.load('transitions', deg, quiet=True).get('ready'):
        return observed_rates(y0, y1, deg), 'hilda+ transition layers (gross)'
    return fallback_rates(y0, y1, deg), 'inferred from state layers (net only)'


def susceptibility(y0=None, y1=None, deg: float = S.DEFAULT_DEG,
                   floor: float = 0.05, ceiling: float = 25.0) -> np.ndarray:
    """How change-prone each cell is, relative to the average land cell.

    Measured as observed turnover per unit land over a training window, then
    normalised so a land-area-weighted average cell scores 1 — which keeps the
    global conversion total roughly where the rate matrix put it while moving
    it out of the stable interiors and into the frontiers.

    The floor matters: a cell that happened not to change during training is
    unlikely, not forbidden, and zeroing it would freeze whole regions
    permanently. The ceiling stops one violently churning cell from absorbing
    the world's conversion budget.
    """
    h, w = R.grid_shape(deg)
    if not cube.load('transitions', deg, quiet=True).get('ready'):
        return np.ones((h, w), dtype=np.float32)
    doc = cube.require('transitions', deg)
    years = doc['years']
    y0 = years[0] if y0 is None else int(y0)
    y1 = years[-1] if y1 is None else int(y1)
    idx = [doc['index'][y] for y in years if y0 <= y <= y1]
    if not idx:
        return np.ones((h, w), dtype=np.float32)
    turnover = doc['changed'][idx].astype(np.float32).sum(axis=0) / 255.0
    turnover /= max(len(idx), 1)
    land = cube.fractions(cube.nearest_year(y1, 'states', deg),
                          deg)[:S.N_CLASSES].sum(axis=0)
    # Turnover is a share of the whole cell; a half-land cell that converted
    # 10% of itself converted 20% of its land.
    rel = np.divide(turnover, np.maximum(land, 1e-6),
                    out=np.zeros_like(turnover), where=land > 0)
    area = R.cell_area_km2(deg)[:, None]
    weight = land * area
    mean = float((rel * weight).sum() / max(weight.sum(), 1e-9))
    if mean <= 0:
        return np.ones((h, w), dtype=np.float32)
    return np.clip(rel / mean, floor, ceiling).astype(np.float32)


# ── the automaton ────────────────────────────────────────────────────────

class Automaton:
    """Six class fractions per cell, stepped one year at a time."""

    def __init__(self, state: np.ndarray, rate: np.ndarray,
                 weight: float = DEFAULT_NEIGHBOURHOOD_WEIGHT,
                 scenario: Optional[Dict[str, float]] = None,
                 protect: Optional[np.ndarray] = None,
                 suit: Optional[np.ndarray] = None,
                 deg: float = S.DEFAULT_DEG):
        self.state = np.array(state[:S.N_CLASSES], dtype=np.float32, copy=True)
        self.rate = np.array(rate, dtype=np.float32, copy=True)
        np.fill_diagonal(self.rate, 0.0)          # diagonals are "stayed put"
        self.weight = float(np.clip(weight, 0.0, 1.0))
        self.deg = deg
        self.land = self.state.sum(axis=0)
        self.mask = self.land > 0
        self.protect = protect
        # Cell area, derived from the grid rather than from ``deg``, so a toy
        # grid works and a mismatched deg cannot silently misweight latitudes.
        # The state is fractions, but demand is an area, so every total the
        # allocator computes has to be area-weighted or high-latitude cells
        # convert as if they were tropical ones.
        self.area = R.cell_area_km2(180.0 / self.state.shape[1])[:, None
                                                                 ].astype(np.float32)
        self.suit = (np.ones_like(self.land) if suit is None
                     else np.asarray(suit, dtype=np.float32))
        self.multiplier = np.ones(S.N_CLASSES, dtype=np.float32)
        for key, val in (scenario or {}).items():
            self.multiplier[S.resolve_class(key)] = float(val)

    def step(self) -> np.ndarray:
        s = self.state
        # Neighbourhood attraction per class, normalised by how much land the
        # neighbourhood has: a coastal cell is not penalised for having ocean
        # on one side.
        nbr_land = np.maximum(moore_mean(self.land), 1e-9)
        pressure = np.empty_like(s)
        for j in range(S.N_CLASSES):
            pressure[j] = (1.0 - self.weight) + self.weight * (
                moore_mean(s[j]) / nbr_land) * 2.0
        pressure *= self.suit[None, :, :]
        if self.protect is not None:
            pressure = pressure * np.where(self.protect, 0.0, 1.0)[None, :, :]

        # Attractiveness of each cell for each i->j conversion. This decides
        # *where*, not *how much*.
        weight = np.einsum('ihw,jhw->ijhw', s, pressure,
                           optimize=True).astype(np.float32)
        if self.protect is not None:
            weight *= np.where(self.protect, 0.0, 1.0)[None, None, :, :]

        # ...and the observed rate decides how much. Each i->j plane is scaled
        # so its total equals rate[i,j] times the area currently under i.
        #
        # Without this the contagion term quietly rewrites the global budget:
        # cells surrounded by forest attract the most forest, and forest cells
        # have the most forest neighbours, so forest grows by feeding itself.
        # An earlier version did exactly that and turned an observed loss of
        # 0.75 M km2 into a simulated gain of 1.1 M. Suitability chooses the
        # location; the transition record sets the quantity.
        stock = (s * self.area).sum(axis=(1, 2))                  # km2 per class
        demand = self.rate * stock[:, None] * self.multiplier[None, :]
        totals = (weight * self.area).sum(axis=(2, 3))            # km2 of weight
        scale = np.divide(demand, totals, out=np.zeros_like(demand),
                          where=totals > 0)
        flow = weight * scale[:, :, None, None]
        # Never move more of a class out of a cell than the cell holds.
        out = flow.sum(axis=1)
        over = out > s
        if over.any():
            scale = np.ones_like(out)
            np.divide(s, np.maximum(out, 1e-12), out=scale, where=over)
            flow *= scale[:, None, :, :]
        self.state = s - flow.sum(axis=1) + flow.sum(axis=0)
        np.clip(self.state, 0.0, None, out=self.state)
        self.state *= np.where(self.mask, 1.0, 0.0)
        # Renormalise to the cell's land area; floating point drift over sixty
        # steps would otherwise quietly create or destroy continents.
        total = self.state.sum(axis=0)
        np.divide(self.state, np.maximum(total, 1e-9), out=self.state,
                  where=(total > 0))
        self.state *= self.land
        return self.state

    def run(self, steps: int) -> List[np.ndarray]:
        return [self.step().copy() for _ in range(int(steps))]


# ── driving it ───────────────────────────────────────────────────────────

def _protect_mask(protect, deg):
    if not protect:
        return None
    box = S.resolve_bbox(protect if isinstance(protect, str) else None,
                         None if isinstance(protect, str) else protect)
    h, w = R.grid_shape(deg)
    mask = np.zeros((h, w), dtype=bool)
    rs, cs = R.bbox_slice(box, deg)
    mask[rs, cs] = True
    return mask


def training_window(start: int, deg: float = S.DEFAULT_DEG) -> tuple:
    """(y0, y1, out_of_sample) — the years the model may learn from.

    Everything strictly before the run's start year. A run that begins at the
    beginning of the record has nothing to learn from, so it trains on the
    whole record and is flagged in-sample; its skill number is an upper bound,
    not a forecast.
    """
    doc = cube.require('states', deg)
    first = doc['years'][0]
    if start > first:
        return first, start, True
    return first, doc['years'][-1], False


def run(start=None, end=None, weight: float = DEFAULT_NEIGHBOURHOOD_WEIGHT,
        scenario=None, protect=None, calibrate_on=None,
        deg: float = S.DEFAULT_DEG, keep_frames: bool = True) -> dict:
    """Step the automaton from ``start`` to ``end`` and report what it did.

    Rates and susceptibility are fitted on the years before ``start`` unless
    ``calibrate_on`` overrides the window. ``end`` may run past the record, in
    which case there is nothing to compare against and the result is a
    projection, labelled as one.
    """
    doc = cube.require('states', deg)
    start = doc['years'][0] if start is None else cube.nearest_year(start, 'states', deg)
    end = int(doc['years'][-1] if end is None else end)
    if end <= start:
        raise ValueError(f'end ({end}) must be after start ({start})')
    t0y, t1y, oos = training_window(start, deg)
    if calibrate_on:
        t0y, t1y = calibrate_on[0] or t0y, calibrate_on[1] or t1y
        oos = int(t1y) <= start
    rate, provenance = rates(t0y, t1y, deg)
    suit = susceptibility(t0y, t1y, deg)
    scen = _parse_scenario(scenario)
    ca = Automaton(cube.fractions(start, deg), rate, weight, scen,
                   _protect_mask(protect, deg), suit, deg)
    t0 = time.time()
    frames = ca.run(end - start)
    years = list(range(start + 1, end + 1))
    area = R.cell_area_km2(deg)[:, None]
    keys = [c['key'] for c in S.CLASSES]
    km2 = {k: [float((cube.fractions(start, deg)[i] * area).sum())]
           for i, k in enumerate(keys)}
    for f in frames:
        for i, k in enumerate(keys):
            km2[k].append(float((f[i] * area).sum()))
    out = {
        'start': start, 'end': end, 'years': [start] + years,
        'weight': ca.weight, 'scenario': scen or {},
        'protect': protect or None,
        'rates_source': provenance,
        'trained_on': [int(t0y), int(t1y)],
        'out_of_sample': bool(oos),
        'rate_matrix': [[float(v) for v in row] for row in ca.rate],
        'classes': keys,
        'km2': km2,
        'projection': end > doc['years'][-1],
        'seconds': round(time.time() - t0, 2),
    }
    if keep_frames:
        out['_frames'] = {y: f for y, f in zip(years, frames)}
        out['_frames'][start] = cube.fractions(start, deg)[:S.N_CLASSES]
    obs_last = doc['years'][-1]
    if end <= obs_last:
        out['skill'] = score(frames[-1], cube.fractions(end, deg)[:S.N_CLASSES],
                             cube.fractions(start, deg)[:S.N_CLASSES], deg)
    return out


def _parse_scenario(scenario) -> Dict[str, float]:
    if not scenario:
        return {}
    if isinstance(scenario, str):
        try:
            scenario = json.loads(scenario)
        except json.JSONDecodeError:
            out = {}
            for part in scenario.split(','):
                k, _, v = part.partition('=')
                if k.strip():
                    out[k.strip()] = float(v or 1.0)
            scenario = out
    return {S.CLASSES[S.resolve_class(k)]['key']: float(v)
            for k, v in dict(scenario).items()}


def score(pred: np.ndarray, obs: np.ndarray, baseline: np.ndarray,
          deg: float = S.DEFAULT_DEG) -> dict:
    """How close the simulation landed, against doing nothing at all.

    ``baseline`` is the start state: a model that predicts no change. Two very
    different questions get separate answers, because this model is good at
    one and bad at the other and a single number hides that:

    ``area_skill``       did it get the global trajectory right — how much of
                         each class there is in the end year?
    ``allocation_skill`` did it put the change in the right cells, better than
                         assuming nothing moved?

    Both are expressed as the share of the no-change baseline's error removed.
    Negative means worse than doing nothing. On a mostly stable world over a
    few decades, "nothing changed" is a strong prediction and beating it
    cell-by-cell is hard; reporting only the flattering number is how land use
    models end up looking better than they are.
    """
    # Derive the cell size from the arrays rather than trusting ``deg``: a
    # mismatch between the two would silently weight the wrong latitudes.
    area = R.cell_area_km2(180.0 / obs.shape[1])[:, None]
    land = obs.sum(axis=0) > 0
    per = {}
    tot_p = tot_b = tot_ap = tot_ab = 0.0
    for i, c in enumerate(S.CLASSES):
        err_p = float(np.abs((pred[i] - obs[i]) * area)[land].sum())
        err_b = float(np.abs((baseline[i] - obs[i]) * area)[land].sum())
        # Global totals: how much of this class exists, ignoring where.
        area_p = abs(float(((pred[i] - obs[i]) * area).sum()))
        area_b = abs(float(((baseline[i] - obs[i]) * area).sum()))
        tot_p += err_p
        tot_b += err_b
        tot_ap += area_p
        tot_ab += area_b
        per[c['key']] = {
            'model_err_km2': err_p, 'persistence_err_km2': err_b,
            'skill': (1.0 - err_p / err_b) if err_b > 0 else None,
            'area_err_km2': float(((pred[i] - obs[i]) * area).sum()),
            'area_skill': (1.0 - area_p / area_b) if area_b > 0 else None,
        }
    return {'per_class': per,
            'model_err_km2': tot_p, 'persistence_err_km2': tot_b,
            'skill': (1.0 - tot_p / tot_b) if tot_b > 0 else None,
            'allocation_skill': (1.0 - tot_p / tot_b) if tot_b > 0 else None,
            'area_err_km2': tot_ap, 'persistence_area_err_km2': tot_ab,
            'area_skill': (1.0 - tot_ap / tot_ab) if tot_ab > 0 else None,
            'note': 'area_skill scores the global trajectory, '
                    'allocation_skill scores cell-by-cell placement; both are '
                    'the share of the no-change baseline error removed, and '
                    'negative means worse than assuming nothing moved'}


def calibrate(start=None, end=None, grid=None, deg: float = S.DEFAULT_DEG) -> dict:
    """Sweep the neighbourhood weight and keep the value that hindcasts best.

    One free parameter, one line search, scored on held-out-in-time data (the
    rates come from the transition record, the weight is chosen by how well
    the run reproduces the end year's map).
    """
    weights = ([float(w) for w in grid] if grid
               else [0.0, 0.15, 0.3, 0.45, 0.6, 0.75, 0.9, 1.0])
    trials = []
    for w in weights:
        r = run(start, end, weight=w, deg=deg, keep_frames=False)
        trials.append({'weight': w,
                       'skill': r['skill']['allocation_skill'],
                       'area_skill': r['skill']['area_skill'],
                       'model_err_km2': r['skill']['model_err_km2']})
    best = max(trials, key=lambda t: (t['skill'] is not None, t['skill']))
    return {'trials': trials, 'best': best,
            'persistence_err_km2': run(start, end, weight=best['weight'],
                                       deg=deg, keep_frames=False
                                       )['skill']['persistence_err_km2'],
            'note': 'weight 0 is a spatially blind model: every cell converts '
                    'at the global average rate. Higher weights make '
                    'conversion contagious.'}


def validate(start=None, end=None, weight: float = DEFAULT_NEIGHBOURHOOD_WEIGHT,
             deg: float = S.DEFAULT_DEG) -> dict:
    """Hindcast and report the scorecard, with the observed curve alongside."""
    r = run(start, end, weight=weight, deg=deg, keep_frames=False)
    obs = series.series(years=f'{r["start"]}-{r["end"]}', deg=deg)
    keys = [c['key'] for c in S.CLASSES]
    return {'start': r['start'], 'end': r['end'], 'weight': r['weight'],
            'rates_source': r['rates_source'],
            'skill': r['skill'],
            'simulated_km2': r['km2'],
            'observed_km2': {k: obs['km2'][k] for k in keys},
            'years': r['years']}


def compare_frame(year, weight: float = DEFAULT_NEIGHBOURHOOD_WEIGHT,
                  start=None, deg: float = S.DEFAULT_DEG) -> dict:
    """Simulated vs observed maps for one year, as dominant-class grids."""
    r = run(start, year, weight=weight, deg=deg)
    sim = r['_frames'][int(year)]
    obs = cube.fractions(cube.nearest_year(year, 'states', deg), deg)[:S.N_CLASSES]
    return {'year': int(year), 'weight': r['weight'], 'start': r['start'],
            'skill': r.get('skill'), 'sim': sim, 'obs': obs}
