#!/usr/bin/env python3
"""Headless browser smoke test of the venice frontend.

Injects a valid mod-protocol session (minted exactly like the app's wallet
sign-in), loads the real Next.js page, and drives the authenticated flow:
models load → save a BYOK key → send a chat → SSE renders into the thread.
Catches React/runtime/fetch/SSE breakage that unit tests can't.

Pass VENICE_KEY=vk-... to exercise REAL media generation; otherwise a dummy
key is used and we just confirm the UI handles the (auth-failure) stream.
"""
import json
import os
import sys
import time

import mod as m
from playwright.sync_api import sync_playwright

APP = os.environ.get("VENICE_APP_URL", "http://localhost:3880/venice")
KEY = os.environ.get("VENICE_KEY", "vk-dummy-ui-smoke")
REAL = "VENICE_KEY" in os.environ

auth = m.mod("auth")(key="test.venice", crypto_type="ecdsa")
token = auth.token(data={"scope": "venice"})
addr = auth.key.address
print(f"test wallet: {addr}  real_key={REAL}")

errors = []
with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context()
    ctx.add_init_script(
        f"window.localStorage.setItem('venice:token', {json.dumps(token)});"
        f"window.localStorage.setItem('venice:addr', {json.dumps(addr.lower())});"
    )
    page = ctx.new_page()
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

    page.goto(APP, wait_until="networkidle")
    # Authenticated UI shows the Access panel once /me resolves.
    page.wait_for_selector("text=Access", timeout=20000)
    print("✓ authenticated UI rendered (Access panel)")

    # Model picker populated with tool-capable models.
    page.wait_for_function("document.querySelectorAll('select option').length > 1", timeout=20000)
    n_models = page.eval_on_selector_all("select option", "els => els.length")
    print(f"✓ orchestrator model picker populated: {n_models} models")

    # Save a BYOK key.
    keybox = page.query_selector("input[type=password]")
    if keybox:
        keybox.fill(KEY)
        page.click("text=Save key")
        page.wait_for_selector("text=your key on file", timeout=15000)
        print("✓ BYOK key saved (Access panel shows 'your key on file')")
    else:
        print("• key already on file, skipping save")

    # Send a chat turn and watch the SSE land in the thread.
    page.fill("textarea", "Generate an image of a small red circle on white." if REAL else "say hi")
    page.click("button.send")
    # An assistant bubble appears; with a real key we expect media, else an error.
    try:
        if REAL:
            page.wait_for_selector("video, img.media", timeout=300000)
            kind = "video" if page.query_selector("video") else "image"
            print(f"✓ REAL media rendered inline in chat ({kind})")
        else:
            page.wait_for_selector(".msg.assistant", timeout=30000)
            time.sleep(6)  # let status/error stream in
            txt = page.inner_text(".thread")
            assert "you" in txt.lower()
            print("✓ chat turn streamed into thread (assistant bubble present)")
    except Exception as e:
        errors.append(f"chat flow: {e}")

    page.screenshot(path="/tmp/venice-ui.png", full_page=True)
    print("✓ screenshot → /tmp/venice-ui.png")
    browser.close()

if errors:
    print("\n✗ runtime errors detected:")
    for e in errors[:20]:
        print("  -", e)
    sys.exit(1)
print("\nALL UI CHECKS PASSED")
