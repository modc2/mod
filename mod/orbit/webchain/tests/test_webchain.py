"""webchain tests — staketime-priority namespace, preemption, subdomains, content.

The namespace logic (claim / preempt / release / subdomain / validation) is
tested purely against an isolated temp index — no live chain, no RPC. Content
(publish -> store CID -> fetch) is exercised in one integration test against a
real local module, skipped if the registry/store is unavailable.
"""
import importlib.util
import os
import sys
import tempfile

import pytest

import mod as m  # the framework package

# Load the local webchain mod.py by path (its filename `mod.py` would otherwise
# shadow the framework `mod` package imported above).
_spec = importlib.util.spec_from_file_location(
    'webchain_mod', os.path.join(os.path.dirname(os.path.dirname(__file__)), 'mod.py'))
_webchain = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_webchain)
Mod = _webchain.Mod

A = '0x' + 'a' * 40
B = '0x' + 'b' * 40
C = '0x' + 'c' * 40


@pytest.fixture
def wc():
    fd, path = tempfile.mkstemp(suffix='.json')
    os.close(fd)
    os.remove(path)
    yield Mod(index_path=path)
    if os.path.exists(path):
        os.remove(path)


# --- weight = staketime ----------------------------------------------------

def test_weight_is_tokens_times_lock(wc):
    assert wc.weight(1000, 200) == 200_000
    assert wc.weight(5000, 200) == 1_000_000


# --- claiming + preemption -------------------------------------------------

def test_claim_unclaimed_name(wc):
    r = wc.claim('foo', amount=1000, lock=200, key=A)
    assert r['holder'] == A and r['weight'] == 200_000 and r['preempted'] is None
    e = wc.resolve('foo')
    assert e['holder'] == A and e['cid'] == '' and e['is_sub'] is False


def test_lower_weight_cannot_preempt(wc):
    wc.claim('foo', amount=1000, lock=200, key=A)          # weight 200k
    with pytest.raises(ValueError):
        wc.claim('foo', amount=10, lock=10, key=B)         # weight 100
    assert wc.resolve('foo')['holder'] == A                # unchanged


def test_equal_weight_cannot_preempt(wc):
    wc.claim('foo', amount=1000, lock=200, key=A)          # 200k
    with pytest.raises(ValueError):
        wc.claim('foo', amount=200, lock=1000, key=B)      # also 200k -> not strictly greater
    assert wc.resolve('foo')['holder'] == A


def test_higher_weight_preempts_and_reports_displaced(wc):
    wc.claim('foo', amount=1000, lock=200, key=A)          # 200k
    r = wc.claim('foo', amount=5000, lock=200, key=B)      # 1,000,000 > 200k
    assert r['holder'] == B and r['preempted'] == A and r['weight'] == 1_000_000
    assert wc.resolve('foo')['holder'] == B


def test_preemption_resets_content(wc):
    # a new holder starts with a blank pointer (mirrors Namespace.sol)
    wc.claim('foo', amount=1000, lock=200, key=A)
    wc.set_content('foo', 'QmDUMMYcid', key=A)
    assert wc.resolve('foo')['cid'] == 'QmDUMMYcid'
    wc.claim('foo', amount=5000, lock=200, key=B)
    assert wc.resolve('foo')['cid'] == ''


# --- name validation -------------------------------------------------------

def test_top_level_rejects_dotted_name(wc):
    with pytest.raises(AssertionError):
        wc.claim('blog.foo', amount=10, lock=10, key=A)


@pytest.mark.parametrize('bad', ['UPPER', 'has space', '-leading', 'a' * 64, ''])
def test_invalid_names_rejected(wc, bad):
    with pytest.raises(AssertionError):
        wc.claim(bad, amount=10, lock=10, key=A)


# --- content ownership gating ----------------------------------------------

def test_set_content_requires_holder(wc):
    wc.claim('foo', amount=1000, lock=200, key=A)
    with pytest.raises(ValueError):
        wc.set_content('foo', 'QmX', key=B)                # B is not the holder
    assert wc.set_content('foo', 'QmX', key=A)['cid'] == 'QmX'


# --- subdomains (parent-delegated) -----------------------------------------

def test_mint_sub_requires_parent_holder(wc):
    wc.claim('foo', amount=1000, lock=200, key=A)
    with pytest.raises(ValueError):
        wc.mint_sub('foo', 'blog', cid='QmS', key=B)       # B doesn't hold foo
    r = wc.mint_sub('foo', 'blog', cid='QmS', key=A)
    assert r['name'] == 'blog.foo'
    e = wc.resolve('blog.foo')
    assert e['holder'] == A and e['is_sub'] is True and e['parent'] == 'foo' and e['cid'] == 'QmS'


def test_cannot_nest_under_subdomain(wc):
    wc.claim('foo', amount=1000, lock=200, key=A)
    wc.mint_sub('foo', 'blog', cid='QmS', key=A)
    with pytest.raises(AssertionError):
        wc.mint_sub('blog.foo', 'deep', cid='QmD', key=A)  # parent is itself a sub


def test_release_drops_subdomains_and_frees_name(wc):
    wc.claim('foo', amount=1000, lock=200, key=A)
    wc.mint_sub('foo', 'blog', cid='QmS', key=A)
    out = wc.release('foo', key=A)
    assert out['released'] == 'foo' and out['subdomains_dropped'] == ['blog.foo']
    assert wc.resolve('foo') is None and wc.resolve('blog.foo') is None
    # name is now free for anyone to claim
    assert wc.claim('foo', amount=1, lock=1, key=B)['holder'] == B


def test_release_requires_holder(wc):
    wc.claim('foo', amount=1000, lock=200, key=A)
    with pytest.raises(ValueError):
        wc.release('foo', key=B)


# --- listing ---------------------------------------------------------------

def test_names_filters_by_holder_and_search(wc):
    wc.claim('foo', amount=10, lock=10, key=A)
    wc.claim('bar', amount=10, lock=10, key=B)
    wc.mint_sub('foo', 'blog', cid='QmS', key=A)
    assert set(wc.names().keys()) == {'foo', 'bar', 'blog.foo'}
    assert set(wc.names(holder=A).keys()) == {'foo', 'blog.foo'}
    assert set(wc.names(search='blog').keys()) == {'blog.foo'}


# --- staketime read is chain-safe ------------------------------------------

def test_staketime_never_raises(wc):
    # returns 0 (not an exception) when the chain/RPC is unavailable
    assert isinstance(wc.staketime(A), int)


# --- integration: publish real code -> store CID -> fetch by name ----------

def test_publish_and_fetch_roundtrip(wc):
    """Pack a real local module into the localfs store and fetch it back by
    name. Skips cleanly if the registry/store backend isn't available."""
    holder = wc.key.address
    wc.claim('foo', amount=1000, lock=200, key=holder)       # must hold a name to publish to it
    try:
        r = wc.publish('foo', mod='0xprof', key=holder)
    except Exception as e:  # registry/store unavailable in this environment
        pytest.skip(f'registry/store unavailable: {e}')
    assert r['cid'] and wc.resolve('foo')['cid'] == r['cid']
    files = wc.fetch('foo')
    assert isinstance(files, dict) and 'mod.py' in files


def test_fetch_without_content_errors(wc):
    wc.claim('foo', amount=10, lock=10, key=A)
    with pytest.raises(ValueError):
        wc.fetch('foo')                                    # no cid set yet


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
