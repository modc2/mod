#!/usr/bin/env python3
"""Screenshot the venice frontend in every display mode.

Signed-out hero for each theme, plus the signed-in shell (a seeded, local-only
conversation) for a few. Writes PNGs to /tmp/venice-shots/.

    python3 scripts/shots.py [theme ...]
"""
import json
import os
import sys

import mod as m
from playwright.sync_api import sync_playwright

APP = os.environ.get("VENICE_APP_URL", "http://localhost:3880/venice")
OUT = os.environ.get("VENICE_SHOT_DIR", "/tmp/venice-shots")
ALL = [
    "arcade", "atelier", "noir", "lagoon", "commodore", "vapor", "matrix",
    "velvet", "paper", "gameboy", "bloom",
]
THEMES = sys.argv[1:] or ALL
SHELL_THEMES = {"arcade", "paper", "gameboy", "velvet", "matrix"}

os.makedirs(OUT, exist_ok=True)

auth = m.mod("auth")(key="test.venice", crypto_type="ecdsa")
token = auth.token(data={"scope": "venice"})
addr = auth.key.address

CONVO = json.dumps([
    {
        "id": "demo1",
        "title": "a Murano-glass koi, then upscale it",
        "updated": 1,
        "thread": [
            {"role": "user", "text": "a Murano-glass koi swimming over a canal at dusk", "media": []},
            {"role": "assistant",
             "text": "Blown a koi in Murano glass — amber body, cobalt fins, lit from inside.\n"
                     "Say the word and I'll upscale it 2× or set it swimming as a 5s clip.",
             "media": []},
            {"role": "user", "text": "upscale it 2x", "media": []},
        ],
    },
    {"id": "demo2", "title": "melting clock over the lagoon", "updated": 0, "thread": []},
])

errors = []
with sync_playwright() as p:
    browser = p.chromium.launch()
    for theme in THEMES:
        for kind in ("hero", "shell"):
            if kind == "shell" and theme not in SHELL_THEMES:
                continue
            ctx = browser.new_context(viewport={"width": 1280, "height": 900},
                                      device_scale_factor=2)
            script = f"window.localStorage.setItem('venice:theme', {json.dumps(theme)});"
            if kind == "shell":
                script += (
                    f"window.localStorage.setItem('venice:token', {json.dumps(token)});"
                    f"window.localStorage.setItem('venice:addr', {json.dumps(addr.lower())});"
                    f"window.localStorage.setItem('venice:idkind', 'local');"
                    f"window.localStorage.setItem('venice:convos:{addr.lower()}', {json.dumps(CONVO)});"
                )
            ctx.add_init_script(script)
            page = ctx.new_page()
            page.on("pageerror", lambda e, t=theme: errors.append(f"{t}: {e}"))
            page.goto(APP, wait_until="networkidle")
            page.wait_for_timeout(1200)
            path = f"{OUT}/{theme}-{kind}.png"
            page.screenshot(path=path)
            print("wrote", path)
            # …and once per theme, the palette list itself, opened.
            if kind == "hero":
                page.click(".tp-btn")
                page.wait_for_timeout(400)
                path = f"{OUT}/{theme}-picker.png"
                page.screenshot(path=path)
                print("wrote", path)
            ctx.close()
    browser.close()

if errors:
    print("PAGE ERRORS:")
    for e in errors:
        print(" ", e)
    sys.exit(1)
print("ok")
