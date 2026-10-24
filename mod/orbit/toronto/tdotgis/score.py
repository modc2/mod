"""
tdot.score — predicting a rental building's RentSafeTO evaluation score.

RentSafeTO scores an apartment building out of 100 on the state of its common
areas: lobbies, stairwells, elevators, garbage rooms, cladding, balcony guards,
the grounds. A low score is the closest thing Toronto publishes to an official
statement that a building is being let go. Roughly 3,500 buildings carry one.
Another ~150 registered buildings have never been evaluated, and every score on
the map is a snapshot of the year an inspector happened to visit.

So: can the *rest* of the open data predict that score?

The answer is a qualified yes, and the qualification is the point. The model
here explains a bit over a third of the variance between buildings — better
than guessing the city average, nowhere near a substitute for an inspection.
That is reported in the open (:func:`report`), because a model of building
condition that overstates itself is worse than no model.

What it is built from, all of it public and key-free:

    registration    the building's own filing — units, storeys, year built,
                    heating and elevator status, sprinklers, records kept,
                    who manages it (`apartment-building-registration`)
    evaluations     the target, and the year it was measured
                    (`apartment-building-evaluation`)
    health hazards  highrise health-hazard investigations within ~40 m
                    (`residential-health-hazards`)
    fire violations fire-code violations found at the address
                    (`residential-fire-inspection-results`)
    census          the neighbourhood's income, tenure, dwelling values and
                    repair backlog (2021 profile, via :mod:`tdotgis.census`)
    crime           police-reported incident rate for the neighbourhood

Deliberately *not* used: the evaluation's own per-area sub-scores. They sum to
the target, so a model fed them would score ~1.0 and know nothing.

The useful output is not the prediction — it's the **residual**. A building
predicted at 84 and evaluated at 61 is doing 23 points worse than buildings
with the same age, size, systems and neighbourhood. That is a shortlist worth
an inspector's morning, and it is what the map colours by.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections import Counter
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import census as N
from . import crime as C
from . import sources as S

# Registration and evaluation are annual-ish; the feature table is expensive to
# assemble (four datasets, one of them 125k rows) and changes about that often.
TABLE_TTL = 14 * S.DAY

REGISTRATION = ('apartment-building-registration',
                'Apartment Building Registration Data')
EVALUATION = ('apartment-building-evaluation',
              'Pre-2023 Apartment Building Evaluations')
HAZARDS = ('residential-health-hazards', 'Residential Health Inspections')
FIRE = ('residential-fire-inspection-results', 'inspection-results')

# A city block is ~150 m; this is deliberately tighter. A hazard case or fire
# violation counts against a building only if it is essentially at its door,
# because "a violation somewhere on this block" is a neighbourhood fact, and
# the neighbourhood is already a feature.
NEAR_DEGREES = 0.0005                    # ~40 m of longitude at 43.7°N
LAT_SQUEEZE = 0.72                       # a degree of latitude is longer

# The crime window that gets joined on. Fixed rather than parameterised: the
# model is refit rarely and a moving window would silently change its inputs.
CRIME_SINCE = '2022-01-01'

# The estimator's hard ceiling on categories per feature; see `_encode`.
MAX_LEVELS = 255
OTHER_LEVEL = '\x00other'                # cannot collide with a city value


# ─────────────────────────────────────────────────────────────────────────────
# reading the city's cells
# ─────────────────────────────────────────────────────────────────────────────

def _num(v: Any) -> Optional[float]:
    try:
        f = float(str(v).replace(',', '').strip())
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _yn(v: Any) -> Optional[float]:
    """
    A registration checkbox as 1/0.

    Blank is left as missing rather than folded into "no" — a landlord who did
    not answer "is there emergency power" is telling you something different
    from one who answered no, and the model can use the difference.
    """
    s = str(v or '').strip().upper()
    return 1.0 if s in ('YES', 'Y', 'TRUE') else 0.0 if s in ('NO', 'N', 'FALSE') else None


def _text(v: Any) -> str:
    return re.sub(r'\s+', ' ', str(v or '')).strip().upper()


# ─────────────────────────────────────────────────────────────────────────────
# what goes into the model
# ─────────────────────────────────────────────────────────────────────────────

# Registration columns read straight through as numbers.
COUNTS = {
    'units': 'CONFIRMED_UNITS',
    'storeys': 'CONFIRMED_STOREYS',
    'year_built': 'YEAR_BUILT',
    'year_registered': 'YEAR_REGISTERED',
    'elevators': 'NO_OF_ELEVATORS',
    'laundry_machines': 'NO_OF_LAUNDRY_ROOM_MACHINES',
    'barrier_free_units': 'NO_BARRIER_FREE_ACCESSBLE_UNITS',
    'accessible_parking': 'NO_OF_ACCESSIBLE_PARKING_SPACES',
    'heating_installed': 'HEATING_EQUIPMENT_YEAR_INSTALLED',
    'sprinkler_installed': 'SPRINKLER_SYSTEM_YEAR_INSTALLED',
}

# Yes/no columns. Two kinds are mixed in here on purpose: what the building
# *has* (sprinklers, an intercom) and what the landlord *keeps* (test records,
# a fire safety plan). The second kind is a proxy for whether anyone is
# actually running the building, which is what the inspection measures.
FLAGS = {
    'sprinklers': 'SPRINKLER_SYSTEM',
    'fire_alarm': 'FIRE_ALARM',
    'fire_safety_plan': 'APPROVED_FIRE_SAFETY_PLAN',
    'alarm_test_records': 'ANNUAL_FIRE_ALARM_TEST_RECORDS',
    'pump_test_records': 'ANNUAL_FIRE_PUMP_FLOW_TEST_RECORDS',
    'power_test_records': 'EMERG_POWER_SUPPLY_TEST_RECORDS',
    'tssa_records': 'TSSA_TEST_RECORDS',
    'sprinkler_records': 'SPRINKLER_SYSTEM_TEST_RECORD',
    'balconies': 'BALCONIES',
    'garbage_chutes': 'GARBAGE_CHUTES',
    'indoor_garbage': 'INDOOR_GARBAGE_STORAGE_AREA',
    'outdoor_garbage': 'OUTDOOR_GARBAGE_STORAGE_AREA',
    'intercom': 'INTERCOM',
    'laundry_room': 'LAUNDRY_ROOM',
    'lockers': 'LOCKER_OR_STORAGE_ROOM',
    'non_smoking': 'NON_SMOKING_BUILDING',
    'pets_allowed': 'PETS_ALLOWED',
    'barrier_free_entry': 'BARRIER_FREE_ACCESSIBILTY_ENTR',
    'fire_escape': 'EXTERIOR_FIRE_ESCAPE',
    'gas_metered': 'SEPARATE_GAS_METERS_EACH_UNIT',
    'hydro_metered': 'SEPARATE_HYDRO_METER_EACH_UNIT',
    'water_metered': 'SEPARATE_WATER_METERS_EA_UNIT',
    'cooling_room': 'IS_THERE_A_COOLING_ROOM',
    'emergency_power': 'IS_THERE_EMERGENCY_POWER',
}

# Categoricals. `cat:` marks them for the estimator, which splits on category
# sets rather than on an arbitrary integer ordering.
CATEGORIES = {
    'cat:heating': 'HEATING_TYPE',
    'cat:cooling': 'AIR_CONDITIONING_TYPE',
    'cat:elevator_status': 'ELEVATOR_STATUS',
    'cat:heating_status': 'HEATING_EQUIPMENT_STATUS',
    'cat:windows': 'WINDOW_TYPE',
    'cat:parking': 'PARKING_TYPE',
    'cat:tenure': 'PROPERTY_TYPE',
    'cat:ward': 'WARD',
}

# Neighbourhood context from the 2021 census profile.
CENSUS_FEATURES = ['median_income', 'renter_share', 'avg_value', 'major_repairs',
                   'tenant_strain', 'built_pre_1960', 'condo_share']

# Human labels, so the importance chart reads as English rather than as column
# names. Anything absent falls back to a de-underscored version of its key.
LABELS = {
    'units': 'Units in building',
    'storeys': 'Storeys',
    'year_built': 'Year built',
    'age': 'Age of building',
    'year_registered': 'Year registered with RentSafeTO',
    'elevators': 'Elevators',
    'laundry_machines': 'Laundry machines',
    'barrier_free_units': 'Barrier-free units',
    'accessible_parking': 'Accessible parking spaces',
    'heating_installed': 'Heating installed (year)',
    'heating_age': 'Age of heating equipment',
    'sprinkler_installed': 'Sprinklers installed (year)',
    'units_per_storey': 'Units per storey',
    'amenities': 'Amenities listed',
    'manager_portfolio': 'Buildings run by the same manager',
    'health_hazards': 'Health-hazard cases at the address',
    'fire_violations': 'Fire-code violations at the address',
    'eval_year': 'Year evaluated',
    'cat:heating': 'Heating type',
    'cat:cooling': 'Air conditioning',
    'cat:elevator_status': 'Elevator status',
    'cat:heating_status': 'Heating equipment status',
    'cat:windows': 'Window type',
    'cat:parking': 'Parking type',
    'cat:tenure': 'Ownership (private / social / TCHC)',
    'cat:ward': 'Ward',
    'cat:manager': 'Property management company',
    'hood:median_income': 'Neighbourhood median income',
    'hood:renter_share': 'Neighbourhood renter share',
    'hood:avg_value': 'Neighbourhood dwelling value',
    'hood:major_repairs': 'Neighbourhood homes needing major repairs',
    'hood:tenant_strain': 'Neighbourhood tenants over 30% of income',
    'hood:built_pre_1960': 'Neighbourhood stock built pre-1960',
    'hood:condo_share': 'Neighbourhood condominium share',
    'hood:crime_rate': 'Neighbourhood crime rate',
}


def label(feature: str) -> str:
    return LABELS.get(feature, feature.split(':')[-1].replace('_', ' ').capitalize())


# ─────────────────────────────────────────────────────────────────────────────
# nearby-event counting
# ─────────────────────────────────────────────────────────────────────────────

def _grid(points: List[Tuple[float, float]]) -> Dict[Tuple[int, int], List[Tuple[float, float]]]:
    """Bucket points into ~40 m cells so counting near a building is O(1)."""
    g: Dict[Tuple[int, int], List[Tuple[float, float]]] = {}
    for lng, lat in points:
        g.setdefault((int(lng / NEAR_DEGREES), int(lat / NEAR_DEGREES)), []).append((lng, lat))
    return g


def _count_near(g: dict, lng: float, lat: float) -> int:
    cx, cy = int(lng / NEAR_DEGREES), int(lat / NEAR_DEGREES)
    total = 0
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for x, y in g.get((cx + dx, cy + dy), ()):
                if abs(x - lng) < NEAR_DEGREES and abs(y - lat) < NEAR_DEGREES * LAT_SQUEEZE:
                    total += 1
    return total


def _hazard_points() -> List[Tuple[float, float]]:
    def fetch():
        rows = S.ckan_records(S.ckan_datastore(*HAZARDS)['id'], max_rows=50_000)
        out = []
        for r in rows:
            lng, lat = _num(r.get('lon')), _num(r.get('lat'))
            if lng and lat:
                out.append((lng, lat))
        return out
    return S.cached('score-hazard-points', TABLE_TTL, fetch)


def _fire_points() -> List[Tuple[float, float]]:
    def fetch():
        rows = S.ckan_records(S.ckan_datastore(*FIRE)['id'], max_rows=200_000)
        out = []
        for r in rows:
            try:
                lng, lat = json.loads(r['geometry'])['coordinates'][:2]
            except (KeyError, TypeError, ValueError, IndexError):
                continue
            if lng and lat:
                out.append((float(lng), float(lat)))
        return out
    return S.cached('score-fire-points', TABLE_TTL, fetch)


# ─────────────────────────────────────────────────────────────────────────────
# the feature table
# ─────────────────────────────────────────────────────────────────────────────

def _registrations() -> Dict[str, dict]:
    def fetch():
        rows = S.ckan_records(S.ckan_datastore(*REGISTRATION)['id'], max_rows=50_000)
        return {str(r.get('RSN') or '').strip(): r for r in rows if r.get('RSN')}
    return S.cached('score-registrations', TABLE_TTL, fetch)


def _evaluations() -> Dict[str, dict]:
    """Each building's most recent evaluation, keyed by RSN."""
    def fetch():
        rows = S.ckan_records(S.ckan_datastore(*EVALUATION)['id'], max_rows=50_000)
        best: Dict[str, dict] = {}
        for r in rows:
            rsn = str(r.get('RSN') or '').strip()
            if not rsn or 'CREATED IN ERROR' in str(r.get('SITE_ADDRESS') or '').upper():
                continue
            when = str(r.get('EVALUATION_COMPLETED_ON') or '')
            if rsn not in best or when > str(best[rsn].get('EVALUATION_COMPLETED_ON') or ''):
                best[rsn] = r
        return best
    return S.cached('score-evaluations', TABLE_TTL, fetch)


