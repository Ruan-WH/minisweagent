"""Fixed prior public input plus file paths; no new clarification or repair hints."""

import argparse
import json
from pathlib import Path
import shutil

from evaluation.analysis.mini_10554_direct_proxy_20261120 import run as direct
from evaluation.analysis.mini_10554_protocol_v2_20261119 import run as native

common = direct.common
REFERENCE = direct.ARCHIVE / 'mini_swe/deepseek-10554-partial-direct-proxy-all-deepseek-20261120-v1'


def prepare(folder):
    direct.prepare(folder)
    prior = json.loads((REFERENCE / 'manifest.json').read_text())
    assert common.sha(REFERENCE / 'task.txt') == prior['input_sha256']['task.txt']
    for name in ('issue.txt', 'initial_question.txt', 'partial_answer.txt', 'dependencies.txt'):
        assert (folder / name).read_bytes() == (REFERENCE / name).read_bytes()
    fixture = json.loads((common.OLD / 'frozen/fixture.json').read_text())
    paths = [item.strip() for item in fixture['private_source']['files'].split(',')]
    assert paths == ['django/db/models/sql/compiler.py', 'django/db/models/sql/query.py']
    suffix = '\n\nFiles identified for this task:\n' + ''.join(f'- {path}\n' for path in paths)
    task = (REFERENCE / 'task.txt').read_text()
    (folder / 'prior_task.txt').write_text(task)
    (folder / 'file_hint.txt').write_text(suffix)
    (folder / 'task.txt').write_text(task + suffix)
    shutil.copy2(REFERENCE / 'public_handoff.json', folder / 'public_handoff.json')
    common.dump(folder / 'clarification_result.json', {
        'elapsed_seconds': 0, 'status': 'reused_public_handoff_no_new_calls',
        'followups_used': 1, 'source': str(REFERENCE),
        'source_task_sha256': common.sha(REFERENCE / 'task.txt'),
    })
    manifest = json.loads((folder / 'manifest.json').read_text())
    source = Path(__file__)
    relative = source.relative_to(common.PROJECT)
    target = folder / 'frozen_source' / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    manifest['source_sha256'][str(relative)] = common.sha(source)
    manifest.update(
        condition='partial_followup_file_hint',
        comparison_reference=str(REFERENCE),
        followups_enabled=False, checker_enabled=False, proxy_enabled=False,
        max_followups=0, clarification_actor='Previously completed public Q/A reused byte-for-byte',
        experiment_change='Only append two file paths from original issue metadata to identical prior solver input',
        file_hint_paths=paths, file_hint_source='Original fixture private_source.files; user explicitly authorized disclosure',
        additional_line_numbers=False, additional_patch_or_fix_advice=False,
        task_content='Exact prior public task + separate file-path-only suffix',
        handoff_frozen_at=common.now(),
    )
    for name in ('task.txt', 'prior_task.txt', 'file_hint.txt'):
        manifest['input_sha256'][name] = common.sha(folder / name)
    for key in ('model_settings', 'base_commit', 'image_id', 'protocol', 'max_model_calls', 'max_tool_calls'):
        assert manifest[key] == prior[key]
    common.dump(folder / 'manifest.json', manifest)
    common.dump(folder / 'input_comparison.json', {
        'reference': str(REFERENCE), 'prior_task_identical': True,
        'prior_task_sha256': common.sha(folder / 'prior_task.txt'),
        'suffix': suffix, 'new_task_sha256': common.sha(folder / 'task.txt'),
        'no_new_clarification_calls': True, 'fresh_baseline_and_trajectory': True,
    })


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=['prepare', 'run'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    {'prepare': prepare, 'run': native.run}[args.stage](args.output.resolve())
