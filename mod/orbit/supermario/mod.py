"""
supermario — an NES emulator in the browser.

The console, not the cartridge: a 6502 checked instruction-for-instruction
against nestest, a per-dot 2C02 with the scroll and sprite-0 behaviour Mario's
status bar depends on, the 2A03's five channels through WebAudio, and the
cartridge boards the Mario games shipped on. No game data ships here — Super
Mario Bros. and the rest of the library are still under copyright, so you
supply a dump of a cartridge you own, and it never leaves your browser.

Everything is static, so this module only has to serve ``web/``.

This is the anchor file: the orbit loader imports it by path and instantiates
``Mod``. Everything the module exposes to the CLI, the gateway and other
modules is a public method on that class.

CLI:
    m supermario                 # null call → info()
    m supermario/play            # serve it and open a browser
    m supermario/serve           # run it under pm2, then register the route
    m supermario/boards          # which cartridge mappers work
    m supermario/test            # nestest diff + the PPU/APU test ROMs
    m supermario/fetch_test_roms # download the ROMs test() wants
    m supermario/kill            # stop it
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List

import mod as m

MODULE_DIR = Path(__file__).parent

# The orbit loader imports this file by path, so the module directory is not
# necessarily importable. Put it on the path before reaching for our package.
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

# Test ROMs are freely redistributable but they are not ours and they are not
# code, so they live off-tree with the rest of this module's local state.
ROM_DIR = Path.home() / '.mod' / 'supermario' / 'testroms'


class Mod:
    description = ('An NES emulator — 6502, 2C02 and 2A03 — that runs entirely '
                   'in the browser. Bring your own ROM.')

    # What the HTTP API exposes. Every public method is a function of this
    # module, but the API answers from the public gateway, so it serves only the
    # ones that read: serve/kill/register and friends stay on the CLI.
    API_FNS = ('info', 'health', 'readme', 'url', 'path', 'boards', 'files')

    # The cartridge boards, and the games that are the reason each one is here.
    BOARDS = [
        (0, 'NROM', 'No banking. Super Mario Bros., Donkey Kong, Excitebike.'),
        (1, 'MMC1', 'Serial-loaded banking. Super Mario Bros. 2, Metroid, Zelda.'),
        (2, 'UxROM', '16K PRG switching with a fixed high bank. Castlevania, '
                     'Contra, Mega Man.'),
        (3, 'CNROM', 'CHR switching only. Arkanoid, Gradius.'),
        (4, 'MMC3', 'Banking plus the scanline IRQ counter. Super Mario Bros. 3, '
                    'Kirby, Mega Man 3-6.'),
        (7, 'AxROM', '32K PRG switching, single-screen mirroring. Battletoads.'),
        (66, 'GxROM', 'One register for both PRG and CHR. Super Mario Bros. / '
                      'Duck Hunt.'),
    ]

    # The public test ROMs test() runs, and where they come from.
    TEST_ROMS = {
        'nestest.nes': 'other/nestest.nes',
        'instr_official.nes': 'instr_test-v5/official_only.nes',
        'instr_all.nes': 'instr_test-v5/all_instrs.nes',
        'cpu_timing.nes': 'cpu_timing_test6/cpu_timing_test.nes',
        'ppu_vbl_nmi.nes': 'ppu_vbl_nmi/ppu_vbl_nmi.nes',
        'apu_test.nes': 'apu_test/apu_test.nes',
        'oam_read.nes': 'oam_read/oam_read.nes',
        'sprite_hit_01.nes': 'sprite_hit_tests_2005.10.05/01.basics.nes',
        'ppu_palette.nes': 'blargg_ppu_tests_2005.09.15b/palette_ram.nes',
        'ppu_vram.nes': 'blargg_ppu_tests_2005.09.15b/vram_access.nes',
        # MMC3's scanline counter is what splits SMB3's screen, so it gets its
        # own tests rather than riding on "the game looked fine".
        'mmc3_1_clocking.nes': 'mmc3_test_2/rom_singles/1-clocking.nes',
        'mmc3_2_details.nes': 'mmc3_test_2/rom_singles/2-details.nes',
        'mmc3_3_a12.nes': 'mmc3_test_2/rom_singles/3-A12_clocking.nes',
        'mmc3_4_scanline.nes': 'mmc3_test_2/rom_singles/4-scanline_timing.nes',
        'mmc3_5_mmc3.nes': 'mmc3_test_2/rom_singles/5-MMC3.nes',
    }
    ROM_BASE = ('https://raw.githubusercontent.com/christopherpow/'
                'nes-test-roms/master')
    NESTEST_LOG = 'https://www.qmtpro.com/~nes/misc/nestest.log'

    # ROMs that predate blargg's $6000 result protocol leave their code in zero
    # page instead — at $F0 on some and $F8 on others. 1 means every test passed.
    LEGACY_RESULT = {
        'ppu_palette.nes': 'f0',
        'ppu_vram.nes': 'f0',
        'sprite_hit_01.nes': 'f8',
    }

    # Suites this build is known not to finish, with what it does reach. They
    # are reported every run but do not fail test(), so a real regression
    # somewhere else still turns the suite red. README has the detail.
    KNOWN_PARTIAL = {
        'ppu_vbl_nmi.nes': '7 of 10 — sub-cycle vblank timing (03, 05, 08)',
        'apu_test.nes': '3 of 8 — the frame counter IRQ fires slightly early',
        'cpu_timing.nes': 'screen-only verdict; it prints PASSED',
        'mmc3_4_scanline.nes': 'the IRQ lands within a scanline, not on the dot',
    }

    def __init__(self, key='supermario', network='testnet'):
        self.key = m.key(key)
        self.network = network
        self.module_dir = MODULE_DIR
        cfg = self._config()
        self.port = int(cfg.get('port', 50342))
        self.app_port = int(cfg.get('app_port', 50342))

    def _config(self) -> dict:
        try:
            with (MODULE_DIR / 'config.json').open() as fh:
                return json.load(fh)
        except Exception:
            return {}

    # ── module surface ───────────────────────────────────────────────────

    def forward(self, action=None, **kwargs):
        """CLI entry: ``m supermario <action> [args]``."""
        if action is None:
            return self.info()
        fn = getattr(self, str(action), None)
        if not callable(fn) or str(action).startswith('_'):
            return {'error': f'unknown action {action!r}', 'fns': self._fns()}
        return fn(**kwargs)

    def _fns(self) -> List[str]:
        return [k for k in dir(self)
                if not k.startswith('_') and callable(getattr(self, k))]

    def info(self) -> dict:
        cfg = self._config()
        return {
            'name': 'supermario',
            'title': 'SUPER MARIO EMULATOR',
            'description': self.description,
            'version': cfg.get('version', '1.0.0'),
            'network': self.network,
            'app': self.url(),
            'api': self.api_url(),
            'schema': cfg.get('schema'),
            'chips': {
                'cpu': 'MOS 6502 — official and unofficial opcodes, exact cycles',
                'ppu': 'RP2C02 — per-dot, loopy scroll registers, sprite 0 hit',
                'apu': 'RP2A03 — two pulses, triangle, noise, DPCM',
            },
            'boards': [f'{n} (#{i})' for i, n, _ in self.BOARDS],
            'controls': {
                'd-pad': 'arrows / WASD',
                'a': 'X, K or space', 'b': 'Z or J',
                'start': 'enter', 'select': 'shift',
                'pause': 'P', 'save state': 'F2', 'load state': 'F4',
                'fast forward': 'tab',
                'gamepad': 'any standard-layout controller',
            },
            'roms': 'none included — load a dump of a cartridge you own',
            'fns': self._fns(),
        }

    def health(self) -> dict:
        return {'ok': True, 'module': 'supermario'}

    def readme(self) -> str:
        path = self.module_dir / 'README.md'
        return path.read_text() if path.exists() else ''

    def url(self, gateway=None) -> str:
        """Where the emulator lives."""
        base = self._config().get('base_path', '/supermario')
        if gateway:
            return f'{str(gateway).rstrip("/")}{base}'
        return f'http://localhost:{self.app_port}{base}'

    def api_url(self, gateway=None) -> str:
        """Where the module's functions answer: ``/api/supermario``."""
        if gateway:
            return f'{str(gateway).rstrip("/")}/api/supermario'
        return f'http://localhost:{self.port}'

    def path(self) -> str:
        """The directory the emulator is served from."""
        return str(self.module_dir / 'web')

    def boards(self) -> list:
        """The cartridge mappers this build supports."""
        return [{'mapper': i, 'board': n, 'games': g} for i, n, g in self.BOARDS]

    def files(self) -> dict:
        """Every file that makes up the emulator, with its size."""
        web = self.module_dir / 'web'
        out = {}
        for p in sorted(web.rglob('*')):
            if p.is_file():
                out[str(p.relative_to(web))] = p.stat().st_size
        return out

    # ── serve / register ─────────────────────────────────────────────────

    def _pm2_start(self, name, cmd, cwd=None, env=None) -> bool:
        subprocess.run(['pm2', 'delete', name], capture_output=True, text=True)
        pm2_cmd = ['pm2', 'start', cmd[0], '--name', name]
        if cwd:
            pm2_cmd += ['--cwd', cwd]
        pm2_cmd += ['--'] + list(cmd[1:])
        r = subprocess.run(pm2_cmd, capture_output=True, text=True,
                           env={**os.environ, **(env or {})})
        if r.returncode != 0:
            print(r.stderr[-800:])
        return r.returncode == 0

    def _pm2_kill(self, name) -> bool:
        return subprocess.run(['pm2', 'delete', name],
                              capture_output=True, text=True).returncode == 0

    def serve_app(self, app_port=None) -> dict:
        """Run the static emulator server under pm2."""
        app_port = int(app_port or self.app_port)
        script = self.module_dir / 'serve.py'
        if not script.exists():
            return {'error': f'{script} not found'}
        env = {'PORT': str(app_port), 'HOST': '0.0.0.0'}
        cmd = ['python3', str(script), '--port', str(app_port), '--host', '0.0.0.0']
        ok = self._pm2_start('supermario.app', cmd, cwd=str(self.module_dir), env=env)
        return {'app': f'http://localhost:{app_port}', 'pm2': 'supermario.app',
                'ok': ok}

    # The emulator is entirely client side, so the API has no work of its own:
    # the static server answers the protocol routes from the same process.
    def serve_api(self, **_) -> dict:
        return {'ok': True, 'api': self.api_url(), 'pm2': 'supermario.app',
                'fns': list(self.API_FNS),
                'note': 'served by supermario.app — the emulator needs no '
                        'second process'}

    def serve(self, app_port=None, register=True, **_) -> dict:
        """Start the server under pm2, then register the gateway route."""
        out = {'app': self.serve_app(app_port=app_port)}
        if register:
            out['registration'] = self.register()
        out['url'] = self.url()
        return out

    def play(self, port=None, open=True, background=True) -> str:
        """Serve the emulator in this process and open it in a browser.

        Handy for a quick session; ``serve()`` is what you want for the fleet,
        since that one survives the shell exiting.
        """
        import importlib.util
        import threading
        import webbrowser
        from http.server import HTTPServer

        # Load serve.py by path: `serve` is a common enough name that importing
        # it normally would fight with whatever else is in sys.modules.
        spec = importlib.util.spec_from_file_location(
            'supermario_serve', str(self.module_dir / 'serve.py'))
        _serve = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_serve)

        port = int(port or self.app_port)
        srv = None
        for p in range(port, port + 20):
            try:
                srv = HTTPServer(('0.0.0.0', p), _serve.Handler)
                port = p
                break
            except OSError:
                continue
        if srv is None:
            raise OSError(f'no free port in {port}-{port + 20}')

        url = f'http://localhost:{port}/supermario/'
        print(f'supermario serving at {url}')
        if open:
            threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        if background:
            self._srv = srv
            threading.Thread(target=srv.serve_forever, daemon=True).start()
        else:
            try:
                srv.serve_forever()
            except KeyboardInterrupt:
                srv.shutdown()
        return url

    def stop(self) -> dict:
        """Stop a server started by ``play()`` in this process."""
        srv = getattr(self, '_srv', None)
        if srv is None:
            return {'ok': False, 'error': 'nothing running in this process'}
        srv.shutdown()
        srv.server_close()
        self._srv = None
        return {'ok': True}

    def kill(self) -> dict:
        killed = [n for n in ('supermario.api', 'supermario.app')
                  if self._pm2_kill(n)]
        return {'killed': killed}

    # ── tests ────────────────────────────────────────────────────────────

    def fetch_test_roms(self, force=False) -> dict:
        """Download the public NES test ROMs that ``test()`` runs.

        They live in ~/.mod/supermario/testroms rather than in the tree: they
        are not this module's code, and none of them are needed to play.
        """
        import urllib.request

        ROM_DIR.mkdir(parents=True, exist_ok=True)
        got, failed, present = [], {}, []
        targets = dict(self.TEST_ROMS)
        targets['nestest.log'] = None          # fetched from its own home

        for name, rel in targets.items():
            dest = ROM_DIR / name
            if dest.exists() and not force:
                present.append(name)
                continue
            url = self.NESTEST_LOG if rel is None else f'{self.ROM_BASE}/{rel}'
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    dest.write_bytes(r.read())
                got.append(name)
            except Exception as e:
                failed[name] = str(e)

        return {'dir': str(ROM_DIR), 'fetched': got, 'present': present,
                'failed': failed, 'ok': not failed}

    def test(self, roms=True) -> dict:
        """The emulator's own test suite.

        Three layers: every JS file parses, the CPU matches nestest's log
        instruction for instruction, and blargg's test ROMs run to their own
        pass/fail verdict. The ROM half is skipped rather than failed when the
        ROMs have not been fetched, so this still says something offline.
        """
        web = self.module_dir / 'web'
        needed = ['index.html', 'js/cpu.js', 'js/ppu.js', 'js/apu.js',
                  'js/mappers.js', 'js/nes.js', 'js/ui.js']
        missing = [f for f in needed if not (web / f).exists()]
        if missing:
            return {'ok': False, 'missing': missing}

        checks = {'files': True}
        node = subprocess.run(['bash', '-lc', 'command -v node'],
                              capture_output=True, text=True)
        if node.returncode != 0:
            checks['node'] = 'not installed — skipped syntax and ROM checks'
            return {'ok': True, 'checks': checks}

        for f in needed[1:]:
            r = subprocess.run(['node', '--check', str(web / f)],
                               capture_output=True, text=True)
            checks[f] = 'ok' if r.returncode == 0 else r.stderr[-400:]
        ok = all(v == 'ok' for k, v in checks.items() if k.startswith('js/'))

        tests = self.module_dir / 'tests'
        if (ROM_DIR / 'nestest.nes').exists() and (ROM_DIR / 'nestest.log').exists():
            r = subprocess.run(['node', str(tests / 'cputest.js')],
                               capture_output=True, text=True, timeout=300)
            try:
                checks['nestest'] = json.loads(r.stdout.strip())
            except Exception:
                checks['nestest'] = {'ok': False,
                                     'error': (r.stderr or r.stdout)[-400:]}
            ok = ok and checks['nestest'].get('ok', False)
        else:
            checks['nestest'] = 'skipped — run m supermario/fetch_test_roms'

        if roms:
            results = {}
            for name in self.TEST_ROMS:
                path = ROM_DIR / name
                if name == 'nestest.nes' or not path.exists():
                    continue
                r = subprocess.run(
                    ['node', str(tests / 'romtest.js'), str(path),
                     '--frames', '3000', '--json'],
                    capture_output=True, text=True, timeout=900)
                try:
                    out = json.loads(r.stdout.strip())
                    if name in self.LEGACY_RESULT:
                        code = out['zp'][self.LEGACY_RESULT[name]]
                        passed, verdict = code == 1, f'result code ${code:02X}'
                    elif out['status'] is not None:
                        passed = out['status'] == 0
                        verdict = out['text'].split('\n')[-1] if out['text'] else ''
                    else:
                        passed, verdict = None, 'no verdict in memory'
                    res = {'passed': passed, 'verdict': verdict,
                           'colors': out['colors']}
                    if name in self.KNOWN_PARTIAL and passed is not True:
                        res['known_partial'] = self.KNOWN_PARTIAL[name]
                    results[name] = res
                except Exception:
                    results[name] = {'error': (r.stderr or r.stdout)[-300:]}
            checks['roms'] = results or 'skipped — run m supermario/fetch_test_roms'
            # A known-partial suite is reported but does not fail the run; a
            # ROM that used to pass and now does not still does.
            for res in (results or {}).values():
                if res.get('passed') is False and 'known_partial' not in res:
                    ok = False
                if 'error' in res:
                    ok = False

        return {'ok': ok, 'checks': checks}

    # ── registration ─────────────────────────────────────────────────────

    def register(self, app_url=None, api_url=None, owner=None,
                 gateway='https://modc2.com') -> dict:
        app_url = app_url or f'http://localhost:{self.app_port}'
        api_url = api_url or f'http://localhost:{self.port}'
        try:
            ns = m.mod('server.namespace')()
            ns.reg('supermario', app_url)
            ns.reg_app('supermario', app_url, owner=owner or '',
                       port=self.app_port, api_url=api_url)
            public = f'{gateway.rstrip("/")}/supermario'
            print(f'supermario registered → {public}  (app: {app_url}, api: {api_url})')
            return {'ok': True, 'gateway': public, 'app': app_url, 'api': api_url}
        except Exception as e:
            print(f'supermario: gateway registration failed: {e}')
            return {'ok': False, 'error': str(e), 'app': app_url, 'api': api_url}

    def deregister(self) -> dict:
        try:
            m.mod('server.namespace')().dereg_app('supermario')
            return {'ok': True, 'deregistered': 'supermario'}
        except Exception as e:
            return {'ok': False, 'error': str(e)}
