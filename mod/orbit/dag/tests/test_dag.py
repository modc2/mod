"""dag tests.

Split in two. The offline half never touches the network: parsing, cycle
detection, reference resolution, the scheduler's failure and skip rules, and
the shaping operators — all of it exercised against `expr` steps and a stubbed
target, because the interesting behaviour of a DAG runner is what it does with
a graph, not what any particular tool returns.

The online half runs three real graphs against the live fleet and is skipped
when the hub is not there, or with DAG_OFFLINE=1.
"""

import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from dagsrc import refs, store, targets                    # noqa: E402
from dagsrc.graph import Graph, SpecError                  # noqa: E402
from dagsrc.plan import plan                               # noqa: E402
from dagsrc.runner import Run, run                         # noqa: E402


def g(steps, **kw):
    return {'name': kw.pop('name', 'test'), 'steps': steps, **kw}


# ── references ───────────────────────────────────────────────────

CTX = {'steps': {'a': {'ok': True, 'status': 'ok',
                       'out': {'items': [{'usd': 3}, {'usd': 1}], 'n': 7}}},
       'inputs': {'w': 'abc'}, 'env': {}, 'run': {'id': 'r1'}}


def test_whole_reference_keeps_its_type():
    assert refs.resolve('${a.n}', CTX, {'a'}) == 7
    assert refs.resolve('${a.items}', CTX, {'a'}) == [{'usd': 3}, {'usd': 1}]


def test_interpolation_stringifies():
    assert refs.resolve('n is ${a.n}', CTX, {'a'}) == 'n is 7'


def test_a_field_over_a_list_maps():
    assert refs.resolve('${a.items.usd}', CTX, {'a'}) == [3, 1]


def test_missing_is_an_error_unless_marked_optional():
    with pytest.raises(refs.RefError):
        refs.resolve('${a.nope}', CTX, {'a'})
    assert refs.resolve('${a.nope?}', CTX, {'a'}) is None


def test_the_error_names_the_expression_and_the_keys_there_are():
    with pytest.raises(refs.RefError) as e:
        refs.resolve('${a.nope}', CTX, {'a'})
    assert 'a.nope' in str(e.value) and 'items' in str(e.value)


def test_edges_are_read_off_the_arguments():
    assert refs.depends_on({'x': '${a.n}', 'y': '${steps.b.out}'}, {'a'}) == {'a', 'b'}


def test_env_hides_what_looks_like_a_credential():
    os.environ['DAG_TEST_API_KEY'] = 'sk-live'
    os.environ['DAG_TEST_COLOUR'] = 'blue'
    view = refs.env_view()
    assert 'DAG_TEST_API_KEY' not in view
    assert view['DAG_TEST_COLOUR'] == 'blue'


# ── parsing ──────────────────────────────────────────────────────

def test_independent_steps_land_in_one_wave():
    graph = Graph(g([{'id': 'a', 'value': 1}, {'id': 'b', 'value': 2},
                     {'id': 'c', 'value': '${a}${b}'}]))
    assert graph.levels() == [['a', 'b'], ['c']]


def test_a_cycle_is_refused_and_named():
    with pytest.raises(SpecError) as e:
        Graph(g([{'id': 'a', 'value': '${b}'}, {'id': 'b', 'value': '${a}'}]))
    assert 'cycle' in str(e.value) and 'a -> b -> a' in str(e.value)


def test_a_reference_to_a_step_that_does_not_exist_is_refused():
    with pytest.raises(SpecError) as e:
        Graph(g([{'id': 'a', 'value': '${ghost.x}', 'needs': ['ghost']}]))
    assert 'ghost' in str(e.value)


def test_an_unknown_step_field_is_refused_rather_than_ignored():
    with pytest.raises(SpecError) as e:
        Graph(g([{'id': 'a', 'value': 1, 'retrys': 3}]))
    assert 'retrys' in str(e.value)


def test_duplicate_ids_are_refused():
    with pytest.raises(SpecError):
        Graph(g([{'id': 'a', 'value': 1}, {'id': 'a', 'value': 2}]))


def test_inputs_are_bound_with_defaults_and_required_ones_enforced():
    graph = Graph(g([{'id': 'a', 'value': '${inputs.x}'}],
                    inputs={'x': {'required': True}, 'y': {'default': 5}}))
    assert graph.bind({'x': 1}) == {'x': 1, 'y': 5}
    with pytest.raises(SpecError):
        graph.bind({})


def test_steps_may_be_written_as_a_dict_keyed_by_id():
    graph = Graph(g({'a': {'value': 1}, 'b': {'value': '${a}'}}))
    assert graph.order == ['a', 'b']


# ── the scheduler ────────────────────────────────────────────────

def test_output_flows_from_one_step_to_the_next():
    rec = run(g([{'id': 'a', 'value': {'n': 2}},
                 {'id': 'b', 'value': 'n=${a.n}'}], output='${b}'),
              persist=False)
    assert rec['status'] == 'ok' and rec['outputs'] == 'n=2'