def _neighbourhood_context() -> Tuple[Callable[[float, float], Optional[str]], Dict[str, Dict[str, float]]]:
    """A point→neighbourhood lookup, and every context metric keyed by area."""
    from . import layers as L                      # boundary loaders live there

    locate = S.area_locator(L.neighbourhoods()['features'], 'AREA_LONG_CODE')
    columns: Dict[str, Dict[str, float]] = {}
    for metric in CENSUS_FEATURES:
        try:
            columns[f'hood:{metric}'] = N.values(metric)
        except Exception:                          # a census label the city moved
            continue
    try:
        counts = C.aggregate('neighbourhood', since=CRIME_SINCE)
        columns['hood:crime_rate'] = {a: float(v.get('incidents') or 0)
                                      for a, v in counts.items()}
    except Exception:
        pass
    return locate, columns


def table(refresh: bool = False) -> dict:
    """
    Every registered rental building as one row of features, plus its score.

    Buildings that have never been evaluated are kept, with ``score: None`` —
    they are exactly who the model is for.
    """
    if refresh:
        S.cache_clear('score-')

    def build() -> dict:
        registrations = _registrations()
        evaluations = _evaluations()
        hazards = _grid(_hazard_points())
        fires = _grid(_fire_points())
        locate, context = _neighbourhood_context()

        managers = Counter(_text(r.get('PROP_MANAGEMENT_COMPANY_NAME'))
                           for r in registrations.values())
        managers.pop('', None)

        rows = []
        for rsn, reg in registrations.items():
            ev = evaluations.get(rsn) or {}
            f: Dict[str, Any] = {}

            for key, col in COUNTS.items():
                f[key] = _num(reg.get(col))
            for key, col in FLAGS.items():
                f[key] = _yn(reg.get(col))
            for key, col in CATEGORIES.items():
                f[key] = _text(reg.get(col)) or None

            year = time.gmtime().tm_year
            f['age'] = year - f['year_built'] if f['year_built'] else None
            f['heating_age'] = (year - f['heating_installed']
                                if f['heating_installed'] else None)
            f['units_per_storey'] = (round(f['units'] / f['storeys'], 2)
                                     if f['units'] and f['storeys'] else None)
            f['amenities'] = float(len([a for a in
                                        str(reg.get('AMENITIES_AVAILABLE') or '').split(',')
                                        if a.strip()]))

            manager = _text(reg.get('PROP_MANAGEMENT_COMPANY_NAME'))
            f['manager_portfolio'] = float(managers.get(manager, 0)) or None
            f['cat:manager'] = manager or None

            # Registration carries no coordinates; the evaluation table geocodes
            # the same RSN. A building never evaluated therefore has no point,
            # and can be scored on its own filing but not on what is around it.
            lng, lat = _num(ev.get('LONGITUDE')), _num(ev.get('LATITUDE'))
            if lng and lat:
                f['health_hazards'] = float(_count_near(hazards, lng, lat))
                f['fire_violations'] = float(_count_near(fires, lng, lat))
                area = locate(lng, lat)
            else:
                f['health_hazards'] = f['fire_violations'] = None
                area = None
            for key, column in context.items():
                value = column.get(area) if area else None
                f[key] = float(value) if value is not None else None

            completed = str(ev.get('EVALUATION_COMPLETED_ON') or '')[:10]
            f['eval_year'] = _num(completed[:4])

            rows.append({
                'rsn': rsn,
                'address': str(reg.get('SITE_ADDRESS') or ev.get('SITE_ADDRESS') or '').strip().title(),
                'ward': str(ev.get('WARDNAME') or reg.get('WARD') or '').strip(),
                'manager': manager.title() or None,
                'lng': lng, 'lat': lat,
                'evaluated': completed or None,
                'score': _num(ev.get('SCORE')),
                'features': f,
            })

        return {
            'rows': rows,
            'buildings': len(rows),
            'evaluated': sum(1 for r in rows if r['score'] is not None),
            'located': sum(1 for r in rows if r['lng']),
            'built': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        }

    return S.cached('score-table', TABLE_TTL, build)


