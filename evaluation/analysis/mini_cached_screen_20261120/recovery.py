"""Prepare a new recovery directory; never resume a failed model trajectory."""

import json
from pathlib import Path
import shutil


TERMINAL = {'Submitted', 'FinalResponse', 'LimitsExceeded', 'ToolLimitExceeded', 'TimeExceeded'}


def reusable(folder):
    if not (folder / 'result.json').exists():
        return False
    result = json.loads((folder / 'result.json').read_text())
    if result['execution_fault'] or result['exit_status'] not in TERMINAL:
        return False
    for name in ('trajectory.json', 'task.txt', 'model.patch', 'predictions.json', 'tool_events.json'):
        if not (folder / name).is_file():
            raise RuntimeError('Completed run missing required artifact: '+str(folder / name))
    assert json.loads((folder / 'predictions.json').read_text())['model_patch'] == (folder / 'model.patch').read_text()
    assert len(json.loads((folder / 'tool_events.json').read_text())) == result['tool_calls']
    return True


def prepare_recovery(output: Path, previous: Path):
    from evaluation.analysis.mini_cached_screen_20261120 import run

    old = json.loads((previous / 'plan.json').read_text())
    assert run.public_settings(run.settings()) == old['model_settings']
    for name, digest in old['input_sha256'].items():
        assert run.sha(previous / name) == digest
    for name, digest in old['source_sha256'].items():
        assert run.sha(previous / 'frozen_source' / name) == digest
    images = {c['instance_id']: c['image_name'] for c in old['cases']}
    caches = {c['instance_id']: c['dependency_cache'] for c in old['cases'] if 'dependency_cache' in c}
    run.prepare(output, [c['instance_id'] for c in old['cases']], Path(old['grader_python']),
                image_overrides=images, dependency_caches=caches)
    plan = json.loads((output / 'plan.json').read_text())
    assert plan['input_sha256'] == old['input_sha256'], 'Recovery must keep actual input bytes identical'
    assert [c['image_id'] for c in plan['cases']] == [c['image_id'] for c in old['cases']]
    reused, fresh = [], []
    for case in old['cases']:
        iid = case['instance_id']
        for condition in ('partial', 'ideal'):
            source = previous / 'runs' / iid / condition
            if reusable(source):
                target = output / 'runs' / iid / condition
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source, target, symlinks=True)
                hashes = {str(p.relative_to(output)): run.sha(p) for p in target.rglob('*')
                          if p.is_file() and not p.is_symlink()}
                reused.append({'instance_id': iid, 'condition': condition, 'source': str(source),
                               'sha256': hashes, 'protocol_revision': old.get('protocol_revision', 'native-v2')})
            else:
                fresh.append({'instance_id': iid, 'condition': condition,
                              'previous_attempt': str(source) if source.exists() else None,
                              'policy': 'New baseline and full independent budget; no old messages or patch supplied'})
    plan['recovery'] = {'prepared_at': run.now(), 'previous_output': str(previous),
                        'previous_plan_sha256': run.sha(previous / 'plan.json'),
                        'reused': reused, 'fresh_runs': fresh,
                        'protocol_change': 'Opt-in standard native text final; preserve first batch-stop cause',
                        'comparability': 'Reused runs retain original protocol provenance; fresh runs use native v3. '
                                         'Report as recovery across protocol revisions, not an unchanged repeated experiment.'}
    run.dump(output / 'plan.json', plan)
    run.dump(output / 'recovery_manifest.json', plan['recovery'])
    print(f'RECOVERY PREPARED: reuse={len(reused)}, new runs={len(fresh)}, model_calls=0', flush=True)
