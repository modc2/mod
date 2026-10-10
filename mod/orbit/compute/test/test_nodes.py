"""Node tests — the half of this module that puts mod on somebody else's box.

The offline half is the contract: how a target string becomes a transport, how
a command is quoted on the way through SSH, what goes into the install tar,
what the registry keeps, and how an answer is recovered from a stream that
also carries login banners and pip warnings. None of it touches the network.

The last test is the whole feature end to end — deploy a container, install
mod, ask it what modules it has, call one — and skips itself unless docker is
here with the base image already pulled, because a test must not download a
python image to tell you the code is fine.
"""

import base64
import io
import json
import os
import subprocess
import sys
import tarfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.dirname(HERE)
if MODULE not in sys.path:
    sys.path.insert(0, MODULE)

import auth                                    # noqa: E402
import mcp                                     # noqa: E402
import modctl                                  # noqa: E402
import node as N                               # noqa: E402


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """A node registry in a temp dir — never the operator's real one."""
    monkeypatch.setattr(N, 'STATE', str(tmp_path))
    monkeypatch.setattr(N, 'NODES_FILE', str(tmp_path / 'nodes.json'))
    monkeypatch.setattr(N, 'SSH_KEY', str(tmp_path / 'id_ed25519'))
    return tmp_path


# ── targets become transports ────────────────────────────────────────────

def test_every_way_a_market_writes_an_ssh_line_parses():
    assert N._ssh_from('root@1.2.3.4') == {
        'kind': 'ssh', 'host': '1.2.3.4', 'user': 'root', 'port': 22}
    assert N._ssh_from('ubuntu@box.example.com:2222')['port'] == 2222
    assert N._ssh_from('ssh -p 41234 root@ssh4.vast.ai') == {
        'kind': 'ssh', 'host': 'ssh4.vast.ai', 'user': 'root', 'port': 41234}
    assert N._ssh_from('ssh -p 22 user@10.0.0.1 -i key.pem')['user'] == 'user'
    with pytest.raises(N.NodeError):
        N._ssh_from('')


def test_transport_is_rebuilt_from_the_record_not_remembered():
    t = N.transport({'id': 'n-1', 'target': {'kind': 'ssh', 'host': 'h', 'port': 9}})
    assert isinstance(t, N.Ssh) and t.port == 9
    d = N.transport({'id': 'n-2', 'target': {'kind': 'docker', 'container': 'c'}})
    assert isinstance(d, N.Docker) and d.host is None
    nested = N.transport({'id': 'n-3', 'target': {
        'kind': 'docker', 'container': 'c', 'host': {'host': 'h', 'user': 'u'}}})
    assert isinstance(nested.host, N.Ssh)
    with pytest.raises(N.NodeError):
        N.transport({'id': 'n-4', 'target': {'kind': 'carrier-pigeon'}})


def test_a_command_survives_the_trip_through_ssh():
    """ssh re-joins argv into one string for the remote shell, so anything with
    a space or a quote has to be quoted once on the way out."""
    argv = N.Ssh('h', port=2222)._wrap("echo 'hi there' && ls -la")
    assert argv[-3:] == ['bash', '-lc', "'echo '\"'\"'hi there'\"'\"' && ls -la'"]
    assert '-p' in argv and '2222' in argv
    # docker exec is execve, not a shell — quoting it again would break it
    assert N.Docker('c')._wrap('echo hi')[-3:] == ['bash', '-lc', 'echo hi']
    # …but a container behind SSH crosses a shell, so it is quoted again
    nested = N.Docker('c', host=N.Ssh('h'))._wrap('echo hi')
    assert nested[-3:] == ['bash', '-lc', "'echo hi'"]
    assert 'docker' in nested and 'exec' in nested


def test_a_provider_exec_transport_refuses_what_it_cannot_do():
    t = N.ProviderExec('targon:abc')
    assert t.streams is False
    r = t.run('ls', stdin=b'data')
    assert r['ok'] is False and 'stdin' in r['err']
    with pytest.raises(N.NodeError):
        t.put_tar(b'x' * 10, '/root/mod')
    with pytest.raises(N.NodeError):
        t.put('/root/big', b'x' * 300_000)      # too big for a command line


# ── the install payload ──────────────────────────────────────────────────

def test_the_install_tar_is_mod_core_and_nothing_heavy():
    blob = N.core_tar()
    with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
        names = tar.getnames()
    assert 'mod/core/mod.py' in names
    assert 'mod/__init__.py' in names
    assert 'config.json' in names, 'Mod() reads port_range out of the root config'
    assert not any('node_modules' in n for n in names)
    assert not any(n.endswith('.pyc') or '__pycache__' in n for n in names)
    assert len(blob) < 12_000_000, 'the point of core-only is that it is small'


