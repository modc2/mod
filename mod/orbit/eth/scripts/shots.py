#!/usr/bin/env python3
"""Screenshot the eth console — every tab, signed in, both themes.

Signing in is done by planting a real protocol token in localStorage before the
page loads: the same envelope a wallet would produce, so the console takes the
identical path it takes for a visitor and the shots show real accounts, real
balances and a real compile rather than a mock.

    python3 scripts/shots.py            # → /tmp/eth-shots/
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# `import mod` from in here would find this module's own mod.py — protocol.py
# is the module that knows to take these directories off the path first.
from protocol import auth  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

APP = os.environ.get('ETH_APP_URL', 'http://127.0.0.1:50731/eth/')
OUT = Path(os.environ.get('ETH_SHOT_DIR', '/tmp/eth-shots'))
OUT.mkdir(parents=True, exist_ok=True)

TOKEN = auth().token({'mod': 'eth'})
TABS = ['pane-build', 'pane-overview', 'pane-accounts', 'pane-contracts',
        'pane-explorer', 'pane-agents']


def shot(browser, tab, theme, token=None, label=None):
    # The bench is three columns; shooting it at reading width would show the
    # stacked layout, which is not what anyone opens it at.
    width = 1500 if tab == 'pane-build' else 1180
    page = browser.new_page(viewport={'width': width, 'height': 1000})
    page.add_init_script(f"""
        try {{
            localStorage.setItem('eth.theme', {theme!r});
            localStorage.setItem('eth.network', 'local');
            {f"localStorage.setItem('eth.token', {token!r});" if token else
             "localStorage.removeItem('eth.token');"}
        }} catch (e) {{}}
    """)
    page.goto(APP, wait_until='networkidle')
    page.wait_for_timeout(1200)
    # Always click, even for the tab that is already on: build is the default
    # now, and a condition that names one tab is a condition that goes stale.
    page.click(f'[data-pane="{tab}"]')
    page.wait_for_timeout(900)
    if tab == 'pane-contracts' and label == 'interact':
        # Open the first contract so the shot shows the ABI-generated forms,
        # which is the part of this console that is hard to describe in words.
        page.click('#contracts [data-open]')
        page.wait_for_timeout(1500)
    if tab == 'pane-build':
        page.click('#b-compile')
        page.wait_for_timeout(3000)
        if label == 'tests':
            page.click('[data-res="res-tests"]')
            page.click('#suite-generate')
            page.wait_for_timeout(3000)
        elif label == 'share':
            page.click('[data-res="res-share"]')
            page.wait_for_timeout(500)
    name = label or tab.replace('pane-', '')
    path = OUT / f'{name}-{theme}.png'
    page.screenshot(path=str(path), full_page=True)
    page.close()
    print(path, flush=True)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for theme in ('dark', 'light'):
            for tab in TABS:
                shot(browser, tab, theme, TOKEN)
        for theme in ('dark', 'light'):
            shot(browser, 'pane-contracts', theme, TOKEN, label='interact')
            shot(browser, 'pane-build', theme, TOKEN, label='tests')
            shot(browser, 'pane-build', theme, TOKEN, label='share')
        shot(browser, 'pane-build', 'dark', None, label='signed-out')
        browser.close()


if __name__ == '__main__':
    main()
