import json
import os
import subprocess
import tempfile

import pytest
import mod as m


@pytest.fixture()
def g(tmp_path, monkeypatch):
    """A Mod instance whose state/acl/github files live in a tmp dir."""
    import mod.orbit.git.mod as gitmod
    inst = gitmod.Mod()
    inst.state_path = str(tmp_path / 'state.json')
    inst.github_path = str(tmp_path / 'github.json')
    inst.access_path = str(tmp_path / 'access.json')
    inst.clones = str(tmp_path / 'repos')
    monkeypatch.delenv('GIT_ACCESS_OPEN', raising=False)
    return inst


def make_repo(path):
    os.makedirs(path, exist_ok=True)
    for cmd in (['git', 'init', '-b', 'main'],
                ['git', 'config', 'user.email', 't@t'],
                ['git', 'config', 'user.name', 't']):
        subprocess.run(cmd, cwd=path, capture_output=True, check=True)
    open(os.path.join(path, 'a.txt'), 'w').write('one\n')
    subprocess.run(['git', 'add', '-A'], cwd=path, capture_output=True, check=True)
    subprocess.run(['git', 'commit', '-m', 'init'], cwd=path, capture_output=True, check=True)
    return path


def test_mod_repo_always_tracked(g):
    st = g._load()
    assert 'mod' in st['repos']
    assert os.path.isdir(os.path.join(st['repos']['mod']['path'], '.git'))
    with pytest.raises(ValueError):
        g.untrack('mod')


def test_changes_tracks_every_kind_of_change(g, tmp_path):
    repo = make_repo(str(tmp_path / 'r'))
    g.track(repo, name='r')
    # modify, delete, add untracked
    open(os.path.join(repo, 'a.txt'), 'w').write('one\ntwo\n')
    open(os.path.join(repo, 'new.txt'), 'w').write('hi\n')
    ch = g.changes('r')
    by = {f['file']: f for f in ch['files']}
    assert ch['branch'] == 'main' and not ch['clean'] and ch['total'] == 2
    assert by['a.txt']['status'] == 'modified' and by['a.txt']['additions'] == 1
    assert by['new.txt']['status'] == 'untracked'
    assert ch['counts'] == {'modified': 1, 'untracked': 1}
    d = g.diff('r')
    assert '+two' in d['diff']
    os.remove(os.path.join(repo, 'a.txt'))
    assert {f['status'] for f in g.changes('r')['files']} == {'deleted', 'untracked'}


def test_commits_and_track_untrack(g, tmp_path):
    repo = make_repo(str(tmp_path / 'r2'))
    out = g.track(repo)
    assert out['tracked'] == 'r2' and 'r2' in g.repos()
    cs = g.commits('r2', n=5)
    assert cs and cs[0]['message'] == 'init' and cs[0]['additions'] == 1
    assert g.untrack('r2')['untracked'] == 'r2'


def test_track_rejects_garbage(g):
    with pytest.raises(ValueError):
        g.track('definitely not a repo ///')


def test_acl_roles_and_authorize(g):
    acl = g._acl()
    me = m.key().address
    assert acl['owner'] == me
    # owner passes any gate with a real signed token
    tok = g.token()
    who = g._authorize({'Authorization': f'Bearer {tok}'}, need='admin')
    assert who['role'] == 'owner' and who['address'] == me
    # unknown/missing tokens fail
    with pytest.raises(PermissionError):
        g._authorize({}, need='write')
    with pytest.raises(PermissionError):
        g._authorize({'Authorization': 'Bearer nonsense'}, need='write')
    # grants: write can't clear the admin gate; revoke removes
    g.grant('0xabc', role='write')
    assert g._role_of('0xabc') == 'write'
    with pytest.raises(ValueError):
        g.grant('0xabc', role='god')
    g.revoke('0xabc')
    assert g._role_of('0xabc') is None


def test_write_rank(g):
    g.grant('0xw', 'write')
    g.grant('0xa', 'admin')
    rank = {'write': 1, 'admin': 2, 'owner': 3}
    assert rank[g._role_of('0xw')] < rank['admin'] <= rank[g._role_of('0xa')]


def test_github_disconnected_by_default(g):
    gh = g.github()
    assert gh['connected'] is False
    with pytest.raises(PermissionError):
        os.environ.pop('GITHUB_TOKEN', None)
        os.environ.pop('GH_TOKEN', None)
        g.github_repos()


def test_info_reports_mod_changes(g):
    info = g.info()
    assert info['name'] == 'git'
    assert 'files_changed' in info['mod_repo'] or 'error' in info['mod_repo']
    assert info['tracking'] == ['mod']
