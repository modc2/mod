"""Pure-logic tests for the crime / news / listing-market engines."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from nycgis import crime as C
from nycgis import news as N
from nycgis import realestate as RE


# ── crime ────────────────────────────────────────────────────────────────

def test_tally_sums_levels_and_total():
    rows = [
        {'addr_pct_cd': '1', 'law_cat_cd': 'FELONY', 'n': '10'},
        {'addr_pct_cd': '1', 'law_cat_cd': 'MISDEMEANOR', 'n': '5'},
        {'addr_pct_cd': '1', 'law_cat_cd': '(null)', 'n': '2'},   # unknown level
        {'addr_pct_cd': '', 'law_cat_cd': 'FELONY', 'n': '9'},    # blank key dropped
    ]
    out = C._tally(rows, 'addr_pct_cd')
    assert out['1'] == {'felony': 10, 'misdemeanor': 5, 'violation': 0, 'total': 17}
    assert '' not in out


def test_change_handles_zero_and_none():
    assert C._change(110, 100) == 10.0
    assert C._change(90, 100) == -10.0
    assert C._change(5, 0) is None
    assert C._change(5, None) is None


def test_precinct_borough_bands():
    assert C._precinct_borough(1) == 'Manhattan'
    assert C._precinct_borough(40) == 'Bronx'
    assert C._precinct_borough(79) == 'Brooklyn'
    assert C._precinct_borough(114) == 'Queens'
    assert C._precinct_borough(120) == 'Staten Island'


# ── news ─────────────────────────────────────────────────────────────────

RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Rents hit a record in Queens</title>
<link>https://x.test/a</link>
<pubDate>Tue, 07 Oct 2026 12:00:00 -0400</pubDate>
<description>Median &lt;b&gt;asking&lt;/b&gt; rent rose.</description></item>
<item><title>Subway delays on the 7 line</title>
<link>https://x.test/b</link><pubDate>Tue, 07 Oct 2026 11:00:00 -0400</pubDate>
<description></description></item>
<item><title>No link, dropped</title></item>
</channel></rss>"""


def test_parse_rss_items_topics_and_cleanup():
    items = N._parse_rss(RSS, 'Test')
    assert len(items) == 2
    assert items[0]['title'] == 'Rents hit a record in Queens'
    assert items[0]['topic'] == 'housing'
    assert items[0]['summary'] == 'Median asking rent rose.'   # tags stripped
    assert items[0]['published'] == '2026-10-07T16:00'          # UTC
    assert items[1]['topic'] == 'transit'


def test_parse_rss_garbage_is_empty_not_fatal():
    assert N._parse_rss('this is not xml', 'X') == []


def test_dedupe_is_title_insensitive():
    items = [{'title': 'Big Story!'}, {'title': 'big story'}, {'title': 'Other'}]
    assert len(N._dedupe(items)) == 2


# ── the listing market ───────────────────────────────────────────────────

def test_latest_walks_back_past_nulls_and_finds_year_ago():
    months = [f'2025-{m:02d}' for m in range(1, 13)] + ['2026-01', '2026-02']
    vals = [100.0 + i for i in range(12)] + [200.0, None]
    m, v, ago = RE._latest(months, vals)
    assert (m, v) == ('2026-01', 200.0)        # trailing null skipped
    assert ago == 100.0                        # 12 months before 2026-01


def test_latest_all_null():
    assert RE._latest(['2026-01'], [None]) == (None, None, None)


def test_yoy():
    assert RE._yoy(110.0, 100.0) == 10.0
    assert RE._yoy(None, 100.0) is None
    assert RE._yoy(100.0, None) is None
    assert RE._yoy(100.0, 0.0) is None
