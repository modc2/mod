"""forecast — a prediction game over any number series, scored against reality.

Nothing here knows what a GPU is. A *series* is a name (`gpu:h100`) and a run
of timestamped values (`ticks`) that someone else feeds in; a *player* calls
where a series will be at a *horizon* from now; when that time comes the call
is scored against the tick nearest the target, and the leaderboard is the mean
of those scores. Lift this file into another module and it forecasts TAO, rent
or the weather unchanged.

Rules the scoring rests on, all of them about fairness:

  1. **Locked at the call.** A prediction stores the series' last value when it
     was made (`base`). Nobody can edit one; the only move is a new call.
  2. **One open call per (player, series, horizon).** Spamming the same
     question a hundred times cannot buy a better average.
  3. **Bots play by the same rules.** The baselines (`naive`, `mean`, `drift`,
     `ewma`) submit through the same `predict` as a person, so "did I beat the
     robots" is a fair question.
  4. **Reality decides, or nobody wins.** A call resolves against a tick within
     `tolerance` of its target; if the ticker was down then, it is *void* —
     never scored against a guess.

Score per call: `100 * 0.5 ** (abs_pct_error / 5%)` — exact is 100, 5% off is
50, 10% off is 25. Each scored call also records whether it beat *persistence*
(the base value carried forward), which is the honest measure of skill: a
forecaster that cannot beat "nothing changes" has not forecast anything.

Players are name + a key minted on first call (stored hashed). No account, no
email, no third party — the key lives in the player's browser or agent.
"""

import hashlib
import hmac
import math
import os
import re
import secrets
import sqlite3
import threading
import time

HALF_LIFE_PCT = 5.0           # every 5% of error halves the score
HORIZONS = {'1h': 3600, '6h': 6 * 3600, '24h': 86400, '7d': 7 * 86400}
MIN_RANKED = 3                # resolved calls before a player is ranked
MAX_OPEN = 200                # open calls one player may hold
NEW_PLAYERS_PER_HOUR = 120    # a public form should not mint names forever
NAME = re.compile(r'^[a-z0-9][a-z0-9_.-]{1,23}$')
BOTS = {
    'naive': 'persistence — says the last value will still be the value',
    'mean': 'mean of the last 24h of ticks',
    'drift': 'least-squares line through the last 24h, carried forward',
    'ewma': 'exponentially weighted mean, half-life 6h',
}
BOT_PREFIX = 'bot.'

SCHEMA = """
create table if not exists ticks (
  series text not null, t real not null, value real not null, meta text,
  primary key (series, t));
create table if not exists players (
  name text primary key, key_hash text not null, created real not null,
  bot integer not null default 0, about text);
create table if not exists predictions (
  id integer primary key autoincrement,
  player text not null, series text not null, horizon text not null,
  made real not null, target real not null, base real, value real not null,
  note text,
  state text not null default 'open',          -- open | scored | void
  actual real, actual_t real, ape real, points real, beat_naive integer,
  direction_ok integer);
create index if not exists p_open on predictions(state, target);
create index if not exists p_player on predictions(player, state);
create index if not exists p_series on predictions(series, state);
"""


class ForecastError(ValueError):
    """A call the rules refuse — the message says which rule."""

    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


def points(value, actual):
    """The score for one call: 100 when exact, halving every HALF_LIFE_PCT."""
    if not actual:
        return 0.0, None
    ape = abs(value - actual) / abs(actual) * 100
    return round(100 * 0.5 ** (ape / HALF_LIFE_PCT), 2), round(ape, 3)


def horizon_seconds(h):
    if h not in HORIZONS:
        raise ForecastError(f'horizon must be one of {", ".join(HORIZONS)}')
    return HORIZONS[h]


def tolerance(seconds, tick_every=1200):
    """How far from the target a tick may sit and still be the answer."""
    return max(1.5 * tick_every, 0.05 * seconds)


def _hash(key):
    return hashlib.sha256(key.encode()).hexdigest()


