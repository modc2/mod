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
FLOOR_H_M = UNIT_M                  # one stacked module ≈ one storey ≈ 3 m

# ━━ Pro-forma benchmarks — savings vs site-built + ROI ━━━━━━━━━━━━━━━
# North-American urban low-rise typicals. Every figure is overridable
# per-design through constraints: site_built_cost_m2, monthly_rent_usd,
# rent_per_m2_mo, land_cost_usd, soft_cost_pct, vacancy_pct, opex_pct.
SITE_BUILT_COST_M2 = 3400        # USD/m² hard cost, conventional stick-built
SITE_BUILT_CARBON_KG_M2 = 650    # kg CO₂e/m² embodied, conventional build
RENT_PER_M2_MO = 30.0            # USD/m²/month asking-rent default
SOFT_COST_PCT = 15.0             # design + permits + fees, % of hard cost
VACANCY_PCT = 5.0                # % of gross rent lost to vacancy
OPEX_PCT = 25.0                  # operating costs, % of collected rent
SITE_MONTHS_BASE = 8.0           # site-built schedule = base + area-scaled
SITE_MONTHS_PER_M2 = 1 / 25.0
MODULAR_SITE_MONTHS = 2.0        # foundations + crane set + hookup

# ━━ Canadian laneway / ADU code presets ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Building-envelope limits for accessory dwelling units (laneway houses,
# garden suites). Selecting one flags whether a design fits a municipality's
# *as-of-right* — no rezoning, expedited-permit — path. These are PROGRAM
# TYPICALS for massing guidance; absolute floor-area caps usually scale with
# lot size / FSR, so always confirm the exact figures with the authority
# having jurisdiction (AHJ). `recommend` seeds the grid lot + floor caps.
#   max_height_m / max_storeys / max_gfa_m2 / max_footprint_m2 are the checks
#   the grid can derive cleanly. (Lot *coverage* and angular planes are measured
#   against the full parcel / setbacks, which this massing tool doesn't model —
#   captured in `note` instead.) Figures current to 2025; verify with the AHJ.
LANEWAY_CODES: List[Dict[str, Any]] = [
    {"id": "none", "name": "Freeform (no code)", "region": "—",
     "note": "No building-code envelope applied.", "source": None,
     "max_height_m": None, "max_storeys": None, "max_gfa_m2": None,
     "max_footprint_m2": None, "recommend": None},
    {"id": "nbc_part9", "name": "NBC Part 9 — Houses & Small Buildings",
     "region": "Canada (national model code)",
     "max_height_m": None, "max_storeys": 3, "max_gfa_m2": None, "max_footprint_m2": 600,
     "recommend": {"lot_w": 3, "lot_d": 4, "max_floors": 2},
     "note": "Part 9 scope: ≤3 storeys AND ≤600 m² building area (Group C residential). Every detached ADU sits well inside it. Provinces add energy tiers — BC Energy Step Code is Step 3 now, Step 5 (net-zero-ready) by 2032.",
     "source": "https://nrc.canada.ca/en/certifications-evaluations-standards/codes-canada/codes-canada-publications/national-building-code-canada-2020"},
    {"id": "cmhc_adu", "name": "CMHC Housing Design Catalogue — ADU",
     "region": "Canada (pre-approved catalogue)",
     "max_height_m": 8.5, "max_storeys": 2, "max_gfa_m2": 128, "max_footprint_m2": None,
     "recommend": {"lot_w": 3, "lot_d": 4, "max_floors": 2},
     "note": "Free national catalogue of ~50 standardized, near-permit-ready designs incl. detached ADUs (studio–2-bed, 1–2 storey, ≈46–128 m²). 14+ municipalities (Vancouver, Toronto, Ottawa…) pre-review them to fast-track approvals. An architect must still adapt to the site.",
     "source": "https://www.housingcatalogue.cmhc-schl.gc.ca/"},
    {"id": "van_laneway", "name": "Vancouver — Laneway House (R1-1)",
     "region": "Vancouver, BC",
     "max_height_m": 8.5, "max_storeys": 2, "max_gfa_m2": 186, "max_footprint_m2": None,
     "recommend": {"lot_w": 3, "lot_d": 4, "max_floors": 2},
     "note": "Zoning By-law §11. Floor area = lesser of 0.25×site area or 186 m² (FSR raised 0.16→0.25 in 2025). 2-storey form ≤8.5 m; 1½-storey ≤~6.7 m by roof pitch. Site coverage ≤50% (whole parcel), 4.9 m separation, no parking required. Verify §11.",
     "source": "https://bylaws.vancouver.ca/zoning/zoning-by-law-section-11.pdf"},
    {"id": "to_laneway", "name": "Toronto — Laneway Suite",
     "region": "Toronto, ON",
     "max_height_m": 6.3, "max_storeys": 2, "max_gfa_m2": None, "max_footprint_m2": 60,
     "recommend": {"lot_w": 3, "lot_d": 4, "max_floors": 2},
     "note": "Zoning By-law 569-2013 Ch.150.8: lot must abut a public lane. Footprint ≤60 m² (≤10 m deep × 8 m wide); height 4.0 m at 5–7.5 m separation, 6.3 m beyond; GFA < main house; ancillary coverage ≤30%. O.Reg 462/24 cuts separation to 4.0 m for ≤4 m units & drops the angular plane.",
     "source": "https://www.toronto.ca/zoning/bylaw_amendments/ZBL_NewProvision_Chapter150_8.htm"},
    {"id": "to_garden", "name": "Toronto — Garden Suite",
     "region": "Toronto, ON",
     "max_height_m": 6.0, "max_storeys": 2, "max_gfa_m2": None, "max_footprint_m2": 60,
     "recommend": {"lot_w": 3, "lot_d": 3, "max_floors": 2},
     "note": "Zoning By-law 569-2013 Ch.150.7 (as-of-right 2022, lots WITHOUT lane access). Footprint = lesser of 0.40×rear-yard or 60 m²; height 4.0 m within 7.5 m separation, 6.0 m beyond; GFA < main house; ancillary coverage ≤20%. O.Reg 462/24 overlay applies.",
     "source": "https://www.toronto.ca/zoning/bylaw_amendments/ZBL_NewProvision_Chapter150_7.htm"},
]
_CODE_INDEX = {c["id"]: c for c in LANEWAY_CODES}

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

