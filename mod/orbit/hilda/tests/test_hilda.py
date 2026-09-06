"""
Tests for hilda.

Split in two. Most of these run against synthetic rasters we build ourselves,
so they exercise the reader, the aggregation and the automaton without a
network or an ingested cube. The handful that need real data are marked
``needs_cube`` and skip cleanly when there is none.

The synthetic GeoTIFF writer at the top is the interesting part: it produces a
file with the same shape as HILDA+ (one LZW strip per row, no predictor),
which is what makes the windowed-read tests meaningful.
"""

import io
import struct
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

MODULE_DIR = Path(__file__).resolve().parent.parent
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

from hildaplus import automata as A          # noqa: E402
from hildaplus import cube as C              # noqa: E402
from hildaplus import raster as R            # noqa: E402
from hildaplus import remote                 # noqa: E402
from hildaplus import render as D            # noqa: E402
from hildaplus import series as T            # noqa: E402
from hildaplus import sources as S           # noqa: E402

Image.MAX_IMAGE_PIXELS = None

needs_cube = pytest.mark.skipif(
    not C.load('states', quiet=True).get('ready'),
    reason='no states cube — run `m hilda/ingest` first')
needs_transitions = pytest.mark.skipif(
    not C.load('transitions', quiet=True).get('ready'),
    reason='no transitions cube — run `m hilda/ingest kind=transitions`')


# ── a HILDA+-shaped raster we can make locally ───────────────────────────

def write_striped_tiff(path, array):
    """Write a one-row-per-strip LZW TIFF, the layout HILDA+ ships."""
    im = Image.fromarray(array, 'L')
    im.save(path, 'TIFF', compression='tiff_lzw', strip_size=array.shape[1])
    with R.Raster(path) as r:
        assert r.rows_per_strip == 1, 'test fixture must be one row per strip'
    return path


@pytest.fixture(scope='module')
def small_tif(tmp_path_factory):
    rng = np.random.default_rng(7)
    codes = np.array([0, 11, 22, 33, 44, 55, 66, 77, 99], dtype=np.uint8)
    arr = codes[rng.integers(0, len(codes), size=(400, 600))]
    arr[:20] = 0                      # a band of ocean to check the mask
    path = tmp_path_factory.mktemp('tif') / 'states.tif'
    write_striped_tiff(path, arr)
    return path, arr


# ── raster reading ───────────────────────────────────────────────────────

class TestRaster:
    def test_header(self, small_tif):
        path, arr = small_tif
        with R.Raster(path) as r:
            assert (r.height, r.width) == arr.shape
            assert r.predictor == 1
            assert len(r.strip_offsets) == arr.shape[0]

    def test_windowed_read_is_exact(self, small_tif):
        path, arr = small_tif
        with R.Raster(path) as r:
            assert np.array_equal(r.rows(10, 40), arr[10:40])
            assert np.array_equal(r.rows(0, 3), arr[0:3])
            assert np.array_equal(r.rows(arr.shape[0] - 2, arr.shape[0]),
                                  arr[-2:])

    def test_single_row_read(self, small_tif):
        """The one-row case needs its own wrapper trick; check every edge."""
        path, arr = small_tif
        with R.Raster(path) as r:
            for row in (0, 1, 137, arr.shape[0] - 1):
                got = r.rows(row, row + 1)
                assert got.shape == (1, arr.shape[1])
                assert np.array_equal(got[0], arr[row])

    def test_rows_tolerant_on_clean_file(self, small_tif):
        path, arr = small_tif
        with R.Raster(path) as r:
            block, bad = r.rows_tolerant(5, 25)
            assert bad == []
            assert np.array_equal(block, arr[5:25])

    def test_rows_tolerant_survives_a_corrupt_strip(self, small_tif, tmp_path):
        """A damaged strip should cost that row, not the whole year."""
        path, arr = small_tif
        blob = bytearray(Path(path).read_bytes())
        with R.Raster(path) as r:
            off, n = r.strip_offsets[12], r.strip_bytes[12]
        blob[off:off + n] = b'\xff' * n            # shred one strip
        broken = tmp_path / 'broken.tif'
        broken.write_bytes(bytes(blob))
        with R.Raster(broken) as r:
            block, bad = r.rows_tolerant(10, 20)
            assert 12 in bad
            assert np.array_equal(block[0], arr[10])     # neighbours intact
            assert np.array_equal(block[9], arr[19])
            assert not block[12 - 10].any()              # damaged row zeroed

    def test_out_of_range(self, small_tif):
        path, _ = small_tif
        with R.Raster(path) as r:
            with pytest.raises(ValueError):
                r.rows(0, r.height + 1)


