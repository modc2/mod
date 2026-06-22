"""
ModCity — Modular Housing & Cities Protocol.

Prefab buildings assembled like LEGO. A library of standardized prefab
*modules* (3×3×3 m bricks: studios, kitchens, baths, atriums, solar roofs,
garden decks, plus NYC-brownstone parts — stoops, parlor floors, bay
windows, cornices) snap onto a grid and stack into floors. Anyone can also
forge their *own* bricks and (optionally) share them. Pick an *architecture
style* (Bauhaus, Brutalist, Japandi, Neo-Tokyo, NYC Brownstone…) and the
same bricks become a completely different building. Compose buildings into
*cities*.

Everything is deterministic and computable: a design is a set of placed
bricks + a style + constraints, so price, floor area, embodied carbon, lead
time, floor count and *constraint compliance* fall straight out of the
catalog. Designs are PRIVATE BY DEFAULT — publish to share, and anyone can
copy-and-remix what's public.

Flow:
  1. Browse / forge bricks            — catalog(), create_brick(...)
  2. Browse architecture styles        — styles()
  3. Set parameters & constraints      — estimate(..., constraints={...})
  4. Save a design (private)           — save_design(...)
  5. Publish / copy-remix / city       — publish_design / copy_design / save_city

Storage: ~/.modcity/{designs.json, cities.json, bricks.json}
Built-in catalog + styles are seeded in-code so the protocol is
self-describing and reproducible on any node.
"""

import json
import os
import time
import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Any

import mod as m


# ━━ Prefab spec ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
UNIT_M = 3.0
UNIT_AREA_M2 = UNIT_M * UNIT_M

CATALOG: List[Dict[str, Any]] = [
    # id          name              category    tone        price   carbon  lead  glass  blurb
    ("studio",    "Studio Cube",    "living",    "warm",     18000,  2100,   21,  False, "A complete micro-home: bed, nook, storage. The atom of ModCity."),
    ("bedroom",   "Bedroom Bay",    "living",    "warm",     16000,  1900,   21,  False, "Quiet sleeping module with a full-height window."),
    ("living",    "Living Hall",    "living",    "warm",     20000,  2300,   24,  False, "Open lounge brick — the social heart of any stack."),
    ("kitchen",   "Galley Kitchen", "service",   "steel",    22000,  2600,   28,  False, "Plug-and-play kitchen: pre-plumbed, appliances pre-fit."),
    ("bath",      "Wet Core",       "service",   "steel",    14000,  1700,   18,  False, "Bathroom + utility riser. Snap it anywhere; pipes self-align."),
    ("office",    "Work Pod",       "work",      "neutral",  17000,  1800,   21,  False, "Acoustically isolated home-office or maker space."),
    ("atrium",    "Glass Atrium",   "light",     "glass",    26000,  2000,   30,  True,  "Double-height glazed void that floods the stack with light."),
    ("stair",     "Stair Core",     "structure", "concrete", 12000,  1500,   14,  False, "Vertical circulation + structural spine. Stacks love a spine."),
    ("solar",     "Solar Roof",     "roof",      "accent",   15000,   900,   16,  False, "Caps a stack: PV roof + rainwater catch. Net-positive energy."),
    ("garden",    "Garden Deck",    "outdoor",   "green",     9000,   400,   12,  False, "Open-air terrace / green roof. Plant it, live on it."),
    ("mezz",      "Mezzanine",      "living",    "warm",     19000,  2150,   24,  False, "Split-level loft insert — doubles usable area in a tall bay."),
    ("retail",    "Ground Retail",  "commerce",  "neutral",  21000,  2400,   26,  True,  "Shopfront / café brick for the street level of a block."),
    # ── NYC brownstone parts ──
    ("stoop",     "Stoop & Entry",  "structure", "stone",    13000,  1400,   14,  False, "The classic NYC stoop: raised entry over a garden level."),
    ("parlor",    "Parlor Floor",   "living",    "warm",     23000,  2400,   26,  False, "High-ceiling parlor with floor-to-ceiling brownstone windows."),
    ("bay",       "Bay Window",     "living",    "warm",     21000,  2200,   24,  False, "Projecting three-sided bay — the brownstone's signature face."),
    ("cornice",   "Cornice Roof",   "roof",      "stone",    16000,  1100,   18,  False, "Ornamented sheet-metal cornice. Crowns the row in true NY style."),
]