# ━━ Panels — designable, shareable façade elements ━━━━━━━━━━━━━━━━
# A *panel* is the prefab cladding/wall element that skins a module face
# (distinct from a module/room, which is the stackable 3 m unit). Each panel
# carries a full FABRICATION SPEC so a prefab factory can build it directly:
# real mm dimensions, assembly type, thermal value (RSI), weight and
# connection. Anyone can forge a panel and publish it to the shared library.
PANEL_FIELDS_DOC = "kind=solid|window|curtain|louver ; glazing 0–1 ; win_cols/rows window grid"
PANELS: List[Dict[str, Any]] = [
    {"id": "clt_solid", "name": "CLT Solid Wall", "kind": "solid",
     "color": "#caa472", "frame": "#7a5a3c", "glazing": 0.0, "win_cols": 0, "win_rows": 0,
     "material": "wood", "reveal": True,
     "width_mm": 3000, "height_mm": 3000, "thickness_mm": 120, "weight_kg": 850,
     "assembly": "CLT (cross-laminated timber)", "r_value": 3.5, "connection": "screwed (self-tapping)",
     "fab_notes": "5-ply CLT, CNC-routed service chases, factory-sealed.",
     "price": 4200, "carbon_kg": 520, "lead_days": 18},
    {"id": "sip_window", "name": "SIP Window Wall", "kind": "window",
     "color": "#e7e2d8", "frame": "#3a3a3a", "glazing": 0.35, "win_cols": 2, "win_rows": 2,
     "material": "composite", "reveal": False,
     "width_mm": 3000, "height_mm": 3000, "thickness_mm": 200, "weight_kg": 210,
     "assembly": "SIP (structural insulated panel)", "r_value": 5.0, "connection": "screwed + spline",
     "fab_notes": "OSB/EPS/OSB core, factory-glazed triple-pane units, taped seams.",
     "price": 3800, "carbon_kg": 410, "lead_days": 14},
    {"id": "curtain_glass", "name": "Unitised Curtain Wall", "kind": "curtain",
     "color": "#9fc4d4", "frame": "#2b3138", "glazing": 0.95, "win_cols": 2, "win_rows": 3,
     "material": "glass", "reveal": False,
     "width_mm": 3000, "height_mm": 3000, "thickness_mm": 60, "weight_kg": 180,
     "assembly": "Unitised aluminium curtain wall", "r_value": 0.7, "connection": "stack-joint clip",
     "fab_notes": "Factory-assembled stick units, low-e double glazing, drained/pressure-equalised.",
     "price": 5600, "carbon_kg": 720, "lead_days": 24},
    {"id": "precast_concrete", "name": "Precast Concrete", "kind": "solid",
     "color": "#b9bcc2", "frame": "#7e8186", "glazing": 0.12, "win_cols": 1, "win_rows": 1,
     "material": "concrete", "reveal": True,
     "width_mm": 3000, "height_mm": 3000, "thickness_mm": 150, "weight_kg": 1100,
     "assembly": "Precast concrete sandwich", "r_value": 2.0, "connection": "bolted (cast-in ferrules)",
     "fab_notes": "Insulated wythe, form-liner reveal pattern, lifting anchors cast in.",
     "price": 4900, "carbon_kg": 1450, "lead_days": 20},
    {"id": "steel_louver", "name": "Steel Louver Screen", "kind": "louver",
     "color": "#5b6066", "frame": "#33373c", "glazing": 0.0, "win_cols": 0, "win_rows": 0,
     "material": "metal", "reveal": False,
     "width_mm": 3000, "height_mm": 3000, "thickness_mm": 80, "weight_kg": 140,
     "assembly": "Steel-stud + perforated/louvered metal", "r_value": 1.0, "connection": "bolted",
     "fab_notes": "Solar-shading screen, powder-coated, ventilated rainscreen cavity.",
     "price": 3100, "carbon_kg": 380, "lead_days": 12},
    {"id": "timber_board", "name": "Timber Board Clad", "kind": "window",
     "color": "#a8753f", "frame": "#5c3d22", "glazing": 0.2, "win_cols": 1, "win_rows": 1,
     "material": "wood", "reveal": True,
     "width_mm": 3000, "height_mm": 3000, "thickness_mm": 180, "weight_kg": 190,
     "assembly": "Steel-stud + charred-timber rainscreen", "r_value": 4.2, "connection": "screwed (clip rail)",
     "fab_notes": "Shou-sugi-ban board-on-board, ventilated cavity, factory-finished.",
     "price": 3500, "carbon_kg": 300, "lead_days": 15},
]
_PANEL_INDEX = {p["id"]: p for p in PANELS}

def _panel_dict(p, **extra):
    d = {"custom": False, "public": True, "owner": "modcity"}
    d.update(p); d.update(extra)
    return d

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
_WET = {"kitchen", "bath"}


# ━━ Pluggable audit agents ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# The building-standards audit is provider-agnostic: a design + its
# deterministic metrics are handed to an *audit agent* that returns a
# structured verdict. Any agent can be plugged in — add one row here.
#   kind='rules'     → built-in deterministic auditor (no key, always works)
#   kind='openai'    → any OpenAI-compatible chat API (Venice, OpenRouter, …)
#   kind='anthropic' → Anthropic Messages API (Claude)
# The default is Venice; if its key is absent (or any LLM call fails) the
# audit transparently falls back to the built-in rules auditor so the
# feature never hard-fails.
AUDIT_PROVIDERS: Dict[str, Dict[str, Any]] = {
    "rules": {
        "label": "Built-in rules auditor", "kind": "rules",
        "env": None, "base_url": None, "default_model": "deterministic",
        "needs_key": False,
        "blurb": "Offline, deterministic code-envelope + life-safety heuristics. Always available.",
    },
    "venice": {
        "label": "Venice", "kind": "openai",
        "base_url": "https://api.venice.ai/api/v1", "env": "VENICE_API_KEY",
        "default_model": "llama-3.3-70b", "needs_key": True,
        "blurb": "Venice AI (OpenAI-compatible). Private, uncensored inference.",
    },
    "claude": {
        "label": "Claude", "kind": "anthropic",
        "base_url": "https://api.anthropic.com/v1", "env": "ANTHROPIC_API_KEY",
        "default_model": "claude-sonnet-4-6", "needs_key": True,
        "blurb": "Anthropic Claude — strong structured reasoning over codes.",
    },
}
DEFAULT_AUDIT_PROVIDER = "venice"

