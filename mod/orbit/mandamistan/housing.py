"""mandamistan — the data core.

Everything the module serves lives in this file: the crisis numbers, the
plan, the timeline of what has actually happened, the honest critiques, the
sources, and one small transparent model (`simulate`) that projects the
affordable stock and tenant savings under adjustable assumptions.

All figures are sourced (see SOURCES); the model is a toy with its
assumptions in the open, not a forecast. Updated 2026-10-07 — the first
Mamdani rent freeze took effect six days ago.
"""

# ── the crisis ───────────────────────────────────────────────────

CRISIS = {
    'as_of': '2026-10-07',
    'stats': [
        {'id': 'vacancy', 'label': 'Net rental vacancy rate',
         'value': 1.41, 'unit': '%',
         'note': 'Lowest since 1968. A healthy market is ~5%.',
         'source': '2023 NYC Housing & Vacancy Survey'},
        {'id': 'vacancy_low', 'label': 'Vacancy, units under $1,100/mo',
         'value': 0.39, 'unit': '%',
         'note': 'Affordable apartments effectively do not come open.',
         'source': '2023 NYC HVS'},
        {'id': 'rent_burden', 'label': 'Renters paying 30%+ of income',
         'value': 51.6, 'unit': '%',
         'note': 'More than half the city is rent-burdened.',
         'source': 'NYC rent burden data, 2024'},
        {'id': 'severe_burden', 'label': 'Renters paying 50%+ of income',
         'value': 28.8, 'unit': '%',
         'note': 'For extremely-low-income households it is 74%.',
         'source': 'Coalition for the Homeless, 2024'},
        {'id': 'shelter', 'label': 'People sleeping in shelters nightly',
         'value': 100437, 'unit': 'people',
         'note': 'Including 33,217 children (Jan 2026).',
         'source': 'Coalition for the Homeless, Jan 2026'},
        {'id': 'stabilized', 'label': 'Rent-stabilized apartments',
         'value': 1000000, 'unit': 'homes',
         'note': 'Nearly half the rental stock — all frozen as of Oct 1, 2026.',
         'source': 'NYC Rent Guidelines Board'},
        {'id': 'gap', 'label': 'Affordable units per 100 poorest households',
         'value': 32, 'unit': 'units',
         'note': 'For every 100 extremely-low-income households in the metro.',
         'source': 'NLIHC gap analysis'},
    ],
    # Net rental vacancy by asking rent tier, 2023 HVS.
    'vacancy_by_rent': [
        {'tier': 'under $1,100', 'rate': 0.39},
        {'tier': '$1,100–$1,649', 'rate': 0.88},
        {'tier': '$1,650–$2,399', 'rate': 1.41},
        {'tier': '$2,400 and up', 'rate': 3.39},
    ],
}

# ── the plan ─────────────────────────────────────────────────────

PLAN = {
    'name': 'Block by Block: The Housing Plan for a New Era',
    'released': '2026-05',
    'headline': ('Freeze the rent for a million stabilized homes, build '
                 '200,000 new permanently-affordable union-built homes and '
                 'preserve 200,000 more over ten years, rebuild NYCHA, and '
                 'rezone the city so housing can actually get built.'),
    'pillars': [
        {'id': 'freeze', 'name': 'Freeze the rent', 'icon': 'F',
         'what': ('Appoint a Rent Guidelines Board that stops rent hikes on '
                  '~1,000,000 rent-stabilized apartments while wages catch up.'),
         'how': ('The mayor appoints all nine RGB members. Done: the board '
                 'voted 7–1 in June 2026 to freeze one-year AND two-year '
                 'leases — the first two-year freeze in city history. In '
                 'effect Oct 1, 2026 → Sep 30, 2027.'),
         'status': 'delivered'},
        {'id': 'build', 'name': 'Build 200,000', 'icon': 'B',
         'what': ('200,000 new publicly-financed, permanently affordable, '
                  'rent-stabilized, union-built homes over 10 years — '
                  'tripling the city’s subsidized production rate.'),
         'how': ('$22B city capital over the first five years, new tools '
                 '(revolving loan fund, city-backed insurance), and city '
                 'land. Targeted at households around the median income.'),
         'status': 'in_progress'},
        {'id': 'preserve', 'name': 'Preserve 200,000', 'icon': 'P',
         'what': ('Keep 200,000 existing affordable homes from falling out '
                  'of the stock — distress, deregulation, decay.'),
         'how': ('Preservation financing, lower operating costs (insurance), '
                 'code enforcement with teeth, and closing deregulation '
                 'loopholes with Albany.'),
         'status': 'in_progress'},
        {'id': 'nycha', 'name': 'Rebuild NYCHA', 'icon': 'N',
         'what': ('One of the largest city capital commitments to public '
                  'housing in recent history: repairs, upgrades, and NYCHA '
                  'as a developer of new homes.'),
         'how': ('Capital funding plus tenant decision-making power; new '
                 'financing and development tools for the authority itself.'),
         'status': 'in_progress'},
        {'id': 'zoning', 'name': 'Unlock the land', 'icon': 'Z',
         'what': ('Rezone for abundance: transit-oriented development, '
                  'accessory dwelling units, faster timelines, upzoning '
                  'wealthier neighborhoods that have built nothing.'),
         'how': ('City-led rezonings and land-use reform so projects pencil '
                 'and permits move — the market-shaped half of the plan.'),
         'status': 'in_progress'},
        {'id': 'tenants', 'name': 'Tenant power', 'icon': 'T',
         'what': ('Tenants who know their rights and a city that enforces '
                  'them: anti-harassment, code enforcement, homelessness '
                  'prevention before the shelter door.'),
         'how': ('More inspectors, right-to-counsel expansion, and state '
                 'legislation extending protections to smaller buildings.'),
         'status': 'in_progress'},
    ],
}