def test_leaves_are_the_output_when_none_is_declared():
    rec = run(g([{'id': 'a', 'value': 1}, {'id': 'b', 'value': '${a}'},
                 {'id': 'c', 'value': 9}]), persist=False)
    assert set(rec['outputs']) == {'b', 'c'}


def test_a_failure_skips_what_depended_on_it_and_says_why():
    rec = run(g([{'id': 'bad', 'value': '${nothing.here?}', 'pick': 'x'},
                 {'id': 'after', 'value': '${bad}'},
                 {'id': 'apart', 'value': 'fine'}]), persist=False)
    by = {s['id']: s for s in rec['steps']}
    assert rec['status'] == 'failed'
    assert by['bad']['status'] == 'failed'
    assert by['after']['status'] == 'skipped' and 'bad' in by['after']['reason']
    assert by['apart']['status'] == 'ok'      # an unrelated branch still runs


def test_continue_on_error_keeps_the_run_alive():
    rec = run(g([{'id': 'bad', 'value': '${x.y?}', 'pick': 'nope',
                  'continue_on_error': True},
                 {'id': 'after', 'value': 'reached'}]), persist=False)
    assert rec['status'] == 'ok'


def test_if_skips_without_failing():
    rec = run(g([{'id': 'a', 'value': 0},
                 {'id': 'b', 'value': 'ran', 'if': '${a}'},
                 {'id': 'c', 'value': 'ran', 'unless': '${a}'}]), persist=False)
    by = {s['id']: s for s in rec['steps']}
    assert by['b']['status'] == 'skipped' and by['c']['status'] == 'ok'
    assert rec['status'] == 'ok'


def test_foreach_runs_once_per_item_and_keeps_the_order():
    rec = run(g([{'id': 'each', 'foreach': [10, 20, 30],
                  'value': '${index}:${item}'}], output='${each}'), persist=False)
    assert rec['outputs'] == ['0:10', '1:20', '2:30']


def test_foreach_over_an_empty_list_is_not_a_failure():
    rec = run(g([{'id': 'each', 'foreach': [], 'value': '${item}'}]), persist=False)
    assert rec['status'] == 'ok' and rec['outputs']['each'] == []


def test_foreach_refuses_something_that_is_not_a_list():
    rec = run(g([{'id': 'each', 'foreach': '${inputs.x}', 'value': '${item}'}],
                inputs={'x': {'default': 5}}), persist=False)
    assert rec['status'] == 'failed'
    assert 'list' in rec['steps'][0]['error']


def test_where_sort_limit_and_pick_shape_a_result():
    rows = [{'s': 'a', 'v': 1}, {'s': 'b', 'v': 9}, {'s': 'c', 'v': 5}]
    rec = run(g([{'id': 'rows', 'value': rows},
                 {'id': 'top', 'value': '${rows}', 'where': [['v', '>', 1]],
                  'sort_by': 'v', 'desc': True, 'limit': 1, 'pick': 's'}],
                output='${top}'), persist=False)
    assert rec['outputs'] == ['b']


def test_a_limit_may_come_from_an_input():
    rec = run(g([{'id': 'rows', 'value': [1, 2, 3, 4]},
                 {'id': 'cut', 'value': '${rows}', 'limit': '${inputs.n}'}],
                inputs={'n': {'default': 2}}, output='${cut}'), persist=False)
    assert rec['outputs'] == [1, 2]


def test_independent_steps_actually_run_at_the_same_time():
    """Three steps that each sleep 0.4s. In series that is 1.2s."""
    graph = Graph(g([{'id': f's{i}', 'use': 'mod', 'call': 'x/y'}
                     for i in range(3)], max_parallel=4))
    r = Run(graph, persist=False)
    r._invoke = lambda step, extra=None: (time.sleep(0.4), step.id)[1]
    t0 = time.time()
    rec = r.execute()
    assert rec['status'] == 'ok'
    assert time.time() - t0 < 0.9


def test_a_retried_step_is_only_retried_while_it_might_work():
    graph = Graph(g([{'id': 'a', 'use': 'mod', 'call': 'x/y', 'retries': 2,
                      'retry_delay': 0}]))
    r, calls = Run(graph, persist=False), []

    def flaky(step, extra=None):
        calls.append(1)
        if len(calls) < 3:
            raise targets.StepError('upstream hiccup', kind='http')
        return 'ok'

    r._invoke = flaky
    assert r.execute()['status'] == 'ok' and len(calls) == 3


def test_a_refusal_that_will_refuse_again_is_not_retried():
    graph = Graph(g([{'id': 'a', 'use': 'mod', 'call': 'x/y', 'retries': 3,
                      'retry_delay': 0}]))
    r, calls = Run(graph, persist=False), []

    def refuse(step, extra=None):
        calls.append(1)
        raise targets.StepError('no such fn', kind='fn')

    r._invoke = refuse
    assert r.execute()['status'] == 'failed' and len(calls) == 1