STYLES: List[Dict[str, Any]] = [
    {"id": "bauhaus", "name": "Bauhaus", "material": "matte",
     "vibe": "Primary colours, honest geometry, form follows function.",
     "accent": "#e63946", "sky": "#f1faee", "price_mult": 1.00, "carbon_mult": 1.00,
     "palette": {"warm": "#f4a261", "service": "#457b9d", "steel": "#457b9d", "neutral": "#e9ecef",
                 "glass": "#a8dadc", "concrete": "#adb5bd", "accent": "#e63946", "green": "#80b918",
                 "work": "#ffd166", "stone": "#cbb799"}},
    {"id": "brutalist", "name": "Brutalist", "material": "concrete",
     "vibe": "Raw béton brut. Monolithic, heroic, unapologetic.",
     "accent": "#6c757d", "sky": "#ced4da", "price_mult": 0.92, "carbon_mult": 1.18,
     "palette": {"warm": "#adb5bd", "service": "#868e96", "steel": "#495057", "neutral": "#ced4da",
                 "glass": "#9aa6b2", "concrete": "#6c757d", "accent": "#343a40", "green": "#74896b",
                 "work": "#8d99ae", "stone": "#8d8378"}},
    {"id": "scandi", "name": "Scandinavian", "material": "wood",
     "vibe": "Pale timber, white render, soft daylight. Hygge as a grid.",
     "accent": "#dda15e", "sky": "#fefae0", "price_mult": 1.08, "carbon_mult": 0.82,
     "palette": {"warm": "#e9d8c4", "service": "#dee2e6", "steel": "#ced4da", "neutral": "#f8f9fa",
                 "glass": "#cfe8ef", "concrete": "#e9ecef", "accent": "#dda15e", "green": "#a3b18a",
                 "work": "#e9d8c4", "stone": "#e7dccb"}},
    {"id": "japandi", "name": "Japandi", "material": "wood",
     "vibe": "Warm wood + black steel + wabi-sabi calm.",
     "accent": "#bb9457", "sky": "#ede0d4", "price_mult": 1.12, "carbon_mult": 0.85,
     "palette": {"warm": "#c8a27c", "service": "#8a817c", "steel": "#3a3a3a", "neutral": "#d6ccc2",
                 "glass": "#b7c4cf", "concrete": "#7f7f7f", "accent": "#bb9457", "green": "#6b705c",
                 "work": "#a98467", "stone": "#a08b73"}},
    {"id": "mediterranean", "name": "Mediterranean", "material": "stucco",
     "vibe": "Whitewash + terracotta, deep shade, sea light.",
     "accent": "#e07a5f", "sky": "#ade8f4", "price_mult": 1.05, "carbon_mult": 0.95,
     "palette": {"warm": "#f2cc8f", "service": "#e9edc9", "steel": "#cb997e", "neutral": "#fefae0",
                 "glass": "#90e0ef", "concrete": "#ddbea9", "accent": "#e07a5f", "green": "#83a98c",
                 "work": "#f4d58d", "stone": "#e3c9a0"}},
    {"id": "neotokyo", "name": "Neo-Tokyo", "material": "neon",
     "vibe": "Dark monolith, neon edges, rain-slick cyberpunk skyline.",
     "accent": "#00f5d4", "sky": "#0b0c1a", "price_mult": 1.20, "carbon_mult": 1.05,
     "palette": {"warm": "#7209b7", "service": "#3a0ca3", "steel": "#4361ee", "neutral": "#1b1b2f",
                 "glass": "#00b4d8", "concrete": "#16213e", "accent": "#00f5d4", "green": "#06d6a0",
                 "work": "#f72585", "stone": "#2a2f45"}},
    {"id": "adobe", "name": "Desert Adobe", "material": "earth",
     "vibe": "Sun-baked earth tones, thick walls, thermal mass.",
     "accent": "#bc6c25", "sky": "#fefae0", "price_mult": 0.96, "carbon_mult": 0.78,
     "palette": {"warm": "#dda15e", "service": "#cb997e", "steel": "#b08968", "neutral": "#e6ccb2",
                 "glass": "#a5a58d", "concrete": "#9c6644", "accent": "#bc6c25", "green": "#7f9172",
                 "work": "#d4a373", "stone": "#c9a27a"}},
    {"id": "glasshouse", "name": "Glasshouse", "material": "glass",
     "vibe": "All-glass curtain wall. A building made of sky.",
     "accent": "#48cae4", "sky": "#caf0f8", "price_mult": 1.28, "carbon_mult": 1.10,
     "palette": {"warm": "#90e0ef", "service": "#48cae4", "steel": "#0096c7", "neutral": "#ade8f4",
                 "glass": "#caf0f8", "concrete": "#00b4d8", "accent": "#0077b6", "green": "#80ffdb",
                 "work": "#48cae4", "stone": "#9fb8c4"}},
    {"id": "brownstone", "name": "NYC Brownstone", "material": "stone",
     "vibe": "Sandstone row houses, iron stoops, parlor windows. Pure New York.",
     "accent": "#8a5a3c", "sky": "#d9c7b0", "price_mult": 1.10, "carbon_mult": 0.90,
     "palette": {"warm": "#9c6b46", "service": "#7a5a48", "steel": "#3a3330", "neutral": "#c9b9a3",
                 "glass": "#b8c4c0", "concrete": "#6b5444", "accent": "#8a5a3c", "green": "#6e7a52",
                 "work": "#a8835c", "stone": "#7a4a2b"}},
]

_STYLE_INDEX = {s["id"]: s for s in STYLES}
_DEFAULT_STYLE = "brownstone"
_SLEEPING = {"studio", "bedroom", "mezz", "parlor", "bay"}


def _builtin_dict(row) -> Dict[str, Any]:
    mid, name, cat, tone, price, carbon, lead, glass, blurb = row
    return {
        "id": mid, "name": name, "category": cat, "tone": tone,
        "price": price, "carbon_kg": carbon, "lead_days": lead,
        "glass": glass, "blurb": blurb, "color": None, "owner": "",
        "custom": False, "public": True, "footprint_m2": UNIT_AREA_M2, "edge_m": UNIT_M,
    }


_BUILTINS = [_builtin_dict(r) for r in CATALOG]
_BUILTIN_INDEX = {b["id"]: b for b in _BUILTINS}


# ━━ Seed example designs (NYC brownstones) ━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Featured, public, owned by the protocol. Deterministic ids so re-seeding
# is idempotent. A brownstone reads front (z=0, with stoop/bay/cornice) +
# back (z=1, garden/kitchen/beds). Stacked ~5 high = a 4-storey + garden level.
def _brownstone(front, back):
    return [{"x": 0, "z": 0, "stack": front}, {"x": 0, "z": 1, "stack": back}]