def feature_names(rows: List[dict]) -> List[str]:
    """Feature order, fixed so a fitted model and a lone building line up."""
    seen: List[str] = []
    for r in rows:
        for k in r['features']:
            if k not in seen:
                seen.append(k)
    return seen


# ─────────────────────────────────────────────────────────────────────────────
# the model
# ─────────────────────────────────────────────────────────────────────────────

# Fitting takes a few seconds and the inputs change fortnightly at most, so the
# fitted estimator is held for the life of the process. `report` is what gets
# cached to disk; an estimator is not JSON.
_FITTED: Dict[str, Any] = {}


def _sklearn():
    try:
        from sklearn.ensemble import HistGradientBoostingRegressor  # noqa: F401
    except ImportError:
        raise RuntimeError(
            'the score model needs scikit-learn (pip install scikit-learn). '
            'Every other tdot layer works without it.') from None
    import sklearn
    return sklearn


def _encode(rows: List[dict], names: List[str],
            levels: Optional[Dict[str, Dict[str, int]]] = None):
    """
    Rows → a float matrix, with categoricals mapped to level indices.

    Missing values stay NaN all the way into the estimator, which splits on
    them natively. Half of these columns are missing for some building — an
    imputed median would be a fact the city never published.
    """
    import numpy as np

    learn = levels is None
    levels = {} if learn else levels
    if learn:
        for name in names:
            if not name.startswith('cat:'):
                continue
            counts = Counter(str(r['features'].get(name)) for r in rows
                             if r['features'].get(name) is not None)
            # Toronto has 766 named property managers and most of them run one
            # building. The estimator caps a categorical at 255 levels anyway,
            # and a level seen once is noise, so the long tail shares the last
            # index — "some small landlord" is itself a useful category.
            common = [v for v, _ in counts.most_common(MAX_LEVELS - 1)]
            levels[name] = {v: i for i, v in enumerate(sorted(common))}
            if len(counts) > len(common):
                levels[name][OTHER_LEVEL] = len(common)

    X = np.full((len(rows), len(names)), np.nan, dtype=float)
    for i, r in enumerate(rows):
        for j, name in enumerate(names):
            v = r['features'].get(name)
            if v is None:
                continue
            if name.startswith('cat:'):
                lut = levels.get(name, {})
                idx = lut.get(str(v), lut.get(OTHER_LEVEL))
                if idx is not None:
                    X[i, j] = float(idx)
            else:
                X[i, j] = float(v)
    return X, levels


