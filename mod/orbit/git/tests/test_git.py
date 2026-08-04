import json
import os
import subprocess
import tempfile
import time

import pytest
import mod as m


@pytest.fixture()
def g(tmp_path, monkeypatch):
    """A Mod instance whose state/acl/github files live in a tmp dir."""
    import mod.orbit.git.mod as gitmod
    inst = gitmod.Mod()
    inst.state_path = str(tmp_path / 'state.json')
    inst.github_path = str(tmp_path / 'github.json')
    inst.oauth_path = str(tmp_path / 'oauth.json')
    inst.pending_path = str(tmp_path / 'pending.json')
    inst.access_path = str(tmp_path / 'access.json')
    inst.owner_path = str(tmp_path / 'owner.json')
    inst.host_owner_path = str(tmp_path / 'host_owner.json')
    inst.clones = str(tmp_path / 'repos')
    for var in ('GIT_ACCESS_OPEN', 'GIT_OWNER', 'MOD_OWNER', 'GITHUB_TOKEN', 'GH_TOKEN',
                'GITHUB_CLIENT_ID', 'GITHUB_CLIENT_SECRET'):
        monkeypatch.delenv(var, raising=False)
    return inst


@pytest.fixture()
def gh_user(monkeypatch):
    """Stub api.github.com — /user identifies whoever the token belongs to."""
    def _api(self, path, token=None, params=None, address=None):
        if path == '/user':
            return {'login': f'user-{token}', 'name': 'T'}, {'X-OAuth-Scopes': 'repo'}
        if path == '/rate_limit':
            return {'resources': {'core': {'remaining': 4999, 'limit': 5000}}}, {}
        return [], {}
    import mod.orbit.git.mod as gitmod
    monkeypatch.setattr(gitmod.Mod, '_gh_api', _api)


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


class FakeAgent:
    """Stands in for the agent module — records the prompt, replies (or dies)."""
    DEFAULT_MODELS = {'model.openrouter': 'test/model'}

    def __init__(self, reply=None, boom=None):
        self._provider = 'model.openrouter'
        self.reply, self.boom, self.prompts = reply, boom, []
        outer = self

        class _Model:
            def forward(self, prompt, **kw):
                outer.prompts.append((prompt, kw))
                if outer.boom:
                    raise RuntimeError(outer.boom)
                return outer.reply
        self.model = _Model()


@pytest.fixture()
def dirty(g, tmp_path):
    """A tracked repo with one modified file."""
    repo = make_repo(str(tmp_path / 'w'))
    g.track(repo, name='w')
    open(os.path.join(repo, 'a.txt'), 'w').write('one\ntwo\n')
    return repo


def test_agent_writes_the_commit_message(g, dirty):
    g._agent_mod = FakeAgent(reply='```\nHere is the commit message:\n'
                                   'add a second line to a.txt\n\n- because one was lonely\n```')
    out = g.message('w')
    assert out['by'] == 'agent' and out['model'] == 'test/model' and out['files'] == 1
    assert out['message'] == 'add a second line to a.txt\n\n- because one was lonely'
    prompt, kw = g._agent_mod.prompts[0]
    assert '+two' in prompt and 'a.txt' in prompt and kw['temperature'] == 0


def test_commit_takes_the_agent_message_and_msg_wins(g, dirty):
    g._agent_mod = FakeAgent(reply='written by the agent')
    out = g.commit('w')
    assert out['committed'] and out['by'] == 'agent'
    assert g.commits('w')[0]['message'] == 'written by the agent'
    assert g.changes('w')['clean']
    # an explicit message never reaches the agent
    open(os.path.join(dirty, 'a.txt'), 'a').write('three\n')
    out = g.commit('w', msg='mine')
    assert out['by'] == 'caller' and g.commits('w')[0]['message'] == 'mine'
    assert len(g._agent_mod.prompts) == 1


def test_message_falls_back_when_the_agent_is_down(g, dirty):
    open(os.path.join(dirty, 'b.txt'), 'w').write('b\n')
    g._agent_mod = FakeAgent(boom='no api key')
    out = g.message('w')
    assert out['by'] == 'fallback' and 'no api key' in out['error']
    assert out['message'] == 'update 2 files in a.txt, b.txt'
    # a dead agent must not block the commit
    assert g.commit('w')['committed'] and g.commits('w')[0]['message'] == out['message']


