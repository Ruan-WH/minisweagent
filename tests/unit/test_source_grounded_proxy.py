"""Grounding gates, bounded correction, private-data boundary and Markdown evidence."""

import json
from types import SimpleNamespace

import pytest

from evaluation.utils.clarification_evidence import markdown_evidence_parts
from evaluation.utils.source_grounded_proxy import SourceGroundedProxy, validate_answer


SOURCE = {'issue': 'First display succeeds.\n>>> qs.order_by().values_list(\'pk\', flat=True)\nThen original fails.',
          'hints': 'A copy might help; the strategy is not confirmed.'}
ANSWER = {'answer': "Evaluate `qs.order_by().values_list('pk', flat=True)`, then display the original again; it fails.",
          'source_quotes': [{'source': 'issue', 'quote': SOURCE['issue']}]}
ACCEPT = {'accepted': True, 'reasons': []}


def make_proxy(tmp_path, replies):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        value = replies[len(calls) - 1]
        if isinstance(value, Exception):
            raise value
        text = json.dumps(value)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=None))],
            model='deepseek-flash', usage=None,
            model_dump=lambda: {'model': 'deepseek-flash', 'content': text},
        )

    fixture = {
        'initial_responses': {'question': 'What happened?',
                              'partial': {'answer': 'The first display succeeds.',
                                          'disclosed_fact_ids': ['F1']}},
        'private_source': {**SOURCE, 'files': 'DO_NOT_SEND_FILES', 'input_snapshot': 'DO_NOT_SEND_PATH'},
        'private_facts': ['DO_NOT_SEND_FACTS'], 'private_release_rules': ['DO_NOT_SEND_RULES'],
    }
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    proxy = SourceGroundedProxy(fixture, 'partial', client, 'deepseek-flash',
                                tmp_path / 'interactions.json', 0, 1)
    proxy.initial_exchange()
    return proxy, calls


def test_valid_natural_answer_requires_review_and_hides_audit_fields(tmp_path):
    proxy, calls = make_proxy(tmp_path, [ANSWER, ACCEPT])
    assert proxy.generate_reply(SimpleNamespace(content='What happens next?')) == ANSWER['answer']
    assert len(calls) == 2
    assert 'DO_NOT_SEND' not in json.dumps(calls)
    assert proxy.public_history[-1] == {'question': 'What happens next?', 'answer': ANSWER['answer']}
    assert all(c['response_format'] == {'type': 'json_object'} for c in calls)
    proxy.clarification_closed = True
    proxy.generate_reply(SimpleNamespace(content='Again?'))
    assert len(calls) == 2


def test_repaired_code_is_blocked_then_corrected_once(tmp_path):
    bad = {**ANSWER, 'answer': "```python\nqs.order_by('invented').values_list('pk', flat=True)\n```"}
    proxy, calls = make_proxy(tmp_path, [bad, ANSWER, ACCEPT])
    assert proxy.generate_reply(SimpleNamespace(content='Which operation?')) == ANSWER['answer']
    saved = json.loads((tmp_path / 'interactions.json').read_text())[-1]
    assert 'verbatim' in saved['attempts'][0]['validation_error']
    assert len(calls) == 3


def test_semantic_rejection_is_not_disclosed_and_stops_after_two_candidates(tmp_path):
    rejected = {'accepted': False, 'reasons': ['Unsolicited causal explanation stated as fact']}
    proxy, calls = make_proxy(tmp_path, [ANSWER, rejected, ANSWER, rejected])
    with pytest.raises(ValueError, match='bounded source validation'):
        proxy.generate_reply(SimpleNamespace(content='What happened?'))
    assert len(calls) == 4 and proxy.turns == 1
    assert len(proxy.public_history) == 1
    assert len(json.loads((tmp_path / 'interactions.json').read_text())[-1]['attempts']) == 4


def test_transport_error_propagates_without_retry(tmp_path):
    proxy, calls = make_proxy(tmp_path, [RuntimeError('quota exhausted')])
    with pytest.raises(RuntimeError, match='quota'):
        proxy.generate_reply(SimpleNamespace(content='What happened?'))
    assert len(calls) == 1 and proxy.turns == 1


def test_false_citations_and_unattributed_facts_are_rejected():
    with pytest.raises(ValueError, match='exact substring'):
        validate_answer({**ANSWER, 'source_quotes': [{'source': 'issue', 'quote': 'Fake observation'}]}, SOURCE)
    with pytest.raises(ValueError, match='citations'):
        validate_answer({'answer': 'It is a clone bug.', 'source_quotes': []}, SOURCE)
    assert validate_answer({'answer': 'I do not have that information in the supplied source.',
                            'source_quotes': []}, SOURCE)


def test_markdown_fence_labels_are_presentation_but_code_is_exact():
    public = "Sequence:\n```python\nqs.order_by().values_list('pk', flat=True)\n```\nOriginal fails."
    exchanges = [{'answer': public}]
    assert markdown_evidence_parts(public.replace('```python', '```pycon'), exchanges) == [public]
    assert not markdown_evidence_parts(public.replace('flat=True', 'flat=False'), exchanges)
    assert not markdown_evidence_parts(public.replace('Original fails', 'Original succeeds'), exchanges)
    assert not markdown_evidence_parts(public.replace('values_list', 'values'), exchanges)


def test_unsolicited_causal_language_is_blocked_before_model_review():
    for text in ["It fails because query state is shared.",
                 "This is due to changes to the underlying query.",
                 "The operation altered its internal query state.",
                 "因为底层查询对象发生改变，所以报错。"]:
        with pytest.raises(ValueError, match='causal explanations'):
            validate_answer({**ANSWER, 'answer': text}, SOURCE)


def test_requirements_policy_allows_later_decision_but_blocks_source_advice(tmp_path):
    source = {'issue': 'Proposal: postpone the behavior change.',
              'hints': 'Agreed: change the public behavior now. Delete a few lines of code and add a test.'}
    value = {'answer': 'The later agreement is to change the public behavior now.',
             'source_quotes': [{'source': 'hints', 'quote': 'Agreed: change the public behavior now.'}]}
    assert validate_answer(value, source, requirements_only=True) == value['answer']
    for answer in ['Delete a few lines of code and add a test.', 'Edit package/core.py.',
                   '```python\npass\n```']:
        with pytest.raises(ValueError, match='Requirements-only'):
            validate_answer({**value, 'answer': answer}, source, requirements_only=True)
    proxy, calls = make_proxy(tmp_path, [value, ACCEPT])
    proxy.fixture['proxy_policy'] = 'requirements-issue-hints-v4'
    proxy.fixture['private_source'].update(source)
    assert proxy.generate_reply(SimpleNamespace(content='What behavior is expected?')) == value['answer']
    assert 'explicit later decision' in calls[0]['messages'][0]['content']
    assert 'implementation instructions' in calls[1]['messages'][0]['content']
    assert all('json' in json.dumps(call['messages']).lower() for call in calls)
    assert 'DO_NOT_SEND' not in json.dumps(calls)
