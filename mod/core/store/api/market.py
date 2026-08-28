"""market — the store's marketplace layer.

Listings are metadata over objects that already live in the store: a seller
lists a CID with a title/description/tags and a price. Pricing reuses the
on-chain BlocTime gate rather than inventing a payment rail:

    price_bloc == 0   free — anyone signed in can grab it
    price_bloc  > 0   the buyer must HOLD >= price_bloc BlocTime on-chain

"Buying" a private object mints a permanent read grant (seller -> buyer)
through the existing Access layer, so acquired items show up under /shared
and every read path (get/preview/tickets/QR) works unchanged. No custody,
no escrow: BlocTime holdings are the ticket, the chain is the source of
truth, and the store only checks balances.

State is one JSON file next to the other off-chain access state:
    ~/.mod/store/market.json
        {"listings": {cid: {...}}, "acquired": {addr: {cid: ts}}}

Listings are public by definition (it's a marketplace), but the underlying
object keeps its own visibility: a private listing stays unreadable until
acquired — that's the product.
"""
import json
import math
import threading
import time
from pathlib import Path
from typing import Optional

MAX_TAGS = 8
MAX_TITLE = 120
MAX_DESC = 1000


def _now() -> int:
    return int(time.time())


def _clean_tags(tags) -> list:
    if isinstance(tags, str):
        tags = tags.split(',')
    if not isinstance(tags, list):
        return []
    out = []
    for t in tags:
        t = str(t).strip().lower().lstrip('#')[:24]
        if t and t not in out:
            out.append(t)
    return out[:MAX_TAGS]


class Market:
    """Listings + likes + acquisitions, persisted as a single JSON blob."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._state = self._read()

    # ── persistence ──
    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
            if isinstance(data, dict):
                data.setdefault('listings', {})
                data.setdefault('acquired', {})
                return data
        except Exception:
            pass
        return {'listings': {}, 'acquired': {}}

    def _write(self) -> None:
        self.path.write_text(json.dumps(self._state, indent=2))

    # ── listings ──
    def upsert(self, cid: str, seller: str, title: str, description: str = '',
               tags=None, price_bloc: float = 0.0) -> dict:
        seller = seller.lower()
        title = (title or '').strip()[:MAX_TITLE]
        if not title:
            raise ValueError('title required')
        price = max(0.0, float(price_bloc or 0))
        with self._lock:
            prev = self._state['listings'].get(cid)
            row = {
                'cid': cid,
                'seller': seller,
                'title': title,
                'description': (description or '').strip()[:MAX_DESC],
                'tags': _clean_tags(tags),
                'price_bloc': price,
                'created': prev['created'] if prev else _now(),
                'updated': _now(),
                'downloads': prev['downloads'] if prev else 0,
                'likes': prev['likes'] if prev else [],
            }
            self._state['listings'][cid] = row
            self._write()
        return self.view(row)

    def delist(self, cid: str) -> bool:
        with self._lock:
            gone = self._state['listings'].pop(cid, None)
            if gone:
                self._write()
        return gone is not None

    def get(self, cid: str) -> Optional[dict]:
        row = self._state['listings'].get(cid)
        return self.view(row) if row else None

    # ── social ──
    def toggle_like(self, cid: str, addr: str) -> Optional[dict]:
        addr = addr.lower()
        with self._lock:
            row = self._state['listings'].get(cid)
            if not row:
                return None
            if addr in row['likes']:
                row['likes'].remove(addr)
                liked = False
            else:
                row['likes'].append(addr)
                liked = True
            self._write()
        return {'cid': cid, 'liked': liked, 'likes': len(row['likes'])}

    def record_acquire(self, cid: str, addr: str) -> None:
        addr = addr.lower()
        with self._lock:
            row = self._state['listings'].get(cid)
            if not row:
                return
            mine = self._state['acquired'].setdefault(addr, {})
            if cid not in mine:            # downloads counts unique acquirers
                row['downloads'] += 1
            mine[cid] = _now()
            self._write()

    def acquired_by(self, addr: str) -> dict:
        return dict(self._state['acquired'].get(addr.lower(), {}))

    def liked(self, cid: str, addr: str) -> bool:
        row = self._state['listings'].get(cid)
        return bool(row) and addr.lower() in row['likes']

    # ── browse ──
    @staticmethod
    def view(row: dict) -> dict:
        out = dict(row)
        out['likes'] = len(row['likes'])
        return out

    def _hot_score(self, row: dict) -> float:
        """Likes weigh 3x a download; freshness decays with a 7-day half-life
        so new drops surface without burying proven ones."""
        age_days = max(0.0, (_now() - row['created']) / 86400)
        return (3 * len(row['likes']) + row['downloads'] + 1) * math.pow(0.5, age_days / 7)

    def browse(self, q: str = '', tag: str = '', seller: str = '',
               sort: str = 'hot', free_only: bool = False, limit: int = 100) -> list:
        ql = (q or '').strip().lower()
        tag = (tag or '').strip().lower()
        seller = (seller or '').strip().lower()
        rows = []
        for row in self._state['listings'].values():
            if seller and row['seller'] != seller:
                continue
            if tag and tag not in row['tags']:
                continue
            if free_only and row['price_bloc'] > 0:
                continue
            if ql and ql not in row['title'].lower() \
                    and ql not in row['description'].lower() \
                    and ql not in row['cid'].lower() \
                    and not any(ql in t for t in row['tags']):
                continue
            rows.append(row)
        if sort == 'new':
            rows.sort(key=lambda r: r['created'], reverse=True)
        elif sort == 'top':
            rows.sort(key=lambda r: (len(r['likes']), r['downloads']), reverse=True)
        else:  # hot
            rows.sort(key=self._hot_score, reverse=True)
        return [self.view(r) for r in rows[:max(1, int(limit))]]

    def tag_counts(self) -> dict:
        counts: dict = {}
        for row in self._state['listings'].values():
            for t in row['tags']:
                counts[t] = counts.get(t, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True))

    def listings_by(self, seller: str) -> list:
        return self.browse(seller=seller, sort='new', limit=10000)
