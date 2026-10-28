"""Offline tests: no network, scratch NANOVM_DATA, nothing touches the fleet's
real vm store. The boot test exercises real namespaces and is skipped where
clone(CLONE_NEW*) is not permitted (non-root, seccomp'd CI)."""

import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, 'src', 'bin', 'nanovm')
sys.path.insert(0, ROOT)


@pytest.fixture(scope='session')
def binary():
    if not os.path.exists(BIN):
        go = '/usr/local/go/bin/go' if os.path.exists('/usr/local/go/bin/go') else 'go'
        r = subprocess.run([go, 'build', '-o', 'bin/nanovm', './cmd/nanovm'],
                           cwd=os.path.join(ROOT, 'src'), capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    return BIN


@pytest.fixture()
def env(tmp_path):
    e = dict(os.environ)
    e['NANOVM_DATA'] = str(tmp_path)
    return e


def run(binary, env, *args, check=False):
    r = subprocess.run([binary, *args], capture_output=True, text=True, env=env, timeout=30)
    if check:
        assert r.returncode == 0, r.stderr
    return r


# ── manifest and anchor ──────────────────────────────────────────

def test_config_sane():
    with open(os.path.join(ROOT, 'config.json')) as f:
        cfg = json.load(f)
    assert cfg['name'] == 'nanovm'
    assert cfg['anchor'] == 'mod.py'
    assert cfg['port'] == 51230
    assert cfg['base_path'] == '/nanovm'
    assert set(cfg['api_fns']) <= set(cfg['fns'])


def test_anchor_exposes_every_declared_fn():
    import mod
    with open(os.path.join(ROOT, 'config.json')) as f:
        cfg = json.load(f)
    m = mod.Mod()
    for fn in cfg['fns']:
        assert callable(getattr(m, fn, None)), f'config.json declares {fn} but mod.py lacks it'


def test_info_and_health_answer_offline():
    import mod
    m = mod.Mod()
    info = m.info()
    assert info['name'] == 'nanovm'
    assert info['port'] == 51230
    h = m.health()
    assert 'built' in h and 'server' in h


def test_verbs_reject_missing_args():
    import mod
    m = mod.Mod()
    for verb in (m.run, m.exec, m.logs, m.stop, m.rm, m.rootfs):
        out = verb()
        assert out.get('ok') is False and 'error' in out


# ── the binary ───────────────────────────────────────────────────

def test_version(binary, env):
    r = run(binary, env, 'version', check=True)
    assert 'nanovm' in r.stdout


def test_ls_empty_store(binary, env):
    r = run(binary, env, 'ls', '--json', check=True)
    d = json.loads(r.stdout)
    assert d['count'] == 0 and d['vms'] == []


def test_bad_name_rejected(binary, env):
    r = run(binary, env, 'run', 'Bad Name!', 'true')
    assert r.returncode != 0
    assert 'name must be' in r.stderr


def test_missing_cmd_rejected(binary, env):
    r = run(binary, env, 'run', 'solo')
    assert r.returncode != 0


def test_unknown_vm_is_a_clean_error(binary, env):
    for verb in ('stop', 'rm', 'logs'):
        r = run(binary, env, verb, 'ghost')
        assert r.returncode != 0
        assert 'no vm named' in r.stderr


def test_flags_stop_at_the_command(binary, env):
    # `sh -c ...` after the name must never be parsed as nanovm flags
    r = run(binary, env, 'run', 'x', 'sh', '--not-a-nanovm-flag')
    assert 'unknown flag' not in r.stderr


# ── a real boot, where the kernel allows it ──────────────────────

def _can_unshare():
    r = subprocess.run(['unshare', '--pid', '--mount', '--fork', 'true'],
                       capture_output=True)
    return r.returncode == 0


@pytest.mark.skipif(not _can_unshare(), reason='namespaces not permitted here')
def test_boot_isolates(binary, env):
    r = run(binary, env, 'run', 'tboot', 'sh', '-c',
            'echo pid=$$; hostname; touch /etc/nanovm-test 2>/dev/null && echo RW || echo RO',
            check=True)
    assert 'pid=1' in r.stdout          # fresh PID namespace
    assert 'tboot' in r.stdout          # UTS namespace, hostname = name
    assert 'RO' in r.stdout             # host root is read-only
    # foreground exit recorded in the store
    d = json.loads(run(binary, env, 'ls', '--json', check=True).stdout)
    assert d['count'] == 1 and d['vms'][0]['status'] == 'exited'
    run(binary, env, 'rm', 'tboot', check=True)