def _estimator(categorical: List[bool]):
    from sklearn.ensemble import HistGradientBoostingRegressor
    return HistGradientBoostingRegressor(
        max_iter=400, learning_rate=0.06, max_depth=6,
        l2_regularization=1.0, categorical_features=categorical,
        random_state=0)


def model(refresh: bool = False) -> dict:
    """
    Fit on every evaluated building and return the fitted parts plus metrics.

    Cross-validation is 5-fold over the same rows the model is fitted on, and
    every number in :func:`report` comes from the held-out folds — an in-sample
    R² on a booster is a number about the booster, not about Toronto.

    The fold estimators are kept, not just their predictions. A booster with
    400 iterations largely memorises its training rows: ask the full model
    about a building it was fitted on and it half-recites the answer. Anything
    said *about a specific evaluated building* — its prediction, its residual,
    what drives it — therefore goes through the one fold that never saw it.
    """
    if refresh:
        _FITTED.clear()
    if _FITTED and not refresh:
        return _FITTED

    _sklearn()
    import numpy as np
    from sklearn.inspection import permutation_importance
    from sklearn.model_selection import KFold

    data = table(refresh=refresh)
    trainable = [r for r in data['rows'] if r['score'] is not None]
    if len(trainable) < 200:
        raise RuntimeError(f'only {len(trainable)} evaluated buildings; '
                           'not enough to fit anything honest')

    names = feature_names(data['rows'])
    X, levels = _encode(trainable, names)
    y = np.array([r['score'] for r in trainable], dtype=float)

    # A column with one distinct value carries nothing and breaks the binner.
    keep = [j for j in range(len(names))
            if len(np.unique(X[np.isfinite(X[:, j]), j])) >= 2]
    dropped = [names[j] for j in range(len(names)) if j not in keep]
    names = [names[j] for j in keep]
    X = X[:, keep]
    categorical = [n.startswith('cat:') for n in names]

    predicted = np.empty(len(y))
    baseline = np.empty(len(y))
    folds: List[Any] = []
    fold_of: Dict[str, int] = {}
    for k, (train, test) in enumerate(KFold(n_splits=5, shuffle=True,
                                            random_state=0).split(X)):
        held = _estimator(categorical)
        held.fit(X[train], y[train])
        predicted[test] = held.predict(X[test])
        baseline[test] = y[train].mean()
        folds.append(held)
        for i in test:
            fold_of[trainable[i]['rsn']] = k

    est = _estimator(categorical)
    est.fit(X, y)

    # Permutation importance, not the booster's split counts: split counts
    # reward high-cardinality columns (ward, manager) for being splittable.
    imp = permutation_importance(est, X, y, n_repeats=5, random_state=0,
                                 scoring='r2', n_jobs=1)

    _FITTED.clear()
    _FITTED.update({
        'estimator': est, 'names': names, 'levels': levels,
        'categorical': categorical, 'dropped': dropped,
        'folds': folds, 'fold_of': fold_of,
        'y': y, 'cv_predicted': predicted, 'cv_baseline': baseline,
        'importance': dict(zip(names, (round(float(v), 4) for v in imp.importances_mean))),
        'trained': len(trainable), 'table': data,
        'fitted_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    })
    return _FITTED


def _metrics(y, p) -> dict:
    import numpy as np
    err = p - y
    ss_res = float((err ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    return {
        'mae': round(float(np.abs(err).mean()), 2),
        'rmse': round(math.sqrt(float((err ** 2).mean())), 2),
        'r2': round(1 - ss_res / ss_tot, 3) if ss_tot else None,
        'within_5': round(float((np.abs(err) <= 5).mean()) * 100, 1),
        'within_10': round(float((np.abs(err) <= 10).mean()) * 100, 1),
    }


def report(refresh: bool = False) -> dict:
    """
    How well the model does, what it learned from, and what it cannot see.

    Cached to disk alongside the predictions, so a freshly started API answers
    from the last fit rather than making the first caller wait half a minute
    for one. :func:`building` is the only entry point that must fit.
    """
    if refresh:
        S.cache_clear('score-report')
    return S.cached('score-report', TABLE_TTL, lambda: _report(refresh))


def _report(refresh: bool = False) -> dict:
    import numpy as np

    fit = model(refresh=refresh)
    y = fit['y']
    order = sorted(fit['importance'].items(), key=lambda kv: -kv[1])

    return {
        'target': {
            'name': 'RentSafeTO building evaluation score',
            'range': [0, 100],
            'buildings_scored': int(len(y)),
            'mean': round(float(y.mean()), 1),
            'sd': round(float(y.std()), 1),
            'min': int(y.min()), 'max': int(y.max()),
        },
        'accuracy': {
            'model': _metrics(y, fit['cv_predicted']),
            'baseline': _metrics(y, fit['cv_baseline']),
            'validation': '5-fold cross-validation; every figure is out-of-fold',
            'reading': ('The model is typically within {mae} points of a '
                        'building\'s actual score, against {base} for guessing '
                        'the city mean. It explains {pct}% of the difference '
                        'between buildings — useful for ranking, not a '
                        'substitute for an inspection.').format(
                            mae=_metrics(y, fit['cv_predicted'])['mae'],
                            base=_metrics(y, fit['cv_baseline'])['mae'],
                            pct=int(round((_metrics(y, fit['cv_predicted'])['r2'] or 0) * 100))),
        },
        'drivers': [{'feature': k, 'label': label(k), 'importance': v}
                    for k, v in order if v > 0][:15],
        'features': {
            'used': len(fit['names']),
            'dropped_constant': fit['dropped'],
            'excluded_by_design': (
                'the evaluation\'s own per-area sub-scores (entrance, stairwells, '
                'elevators, cladding …) — they sum to the target'),
        },
        'coverage': {
            'registered_buildings': fit['table']['buildings'],
            'ever_evaluated': fit['table']['evaluated'],
            'never_evaluated': fit['table']['buildings'] - fit['table']['evaluated'],
            'geocoded': fit['table']['located'],
        },
        'sources': [
            {'name': 'Apartment Building Registration', 'package': REGISTRATION[0],
             'role': 'features — the building\'s own filing'},
            {'name': 'Apartment Building Evaluation', 'package': EVALUATION[0],
             'role': 'target — the score being predicted'},
            {'name': 'Highrise Residential Health Hazards', 'package': HAZARDS[0],
             'role': 'feature — hazard cases at the address'},
            {'name': 'Residential Fire Inspection Results', 'package': FIRE[0],
             'role': 'feature — fire-code violations at the address'},
            {'name': 'Neighbourhood Profiles (2021 Census)', 'package': 'neighbourhood-profiles',
             'role': 'features — income, tenure, values, repair backlog'},
            {'name': 'TPS Major Crime Indicators', 'package': 'major-crime-indicators',
             'role': 'feature — neighbourhood incident count'},
        ],
        'caveats': [
            'Evaluations ran 2017–2023; a score is a snapshot of the year an '
            'inspector visited, and the year is itself a feature.',
            'Buildings never evaluated have no published coordinates, so they '
            'are predicted from their filing alone, without neighbourhood or '
            'nearby-violation context.',
            'Correlation only. The model says which buildings resemble '
            'low-scoring ones, not why any given building scores what it does.',
        ],
        'fitted_at': fit['fitted_at'],
        'table_built': fit['table']['built'],
    }


# ─────────────────────────────────────────────────────────────────────────────
# predictions
# ─────────────────────────────────────────────────────────────────────────────

def _predict_rows(fit: dict, rows: List[dict]):
    X, _ = _encode(rows, fit['names'], fit['levels'])
    return fit['estimator'].predict(X)


def predictions(refresh: bool = False) -> dict:
    """
    Every registered building as a point: predicted score, actual, residual.

    The prediction attached to an evaluated building is its **out-of-fold**
    one — the value a model that had never seen it would have produced. Using
    the in-sample fit here would shrink every residual toward zero and quietly
    hide the buildings this is meant to surface.
    """
    if refresh:
        S.cache_clear('score-predictions')
    return S.cached('score-predictions', TABLE_TTL, lambda: _predictions(refresh))


def _predictions(refresh: bool = False) -> dict:
    fit = model(refresh=refresh)
    rows = fit['table']['rows']

    trainable = [r for r in rows if r['score'] is not None]
    out_of_fold = {r['rsn']: float(p)
                   for r, p in zip(trainable, fit['cv_predicted'])}

    unseen = [r for r in rows if r['score'] is None]
    fitted = dict(zip((r['rsn'] for r in unseen), _predict_rows(fit, unseen))) \
        if unseen else {}

    feats = []
    for r in rows:
        if not (r['lng'] and r['lat']):
            continue
        predicted = out_of_fold.get(r['rsn'], fitted.get(r['rsn']))
        if predicted is None:
            continue
        predicted = round(float(predicted), 1)
        actual = r['score']
        props = {
            'rsn': r['rsn'],
            'address': r['address'],
            'ward': r['ward'],
            'manager': r['manager'],
            'units': r['features'].get('units'),
            'storeys': r['features'].get('storeys'),
            'year_built': r['features'].get('year_built'),
            'evaluated': r['evaluated'],
            'predicted': predicted,
            'score': int(actual) if actual is not None else None,
            # Negative = scoring worse than its peers. This is the column the
            # map colours by and the reason the layer exists.
            'residual': round(actual - predicted, 1) if actual is not None else None,
            'health_hazards': r['features'].get('health_hazards'),
            'fire_violations': r['features'].get('fire_violations'),
        }
        feats.append({'type': 'Feature',
                      'properties': props,
                      'geometry': {'type': 'Point',
                                   'coordinates': [round(r['lng'], 5), round(r['lat'], 5)]}})

    residuals = sorted(f['properties']['residual'] for f in feats
                       if f['properties']['residual'] is not None)
    fc = {'type': 'FeatureCollection', 'features': feats}
    fc['breaks'] = {
        'metric': 'residual',
        'stops': [round(residuals[min(int(i * len(residuals) / 6), len(residuals) - 1)], 1)
                  for i in range(6)] if residuals else [],
        'min': residuals[0] if residuals else None,
        'max': residuals[-1] if residuals else None,
        'count': len(residuals),
        'diverging': True,
    }
    acc = report()['accuracy']['model']
    fc['meta'] = {
        'metric': 'residual',
        'buildings': len(feats),
        'predicted_only': sum(1 for f in feats if f['properties']['score'] is None),
        'typical_error': acc['mae'],
        'r2': acc['r2'],
        'source': 'tdot score model — RentSafeTO + registration + city open data',
    }
    return fc


def outliers(limit: int = 25, direction: str = 'under') -> dict:
    """
    The buildings furthest from what their features predict.

    ``under`` are scoring below their peers — the shortlist. ``over`` are the
    other tail, which is worth reading too: a building that beats its own
    stock is usually somebody managing it well.
    """
    fc = predictions()
    # The point travels with each row so a caller can fly to a building without
    # having first loaded the whole layer to look its coordinates up.
    rows = [{**f['properties'],
             'lng': f['geometry']['coordinates'][0],
             'lat': f['geometry']['coordinates'][1]}
            for f in fc['features'] if f['properties']['residual'] is not None]
    rows.sort(key=lambda r: r['residual'], reverse=(direction == 'over'))
    return {
        'direction': direction,
        'buildings': rows[:max(1, min(int(limit), 200))],
        'reading': ('Each scored this many points below what buildings of the '
                    'same age, size, systems and neighbourhood score.'
                    if direction == 'under' else
                    'Each scored this many points above comparable buildings.'),
        'typical_error': fc['meta']['typical_error'],
    }


def building(rsn: str) -> dict:
    """
    One building: what the city knows, what the model predicts, and why.

    The "why" is a one-at-a-time sweep — each input reset to the building
    stock's median in turn, and the prediction taken again. It answers "what is
    *this* building's score being driven by", which a global importance chart
    cannot.

    Both the prediction and the sweep run on the fold estimator that never saw
    this building, so the number here is the same one the map shows.
    """
    import numpy as np

    fit = model()
    rows = fit['table']['rows']
    row = next((r for r in rows if r['rsn'] == str(rsn).strip()), None)
    if row is None:
        raise KeyError(f'no registered building with RSN {rsn!r}')

    fold = fit['fold_of'].get(row['rsn'])
    est = fit['folds'][fold] if fold is not None else fit['estimator']

    names = fit['names']
    X, _ = _encode([row], names, fit['levels'])
    base = float(est.predict(X)[0])

    # The stock's median for every feature, as the counterfactual to swap in.
    all_X, _ = _encode(rows, names, fit['levels'])
    medians = np.nanmedian(all_X, axis=0)

    contributions = []
    for j, name in enumerate(names):
        if not np.isfinite(X[0, j]) or not np.isfinite(medians[j]):
            continue
        swapped = X.copy()
        swapped[0, j] = medians[j]
        delta = base - float(est.predict(swapped)[0])
        if abs(delta) >= 0.05:
            contributions.append({'feature': name, 'label': label(name),
                                  'value': row['features'].get(name),
                                  'effect': round(delta, 2)})
    contributions.sort(key=lambda c: -abs(c['effect']))

    return {
        'rsn': row['rsn'],
        'address': row['address'],
        'ward': row['ward'],
        'manager': row['manager'],
        'units': row['features'].get('units'),
        'storeys': row['features'].get('storeys'),
        'year_built': row['features'].get('year_built'),
        'evaluated': row['evaluated'],
        'score': int(row['score']) if row['score'] is not None else None,
        'predicted': round(base, 1),
        'residual': (round(row['score'] - base, 1)
                     if row['score'] is not None else None),
        'typical_error': report()['accuracy']['model']['mae'],
        'drivers': contributions[:12],
        'note': ('Effects are what the prediction moves by when that one input '
                 'is swapped for the typical building\'s. They do not sum to '
                 'the prediction — the model is not additive.'),
    }
