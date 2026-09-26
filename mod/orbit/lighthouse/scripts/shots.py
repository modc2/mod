#!/usr/bin/env python3
"""Screenshot the lighthouse console, signed out and signed in, both themes.

Signing in is done by planting a real protocol token in localStorage before the
page loads — the same envelope a wallet would produce, so the console takes the
identical path it takes for a visitor and the shot shows the real store link
(whitelist, terms, quota) rather than a mock.

    python3 scripts/shots.py            # → /tmp/lighthouse-shots/
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# `import mod` from in here would find this module's own mod.py — protocol.py
# is the module that knows to take these directories off the path first.
from protocol import auth  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

APP = os.environ.get('LIGHTHOUSE_APP_URL', 'http://127.0.0.1:50681/lighthouse/')
OUT = Path(os.environ.get('LIGHTHOUSE_SHOT_DIR', '/tmp/lighthouse-shots'))
OUT.mkdir(parents=True, exist_ok=True)

TOKEN = auth().token({'mod': 'lighthouse'})


def shot(page, name, theme, token=None):
    page.add_init_script(f"""
        try {{
            localStorage.setItem('lighthouse.theme', {theme!r});
            {f"localStorage.setItem('lighthouse.token', {token!r});" if token else
             "localStorage.removeItem('lighthouse.token');"}
        }} catch (e) {{}}
    """)
    page.goto(APP, wait_until='networkidle')
    page.wait_for_timeout(1200)
    path = OUT / f'{name}-{theme}.png'
    page.screenshot(path=str(path), full_page=True)
    print(path, flush=True)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for theme in ('dark', 'light'):
            for name, token in (('out', None), ('in', TOKEN)):
                page = browser.new_page(viewport={'width': 1180, 'height': 1000})
                shot(page, name, theme, token)
                page.close()
        browser.close()


if __name__ == '__main__':
    main()