def test_pushing_a_module_sends_the_directory_as_it_is_on_this_disk():
    blob, arc = N.mod_tar('compute')
    assert arc == 'mod/orbit/compute'
    with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
        names = tar.getnames()
    assert 'mod/orbit/compute/mod.py' in names
    assert 'mod/orbit/compute/node.py' in names
    assert not any('__pycache__' in n for n in names)
    with pytest.raises(N.NodeError):
        N.mod_tar('no-such-module-anywhere')


def test_the_cli_shim_needs_no_pip_install():
    assert N.REMOTE_MOD in N.SHIM
    assert 'PYTHONPATH' in N.SHIM and 'import mod' in N.SHIM


# ── the registry ─────────────────────────────────────────────────────────

def test_nodes_round_trip_through_the_off_tree_registry(registry):
    a = N._new(name='one', target={'kind': 'docker', 'container': 'c1'}, usd_hr=2.0)
    N._new(name='two', target={'kind': 'docker', 'container': 'c2'}, usd_hr=0.5)
    listed = N.nodes()
    assert listed['count'] == 2
    assert listed['burn_usd_hr'] == 2.5, 'a node list that hides the burn is a bill'
    assert N.get(a['id'])['name'] == 'one'
    assert 'log' not in listed['nodes'][0], 'the list view drops the install trail'
    N.forget(a['id'])
    assert N.nodes()['count'] == 1
    with pytest.raises(N.NodeError):
        N.get(a['id'])


def test_the_boot_log_is_kept_short_and_recent(registry):
    n = N._new(name='chatty', target={'kind': 'docker', 'container': 'c'})
    for i in range(80):
        N._log(n, f'step{i}', True, 1.0, out='x' * 4000)
    saved = N.get(n['id'])
    assert len(saved['log']) == 60
    assert saved['log'][-1]['step'] == 'step79'
    assert len(saved['log'][-1]['out']) <= 1200


def test_two_ports_allocated_in_a_row_are_two_different_ports(registry):
    first = N.free_port()
    assert N.free_port(taken=[first]) != first


# ── control ──────────────────────────────────────────────────────────────

class FakeTransport(N.Transport):
    kind = 'fake'

    def __init__(self, out='', ok=True):
        self.out, self.ok, self.seen = out, ok, []

    def run(self, cmd, timeout=120, stdin=None):
        self.seen.append(cmd)
        return {'ok': self.ok, 'code': 0 if self.ok else 1, 'out': self.out,
                'err': '', 'cmd': cmd}


def test_an_answer_is_recovered_from_a_noisy_stream():
    """A rented box greets you with a login banner, a CUDA notice and a pip
    warning. The answer is whatever sits between the markers, and nothing else."""
    payload = json.dumps({'ok': True, 'op': 'mods', 'mods': ['chain', 'key']})
    noisy = (f'Welcome to Ubuntu 22.04\n=== CUDA 12.4 ===\n'
             f'{N.BEGIN}{payload}{N.END}\nWARNING: pip is out of date\n')
    got = N._ctl({'id': 'n-1'}, FakeTransport(noisy), 'mods')
    assert got['mods'] == ['chain', 'key']


def test_a_node_that_never_ran_the_installer_says_so_plainly():
    with pytest.raises(N.NodeError) as e:
        N._ctl({'id': 'n-1'}, FakeTransport('bash: python3: command not found'), 'info')
    assert 'modctl' in str(e.value)


def test_the_control_payload_cannot_be_mangled_by_a_shell():
    """Args ride as base64 on argv, so a quote or a newline in a value is data."""
    t = FakeTransport(f'{N.BEGIN}{{"ok":true,"op":"call"}}{N.END}')
    N._ctl({'id': 'n-1'}, t, 'call', mod='x', kwargs={'q': "it's \"here\"\n;rm -rf /"})
    blob = t.seen[0].split()[-1]
    assert json.loads(base64.b64decode(blob))['kwargs']['q'].startswith("it's")


def test_modctl_answers_in_one_line_between_markers(capsys):
    modctl.main(['modctl.py', base64.b64encode(b'{"op": "info"}').decode()])
    out = capsys.readouterr().out
    assert out.startswith(N.BEGIN) and out.strip().endswith(N.END)
    answer = json.loads(out.split(N.BEGIN, 1)[1].split(N.END, 1)[0])
    assert answer['ok'] is True and 'python' in answer


def test_modctl_turns_a_bad_op_into_an_answer_not_a_crash(capsys):
    modctl.main(['modctl.py', base64.b64encode(b'{"op": "launch_missiles"}').decode()])
    answer = json.loads(capsys.readouterr().out.split(N.BEGIN, 1)[1].split(N.END, 1)[0])
    assert answer['ok'] is False and 'launch_missiles' in answer['error']