TIMELINE = [
    {'date': '2025-02', 'event': 'Campaign proposes 200,000 city-financed affordable homes.', 'status': 'done'},
    {'date': '2025-11', 'event': 'Mamdani wins the mayoralty on an affordability mandate.', 'status': 'done'},
    {'date': '2026-01', 'event': 'Takes office; begins appointing a new Rent Guidelines Board.', 'status': 'done'},
    {'date': '2026-05', 'event': '"Block by Block" housing plan released: 200k new + 200k preserved, $22B/5yr.', 'status': 'done'},
    {'date': '2026-06', 'event': 'RGB votes 7–1 to freeze stabilized rents — first-ever two-year-lease freeze.', 'status': 'done'},
    {'date': '2026-10', 'event': 'Freeze takes effect for ~1M apartments (Oct 1, 2026 → Sep 30, 2027).', 'status': 'done'},
    {'date': '2027+', 'event': 'Build-out: rezonings, NYCHA capital, Albany fights, 200k units.', 'status': 'next'},
]

# ── the fight (honest critiques, and the counters) ───────────────

CRITIQUES = [
    {'claim': ('The money is not there: ~$70B of extra borrowing would blow '
               'past the statutory debt limit and push debt service from '
               '~12% to ~18% of city tax revenue.'),
     'who': 'Step Two Policy Project',
     'counter': ('The enacted plan is $22B over five years inside the capital '
                 'budget, plus leverage: a revolving loan fund, city-backed '
                 'insurance, state and federal match, and debt-limit relief '
                 'from Albany. The $100B figure was the campaign ceiling, '
                 'not the appropriation.')},
    {'claim': ('A rent freeze starves stabilized buildings of maintenance '
               'money and accelerates decay — affordability on paper, '
               'dilapidation in practice.'),
     'who': 'Reason / landlord groups',
     'counter': ('The freeze is paired with operating-cost relief (insurance, '
                 'financing) and a distressed-building carve-out; the bet is '
                 'that costs, not revenue, are the lever. Watch repair '
                 'violations per 1,000 stabilized units to see who is right.')},
    {'claim': ('Subsidized units at ~$500k each cannot close a shortage of '
               'hundreds of thousands of homes; only market supply at scale '
               'can.'),
     'who': 'Manhattan Institute',
     'counter': ('The plan is deliberately both: public money for homes the '
                 'market will never build, and rezoning/TOD/ADUs so private '
                 'supply grows too. The YIMBY half is real — new permits are '
                 'the metric to watch.')},
]