# ── aggregation and geometry ─────────────────────────────────────────────

class TestGrid:
    def test_block_size_must_divide(self):
        assert R.block_size(0.5) == 50
        assert R.grid_shape(0.5) == (360, 720)
        assert R.grid_shape(0.25) == (720, 1440)
        with pytest.raises(ValueError):
            R.block_size(0.7)          # 70 source pixels does not divide 18000
        with pytest.raises(ValueError):
            R.block_size(0.015)        # not a whole number of source pixels

    def test_cell_area_matches_the_sphere(self):
        """Summed cell areas must equal the surface of the earth."""
        area = R.cell_area_km2(0.5)
        total = float((area * 720).sum())
        exact = 4 * np.pi * S.EARTH_RADIUS_KM ** 2
        assert abs(total - exact) / exact < 1e-9

    def test_cells_shrink_toward_the_poles(self):
        area = R.cell_area_km2(0.5)
        assert area[180] > area[0] * 50        # equator vs polar row
        assert np.all(np.diff(area[:180]) > 0)

    def test_bbox_slice_and_cell_bounds_round_trip(self):
        rs, cs = R.bbox_slice([-60, -10, -50, 0], 0.5)
        assert (rs.start, rs.stop) == (180, 200)
        assert (cs.start, cs.stop) == (240, 260)
        assert R.cell_bounds(180, 240, 0.5) == [-60.0, -0.5, -59.5, 0.0]

    def test_lonlat_to_cell_clamps(self):
        assert R.lonlat_to_cell(-180, 90, 0.5) == (0, 0)
        assert R.lonlat_to_cell(180, -90, 0.5) == (359, 719)