def test_message_needs_something_to_describe(g, tmp_path):
    g.track(make_repo(str(tmp_path / 'c')), name='c')
    g._agent_mod = FakeAgent(reply='nope')
    with pytest.raises(ValueError):
        g.message('c')
    assert not g._agent_mod.prompts


def test_push_commits_then_pushes(g, dirty, tmp_path):
    bare = str(tmp_path / 'remote.git')
    subprocess.run(['git', 'init', '--bare', bare], capture_output=True, check=True)
    for cmd in (['git', 'remote', 'add', 'origin', bare],
                ['git', 'push', '-u', 'origin', 'main']):
        subprocess.run(cmd, cwd=dirty, capture_output=True, check=True)
    g._agent_mod = FakeAgent(reply='add two')
    out = g.push('w')
    assert out['committed'] and out['message'] == 'add two' and out['pushed']
    assert g.commits('w')[0]['hash'].startswith(out['hash'])
    # nothing left to commit — push is then a no-op that still reports honestly
    again = g.push('w')
    assert again['committed'] is False and again['pushed'] and len(g._agent_mod.prompts) == 1


def test_refused_push_names_the_missing_piece(g, dirty, gh_user):
    """A push GitHub refuses says what to fix, not what git printed."""
    refusal = ("fatal: could not read Username for 'https://github.com': "
               'terminal prompts disabled')
    subprocess.run(['git', 'remote', 'add', 'origin', 'https://github.com/o/r.git'],
                   cwd=dirty, capture_output=True, check=True)
    assert 'no GitHub account connected' in g._push_hint(dirty, None, None, refusal)
    # connected, but that account can't write here — a different fix
    g.connect('ghp_x', address='0xA')
    assert 'reconnect' in g._push_hint(dirty, '0xA', None, refusal)
    # and anything that isn't an auth refusal gets no hint at all
    assert g._push_hint(dirty, None, None, 'error: failed to push some refs') is None


def test_network_git_never_waits_on_a_prompt(g):
    """Headless under pm2 there is nobody to type a password."""
    assert g.GIT_ENV['GIT_TERMINAL_PROMPT'] == '0'
    assert 'BatchMode=yes' in g.GIT_ENV['GIT_SSH_COMMAND']


def test_push_output_never_leaks_the_token(g):
    assert g._scrub('To https://x-access-token:ghp_secret@github.com/o/r.git') == \
        'To https://***@github.com/o/r.git'


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


def wallet_token(acct, data=None):
    """What the app's "sign in with wallet" mints: base64url of
    {data,time,key,signature} over the compact {"data":…,"time":…}."""
    import base64
    from eth_account import Account
    from eth_account.messages import encode_defunct
    body = {'data': data or {'mod': 'git'}, 'time': str(time.time())}
    sig = Account.sign_message(
        encode_defunct(text=json.dumps(body, separators=(',', ':'))),
        private_key=acct.key).signature.hex()
    tok = dict(body, key=acct.address.lower(),
               signature=sig if sig.startswith('0x') else '0x' + sig)
    return base64.urlsafe_b64encode(
        json.dumps(tok, separators=(',', ':')).encode()).rstrip(b'=').decode()


def test_host_owner_can_commit_without_a_grant(g):
    from eth_account import Account
    host, stranger = Account.create(), Account.create()
    # the box records its owner the way every module does; checksummed there,
    # lowercase from the wallet — the ACL must not care
    json.dump({'owner': host.address}, open(g.host_owner_path, 'w'))
    assert g.access()['host_owner'] == host.address
    assert g._role_of(host.address.lower()) == 'owner'
    who = g._authorize({'Authorization': f'Bearer {wallet_token(host)}'}, need='write')
    assert who['role'] == 'owner' and who['address'] == host.address.lower()
    # …and the gate still holds for everyone else
    with pytest.raises(PermissionError):
        g._authorize({'Authorization': f'Bearer {wallet_token(stranger)}'}, need='write')


def test_host_owner_sources_in_order(g, monkeypatch):
    from eth_account import Account
    a, b, c = (Account.create().address for _ in range(3))
    assert g._host_owner() is None                      # nothing pinned anywhere
    json.dump({'owner': a}, open(g.host_owner_path, 'w'))
    assert g._host_owner() == a                         # the host's owner of record
    g.set_owner(b, host=True)
    assert g._host_owner() == b                         # git's own file wins
    monkeypatch.setenv('GIT_OWNER', c)
    assert g._host_owner() == c                         # …and the env wins over both


def test_grants_are_case_insensitive(g):
    g.grant('0xAbCdEf0000000000000000000000000000000001', role='write')
    assert g._role_of('0xabcdef0000000000000000000000000000000001') == 'write'


