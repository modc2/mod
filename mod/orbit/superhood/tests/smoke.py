"""Headless smoke test for SUPER HOOD BROS.

Boots the game in Chromium, plays it for a while with synthetic key presses
and asserts that nothing throws, that Sal actually moves, that the levels all
build, and that the title screen and each course render.

    python3 tests/smoke.py [--shots]      # --shots writes PNGs to tests/shots/
"""

import argparse
import os
import sys
import threading
from http.server import HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import importlib.util

spec = importlib.util.spec_from_file_location('sm_serve', os.path.join(ROOT, 'serve.py'))
sm_serve = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sm_serve)


def start_server():
    srv = HTTPServer(('127.0.0.1', 0), sm_serve.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f'http://127.0.0.1:{srv.server_port}/index.html'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--shots', action='store_true', help='save screenshots')
    ap.add_argument('--headed', action='store_true')
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    srv, url = start_server()
    shots = os.path.join(HERE, 'shots')
    if args.shots:
        os.makedirs(shots, exist_ok=True)

    failures, errors = [], []

    def check(name, cond, detail=''):
        (print if cond else failures.append)(
            f'{"ok  " if cond else "FAIL"} {name} {detail}'.rstrip())
        if not cond:
            print(f'FAIL {name} {detail}')

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed,
                                     args=['--autoplay-policy=no-user-gesture-required'])
        page = browser.new_page(viewport={'width': 900, 'height': 760})
        page.on('console', lambda msg: errors.append(msg.text)
                if msg.type == 'error' else None)
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(url)
        page.wait_for_function('window.__sm !== undefined', timeout=10000)

        check('boots', page.evaluate('!!window.__sm'))
        check('title screen', page.evaluate('__sm.state().mode') == 'title')
        if args.shots:
            page.locator('#screen').screenshot(path=os.path.join(shots, '00-title.png'))

        # every level builds in the browser too
        built = page.evaluate("""() => {
            const out = {};
            for (const id of __sm.levels()) {
              try { const L = LEVELS.make(id); out[id] = L.w; }
              catch (e) { out[id] = 'ERR ' + e.message; }
            }
            return out;
        }""")
        check('levels build', all(isinstance(v, int) for v in built.values()), str(built))

        # start the game
        page.keyboard.press('Enter')
        page.wait_for_timeout(400)
        st = page.evaluate('__sm.state()')
        check('game starts', st['mode'] == 'play', st['level'])
        check('sal exists', st['player'] is not None)

        # walk right for three seconds, jumping
        x0 = page.evaluate('__sm.state().player.x')
        page.keyboard.down('ArrowRight')
        page.keyboard.down('ShiftLeft')
        for _ in range(6):
            page.wait_for_timeout(320)
            page.keyboard.press('KeyZ')
        page.wait_for_timeout(600)
        page.keyboard.up('ShiftLeft')
        page.keyboard.up('ArrowRight')
        st = page.evaluate('__sm.state()')
        check('sal advances', st['player']['x'] > x0 + 200,
              f"{x0:.0f} -> {st['player']['x']:.0f}")
        check('still playing', st['mode'] in ('play', 'clear', 'dying'), st['mode'])
        check('clock runs', st['time'] < 400, str(st['time']))
        if args.shots:
            page.locator('#screen').screenshot(path=os.path.join(shots, '01-park-slope.png'))

        # a pigeon met head-on costs a life; stomped, it pays 100
        stomp = page.evaluate("""() => {
            const g = __sm.game;
            g.loadLevel('1-1');
            const p = g.player;
            p.x = 24 * 16; p.y = 9 * 16; p.vy = 2;
            g.spawnAhead();
            const before = g.score;
            for (let i = 0; i < 40; i++) g.update();
            const foe = g.ents.find(e => e.kind === 'pigeon');
            return { gained: g.score - before, mode: g.mode,
                     flat: foe ? !!foe.dead : null };
        }""")
        check('stomping a pigeon scores', stomp['gained'] >= 100, str(stomp))
        check('stomp is survivable', stomp['mode'] == 'play', str(stomp))

        # the crates and the enemies actually do something
        interacted = page.evaluate("""() => {
            const g = __sm.game;
            g.loadLevel('1-1');
            const p = g.player;
            // stand Sal under the first crate on 1-1 and bump it
            p.x = 16 * 16 + 2; p.y = 9 * 16 + 8; p.vy = -3; p.vx = 0;
            const before = g.tokens;
            for (let i = 0; i < 30; i++) g.update();
            return { tokens: g.tokens - before, mode: g.mode };
        }""")
        check('bumping a crate pays out', interacted['tokens'] >= 1, str(interacted))

        # power up, then check the sprite state machine survives every pose
        poses = page.evaluate("""() => {
            const g = __sm.game, p = g.player;
            p.big = true; p.w = 12; p.h = 24; p.fire = true;
            const seen = [];
            for (const s of ['idle','walk1','walk2','walk3','jump','skid','duck','dead']) {
              seen.push(!!(SPR.salBig[0][s] && SPR.salSmall[0][s === 'duck' ? 'idle' : s]));
            }
            g.throwCap();
            for (let i = 0; i < 20; i++) g.update();
            return { poses: seen.every(Boolean), caps: g.caps.length };
        }""")
        check('all poses exist', poses['poses'])
        check('bottle caps fly', poses['caps'] >= 1, str(poses['caps']))

        # the subway entrance on 1-1 warps down to the cellar and back
        warp = page.evaluate("""() => {
            const g = __sm.game;
            g.loadLevel('1-1');
            const w = g.level.warps[0];
            const p = g.player;
            p.x = w.x * 16 + 6; p.y = w.y * 16 - p.h; p.vy = 0; p.onGround = true;
            __sm.press('down');
            for (let i = 0; i < 90; i++) g.update();
            __sm.release('down');
            const down = g.levelId;
            // ride the exit pipe back up
            const e = g.level.exitPipe;
            const q = g.player;
            q.x = e.x * 16 + 6; q.y = e.y * 16 - q.h; q.vy = 0; q.onGround = true;
            __sm.press('down');
            for (let i = 0; i < 90; i++) g.update();
            __sm.release('down');
            return { down, back: g.levelId, x: Math.round(g.player.x / 16) };
        }""")
        check('subway warps to the cellar', warp['down'] == 'cellar', str(warp))
        check('and comes back up on 1-1', warp['back'] == '1-1' and warp['x'] > 100,
              str(warp))

        # each course loads and renders
        for lid in ('1-2', '1-3', 'cellar'):
            page.evaluate(f'__sm.goto("{lid}")')
            page.wait_for_timeout(500)
            st = page.evaluate('__sm.state()')
            check(f'course {lid}', st['mode'] == 'play' and st['level'] == lid, str(st['level']))
            if args.shots:
                page.locator('#screen').screenshot(
                    path=os.path.join(shots, f'02-{lid}.png'))

        # the boss shows up when you get near him
        boss = page.evaluate("""() => {
            const g = __sm.game;
            g.loadLevel('1-3');
            g.player.x = 170 * 16; g.player.y = 10 * 16;
            g.cam.x = 165 * 16;
            for (let i = 0; i < 240; i++) g.update();
            return { boss: g.boss ? g.boss.hp : null, mode: g.mode };
        }""")
        check('the landlord appears', boss['boss'] is not None, str(boss))
        if args.shots:
            page.locator('#screen').screenshot(path=os.path.join(shots, '03-boss.png'))

        # ... and can be beaten, which opens the gate
        beaten = page.evaluate("""() => {
            const g = __sm.game;
            if (!g.boss) return { ok: false };
            for (let i = 0; i < 8 && g.boss && !g.boss.dead; i++) {
              g.boss.hurt = 0; g.hitBoss(g.boss);
            }
            const gate = g.level.gate;
            const cleared = gate ? g.tileAt(gate.x, gate.y0) === ' ' : false;
            for (let i = 0; i < 60; i++) g.update();
            return { ok: g.bossDead, gate: cleared, mode: g.mode };
        }""")
        check('landlord goes down', beaten.get('ok') is True, str(beaten))
        check('gate opens', beaten.get('gate') is True, str(beaten))

        # finishing a course advances the world
        cleared = page.evaluate("""() => {
            const g = __sm.game;
            g.loadLevel('1-1');
            g.player.x = (g.level.goalX * 16) - 2;
            g.update();
            const clearMode = g.mode;
            for (let i = 0; i < 300; i++) g.update();
            return { clearMode, level: g.levelId, mode: g.mode };
        }""")
        check('touching the pole clears', cleared['clearMode'] == 'clear', str(cleared))
        check('advances to 1-2', cleared['level'] == '1-2', str(cleared))

        if args.shots:
            # big, starred-up Sal at the lamppost, mid-slide
            page.evaluate("""() => {
                const g = __sm.game;
                g.loadLevel('1-1');
                const p = g.player;
                p.big = true; p.fire = true; p.w = 12; p.h = 24; p.star = 300;
                p.x = (g.level.goalX - 3) * 16; p.y = 10 * 16;
                g.cam.x = (g.level.goalX - 7) * 16;
                for (let i = 0; i < 4; i++) g.update();
                g.startClear();
                for (let i = 0; i < 30; i++) g.update();
            }""")
            page.wait_for_timeout(120)
            page.locator('#screen').screenshot(path=os.path.join(shots, '04-goal.png'))

            page.evaluate("""() => {
                const g = __sm.game;
                g.loadLevel('1-1');
                const p = g.player;
                p.big = true; p.fire = true; p.w = 12; p.h = 24;
                p.x = 111 * 16; p.y = 10 * 16;
                g.cam.x = 106 * 16;
                for (let i = 0; i < 8; i++) g.update();
            }""")
            page.wait_for_timeout(120)
            page.locator('#screen').screenshot(path=os.path.join(shots, '05-big-sal.png'))

        # the controls: nothing the player asks for should get dropped
        page.evaluate('__sm.game.loadLevel("1-1")')
        feel = page.evaluate("""() => {
            const g = __sm.game;
            // drop him on the floor of 1-1 and hand back a settled player
            const settle = () => {
              g.loadLevel('1-1');
              __sm.clear();
              const p = g.player;
              for (let i = 0; i < 40 && !p.onGround; i++) g.update();
              return p;
            };
            // a tap that starts and ends between two frames still moves him
            const p = settle();
            p.vx = 0;
            __sm.press('right'); __sm.release('right');
            g.update();
            const tap = p.vx;
            // a jump asked for in mid-air fires on landing (buffered press)
            const q = settle();
            q.y -= 4; q.vy = 1; q.onGround = false;
            __sm.press('jump'); __sm.release('jump');
            let buffered = false;
            for (let i = 0; i < 6; i++) { g.update(); if (q.vy < -3) buffered = true; }
            // and one asked for just after walking off an edge (coyote frames)
            const r = settle();
            r.onGround = false; r.vy = 1;      // stepped off, still falling slow
            __sm.press('jump'); __sm.release('jump');
            g.update();
            return { tap, buffered, coyote: r.vy < -3 };
        }""")
        check('a sub-frame tap registers', feel['tap'] > 0, str(feel['tap']))
        check('a jump before landing is buffered', feel['buffered'], str(feel))
        check('a jump after the edge still counts', feel['coyote'], str(feel))

        # the on-screen key hints are live buttons
        page.evaluate('__sm.game.loadLevel("1-1")')
        page.wait_for_timeout(60)
        x0 = page.evaluate('__sm.state().player.x')
        box = page.locator('footer kbd[data-key="right"]').bounding_box()
        page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
        page.mouse.down()
        page.wait_for_timeout(400)
        lit = page.evaluate(
            '!!document.querySelector(\'footer kbd[data-key="right"]\').classList.contains("on")')
        moved = page.evaluate('__sm.state().player.x')
        page.mouse.up()
        page.wait_for_timeout(60)
        check('holding a key hint walks', moved > x0 + 10, f'{x0:.0f} -> {moved:.0f}')
        check('a held button lights up', lit)
        check('releasing lets go', not page.evaluate(
            '!!document.querySelector(\'footer kbd[data-key="right"]\').classList.contains("on")'))

        # running a long time must not throw
        page.evaluate('__sm.game.loadLevel("1-2")')
        page.evaluate("""() => { const g = __sm.game;
            for (let i = 0; i < 1800; i++) g.update(); }""")
        check('1800 frames of 1-2 survive', True)

        real = [e for e in errors if 'favicon' not in e.lower()]
        check('no console errors', not real, '; '.join(real[:3]))
        browser.close()

    srv.shutdown()
    print()
    if failures:
        print(f'{len(failures)} check(s) failed')
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
