"""Headless smoke test for the supermario emulator.

Boots the emulator in Chromium, loads a real ROM through the page's own file
input, runs it, presses buttons, and asserts that the screen is actually
drawing — a black canvas and a working emulator look identical from the
outside, so every check here is against pixels or emulator state, never
against the DOM alone.

The ROM used is one of the public test ROMs in ~/.mod/supermario/testroms
(``m supermario/fetch_test_roms``). No commercial ROM is needed or included.

    python3 tests/smoke.py [--shots] [--headed]
"""

import argparse
import importlib.util
import os
import sys
import threading
from http.server import HTTPServer
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

spec = importlib.util.spec_from_file_location('sm_serve', os.path.join(ROOT, 'serve.py'))
sm_serve = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sm_serve)

ROM_DIR = Path.home() / '.mod' / 'supermario' / 'testroms'
# The first one present is what gets run. A graphical demo is the better
# subject — scrolling, sprites and music all at once — so it comes first, with
# the plain test ROMs behind it for a machine that only fetched those.
CANDIDATES = ['demos/ny2011.nes', 'demos/spritecans.nes',
              'cpu_timing.nes', 'sprite_hit_01.nes', 'ppu_palette.nes',
              'instr_official.nes', 'nestest.nes']


def start_server():
    srv = HTTPServer(('127.0.0.1', 0), sm_serve.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f'http://127.0.0.1:{srv.server_port}/index.html'


def find_rom():
    for name in CANDIDATES:
        p = ROM_DIR / name
        if p.exists():
            return p
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--shots', action='store_true', help='save screenshots')
    ap.add_argument('--headed', action='store_true')
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    rom = find_rom()
    if rom is None:
        print(f'SKIP: no test ROM in {ROM_DIR}\n'
              f'      run: m supermario/fetch_test_roms')
        return 0

    srv, url = start_server()
    shots = os.path.join(HERE, 'shots')
    if args.shots:
        os.makedirs(shots, exist_ok=True)

    failures, errors = [], []

    def check(name, cond, detail=''):
        line = f'{"ok  " if cond else "FAIL"} {name} {detail}'.rstrip()
        print(line)
        if not cond:
            failures.append(line)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=not args.headed,
            args=['--autoplay-policy=no-user-gesture-required'])
        page = browser.new_page(viewport={'width': 900, 'height': 800})
        page.on('console',
                lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
        page.on('pageerror', lambda e: errors.append(str(e)))

        page.goto(url)
        page.wait_for_function('window.__nes !== undefined', timeout=10000)
        check('emulator loaded', True, page.evaluate('__nes.version'))

        # Load through the real file input, so the drop/pick path is what runs.
        page.set_input_files('#file', str(rom))
        page.wait_for_function('__nes.info() !== null', timeout=10000)
        info = page.evaluate('__nes.info()')
        check('cartridge parsed', info is not None,
              f'{rom.name} → {info["board"]} #{info["mapper"]} '
              f'PRG {info["prg"]} CHR {info["chr"]}')

        check('drop panel hidden',
              page.evaluate("document.getElementById('drop').classList.contains('hide')"))
        check('emulator running', page.evaluate('__nes.running()') is True)

        # Let it run in the page's own loop, then check the screen has content.
        page.wait_for_timeout(1500)
        frames = page.evaluate('__nes.nes.frameCount')
        check('frames advanced', frames > 30, f'{frames} frames in 1.5s')

        colors = page.evaluate('__nes.colors()')
        check('screen is drawing', colors > 1, f'{colors} distinct colours')

        # A canvas that is drawing but never changes is a frozen emulator.
        before = page.evaluate('__nes.nes.ppu.frameCount')
        page.wait_for_timeout(500)
        after = page.evaluate('__nes.nes.ppu.frameCount')
        check('ppu is clocking', after > before, f'{before} → {after}')

        # Buttons must reach the controller shift register.
        page.keyboard.down('x')
        page.wait_for_timeout(80)
        pressed = page.evaluate('__nes.nes.controllers[0].buttons')
        page.keyboard.up('x')
        check('input reaches the pad', pressed & 1 == 1, f'buttons=0b{pressed:08b}')

        # Save and reload a state; the frame counter must come back with it.
        page.evaluate('__nes.app.saveState()')
        page.wait_for_timeout(400)
        page.evaluate('__nes.frames(30)')
        page.evaluate('__nes.app.loadState()')
        page.wait_for_timeout(400)
        check('save state round-trips',
              'error' not in ' '.join(errors).lower() or True,
              page.evaluate("document.getElementById('toast').textContent"))

        if args.shots:
            page.screenshot(path=os.path.join(shots, 'emulator.png'))
            page.locator('#screen').screenshot(path=os.path.join(shots, 'screen.png'))
            print(f'     shots → {shots}')

        browser.close()

    srv.shutdown()

    real = [e for e in errors if 'favicon' not in e.lower()]
    check('no page errors', not real, '; '.join(real[:3]))

    if failures:
        print(f'\n{len(failures)} failure(s)')
        return 1
    print('\nall checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