class Board:
    """One game: ticks, players, predictions, scores. Thread-safe."""

    def __init__(self, path, tick_every=1200):
        self.path = path
        self.tick_every = tick_every
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.execute('pragma journal_mode=wal')
            self._db.commit()

    def _q(self, sql, args=()):
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    def _x(self, sql, args=()):
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur

    # ── reality ───────────────────────────────────────────────────────

    def tick(self, values, t=None, meta=None):
        """Record one observation per series: {series: value}. Then resolve."""
        t = float(t or time.time())
        rows = [(s, t, float(v), (meta or {}).get(s)) for s, v in values.items()
                if v is not None and math.isfinite(float(v)) and float(v) > 0]
        with self._lock:
            self._db.executemany(
                'insert or replace into ticks(series,t,value,meta) values (?,?,?,?)', rows)
            self._db.commit()
        return {'t': t, 'series': len(rows), 'resolved': self.resolve(now=t)}

    def last(self, series):
        r = self._q('select t, value from ticks where series=? order by t desc limit 1',
                    (series,))
        return r[0] if r else None

    def last_meta(self, series):
        """Whatever the feeder attached to the latest tick (a JSON string)."""
        r = self._q('select meta from ticks where series=? order by t desc limit 1',
                    (series,))
        return r[0]['meta'] if r else None

    def history(self, series, since=None, limit=2000):
        since = since if since is not None else time.time() - 7 * 86400
        rows = self._q('select t, value from ticks where series=? and t>=? '
                       'order by t desc limit ?', (series, since, int(limit)))
        return list(reversed(rows))

    def series(self):
        """Every series with its last value and the change over 24h."""
        out = []
        for r in self._q('select series, count(*) n, max(t) last_t from ticks '
                         'group by series order by series'):
            last = self.last(r['series'])
            day = self._q('select value from ticks where series=? and t<=? '
                          'order by t desc limit 1',
                          (r['series'], last['t'] - 86400 + 60))
            prev = day[0]['value'] if day else None
            out.append({'series': r['series'], 'value': last['value'], 't': last['t'],
                        'ticks': r['n'],
                        'change_24h_pct': round((last['value'] / prev - 1) * 100, 2)
                        if prev else None})
        return out

    # ── players ───────────────────────────────────────────────────────

    def join(self, name, about=None, bot=False):
        """Mint a player. Returns the key — once. It is stored only as a hash."""
        name = str(name or '').strip().lower()
        if not NAME.match(name):
            raise ForecastError('name: 2-24 chars, a-z 0-9 _ . - , starting with a '
                                'letter or digit')
        if name.startswith(BOT_PREFIX) and not bot:
            raise ForecastError(f'names starting "{BOT_PREFIX}" belong to the baselines')
        if self._q('select 1 from players where name=?', (name,)):
            raise ForecastError(f'{name} is taken — pick another, or send its key',
                                status=409)
        if not bot:
            recent = self._q('select count(*) n from players where bot=0 and created>?',
                             (time.time() - 3600,))[0]['n']
            if recent >= NEW_PLAYERS_PER_HOUR:
                raise ForecastError('too many new players this hour — try later',
                                    status=429)
        key = secrets.token_urlsafe(18)
        self._x('insert into players(name,key_hash,created,bot,about) values (?,?,?,?,?)',
                (name, _hash(key), time.time(), int(bot), about))
        return {'player': name, 'key': key,
                'note': 'keep this key — it is the only way to play as this name'}

    def _check(self, name, key):
        r = self._q('select key_hash from players where name=?', (name,))
        if not r:
            return False
        return hmac.compare_digest(r[0]['key_hash'], _hash(str(key or '')))

    # ── calls ─────────────────────────────────────────────────────────

    def predict(self, player, key, series, horizon, value, note=None, now=None,
                _trusted=False):
        """Call where `series` will be `horizon` from now. Locked once made."""
        player = str(player or '').strip().lower()
        if not _trusted and not self._check(player, key):
            raise ForecastError('unknown player or wrong key — join first', status=401)
        secs = horizon_seconds(horizon)
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ForecastError('value must be a number')
        if not math.isfinite(value) or value <= 0:
            raise ForecastError('value must be a positive number')
        last = self.last(series)
        if not last:
            raise ForecastError(f'no series {series} — see the series list', status=404)
        now = float(now or time.time())
        if now - last['t'] > max(3 * self.tick_every, 3600):
            raise ForecastError(f'{series} has not ticked since {int(now - last["t"])}s '
                                f'ago — calls are closed until it does', status=409)
        if not (last['value'] / 20 <= value <= last['value'] * 20):
            raise ForecastError(f'{value} is more than 20x away from the current '
                                f'{last["value"]} — that is a typo, not a forecast')
        with self._lock:
            if self._q('select 1 from predictions where player=? and series=? and '
                       'horizon=? and state=\'open\'', (player, series, horizon)):
                raise ForecastError(f'{player} already has an open {horizon} call on '
                                    f'{series} — wait for it to resolve', status=409)
            n_open = self._q('select count(*) n from predictions where player=? and '
                             'state=\'open\'', (player,))[0]['n']
            if n_open >= MAX_OPEN:
                raise ForecastError(f'{MAX_OPEN} open calls is the limit', status=429)
            cur = self._x('insert into predictions(player,series,horizon,made,target,'
                          'base,value,note) values (?,?,?,?,?,?,?,?)',
                          (player, series, horizon, now, now + secs, last['value'],
                           value, (str(note)[:140] if note else None)))
        return self.call(cur.lastrowid)

    def call(self, pid):
        r = self._q('select * from predictions where id=?', (int(pid),))
        if not r:
            raise ForecastError(f'no call {pid}', status=404)
        return r[0]

    def resolve(self, now=None):
        """Score every call whose target has passed. Returns how many changed."""
        now = float(now or time.time())
        n = 0
        for p in self._q('select * from predictions where state=\'open\' and target<=?',
                         (now,)):
            tol = tolerance(HORIZONS[p['horizon']], self.tick_every)
            near = self._q('select t, value from ticks where series=? and t between ? and ? '
                           'order by abs(t-?) limit 1',
                           (p['series'], p['target'] - tol, p['target'] + tol, p['target']))
            if near:
                actual = near[0]['value']
                pts, ape = points(p['value'], actual)
                naive_err = abs((p['base'] or actual) - actual)
                beat = int(abs(p['value'] - actual) < naive_err)
                moved = actual - (p['base'] or actual)
                called = p['value'] - (p['base'] or p['value'])
                direction = None if moved == 0 else int(moved * called > 0)
                self._x('update predictions set state=\'scored\', actual=?, actual_t=?, '
                        'ape=?, points=?, beat_naive=?, direction_ok=? where id=?',
                        (actual, near[0]['t'], ape, pts, beat, direction, p['id']))
                n += 1
            elif now > p['target'] + tol:
                self._x('update predictions set state=\'void\' where id=?', (p['id'],))
                n += 1
        return n

    # ── the board ─────────────────────────────────────────────────────

    def leaderboard(self, series=None, horizon=None, since=None, limit=100):
        """Players ranked by mean points; under MIN_RANKED calls is provisional."""
        where, args = ['p.state=\'scored\''], []
        if series:
            where.append('p.series=?'); args.append(series)
        if horizon:
            horizon_seconds(horizon); where.append('p.horizon=?'); args.append(horizon)
        if since:
            where.append('p.target>=?'); args.append(float(since))
        rows = self._q(
            'select p.player, pl.bot, count(*) n, avg(p.points) score, '
            'sum(p.points) total, avg(p.ape) mape, avg(p.beat_naive) beat, '
            'avg(p.direction_ok) hit, max(p.points) best '
            'from predictions p join players pl on pl.name=p.player '
            f'where {" and ".join(where)} group by p.player', args)
        opened = {r['player']: r['n'] for r in self._q(
            'select player, count(*) n from predictions where state=\'open\' group by player')}
        for r in rows:
            r['ranked'] = r['n'] >= MIN_RANKED
            r['bot'] = bool(r['bot'])
            r['open'] = opened.get(r['player'], 0)
            for k in ('score', 'total', 'mape', 'best'):
                r[k] = round(r[k], 2) if r[k] is not None else None
            for k in ('beat', 'hit'):
                r[k] = round(r[k] * 100, 1) if r[k] is not None else None
        rows.sort(key=lambda r: (not r['ranked'], -(r['score'] or 0), -r['n']))
        rank = 0
        for r in rows:
            if r['ranked']:
                rank += 1
                r['rank'] = rank
            else:
                r['rank'] = None
        # players who have only open calls still belong on the board
        seen = {r['player'] for r in rows}
        for name, n in opened.items():
            if name not in seen:
                bot = self._q('select bot from players where name=?', (name,))
                rows.append({'player': name, 'bot': bool(bot and bot[0]['bot']),
                             'n': 0, 'open': n, 'ranked': False, 'rank': None,
                             'score': None, 'total': None, 'mape': None, 'beat': None,
                             'hit': None, 'best': None})
        return {'board': rows[:int(limit)], 'min_ranked': MIN_RANKED,
                'scoring': f'100 x 0.5^(abs % error / {HALF_LIFE_PCT}%) per call; '
                           f'rank = mean, after {MIN_RANKED} scored calls',
                'filters': {'series': series, 'horizon': horizon, 'since': since}}

    def calls(self, player=None, series=None, state=None, limit=50):
        where, args = [], []
        for col, v in (('player', player), ('series', series), ('state', state)):
            if v:
                where.append(f'{col}=?'); args.append(v.lower() if col == 'player' else v)
        sql = 'select * from predictions'
        if where:
            sql += ' where ' + ' and '.join(where)
        sql += ' order by id desc limit ?'
        return self._q(sql, args + [int(limit)])

    # ── baselines ─────────────────────────────────────────────────────

    def ensure_bots(self):
        for b, about in BOTS.items():
            if not self._q('select 1 from players where name=?', (BOT_PREFIX + b,)):
                self.join(BOT_PREFIX + b, about=about, bot=True)

    def bots_play(self, now=None):
        """Every baseline makes every call it does not already have open."""
        self.ensure_bots()
        now = float(now or time.time())
        made = 0
        for s in self.series():
            hist = self.history(s['series'], since=now - 86400)
            for b in BOTS:
                v = baseline(b, hist, now)
                if v is None:
                    continue
                for h in HORIZONS:
                    try:
                        self.predict(BOT_PREFIX + b, None, s['series'], h,
                                     _project(b, hist, now + HORIZONS[h]) or v,
                                     now=now, _trusted=True)
                        made += 1
                    except ForecastError:
                        pass
        return made

    def stats(self):
        c = self._q('select state, count(*) n from predictions group by state')
        return {'calls': {r['state']: r['n'] for r in c},
                'players': self._q('select count(*) n from players where bot=0')[0]['n'],
                'series': len(self.series()),
                'ticks': self._q('select count(*) n from ticks')[0]['n']}


def baseline(kind, hist, now):
    """A baseline's call for 'now' — horizon-dependent ones go through _project."""
    if not hist:
        return None
    vals = [h['value'] for h in hist]
    if kind == 'naive':
        return vals[-1]
    if kind == 'mean':
        return sum(vals) / len(vals)
    if kind == 'ewma':
        acc = w = 0.0
        for h in hist:
            k = 0.5 ** ((now - h['t']) / (6 * 3600))
            acc += k * h['value']; w += k
        return acc / w if w else vals[-1]
    if kind == 'drift':
        return vals[-1]
    return None


def _project(kind, hist, at):
    """drift extends the 24h trend line to the target; others are flat."""
    if kind != 'drift' or len(hist) < 3:
        return None
    ts = [h['t'] for h in hist]
    vs = [h['value'] for h in hist]
    mt, mv = sum(ts) / len(ts), sum(vs) / len(vs)
    var = sum((t - mt) ** 2 for t in ts)
    if not var:
        return None
    slope = sum((t - mt) * (v - mv) for t, v in zip(ts, vs)) / var
    got = mv + slope * (at - mt)
    # a trend through noise can run anywhere; never call past half or double
    return min(max(got, vs[-1] / 2), vs[-1] * 2)
