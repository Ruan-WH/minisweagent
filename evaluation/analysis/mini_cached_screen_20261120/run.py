"""Prepare without API calls; explicitly run screening, then fresh candidate followups."""

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import tomllib

os.environ.setdefault('MSWEA_GLOBAL_CONFIG_DIR', '/tmp/ambig-mini-config')
os.environ.setdefault('MSWEA_SILENT_STARTUP', '1')
os.environ.setdefault('LITELLM_LOCAL_MODEL_COST_MAP', 'True')
os.environ['MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT'] = '1'

PROJECT = Path(__file__).resolve().parents[3]
MODULE = 'evaluation.analysis.mini_cached_screen_20261120.run'
ARCHIVE = PROJECT.parent / 'InteractiveSWEAgents/evaluation/evaluation_outputs'
DEFAULT_IDS = ['astropy__astropy-12907', 'astropy__astropy-13033',
               'astropy__astropy-13236', 'astropy__astropy-13398', 'astropy__astropy-13579']
CONFIG_NAME = 'hidden_deepseek_flash'
QUESTION = 'Please describe the reported problem and provide the available reproduction details and expected behavior.'


def now():
    return datetime.now(timezone.utc).isoformat()


def dump(path, value):
    temporary = path.with_suffix(path.suffix + f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def request_stop(output, value):
    """Preserve the first cause; keep later workers' stop events separately."""
    events = output / 'stop_events'
    events.mkdir(exist_ok=True)
    dump(events / f'{time.time_ns()}-{os.getpid()}.json', value)
    try:
        descriptor = os.open(output / 'STOP.json', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
    return True


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def settings():
    cfg = tomllib.loads((PROJECT / 'config.toml').read_text())['llm'][CONFIG_NAME]
    if cfg['model'] != 'openai/deepseek-flash':
        raise ValueError('This plan requires the existing DeepSeek configuration; no model substitution')
    return cfg


def public_settings(cfg):
    return {k: v for k, v in cfg.items() if 'key' not in k.lower()}


def benchmark_version(value):
    version = str(value)
    return version+'.0' if version.isdigit() else version


def fixed_record(case, condition):
    answer = case['summary'] if condition == 'partial' else case['original_issue']
    return {'protocol': 'cached-screen-fixed-initial-v1', 'status': 'not_run',
            'exchanges': [{'question': QUESTION, 'answer': answer}], 'checks': [],
            'stop_reason': 'initial_only_control', 'unresolved_information': '',
            'unavailable_information': [], 'error': None, 'followups_used': 0}


def task_text(case, record):
    from evaluation.utils.answer_check import checked_solving_instruction
    return checked_solving_instruction(case['summary'], record)


def source_paths():
    return sorted(set([
        *Path(__file__).parent.glob('*.py'),
        PROJECT / 'run_cached_screen.sh',
        *(PROJECT / 'evaluation/benchmarks/swe_bench/mini_swe').glob('*.py'),
        *(PROJECT / 'vendor/mini-swe-agent/src/minisweagent').rglob('*.py'),
        *[PROJECT / 'evaluation/utils' / name for name in
          ['reference_history.py', 'source_grounded_proxy.py', 'controlled_proxy.py',
           'simple_clarification.py', 'simple_followup.py', 'clarification_evidence.py', 'answer_check.py']],
    ]))


def child_env():
    env = dict(os.environ)
    for key in ('ALL_PROXY', 'all_proxy', 'HTTP_PROXY', 'http_proxy', 'HTTPS_PROXY', 'https_proxy'):
        env.pop(key, None)
    env['PYTHONPATH'] = str(PROJECT / 'vendor/mini-swe-agent/src') + os.pathsep + str(PROJECT)
    env['PYTHONUNBUFFERED'] = '1'
    return env


def canonical_records():
    canonical = {}
    for name in ('deepseek-targeted-v1-20261027', 'targeted-v1-20261027',
                 'qwen-new7-grounded-v1-20261101'):
        path = ARCHIVE / name / 'official_eval/dataset.json'
        if path.exists():
            for row in json.loads(path.read_text()):
                canonical.setdefault(row['instance_id'], (row, str(path), sha(path)))
    return canonical


def prepare(output, ids, grader_python, image_overrides=None, dependency_caches=None, conditions=None, proxy_source="issue"):
    import docker
    import pandas as pd
    import minisweagent
    from evaluation.analysis.mini_cached_screen_20261120.environment import setup, save_dependencies

    output.mkdir(parents=True, exist_ok=False)
    cfg = settings()
    conditions = conditions or ['partial', 'ideal']
    if conditions not in (['partial', 'ideal'], ['full'], ['followup']):
        raise ValueError('Supported plans are paired screening, full-only or followup-only')
    dataset = PROJECT / 'evaluation/benchmarks/swe_bench/data/full_summaries_verified.xlsx'
    rows = pd.read_excel(dataset, dtype={'version': str}).fillna('').to_dict('records')
    by_id = {r['instance_id']: r for r in rows}
    if not ids or len(set(ids)) != len(ids):
        raise ValueError('Need unique selected IDs')
    client = docker.from_env()
    inventory = []
    for row in rows:
        iid = row['instance_id']
        names = [f'sweb.eval.x86_64.{iid}:latest',
                 'xingyaoww/sweb.eval.x86_64.' + iid.replace('__', '_s_') + ':latest']
        cached = []
        for name in names:
            try:
                image = client.images.get(name)
                cached.append({'name': name, 'id': image.id})
            except docker.errors.ImageNotFound:
                pass
        if cached:
            inventory.append({'instance_id': iid, 'images': cached})
    dump(output / 'cached_inventory.json', inventory)
    cases = []
    # Prefer canonical issue text from previously cached evaluator datasets.
    canonical = canonical_records()
    for iid in ids:
        row = by_id[iid]
        image_name = (image_overrides or {}).get(iid, f'sweb.eval.x86_64.{iid}:latest')
        image = client.images.get(image_name)
        # The evaluator remains host-only. No gold or test data enters the solver container.
        if iid in canonical:
            evaluation, origin, digest = canonical[iid]
            assert evaluation['base_commit'] == row['base_commit']
        else:
            evaluation = {k: v for k, v in row.items() if k not in ('original_issue', 'files')}
            evaluation['problem_statement'] = row['original_issue'].replace('_x000D_', '\r').replace('\r\n', '\n').replace('\r', '\n')
            origin, digest = str(dataset), sha(dataset)
        evaluation = dict(evaluation)
        evaluation['version'] = benchmark_version(evaluation['version'])
        if not evaluation.get('environment_setup_commit'):
            evaluation.pop('environment_setup_commit', None)
        case = {'instance_id': iid, 'base_commit': row['base_commit'],
                'package': row['repo'].split('/')[0], 'summary': row['problem_statement'],
                'original_issue': evaluation['problem_statement'],
                'image_name': image_name, 'image_id': image.id,
                'evaluation_record': evaluation, 'source_dataset': origin, 'source_sha256': digest}
        if conditions == ['followup'] and proxy_source == 'issue-hints':
            case['proxy_hints'] = row['hints_text']
            case['proxy_policy'] = 'requirements-issue-hints-v4'
        if conditions == ['full']:
            from evaluation.benchmarks.swe_bench.mini_swe.run import build_task
            case['full_task'] = build_task(row, 'full')
        if iid in (dependency_caches or {}):
            case['dependency_cache'] = dependency_caches[iid]
        cases.append(case)
        public_dir = output / 'inputs' / iid
        public_dir.mkdir(parents=True)
        (public_dir / 'summary.txt').write_text(case['summary'])
        (public_dir / 'original_issue.txt').write_text(case['original_issue'])
        (public_dir / 'spreadsheet_original_raw.txt').write_text(row['original_issue'])
        if conditions == ['full'] or (conditions == ['followup'] and proxy_source == 'issue-hints'):
            (public_dir / 'hints_raw.txt').write_text(row['hints_text'])
        for condition in conditions:
            task = (case['full_task'] if condition == 'full' else
                    task_text(case, fixed_record(case, 'partial' if condition == 'followup' else condition)))
            name = 'followup.initial.task.txt' if condition == 'followup' else f'{condition}.task.txt'
            (public_dir / name).write_text(task)
    client.close()
    sources = {}
    for path in source_paths():
        relative = str(path.relative_to(PROJECT))
        target = output / 'frozen_source' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        sources[relative] = sha(path)
    plan = {'created_at': now(), 'prepared': False, 'workers': 2, 'grading_workers': 1,
            'solver_python': sys.executable, 'grader_python': str(grader_python),
            'config_name': CONFIG_NAME, 'model_settings': public_settings(cfg),
            'mini_version': minisweagent.__version__,
            'mini_commit': json.loads((PROJECT / 'EXPORT_PROVENANCE.json').read_text())['mini_commit'],
            'mini_import_path': minisweagent.__file__,
            'host_package_versions': {name: importlib.metadata.version(name) for name in
                                      ('openai', 'httpx', 'litellm', 'pydantic')},
            'max_model_calls': 100, 'max_tool_calls': 100, 'command_timeout': 120,
            'wall_time_limit': 7200, 'api_retries': 2, 'whole_request_attempts': 1,
            'tool_choice': 'auto', 'parallel_tool_calls': False,
            'allow_native_text_final': True,
            'protocol_revision': 'native-v3-standard-stop-text-final',
            'final_policy': 'Nonempty native text with finish_reason=stop and no tool/function calls '
                            'ends as FinalResponse. No tool execution, no inferred test success.',
            'thinking': 'No override; preserve existing endpoint default and reasoning content',
            'conditions': conditions, 'source_sha256': sources,
            'run_mode': ('full_only' if conditions == ['full'] else
                         'followup_only' if conditions == ['followup'] else 'paired_screening'),
            'input_policy': 'New screening fixtures: fixed generic question; partial answer=existing summary; '
                            'ideal answer=original issue only. No hints, files, gold, hidden tests or new QA generation. '
                            'These are not the saved 10554 ideal/partial QAs.',
            'followup_policy': 'Fresh run only when clean partial resolved=false and clean ideal resolved=true; '
                               'all DeepSeek; simple-focused-answer-v1 checker; max2; '
                               'source-grounded-reviewed-v3 proxy, original issue only; no rule table',
            'checkpoint20': 'Automatic transport/alignment/failure-sentinel checks; no repair hints',
            'resources': {'cpu': 'Intel Core i9-14900HX,32 logical CPUs',
                          'ram_total_gib': 15.4, 'observed_available_gib': 6.3,
                          'observed_swap_used_gib': 4.2,
                          'reason_for_2_workers': 'Memory and swap pressure; 4 concurrent containers not recommended',
                          'container_memory_limit_gib': 6, 'container_memory_swap_limit_gib': 8},
            'cases': cases}
    if conditions == ['full']:
        plan['input_policy'] = ('Exact existing mini_swe.run.build_task(row, full): original_issue + '
                                'Later discussion / hints + hints_text from the repository spreadsheet. '
                                'No Q/A wrapper, checker, proxy, file hints or hidden evaluation feedback.')
        plan['followup_policy'] = 'Disabled: full-only batch'
    if conditions == ['followup']:
        plan['input_policy'] = ('Exact existing partial summary and fixed initial answer; then public clarification exchanges. '
                                'Checker sees no private source; proxy sees original issue only, hints empty. '
                                'Only public exchanges and uncertainty reach repair; no full-run patch or evaluation feedback.')
        plan['followup_policy'] = ('User-selected diagnostic outside the original partial-fail/ideal-pass gate; '
                                   'same all-DeepSeek checker/proxy, max2 followups, new baseline repair.')
        plan['proxy_source'] = proxy_source
        if proxy_source == 'issue-hints':
            plan['input_policy'] = plan['input_policy'].replace('original issue only, hints empty', 'original issue + raw spreadsheet hints; requirements-only disclosure')
            plan['followup_policy'] += '; requirements-issue-hints-v4; no code/edit locations/implementation advice/diagnosis'
        plan['candidate_selection_override'] = 'Explicit user request; do not count as an original-gate screening hit'
    plan['input_sha256'] = {str(p.relative_to(output)): sha(p) for p in (output / 'inputs').rglob('*.txt')}
    dump(output / 'plan.json', plan)
    grader_check = subprocess.run([str(grader_python), '-B', '-m', MODULE, '_check-grader',
                                   '--output', str(output)], cwd=PROJECT, env=child_env(), check=False)
    if grader_check.returncode:
        raise RuntimeError('Grader cache/spec preflight failed; no model calls made')
    for case in cases:
        folder = output / 'preflight' / case['instance_id']
        folder.mkdir(parents=True)
        try:
            env, audit = setup(case)
        except Exception as exc:
            dump(folder / 'failure.json', {'at': now(), 'model_calls': 0,
                                           'error': type(exc).__name__ + ': ' + str(exc)})
            raise
        try:
            save_dependencies(folder, audit)
            dump(folder / 'audit.json', audit)
        finally:
            env.cleanup()
        print('PREFLIGHT PASS', case['instance_id'], flush=True)
    plan['prepared'] = True
    dump(output / 'plan.json', plan)
    print(f'PREPARED: {len(cases)} issues, {len(cases)*len(conditions)} runs, conditions={conditions}, workers=2; model_calls=0', flush=True)


def verify_plan(output):
    plan = json.loads((output / 'plan.json').read_text())
    if not plan['prepared']:
        raise RuntimeError('Preparation incomplete')
    if public_settings(settings()) != plan['model_settings']:
        raise RuntimeError('Model settings changed; prepare a new output directory')
    for relative, digest in plan['source_sha256'].items():
        if sha(PROJECT / relative) != digest:
            raise RuntimeError('Source changed; prepare a new output directory: ' + relative)
    for relative, digest in plan['input_sha256'].items():
        if sha(output / relative) != digest:
            raise RuntimeError('Frozen input changed: ' + relative)
    return plan


def wire_usage(folder):
    usage = dict(prompt_tokens=0, completion_tokens=0, total_tokens=0, reasoning_tokens=0)
    models, statuses = set(), []
    for path in sorted(folder.rglob('*.response.json')):
        response = json.loads(path.read_text())
        statuses.append(response['status_code'])
        body = response['body']
        models.add(body.get('model', ''))
        current = body.get('usage') or {}
        for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
            usage[key] += current.get(key, 0) or 0
        usage['reasoning_tokens'] += (current.get('completion_tokens_details') or {}).get('reasoning_tokens', 0) or 0
    return {'http_requests': len(list(folder.rglob('*.request.json'))),
            'http_responses': len(statuses), 'http_statuses': statuses,
            'returned_models': sorted(models), 'usage': usage}


def guard_request(output, request_count):
    if request_count >= 100:
        raise RuntimeError('Actual solver HTTP request budget exhausted (including SDK retries)')
    if (output / 'STOP.json').exists():
        raise RuntimeError('Batch stopped before HTTP request/retry')


def clarify(case, folder, cfg):
    import httpx
    from openai import OpenAI
    from evaluation.utils import simple_clarification
    from evaluation.utils.clarification_evidence import markdown_evidence_parts
    from evaluation.utils.simple_clarification import SimpleJSONChecker, run_simple_clarification
    from evaluation.utils.source_grounded_proxy import SourceGroundedProxy

    clients = []
    def client_for(role):
        wire = folder / 'clarification_http' / role
        wire.mkdir(parents=True)
        counts = {'request': 0, 'response': 0}
        def request(req):
            if (folder.parents[2] / 'STOP.json').exists():
                raise RuntimeError('Batch stopped before clarification request')
            counts['request'] += 1
            dump(wire / f"{counts['request']:03}.request.json",
                 {'method': req.method, 'url': str(req.url), 'body': json.loads(req.content)})
        def response(res):
            res.read()
            counts['response'] += 1
            try:
                body = res.json()
            except ValueError:
                body = {'raw_text': res.text}
            dump(wire / f"{counts['response']:03}.response.json", {'status_code': res.status_code, 'body': body})
        client = OpenAI(api_key=os.path.expandvars(cfg['api_key']), base_url=cfg['base_url'],
                        timeout=120, max_retries=0,
                        http_client=httpx.Client(event_hooks={'request': [request], 'response': [response]}))
        clients.append(client)
        return client
    fixture = {'proxy_policy': case.get('proxy_policy', 'source-grounded-reviewed-v3'),
               'private_source': {'issue': case['original_issue'], 'hints': case.get('proxy_hints', '')},
               'initial_responses': {'question': QUESTION,
                                     'partial': {'answer': case['summary'], 'disclosed_fact_ids': []}}}
    proxy = SourceGroundedProxy(fixture, 'partial', client_for('proxy'),
                                cfg['model'].removeprefix('openai/'), folder / 'interactions.json', 0, 1)
    exchange = proxy.initial_exchange()
    checker = SimpleJSONChecker(client_for('checker'), cfg['model'])
    previous = simple_clarification.public_evidence_parts
    simple_clarification.public_evidence_parts = markdown_evidence_parts
    try:
        record = run_simple_clarification(checker, case['summary'], [exchange], proxy,
                                          folder / 'answer_check.json', max_followups=2)
    finally:
        simple_clarification.public_evidence_parts = previous
        for client in clients:
            client.close()
    if record['status'] == 'failed':
        raise RuntimeError('Clarification failed; no repair run: ' + str(record['error']))
    return record


def checkpoint(case, env, original, events, folder):
    from evaluation.analysis.mini_cached_screen_20261120.environment import alignment
    wire = wire_usage(folder / 'http')
    assert wire['http_requests'] == wire['http_responses']
    assert all(code == 200 for code in wire['http_statuses'])
    assert not any('</parameter>' in e['command'] or 'mswea_bash_command>' in e['command'] for e in events)
    # Bypass instrumentation: these are host audits, not agent tools or repair hints.
    env.execute = original
    try:
        imported = alignment(env, case['package'])
        failure = original({'command': '(exit 7) | cat'})
        assert failure['returncode'] == 7
    finally:
        env.execute = env._instrumented_execute
    return {'passed': True, 'at': now(), 'tool_calls': len(events), 'wire': wire,
            'alignment': imported, 'failure_probe': failure,
            'limitation': 'Checks transport and a failure sentinel, not semantic truth of every agent test claim'}


def worker(output, iid, condition):
    from evaluation.analysis.mini_cached_screen_20261120.environment import setup, save_dependencies, verify_image
    from evaluation.benchmarks.swe_bench.mini_swe.agent import AmbigAgent, NATIVE_SYSTEM
    from evaluation.benchmarks.swe_bench.mini_swe.model import NativeBenchmarkModel, OBSERVATION, model_options
    from evaluation.benchmarks.swe_bench.mini_swe.runtime import extract_patch
    from minisweagent.exceptions import Submitted

    plan = verify_plan(output)
    case = next(c for c in plan['cases'] if c['instance_id'] == iid)
    folder = output / 'runs' / iid / condition
    folder.mkdir(parents=True, exist_ok=False)
    cfg = settings()
    start = time.monotonic()
    env = model = agent = original = None
    events, errors = [], []
    status, patch, solver_elapsed = 'NotStarted', '', 0.0
    dump(folder / 'started.json', {'at': now(), 'condition': condition})
    try:
        if (output / 'STOP.json').exists():
            raise RuntimeError('Batch stop requested')
        verify_image(case)
        if condition == 'full':
            record = {'status': 'not_run_full', 'exchanges': [], 'checks': [], 'followups_used': 0,
                      'stop_reason': 'full_input_no_clarification', 'error': None}
            task = case['full_task']
        else:
            record = clarify(case, folder, cfg) if condition == 'followup' else fixed_record(case, condition)
            task = task_text(case, record)
        if condition != 'followup':
            assert task == (output / 'inputs' / iid / f'{condition}.task.txt').read_text()
        dump(folder / 'answer_check.json', record)
        (folder / 'task.txt').write_text(task)
        dump(folder / 'input_manifest.json', {'sha256': sha(folder / 'task.txt'), 'extra_file_hints': False,
                                             'official_feedback': False, 'condition': condition})
        env, audit = setup(case)
        save_dependencies(folder, audit)
        if (folder / 'dependencies.txt').read_bytes() != (output / 'preflight' / iid / 'dependencies.txt').read_bytes():
            raise RuntimeError('Dependencies changed since preparation')
        dump(folder / 'preflight.json', audit)
        original = env.execute
        solver_start = time.monotonic()
        def execute(action, *args, **kwargs):
            if len(events) >= 100 or (output / 'STOP.json').exists():
                raise RuntimeError('Tool budget or batch stop')
            if '</parameter>' in action['command'] or 'mswea_bash_command>' in action['command']:
                raise RuntimeError('Tool protocol residue; stop before execution')
            event = {'tool_call': len(events)+1, 'command': action['command'],
                     'elapsed_start_seconds': time.monotonic()-solver_start}
            t = time.monotonic()
            try:
                result = original(action, *args, **kwargs)
                event['result'] = result
                return result
            except Submitted as exc:
                event['result'] = exc.messages[0]['extra']['command_output']
                raise
            except BaseException as exc:
                event['exception'] = type(exc).__name__ + ': ' + str(exc)
                raise
            finally:
                event['duration_seconds'] = time.monotonic()-t
                diff = original({'command': 'git diff --name-only ' + case['base_commit']})
                if diff['returncode']:
                    raise RuntimeError('Unable to inspect actual source diff')
                event['tracked_changes'] = diff['output'].splitlines()
                events.append(event)
                dump(folder / 'tool_events.json', events)
                print(f"{iid}/{condition}: tool={len(events)}, elapsed={time.monotonic()-solver_start:.1f}s", flush=True)
                if len(events) == 20:
                    dump(folder / 'checkpoint20.json', checkpoint(case, env, original, events, folder))
        env._instrumented_execute = execute
        env.execute = execute
        options = model_options(cfg)
        class GuardedModel(NativeBenchmarkModel):
            def _record_request(self, request):
                guard_request(output, self.http_requests)
                super()._record_request(request)
        model = GuardedModel(request_log_dir=folder / 'http', tool_choice='auto',
                             allow_text_final=plan.get('allow_native_text_final', False),
                             model_name=cfg['model'], model_kwargs=options,
                             cost_tracking='ignore_errors', observation_template=OBSERVATION)
        agent = AmbigAgent(model, env, reporter=None, tool_limit=100,
                           system_template=NATIVE_SYSTEM+'\nNo further user replies are available.',
                           instance_template='{{task}}', step_limit=100, cost_limit=0,
                           wall_time_limit_seconds=7200, output_path=folder / 'trajectory.json')
        agent.instance_id = iid
        query = agent.query
        def guarded_query():
            if (output / 'STOP.json').exists():
                raise RuntimeError('Batch stop before next model request')
            return query()
        agent.query = guarded_query
        try:
            status = agent.run(task).get('exit_status')
        finally:
            solver_elapsed = time.monotonic()-solver_start
            env.execute = original
    except Exception as exc:
        status = type(exc).__name__
        errors.append(str(exc))
    finally:
        if env:
            if original:
                env.execute = original
            try:
                patch = extract_patch(env, case['base_commit'])
            except Exception as exc:
                errors.append('Patch extraction: ' + str(exc))
            try:
                env.cleanup()
            except Exception as exc:
                errors.append('Cleanup: ' + str(exc))
        if agent:
            agent.save(folder / 'trajectory.json', {'info': {'exit_status': status, 'errors': errors}})
        if model:
            model.close()
        (folder / 'model.patch').write_text(patch)
        dump(folder / 'predictions.json', {'instance_id': iid,
             'model_name_or_path': 'mini-deepseek-cached-screen-'+condition, 'model_patch': patch})
        first = next((e for e in events if any(p.startswith(case['package']+'/') and '/tests/' not in p
                                               for p in e['tracked_changes'])), None)
        fault = bool(errors) or status not in ('Submitted', 'FinalResponse', 'LimitsExceeded', 'ToolLimitExceeded', 'TimeExceeded')
        solver_wire = wire_usage(folder / 'http')
        clarification_wire = wire_usage(folder / 'clarification_http')
        result = {'instance_id': iid, 'condition': condition, 'exit_status': status, 'errors': errors,
                  'execution_fault': fault, 'model_calls': solver_wire['http_requests'],
                  'agent_query_count': agent.n_calls if agent else 0,
                  'clarification_model_calls': clarification_wire['http_requests'],
                  'all_model_calls': solver_wire['http_requests'] + clarification_wire['http_requests'],
                  'tool_calls': len(events), 'patch_bytes': len(patch.encode()),
                  'solver_elapsed_seconds': solver_elapsed, 'total_elapsed_seconds': time.monotonic()-start,
                  'first_production_change': first, 'finished_at': now(),
                  'solver_wire': solver_wire,
                  'clarification_wire': clarification_wire}
        dump(folder / 'result.json', result)
        if fault:
            request_stop(output, {'at': now(), 'instance_id': iid, 'condition': condition,
                                        'reason': 'Execution/clarification/protocol fault; preserve results and stop new requests',
                                        'errors': errors, 'exit_status': status})
    return 1 if fault else 0


def eligible(partial, ideal, partial_report, ideal_report):
    return (not partial['execution_fault'] and not ideal['execution_fault']
            and partial_report.get('patch_successfully_applied') is True
            and ideal_report.get('patch_successfully_applied') is True
            and partial_report.get('resolved') is False and ideal_report.get('resolved') is True)


def parallel_jobs(output, plan, jobs):
    pending, running = list(jobs), []
    try:
        while pending or running:
            while pending and len(running) < plan['workers'] and not (output / 'STOP.json').exists():
                iid, condition = pending.pop(0)
                logs = output / 'console'
                logs.mkdir(exist_ok=True)
                log = (logs / f'{iid}.{condition}.log').open('x')
                cmd = [plan['solver_python'], '-B', '-u', '-m', MODULE, '_worker',
                       '--output', str(output), '--iid', iid, '--condition', condition]
                process = subprocess.Popen(cmd, cwd=PROJECT, env=child_env(), stdout=log, stderr=subprocess.STDOUT)
                running.append((process, log, iid, condition))
                print('START', iid, condition, flush=True)
            for entry in list(running):
                process, log, iid, condition = entry
                if process.poll() is not None:
                    log.close()
                    running.remove(entry)
                    print('FINISH', iid, condition, 'exit='+str(process.returncode), flush=True)
                    if process.returncode and not (output / 'STOP.json').exists():
                        request_stop(output, {'at': now(), 'reason': 'Worker failed; inspect console',
                                                   'instance_id': iid, 'condition': condition})
            if not running and (output / 'STOP.json').exists():
                break
            if running:
                time.sleep(0.5)
    except BaseException:
        request_stop(output, {'at': now(), 'reason': 'Controller interrupted; workers stop before next request'})
        # Give active API/tool requests a bounded chance to finish and save the trajectory.
        deadline = time.monotonic()+320
        for process, log, _, _ in running:
            try:
                process.wait(timeout=max(1, deadline-time.monotonic()))
            except subprocess.TimeoutExpired:
                process.terminate()
            log.close()
        raise
    if (output / 'STOP.json').exists():
        raise RuntimeError('Batch stopped; no automatic reruns. See STOP.json and console logs.')


def grade_jobs(output, plan, jobs):
    for iid, condition in jobs:
        folder = output / 'runs' / iid / condition
        if (folder / 'official_eval/report.json').exists():
            print('REUSE GRADE', iid, condition, flush=True)
            continue
        with (folder / 'grader.console.log').open('x') as log:
            subprocess.run([plan['grader_python'], '-B', '-u', '-m', MODULE, '_grade',
                            '--output', str(output), '--iid', iid, '--condition', condition],
                           cwd=PROJECT, env=child_env(), stdout=log, stderr=subprocess.STDOUT, check=True)
        print('GRADED', iid, condition, flush=True)


def summary(output, plan):
    rows = []
    for case in plan['cases']:
        for condition in (*plan.get('conditions', ['partial', 'ideal']), 'followup'):
            folder = output / 'runs' / case['instance_id'] / condition
            if not (folder / 'result.json').exists():
                continue
            result = json.loads((folder / 'result.json').read_text())
            report_path = folder / 'official_eval/report.json'
            report = json.loads(report_path.read_text()) if report_path.exists() else None
            reused = next((item for item in plan.get('recovery', {}).get('reused', [])
                           if item['instance_id'] == case['instance_id'] and item['condition'] == condition), None)
            rows.append({**result, 'official_report': report,
                         'reused_from': reused['source'] if reused else None,
                         'protocol_revision': reused['protocol_revision'] if reused else plan.get('protocol_revision', 'native-v2')})
    dump(output / 'summary.json', rows)
    lines = ['instance_id\tcondition\tstatus\tresolved\tmodel_calls\ttool_calls\ttotal_tokens\tseconds']
    for r in rows:
        tokens = sum(r[k]['usage']['total_tokens'] for k in ('solver_wire', 'clarification_wire'))
        lines.append('\t'.join(map(str, [r['instance_id'], r['condition'], r['exit_status'],
                          (r['official_report'] or {}).get('resolved'), r['model_calls'], r['tool_calls'],
                          tokens, round(r['total_elapsed_seconds'], 2)])))
    (output / 'summary.tsv').write_text('\n'.join(lines)+'\n')


def run(output):
    plan = verify_plan(output)
    reused = plan.get('recovery', {}).get('reused', [])
    for item in reused:
        for relative, digest in item['sha256'].items():
            if sha(output / relative) != digest:
                raise RuntimeError('Reused result changed: '+relative)
    reused_jobs = {(r['instance_id'], r['condition']) for r in reused}
    # An interrupted experiment is preserved, never silently resumed or overwritten.
    with (output / 'experiment_started.json').open('x') as stream:
        json.dump({'at': now(), 'workers': plan['workers']}, stream)
    jobs = [(c['instance_id'], condition) for c in plan['cases'] for condition in plan['conditions']]
    try:
        parallel_jobs(output, plan, [job for job in jobs if job not in reused_jobs])
        # All screen solvers have ended before scoring starts.
        grade_jobs(output, plan, jobs)
        if plan.get('run_mode') in ('full_only', 'followup_only'):
            dump(output / 'completed.json', {'at': now(),
                                             'full_runs': len(jobs) if plan['run_mode'] == 'full_only' else 0,
                                             'followup_runs': len(jobs) if plan['run_mode'] == 'followup_only' else 0})
            return
        candidates = []
        for case in plan['cases']:
            base = output / 'runs' / case['instance_id']
            values = [json.loads((base / c / 'result.json').read_text()) for c in ('partial', 'ideal')]
            reports = [json.loads((base / c / 'official_eval/report.json').read_text()) for c in ('partial', 'ideal')]
            if eligible(*values, *reports):
                candidates.append(case['instance_id'])
        dump(output / 'candidates.json', {'at': now(), 'instance_ids': candidates,
                                          'selection': 'clean partial false, ideal true; no scores/test details sent to models'})
        followups = [(iid, 'followup') for iid in candidates]
        parallel_jobs(output, plan, followups)
        grade_jobs(output, plan, followups)
        dump(output / 'completed.json', {'at': now(), 'screen_runs': len(jobs),
                                         'reused_screen_runs': len(reused_jobs),
                                         'new_screen_runs': len(jobs)-len(reused_jobs),
                                         'followup_runs': len(followups)})
    finally:
        summary(output, plan)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['prepare', 'prepare-full', 'prepare-followup', 'prepare-remaining', 'prepare-recovery', 'run', '_worker', '_grade', '_check-grader'])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--instance-ids', nargs='+', default=DEFAULT_IDS)
    parser.add_argument('--grader-python', type=Path,
                        default=Path('/home/rwh/miniconda3/envs/ambig-swe-grader/bin/python'))
    parser.add_argument('--proxy-source', choices=['issue', 'issue-hints'], default='issue')
    parser.add_argument('--iid')
    parser.add_argument('--previous-output', type=Path)
    parser.add_argument('--reuse-preparation', type=Path,
                        help='Reuse verified local image preparation, never solver results')
    parser.add_argument('--exclude-ids', nargs='*', default=['django__django-10554'])
    parser.add_argument('--condition', choices=['partial', 'ideal', 'followup', 'full'])
    args = parser.parse_args()
    output = args.output.resolve()
    if args.stage == 'prepare':
        prepare(output, args.instance_ids, args.grader_python)
    elif args.stage == 'prepare-full':
        prepare(output, args.instance_ids, args.grader_python, conditions=['full'])
    elif args.stage == 'prepare-followup':
        prepare(output, args.instance_ids, args.grader_python, conditions=['followup'], proxy_source=args.proxy_source)
    elif args.stage == 'prepare-remaining':
        if args.previous_output is None:
            parser.error('prepare-remaining requires --previous-output')
        from evaluation.analysis.mini_cached_screen_20261120.expand import prepare_remaining
        prepare_remaining(output, args.previous_output.resolve(), args.exclude_ids, args.grader_python,
                          args.reuse_preparation.resolve() if args.reuse_preparation else None)
    elif args.stage == 'prepare-recovery':
        if args.previous_output is None:
            parser.error('prepare-recovery requires --previous-output')
        from evaluation.analysis.mini_cached_screen_20261120.recovery import prepare_recovery
        prepare_recovery(output, args.previous_output.resolve())
    elif args.stage == 'run':
        run(output)
    elif args.stage == '_worker':
        sys.exit(worker(output, args.iid, args.condition))
    else:
        from evaluation.analysis.mini_cached_screen_20261120 import grader
        plan = json.loads((output / 'plan.json').read_text())
        if args.stage == '_check-grader':
            dump(output / 'grader_preflight.json', grader.check_specs(plan['cases']))
        else:
            case = next(c for c in plan['cases'] if c['instance_id'] == args.iid)
            grader.grade(case, output / 'runs' / args.iid / args.condition)


if __name__ == '__main__':
    main()
