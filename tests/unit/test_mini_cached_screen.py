"""Offline checks of screening isolation, accounting and the candidate gate."""

import json

import pytest

from evaluation.analysis.mini_cached_screen_20261120.run import (
    benchmark_version, eligible, fixed_record, guard_request, request_stop, task_text, wire_usage,
)
from evaluation.analysis.mini_cached_screen_20261120.expand import remaining, SUBMODULE_PREPARE
from evaluation.analysis.mini_cached_screen_20261120.recovery import reusable


def test_task_exposes_only_selected_public_answer():
    case = {'summary': 'Nested computation is wrong.',
            'original_issue': 'The exact nested call is f(a & b). Expected diagonal output.',
            'evaluation_record': {'patch': 'GOLD_SECRET', 'test_patch': 'TEST_SECRET'},
            'files': 'PRIVATE_PATH', 'hints': 'PRIVATE_DIAGNOSIS'}
    partial = task_text(case, fixed_record(case, 'partial'))
    ideal = task_text(case, fixed_record(case, 'ideal'))
    assert 'f(a & b)' not in partial and 'f(a & b)' in ideal
    for task in (partial, ideal):
        assert 'Nested computation is wrong.' in task
        assert all(secret not in task for secret in
                   ('GOLD_SECRET', 'TEST_SECRET', 'PRIVATE_PATH', 'PRIVATE_DIAGNOSIS'))
    assert fixed_record(case, 'partial')['exchanges'][0]['question'] == fixed_record(case, 'ideal')['exchanges'][0]['question']


@pytest.mark.parametrize(('partial_fault', 'ideal_fault', 'partial_applied', 'ideal_applied', 'p', 'i', 'expected'), [
    (False, False, True, True, False, True, True),
    (True, False, True, True, False, True, False),
    (False, True, True, True, False, True, False),
    (False, False, False, True, False, True, False),
    (False, False, True, False, False, True, False),
    (False, False, True, True, True, True, False),
    (False, False, True, True, False, False, False),
    (False, False, True, True, None, True, False),
])
def test_candidate_gate_rejects_faults_and_missing_results(partial_fault, ideal_fault,
                                                         partial_applied, ideal_applied, p, i, expected):
    assert eligible({'execution_fault': partial_fault}, {'execution_fault': ideal_fault},
                    {'patch_successfully_applied': partial_applied, 'resolved': p},
                    {'patch_successfully_applied': ideal_applied, 'resolved': i}) is expected


def test_wire_accounting_keeps_failed_attempt_and_reasoning_subset(tmp_path):
    for n, response in enumerate([
        {'status_code': 429, 'body': {'error': {'message': 'quota exhausted'}}},
        {'status_code': 200, 'body': {'model': 'deepseek-flash',
         'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120,
                   'completion_tokens_details': {'reasoning_tokens': 15}}}},
    ], 1):
        (tmp_path / f'{n:03}.request.json').write_text('{}')
        (tmp_path / f'{n:03}.response.json').write_text(json.dumps(response))
    result = wire_usage(tmp_path)
    assert result['http_requests'] == result['http_responses'] == 2
    assert result['http_statuses'] == [429, 200]
    assert result['usage'] == {'prompt_tokens': 100, 'completion_tokens': 20,
                               'total_tokens': 120, 'reasoning_tokens': 15}


def test_request_guard_prevents_budget_overrun_and_calls_after_batch_stop(tmp_path):
    guard_request(tmp_path, 99)
    with pytest.raises(RuntimeError, match='budget exhausted'):
        guard_request(tmp_path, 100)
    (tmp_path / 'STOP.json').write_text('{}')
    with pytest.raises(RuntimeError, match='before HTTP'):
        guard_request(tmp_path, 0)


def test_remaining_selection_does_not_repeat_completed_or_excluded_instances():
    inventory = [{'instance_id': iid} for iid in ['done', 'new-a', '10554', 'new-b']]
    assert remaining(inventory, ['done'], ['10554']) == ['new-a', 'new-b']
    assert remaining(inventory, ['done', 'new-a', 'new-b'], ['10554']) == []


def test_submodule_preparation_keeps_literal_audit_marker_and_valid_python():
    code = SUBMODULE_PREPARE.replace('BASE_COMMIT_VALUE', repr('a'*40))
    compile(code, '<offline-submodule-preparation>', 'exec')
    assert 'BASELINE_SUBMODULES=' in code
    assert "'fetch'" not in code and "'pull'" not in code
    assert "'reset','--hard',expected" in code


def test_excel_integer_versions_are_normalized_without_changing_minor_versions():
    assert benchmark_version('5') == '5.0'
    assert benchmark_version(3) == '3.0'
    assert benchmark_version('3.1') == '3.1'
    assert benchmark_version('5.0') == '5.0'


def test_secondary_worker_cannot_overwrite_first_stop_cause(tmp_path):
    first = {'instance_id': '14365', 'reason': 'format error'}
    second = {'instance_id': '14182', 'reason': 'batch already stopped'}
    assert request_stop(tmp_path, first) is True
    assert request_stop(tmp_path, second) is False
    assert json.loads((tmp_path / 'STOP.json').read_text()) == first
    assert len(list((tmp_path / 'stop_events').glob('*.json'))) == 2


def test_recovery_reuses_completed_but_never_failed_attempts(tmp_path):
    assert not reusable(tmp_path)
    (tmp_path / 'result.json').write_text(json.dumps({'exit_status': 'RepeatedFormatError', 'execution_fault': True}))
    assert not reusable(tmp_path)
    (tmp_path / 'result.json').write_text(json.dumps({'exit_status': 'Submitted', 'execution_fault': False, 'tool_calls': 1}))
    with pytest.raises(RuntimeError, match='missing required artifact'):
        reusable(tmp_path)
    for name in ('trajectory.json', 'task.txt'):
        (tmp_path / name).write_text('{}')
    (tmp_path / 'model.patch').write_text('actual diff')
    (tmp_path / 'predictions.json').write_text(json.dumps({'model_patch': 'actual diff'}))
    (tmp_path / 'tool_events.json').write_text('[{}]')
    assert reusable(tmp_path)
    (tmp_path / 'predictions.json').write_text(json.dumps({'model_patch': 'different diff'}))
    with pytest.raises(AssertionError):
        reusable(tmp_path)