_AUDIT_SYSTEM = (
    "You are a meticulous building-permit plan examiner auditing a modular "
    "(prefab, 3×3×3 m volumetric module) building design for code compliance and "
    "buildability. You are given the design, its computed metrics, the applicable "
    "building-code envelope, and a set of DETERMINISTIC compliance checks already "
    "evaluated by the tool. Treat those deterministic checks as ground truth — do "
    "not contradict their pass/fail, but explain them and add the judgement a "
    "plan examiner would: life-safety (egress, fire separation, second exit), "
    "habitability (light/air, ceiling height, wet cores, accessibility), "
    "structural plausibility (slenderness, lateral stability), energy, and "
    "permitting path. Be specific and cite the named code where possible. You are "
    "an advisory pre-check, NOT a stamped professional approval.\n\n"
    "Respond with STRICT JSON only (no prose, no markdown fences) matching:\n"
    "{\n"
    '  "verdict": "pass" | "conditional" | "fail",\n'
    '  "score": 0-100,\n'
    '  "summary": "one-paragraph plain-language verdict",\n'
    '  "checks": [{"standard": str, "category": "life-safety"|"habitability"|"structure"|"energy"|"envelope"|"zoning"|"accessibility", "status": "pass"|"warn"|"fail"|"unknown", "detail": str, "recommendation": str, "severity": "low"|"medium"|"high"}],\n'
    '  "red_flags": [str],\n'
    '  "recommendations": [str]\n'
    "}"
)


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


