"""
tdot.housing — every open dataset Toronto publishes about housing, in one list.

Searching the city's portal for housing returns a hundred and seventy datasets,
most of which are about something else. This module is the filtered answer: the
datasets that actually describe where people live, what it costs, who owns it,
what condition it is in and what is being built — and, for each, what tdot does
with it.

Four roles, and the honest ones matter most:

    layer      on the map now, under this id
    feature    not a layer, but an input to the score model (:mod:`tdotgis.score`)
    table      open and about housing, but with no geography to draw — a
               city-wide quarterly count, a waiting-list total
    closed     the data people actually ask for, which is not open at all

That last role exists because the most common question about Toronto housing
data — "where are the sale prices" — has an answer nobody likes: MLS
transactions are TRREB's copyrighted database. They are not open at any price,
and listing them here as a gap is more useful than pretending the gap is not
there. :mod:`tdotgis.market` is the path that does exist, for whoever holds a
licence and may redistribute.

:func:`inventory` checks each entry against the portal, so the row counts and
refresh dates are the city's own rather than something written down once and
left to rot.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from . import sources as S

INVENTORY_TTL = 3 * S.DAY

ROLES = ('layer', 'feature', 'table', 'closed')

# One entry per dataset. `layer` names the tdot layer it is drawn as; `what`
# is what the dataset actually contains, in a sentence a non-planner can read.
CATALOGUE: List[Dict[str, Any]] = [
    # ── on the map ───────────────────────────────────────────────────────
    {'package': 'neighbourhood-profiles', 'role': 'layer', 'layer': 'prices',
     'title': 'Neighbourhood Profiles (2021 Census)',
     'what': 'Dwelling values, rents, tenure, shelter-cost burden, dwelling age '
             'and condition for all 158 neighbourhoods. The only open, '
             'per-neighbourhood record of what housing here is worth.'},
    {'package': 'apartment-building-evaluation', 'role': 'layer', 'layer': 'apartments',
     'title': 'Apartment Building Evaluation',
     'what': 'RentSafeTO inspection scores out of 100 for registered rental '
             'buildings — the city\'s own read on building condition.'},
    {'package': 'apartment-building-registration', 'role': 'layer', 'layer': 'rental_buildings',
     'title': 'Apartment Building Registration',
     'what': 'Every rental building of 3+ storeys and 10+ units: size, age, '
             'heating, elevators, sprinklers, amenities and who manages it.'},
    {'package': 'toronto-community-housing-data', 'role': 'layer', 'layer': 'community_housing',
     'title': 'Toronto Community Housing',
     'what': 'TCHC\'s buildings with their rent-geared-to-income and market '
             'unit counts — the city\'s own landlord portfolio.'},
    {'package': 'subsidized-housing-listings', 'role': 'layer', 'layer': 'subsidized_housing',
     'title': 'Subsidized Housing Listings',
     'what': 'Every building on the centralized waiting list, with its unit '
             'mix, provider and accessibility.'},
    {'package': 'residential-health-hazards', 'role': 'layer', 'layer': 'health_hazards',
     'title': 'Highrise Residential Health Hazards',
     'what': 'Health-hazard investigations in highrise residential buildings — '
             'pests, mould, indoor sanitation — by address and outcome.'},
    {'package': 'residential-fire-inspection-results', 'role': 'layer', 'layer': 'fire_violations',
     'title': 'Residential Fire Inspection Results',
     'what': 'Fire-code violations found in residential buildings, with the '
             'code section and whether it went to enforcement.'},
    {'package': 'upcoming-and-recently-completed-affordable-housing-units',
     'role': 'layer', 'layer': 'affordable_housing',
     'title': 'Affordable Housing Pipeline',
     'what': 'Affordable and rent-geared-to-income homes approved, under way '
             'or just completed.'},
    {'package': 'development-pipeline', 'role': 'layer', 'layer': 'development_pipeline',
     'title': 'Development Pipeline',
     'what': 'Residential units and floor area proposed in active '
             'applications — the housing supply actually in the queue.'},
    {'package': 'development-applications', 'role': 'layer', 'layer': 'development_applications',
     'title': 'Development Applications',
     'what': 'Every rezoning, site-plan, condo and subdivision application, '
             'and where it stands.'},
    {'package': 'building-permits-active-permits', 'role': 'layer', 'layer': 'building_permits',
     'title': 'Building Permits — Active',
     'what': 'Open permits by property, with construction value and the '
             'dwelling units each one creates or destroys.'},
    {'package': 'multi-tenant-house-licences', 'role': 'layer', 'layer': 'multi_tenant_houses',
     'title': 'Multi-Tenant (Rooming) House Licences',
     'what': 'Licensed rooming houses — the cheapest legal rental floor in '
             'the city.'},
    {'package': 'short-term-rentals-registration', 'role': 'layer', 'layer': 'short_term_rentals',
     'title': 'Short-Term Rental Registrations',
     'what': 'Registered short-term rental operators: the housing stock let '
             'by the night. Addresses withheld, so counted per ward.'},
    {'package': 'demolition-and-replacement-of-rental-housing-units',
     'role': 'layer', 'layer': 'rental_demolitions',
     'title': 'Demolition and Replacement of Rental Housing',
     'what': 'Rental homes approved for demolition and how many were '
             'replaced — net rental loss, per ward.'},
    {'package': 'retirement-homes', 'role': 'layer', 'layer': 'retirement_homes',
     'title': 'Retirement Homes',
     'what': 'Licensed retirement homes and their resident capacity.'},
    {'package': 'real-estate-asset-inventory', 'role': 'layer', 'layer': 'city_realty',
     'title': 'Real Estate Asset Inventory',
     'what': 'Every building the city itself owns or manages, with floor '
             'area, use and year built.'},

    # ── feeding the score model ──────────────────────────────────────────
    {'package': 'major-crime-indicators', 'role': 'feature', 'layer': 'crime',
     'title': 'Major Crime Indicators (Toronto Police)',
     'what': 'Neighbourhood incident counts, joined onto each building as '
             'context for the score model.',
     'portal': 'Toronto Police Service open data'},

    # ── open, about housing, nothing to draw ─────────────────────────────
    {'package': 'active-affordable-and-social-housing-units', 'role': 'table',
     'title': 'Active Affordable and Social Housing Units',
     'what': 'Quarterly city-wide totals of subsidized and affordable units.',
     'note': 'One row per quarter, no geography.'},
    {'package': 'centralized-waiting-list-activity-for-social-housing', 'role': 'table',
     'title': 'Centralized Waiting List for Social Housing',
     'what': 'How many households are waiting, by household type and unit '
             'size, quarter by quarter.',
     'note': 'City-wide totals only.'},
    {'package': 'social-housing-wait-list-rent-bank-loans-granted-and-shelter-use-summary',
     'role': 'table',
     'title': 'Wait List, Rent Bank Loans and Shelter Use',
     'what': 'The three back-stops against losing housing, in one summary.',
     'note': 'Published as a summary table.'},
    {'package': 'tenant-notification-for-rent-reduction', 'role': 'table',
     'title': 'Tenant Notification for Rent Reduction',
     'what': 'Properties whose taxes fell enough to require a rent reduction '
             'notice — a rare per-address record of rent moving down.',
     'note': 'Addresses are split across columns with no coordinates or '
             'address id; ~120k rows a year, one per unit.'},
    {'package': 'cost-of-living-in-toronto-for-low-income-households', 'role': 'table',
     'title': 'Cost of Living for Low-Income Households',
     'what': 'Modelled household budgets including rent, by household type.',
     'note': 'City-wide model, not geographic.'},
    {'package': 'toronto-shelter-system-flow', 'role': 'table',
     'title': 'Shelter System Flow',
     'what': 'How many people entered and left homelessness each month — the '
             'bottom of the housing system.',
     'note': 'Monthly city-wide counts.'},
    {'package': 'daily-shelter-overnight-service-occupancy-capacity', 'role': 'table',
     'title': 'Daily Shelter & Overnight Service Occupancy',
     'what': 'Nightly occupancy and capacity per shelter programme.',
     'note': 'Programme addresses are withheld for client safety.'},
    {'package': 'housing-to-action-plan', 'role': 'table',
     'title': 'HousingTO Action Plan',
     'what': 'Progress against the city\'s 10-year housing targets.',
     'note': 'Reporting table.'},
    {'package': 'wellbeing-toronto-housing', 'role': 'table',
     'title': 'Wellbeing Toronto — Housing',
     'what': 'Older per-neighbourhood housing indicators (2011-era).',
     'note': 'XLSX only, and superseded by the 2021 profile.'},
    {'package': 'neighbourhood-intensification-estimates-to-2051', 'role': 'table',
     'title': 'Neighbourhood Intensification Estimates to 2051',
     'what': 'Where planners expect the next thirty years of growth to land.',
     'note': 'XLSX only, no boundary file to join on.'},
    {'package': 'current-value-assessment-cva-tax-impact-residential-properties',
     'role': 'table',
     'title': 'Current Value Assessment — Residential Tax Impact',
     'what': 'How reassessment moved residential tax bills. The nearest open '
             'thing to per-property valuation.',
     'note': 'Aggregated to bands, not properties.'},
    {'package': 'registered-residential-non-residential-condominiums', 'role': 'table',
     'title': 'Registered Condominiums',
     'what': 'Every registered condominium corporation in the city.',
     'note': 'Published as SHP/TXT/XLSX with no datastore or GeoJSON.'},
    {'package': 'building-construction-demolition-violations', 'role': 'table',
     'title': 'Building Construction/Demolition Violations',
     'what': 'Building-code violations on construction and demolition sites.',
     'note': 'Carries a street address but no coordinates or address id, so '
             'rows cannot be placed reliably.'},
    {'package': 'social-housing-unit-density-by-neighbourhoods', 'role': 'table',
     'title': 'Social Housing Unit Density by Neighbourhood',
     'what': 'Social housing units per neighbourhood.',
     'note': 'XLSX against the retired 140-neighbourhood model.'},

    # ── not open ─────────────────────────────────────────────────────────
    {'package': None, 'role': 'closed',
     'title': 'MLS sale prices and listing history',
     'what': 'What homes actually sold for, sale by sale.',
     'note': 'TRREB\'s copyrighted database. Not open data at any price, and '
             'scraping it does not make it open. A licence-holder can publish '
             'their own feed through the tdot market and it will render like '
             'any other layer.',
     'portal': 'Toronto Regional Real Estate Board'},
    {'package': None, 'role': 'closed',
     'title': 'Asking rents by unit',
     'what': 'What a vacant unit is being advertised at today.',
     'note': 'Held by listing platforms. CMHC publishes survey averages by '
             'zone annually, which is not the same measurement and is not on '
             'the city portal.',
     'portal': 'CMHC / listing platforms'},
]


def _entry(item: dict) -> dict:
    return {
        'package': item.get('package'),
        'title': item['title'],
        'what': item['what'],
        'role': item['role'],
        'layer': item.get('layer'),
        'note': item.get('note'),
        'portal': item.get('portal', 'Toronto Open Data'),
        'url': (f'https://open.toronto.ca/dataset/{item["package"]}/'
                if item.get('package') else None),
    }


def _probe(package: str) -> dict:
    """What the portal says about one dataset right now."""
    try:
        pkg = S.ckan_package(package)
    except Exception as e:
        return {'available': False, 'error': str(e)[:120]}
    resources = pkg.get('resources') or []
    live = [r for r in resources if r.get('datastore_active')]
    return {
        'available': True,
        'refreshed': (pkg.get('last_refreshed') or pkg.get('metadata_modified') or '')[:10],
        'formats': sorted({(r.get('format') or '').upper()
                           for r in resources if r.get('format')}),
        'queryable': bool(live),
        'resources': len(resources),
        'topics': pkg.get('topics') or '',
    }


def inventory(role: Optional[str] = None, live: bool = True) -> dict:
    """
    The catalogue, checked against the portal.

    ``live=False`` skips the portal round-trip — useful when all you want is
    what tdot claims to know about, not whether the city moved it this week.
    """
    def build() -> List[dict]:
        out = []
        for item in CATALOGUE:
            entry = _entry(item)
            if entry['package']:
                entry['portal_status'] = _probe(entry['package'])
            out.append(entry)
        return out

    entries = (S.cached('housing-inventory', INVENTORY_TTL, build) if live
               else [_entry(i) for i in CATALOGUE])

    if role:
        if role not in ROLES:
            raise KeyError(f'unknown role {role!r}; known: {list(ROLES)}')
        entries = [e for e in entries if e['role'] == role]

    counts = {r: sum(1 for e in entries if e['role'] == r) for r in ROLES}
    missing = [e['title'] for e in entries
               if e.get('portal_status') and not e['portal_status']['available']]
    return {
        'datasets': entries,
        'count': len(entries),
        'by_role': counts,
        'roles': {
            'layer': 'drawn on the map now',
            'feature': 'not a layer, but an input to the score model',
            'table': 'open and about housing, with no geography to draw',
            'closed': 'what people ask for that is not open data',
        },
        'unavailable': missing,
        'checked': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()) if live else None,
        'portals': [
            {'name': 'Toronto Open Data', 'url': 'https://open.toronto.ca'},
            {'name': 'Toronto Police Service open data',
             'url': 'https://data.torontopolice.on.ca'},
        ],
    }


def search(q: str, rows: int = 20) -> List[dict]:
    """
    Housing datasets on the portal that tdot has *not* catalogued.

    The catalogue above is curated and will fall behind. This is the escape
    hatch: anything it turns up can be added as a layer from the browser
    (see :mod:`tdotgis.registry`) without touching this file.
    """
    known = {i['package'] for i in CATALOGUE if i.get('package')}
    out = []
    for hit in S.cached(f'housing-search-{q.lower().strip()}-{rows}', S.DAY,
                        lambda: _search(q, rows)):
        if hit['package'] not in known:
            out.append(hit)
    return out


def _search(q: str, rows: int) -> List[dict]:
    d = S._get(f'{S.CKAN}/api/3/action/package_search', q=q, rows=int(rows)).json()
    return [{
        'package': p['name'],
        'title': p.get('title') or p['name'],
        'notes': (p.get('notes') or '')[:300],
        'formats': sorted({(r.get('format') or '').upper()
                           for r in p.get('resources') or [] if r.get('format')}),
        'queryable': any(r.get('datastore_active') for r in p.get('resources') or []),
        'url': f'https://open.toronto.ca/dataset/{p["name"]}/',
    } for p in d['result']['results']]
