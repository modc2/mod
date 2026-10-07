"""The approval gate in src/mcp.py — the safety contract of the console chat.

What these tests pin (the copytensor rules, ported):
  * a gated tool NEVER executes before the owner answers;
  * a decline (or an expiry, or a broken queue) never runs it — fail closed;
  * a decline is a successful tool RESULT carrying the owner's note, not an
    error, so the model adapts instead of retrying;
  * the gate only arms inside a chat run (POLYMARKET_AGENT_RUN) — direct
    stdio callers keep their documented trusted-by-construction behavior;
  * pm_copy_stop stays free (stopping only reduces exposure);
  * console tools (pm_strat_*) return the console's applied result verbatim.

No network: gated handlers are monkeypatched; the gate parks before the
handler, so most tests never reach one.
"""

import importlib.util
import json
import os
import sys
import threading
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
MCP_PATH = os.path.join(os.path.dirname(HERE), 'src', 'mcp.py')


@pytest.fixture()
def mcp(tmp_path, monkeypatch):
    """A fresh mcp module armed as a chat run, with a throwaway state dir
    and a fast poll so expiry tests run in milliseconds."""
    monkeypatch.setenv('POLYMARKET_ACCESS_DIR', str(tmp_path))
    monkeypatch.setenv('POLYMARKET_AGENT_RUN', 'run_test')
    monkeypatch.setenv('POLYMARKET_APPROVAL_TTL', '10')
    monkeypatch.setenv('POLYMARKET_APPROVAL_POLL', '0.05')
    spec = importlib.util.spec_from_file_location('polymarket_mcp_under_test', MCP_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    yield mod
    del sys.modules[spec.name]


def pending_files(mod):
    d = mod._approvals_dir()
    return [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith('.json')]


def decide(path, decision, note='', result=None):
    with open(path) as f:
        entry = json.load(f)
    entry.update({'decision': decision, 'note': note, 'result': result,
                  'decided_at': time.time()})
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(entry, f)
    os.replace(tmp, path)


def call_in_thread(mod, name, args):
    """_call_tool blocks on the queue; run it like the CLI would."""
    out = {}

    def run():
        out['resp'] = mod._call_tool(1, {'name': name, 'arguments': args})

    t = threading.Thread(target=run, daemon=True)
    t.start()
    deadline = time.time() + 5
    while not pending_files(mod) and time.time() < deadline and t.is_alive():
        time.sleep(0.01)
    return t, out


def result_text(resp):
    return resp['result']['content'][0]['text']


# ── arming ──

def test_gate_off_without_agent_run(mcp, monkeypatch):
    """Direct stdio callers are untouched: the handler runs, nothing parks."""
    monkeypatch.delenv('POLYMARKET_AGENT_RUN')
    calls = []
    mcp.TOOLS['pm_copy_allocate']['handler'] = lambda a: calls.append(a) or {'ok': True}
    resp = mcp._call_tool(1, {'name': 'pm_copy_allocate',
                              'arguments': {'address': '0xabc', 'allocationUsd': 50}})
    assert calls and resp['result']['isError'] is False
    assert pending_files(mcp) == []


def test_reads_and_stop_never_park(mcp):
    """pm_copy_stop and reads are FREE even inside a chat run."""
    for name in ('pm_copy_stop', 'pm_copy_book', 'pm_strats'):
        mcp.TOOLS[name]['handler'] = lambda a: {'ok': True}
        resp = mcp._call_tool(1, {'name': name, 'arguments': {}})
        assert resp['result']['isError'] is False
        assert pending_files(mcp) == [], f'{name} parked an approval'


def test_autostrat_status_free_run_gated(mcp):
    assert mcp._gate_kind('pm_autostrat', {}) is None
    assert mcp._gate_kind('pm_autostrat', {'op': 'status'}) is None
    assert mcp._gate_kind('pm_autostrat', {'op': 'off'}) is None
    assert mcp._gate_kind('pm_autostrat', {'op': 'run'}) == 'spend'
    assert mcp._gate_kind('pm_autostrat', {'op': 'on'}) == 'spend'


def test_every_money_and_strat_tool_is_gated(mcp):
    for name in ('pm_copy_allocate', 'pm_copy_remove', 'pm_copy_rebalance',
                 'pm_copy_start'):
        assert mcp._gate_kind(name, {}) == 'money', name
    for name in ('pm_strat_create', 'pm_strat_update', 'pm_strat_delete'):
        assert mcp._gate_kind(name, {}) == 'strat', name
        assert name in mcp.CONSOLE_TOOLS


# ── the gate itself ──

def test_gated_tool_parks_and_does_not_execute_before_answer(mcp):
    calls = []
    mcp.TOOLS['pm_copy_allocate']['handler'] = lambda a: calls.append(a) or {'ok': True}
    t, out = call_in_thread(mcp, 'pm_copy_allocate',
                            {'address': '0xabc', 'allocationUsd': 75})
    files = pending_files(mcp)
    assert len(files) == 1
    with open(files[0]) as f:
        entry = json.load(f)
    assert entry['tool'] == 'pm_copy_allocate'
    assert entry['run'] == 'run_test'
    assert entry['kind'] == 'money'
    assert '0xabc' in entry['summary'] and '$75' in entry['summary']
    assert calls == [], 'handler ran before the owner answered'
    decide(files[0], 'approve')
    t.join(timeout=5)
    assert calls, 'approved call never executed'
    assert out['resp']['result']['isError'] is False


def test_decline_never_executes_and_carries_the_note(mcp):
    calls = []
    mcp.TOOLS['pm_copy_start']['handler'] = lambda a: calls.append(a) or {'ok': True}
    t, out = call_in_thread(mcp, 'pm_copy_start', {'autoExecute': True})
    files = pending_files(mcp)
    with open(files[0]) as f:
        assert 'REAL MONEY' in json.load(f)['summary']
    decide(files[0], 'decline', note='too risky, dry-run it first')
    t.join(timeout=5)
    assert calls == [], 'a declined call executed'
    resp = out['resp']
    # A decline is a RESULT the model can adapt to, never an error.
    assert resp['result']['isError'] is False
    text = result_text(resp)
    assert 'DECLINED' in text and 'too risky, dry-run it first' in text


def test_expiry_declines(mcp, monkeypatch):
    monkeypatch.setenv('POLYMARKET_APPROVAL_TTL', '10')  # min clamp
    # Shrink further below the clamp via the function the loop reads.
    monkeypatch.setattr(mcp, '_approval_ttl', lambda: 1)
    calls = []
    mcp.TOOLS['pm_copy_remove']['handler'] = lambda a: calls.append(a) or {'ok': True}
    t, out = call_in_thread(mcp, 'pm_copy_remove', {'address': '0xdead'})
    t.join(timeout=10)
    assert calls == []
    text = result_text(out['resp'])
    assert 'expired' in text.lower()
    # The expiry is written back so the console can grey the card.
    with open(pending_files(mcp)[0]) as f:
        assert json.load(f)['decision'] == 'expired'


def test_unwritable_queue_fails_closed(mcp, monkeypatch):
    calls = []
    mcp.TOOLS['pm_copy_rebalance']['handler'] = lambda a: calls.append(a) or {'ok': True}
    monkeypatch.setattr(mcp, '_write_json_atomic',
                        lambda *a, **k: (_ for _ in ()).throw(OSError('disk gone')))
    resp = mcp._call_tool(1, {'name': 'pm_copy_rebalance', 'arguments': {}})
    assert calls == []
    assert resp['result']['isError'] is False
    assert 'DECLINED' in result_text(resp)


def test_deleted_approval_is_a_decline(mcp):
    calls = []
    mcp.TOOLS['pm_copy_allocate']['handler'] = lambda a: calls.append(a) or {'ok': True}
    t, out = call_in_thread(mcp, 'pm_copy_allocate',
                            {'address': '0xabc', 'allocationUsd': 10})
    os.unlink(pending_files(mcp)[0])
    t.join(timeout=5)
    assert calls == []
    assert 'DECLINED' in result_text(out['resp'])


# ── console tools ──

def test_console_tool_returns_the_consoles_result(mcp):
    t, out = call_in_thread(mcp, 'pm_strat_create',
                            {'name': 'steady 3', 'traders': ['0x' + 'a' * 40]})
    files = pending_files(mcp)
    applied = {'ok': True, 'id': 'chat_x1', 'name': 'steady 3',
               'rejected': ['foo: not an editable strat parameter']}
    decide(files[0], 'approve', result=applied)
    t.join(timeout=5)
    resp = out['resp']
    assert resp['result']['structuredContent'] == applied
    assert 'chat_x1' in result_text(resp)


def test_console_tool_outside_chat_refuses_honestly(mcp, monkeypatch):
    monkeypatch.delenv('POLYMARKET_AGENT_RUN')
    resp = mcp._call_tool(1, {'name': 'pm_strat_delete', 'arguments': {'id': 'x'}})
    text = result_text(resp)
    assert 'console chat' in text
    assert pending_files(mcp) == []