# Pre-approved-style LANEWAY templates — ready compositions that already fit
# the matching code envelope, mirroring the CMHC catalogue / municipal "as-of-
# right" fast track. Each carries its own constraints (code + lot + floors) so
# its compliance is green out of the box; load → tweak → save.
LANEWAY_TEMPLATES = [
    {"id": "dsn_lane_studio", "name": "Studio Laneway · 1-storey", "style": "scandi",
     "laneway": True, "code": "van_laneway",
     "description": "388 sq ft single-storey laneway studio — living, galley kitchen, bath, sleeping nook. Vancouver-compliant.",
     "constraints": {"code": "van_laneway", "lot_w": 3, "lot_d": 3, "max_floors": 2},
     "cells": [
         {"x": 0, "z": 0, "stack": ["living"]}, {"x": 1, "z": 0, "stack": ["kitchen"]},
         {"x": 0, "z": 1, "stack": ["studio"]}, {"x": 1, "z": 1, "stack": ["bath"]},
     ]},
    {"id": "dsn_lane_onebed", "name": "One-Bed Laneway · 1½-storey", "style": "scandi",
     "laneway": True, "code": "cmhc_adu",
     "description": "775 sq ft 1-bed over two levels — ground living/kitchen, upper bedroom + work loft, solar roof. CMHC-catalogue style.",
     "constraints": {"code": "cmhc_adu", "lot_w": 3, "lot_d": 3, "max_floors": 2},
     "cells": [
         {"x": 0, "z": 0, "stack": ["living", "bedroom"]}, {"x": 1, "z": 0, "stack": ["kitchen", "bath"]},
         {"x": 0, "z": 1, "stack": ["bath", "office"]}, {"x": 1, "z": 1, "stack": ["stair", "solar"]},
     ]},
    {"id": "dsn_lane_twobed", "name": "Two-Bed Laneway · 2-storey", "style": "scandi",
     "laneway": True, "code": "to_laneway",
     "description": "969 sq ft 2-bed laneway suite — full ground floor + two bedrooms up, rear green deck. Toronto laneway-suite envelope.",
     "constraints": {"code": "to_laneway", "lot_w": 3, "lot_d": 5, "max_floors": 2},
     "cells": [
         {"x": 0, "z": 0, "stack": ["living", "bedroom"]}, {"x": 1, "z": 0, "stack": ["kitchen", "bedroom"]},
         {"x": 0, "z": 1, "stack": ["bath", "bath"]}, {"x": 1, "z": 1, "stack": ["stair", "office"]},
         {"x": 0, "z": 2, "stack": ["garden"]}, {"x": 1, "z": 2, "stack": ["garden"]},
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
        self.panels_path = self.store_dir / 'panels.json'

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
    def _load_panels(self): return self._load_json(self.panels_path, {})
    def _save_panels(self, d): self._save_json(self.panels_path, d)

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

    def _referenced_panels(self, constraints):
        """Custom façade panel referenced as the building's skin — bundled so an
        export renders identically on anyone's node."""
        skin = (constraints or {}).get('skin')
        if not skin:
            return {}
        custom = self._load_panels()
        return {skin: custom[skin]} if skin in custom else {}

    def _portable(self, rec):
        """The self-contained, shareable building document."""
        return {
            'modcity': 1, 'kind': 'building',
            'name': rec.get('name'), 'style': rec.get('style'),
            'cells': rec.get('cells', []), 'constraints': rec.get('constraints'),
            'bricks': self._referenced_bricks(rec.get('cells')),
            'panels': self._referenced_panels(rec.get('constraints')),
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
        # bring along any bundled custom façade panel (the building's skin)
        bundled_panels = doc.get('panels') or {}
        if bundled_panels:
            pstore = self._load_panels()
            for pid, spec in bundled_panels.items():
                if pid not in pstore and isinstance(spec, dict):
                    spec = dict(spec); spec['id'] = pid
                    spec['owner'] = owner or spec.get('owner', '')
                    spec['public'] = False; spec.setdefault('custom', True)
                    pstore[pid] = spec
            self._save_panels(pstore)
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

    # ━━ Panels — designable, shareable façade elements ━━━━━━━━━━━━
    def _panel_index(self):
        """Built-ins + owner's + public custom panels, keyed by id."""
        idx = {p['id']: _panel_dict(p) for p in PANELS}
        for pid, p in self._load_panels().items():
            idx[pid] = p
        return idx

    def panel_catalog(self, owner: str = None):
        idx = {p['id']: _panel_dict(p) for p in PANELS}
        for pid, p in self._load_panels().items():
            if p.get('public') or (owner and p.get('owner') == owner):
                p = dict(p)
                p['mine'] = bool(owner and p.get('owner') == owner)
                idx[pid] = p
        return list(idx.values())

    def panels(self, owner: str = None, include_public: bool = True, limit: int = 200):
        out = []
        for p in self._load_panels().values():
            mine = owner and p.get('owner') == owner
            if mine or (include_public and p.get('public')):
                p = dict(p); p['mine'] = bool(mine)
                out.append(p)
        out.sort(key=lambda p: p.get('updated', 0), reverse=True)
        return out[:int(limit)]

    def panel(self, panel_id: str):
        return self._panel_index().get(panel_id) or {'error': f'unknown panel: {panel_id}'}

    def _panel_id(self, name, owner):
        return 'pnl_' + hashlib.sha256(f"{name}|{owner}|{time.time()}".encode()).hexdigest()[:10]

    def create_panel(self, name: str = '', kind: str = 'solid', color: str = '#caa472',
                     frame: str = '#5c3d22', glazing: float = 0.2, win_cols: int = 1,
                     win_rows: int = 1, material: str = 'wood', reveal: bool = False,
                     width_mm: int = 3000, height_mm: int = 3000, thickness_mm: int = 180,
                     weight_kg: float = 200, assembly: str = 'Steel-stud + rainscreen',
                     r_value: float = 4.0, connection: str = 'screwed', fab_notes: str = '',
                     price: float = 3500, carbon_kg: float = 350, lead_days: int = 15,
                     owner: str = '', public: bool = False, panel_id: str = None):
        """Forge a custom façade panel. PRIVATE BY DEFAULT (``public=False``).
        Carries a full prefab fabrication spec so a factory can build it."""
        name = (name or '').strip() or 'Custom Panel'
        if kind not in ('solid', 'window', 'curtain', 'louver'):
            kind = 'solid'
        store = self._load_panels()
        pid = panel_id or self._panel_id(name, owner)
        now = int(time.time())
        rec = {
            'id': pid, 'name': name, 'kind': kind,
            'color': color, 'frame': frame,
            'glazing': max(0.0, min(1.0, float(glazing))),
            'win_cols': max(0, int(win_cols)), 'win_rows': max(0, int(win_rows)),
            'material': material, 'reveal': bool(reveal),
            'width_mm': int(width_mm), 'height_mm': int(height_mm), 'thickness_mm': int(thickness_mm),
            'weight_kg': round(float(weight_kg), 1), 'assembly': assembly,
            'r_value': round(float(r_value), 2), 'connection': connection,
            'fab_notes': fab_notes,
            'price': max(0, round(float(price))), 'carbon_kg': max(0, round(float(carbon_kg))),
            'lead_days': max(1, int(lead_days)),
            'custom': True, 'public': bool(public), 'owner': owner,
            'created': store.get(pid, {}).get('created', now), 'updated': now,
        }
        store[pid] = rec
        self._save_panels(store)
        return rec

    def publish_panel(self, panel_id: str, owner: str = None, public: bool = True):
        store = self._load_panels()
        p = store.get(panel_id)
        if not p:
            return {'error': f'unknown panel: {panel_id}'}
        if owner and p.get('owner') and p['owner'] != owner:
            return {'error': 'not your panel'}
        p['public'] = bool(public); p['updated'] = int(time.time())
        self._save_panels(store)
        return p

    def delete_panel(self, panel_id: str, owner: str = None):
        store = self._load_panels()
        p = store.get(panel_id)
        if not p:
            return {'error': f'unknown panel: {panel_id}'}
        if owner and p.get('owner') and p['owner'] != owner:
            return {'error': 'not your panel'}
        del store[panel_id]
        self._save_panels(store)
        return {'deleted': panel_id}

    # ━━ Styles ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def styles(self): return STYLES

    def style(self, style_id: str):
        return _STYLE_INDEX.get(style_id) or {'error': f'unknown style: {style_id}'}

    def codes(self):
        """Laneway / ADU building-code presets (Canadian)."""
        return LANEWAY_CODES

    def code(self, code_id: str):
        return _CODE_INDEX.get(code_id) or {'error': f'unknown code: {code_id}'}

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
            'height_m': round(floors * FLOOR_H_M, 1),
            'footprint_m2': round(footprint_cells * UNIT_AREA_M2, 1),
            'cost_breakdown': self._cost_breakdown(specs, st),
        }
        # façade takeoff — modules price structure + fit-out + cassettes; the
        # exposed envelope is priced per-panel from the active skin, so choosing
        # (or forging) a cheaper panel visibly changes the building's cost.
        env = self._envelope(cells, constraints or {}) if cells else None
        if env and env['panel_count']:
            result['price_usd'] += env['subtotal_usd']
            result['price_per_m2'] = round(result['price_usd'] / area) if area else 0
            result['embodied_carbon_kg'] += env['carbon_kg']
            result['lead_time_days'] = (max(lead, env['lead_days']) + assembly) if specs else 0
            result['cost_breakdown']['envelope'] = env
            result['cost_breakdown']['total_usd'] = \
                result['cost_breakdown']['module_subtotal_usd'] + env['subtotal_usd']
        fin = self._financials(result, constraints or {})
        if fin:
            result['financials'] = fin
        if constraints:
            plot = self._plot_metrics(constraints, result)
            if plot:
                result['plot'] = plot
            result['compliance'] = self._check(result, constraints, max_ax, max_az)
        return result

    def _envelope(self, cells, constraints):
        """Façade panel takeoff: count the EXPOSED wall / curtain faces of a
        composition (party walls between adjacent modules are culled, exactly
        as rendered) and price them with the active skin panel
        (``constraints.skin``) or the built-in defaults."""
        idx = self._spec_index()
        pidx = self._panel_index()
        skin = pidx.get((constraints or {}).get('skin') or '')
        wall = skin if skin and skin.get('kind') != 'curtain' else _PANEL_INDEX['clt_solid']
        curtain = skin if skin and skin.get('kind') == 'curtain' else _PANEL_INDEX['curtain_glass']
        occ = {}
        for c in (cells or []):
            if isinstance(c, dict):
                occ[(int(c.get('x', 0)), int(c.get('z', 0)))] = c.get('stack', [])

        def is_open(x, z, level):
            stack = occ.get((x, z), [])
            s = idx.get(stack[level]) if level < len(stack) else None
            return (not s) or s['id'] == 'garden' or \
                   (s.get('category') == 'outdoor' and not s.get('glass'))

        walls = curtains = 0
        for (x, z), stack in occ.items():
            for level, mid in enumerate(stack):
                s = idx.get(mid)
                if not s or is_open(x, z, level):
                    continue
                exposed = sum(1 for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))
                              if is_open(x + dx, z + dz, level))
                if s.get('glass'):
                    curtains += exposed
                else:
                    walls += exposed
        items = []
        for pn, qty, kind in ((wall, walls, 'wall'), (curtain, curtains, 'curtain')):
            if qty:
                items.append({'id': pn['id'], 'name': pn['name'], 'kind': kind, 'qty': qty,
                              'unit_price': pn['price'], 'line_total': pn['price'] * qty,
                              'unit_carbon_kg': pn.get('carbon_kg', 0)})
        return {
            'skin': skin['id'] if skin else None,
            'items': items,
            'panel_count': walls + curtains,
            'subtotal_usd': sum(i['line_total'] for i in items),
            'carbon_kg': sum(i['unit_carbon_kg'] * i['qty'] for i in items),
            'lead_days': max((pn['lead_days'] for pn, q, _ in
                              ((wall, walls, 0), (curtain, curtains, 0)) if q), default=0),
        }

    def _financials(self, est, c):
        """Pro-forma: what modular saves vs a conventional site-built job of
        the same floor area, plus rental ROI (yield-on-cost, payback). Every
        benchmark is overridable through constraints."""
        area = est.get('floor_area_m2') or 0
        if not area:
            return None
        c = c or {}
        hard = est['price_usd']
        site_m2 = float(c.get('site_built_cost_m2') or SITE_BUILT_COST_M2)
        soft_pct = float(c.get('soft_cost_pct') if c.get('soft_cost_pct') is not None else SOFT_COST_PCT)
        land = float(c.get('land_cost_usd') or 0)
        vac = float(c.get('vacancy_pct') if c.get('vacancy_pct') is not None else VACANCY_PCT)
        opex = float(c.get('opex_pct') if c.get('opex_pct') is not None else OPEX_PCT)
        rent_m2 = float(c.get('rent_per_m2_mo') or RENT_PER_M2_MO)
        rent = float(c.get('monthly_rent_usd') or round(area * rent_m2))

        all_in = hard * (1 + soft_pct / 100) + land
        site_hard = area * site_m2
        site_all_in = site_hard * (1 + soft_pct / 100) + land
        savings = site_all_in - all_in
        modular_months = round(est.get('lead_time_days', 0) / 30 + MODULAR_SITE_MONTHS, 1)
        site_months = round(SITE_MONTHS_BASE + area * SITE_MONTHS_PER_M2, 1)
        months_saved = max(0.0, round(site_months - modular_months, 1))
        annual_gross = rent * 12 * (1 - vac / 100)
        noi = annual_gross * (1 - opex / 100)
        return {
            'all_in_cost_usd': round(all_in),
            'site_built_cost_usd': round(site_all_in),
            'savings_usd': round(savings),
            'savings_pct': round(savings / site_all_in * 100, 1) if site_all_in else 0,
            'modular_months': modular_months,
            'site_built_months': site_months,
            'months_saved': months_saved,
            'early_revenue_usd': round(months_saved * rent),
            'monthly_rent_usd': round(rent),
            'annual_noi_usd': round(noi),
            'yield_on_cost_pct': round(noi / all_in * 100, 2) if all_in else 0,
            'payback_years': round(all_in / noi, 1) if noi > 0 else None,
            'carbon_saved_kg': round(area * SITE_BUILT_CARBON_KG_M2 - est.get('embodied_carbon_kg', 0)),
            'assumptions': {
                'site_built_cost_m2': site_m2, 'rent_per_m2_mo': rent_m2,
                'land_cost_usd': land, 'soft_cost_pct': soft_pct,
                'vacancy_pct': vac, 'opex_pct': opex,
                'site_built_carbon_kg_m2': SITE_BUILT_CARBON_KG_M2,
            },
        }

    def _cost_breakdown(self, specs, st):
        """Bill of quantities: every distinct module priced per-unit (style-
        adjusted) × quantity. This is the 'units cost' a buyer signs off on."""
        mult = st['price_mult']
        groups: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        for s in specs:
            g = groups.get(s['id'])
            if g is None:
                groups[s['id']] = g = {
                    'id': s['id'], 'name': s['name'], 'category': s.get('category', ''),
                    'qty': 0, 'unit_price_base': s['price'],
                    'unit_carbon_kg': s.get('carbon_kg', 0),
                }
                order.append(s['id'])
            g['qty'] += 1
        items = []
        for k in order:
            g = groups[k]
            unit = round(g['unit_price_base'] * mult)
            items.append({**g, 'unit_price': unit, 'line_total': unit * g['qty']})
        subtotal = sum(i['line_total'] for i in items)
        return {'style': st['id'], 'price_mult': mult, 'modules': items,
                'module_subtotal_usd': subtotal, 'unit_count': sum(i['qty'] for i in items)}

    def _plot_metrics(self, c, est):
        """Resolve a *realistic* parcel (metres) into a buildable envelope.

        Real lots are measured in metres with setbacks; this snaps the
        buildable rectangle to the 3 m module grid and derives site-coverage
        and FSR (floor-space ratio / FAR) — the figures every zoning bylaw
        actually checks. Returns None unless a plot is given."""
        pw, pd = c.get('plot_w_m'), c.get('plot_d_m')
        if not pw or not pd:
            return None
        pw, pd = float(pw), float(pd)
        sf = float(c.get('setback_front_m', 0) or 0)
        sr = float(c.get('setback_rear_m', 0) or 0)
        ss = float(c.get('setback_side_m', 0) or 0)
        build_w = max(0.0, pw - 2 * ss)
        build_d = max(0.0, pd - sf - sr)
        plot_area = pw * pd
        return {
            'plot_w_m': round(pw, 2), 'plot_d_m': round(pd, 2),
            'plot_area_m2': round(plot_area, 1),
            'buildable_w_m': round(build_w, 2), 'buildable_d_m': round(build_d, 2),
            'buildable_area_m2': round(build_w * build_d, 1),
            'build_cells_w': int(build_w // UNIT_M), 'build_cells_d': int(build_d // UNIT_M),
            'coverage_pct': round(est['footprint_m2'] / plot_area * 100, 1) if plot_area else 0,
            'fsr': round(est['floor_area_m2'] / plot_area, 2) if plot_area else 0,
        }

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

        # ── realistic parcel: setbacks / coverage / FSR ──────────────
        plot = est.get('plot')
        if plot:
            bw, bd = plot['build_cells_w'], plot['build_cells_d']
            grid_w, grid_d = max_ax * 2 + 1, max_az * 2 + 1
            rules['plot_fit'] = {'value': f'{grid_w}×{grid_d}', 'limit': f'{bw}×{bd}',
                                 'ok': grid_w <= bw and grid_d <= bd}
            if c.get('max_coverage_pct'):
                lim = float(c['max_coverage_pct'])
                rules['coverage'] = rule(plot['coverage_pct'], lim, plot['coverage_pct'] <= lim)
            if c.get('max_fsr'):
                lim = float(c['max_fsr'])
                rules['fsr'] = rule(plot['fsr'], lim, plot['fsr'] <= lim)

        # ── laneway / ADU code envelope ──────────────────────────
        code = _CODE_INDEX.get(c.get('code'))
        if code:
            if code.get('max_height_m'):
                rules['height_m'] = rule(est['height_m'], code['max_height_m'],
                                         est['height_m'] <= code['max_height_m'])
            if code.get('max_storeys'):
                rules['storeys'] = rule(est['floors'], code['max_storeys'],
                                        est['floors'] <= code['max_storeys'])
            if code.get('max_gfa_m2'):
                rules['gfa'] = rule(est['floor_area_m2'], code['max_gfa_m2'],
                                    est['floor_area_m2'] <= code['max_gfa_m2'])
            if code.get('max_footprint_m2'):
                rules['footprint'] = rule(est['footprint_m2'], code['max_footprint_m2'],
                                          est['footprint_m2'] <= code['max_footprint_m2'])

        rules['ok'] = all(r.get('ok', True) for k, r in rules.items() if k != 'ok')
        return rules

    # ━━ Audit agent (pluggable: rules / Venice / Claude / any) ━━━━━━━
    def audit_providers(self):
        """List the available audit agents and whether each is ready (key
        present). Any OpenAI-compatible or Anthropic endpoint can be added to
        AUDIT_PROVIDERS — this is the seam that makes the auditor swappable."""
        out = []
        for pid, cfg in AUDIT_PROVIDERS.items():
            ready = (not cfg['needs_key']) or bool(cfg.get('env') and os.environ.get(cfg['env']))
            out.append({'id': pid, 'label': cfg['label'], 'kind': cfg['kind'],
                        'default_model': cfg['default_model'], 'needs_key': cfg['needs_key'],
                        'ready': ready, 'blurb': cfg.get('blurb', '')})
        return {'default': DEFAULT_AUDIT_PROVIDER, 'providers': out}

    def _audit_brief(self, est, name, style, constraints):
        """A compact, model-agnostic description of what's being audited."""
        c = constraints or {}
        code = _CODE_INDEX.get(c.get('code'))
        cb = est.get('cost_breakdown', {})
        modules = ', '.join(f"{i['qty']}× {i['name']}" for i in cb.get('modules', [])) or 'none'
        brief = {
            'building': {'name': name or 'Untitled', 'style': style,
                         'floors': est['floors'], 'height_m': est['height_m'],
                         'module_count': est['module_count'], 'modules': modules,
                         'occupancy_beds': est['occupancy'],
                         'floor_area_m2': est['floor_area_m2'],
                         'footprint_m2': est['footprint_m2'],
                         'has_stair_core': est.get('_has_stair', False),
                         'has_kitchen': est.get('_has_kitchen', False),
                         'has_bath': est.get('_has_bath', False),
                         'glazed_modules': est.get('_glazed', 0)},
            'cost': {'total_usd': est['price_usd'], 'price_per_m2': est['price_per_m2'],
                     'embodied_carbon_kg': est['embodied_carbon_kg'],
                     'lead_time_days': est['lead_time_days']},
            'plot': est.get('plot'),
            'code': ({'id': code['id'], 'name': code['name'], 'region': code['region'],
                      'note': code['note'], 'source': code.get('source'),
                      'limits': {k: code.get(k) for k in
                                 ('max_height_m', 'max_storeys', 'max_gfa_m2', 'max_footprint_m2')}}
                     if code else None),
            'deterministic_compliance': est.get('compliance'),
        }
        return brief

    def _design_flags(self, cells):
        """Life-safety / habitability presence flags read straight off the grid."""
        ids = set()
        for ce in (cells or []):
            if isinstance(ce, dict):
                ids.update(ce.get('stack') or [])
        return {'has_stair': 'stair' in ids, 'has_kitchen': 'kitchen' in ids,
                'has_bath': 'bath' in ids,
                'glazed': sum(1 for i in ids if i in {'atrium', 'parlor', 'bay', 'retail'})}

    def audit(self, design_id: str = None, cells: List[Dict[str, Any]] = None,
              style: str = _DEFAULT_STYLE, constraints: Dict[str, Any] = None,
              name: str = None, provider: str = None, model: str = None,
              api_key: str = None, persist: bool = True):
        """Audit a design against building standards with a pluggable audit
        agent. ``provider`` ∈ AUDIT_PROVIDERS (default Venice); falls back to the
        built-in rules auditor if the LLM key is missing or the call fails.
        Pass ``design_id`` to audit (and optionally persist the verdict on) a
        saved design, or pass ``cells``/``style``/``constraints`` directly."""
        rec = None
        if design_id:
            rec = self._load_designs().get(design_id)
            if not rec:
                return {'error': f'unknown design: {design_id}'}
            cells = rec.get('cells', cells or [])
            style = rec.get('style', style)
            constraints = rec.get('constraints', constraints)
            name = name or rec.get('name')
        cells = cells or []
        est = self.estimate(cells=cells, style=style, constraints=constraints)
        flags = self._design_flags(cells)
        est.update({'_has_stair': flags['has_stair'], '_has_kitchen': flags['has_kitchen'],
                    '_has_bath': flags['has_bath'], '_glazed': flags['glazed']})
        brief = self._audit_brief(est, name, style, constraints)

        pid = (provider or DEFAULT_AUDIT_PROVIDER)
        cfg = AUDIT_PROVIDERS.get(pid)
        if not cfg:
            return {'error': f"unknown audit provider: {pid}; choose {list(AUDIT_PROVIDERS)}"}

        key = None
        fallback_from = None
        if cfg['kind'] != 'rules':
            key = api_key or (cfg.get('env') and os.environ.get(cfg['env']))
            if not key:
                fallback_from = f"{cfg['label']} (no {cfg.get('env')} key)"
                pid, cfg = 'rules', AUDIT_PROVIDERS['rules']

        if cfg['kind'] == 'rules':
            audit = self._rules_audit(brief, est, flags)
            audit['provider'] = 'rules'
            audit['model'] = 'deterministic'
        else:
            try:
                audit = self._llm_audit(pid, cfg, brief, model, key)
            except Exception as e:
                audit = self._rules_audit(brief, est, flags)
                audit['provider'] = 'rules'
                audit['model'] = 'deterministic'
                fallback_from = f"{cfg['label']} ({type(e).__name__}: {e})"

        audit['agent'] = {'requested': provider or DEFAULT_AUDIT_PROVIDER,
                          'used': audit.get('provider', pid),
                          'fallback_from': fallback_from}
        audit['grounding'] = brief
        audit['audited_at'] = int(time.time())
        audit['disclaimer'] = ('Advisory automated pre-check — not a stamped review. '
                               'A licensed architect/engineer and the authority having '
                               'jurisdiction (AHJ) must approve any permit.')

        if design_id and rec and persist:
            designs = self._load_designs()
            if design_id in designs:
                designs[design_id]['last_audit'] = {
                    k: audit[k] for k in ('verdict', 'score', 'summary', 'agent', 'audited_at')
                    if k in audit}
                designs[design_id]['updated'] = int(time.time())
                self._save_designs(designs)
        return audit

    # ── built-in deterministic auditor (default fallback, no key) ───
    def _rules_audit(self, brief, est, flags):
        checks = []

        def add(standard, category, status, detail, rec='', severity='medium'):
            checks.append({'standard': standard, 'category': category, 'status': status,
                           'detail': detail, 'recommendation': rec, 'severity': severity})

        comp = est.get('compliance') or {}
        code = brief.get('code')
        # 1) code envelope — straight from deterministic checks
        for k, lbl in (('height_m', 'Height'), ('storeys', 'Storeys'),
                       ('gfa', 'Gross floor area'), ('footprint', 'Building footprint')):
            r = comp.get(k)
            if r:
                add(f"{(code or {}).get('name', 'Code')} — {lbl}", 'envelope',
                    'pass' if r['ok'] else 'fail',
                    f"{lbl} {r['value']} vs limit {r['limit']}.",
                    '' if r['ok'] else f"Reduce {lbl.lower()} to ≤ {r['limit']}.",
                    'low' if r['ok'] else 'high')
        # 2) parcel fit / coverage / FSR
        for k, lbl, sev in (('plot_fit', 'Setback / parcel fit', 'high'),
                            ('coverage', 'Site coverage', 'high'),
                            ('fsr', 'Floor-space ratio (FSR)', 'high')):
            r = comp.get(k)
            if r:
                add(lbl, 'zoning', 'pass' if r['ok'] else 'fail',
                    f"{lbl} {r['value']} vs limit {r['limit']}.",
                    '' if r['ok'] else f"Design exceeds {lbl.lower()} — shrink footprint or floors.",
                    'low' if r['ok'] else sev)
        # 3) egress / life-safety
        floors = est['floors']
        if floors >= 2 and not flags['has_stair']:
            add('Means of egress', 'life-safety', 'fail',
                f"{floors}-storey building has no Stair Core module.",
                "Add a Stair Core for code-compliant vertical egress.", 'high')
        elif floors >= 2:
            add('Means of egress', 'life-safety', 'pass',
                "Stair Core present for vertical circulation.", '', 'low')
        if floors >= 4 and est.get('footprint_cells', 0) < 2:
            add('Second exit / structural slenderness', 'structure', 'warn',
                f"{floors} storeys on a single-cell footprint is slender and likely needs a second exit.",
                "Widen the base or add a second egress stair.", 'medium')
        # 4) habitability — wet cores & light
        if est['occupancy'] > 0:
            if not flags['has_kitchen']:
                add('Habitability — kitchen', 'habitability', 'warn',
                    "Sleeping modules present but no Galley Kitchen.",
                    "Add a kitchen for a self-contained dwelling unit.", 'medium')
            if not flags['has_bath']:
                add('Habitability — sanitation', 'habitability', 'fail',
                    "Dwelling has sleeping space but no Wet Core (bathroom).",
                    "Add a Wet Core — a bathroom is mandatory for occupancy.", 'high')
            if flags['glazed'] == 0:
                add('Light & ventilation', 'habitability', 'warn',
                    "No glazed/parlor/atrium modules — natural light may be insufficient.",
                    "Add glazed modules so habitable rooms meet daylight minimums.", 'low')
        else:
            add('Occupancy', 'habitability', 'unknown',
                "No sleeping modules — not a dwelling (or beds modelled differently).",
                "Add Studio/Bedroom modules if residential occupancy is intended.", 'low')
        # 5) energy
        if est.get('net_positive_energy'):
            add('Energy', 'energy', 'pass', "Solar Roof present — net-positive energy target.",
                '', 'low')
        else:
            add('Energy', 'energy', 'warn', "No Solar Roof — verify the energy-tier path (e.g. BC Step Code).",
                "Add a Solar Roof or confirm energy compliance another way.", 'low')

        fails = [c for c in checks if c['status'] == 'fail']
        warns = [c for c in checks if c['status'] == 'warn']
        passes = [c for c in checks if c['status'] == 'pass']
        graded = len(fails) + len(warns) + len(passes)
        score = round(100 * (len(passes) + 0.5 * len(warns)) / graded) if graded else 50
        verdict = 'fail' if fails else ('conditional' if warns else 'pass')
        if not fails and not warns and not passes:
            verdict, score = 'conditional', 60
        summary = {
            'pass': "Design meets the deterministic checks; no blocking issues found in the automated pre-check.",
            'conditional': f"Likely permittable with conditions: {len(warns)} item(s) to address, no hard failures.",
            'fail': f"Not yet compliant: {len(fails)} blocking issue(s) must be resolved before permit.",
        }[verdict]
        return {'verdict': verdict, 'score': score, 'summary': summary, 'checks': checks,
                'red_flags': [c['detail'] for c in fails],
                'recommendations': [c['recommendation'] for c in (fails + warns) if c['recommendation']]}

    # ── LLM-backed auditor (OpenAI-compatible or Anthropic) ─────────
    def _llm_audit(self, pid, cfg, brief, model, key):
        user = ("Audit this modular building design and return STRICT JSON only.\n\n"
                + json.dumps(brief, indent=2, default=str))
        mdl = model or cfg['default_model']
        if cfg['kind'] == 'anthropic':
            text = self._call_anthropic(cfg['base_url'], key, mdl, _AUDIT_SYSTEM, user)
        else:
            text = self._call_openai(cfg['base_url'], key, mdl, _AUDIT_SYSTEM, user)
        audit = self._parse_audit_json(text)
        audit['provider'] = pid
        audit['model'] = mdl
        return audit

    @staticmethod
    def _parse_audit_json(text):
        """Extract the JSON object from a model reply, tolerating prose/fences."""
        if not text:
            raise ValueError('empty model response')
        s = text.strip()
        if s.startswith('```'):
            inner = s[3:]
            s = inner.split('```', 1)[0]
            if s[:4].lower() == 'json':
                s = s[4:]
            s = s.strip()
        try:
            obj = json.loads(s)
        except Exception:
            i, j = s.find('{'), s.rfind('}')
            if i < 0 or j <= i:
                raise ValueError('no JSON object in model response')
            obj = json.loads(s[i:j + 1])
        obj.setdefault('verdict', 'conditional')
        obj.setdefault('score', 50)
        obj.setdefault('summary', '')
        obj.setdefault('checks', [])
        obj.setdefault('red_flags', [])
        obj.setdefault('recommendations', [])
        return obj

    @staticmethod
    def _call_anthropic(base_url, key, model, system, user, max_tokens=2000):
        import requests
        r = requests.post(base_url.rstrip('/') + '/messages',
                          headers={'x-api-key': key, 'anthropic-version': '2023-06-01',
                                   'content-type': 'application/json'},
                          json={'model': model, 'max_tokens': max_tokens, 'system': system,
                                'messages': [{'role': 'user', 'content': user}]}, timeout=90)
        if r.status_code >= 400:
            raise RuntimeError(f'{r.status_code} {r.text[:300]}')
        data = r.json()
        parts = data.get('content') or []
        return ''.join(p.get('text', '') for p in parts if isinstance(p, dict))

    @staticmethod
    def _call_openai(base_url, key, model, system, user, max_tokens=2000):
        import requests
        r = requests.post(base_url.rstrip('/') + '/chat/completions',
                          headers={'Authorization': f'Bearer {key}',
                                   'Content-Type': 'application/json'},
                          json={'model': model, 'max_tokens': max_tokens, 'temperature': 0.2,
                                'messages': [{'role': 'system', 'content': system},
                                             {'role': 'user', 'content': user}]}, timeout=90)
        if r.status_code >= 400:
            raise RuntimeError(f'{r.status_code} {r.text[:300]}')
        data = r.json()
        return data['choices'][0]['message']['content']

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
        """Idempotently install the featured public example designs +
        the pre-approved laneway/ADU templates."""
        designs = self._load_designs()
        added = []
        for s in SEED_DESIGNS + LANEWAY_TEMPLATES:
            constraints = s.get('constraints')
            laneway = s.get('laneway', False)
            if s['id'] in designs:
                # keep cells/style/constraints fresh if we edit the seeds in code
                rec = designs[s['id']]
                rec.update({'name': s['name'], 'style': s['style'],
                            'description': s['description'], 'cells': s['cells'],
                            'constraints': constraints, 'laneway': laneway,
                            'public': True, 'featured': True, 'owner': 'modcity'})
                rec['cid'] = self._store_blob(rec)
                continue
            now = int(time.time())
            rec = {
                'id': s['id'], 'name': s['name'], 'owner': 'modcity',
                'description': s['description'], 'style': s['style'], 'cells': s['cells'],
                'public': True, 'featured': True, 'constraints': constraints,
                'laneway': laneway, 'copies': 0,
                'forked_from': None, 'created': now, 'updated': now,
            }
            rec['cid'] = self._store_blob(rec)
            designs[s['id']] = rec
            added.append(s['id'])
        self._save_designs(designs)
        return {'seeded': added, 'total_featured': len(SEED_DESIGNS) + len(LANEWAY_TEMPLATES)}

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
            'codes': lambda: self.codes(),
            'code': lambda: self.code(kwargs.get('code_id', '')),
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
            'panel_catalog': lambda: self.panel_catalog(owner=kwargs.get('owner')),
            'panels': lambda: self.panels(owner=kwargs.get('owner'),
                                          include_public=_b(kwargs.get('include_public', True)),
                                          limit=int(kwargs.get('limit', 200))),
            'panel': lambda: self.panel(kwargs.get('panel_id', '')),
            'create_panel': lambda: self.create_panel(
                name=kwargs.get('name', ''), kind=kwargs.get('kind', 'solid'),
                color=kwargs.get('color', '#caa472'), frame=kwargs.get('frame', '#5c3d22'),
                glazing=kwargs.get('glazing', 0.2), win_cols=kwargs.get('win_cols', 1),
                win_rows=kwargs.get('win_rows', 1), material=kwargs.get('material', 'wood'),
                reveal=_b(kwargs.get('reveal', False)), width_mm=kwargs.get('width_mm', 3000),
                height_mm=kwargs.get('height_mm', 3000), thickness_mm=kwargs.get('thickness_mm', 180),
                weight_kg=kwargs.get('weight_kg', 200), assembly=kwargs.get('assembly', 'Steel-stud + rainscreen'),
                r_value=kwargs.get('r_value', 4.0), connection=kwargs.get('connection', 'screwed'),
                fab_notes=kwargs.get('fab_notes', ''), price=kwargs.get('price', 3500),
                carbon_kg=kwargs.get('carbon_kg', 350), lead_days=kwargs.get('lead_days', 15),
                owner=kwargs.get('owner', ''), public=_b(kwargs.get('public', False)),
                panel_id=kwargs.get('panel_id')),
            'publish_panel': lambda: self.publish_panel(kwargs.get('panel_id', ''),
                                                        owner=kwargs.get('owner'),
                                                        public=_b(kwargs.get('public', True))),
            'delete_panel': lambda: self.delete_panel(kwargs.get('panel_id', ''),
                                                      owner=kwargs.get('owner')),
            'estimate': lambda: self.estimate(modules=_j(kwargs.get('modules')),
                                              style=kwargs.get('style', _DEFAULT_STYLE),
                                              cells=_j(kwargs.get('cells')),
                                              constraints=_j(kwargs.get('constraints'))),
            'audit_providers': lambda: self.audit_providers(),
            'audit': lambda: self.audit(
                design_id=kwargs.get('design_id'), cells=_j(kwargs.get('cells')),
                style=kwargs.get('style', _DEFAULT_STYLE), constraints=_j(kwargs.get('constraints')),
                name=kwargs.get('name'), provider=kwargs.get('provider'),
                model=kwargs.get('model'), api_key=kwargs.get('api_key'),
                persist=_b(kwargs.get('persist', True))),
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