def test_a_dry_run_calls_nothing_and_shows_the_resolved_arguments():
    graph = Graph(g([{'id': 'a', 'value': {'mint': 'M'}},
                     {'id': 'b', 'tool': 'solana__sol_token',
                      'args': {'mint': '${a.mint}'}}]))
    rec = Run(graph, persist=False, dry_run=True).execute()
    by = {s['id']: s for s in rec['steps']}
    assert rec['status'] == 'ok'
    assert by['b']['out']['would_call'] == 'solana__sol_token'
    assert by['b']['out']['with'] == {'mint': 'M'}


# ── MCP envelopes ────────────────────────────────────────────────

def test_a_tool_result_is_unwrapped_to_the_data_inside_it():
    env = {'content': [{'type': 'text', 'text': '{"usd": 4}'}], 'isError': False}
    assert targets.unwrap(env, 't') == {'usd': 4}


def test_structured_content_wins_over_the_text_block():
    env = {'content': [{'type': 'text', 'text': 'ignored'}],
           'structuredContent': {'usd': 4}}
    assert targets.unwrap(env, 't') == {'usd': 4}


def test_an_error_result_becomes_a_failed_step_not_a_value():
    env = {'content': [{'type': 'text', 'text': '{"error": "bad mint"}'}],
           'isError': True}
    with pytest.raises(targets.StepError) as e:
        targets.unwrap(env, 'sol_token')
    assert 'bad mint' in str(e.value)


def test_plain_text_survives_unwrapping():
    assert targets.unwrap({'content': [{'type': 'text', 'text': 'hello'}]},
                          't') == 'hello'


# ── offline planning ─────────────────────────────────────────────

def test_planning_prices_the_run_and_draws_it():
    p = plan(g([{'id': 'a', 'tool': 's__t'}, {'id': 'b', 'tool': 's__u'},
                {'id': 'c', 'value': '${a}${b}'}]), check_tools=False)
    assert p['calls'] == 2 and p['waves'] == 2
    assert 'a' in p['ascii']


def test_planning_flags_a_reference_to_an_undeclared_input():
    p = plan(g([{'id': 'a', 'value': '${inputs.ghost}'}]), check_tools=False)
    assert any(i['issue'] == 'undeclared_input' for i in p['issues'])


# ── the store ────────────────────────────────────────────────────

def test_a_graph_round_trips_through_the_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'GRAPHS', str(tmp_path / 'graphs'))
    monkeypatch.setattr(store, 'RUNS', str(tmp_path / 'runs'))
    spec = g([{'id': 'a', 'value': 1}], name='kept')
    store.save_graph('kept', spec)
    assert store.load_graph('kept')['steps'][0]['id'] == 'a'
    assert [x['name'] for x in store.graphs()] == ['kept']
    store.delete_graph('kept')
    with pytest.raises(store.StoreError):
        store.load_graph('kept')


def test_a_missing_graph_says_what_there_is(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'GRAPHS', str(tmp_path / 'graphs'))
    with pytest.raises(store.StoreError) as e:
        store.load_graph('nope')
    assert 'nope' in str(e.value)


# ── against the live fleet ───────────────────────────────────────

def _fleet_is_up():
    if os.environ.get('DAG_OFFLINE'):
        return False
    try:
        return len(targets.tool_index(timeout=5)) > 0
    except Exception:
        return False


online = pytest.mark.skipif(not _fleet_is_up(),
                            reason='the MCP hub is not answering')


@online
def test_the_tool_catalogue_is_searchable():
    from dagsrc import mcp as srv
    out = srv.call_tool('dag_tools', {'q': 'portfolio', 'limit': 5})
    assert out['total'] > 50 and out['tools']


@online
def test_every_shipped_example_still_checks_out():
    import json
    root = os.path.join(HERE, 'examples')
    for f in sorted(os.listdir(root)):
        with open(os.path.join(root, f)) as fh:
            spec = json.load(fh)
        p = plan(spec, inputs={k: (v.get('default') or 'x') for k, v in
                               (spec.get('inputs') or {}).items()})
        assert p['ok'], f'{f}: {p["issues"]}'


@online
def test_a_real_two_wave_graph_runs():
    rec = run(g([{'id': 'price', 'tool': 'solana__sol_price',
                  'args': {'ids': 'SOL'}},
                 {'id': 'usd', 'value': '${price.prices[0].usd}'}],
                output='${usd}'), persist=False)
    assert rec['status'] == 'ok', rec['steps']
    assert isinstance(rec['outputs'], (int, float)) and rec['outputs'] > 0


@online
def test_a_misspelled_tool_is_caught_before_it_costs_a_call():
    p = plan(g([{'id': 'a', 'tool': 'solana__sol_portfoli',
                 'args': {'address': 'x'}}]))
    assert not p['ok']
    assert 'sol_portfolio' in p['issues'][0]['message']