def test_a_failed_install_step_leaves_the_node_marked_error(registry):
    n = N._new(name='doomed', target={'kind': 'docker', 'container': 'c'})
    with pytest.raises(N.NodeError):
        N._step(n, 'deps', FakeTransport('no space left on device', ok=False),
                'pip install …')
    saved = N.get(n['id'])
    assert saved['state'] == 'error' and 'deps' in saved['error']
    assert saved['log'][-1]['ok'] is False


# ── who is allowed ───────────────────────────────────────────────────────

def test_reading_the_market_is_public_but_spending_is_not():
    auth.guard('/search')
    auth.guard('/providers')
    for owned in ('/rent', '/stop', '/raw', '/nodes', '/node/sh', '/nodes/deploy'):
        with pytest.raises(auth.Denied):
            auth.guard(owned)
        auth.guard(owned, owner=True)


def test_your_own_key_buys_you_your_own_account_but_not_a_shell():
    auth.guard('/instances', keys={'vast': 'their-key'})
    auth.guard('/balance', keys={'vast': 'their-key'})
    with pytest.raises(auth.Denied):
        auth.guard('/node/sh', keys={'vast': 'their-key'})


def test_a_proxied_request_can_never_look_like_localhost():
    """The gateway forwards everything from 127.0.0.1, so loopback alone proves
    nothing — X-Forwarded-For is what tells the two apart."""
    assert auth.is_local('127.0.0.1', {})
    assert not auth.is_local('127.0.0.1', {'x-forwarded-for': '8.8.8.8'})
    assert not auth.is_local('10.1.2.3', {})


def test_the_token_is_the_only_other_way_in(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, 'SECRET_FILE', str(tmp_path / 'server.secret'))
    monkeypatch.setattr(auth, 'STATE', str(tmp_path))
    token = auth.secret()
    assert len(token) == 64 and auth.secret() == token, 'minted once, then stable'
    assert oct(os.stat(auth.SECRET_FILE).st_mode)[-3:] == '600'
    assert auth.authed({'authorization': f'Bearer {token}'}, '8.8.8.8')
    assert not auth.authed({'authorization': 'Bearer wrong'}, '8.8.8.8')
    assert not auth.authed({}, '8.8.8.8')


def test_an_unowned_mcp_call_cannot_deploy_or_spend():
    r = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                    'params': {'name': 'compute_deploy', 'arguments': {'docker': True}}})
    assert r['result']['isError'] is True
    assert 'owner-only' in r['result']['content'][0]['text']
    # …but searching still works for anyone
    r2 = mcp.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                     'params': {'name': 'compute_providers', 'arguments': {}}})
    assert r2['result']['isError'] is False


def test_the_node_tools_are_declared_like_every_other_tool():
    for name in ('compute_deploy', 'compute_nodes', 'compute_node_sh',
                 'compute_node_mods', 'compute_node_call', 'compute_node_push',
                 'compute_node_rm'):
        t = mcp.TOOLS[name]
        assert t['description'] and callable(t['handler'])
        assert t['inputSchema']['type'] == 'object'


# ── the whole thing, for real ────────────────────────────────────────────

def _docker_ready():
    try:
        if subprocess.run(['docker', 'info'], capture_output=True,
                          timeout=20).returncode != 0:
            return False
        r = subprocess.run(['docker', 'image', 'inspect', N.DEFAULT_IMAGE],
                           capture_output=True, timeout=20)
        return r.returncode == 0        # never pull inside a test
    except Exception:
        return False


@pytest.mark.skipif(not _docker_ready(),
                    reason='needs docker with ' + N.DEFAULT_IMAGE + ' already pulled')
def test_a_container_becomes_a_mod_node_and_answers_for_itself(registry):
    node = N.deploy(docker=1, name='pytest-node', wait=True, ports=[])
    try:
        assert node['state'] == 'ready', node.get('error')
        assert node['mod']['modules'] > 10, 'a bootstrapped node lists its modules'

        shell = N.sh(node['id'], 'python3 -V')
        assert shell['ok'] and 'Python 3' in shell['out']

        mods = N.ctl(node['id'], 'mods')['mods']
        assert 'chain' in mods and 'key' in mods

        fns = N.ctl(node['id'], 'fns', mod='hub')['fns']
        assert any(f['name'] == 'modules' for f in fns)

        answer = N.call(node['id'], 'hub', 'modules')
        assert answer['ok'] and answer['result']

        pushed = N.push(node['id'], 'compute')
        assert pushed['bytes'] > 1000
        assert 'compute' in N.ctl(node['id'], 'mods')['mods'], \
            'a pushed module is a module the node can now run'

        again = N.bootstrap(node['id'])
        assert again['state'] == 'ready'
        assert again['log'][-1]['out'] == 'already installed' \
            if isinstance(again.get('log'), list) else True
    finally:
        N.destroy(node['id'])
        assert N.get(node['id'])['state'] == 'gone'