class TestReduce:
    def test_fractions_sum_and_scale(self, small_tif, monkeypatch):
        """A reduced grid must reproduce the source class histogram."""
        path, arr = small_tif
        monkeypatch.setattr(S, 'SRC_H', arr.shape[0])
        monkeypatch.setattr(S, 'SRC_W', arr.shape[1])
        frac = R.reduce_states(path, deg=0.2)     # 20x20 blocks of 0.01 deg
        assert frac.shape[0] == S.N_PLANES
        for i, code in enumerate(S.PLANES):
            share = (arr == code).mean()
            assert abs(frac[i].mean() / 255.0 - share) < 0.01

    def test_transition_matrix_is_area_weighted(self, tmp_path, monkeypatch):
        """Equal pixel counts at different latitudes must not weigh equally."""
        arr = np.full((200, 200), 44, dtype=np.uint8)
        arr[:100] = 43            # forest -> pasture in the northern half
        arr[100:] = 44            # forest stays forest in the southern half
        path = write_striped_tiff(tmp_path / 't.tif', arr)
        monkeypatch.setattr(S, 'SRC_H', 200)
        monkeypatch.setattr(S, 'SRC_W', 200)
        matrix, changed = R.reduce_transitions(path, deg=0.1)
        f, p = S.KEY_INDEX['forest'], S.KEY_INDEX['pasture']
        assert matrix[f, p] > 0
        assert matrix[f, f] > 0
        # Northern rows are nearer the pole here, so their km2 must be smaller
        # than the equal pixel count in the south.
        assert matrix[f, p] < matrix[f, f]
        assert changed[:changed.shape[0] // 2].max() == 255
        assert changed[changed.shape[0] // 2:].max() == 0


# ── the archive reader ───────────────────────────────────────────────────

class TestRemote:
    def test_member_templates_are_wellformed(self):
        name = S.MEMBER['states'].format(year=1985)
        assert name.endswith('hilda_plus_1985_states_GLOB-v1-0_wgs84-nn.tif')
        name = S.MEMBER['transitions'].format(year=1985, prev=1984)
        assert '1985-1984_transitions' in name

    def test_year_bounds_are_enforced(self):
        with pytest.raises(ValueError):
            remote.member_name(1850, 'states')
        with pytest.raises(ValueError):
            remote.member_name(1960, 'transitions')    # transitions start 1961
        with pytest.raises(ValueError):
            remote.member_name(1990, 'nonsense')

    def test_central_directory_parser(self):
        """Round-trip a real ZIP through the parser we use on the archive."""
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('dir/', b'')
            z.writestr('dir/a.tif', b'x' * 5000)
            z.writestr('dir/b.tif', b'y' * 900)
        raw = buf.getvalue()
        end = raw.rfind(b'PK\x05\x06')
        size = struct.unpack('<I', raw[end + 12:end + 16])[0]
        off = struct.unpack('<I', raw[end + 16:end + 20])[0]
        members = remote._parse_central_directory(raw[off:off + size])
        assert set(members) == {'dir/a.tif', 'dir/b.tif'}   # no directories
        assert members['dir/a.tif']['usize'] == 5000


# ── classes, regions, rendering ──────────────────────────────────────────

class TestCatalogue:
    def test_class_resolution(self):
        assert S.resolve_class('forest') == S.KEY_INDEX['forest']
        assert S.resolve_class(44) == S.KEY_INDEX['forest']
        assert S.resolve_class('FOR') == S.KEY_INDEX['forest']
        assert S.resolve_class(3) == 3
        with pytest.raises(ValueError):
            S.resolve_class('unobtainium')

    def test_bbox_resolution(self):
        assert S.resolve_bbox('amazon') == S.REGIONS['amazon']['bbox']
        assert S.resolve_bbox(None, '-10,-5,10,5') == [-10, -5, 10, 5]
        assert S.resolve_bbox() == [-180, -90, 180, 90]
        with pytest.raises(ValueError):
            S.resolve_bbox('atlantis')
        with pytest.raises(ValueError):
            S.resolve_bbox(None, '1,2,3')

    def test_palette_is_complete_and_distinct(self):
        pal = D.palette()
        colours = [c['color'] for c in pal]
        assert len(colours) == len(set(colours)), 'two classes share a colour'
        assert {c['index'] for c in pal} >= set(range(S.N_CLASSES))


class TestClassify:
    def test_dominant_class_wins(self):
        frame = np.zeros((S.N_PLANES, 2, 2), dtype=np.uint8)
        frame[S.KEY_INDEX['forest'], 0, 0] = 200
        frame[S.KEY_INDEX['cropland'], 0, 0] = 55
        frame[S.KEY_INDEX['cropland'], 0, 1] = 90
        out = D.classify(frame)
        assert out[0, 0] == S.KEY_INDEX['forest']
        assert out[0, 1] == S.KEY_INDEX['cropland']
        assert out[1, 1] == D.OCEAN            # nothing there at all

    def test_land_beats_water_unless_water_dominates(self):
        """Coastal cells are part sea; they should still read as their land."""
        frame = np.zeros((S.N_PLANES, 1, 2), dtype=np.uint8)
        frame[S.KEY_INDEX['forest'], 0, 0] = 60
        frame[S.N_CLASSES, 0, 0] = 40           # water minority
        frame[S.KEY_INDEX['forest'], 0, 1] = 30
        frame[S.N_CLASSES, 0, 1] = 200          # water majority
        out = D.classify(frame)
        assert out[0, 0] == S.KEY_INDEX['forest']
        assert out[0, 1] == D.WATER

    def test_pack_grid_round_trip(self):
        grids = np.arange(2 * 3 * 4, dtype=np.uint8).reshape(2, 3, 4)
        blob = D.pack_grid([1999, 2000], grids, 0.5)
        assert blob[:4] == b'HILD'
        n, h, w = struct.unpack('<HHH', blob[6:12])
        assert (n, h, w) == (2, 3, 4)
        years = struct.unpack('<hh', blob[16:20])
        assert [y + 1900 for y in years] == [1999, 2000]
        assert np.array_equal(
            np.frombuffer(blob[20:], dtype=np.uint8).reshape(2, 3, 4), grids)


# ── the automaton, on a synthetic world ──────────────────────────────────

def toy_state(h=24, w=48):
    """Half forest, one block of cropland, the rest ocean."""
    s = np.zeros((S.N_CLASSES, h, w), dtype=np.float32)
    s[S.KEY_INDEX['forest'], 4:20, 4:40] = 1.0
    s[S.KEY_INDEX['cropland'], 8:12, 8:12] = 1.0
    s[S.KEY_INDEX['forest'], 8:12, 8:12] = 0.0
    return s


class TestAutomaton:
    def test_moore_mean_wraps_longitude_only(self):
        a = np.zeros((3, 4), dtype=np.float32)
        a[1, 0] = 9.0
        m = A.moore_mean(a)
        assert m[1, -1] > 0, 'east edge should see the west edge'
        assert m[1, 1] > 0
        b = np.zeros((3, 4), dtype=np.float32)
        b[0, 1] = 9.0
        assert A.moore_mean(b)[-1, 1] == 0, 'north must not wrap to south'

    def test_land_is_conserved(self):
        s = toy_state()
        rate = np.full((S.N_CLASSES, S.N_CLASSES), 0.01, dtype=np.float32)
        ca = A.Automaton(s, rate, weight=0.5)
        before = s.sum(axis=0).copy()
        for _ in range(10):
            ca.step()
        assert np.allclose(ca.state.sum(axis=0), before, atol=1e-4)
        assert ca.state.min() >= 0.0

    def test_ocean_stays_empty(self):
        ca = A.Automaton(toy_state(), np.full((6, 6), 0.05, np.float32), 1.0)
        for _ in range(5):
            ca.step()
        assert ca.state[:, 0, 0].sum() == 0.0

    def test_demand_matches_the_rate_matrix(self):
        """The observed rate sets how much moves; suitability only says where."""
        s = toy_state()
        rate = np.zeros((S.N_CLASSES, S.N_CLASSES), dtype=np.float32)
        f, c = S.KEY_INDEX['forest'], S.KEY_INDEX['cropland']
        rate[f, c] = 0.02
        stock = s[f].sum()
        ca = A.Automaton(s, rate, weight=0.5)
        ca.step()
        moved = stock - ca.state[f].sum()
        assert abs(moved - 0.02 * stock) / (0.02 * stock) < 0.02

    def test_neighbourhood_weight_concentrates_change(self):
        """With w=1 conversion should hug the existing cropland block."""
        s = toy_state()
        rate = np.zeros((S.N_CLASSES, S.N_CLASSES), dtype=np.float32)
        rate[S.KEY_INDEX['forest'], S.KEY_INDEX['cropland']] = 0.05
        near = (slice(6, 14), slice(6, 14))
        gains = {}
        for w in (0.0, 1.0):
            ca = A.Automaton(s.copy(), rate, weight=w)
            for _ in range(5):
                ca.step()
            crop = ca.state[S.KEY_INDEX['cropland']]
            gains[w] = crop[near].sum() / crop.sum()
        assert gains[1.0] > gains[0.0]

    def test_protection_freezes_a_box(self):
        s = toy_state()
        rate = np.full((S.N_CLASSES, S.N_CLASSES), 0.05, dtype=np.float32)
        mask = np.zeros(s.shape[1:], dtype=bool)
        mask[4:10, 4:10] = True
        ca = A.Automaton(s, rate, weight=0.5, protect=mask)
        before = s[:, mask].copy()
        for _ in range(5):
            ca.step()
        assert np.allclose(ca.state[:, mask], before, atol=1e-6)
        assert not np.allclose(ca.state[:, ~mask], s[:, ~mask], atol=1e-6)

    def test_scenario_multiplier_biases_a_class(self):
        s = toy_state()
        rate = np.zeros((S.N_CLASSES, S.N_CLASSES), dtype=np.float32)
        rate[S.KEY_INDEX['forest'], S.KEY_INDEX['urban']] = 0.01
        base = A.Automaton(s.copy(), rate, 0.5)
        hot = A.Automaton(s.copy(), rate, 0.5, scenario={'urban': 3.0})
        for _ in range(5):
            base.step(); hot.step()
        u = S.KEY_INDEX['urban']
        assert hot.state[u].sum() > base.state[u].sum() * 2

    def test_scenario_parsing(self):
        assert A._parse_scenario('urban=2,forest=0.5') == {'urban': 2.0, 'forest': 0.5}
        assert A._parse_scenario('{"urban": 2}') == {'urban': 2.0}
        assert A._parse_scenario(None) == {}

    def test_score_rewards_getting_closer(self):
        start = toy_state()
        obs = start.copy()
        obs[S.KEY_INDEX['forest'], 5, 5] = 0.0
        obs[S.KEY_INDEX['cropland'], 5, 5] = 1.0
        perfect = A.score(obs, obs, start, deg=0.5)
        assert perfect['skill'] == pytest.approx(1.0)
        useless = A.score(start, obs, start, deg=0.5)
        assert useless['skill'] == pytest.approx(0.0)


# ── against the real cube ────────────────────────────────────────────────

@needs_cube
class TestRealData:
    def test_cube_covers_the_record(self):
        doc = C.require('states')
        assert doc['years'][0] <= 1960 and doc['years'][-1] >= 2019
        assert doc['data'].shape[1:] == (S.N_PLANES, 360, 720)

    def test_global_areas_are_physically_plausible(self):
        a = T.areas(2019)
        land = a['land_km2']
        assert 125e6 < land < 150e6, f'global land {land/1e6:.1f} M km2'
        assert 35e6 < a['km2']['forest'] < 48e6
        assert 12e6 < a['km2']['cropland'] < 20e6
        assert 1e6 < a['km2']['urban'] < 3e6

    def test_land_area_is_stable_over_time(self):
        s = T.series()
        land = np.array(s['land_km2'])
        assert land.std() / land.mean() < 0.01

    def test_urban_only_grows(self):
        """Cities are not observed to un-build in this record."""
        s = T.series()
        urban = np.array(s['km2']['urban'])
        assert urban[-1] > urban[0] * 1.3
        assert (np.diff(urban) >= -urban[:-1] * 0.01).all()

    def test_amazon_lost_forest_to_pasture(self):
        n = T.net_change(1960, 2019, region='amazon')
        assert n['km2']['forest'] < 0
        assert n['km2']['pasture'] > 0

    def test_region_areas_sum_under_the_globe(self):
        g = T.areas(2019)['land_km2']
        for key in ('africa', 'europe', 'amazon'):
            assert T.areas(2019, region=key)['land_km2'] < g

    def test_cell_history_has_every_year(self):
        c = T.cell(-60, -3)
        assert len(c['years']) == len(C.require('states')['years'])
        assert len(c['fraction']['forest']) == len(c['years'])

    def test_grid_payload_round_trips(self):
        years, grids = D.dominant_cube()
        blob = D.pack_grid(years, grids, 0.5)
        assert blob[:4] == b'HILD'
        n, h, w = struct.unpack('<HHH', blob[6:12])
        assert (n, h, w) == (len(years), 360, 720)
        assert len(D.gzipped(blob)) < len(blob) / 3


@needs_cube
@needs_transitions
class TestRealModel:
    def test_rates_are_a_probability_matrix(self):
        m, source = A.rates()
        assert 'transition layers' in source
        assert np.allclose(m.sum(axis=1), 1.0, atol=1e-4)
        assert (np.diag(m) > 0.98).all(), 'most land does not change in a year'
        assert (m >= 0).all()

    def test_gross_change_matches_the_published_figure(self):
        """End-to-end check against the paper.

        Winkler et al. 2021 report that 1960-2019 land use change affected
        about 43 M km2 — 32% of the global land surface. Our number comes from
        a completely independent path: range-read the archive, decode the
        transition rasters, aggregate in km2 with latitude weighting. Landing
        on the same total means the whole pipeline is sound.
        """
        t = T.transitions()
        assert 40e6 < t['gross_km2'] < 46e6, f'{t["gross_km2"]/1e6:.1f} M km2'
        land = T.areas(2019)['land_km2']
        assert 0.28 < t['gross_km2'] / land < 0.36

    def test_gross_dwarfs_net(self):
        """Gross change is many times net, and the two routes to net agree."""
        t = T.transitions()
        assert t['gross_over_net'] > 4.0
        n = T.net_change(1960, 2019)['km2']
        from_states = sum(abs(v) for k, v in n.items() if k != 'water') / 2
        assert abs(from_states - t['net_km2']) / t['net_km2'] < 0.15

    def test_susceptibility_is_normalised_and_bounded(self):
        s = A.susceptibility(1961, 1990)
        assert s.shape == (360, 720)
        assert s.min() >= 0.05 and s.max() <= 25.0
        assert 0.2 < float(s[s > 0.05].mean()) < 20

    def test_run_conserves_land_and_tracks_totals(self):
        r = A.run(1990, 2019, weight=0.6, keep_frames=True)
        frames = r['_frames']
        land = [float(f.sum()) for f in frames.values()]
        assert max(land) - min(land) < max(land) * 1e-3
        assert r['trained_on'][1] <= 1990, 'training must precede the run'
        assert r['out_of_sample'] is True

    def test_reproduces_the_global_trajectory_of_stable_classes(self):
        """Urban and cropland trend steadily, and the model should track them.

        Pasture and grassland reverse sign between the halves of the record,
        so a rate matrix fitted before 1990 cannot predict them and is not
        asked to here — see test_the_record_is_not_stationary.
        """
        r = A.run(1990, 2019, keep_frames=False)
        per = r['skill']['per_class']
        assert per['urban']['area_skill'] > 0.7
        assert per['cropland']['area_skill'] > 0.7

    def test_the_record_is_not_stationary(self):
        """Why the aggregate area skill is negative — a property of the data.

        If this ever fails, HILDA+ changed, and the model's scorecard should
        be re-read before anything in the model is.
        """
        early = T.net_change(1960, 1990)['km2']
        late = T.net_change(1990, 2019)['km2']
        assert early['pasture'] > 0 > late['pasture']
        assert early['grassland'] < 0 < late['grassland']

    def test_allocation_is_scored_honestly(self):
        """The cell-by-cell score must be reported even though it is bad.

        The automaton does not beat persistence on placement at 0.5 degrees.
        That is the finding; this test exists so a future change cannot quietly
        drop the number that says so.
        """
        r = A.run(1990, 2019, keep_frames=False)
        s = r['skill']
        assert 'allocation_skill' in s and 'area_skill' in s
        assert s['allocation_skill'] == pytest.approx(s['skill'])
        assert -1.0 < s['allocation_skill'] < 1.0

    def test_demand_allocation_pins_global_totals(self):
        """Given the matching rate window the totals must come out right.

        This is the machinery check: with rates fitted on exactly the period
        being replayed, simulated net change should match observed to within a
        few percent on every class. It caught the two bugs that mattered — a
        contagion term that let forest feed itself, and a demand budget
        computed in cell fractions instead of km2.
        """
        r = A.run(1960, 1990, calibrate_on=[1961, 1990], keep_frames=True)
        start = C.fractions(1960)[:S.N_CLASSES]
        sim = r['_frames'][1990]
        area = R.cell_area_km2()[:, None]
        obs = T.net_change(1960, 1990)['km2']
        for i, c in enumerate(S.CLASSES):
            got = float(((sim[i] - start[i]) * area).sum())
            assert abs(got - obs[c['key']]) < 0.05e6, c['key']

    def test_projection_past_the_record_is_labelled(self):
        r = A.run(2019, 2040, keep_frames=False)
        assert r['projection'] is True
        assert 'skill' not in r

    def test_scenario_changes_the_outcome(self):
        base = A.run(2000, 2019, keep_frames=False)
        hot = A.run(2000, 2019, scenario='urban=3', keep_frames=False)
        assert hot['km2']['urban'][-1] > base['km2']['urban'][-1]


@needs_cube
class TestKnownDataDefects:
    """The archive has two documented flaws; both must stay handled."""

    def test_2015_base_map_is_excluded_from_ranges(self):
        assert 2015 in S.EXCLUDED_STATE_YEARS
        assert 2015 not in C.parse_years('1960-2019')
        assert C.parse_years('2015') == [2015], 'naming it should still work'
        assert 2015 not in C.require('states')['index']

    def test_land_area_has_no_spikes(self):
        """What excluding the base map protects: 12.4 M km2 of phantom land."""
        land = np.array(T.series()['land_km2'])
        assert land.std() / land.mean() < 1e-4
        assert land.max() - land.min() < 1e6