def test_write_rank(g):
    g.grant('0xw', 'write')
    g.grant('0xa', 'admin')
    rank = {'write': 1, 'admin': 2, 'owner': 3}
    assert rank[g._role_of('0xw')] < rank['admin'] <= rank[g._role_of('0xa')]


def test_github_disconnected_by_default(g):
    gh = g.github()
    assert gh['connected'] is False and gh['keys'] == []
    with pytest.raises(PermissionError):
        g.github_repos()


def test_github_accounts_are_per_key(g, gh_user):
    owner = g._acl()['owner']
    g.connect('tok-a', address='0xA')
    assert g.github(address='0xA')['login'] == 'user-tok-a'
    assert g.github(address='0xB')['connected'] is False
    # 0xB has no account of its own and the owner has none either → nothing
    assert g._github_token('0xB') is None
    g.connect('tok-owner')
    assert g._github_token('0xA') == 'tok-a'          # your own key wins
    assert g._github_token('0xB') == 'tok-owner'      # …else the owner's
    assert {k['key'] for k in g.github()['keys']} == {'0xA', owner}
    assert g.disconnect(address='0xA')['was'] == 'user-tok-a'
    assert g.github(address='0xA')['connected'] is False


def test_legacy_single_account_migrates_to_the_owner(g):
    json.dump({'token': 'old', 'login': 'legacy'}, open(g.github_path, 'w'))
    assert g._github_token() == 'old'
    assert g.github()['login'] == 'legacy'
    assert list(json.load(open(g.github_path))['accounts']) == [g._acl()['owner']]


def fake_post(responses):
    """Stub github.com/login/* — pops one canned json body per call."""
    class R:
        def __init__(self, body):
            self.text = json.dumps(body)
            self._b = body

        def json(self):
            return self._b
    calls = []

    def _post(url, headers=None, data=None, timeout=None):
        calls.append((url, data))
        return R(responses.pop(0))
    return _post, calls


def test_oauth_needs_an_app_first(g):
    with pytest.raises(PermissionError):
        g.oauth()
    assert g.oauth_status()['configured'] is False
    st = g.oauth_app(client_id='cid')
    assert st['configured'] and st['device_flow'] and not st['web_flow']
    assert g.oauth_app(client_id='cid', client_secret='sek')['web_flow'] is True
    assert oct(os.stat(g.oauth_path).st_mode)[-3:] == '600'


def test_oauth_device_flow_attaches_to_the_key(g, gh_user, monkeypatch):
    import requests
    g.oauth_app(client_id='cid')
    post, calls = fake_post([
        {'device_code': 'DEV', 'user_code': 'ABCD-1234', 'interval': 0,
         'verification_uri': 'https://github.com/login/device', 'expires_in': 900},
        {'error': 'authorization_pending'},
        {'access_token': 'gho_new'},
    ])
    monkeypatch.setattr(requests, 'post', post)
    s = g.oauth(address='0xA')
    assert s['user_code'] == 'ABCD-1234' and s['key'] == '0xA'
    assert g.oauth_poll(s['session'])['status'] == 'pending'
    done = g.oauth_poll(s['session'])
    assert done['status'] == 'connected' and done['login'] == 'user-gho_new'
    assert g.github(address='0xA')['via'] == 'oauth'
    assert g._github_token('0xA') == 'gho_new'
    # the session is spent
    with pytest.raises(KeyError):
        g.oauth_poll(s['session'])


def test_oauth_web_flow_binds_state_to_a_key(g, gh_user, monkeypatch):
    import requests
    g.oauth_app(client_id='cid', client_secret='sek')
    url = g.oauth_url('https://x/git/oauth/callback', address='0xA')
    assert 'client_id=cid' in url['url'] and url['state'] in g._pending()
    post, _ = fake_post([{'access_token': 'gho_web'}])
    monkeypatch.setattr(requests, 'post', post)
    # a code with an unknown state can't graft an account onto a key
    with pytest.raises(PermissionError):
        g.oauth_callback(code='c', state='not-a-state')
    out = g.oauth_callback(code='c', state=url['state'])
    assert out['key'] == '0xA' and out['login'] == 'user-gho_web'
    assert g._pending() == {}


def test_info_reports_mod_changes(g):
    info = g.info()
    assert info['name'] == 'git'
    assert 'files_changed' in info['mod_repo'] or 'error' in info['mod_repo']
    assert info['tracking'] == ['mod']
