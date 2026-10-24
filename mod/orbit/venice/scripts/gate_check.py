#!/usr/bin/env python3
"""Headless check of the no-key composer gate.

A signed-in identity with no BYOK key (and no paid path) used to face a dead
SEND with no explanation. Now: a .gate notice sits above the composer, SEND
stays clickable, and clicking either opens the account menu with the key
field focused.
"""
import json
import os
import sys

import mod as m
from playwright.sync_api import sync_playwright

APP = os.environ.get("VENICE_APP_URL", "http://localhost:9000/venice")

# A fresh identity that has never saved a key.
auth = m.mod("auth")(key="test.venice.nokey", crypto_type="ecdsa")
token = auth.token(data={"scope": "venice"})
addr = auth.key.address
print(f"keyless wallet: {addr}")

errors = []
with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.add_init_script(
        f"window.localStorage.setItem('venice:token', {json.dumps(token)});"
        f"window.localStorage.setItem('venice:addr', {json.dumps(addr.lower())});"
        "window.localStorage.setItem('venice:theme', 'arcade');"
    )
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

    page.goto(APP, wait_until="networkidle")
    page.wait_for_selector(".acct-btn", timeout=20000)

    # 1. The gate notice is visible without any interaction.
    page.wait_for_selector(".gate", timeout=20000)
    print("✓ gate notice visible for keyless identity")

    # 2. SEND is alive even though nothing can be funded.
    page.fill("textarea", "draw a fox")
    disabled = page.eval_on_selector("button.send", "el => el.disabled")
    assert not disabled, "SEND should stay clickable when a key is missing"
    print("✓ SEND stays clickable")

    # 3. Clicking SEND opens the account menu on the key field.
    page.click("button.send")
    page.wait_for_selector(".acct-pop input[type=password]", timeout=10000)
    focused = page.evaluate("document.activeElement?.type === 'password'")
    assert focused, "key input should take focus"
    print("✓ SEND opens the account menu, key field focused")
    page.screenshot(path="/tmp/venice-gate-open.png")
    page.keyboard.press("Escape")

    # 4. The gate CTA does the same.
    page.click(".gate >> text=Add key")
    page.wait_for_selector(".acct-pop input[type=password]", timeout=10000)
    print("✓ gate CTA opens the account menu")
    page.keyboard.press("Escape")
    page.screenshot(path="/tmp/venice-gate.png", full_page=True)
    print("✓ screenshots → /tmp/venice-gate.png, /tmp/venice-gate-open.png")
    browser.close()

if errors:
    print("\n✗ runtime errors:")
    for e in errors[:10]:
        print("  -", e)
    sys.exit(1)
print("\nGATE CHECKS PASSED")