SEED_DESIGNS = [
    {"id": "dsn_seed_harlem", "name": "Harlem Brownstone", "style": "brownstone",
     "description": "A single classic Harlem brownstone — stoop, parlor, two bedroom floors, cornice.",
     "cells": _brownstone(["stoop", "parlor", "bedroom", "bay", "cornice"],
                          ["garden", "kitchen", "bedroom", "bedroom", "solar"])},
    {"id": "dsn_seed_village", "name": "West Village Row", "style": "brownstone",
     "description": "Three brownstones in a row — the West Village streetwall, stepped for charm.",
     "cells": [
         {"x": -1, "z": 0, "stack": ["stoop", "parlor", "bedroom", "cornice"]},
         {"x": -1, "z": 1, "stack": ["garden", "kitchen", "bath", "solar"]},
         {"x": 0, "z": 0, "stack": ["stoop", "parlor", "bedroom", "bay", "cornice"]},
         {"x": 0, "z": 1, "stack": ["garden", "kitchen", "bedroom", "bedroom", "solar"]},
         {"x": 1, "z": 0, "stack": ["stoop", "parlor", "bay", "cornice"]},
         {"x": 1, "z": 1, "stack": ["garden", "kitchen", "bedroom", "solar"]},
     ]},
    {"id": "dsn_seed_parkslope", "name": "Park Slope Limestone", "style": "brownstone",
     "description": "A wide corner limestone — parlor + bay on two faces, roof garden up top.",
     "cells": [
         {"x": 0, "z": 0, "stack": ["stoop", "parlor", "bedroom", "bay", "cornice"]},
         {"x": 1, "z": 0, "stack": ["retail", "parlor", "bedroom", "garden"]},
         {"x": 0, "z": 1, "stack": ["garden", "living", "bedroom", "bedroom", "solar"]},
         {"x": 1, "z": 1, "stack": ["bath", "kitchen", "office", "garden"]},
     ]},
    {"id": "dsn_seed_neotower", "name": "Hudson Yards Spire", "style": "neotokyo",
     "description": "What the same bricks become in Neo-Tokyo: a neon glass spire.",
     "cells": [
         {"x": 0, "z": 0, "stack": ["retail", "living", "bedroom", "office", "mezz", "atrium", "solar"]},
         {"x": 1, "z": 0, "stack": ["stair", "stair", "stair", "stair", "stair", "stair"]},
         {"x": 0, "z": 1, "stack": ["retail", "kitchen", "bedroom", "bath", "living", "garden"]},
         {"x": 1, "z": 1, "stack": ["bath", "office", "bedroom", "atrium", "garden"]},
     ]},
]