SOURCES = [
    {'title': 'NYC Mayor’s Office — "Block by Block" release transcript (May 2026)',
     'url': 'https://www.nyc.gov/mayors-office/news/2026/05/transcript--mayor-mamdani-releases--block-by-block--the-housing-'},
    {'title': 'NYC Mayor’s Office — statement on the RGB final vote (June 2026)',
     'url': 'https://www.nyc.gov/mayors-office/news/2026/06/mayor-mamdani-s-statement-on-the-rent-guidelines-board-s-final-v'},
    {'title': 'CNN — Rent board approves freeze on 1M apartments (June 2026)',
     'url': 'https://www.cnn.com/2026/06/25/us/new-york-city-rent-board-approves-freeze'},
    {'title': 'TIME — What to know about the approved rent freeze',
     'url': 'https://time.com/article/2026/06/26/new-york-rent-freeze-stabilized-apartments-zohran-mamdani-housing-promise/'},
    {'title': 'New York YIMBY — Block by Block production agenda',
     'url': 'https://newyorkyimby.com/2026/05/mayor-mamdani-outlines-block-by-block-plan-for-affordable-housing-construction.html'},
    {'title': 'PoliticsNY — campaign 200,000-home proposal (Feb 2025)',
     'url': 'https://politicsny.com/2025/02/03/exclusive-mayoral-hopeful-mamdani-proposes-building-200000-new-affordable-homes-with-city-dollars/'},
    {'title': 'Step Two Policy Project — the financing critique',
     'url': 'https://www.steptwopolicy.org/post/what-s-missing-in-the-mamdani-housing-plan'},
    {'title': 'Reason — the rent-freeze critique',
     'url': 'https://reason.com/2026/06/26/mamdani-got-his-rent-freeze-wish-dont-expect-new-york-city-housing-to-become-more-affordable/'},
    {'title': 'Coalition for the Homeless — shelter census & rent-burden data',
     'url': 'https://www.coalitionforthehomeless.org/'},
    {'title': 'CBIZ — how the 2026 policies reshape NYC real estate',
     'url': 'https://www.cbiz.com/insights/article/how-mamdanis-2026-housing-policies-may-reshape-nyc-real-estate'},
]


# ── the model ────────────────────────────────────────────────────

def simulate(new_per_year=20000, preserved_per_year=20000,
             freeze_years=2, rgb_hike=3.0, attrition_per_year=10000,
             median_stabilized_rent=1500, years=10):
    """Project the affordable stock and tenant savings, plan vs status quo.

    A deliberately small, transparent model — every assumption is an input:

      new_per_year        new affordable homes completed per year (plan: 20k)
      preserved_per_year  existing affordable homes saved per year (plan: 20k)
      freeze_years        years the stabilized freeze holds (then rgb_hike)
      rgb_hike            %/yr rent growth without (and after) the freeze
      attrition_per_year  affordable homes lost yearly to deregulation,
                          distress and decay if nobody intervenes
      median_stabilized_rent  $/mo, for the savings arithmetic
      years               horizon

    Status quo = no new subsidized pipeline beyond replacement (~7k/yr, the
    pre-plan pace), no preservation push, rents rise rgb_hike every year.
    Returns per-year series plus headline totals.
    """
    new_per_year = max(0, int(new_per_year))
    preserved_per_year = max(0, int(preserved_per_year))
    years = max(1, min(int(years), 30))
    freeze_years = max(0, min(int(freeze_years), years))
    rgb_hike = max(0.0, float(rgb_hike))
    attrition = max(0, int(attrition_per_year))
    rent0 = max(1, float(median_stabilized_rent))

    base_pace = 7000          # pre-plan subsidized completions/yr
    stabilized = 1_000_000    # homes under the freeze

    plan_stock, sq_stock = [0], [0]
    plan_rent, sq_rent = [rent0], [rent0]
    savings = 0.0
    for y in range(1, years + 1):
        # stock: net affordable homes added vs the day the plan started
        plan_net = new_per_year + min(preserved_per_year, attrition) - attrition
        plan_stock.append(plan_stock[-1] + plan_net)
        sq_stock.append(sq_stock[-1] + base_pace - attrition)
        # rents: frozen for freeze_years under the plan, compounding otherwise
        pr = plan_rent[-1] * (1 if y <= freeze_years else 1 + rgb_hike / 100)
        sr = sq_rent[-1] * (1 + rgb_hike / 100)
        plan_rent.append(round(pr, 2))
        sq_rent.append(round(sr, 2))
        savings += (sr - pr) * 12 * stabilized

    return {
        'assumptions': {
            'new_per_year': new_per_year, 'preserved_per_year': preserved_per_year,
            'freeze_years': freeze_years, 'rgb_hike_pct': rgb_hike,
            'attrition_per_year': attrition, 'median_stabilized_rent': rent0,
            'status_quo_pace': base_pace, 'stabilized_units': stabilized,
            'years': years,
        },
        'years': list(range(years + 1)),
        'plan_stock': plan_stock, 'status_quo_stock': sq_stock,
        'plan_rent': plan_rent, 'status_quo_rent': sq_rent,
        'headline': {
            'net_affordable_homes_gained': plan_stock[-1],
            'vs_status_quo': plan_stock[-1] - sq_stock[-1],
            'tenant_savings_total_usd': round(savings),
            'monthly_rent_gap_final_usd': round(sq_rent[-1] - plan_rent[-1], 2),
        },
        'disclaimer': ('A transparent toy, not a forecast. Change the inputs '
                       'and see what has to be true for the plan to work.'),
    }