class Mod:
    description = "Modular housing & cities — forge prefab bricks, snap buildings like LEGO, in any architecture style."

    def __init__(self, config=None):
        self.module_dir = Path(__file__).parent
        self.config = config or self._load_config()
        self.store_dir = Path(os.path.expanduser('~/.modcity'))
        self.store_dir.mkdir(parents=True, exist_ok=True)

        self.designs_path = self.store_dir / 'designs.json'
        self.cities_path = self.store_dir / 'cities.json'
        self.bricks_path = self.store_dir / 'bricks.json'

        self.port = int(self.config.get('port', 50140))
        self.app_port = int(self.config.get('app_port', 50141))

    def _load_config(self):
        config_path = self.module_dir / 'config.json'
        if config_path.exists():
            with open(config_path) as f:
                return json.load(f)
        return {}

    # ━━ Storage ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def _load_json(self, path, default):
        p = Path(path)
        if p.exists():
            try:
                with open(p) as f:
                    return json.load(f)
            except Exception:
                return default
        return default

    def _save_json(self, path, data):
        tmp = Path(str(path) + '.tmp')
        with open(tmp, 'w') as f:
            json.dump(data, f, indent=2, default=str)
        tmp.replace(path)

    def _load_designs(self): return self._load_json(self.designs_path, {})
    def _save_designs(self, d): self._save_json(self.designs_path, d)
    def _load_cities(self): return self._load_json(self.cities_path, {})
    def _save_cities(self, d): self._save_json(self.cities_path, d)
    def _load_bricks(self): return self._load_json(self.bricks_path, {})
    def _save_bricks(self, d): self._save_json(self.bricks_path, d)

    # ━━ localfs — content-addressed building storage (modular) ━━━
    # Buildings are saved/loaded through the `localfs` mod (IPFS-style CIDs)
    # rather than reinventing a store. A building's portable doc gets a CID;
    # the CID *is* the shareable, exportable artifact. Best-effort: if localfs
    # is down, the JSON index still works (cid just stays null).
    def _fs(self):
        fs = getattr(self, '_fs_inst', None)
        if fs is None:
            try:
                fs = m.mod('localfs')()
            except Exception as e:
                print(f'modcity: localfs unavailable, falling back to local index: {e}')
                fs = False
            self._fs_inst = fs
        return fs or None

    def _referenced_bricks(self, cells):
        """Custom-brick specs used by these cells — bundled so an export is
        fully self-contained and renders on anyone else's node."""
        ids = set()
        for c in (cells or []):
            if isinstance(c, dict):
                for b in (c.get('stack') or []):
                    ids.add(b)
        custom = self._load_bricks()
        return {bid: custom[bid] for bid in ids if bid in custom}

    def _portable(self, rec):
        """The self-contained, shareable building document."""
        return {
            'modcity': 1, 'kind': 'building',
            'name': rec.get('name'), 'style': rec.get('style'),
            'cells': rec.get('cells', []), 'constraints': rec.get('constraints'),
            'bricks': self._referenced_bricks(rec.get('cells')),
            'by': rec.get('owner', ''), 'forked_from': rec.get('forked_from'),
            'created': rec.get('created'), 'updated': rec.get('updated'),
        }

    def _store_blob(self, rec):
        """Put a building's portable doc into localfs; return its CID or None."""
        fs = self._fs()
        if not fs:
            return None
        try:
            cid = fs.put(self._portable(rec))
            try:
                fs.pin(cid)
            except Exception:
                pass
            return cid
        except Exception as e:
            print(f'modcity: localfs put failed: {e}')
            return None

    def export_design(self, design_id: str):
        """Export a design as a portable, self-contained document + its CID.
        The app downloads this as ``<name>.modcity.json``; the CID shares it."""
        d = self._load_designs().get(design_id)
        if not d:
            return {'error': f'unknown design: {design_id}'}
        doc = self._portable(d)
        cid = d.get('cid') or self._store_blob(d)
        safe = ''.join(ch if ch.isalnum() or ch in '-_' else '-' for ch in (d.get('name') or 'building'))
        return {'cid': cid, 'filename': f'{safe or "building"}.modcity.json', 'doc': doc}

    def load_cid(self, cid: str):
        """Load any building by its localfs CID — even one not in our index.
        This is how a shared building travels between people/nodes."""
        fs = self._fs()
        if not fs:
            return {'error': 'localfs unavailable'}
        try:
            doc = fs.get(cid)
        except Exception as e:
            return {'error': f'could not load {cid}: {e}'}
        if isinstance(doc, bytes):
            try:
                doc = json.loads(doc.decode())
            except Exception:
                return {'error': 'blob is not a modcity building'}
        if not isinstance(doc, dict) or doc.get('kind') != 'building':
            return {'error': 'not a modcity building'}
        doc['cid'] = cid
        return doc

    def import_design(self, doc=None, owner: str = '', public: bool = False, name: str = None):
        """Import a building from a portable doc (file upload or CID load) into
        a new PRIVATE design you own. Bundled custom bricks come along."""
        if isinstance(doc, str):
            try:
                doc = json.loads(doc)
            except Exception:
                return {'error': 'doc is not valid JSON'}
        if not isinstance(doc, dict) or doc.get('kind') != 'building':
            return {'error': 'not a modcity building'}
        # bring along any bundled custom bricks the design references
        bundled = doc.get('bricks') or {}
        if bundled:
            store = self._load_bricks()
            for bid, spec in bundled.items():
                if bid not in store and isinstance(spec, dict):
                    spec = dict(spec)
                    spec['id'] = bid
                    spec['owner'] = owner or spec.get('owner', '')
                    spec['public'] = False
                    spec.setdefault('custom', True)
                    store[bid] = spec
            self._save_bricks(store)
        return self.save_design(
            name=name or doc.get('name') or 'Imported building', owner=owner,
            cells=doc.get('cells', []), style=doc.get('style', _DEFAULT_STYLE),
            description=doc.get('description', ''), public=public,
            constraints=doc.get('constraints'))

    # ━━ Brick library ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def _spec_index(self):
        """id → spec dict for EVERY brick (built-in + all custom). Used to
        price/measure any design, regardless of brick privacy."""
        idx = dict(_BUILTIN_INDEX)
        for b in self._load_bricks().values():
            idx[b["id"]] = b
        return idx

    def catalog(self, owner: str = None):
        """The brick palette: built-ins + custom bricks visible to ``owner``
        (their own private bricks + everyone's public bricks)."""
        out = list(_BUILTINS)
        for b in self._load_bricks().values():
            if b.get("public") or (owner and b.get("owner") == owner):
                out.append(b)
        return out

    def module_detail(self, module_id: str):
        spec = self._spec_index().get(module_id)
        return spec or {'error': f'unknown module: {module_id}'}

    def _brick_id(self, name, owner):
        seed = f"brick|{name}|{owner}|{time.time()}".encode()
        return 'brk_' + hashlib.sha256(seed).hexdigest()[:10]

    def create_brick(self, name: str = '', category: str = 'living', color: str = '#f4a261',
                     price: float = 18000, carbon_kg: float = 2000, lead_days: int = 21,
                     glass: bool = False, blurb: str = '', owner: str = '',
                     public: bool = False, brick_id: str = None):
        """Forge a custom brick. Private by default — set ``public`` to share
        it in the community library. Pass ``brick_id`` to edit your own brick."""
        name = (name or '').strip()
        if not name:
            return {'error': 'brick needs a name'}
        store = self._load_bricks()
        if brick_id and brick_id in store:
            existing = store[brick_id]
            if owner and existing.get('owner') and existing['owner'] != owner:
                return {'error': 'not your brick'}
            bid = brick_id
            created = existing.get('created', int(time.time()))
        else:
            bid = self._brick_id(name, owner)
            created = int(time.time())

        rec = {
            "id": bid, "name": name[:40], "category": category or 'living',
            "tone": "custom", "color": color or '#f4a261',
            "price": max(0, round(float(price))), "carbon_kg": max(0, round(float(carbon_kg))),
            "lead_days": max(1, int(lead_days)), "glass": bool(glass),
            "blurb": (blurb or '').strip()[:160] or f'A custom {category} brick.',
            "owner": owner or '', "custom": True, "public": bool(public),
            "footprint_m2": UNIT_AREA_M2, "edge_m": UNIT_M, "created": created,
            "updated": int(time.time()),
        }
        store[bid] = rec
        self._save_bricks(store)
        return rec

    def bricks(self, owner: str = None, include_public: bool = True, limit: int = 200):
        """List custom bricks visible to ``owner`` (own + public)."""
        store = self._load_bricks()
        out = []
        for b in store.values():
            mine = owner and b.get('owner') == owner
            if mine or (include_public and b.get('public')):
                bb = dict(b)
                bb['mine'] = bool(mine)
                out.append(bb)
        out.sort(key=lambda b: b.get('updated', 0), reverse=True)
        return out[:int(limit)]

    def brick(self, brick_id: str):
        b = self._load_bricks().get(brick_id)
        return b or {'error': f'unknown brick: {brick_id}'}

    def publish_brick(self, brick_id: str, owner: str = None, public: bool = True):
        store = self._load_bricks()
        b = store.get(brick_id)
        if not b:
            return {'error': f'unknown brick: {brick_id}'}
        if owner and b.get('owner') and b['owner'] != owner:
            return {'error': 'not your brick'}
        b['public'] = bool(public)
        b['updated'] = int(time.time())
        self._save_bricks(store)
        return b

    def delete_brick(self, brick_id: str, owner: str = None):
        store = self._load_bricks()
        b = store.get(brick_id)
        if not b:
            return {'error': f'unknown brick: {brick_id}'}
        if owner and b.get('owner') and b['owner'] != owner:
            return {'error': 'not your brick'}
        del store[brick_id]
        self._save_bricks(store)
        return {'deleted': brick_id}

    # ━━ Styles ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def styles(self): return STYLES

    def style(self, style_id: str):
        return _STYLE_INDEX.get(style_id) or {'error': f'unknown style: {style_id}'}

    # ━━ Composition / estimator ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def estimate(self, modules: List[str] = None, style: str = _DEFAULT_STYLE,
                 cells: List[Dict[str, Any]] = None, constraints: Dict[str, Any] = None):
        """Cost / area / carbon / lead-time of a composition, plus compliance
        against optional ``constraints`` (lot, max_floors, max_budget,
        max_carbon_kg, min_occupancy)."""
        idx = self._spec_index()
        flat: List[Dict[str, Any]] = []
        floors = 0
        footprint_cells = 0
        max_ax = max_az = 0
        if cells:
            for c in cells:
                if not isinstance(c, dict):
                    continue
                stack = c.get('stack', [])
                if stack:
                    footprint_cells += 1
                    max_ax = max(max_ax, abs(int(c.get('x', 0))))
                    max_az = max(max_az, abs(int(c.get('z', 0))))
                floors = max(floors, len(stack))
                flat.extend(stack)
        for mid in (modules or []):
            flat.append(mid)
        specs = [idx[mid] for mid in flat if mid in idx]

        st = _STYLE_INDEX.get(style, _STYLE_INDEX[_DEFAULT_STYLE])
        base_price = sum(s['price'] for s in specs)
        base_carbon = sum(s['carbon_kg'] for s in specs)
        lead = max((s['lead_days'] for s in specs), default=0)

        price = round(base_price * st['price_mult'])
        carbon = round(base_carbon * st['carbon_mult'])
        area = round(len(specs) * UNIT_AREA_M2, 1)
        assembly = max(1, round(len(specs) * 0.4)) if specs else 0
        solar = sum(1 for s in specs if s['id'] == 'solar')
        green = sum(1 for s in specs if s['id'] == 'garden')
        occ = sum(1 for s in specs if s['id'] in _SLEEPING or
                  (s.get('custom') and s.get('category') == 'living'))

        result = {
            'module_count': len(specs), 'floors': floors, 'style': st['id'],
            'price_usd': price, 'price_per_m2': round(price / area) if area else 0,
            'floor_area_m2': area, 'floor_area_ft2': round(area * 10.7639),
            'embodied_carbon_kg': carbon, 'lead_time_days': (lead + assembly) if specs else 0,
            'occupancy': occ, 'solar_modules': solar, 'green_modules': green,
            'net_positive_energy': solar > 0, 'footprint_cells': footprint_cells,
        }
        if constraints:
            result['compliance'] = self._check(result, constraints, max_ax, max_az)
        return result

    def _check(self, est, c, max_ax=0, max_az=0):
        """Compare an estimate against the user's parameters/constraints."""
        rules = {}

        def rule(value, limit, ok):
            return {'value': value, 'limit': limit, 'ok': bool(ok)}

        if c.get('max_budget'):
            lim = float(c['max_budget'])
            rules['budget'] = rule(est['price_usd'], lim, est['price_usd'] <= lim)
        if c.get('max_floors'):
            lim = int(c['max_floors'])
            rules['floors'] = rule(est['floors'], lim, est['floors'] <= lim)
        if c.get('max_carbon_kg'):
            lim = float(c['max_carbon_kg'])
            rules['carbon'] = rule(est['embodied_carbon_kg'], lim, est['embodied_carbon_kg'] <= lim)
        if c.get('min_occupancy'):
            lim = int(c['min_occupancy'])
            rules['occupancy'] = rule(est['occupancy'], lim, est['occupancy'] >= lim)
        if c.get('lot_w') and c.get('lot_d'):
            lw, ld = int(c['lot_w']), int(c['lot_d'])
            half_w, half_d = lw // 2, ld // 2
            fits = max_ax <= half_w and max_az <= half_d
            rules['lot'] = {'value': f'{max_ax*2+1}×{max_az*2+1}', 'limit': f'{lw}×{ld}', 'ok': fits}

        rules['ok'] = all(r.get('ok', True) for k, r in rules.items() if k != 'ok')
        return rules

    # ━━ Designs ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def _design_id(self, name, owner):
        return 'dsn_' + hashlib.sha256(f"{name}|{owner}|{time.time()}".encode()).hexdigest()[:10]

    def _hydrate_design(self, d):
        est = self.estimate(cells=d.get('cells', []), style=d.get('style', _DEFAULT_STYLE),
                            constraints=d.get('constraints'))
        out = dict(d)
        out['stats'] = est
        out.setdefault('public', False)
        out.setdefault('copies', 0)
        out.setdefault('constraints', None)
        out.setdefault('cid', None)
        return out

    def save_design(self, name='', owner='', cells=None, style=_DEFAULT_STYLE,
                    design_id=None, description='', public=False, constraints=None):
        """Persist a building design. PRIVATE BY DEFAULT (``public=False``)."""
        cells = cells or []
        if not any(c.get('stack') for c in cells if isinstance(c, dict)):
            return {'error': 'design has no bricks'}
        if style not in _STYLE_INDEX:
            return {'error': f'unknown style: {style}'}

        designs = self._load_designs()
        if design_id and design_id in designs:
            existing = designs[design_id]
            if owner and existing.get('owner') and existing['owner'] != owner:
                return {'error': 'not your design'}
            did = design_id
            created = existing.get('created', int(time.time()))
            copies = existing.get('copies', 0)
            forked = existing.get('forked_from')
            featured = existing.get('featured', False)
        else:
            did = self._design_id(name, owner)
            created = int(time.time())
            copies, forked, featured = 0, None, False

        rec = {
            'id': did, 'name': name or 'Untitled', 'owner': owner or '',
            'description': description or '', 'style': style, 'cells': cells,
            'public': bool(public), 'constraints': constraints, 'copies': copies,
            'forked_from': forked, 'featured': featured,
            'created': created, 'updated': int(time.time()),
        }
        rec['cid'] = self._store_blob(rec)      # content-address via localfs
        designs[did] = rec
        self._save_designs(designs)
        return self._hydrate_design(rec)

    def publish_design(self, design_id: str, owner: str = None, public: bool = True):
        """Flip a design between private and public (shareable)."""
        designs = self._load_designs()
        d = designs.get(design_id)
        if not d:
            return {'error': f'unknown design: {design_id}'}
        if owner and d.get('owner') and d['owner'] != owner:
            return {'error': 'not your design'}
        d['public'] = bool(public)
        d['updated'] = int(time.time())
        self._save_designs(designs)
        return self._hydrate_design(d)

    def copy_design(self, design_id: str, owner: str = '', name: str = None):
        """Copy-and-remix any public design (or your own) into a new PRIVATE
        design you own. Bumps the original's copy counter."""
        designs = self._load_designs()
        src = designs.get(design_id)
        if not src:
            return {'error': f'unknown design: {design_id}'}
        if not src.get('public') and owner and src.get('owner') != owner:
            return {'error': 'design is private'}
        did = self._design_id(src['name'], owner)
        rec = {
            'id': did, 'name': name or f"{src['name']} (remix)", 'owner': owner or '',
            'description': src.get('description', ''), 'style': src['style'],
            'cells': json.loads(json.dumps(src['cells'])), 'public': False,
            'constraints': src.get('constraints'), 'copies': 0,
            'forked_from': design_id, 'featured': False,
            'created': int(time.time()), 'updated': int(time.time()),
        }
        rec['cid'] = self._store_blob(rec)
        designs[did] = rec
        src['copies'] = src.get('copies', 0) + 1
        self._save_designs(designs)
        return self._hydrate_design(rec)

    def designs(self, owner: str = None, limit: int = 100, scope: str = 'public'):
        """List designs. ``scope='public'`` → the shared city (public only,
        featured first). ``scope='mine'`` → all of ``owner``'s designs."""
        data = self._load_designs()
        items = list(data.values())
        if scope == 'mine':
            if not owner:
                return []
            items = [d for d in items if d.get('owner') == owner]
            items.sort(key=lambda d: d.get('updated', 0), reverse=True)
        else:
            items = [d for d in items if d.get('public')]
            items.sort(key=lambda d: (d.get('featured', False), d.get('updated', 0)), reverse=True)
        return [self._hydrate_design(d) for d in items[:int(limit)]]

    def my_designs(self, owner: str, limit: int = 100):
        return self.designs(owner=owner, limit=limit, scope='mine')

    def design(self, design_id: str):
        d = self._load_designs().get(design_id)
        return self._hydrate_design(d) if d else {'error': f'unknown design: {design_id}'}

    def delete_design(self, design_id: str, owner: str = None):
        data = self._load_designs()
        d = data.get(design_id)
        if not d:
            return {'error': f'unknown design: {design_id}'}
        if d.get('featured'):
            return {'error': 'cannot delete a featured example'}
        if owner and d.get('owner') and d['owner'] != owner:
            return {'error': 'not your design'}
        del data[design_id]
        self._save_designs(data)
        return {'deleted': design_id}

    # ━━ Cities ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def _city_id(self, name, owner):
        return 'cty_' + hashlib.sha256(f"city|{name}|{owner}|{time.time()}".encode()).hexdigest()[:10]

    def save_city(self, name='', owner='', designs=None, layout=None, city_id=None,
                  description='', public=False):
        design_ids = designs or [d.get('design') for d in (layout or []) if isinstance(d, dict)]
        design_ids = [d for d in design_ids if d]
        if not design_ids:
            return {'error': 'city has no buildings'}
        store = self._load_cities()
        if city_id and city_id in store:
            existing = store[city_id]
            if owner and existing.get('owner') and existing['owner'] != owner:
                return {'error': 'not your city'}
            cid = city_id
            created = existing.get('created', int(time.time()))
        else:
            cid = self._city_id(name, owner)
            created = int(time.time())
        rec = {'id': cid, 'name': name or 'Untitled City', 'owner': owner or '',
               'description': description or '', 'designs': design_ids,
               'layout': layout or [], 'public': bool(public),
               'created': created, 'updated': int(time.time())}
        store[cid] = rec
        self._save_cities(store)
        return self._hydrate_city(rec)

    def _hydrate_city(self, c):
        all_designs = self._load_designs()
        buildings = []
        agg = {'price_usd': 0, 'floor_area_m2': 0.0, 'embodied_carbon_kg': 0,
               'occupancy': 0, 'module_count': 0}
        for did in c.get('designs', []):
            d = all_designs.get(did)
            if not d:
                continue
            hd = self._hydrate_design(d)
            buildings.append({'id': did, 'name': hd['name'], 'style': hd['style'], 'stats': hd['stats']})
            s = hd['stats']
            for k in agg:
                agg[k] += s[k]
        agg['floor_area_m2'] = round(agg['floor_area_m2'], 1)
        out = dict(c)
        out['building_count'] = len(buildings)
        out['buildings'] = buildings
        out['stats'] = agg
        return out

    def cities(self, owner: str = None, limit: int = 100):
        data = self._load_cities()
        items = list(data.values())
        if owner:
            items = [c for c in items if c.get('owner') == owner]
        items.sort(key=lambda c: c.get('updated', 0), reverse=True)
        return [self._hydrate_city(c) for c in items[:int(limit)]]

    def city(self, city_id: str):
        c = self._load_cities().get(city_id)
        return self._hydrate_city(c) if c else {'error': f'unknown city: {city_id}'}

    def delete_city(self, city_id: str, owner: str = None):
        data = self._load_cities()
        c = data.get(city_id)
        if not c:
            return {'error': f'unknown city: {city_id}'}
        if owner and c.get('owner') and c['owner'] != owner:
            return {'error': 'not your city'}
        del data[city_id]
        self._save_cities(data)
        return {'deleted': city_id}

    # ━━ Seeding ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def seed_examples(self):
        """Idempotently install the featured public example designs."""
        designs = self._load_designs()
        added = []
        for s in SEED_DESIGNS:
            if s['id'] in designs:
                # keep cells/style fresh if we edit the seeds in code
                designs[s['id']].update({'name': s['name'], 'style': s['style'],
                                         'description': s['description'], 'cells': s['cells'],
                                         'public': True, 'featured': True, 'owner': 'modcity'})
                continue
            now = int(time.time())
            designs[s['id']] = {
                'id': s['id'], 'name': s['name'], 'owner': 'modcity',
                'description': s['description'], 'style': s['style'], 'cells': s['cells'],
                'public': True, 'featured': True, 'constraints': None, 'copies': 0,
                'forked_from': None, 'created': now, 'updated': now,
            }
            added.append(s['id'])
        self._save_designs(designs)
        return {'seeded': added, 'total_featured': len(SEED_DESIGNS)}

    # ━━ Health / status ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def health(self):
        return {'status': 'ok', 'module': 'modcity',
                'builtin_modules': len(CATALOG), 'custom_bricks': len(self._load_bricks()),
                'styles': len(STYLES)}

    def status(self):
        designs = self._load_designs()
        cities = self._load_cities()
        bricks = self._load_bricks()
        public = [d for d in designs.values() if d.get('public')]
        hydrated = [self._hydrate_design(d) for d in public]
        return {
            'module': 'modcity',
            'builtin_modules': len(CATALOG), 'custom_bricks': len(bricks),
            'public_bricks': sum(1 for b in bricks.values() if b.get('public')),
            'styles': len(STYLES), 'designs': len(designs), 'public_designs': len(public),
            'cities': len(cities),
            'bricks_placed': sum(d['stats']['module_count'] for d in hydrated),
            'total_design_value_usd': sum(d['stats']['price_usd'] for d in hydrated),
            'total_floor_area_m2': round(sum(d['stats']['floor_area_m2'] for d in hydrated), 1),
        }

    # ━━ Source ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    _SOURCE_FILES = [
        ('mod.py', 'python', 'The protocol: prefab spec, styles, brick forge, composition, constraints engine.'),
        ('api/api.py', 'python', 'FastAPI REST surface over the module.'),
    ]

    def source(self):
        out = []
        for rel, lang, desc in self._SOURCE_FILES:
            p = self.module_dir / rel
            if not p.exists():
                continue
            try:
                content = p.read_text()
            except Exception as e:
                content = f'// could not read {rel}: {e}'
            out.append({'name': rel, 'language': lang, 'description': desc,
                        'lines': content.count('\n') + 1,
                        'bytes': len(content.encode('utf-8')), 'content': content})
        return out

    # ━━ Serve / kill ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def _pm2_start(self, name, cmd, cwd=None, env=None):
        import subprocess
        subprocess.run(['pm2', 'delete', name], capture_output=True, text=True)
        pm2_cmd = ['pm2', 'start', cmd[0], '--name', name, '--']
        pm2_cmd.extend(cmd[1:])
        if cwd:
            idx = pm2_cmd.index('--')
            pm2_cmd.insert(idx, cwd)
            pm2_cmd.insert(idx, '--cwd')
        result = subprocess.run(pm2_cmd, capture_output=True, text=True,
                                env={**os.environ, **(env or {})})
        return result.returncode == 0

    def _pm2_kill(self, name):
        import subprocess
        return subprocess.run(['pm2', 'delete', name], capture_output=True, text=True).returncode == 0

    def serve_api(self, port=None, reload=True):
        port = int(port or self.port)
        name = 'modcity.api'
        api_dir = self.module_dir / 'api'
        if not (api_dir / 'api.py').exists():
            return {'error': 'api/api.py not found'}
        mod_root = str(self.module_dir.parent.parent.parent)
        env = {'PYTHONPATH': f"{mod_root}:{self.module_dir}:{os.environ.get('PYTHONPATH', '')}",
               'PORT': str(port)}
        cmd = ['python3', '-m', 'uvicorn', 'api:app', '--host', '0.0.0.0',
               '--port', str(port), '--app-dir', str(api_dir)]
        if reload:
            cmd.append('--reload')
        self._pm2_start(name, cmd, env=env)
        return {'api': f'http://localhost:{port}', 'pm2': name, 'docs': f'http://localhost:{port}/docs'}

    def kill_api(self):
        ok = self._pm2_kill('modcity.api')
        return {'killed': ['modcity.api'] if ok else [], 'success': ok}

    def serve_app(self, app_port=None, dev=True):
        app_port = int(app_port or self.app_port)
        results = {}
        self.kill_app()
        self.seed_examples()
        results.update(self.serve_api(port=self.port, reload=dev))
        app_dir = self.module_dir / 'app'
        if (app_dir / 'package.json').exists():
            name = 'modcity.app'
            env = {'NEXT_PUBLIC_API_URL': f'http://localhost:{self.port}', 'PORT': str(app_port)}
            cmd = ['npx', 'next', 'dev' if dev else 'start', '-p', str(app_port)]
            self._pm2_start(name, cmd, cwd=str(app_dir), env=env)
            results['app'] = f'http://localhost:{app_port}'
            results['pm2_app'] = name
        else:
            results['app'] = None
        results['dev'] = dev
        results['registration'] = self.register(
            app_url=f'http://localhost:{app_port}', api_url=f'http://localhost:{self.port}',
            owner=os.environ.get('MODCITY_OWNER', ''))
        return results

    def serve(self, port=None, app_port=None, dev=True):
        return self.serve_app(app_port=app_port, dev=dev)

    def kill_app(self):
        killed = []
        if self._pm2_kill('modcity.api'): killed.append('modcity.api')
        if self._pm2_kill('modcity.app'): killed.append('modcity.app')
        return {'killed': killed}

    def kill(self): return self.kill_app()

    # ━━ Registration ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def register(self, app_url=None, api_url=None, owner=None, gateway='https://modc2.com'):
        app_url = app_url or f'http://localhost:{self.app_port}'
        api_url = api_url or f'http://localhost:{self.port}'
        try:
            ns = m.mod('server.namespace')()
            ns.reg('modcity', app_url)
            ns.reg_app('modcity', app_url, owner=owner or '', port=self.app_port, api_url=api_url)
            public = f"{gateway.rstrip('/')}/modcity"
            print(f"modcity registered → {public}  (app: {app_url}, api: {api_url})")
            return {'ok': True, 'gateway': public, 'app': app_url, 'api': api_url}
        except Exception as e:
            print(f"modcity: gateway registration failed: {e}")
            return {'ok': False, 'error': str(e), 'app': app_url, 'api': api_url}

    def deregister(self):
        try:
            m.mod('server.namespace')().dereg_app('modcity')
            return {'ok': True, 'deregistered': 'modcity'}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    # ━━ CLI ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def forward(self, action=None, **kwargs):
        """CLI entry point: modcity <action> [args]"""
        def _j(v, default=None):
            if v is None:
                return default
            if isinstance(v, str):
                try:
                    return json.loads(v)
                except Exception:
                    return [x for x in v.split(',') if x] if ',' in v else v
            return v

        def _b(v):
            return str(v).lower() in ('1', 'true', 'yes', 'on') if not isinstance(v, bool) else v

        actions = {
            'health': lambda: self.health(),
            'status': lambda: self.status(),
            'catalog': lambda: self.catalog(owner=kwargs.get('owner')),
            'module_detail': lambda: self.module_detail(kwargs.get('module_id', '')),
            'styles': lambda: self.styles(),
            'style': lambda: self.style(kwargs.get('style_id', '')),
            'create_brick': lambda: self.create_brick(
                name=kwargs.get('name', ''), category=kwargs.get('category', 'living'),
                color=kwargs.get('color', '#f4a261'), price=kwargs.get('price', 18000),
                carbon_kg=kwargs.get('carbon_kg', 2000), lead_days=kwargs.get('lead_days', 21),
                glass=_b(kwargs.get('glass', False)), blurb=kwargs.get('blurb', ''),
                owner=kwargs.get('owner', ''), public=_b(kwargs.get('public', False)),
                brick_id=kwargs.get('brick_id')),
            'bricks': lambda: self.bricks(owner=kwargs.get('owner'),
                                          include_public=_b(kwargs.get('include_public', True)),
                                          limit=int(kwargs.get('limit', 200))),
            'brick': lambda: self.brick(kwargs.get('brick_id', '')),
            'publish_brick': lambda: self.publish_brick(kwargs.get('brick_id', ''),
                                                        owner=kwargs.get('owner'),
                                                        public=_b(kwargs.get('public', True))),
            'delete_brick': lambda: self.delete_brick(kwargs.get('brick_id', ''),
                                                      owner=kwargs.get('owner')),
            'estimate': lambda: self.estimate(modules=_j(kwargs.get('modules')),
                                              style=kwargs.get('style', _DEFAULT_STYLE),
                                              cells=_j(kwargs.get('cells')),
                                              constraints=_j(kwargs.get('constraints'))),
            'save_design': lambda: self.save_design(
                name=kwargs.get('name', ''), owner=kwargs.get('owner', ''),
                cells=_j(kwargs.get('cells')), style=kwargs.get('style', _DEFAULT_STYLE),
                design_id=kwargs.get('design_id'), description=kwargs.get('description', ''),
                public=_b(kwargs.get('public', False)), constraints=_j(kwargs.get('constraints'))),
            'publish_design': lambda: self.publish_design(kwargs.get('design_id', ''),
                                                          owner=kwargs.get('owner'),
                                                          public=_b(kwargs.get('public', True))),
            'copy_design': lambda: self.copy_design(kwargs.get('design_id', ''),
                                                    owner=kwargs.get('owner', ''),
                                                    name=kwargs.get('name')),
            'designs': lambda: self.designs(owner=kwargs.get('owner'),
                                            limit=int(kwargs.get('limit', 100)),
                                            scope=kwargs.get('scope', 'public')),
            'my_designs': lambda: self.my_designs(kwargs.get('owner', ''),
                                                  limit=int(kwargs.get('limit', 100))),
            'design': lambda: self.design(kwargs.get('design_id', '')),
            'delete_design': lambda: self.delete_design(kwargs.get('design_id', ''),
                                                        owner=kwargs.get('owner')),
            'export_design': lambda: self.export_design(kwargs.get('design_id', '')),
            'load_cid': lambda: self.load_cid(kwargs.get('cid', '')),
            'import_design': lambda: self.import_design(doc=_j(kwargs.get('doc')),
                                                        owner=kwargs.get('owner', ''),
                                                        public=_b(kwargs.get('public', False)),
                                                        name=kwargs.get('name')),
            'save_city': lambda: self.save_city(name=kwargs.get('name', ''),
                                                owner=kwargs.get('owner', ''),
                                                designs=_j(kwargs.get('designs')),
                                                layout=_j(kwargs.get('layout')),
                                                city_id=kwargs.get('city_id'),
                                                description=kwargs.get('description', ''),
                                                public=_b(kwargs.get('public', False))),
            'cities': lambda: self.cities(owner=kwargs.get('owner'),
                                          limit=int(kwargs.get('limit', 100))),
            'city': lambda: self.city(kwargs.get('city_id', '')),
            'delete_city': lambda: self.delete_city(kwargs.get('city_id', ''),
                                                    owner=kwargs.get('owner')),
            'seed_examples': lambda: self.seed_examples(),
            'source': lambda: self.source(),
            'serve': lambda: self.serve(app_port=kwargs.get('app_port'), dev=kwargs.get('dev', True)),
            'kill': lambda: self.kill(),
            'serve_api': lambda: self.serve_api(port=kwargs.get('port'), reload=kwargs.get('reload', True)),
            'kill_api': lambda: self.kill_api(),
            'serve_app': lambda: self.serve_app(app_port=kwargs.get('app_port'), dev=kwargs.get('dev', True)),
            'kill_app': lambda: self.kill_app(),
            'register': lambda: self.register(app_url=kwargs.get('app_url'), api_url=kwargs.get('api_url'),
                                              owner=kwargs.get('owner'),
                                              gateway=kwargs.get('gateway', 'https://modc2.com')),
            'deregister': lambda: self.deregister(),
        }
        if not action or action not in actions:
            return {'module': 'modcity', 'description': self.description,
                    'actions': list(actions.keys()), 'status': self.status()}
        return actions[action]()
